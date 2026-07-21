#!/usr/bin/env python3
"""Independently validate closed Genera Fonts release archives."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import struct
import tarfile


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
LATIN_CODES = frozenset((*range(0x41, 0x5B), *range(0x61, 0x7B)))
ROOT_PATTERN = re.compile(r"^Genera-fonts-(latin|symbols)-([A-Za-z0-9._+~-]+)$")
EXPECTED_FONTTOSFNT_VERSION = "fonttosfnt 1.2.5"
ROOT_FILES = {
    "LICENSE",
    "NOTICE.md",
    "README.release.md",
    "RELEASE-MANIFEST.json",
    "SHA256SUMS",
}
METADATA_FILES = {
    "metadata/BUILD-MANIFEST.json",
    "metadata/FONT-IDENTITIES.json",
    "metadata/SOURCE-MANIFEST.json",
    "metadata/UNICODE-MAPPING.json",
}


class ReleaseCheckError(AssertionError):
    """A release archive violates the public distribution contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseCheckError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def safe_font_stem(name: str) -> str:
    result: list[str] = []
    previous_hyphen = False
    for character in name.lower():
        if character.isascii() and character.isalnum():
            result.append(character)
            previous_hyphen = False
        elif not previous_hyphen:
            result.append("-")
            previous_hyphen = True
    return "".join(result).strip("-") or "unnamed-font"


def expected_logical_identity(mapping: dict[str, object], name: str) -> dict[str, object]:
    assignments = mapping.get("assignments")
    families = mapping.get("families")
    faces = mapping.get("faces")
    named_sizes = mapping.get("x_named_sizes")
    disambiguators = mapping.get("desktop_style_disambiguators")
    require(isinstance(assignments, dict), "font-identity assignments are missing")
    require(isinstance(families, dict), "font-identity families are missing")
    require(isinstance(faces, dict), "font-identity faces are missing")
    require(isinstance(named_sizes, dict), "font-identity X named sizes are missing")
    require(
        isinstance(disambiguators, dict),
        "font-identity desktop style disambiguators are missing",
    )
    assignment = assignments.get(name)
    require(isinstance(assignment, dict), f"{name}: font identity is missing")
    family = families.get(assignment.get("typographic_family"))
    face = faces.get(assignment.get("typographic_face"))
    require(isinstance(family, dict), f"{name}: typographic family is missing")
    require(isinstance(face, dict), f"{name}: typographic face is missing")
    primary = assignment.get("primary")
    logical_name = None
    nominal_x_size = None
    if primary is not None:
        require(isinstance(primary, dict), f"{name}: primary logical identity is malformed")
        try:
            logical_name = "/".join(
                (str(primary["family"]), str(primary["face"]), str(primary["size"]))
            )
            nominal_x_size = named_sizes[primary["size"]]
        except KeyError as error:
            raise ReleaseCheckError(f"{name}: primary logical identity is incomplete") from error
    disambiguator = disambiguators.get(name)
    add_style_name = " ".join(
        value for value in (disambiguator, face.get("add_style_name")) if value
    )
    return {
        "mapping_status": assignment.get("mapping_status"),
        "logical_name": logical_name,
        "primary": primary,
        "nominal_x_size": nominal_x_size,
        "desktop_style_disambiguator": disambiguator,
        "typographic": {
            "family_name": family.get("family_name"),
            "weight_name": face.get("weight_name"),
            "slant": face.get("slant"),
            "setwidth_name": face.get("setwidth_name"),
            "add_style_name": add_style_name,
        },
    }


def _parse_bdf(data: bytes, label: str) -> dict[str, object]:
    try:
        lines = data.decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise ReleaseCheckError(f"{label}: BDF is not ASCII") from error
    fonts = [line[5:] for line in lines if line.startswith("FONT ")]
    declarations = [line for line in lines if line.startswith("CHARS ")]
    require(len(fonts) == 1, f"{label}: expected one FONT")
    require(fonts[0].endswith("-ISO10646-1"), f"{label}: not ISO10646-1")
    require(len(declarations) == 1, f"{label}: expected one CHARS")
    try:
        declared = int(declarations[0].split()[1])
    except (IndexError, ValueError) as error:
        raise ReleaseCheckError(f"{label}: malformed CHARS") from error
    encodings: set[int] = set()
    visible: set[int] = set()
    index = 0
    while index < len(lines):
        if not lines[index].startswith("STARTCHAR "):
            index += 1
            continue
        try:
            end = lines.index("ENDCHAR", index + 1)
        except ValueError as error:
            raise ReleaseCheckError(f"{label}: unterminated glyph") from error
        block = lines[index : end + 1]
        enc = [line for line in block if line.startswith("ENCODING ")]
        require(len(enc) == 1, f"{label}: malformed ENCODING")
        try:
            code = int(enc[0].split()[1])
            bitmap = block.index("BITMAP") + 1
            has_ink = any(int(row, 16) != 0 for row in block[bitmap:-1])
        except (IndexError, ValueError) as error:
            raise ReleaseCheckError(f"{label}: malformed glyph") from error
        require(code >= 0, f"{label}: unencoded glyph")
        require(code not in encodings, f"{label}: duplicate U+{code:04X}")
        encodings.add(code)
        if has_ink:
            visible.add(code)
        index = end + 1
    require(len(encodings) == declared, f"{label}: CHARS count mismatch")
    return {
        "xlfd": fonts[0],
        "glyph_count": declared,
        "codes": encodings,
        "class": "latin" if visible & LATIN_CODES else "symbols",
    }


def _parse_indexes(
    files: dict[str, bytes], expected_bdfs: set[str], label: str
) -> int:
    dir_path = "fonts/bdf/fonts.dir"
    alias_path = "fonts/bdf/fonts.alias"
    require(dir_path in files and alias_path in files, f"{label}: missing X indexes")
    try:
        dir_lines = files[dir_path].decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise ReleaseCheckError(f"{label}: fonts.dir is not ASCII") from error
    require(bool(dir_lines), f"{label}: empty fonts.dir")
    try:
        declared = int(dir_lines[0])
    except ValueError as error:
        raise ReleaseCheckError(f"{label}: malformed fonts.dir count") from error
    entries: dict[str, str] = {}
    for line in dir_lines[1:]:
        try:
            filename, xlfd = line.split(" ", 1)
        except ValueError as error:
            raise ReleaseCheckError(f"{label}: malformed fonts.dir") from error
        require(filename not in entries, f"{label}: duplicate fonts.dir filename")
        entries[filename] = xlfd
    require(declared == len(entries), f"{label}: fonts.dir count mismatch")
    require(set(entries) == expected_bdfs, f"{label}: fonts.dir is not closed")

    aliases: dict[str, str] = {}
    try:
        alias_lines = files[alias_path].decode("ascii").splitlines()
    except UnicodeDecodeError as error:
        raise ReleaseCheckError(f"{label}: fonts.alias is not ASCII") from error
    targets = set(entries.values())
    for line in alias_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("!"):
            continue
        try:
            fields = shlex.split(stripped)
        except ValueError as error:
            raise ReleaseCheckError(f"{label}: malformed fonts.alias") from error
        require(len(fields) == 2, f"{label}: malformed fonts.alias")
        name, target = fields
        require(name not in aliases, f"{label}: duplicate alias")
        require(target in targets, f"{label}: dangling alias target")
        aliases[name] = target
    return len(aliases)


def _read_archive(path: Path) -> tuple[list[tarfile.TarInfo], dict[str, bytes], str]:
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            require(bool(members), f"{path}: empty archive")
            names: set[str] = set()
            roots: set[str] = set()
            absolute_files: dict[str, bytes] = {}
            for member in members:
                normalized = member.name.rstrip("/")
                require(normalized and normalized not in names, f"{path}: duplicate member")
                names.add(normalized)
                member_path = PurePosixPath(normalized)
                require(
                    not member_path.is_absolute() and ".." not in member_path.parts,
                    f"{path}: unsafe archive path {member.name!r}",
                )
                require(
                    normalized == str(member_path),
                    f"{path}: non-canonical archive path {member.name!r}",
                )
                require(member.isdir() or member.isfile(), f"{path}: special file or link")
                roots.add(member_path.parts[0])
                if member.isfile():
                    stream = archive.extractfile(member)
                    require(stream is not None, f"{path}: unreadable {member.name}")
                    absolute_files[normalized] = stream.read()
            require(len(roots) == 1, f"{path}: expected one archive root")
            root = next(iter(roots))
            files = {
                str(PurePosixPath(name).relative_to(root)): data
                for name, data in absolute_files.items()
            }
            return members, files, root
    except (OSError, tarfile.TarError) as error:
        raise ReleaseCheckError(f"cannot read {path}: {error}") from error


def _allowed_file(name: str) -> bool:
    if name in ROOT_FILES or name in METADATA_FILES:
        return True
    path = PurePosixPath(name)
    if path.parent == PurePosixPath("fonts/bdf"):
        return path.suffix == ".bdf" or path.name in {"fonts.dir", "fonts.alias"}
    if path.parent == PurePosixPath("fonts/otb"):
        return path.suffix == ".otb"
    return False


def check_archive(path: Path) -> dict[str, object]:
    require(path.is_file() and not path.is_symlink(), f"{path}: archive is not a regular file")
    unresolved_sidecar = path.with_name(f"{path.name}.sha256")
    require(
        unresolved_sidecar.is_file() and not unresolved_sidecar.is_symlink(),
        f"{path}: checksum is not a regular file",
    )
    path = path.resolve()
    sidecar = path.with_name(f"{path.name}.sha256")
    require(sidecar.is_file(), f"{path}: missing external checksum")
    require(
        sidecar.read_text(encoding="ascii") == f"{file_digest(path)}  {path.name}\n",
        f"{path}: external checksum differs",
    )
    raw_gzip = path.read_bytes()
    require(raw_gzip[:2] == b"\x1f\x8b", f"{path}: not gzip")
    gzip_mtime = struct.unpack("<I", raw_gzip[4:8])[0]
    members, files, root = _read_archive(path)
    match = ROOT_PATTERN.fullmatch(root)
    require(match is not None, f"{path}: invalid archive root {root!r}")
    content_class, version = match.groups()
    require(path.name == f"{root}.tar.gz", f"{path}: filename/root mismatch")
    normalized_names = [member.name.rstrip("/") for member in members]
    require(normalized_names == sorted(normalized_names), f"{path}: tar members are not sorted")
    allowed_directories = {
        root,
        f"{root}/fonts",
        f"{root}/fonts/bdf",
        f"{root}/fonts/otb",
        f"{root}/metadata",
    }
    observed_directories = {member.name.rstrip("/") for member in members if member.isdir()}
    require(
        observed_directories == allowed_directories,
        f"{path}: archive directory layout differs",
    )

    require(ROOT_FILES <= set(files), f"{path}: required root files are missing")
    require(METADATA_FILES <= set(files), f"{path}: required metadata is missing")
    unexpected = sorted(name for name in files if not _allowed_file(name))
    require(not unexpected, f"{path}: forbidden/unexpected files: {unexpected}")
    forbidden_names = [
        name
        for name in files
        if name.lower().endswith(".vlod")
        or "raw" in PurePosixPath(name.lower()).parts
        or "sources" in PurePosixPath(name.lower()).parts
        or PurePosixPath(name).parent == PurePosixPath("fonts/json")
    ]
    require(not forbidden_names, f"{path}: source/raw payload leaked: {forbidden_names}")

    manifest = json.loads(files["RELEASE-MANIFEST.json"])
    require(manifest.get("schema_version") == 1, f"{path}: manifest schema differs")
    expected_identity = {"name": root, "version": version, "content_class": content_class}
    require(
        {key: manifest.get(key) for key in expected_identity} == expected_identity,
        f"{path}: manifest identity differs",
    )
    epoch = manifest.get("source_date_epoch")
    require(isinstance(epoch, int) and epoch == gzip_mtime, f"{path}: gzip epoch differs")
    for member in members:
        require(
            (member.uid, member.gid, member.uname, member.gname) == (0, 0, "", ""),
            f"{path}:{member.name}: ownership metadata differs",
        )
        require(member.mtime == epoch, f"{path}:{member.name}: timestamp differs")
        require(
            member.mode == (0o755 if member.isdir() else 0o644),
            f"{path}:{member.name}: mode differs",
        )

    generator = manifest.get("generator")
    require(isinstance(generator, dict), f"{path}: generator record is missing")
    require(
        generator
        == {
            "path": "scripts/build_release.py",
            "sha256": file_digest(REPOSITORY_ROOT / "scripts" / "build_release.py"),
            "fonttosfnt": EXPECTED_FONTTOSFNT_VERSION,
            "otb_input": "selected ISO10646-1 BDF",
        },
        f"{path}: release generator identity differs",
    )

    checksum_lines = files["SHA256SUMS"].decode("ascii").splitlines()
    checksums: dict[str, str] = {}
    for line in checksum_lines:
        fields = line.split("  ", 1)
        require(len(fields) == 2, f"{path}: malformed internal checksum")
        checksum, name = fields
        require(re.fullmatch(r"[0-9a-f]{64}", checksum) is not None, f"{path}: bad digest")
        require(name not in checksums, f"{path}: duplicate internal checksum")
        checksums[name] = checksum
    covered = set(files) - {"SHA256SUMS"}
    require(set(checksums) == covered, f"{path}: internal checksum set is not closed")
    for name in covered:
        require(checksums[name] == digest(files[name]), f"{path}:{name}: checksum differs")

    notices = manifest.get("notices")
    require(isinstance(notices, dict), f"{path}: notice manifest missing")
    notice_paths = {"project_license": "LICENSE", "font_publication": "NOTICE.md"}
    for key, expected_name in notice_paths.items():
        record = notices.get(key)
        require(isinstance(record, dict), f"{path}: {key} notice record missing")
        require(record.get("path") == expected_name, f"{path}: {key} notice path differs")
        require(record.get("sha256") == digest(files[expected_name]), f"{path}: {key} hash differs")

    metadata_records = manifest.get("metadata")
    require(isinstance(metadata_records, dict), f"{path}: metadata manifest missing")
    for relative in sorted(METADATA_FILES):
        name = PurePosixPath(relative).name
        record = metadata_records.get(name)
        require(isinstance(record, dict), f"{path}: missing metadata record {name}")
        require(record.get("path") == relative, f"{path}: metadata path differs for {name}")
        require(record.get("sha256") == digest(files[relative]), f"{path}: metadata hash differs")

    bdf_files = {name: data for name, data in files.items() if name.startswith("fonts/bdf/") and name.endswith(".bdf")}
    otb_files = {name: data for name, data in files.items() if name.startswith("fonts/otb/") and name.endswith(".otb")}
    require(bool(bdf_files), f"{path}: no BDFs")
    bdf_summaries = {name: _parse_bdf(data, f"{path}:{name}") for name, data in bdf_files.items()}
    require(
        all(summary["class"] == content_class for summary in bdf_summaries.values()),
        f"{path}: content-derived partition differs",
    )
    require(
        {PurePosixPath(name).stem for name in bdf_files}
        == {PurePosixPath(name).stem for name in otb_files},
        f"{path}: OTB/BDF filename closure differs",
    )
    require(
        all(data[:4] in {b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"} for data in otb_files.values()),
        f"{path}: invalid OTB signature",
    )
    alias_count = _parse_indexes(
        files, {PurePosixPath(name).name for name in bdf_files}, str(path)
    )

    mapping = json.loads(files["metadata/UNICODE-MAPPING.json"])
    font_identities = json.loads(files["metadata/FONT-IDENTITIES.json"])
    mapping_id = mapping.get("mapping_id")
    assignments = mapping.get("assignments")
    private_registry = mapping.get("bmp_private_use")
    require(isinstance(mapping_id, str) and mapping_id, f"{path}: mapping ID missing")
    identity_mapping_id = font_identities.get("mapping_id")
    require(
        isinstance(identity_mapping_id, str) and identity_mapping_id,
        f"{path}: font-identity mapping ID missing",
    )
    require(isinstance(assignments, dict), f"{path}: mapping assignments missing")
    require(isinstance(private_registry, dict), f"{path}: mapping PUA registry missing")
    blocks = private_registry.get("blocks")
    require(isinstance(blocks, dict), f"{path}: mapping PUA blocks missing")

    artifacts = manifest.get("artifacts")
    require(isinstance(artifacts, list), f"{path}: artifact manifest missing")
    require(len(artifacts) == len(bdf_files), f"{path}: artifact count differs")
    seen_paths: set[str] = set()
    artifact_names: set[str] = set()
    artifact_stems: set[str] = set()
    standard_glyph_count = 0
    private_use_glyph_count = 0
    for artifact in artifacts:
        require(isinstance(artifact, dict), f"{path}: invalid artifact record")
        name = artifact.get("name")
        require(isinstance(name, str) and name not in artifact_names, f"{path}: duplicate font name")
        artifact_names.add(name)
        require(
            artifact.get("logical_identity")
            == expected_logical_identity(font_identities, name),
            f"{path}:{name}: logical identity differs",
        )
        artifact_files = artifact.get("files")
        require(isinstance(artifact_files, dict), f"{path}:{name}: file records missing")
        for kind, suffix in (("bdf", ".bdf"), ("otb", ".otb")):
            record = artifact_files.get(kind)
            require(isinstance(record, dict), f"{path}:{name}: missing {kind}")
            relative = record.get("path")
            require(isinstance(relative, str) and relative.endswith(suffix), f"{path}:{name}: bad {kind} path")
            require(relative in files and relative not in seen_paths, f"{path}:{name}: {kind} path not unique")
            seen_paths.add(relative)
            require(record.get("sha256") == digest(files[relative]), f"{path}:{name}: {kind} hash differs")
        bdf_path = str(artifact_files["bdf"]["path"])
        summary = bdf_summaries[bdf_path]
        stem = PurePosixPath(bdf_path).stem
        require(stem == safe_font_stem(name), f"{path}:{name}: filename identity differs")
        require(stem not in artifact_stems, f"{path}:{name}: duplicate font stem")
        artifact_stems.add(stem)
        require(artifact.get("glyph_count") == summary["glyph_count"], f"{path}:{name}: glyph count differs")
        require(artifact.get("unicode_xlfd_name") == summary["xlfd"], f"{path}:{name}: XLFD differs")
        repertoire = assignments.get(name)
        require(artifact.get("repertoire") == repertoire, f"{path}:{name}: repertoire differs from mapping")
        codes = summary["codes"]
        require(isinstance(codes, set), f"{path}:{name}: encoded repertoire is invalid")
        if repertoire == "standard":
            require(
                all(not 0xE000 <= code <= 0xF8FF for code in codes),
                f"{path}:{name}: standard repertoire contains BMP PUA",
            )
            artifact_standard = len(codes)
            artifact_private = 0
        else:
            block = blocks.get(repertoire)
            require(isinstance(block, dict), f"{path}:{name}: unknown PUA repertoire {repertoire!r}")
            first = block.get("first")
            require(type(first) is int, f"{path}:{name}: invalid PUA block")
            require(
                all(first <= code < first + 128 for code in codes),
                f"{path}:{name}: glyph falls outside its PUA block",
            )
            artifact_standard = 0
            artifact_private = len(codes)
        require(
            artifact.get("standard_glyph_count") == artifact_standard,
            f"{path}:{name}: standard glyph count differs",
        )
        require(
            artifact.get("private_use_glyph_count") == artifact_private,
            f"{path}:{name}: private-use glyph count differs",
        )
        standard_glyph_count += artifact_standard
        private_use_glyph_count += artifact_private
    require(seen_paths == set(bdf_files) | set(otb_files), f"{path}: font records are not closed")

    counts = manifest.get("counts")
    require(isinstance(counts, dict), f"{path}: counts missing")
    observed_counts = {
        "font_count": len(bdf_files),
        "bdf_count": len(bdf_files),
        "otb_count": len(otb_files),
        "alias_count": alias_count,
        "glyph_count": sum(int(summary["glyph_count"]) for summary in bdf_summaries.values()),
        "standard_glyph_count": standard_glyph_count,
        "private_use_glyph_count": private_use_glyph_count,
    }
    require(
        {key: counts.get(key) for key in observed_counts} == observed_counts,
        f"{path}: manifest counts differ",
    )
    build_manifest = json.loads(files["metadata/BUILD-MANIFEST.json"])
    source_distribution = manifest.get("source_distribution")
    require(isinstance(source_distribution, dict), f"{path}: source distribution missing")
    mapping_record = source_distribution.get("unicode_mapping")
    require(isinstance(mapping_record, dict), f"{path}: mapping provenance missing")
    require(mapping_record.get("id") == mapping_id, f"{path}: mapping provenance ID differs")
    identity_record = source_distribution.get("font_identities")
    require(isinstance(identity_record, dict), f"{path}: font-identity provenance missing")
    require(
        identity_record.get("id") == identity_mapping_id,
        f"{path}: font-identity provenance ID differs",
    )
    require(
        identity_record.get("sha256")
        == digest(files["metadata/FONT-IDENTITIES.json"]),
        f"{path}: font-identity provenance hash differs",
    )
    class_counts = build_manifest.get("content_class_counts")
    require(isinstance(class_counts, dict), f"{path}: build class counts missing")
    require(class_counts.get(content_class) == len(bdf_files), f"{path}: build class count differs")
    return {
        "archive": str(path),
        "version": version,
        "content_class": content_class,
        "font_count": len(bdf_files),
        "glyph_count": observed_counts["glyph_count"],
        "font_names": artifact_names,
        "font_stems": artifact_stems,
        "standard_glyph_count": standard_glyph_count,
        "private_use_glyph_count": private_use_glyph_count,
        "build_manifest": build_manifest,
        "source_manifest": json.loads(files["metadata/SOURCE-MANIFEST.json"]),
        "metadata_digests": {name: digest(files[name]) for name in sorted(METADATA_FILES)},
    }


def check_archives(paths: list[Path]) -> list[dict[str, object]]:
    require(bool(paths), "no release archives supplied")
    results = [check_archive(path) for path in paths]
    identities = [(str(result["version"]), str(result["content_class"])) for result in results]
    require(len(identities) == len(set(identities)), "duplicate release version/content class")
    if len(results) == 2 and {result["content_class"] for result in results} == {"latin", "symbols"}:
        require(results[0]["version"] == results[1]["version"], "release versions differ")
        require(results[0]["metadata_digests"] == results[1]["metadata_digests"], "release metadata differs")
        left = results[0]["font_names"]
        right = results[1]["font_names"]
        require(isinstance(left, set) and isinstance(right, set) and not left & right, "release classes overlap")
        build_manifest = results[0]["build_manifest"]
        source_manifest = results[0]["source_manifest"]
        require(
            len(left | right) == build_manifest.get("font_count"),
            "release classes do not close the build inventory",
        )
        source_files = source_manifest.get("files")
        require(isinstance(source_files, list), "release source manifest lacks file closure")
        expected_stems: set[str] = set()
        for record in source_files:
            require(isinstance(record, dict), "release source manifest has an invalid file record")
            relative = record.get("path")
            if isinstance(relative, str):
                candidate = PurePosixPath(relative)
                if candidate.parent == PurePosixPath("fonts") and candidate.suffix == ".json":
                    expected_stems.add(candidate.stem)
        observed_stems = results[0]["font_stems"] | results[1]["font_stems"]
        require(observed_stems == expected_stems, "release classes do not match the exact source font inventory")
        combined = {
            "unicode_glyph_count": sum(int(result["glyph_count"]) for result in results),
            "standard_unicode_glyph_count": sum(int(result["standard_glyph_count"]) for result in results),
            "private_use_unicode_glyph_count": sum(int(result["private_use_glyph_count"]) for result in results),
        }
        for key, value in combined.items():
            require(build_manifest.get(key) == value, f"combined release {key} differs from build")
        expected_source = source_manifest.get("expected")
        require(isinstance(expected_source, dict), "release source manifest lacks expected invariants")
        require(
            combined["unicode_glyph_count"] == expected_source.get("defined_glyph_count"),
            "release glyph union does not close the source inventory",
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="*", type=Path)
    parser.add_argument("--release-dir", type=Path, default=Path("dist/release"))
    args = parser.parse_args()
    directory_mode = not args.archives
    paths = args.archives or sorted(args.release_dir.glob("Genera-fonts-*.tar.gz"))
    try:
        if directory_mode:
            require(len(paths) == 2, f"{args.release_dir}: expected exactly two release archives")
        results = check_archives(paths)
        if directory_mode:
            require(
                {result["content_class"] for result in results} == {"latin", "symbols"},
                f"{args.release_dir}: expected one Latin and one symbols archive",
            )
            require(
                len({result["version"] for result in results}) == 1,
                f"{args.release_dir}: archive versions differ",
            )
            expected_assets = {
                path.name for path in paths
            } | {f"{path.name}.sha256" for path in paths}
            observed_assets = {
                path.name for path in args.release_dir.iterdir()
            }
            require(
                observed_assets == expected_assets,
                f"{args.release_dir}: release asset closure differs",
            )
    except (OSError, UnicodeError, json.JSONDecodeError, ReleaseCheckError) as error:
        parser.error(str(error))
    serializable = [
        {
            key: value
            for key, value in result.items()
            if key
            not in {
                "font_names",
                "font_stems",
                "build_manifest",
                "source_manifest",
                "metadata_digests",
            }
        }
        for result in results
    ]
    print(json.dumps({"status": "ok", "archives": serializable}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
