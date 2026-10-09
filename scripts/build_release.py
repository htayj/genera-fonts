#!/usr/bin/env python3
"""Build deterministic Latin and symbols release archives from Unicode BDFs."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile

try:
    from fontTools.ttLib import TTFont, newTable
    from fontTools.ttLib.tables.DefaultTable import DefaultTable
    from fontTools.ttLib.tables.E_B_D_T_ import ebdt_bitmap_format_7
    from fontTools.ttLib.tables.E_B_L_C_ import eblc_index_sub_table_1
except ImportError as error:  # pragma: no cover - exercised by release hosts
    raise SystemExit(
        "build_release.py requires fontTools (for example python3-fonttools)"
    ) from error


ROOT = Path(__file__).resolve().parents[1]
CONTENT_CLASSES = ("latin", "symbols")
LATIN_CODES = frozenset((*range(0x41, 0x5B), *range(0x61, 0x7B)))
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9._+~-]+$")
ASSET_PATTERN = re.compile(
    r"^Genera-fonts-(latin|symbols)-[A-Za-z0-9._+~-]+\.tar\.gz(?:\.sha256)?$"
)
EXPECTED_FONTTOSFNT_VERSION = "fonttosfnt 1.2.5"


class ReleaseError(RuntimeError):
    """The requested release is not a closed, reproducible distribution."""


@dataclass(frozen=True)
class BdfSummary:
    xlfd: str
    glyph_count: int
    encodings: frozenset[int]
    visible_codes: frozenset[int]

    @property
    def content_class(self) -> str:
        return "latin" if self.visible_codes & LATIN_CODES else "symbols"


@dataclass(frozen=True)
class Artifact:
    name: str
    record: dict[str, object]
    path: Path
    summary: BdfSummary
    content_class: str


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseError(f"cannot read {path}: {error}") from error
    require(isinstance(value, dict), f"{path}: expected a JSON object")
    return value


def read_bdf_summary(path: Path) -> BdfSummary:
    """Read the identity and visible encoded glyphs used for release selection."""

    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as error:
        raise ReleaseError(f"cannot read BDF {path}: {error}") from error

    fonts = [line[5:] for line in lines if line.startswith("FONT ")]
    declarations = [line for line in lines if line.startswith("CHARS ")]
    require(len(fonts) == 1, f"{path}: expected exactly one FONT declaration")
    require(len(declarations) == 1, f"{path}: expected exactly one CHARS declaration")
    require(fonts[0].endswith("-ISO10646-1"), f"{path}: release BDF is not ISO10646-1")
    try:
        declared_count = int(declarations[0].split()[1])
    except (IndexError, ValueError) as error:
        raise ReleaseError(f"{path}: malformed CHARS declaration") from error

    encodings: set[int] = set()
    visible: set[int] = set()
    index = 0
    while index < len(lines):
        if not lines[index].startswith("STARTCHAR "):
            index += 1
            continue
        start = index
        try:
            end = lines.index("ENDCHAR", start + 1)
        except ValueError as error:
            raise ReleaseError(f"{path}:{start + 1}: unterminated glyph") from error
        block = lines[start : end + 1]
        encoding_lines = [line for line in block if line.startswith("ENCODING ")]
        require(len(encoding_lines) == 1, f"{path}:{start + 1}: malformed ENCODING")
        try:
            encoding = int(encoding_lines[0].split()[1])
            bitmap_start = block.index("BITMAP") + 1
            has_ink = any(int(row, 16) != 0 for row in block[bitmap_start:-1])
        except (IndexError, ValueError) as error:
            raise ReleaseError(f"{path}:{start + 1}: malformed glyph") from error
        require(encoding >= 0, f"{path}:{start + 1}: unencoded release glyph")
        require(encoding not in encodings, f"{path}: duplicate encoding {encoding}")
        encodings.add(encoding)
        if has_ink:
            visible.add(encoding)
        index = end + 1

    require(
        len(encodings) == declared_count,
        f"{path}: CHARS declares {declared_count}, parsed {len(encodings)}",
    )
    return BdfSummary(
        xlfd=fonts[0],
        glyph_count=declared_count,
        encodings=frozenset(encodings),
        visible_codes=frozenset(visible),
    )


def _parse_fonts_dir(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as error:
        raise ReleaseError(f"cannot read {path}: {error}") from error
    require(bool(lines), f"{path}: empty fonts.dir")
    try:
        declared = int(lines[0])
    except ValueError as error:
        raise ReleaseError(f"{path}: malformed fonts.dir count") from error
    entries: dict[str, str] = {}
    for line_number, line in enumerate(lines[1:], 2):
        try:
            filename, xlfd = line.split(" ", 1)
        except ValueError as error:
            raise ReleaseError(f"{path}:{line_number}: malformed fonts.dir entry") from error
        require(filename not in entries, f"{path}:{line_number}: duplicate {filename}")
        entries[filename] = xlfd
    require(declared == len(entries), f"{path}: fonts.dir count mismatch")
    return entries


def _parse_fonts_alias(path: Path) -> tuple[str, dict[str, str]]:
    comment = ""
    aliases: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as error:
        raise ReleaseError(f"cannot read {path}: {error}") from error
    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("!"):
            if not comment:
                comment = stripped
            continue
        try:
            fields = shlex.split(stripped)
        except ValueError as error:
            raise ReleaseError(f"{path}:{line_number}: malformed alias") from error
        require(len(fields) == 2, f"{path}:{line_number}: alias needs two fields")
        name, target = fields
        require(name not in aliases, f"{path}:{line_number}: duplicate alias {name}")
        aliases[name] = target
    return comment, aliases


def write_filtered_indexes(
    source: Path, destination: Path, selected: dict[str, str]
) -> int:
    """Write indexes closed over the selected Unicode BDFs."""

    full_dir = _parse_fonts_dir(source / "fonts.dir")
    comment, full_aliases = _parse_fonts_alias(source / "fonts.alias")
    require(set(selected) <= set(full_dir), f"{source}: selected BDF missing from fonts.dir")
    for filename, xlfd in selected.items():
        require(full_dir[filename] == xlfd, f"{source}/{filename}: XLFD differs from fonts.dir")
    known_targets = set(full_dir.values())
    require(
        all(target in known_targets for target in full_aliases.values()),
        f"{source}: alias target is absent from fonts.dir",
    )
    selected_targets = set(selected.values())
    aliases = {
        name: target for name, target in full_aliases.items() if target in selected_targets
    }
    require(all('"' not in target for target in aliases.values()), f"{source}: quoted XLFD")

    destination.mkdir(parents=True, exist_ok=True)
    entries = sorted(selected.items())
    (destination / "fonts.dir").write_text(
        str(len(entries)) + "\n" + "".join(f"{name} {xlfd}\n" for name, xlfd in entries),
        encoding="ascii",
    )
    release_comment = comment or "! Genera Fonts aliases; XLFD names are authoritative."
    (destination / "fonts.alias").write_text(
        release_comment
        + "\n"
        + "".join(f'{name} "{aliases[name]}"\n' for name in sorted(aliases)),
        encoding="ascii",
    )
    return len(aliases)


def _artifact_inventory(distribution: Path) -> tuple[list[Artifact], dict[str, object]]:
    build_manifest_path = distribution / "BUILD-MANIFEST.json"
    source_manifest_path = distribution / "SOURCE-MANIFEST.json"
    catalog_path = distribution / "unicode" / "catalog.json"
    mapping_path = distribution / "unicode" / "UNICODE-MAPPING.json"
    identities_path = distribution / "FONT-IDENTITIES.json"
    build_manifest = load_json(build_manifest_path)
    source_manifest = load_json(source_manifest_path)
    catalog = load_json(catalog_path)
    mapping = load_json(mapping_path)
    identities = load_json(identities_path)
    require(build_manifest.get("schema_version") == 1, "unsupported BUILD-MANIFEST schema")
    require(source_manifest.get("schema_version") == 1, "unsupported SOURCE-MANIFEST schema")
    require(catalog.get("schema_version") == 1, "unsupported Unicode catalog schema")
    require(mapping.get("schema_version") == 1, "unsupported Unicode mapping schema")
    require(identities.get("schema_version") == 1, "unsupported font-identity schema")
    mapping_id = mapping.get("mapping_id")
    require(isinstance(mapping_id, str) and mapping_id, "Unicode mapping ID is missing")
    identity_mapping_id = identities.get("mapping_id")
    require(
        isinstance(identity_mapping_id, str) and identity_mapping_id,
        "font-identity mapping ID is missing",
    )
    require(
        build_manifest.get("font_identities")
        == {"id": identity_mapping_id, "sha256": sha256(identities_path)},
        "build manifest font-identity provenance differs",
    )
    identity_assignments = identities.get("assignments")
    require(isinstance(identity_assignments, dict), "font-identity assignments are missing")
    records = catalog.get("fonts")
    require(isinstance(records, list), "Unicode catalog lacks fonts")
    require(catalog.get("font_count") == len(records), "Unicode catalog font count differs")

    artifacts: list[Artifact] = []
    seen_names: set[str] = set()
    for record in records:
        require(isinstance(record, dict), "invalid Unicode font record")
        name = record.get("name")
        relative = record.get("bdf_path")
        require(isinstance(name, str) and name, "Unicode font record lacks name")
        require(name not in seen_names, f"duplicate Unicode font name: {name}")
        seen_names.add(name)
        require(name in identity_assignments, f"{name}: no logical font assignment")
        require(isinstance(record.get("logical_identity"), dict), f"{name}: logical identity missing")
        require(isinstance(relative, str), f"{name}: Unicode record lacks bdf_path")
        relative_path = Path(relative)
        require(
            not relative_path.is_absolute()
            and relative_path.parts[:2] == ("unicode", "bdf")
            and len(relative_path.parts) == 3
            and relative_path.suffix == ".bdf",
            f"{name}: unsafe or non-Unicode BDF path: {relative}",
        )
        path = distribution / relative_path
        summary = read_bdf_summary(path)
        bdf_profile = record.get("bdf_profile")
        require(isinstance(bdf_profile, dict), f"{name}: missing BDF profile")
        require(summary.xlfd == bdf_profile.get("xlfd_name"), f"{name}: XLFD drift")
        require(summary.glyph_count == record.get("glyph_count"), f"{name}: glyph count drift")
        require(sha256(path) == record.get("bdf_sha256"), f"{name}: BDF hash drift")
        content_class = summary.content_class
        require(record.get("content_class") == content_class, f"{name}: content class drift")
        require(
            int(record.get("standard_glyph_count", -1))
            + int(record.get("private_use_glyph_count", -1))
            == summary.glyph_count,
            f"{name}: repertoire counts do not close the BDF",
        )
        artifacts.append(Artifact(name, record, path, summary, content_class))

    artifacts.sort(key=lambda artifact: artifact.path.name)
    observed_counts = {
        group: sum(artifact.content_class == group for artifact in artifacts)
        for group in CONTENT_CLASSES
    }
    require(catalog.get("content_class_counts") == observed_counts, "catalog class counts differ")
    require(
        build_manifest.get("content_class_counts") == observed_counts,
        "build-manifest class counts differ",
    )
    require(build_manifest.get("font_count") == len(artifacts), "build font count differs")
    require(catalog.get("glyph_count") == sum(a.summary.glyph_count for a in artifacts), "catalog glyph count differs")

    provenance = {
        "build_manifest": {"path": "BUILD-MANIFEST.json", "sha256": sha256(build_manifest_path)},
        "source_manifest": {"path": "SOURCE-MANIFEST.json", "sha256": sha256(source_manifest_path)},
        "unicode_catalog": {"path": "unicode/catalog.json", "sha256": sha256(catalog_path)},
        "unicode_mapping": {
            "path": "unicode/UNICODE-MAPPING.json",
            "id": mapping_id,
            "sha256": sha256(mapping_path),
        },
        "font_identities": {
            "path": "FONT-IDENTITIES.json",
            "id": identity_mapping_id,
            "sha256": sha256(identities_path),
        },
        "source": build_manifest.get("source"),
        "profile": build_manifest.get("profile"),
    }
    return artifacts, provenance


def _fonttosfnt_version(executable: str) -> str:
    resolved = shutil.which(executable)
    require(resolved is not None, f"cannot find fonttosfnt executable: {executable}")
    try:
        process = subprocess.run(
            [resolved, "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as error:
        raise ReleaseError(f"cannot run {resolved}: {error}") from error
    except subprocess.CalledProcessError as error:
        raise ReleaseError(f"cannot identify fonttosfnt version: {error}") from error
    version = (process.stdout or process.stderr).strip()
    require(
        version == EXPECTED_FONTTOSFNT_VERSION,
        f"unsupported fonttosfnt: expected {EXPECTED_FONTTOSFNT_VERSION!r}, got {version!r}",
    )
    return version


def _normalize_empty_bitmap_runs(font: TTFont) -> int:
    """Give empty fixed-size bitmap runs a real, metric-only EBDT record.

    fonttosfnt's index 2/5, image 5 encoding stores no bytes for zero-area
    glyphs. FreeType rejects these zero-length records before reading metrics.
    Index 1 with image 7 instead stores all eight BigGlyphMetrics bytes per
    glyph, without adding pixels or altering either direction's metrics.
    """
    changed = 0
    for strike, bitmaps in zip(font["EBLC"].strikes, font["EBDT"].strikeData):
        for index, subtable in enumerate(strike.indexSubTables):
            if (
                subtable.indexFormat not in (2, 5)
                or subtable.imageFormat != 5
                or subtable.imageSize != 0
            ):
                continue
            require(
                subtable.metrics.width * subtable.metrics.height == 0,
                "zero-size EBDT run has nonempty bitmap dimensions",
            )
            replacement = eblc_index_sub_table_1(None, font)
            replacement.indexFormat = 1
            replacement.imageFormat = 7
            replacement.names = list(subtable.names)
            for name in replacement.names:
                require(not bitmaps[name].imageData, f"{name}: zero-size run has pixels")
                # Distinct objects keep records consecutive when fontTools compiles
                # EBDT: its alias deduplication is unsuitable for index 1 offsets.
                bitmap = ebdt_bitmap_format_7(None, font)
                bitmap.metrics = subtable.metrics
                bitmap.imageData = b""
                bitmaps[name] = bitmap
            strike.indexSubTables[index] = replacement
            changed += len(replacement.names)
    return changed


def _normalize_horizontal_metrics(font: TTFont) -> None:
    """Expose every bitmap escapement to consumers of unscaled SFNT metrics."""
    require(len(font["EBLC"].strikes) == 1, "horizontal metrics require one native strike")
    strike = font["EBLC"].strikes[0]
    ppem = strike.bitmapSizeTable.ppemX
    units = font["head"].unitsPerEm
    require(ppem > 0, "native strike has zero horizontal ppem")
    metrics: dict[str, tuple[int, int]] = {}
    bitmaps = font["EBDT"].strikeData[0]
    for subtable in strike.indexSubTables:
        for name in subtable.names:
            bitmap = bitmaps[name]
            glyph_metrics = bitmap.metrics if hasattr(bitmap, "metrics") else subtable.metrics
            advance = (
                glyph_metrics.Advance if hasattr(glyph_metrics, "Advance")
                else glyph_metrics.horiAdvance
            )
            # Nearest design unit, matching fonttosfnt's positive-width rounding.
            scaled = (2 * advance * units + ppem) // (2 * ppem)
            # OTB has no contours: hmtx LSB is zero, independently of the
            # baseline-relative bitmap bearing preserved in EBDT/EBLC.
            metrics[name] = (scaled, 0)
    require(set(metrics) == set(font.getGlyphOrder()), "native strike misses an SFNT glyph")
    font["hmtx"] = newTable("hmtx")
    font["hmtx"].metrics = metrics
    # There are no outlines: do not load/recompile glyf merely to derive zeros.
    hhea = font["hhea"]
    hhea.advanceWidthMax = max(width for width, _ in metrics.values())
    hhea.minLeftSideBearing = hhea.minRightSideBearing = hhea.xMaxExtent = 0
    font["OS/2"].recalcAvgCharWidth(font)
    positive_widths = {width for width, _ in metrics.values() if width > 0}
    font["post"].isFixedPitch = int(len(positive_widths) == 1)


def _normalize_otb(path: Path, epoch: int) -> None:
    with TTFont(path, recalcTimestamp=False, recalcBBoxes=False) as font:
        # fonttosfnt 1.2.5 treats SOURCE_DATE_EPOCH=0 as unspecified and uses
        # wall-clock time. Write the requested epoch explicitly, in SFNT's
        # seconds-since-1904 units, even when the Unix epoch is zero.
        font["head"].created = font["head"].modified = epoch + 0x7C259DC0
        # fontTools' glyf compiler inserts a byte for an all-empty outline table.
        # That changes FreeType/Fontconfig from bitmap-only to scalable. Retain
        # the converter's raw zero-length glyf and matching loca placeholders.
        require(font.reader.tables["glyf"].length == 0, f"{path}: unexpected outlines")
        for tag in ("glyf", "loca"):
            table = DefaultTable(tag)
            table.data = font.reader[tag]
            font[tag] = table
        _normalize_empty_bitmap_runs(font)
        _normalize_horizontal_metrics(font)
        font.save(path)


def _convert_otb(source: Path, destination: Path, executable: str, epoch: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["SOURCE_DATE_EPOCH"] = str(epoch)
    try:
        subprocess.run(
            [executable, "-o", str(destination), str(source)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        detail = (getattr(error, "stderr", b"") or b"").decode("utf-8", "replace").strip()
        raise ReleaseError(f"fonttosfnt failed for {source}: {detail or error}") from error
    require(destination.is_file() and destination.stat().st_size > 0, f"empty OTB {destination}")
    require(
        destination.read_bytes()[:4] in {b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"},
        f"{destination}: output lacks an SFNT signature",
    )
    _normalize_otb(destination, epoch)


def _copy(source: Path, destination: Path) -> None:
    require(source.is_file(), f"required release input is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _write_readme(path: Path, version: str, content_class: str) -> None:
    if content_class == "latin":
        description = (
            "This archive contains every Genera 8.5 resident font whose emitted "
            "Unicode BDF has a visible Basic Latin letter."
        )
    else:
        description = (
            "This archive contains the complementary Genera 8.5 resident "
            "specialty fonts without a visible Basic Latin letter."
        )
    path.write_text(
        f"""# Genera Fonts {content_class} release {version}

{description}

The installable typefaces are ISO10646-1 BDFs under `fonts/bdf/` and
display-equivalent OTB conversions under `fonts/otb/`. This archive does not
contain the Genera VLOD, extracted source JSON, raw Lisp Machine encoded BDFs,
or raw-code glyph sheets.

`RELEASE-MANIFEST.json` records the content-derived selection, exact font
identities, input provenance, and hashes. `SHA256SUMS` covers every other file.
`LICENSE` covers repository-authored work. `NOTICE.md` records the publication
basis and scope for the redistributed typefaces; it must accompany them.
""",
        encoding="utf-8",
    )


def _write_internal_checksums(release_root: Path) -> int:
    checksum_path = release_root / "SHA256SUMS"
    files = sorted(
        path for path in release_root.rglob("*") if path.is_file() and path != checksum_path
    )
    checksum_path.write_text(
        "".join(
            f"{sha256(path)}  {path.relative_to(release_root).as_posix()}\n"
            for path in files
        ),
        encoding="ascii",
    )
    return len(files)


def write_deterministic_archive(root: Path, archive: Path, epoch: int) -> None:
    require(0 <= epoch <= 0xFFFFFFFF, "SOURCE_DATE_EPOCH is outside gzip range")
    archive.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive.with_name(f".{archive.name}.tmp")
    if temporary.exists():
        temporary.unlink()
    try:
        with temporary.open("wb") as raw_stream:
            with gzip.GzipFile(fileobj=raw_stream, mode="wb", filename="", mtime=epoch) as gz:
                with tarfile.open(fileobj=gz, mode="w", format=tarfile.GNU_FORMAT) as tar:
                    paths = [root, *sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix())]
                    for path in paths:
                        require(not path.is_symlink(), f"release staging contains symlink {path}")
                        arcname = root.name if path == root else f"{root.name}/{path.relative_to(root).as_posix()}"
                        info = tar.gettarinfo(str(path), arcname=arcname)
                        info.uid = 0
                        info.gid = 0
                        info.uname = ""
                        info.gname = ""
                        info.mtime = epoch
                        info.mode = 0o755 if path.is_dir() else 0o644
                        info.pax_headers = {}
                        if path.is_file():
                            with path.open("rb") as stream:
                                tar.addfile(info, stream)
                        else:
                            tar.addfile(info)
        os.replace(temporary, archive)
    finally:
        if temporary.exists():
            temporary.unlink()


def _build_one(
    *,
    distribution: Path,
    destination_directory: Path,
    version: str,
    epoch: int,
    content_class: str,
    artifacts: list[Artifact],
    provenance: dict[str, object],
    fonttosfnt: str,
    converter_version: str,
) -> tuple[Path, Path]:
    package_name = f"Genera-fonts-{content_class}-{version}"
    selected = [artifact for artifact in artifacts if artifact.content_class == content_class]
    with tempfile.TemporaryDirectory(prefix="genera-fonts-release-") as temporary:
        release_root = Path(temporary) / package_name
        release_root.mkdir()
        _write_readme(release_root / "README.release.md", version, content_class)
        _copy(ROOT / "LICENSE", release_root / "LICENSE")
        _copy(ROOT / "NOTICE.md", release_root / "NOTICE.md")

        metadata_sources = {
            "BUILD-MANIFEST.json": distribution / "BUILD-MANIFEST.json",
            "FONT-IDENTITIES.json": distribution / "FONT-IDENTITIES.json",
            "SOURCE-MANIFEST.json": distribution / "SOURCE-MANIFEST.json",
            "UNICODE-MAPPING.json": distribution / "unicode" / "UNICODE-MAPPING.json",
        }
        metadata_records: dict[str, dict[str, str]] = {}
        for name, source in metadata_sources.items():
            destination = release_root / "metadata" / name
            _copy(source, destination)
            metadata_records[name] = {
                "path": destination.relative_to(release_root).as_posix(),
                "sha256": sha256(destination),
            }

        bdf_root = release_root / "fonts" / "bdf"
        otb_root = release_root / "fonts" / "otb"
        alias_count = write_filtered_indexes(
            distribution / "unicode" / "bdf",
            bdf_root,
            {artifact.path.name: artifact.summary.xlfd for artifact in selected},
        )
        manifest_artifacts: list[dict[str, object]] = []
        for artifact in selected:
            bdf_destination = bdf_root / artifact.path.name
            otb_destination = otb_root / f"{artifact.path.stem}.otb"
            _copy(artifact.path, bdf_destination)
            _convert_otb(artifact.path, otb_destination, fonttosfnt, epoch)
            manifest_artifacts.append(
                {
                    "name": artifact.name,
                    "logical_identity": artifact.record.get("logical_identity"),
                    "repertoire": artifact.record.get("repertoire"),
                    "glyph_count": artifact.summary.glyph_count,
                    "standard_glyph_count": artifact.record.get("standard_glyph_count"),
                    "private_use_glyph_count": artifact.record.get("private_use_glyph_count"),
                    "unicode_xlfd_name": artifact.summary.xlfd,
                    "files": {
                        "bdf": {
                            "path": bdf_destination.relative_to(release_root).as_posix(),
                            "sha256": sha256(bdf_destination),
                        },
                        "otb": {
                            "path": otb_destination.relative_to(release_root).as_posix(),
                            "sha256": sha256(otb_destination),
                        },
                    },
                }
            )

        release_manifest = {
            "schema_version": 1,
            "name": package_name,
            "version": version,
            "content_class": content_class,
            "content_class_policy": {
                "latin": "at least one emitted Unicode BDF glyph in U+0041-U+005A or U+0061-U+007A has visible ink",
                "symbols": "the closed complement of the latin font set",
                "selection_input": "emitted ISO10646-1 BDF content, not font names or catalog labels",
                "glyph_preservation": "classification selects complete fonts; it removes no glyphs",
            },
            "source_date_epoch": epoch,
            "generator": {
                "path": "scripts/build_release.py",
                "sha256": sha256(Path(__file__).resolve()),
                "fonttosfnt": converter_version,
                "otb_input": "selected ISO10646-1 BDF",
            },
            "source_distribution": provenance,
            "notices": {
                "project_license": {"path": "LICENSE", "sha256": sha256(release_root / "LICENSE")},
                "font_publication": {"path": "NOTICE.md", "sha256": sha256(release_root / "NOTICE.md")},
            },
            "metadata": metadata_records,
            "counts": {
                "font_count": len(selected),
                "bdf_count": len(selected),
                "otb_count": len(selected),
                "alias_count": alias_count,
                "glyph_count": sum(artifact.summary.glyph_count for artifact in selected),
                "standard_glyph_count": sum(int(artifact.record["standard_glyph_count"]) for artifact in selected),
                "private_use_glyph_count": sum(int(artifact.record["private_use_glyph_count"]) for artifact in selected),
            },
            "excluded_source_material": [
                "Genera VLOD images",
                "extracted source JSON",
                "raw Lisp Machine encoded BDFs",
                "raw-code glyph sheets and the repository specimen gallery",
            ],
            "artifacts": manifest_artifacts,
        }
        (release_root / "RELEASE-MANIFEST.json").write_text(
            json.dumps(release_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _write_internal_checksums(release_root)

        archive = destination_directory / f"{package_name}.tar.gz"
        write_deterministic_archive(release_root, archive, epoch)
        checksum = archive.with_name(f"{archive.name}.sha256")
        checksum.write_text(f"{sha256(archive)}  {archive.name}\n", encoding="ascii")
        return archive, checksum


def build_release(
    *,
    distribution: Path,
    release_dir: Path,
    version: str,
    source_date_epoch: int,
    fonttosfnt: str = "fonttosfnt",
) -> list[Path]:
    require(VERSION_PATTERN.fullmatch(version) is not None, "unsafe release version")
    require(0 <= source_date_epoch <= 0xFFFFFFFF, "invalid SOURCE_DATE_EPOCH")
    distribution = distribution.resolve()
    release_dir = release_dir.resolve()
    require(distribution.is_dir(), f"distribution directory does not exist: {distribution}")
    require((ROOT / "LICENSE").is_file(), "project LICENSE is missing")
    require((ROOT / "NOTICE.md").is_file(), "font publication NOTICE.md is missing")
    artifacts, provenance = _artifact_inventory(distribution)
    converter_version = _fonttosfnt_version(fonttosfnt)

    release_dir.mkdir(parents=True, exist_ok=True)
    existing = list(release_dir.iterdir())
    unknown = sorted(
        path.name
        for path in existing
        if not path.is_file()
        or path.is_symlink()
        or ASSET_PATTERN.fullmatch(path.name) is None
    )
    require(
        not unknown,
        f"release directory contains unknown entries; refusing to alter them: {unknown}",
    )
    outputs: list[Path] = []
    with tempfile.TemporaryDirectory(prefix=".genera-fonts-assets-", dir=release_dir) as temp:
        temporary_assets = Path(temp)
        for content_class in CONTENT_CLASSES:
            archive, checksum = _build_one(
                distribution=distribution,
                destination_directory=temporary_assets,
                version=version,
                epoch=source_date_epoch,
                content_class=content_class,
                artifacts=artifacts,
                provenance=provenance,
                fonttosfnt=fonttosfnt,
                converter_version=converter_version,
            )
        # All four new assets now exist.  Only recognized Genera release
        # assets may be replaced; an unknown entry was rejected above.
        for old_asset in existing:
            old_asset.unlink()
        for content_class in CONTENT_CLASSES:
            package_name = f"Genera-fonts-{content_class}-{version}.tar.gz"
            for name in (package_name, f"{package_name}.sha256"):
                source = temporary_assets / name
                require(source.is_file(), f"release build did not produce {name}")
                destination = release_dir / name
                os.replace(source, destination)
                outputs.append(destination)
    require(
        {path.name for path in release_dir.iterdir()} == {path.name for path in outputs},
        "release output directory is not an exact four-asset closure",
    )
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", type=Path, default=Path("dist"))
    parser.add_argument("--release-dir", type=Path, default=Path("dist/release"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    parser.add_argument("--fonttosfnt", default="fonttosfnt", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        outputs = build_release(
            distribution=args.distribution,
            release_dir=args.release_dir,
            version=args.version,
            source_date_epoch=args.source_date_epoch,
            fonttosfnt=args.fonttosfnt,
        )
    except ReleaseError as error:
        parser.exit(1, f"release build failed: {error}\n")
    print(json.dumps({"release_assets": [str(path) for path in outputs]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
