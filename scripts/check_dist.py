#!/usr/bin/env python3
"""Validate the closed Genera font distribution and both BDF profiles."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "sources" / "genera-8.5"
SOURCE_MANIFEST = SOURCE_ROOT / "SOURCE-MANIFEST.json"
SOURCE_CATALOG = SOURCE_ROOT / "catalog.json"
SOURCE_VALIDATION = SOURCE_ROOT / "validation.json"
UNICODE_MAPPING = ROOT / "config" / "unicode-mapping.json"
FONT_IDENTITIES = ROOT / "config" / "font-identities.json"
CORPUS_PROFILE = ROOT / "config" / "corpus-profile.json"
DEFAULT_OUTPUT = ROOT / "dist"
HEX_DIGEST = re.compile(r"[0-9a-f]{64}")
HEX_ROW = re.compile(r"[0-9A-F]+")
LATIN_LETTERS = frozenset(
    (*range(ord("A"), ord("Z") + 1), *range(ord("a"), ord("z") + 1))
)
PANGRAM = "The five boxing Lisp wizards jump quickly."


class DistCheckError(AssertionError):
    """A generated artifact violates a reviewed distribution invariant."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DistCheckError(message)


def exact_keys(value: object, keys: set[str], context: str) -> dict[str, object]:
    require(isinstance(value, dict), f"{context}: expected an object")
    observed = set(value)
    require(
        observed == keys,
        f"{context}: schema keys differ; "
        f"missing={sorted(keys - observed)}, unexpected={sorted(observed - keys)}",
    )
    return value


def bounded_string(value: object, context: str, *, limit: int = 512) -> str:
    require(isinstance(value, str) and 0 < len(value) <= limit, f"{context}: invalid string")
    require(all(character.isprintable() for character in value), f"{context}: non-printable text")
    return value


def checked_sha256(value: object, context: str) -> str:
    require(isinstance(value, str) and HEX_DIGEST.fullmatch(value) is not None, f"{context}: invalid SHA-256")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha256(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def safe_filename(name: str) -> str:
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


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_reject_duplicate_keys
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DistCheckError(f"cannot load {path}: {error}") from error
    require(isinstance(value, dict), f"{path}: expected a JSON object")
    return value


def safe_relative(value: object, purpose: str) -> Path:
    require(isinstance(value, str) and bool(value), f"invalid {purpose} path")
    require("\\" not in value, f"unsafe {purpose} path: {value}")
    pure = PurePosixPath(value)
    require(
        not pure.is_absolute()
        and value == pure.as_posix()
        and "." not in pure.parts
        and ".." not in pure.parts,
        f"unsafe {purpose} path: {value}",
    )
    return Path(*pure.parts)


def _regular_tree(root: Path) -> dict[str, Path]:
    require(root.is_dir() and not root.is_symlink(), f"missing directory: {root}")
    result: dict[str, Path] = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        require(not path.is_symlink(), f"symlink is outside the closed tree: {relative}")
        if path.is_dir():
            continue
        require(path.is_file(), f"special file is outside the closed tree: {relative}")
        result[relative] = path
    return result


def _integer(value: object, message: str, *, minimum: int | None = None) -> int:
    require(type(value) is int, message)
    number = int(value)
    if minimum is not None:
        require(number >= minimum, message)
    return number


def validate_source_font(record: dict[str, object], path: Path) -> None:
    exact_keys(
        record,
        {"schema_version", "name", "source_format", "source_object", "metrics", "slots"},
        str(path),
    )
    require(record["schema_version"] == 1, f"{path}: unsupported schema")
    bounded_string(record["name"], f"{path}: font name", limit=64)
    require(
        record["source_format"] == "little-endian VLM VLOD V2 resident FONT array",
        f"{path}: source format differs",
    )
    source_object = exact_keys(
        record["source_object"],
        {"header_vma", "fill_pointer", "special_high_codes"},
        f"{path}: source object",
    )
    header_vma = source_object["header_vma"]
    require(
        isinstance(header_vma, str)
        and len(header_vma) == 10
        and header_vma.startswith("0x")
        and all(character in "0123456789abcdef" for character in header_vma[2:]),
        f"{path}: invalid source object address",
    )
    fill_pointer = _integer(source_object["fill_pointer"], f"{path}: invalid fill pointer", minimum=1)
    special_high_codes = source_object["special_high_codes"]
    require(isinstance(special_high_codes, list), f"{path}: invalid special high codes")
    for code in special_high_codes:
        _integer(code, f"{path}: invalid special high code", minimum=fill_pointer)
    require(
        special_high_codes == sorted(set(special_high_codes)),
        f"{path}: special high codes are not sorted and unique",
    )
    metrics = exact_keys(
        record["metrics"],
        {
            "character_height",
            "default_character_width",
            "raster_height",
            "raster_width",
            "baseline",
            "blinker_width",
            "blinker_height",
            "variable_width",
        },
        f"{path}: metrics",
    )
    slots = record["slots"]
    require(isinstance(slots, list) and bool(slots), f"{path}: missing slots")
    for key in (
        "character_height",
        "default_character_width",
        "raster_height",
        "raster_width",
        "baseline",
        "blinker_width",
        "blinker_height",
    ):
        value = _integer(metrics[key], f"{path}: invalid metric {key}", minimum=0)
        require(value <= 4096, f"{path}: unreasonable metric {key}")
    require(type(metrics["variable_width"]) is bool, f"{path}: invalid variable-width flag")
    require(metrics["character_height"] > 0, f"{path}: non-positive character height")
    require(metrics["raster_height"] > 0, f"{path}: non-positive raster height")
    require(
        0 <= metrics["baseline"] <= metrics["character_height"],
        f"{path}: invalid baseline",
    )
    codes: list[int] = []
    for index, item in enumerate(slots):
        slot = exact_keys(
            item,
            {"code", "code_octal", "exists", "advance", "left_kern", "raster_width", "raster_height", "rows"},
            f"{path}: slots[{index}]",
        )
        code = _integer(slot["code"], f"{path}: invalid slot code", minimum=0)
        require(code <= 0x10FFFF, f"{path}: slot code exceeds scalar-sized bound")
        require(slot["code_octal"] == f"{code:o}", f"{path}: stale octal code")
        require(type(slot["exists"]) is bool, f"{path}: invalid existence flag")
        advance = _integer(slot["advance"], f"{path}: invalid advance")
        left_kern = _integer(slot["left_kern"], f"{path}: invalid left kern")
        require(abs(advance) <= 4096 and abs(left_kern) <= 4096, f"{path}: unreasonable glyph metric")
        width = _integer(
            slot["raster_width"], f"{path}: invalid raster width", minimum=0
        )
        height = _integer(
            slot["raster_height"], f"{path}: invalid raster height", minimum=0
        )
        require(width <= 4096 and height <= 4096, f"{path}: unreasonable raster dimensions")
        require(height == metrics["raster_height"], f"{path}: slot raster height drift")
        rows = slot.get("rows")
        require(isinstance(rows, list), f"{path}: invalid raster rows")
        require(
            len(rows) == (height if slot["exists"] else 0),
            f"{path}: raster row count drift",
        )
        digits = max(1, (width + 3) // 4)
        for row in rows:
            require(
                isinstance(row, str)
                and len(row) == digits
                and HEX_ROW.fullmatch(row) is not None,
                f"{path}: malformed raster row",
            )
            value = int(row, 16)
            require(
                value < (1 << width) if width else value == 0,
                f"{path}: raster row exceeds declared width",
            )
        codes.append(code)
    require(
        codes == sorted(codes) and len(codes) == len(set(codes)),
        f"{path}: slot codes are not unique and sorted",
    )
    require(
        codes == [*range(fill_pointer), *special_high_codes],
        f"{path}: slots do not match the resident fill pointer and special-high codes",
    )


def source_geometry(records: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        {
            "name": record["name"],
            "metrics": record["metrics"],
            "slots": [
                {
                    key: slot[key]
                    for key in (
                        "code",
                        "exists",
                        "advance",
                        "left_kern",
                        "raster_width",
                        "raster_height",
                        "rows",
                    )
                }
                for slot in record["slots"]
            ],
        }
        for record in records
    ]


def check_source_identity(value: object, context: str) -> dict[str, object]:
    source = exact_keys(value, {"filename", "size_bytes", "sha256", "format"}, context)
    filename = bounded_string(source["filename"], f"{context}.filename", limit=128)
    require(Path(filename).name == filename and "\\" not in filename, f"{context}: unsafe filename")
    _integer(source["size_bytes"], f"{context}: invalid size", minimum=1)
    checked_sha256(source["sha256"], f"{context}.sha256")
    bounded_string(source["format"], f"{context}.format", limit=128)
    return source


def check_decoder_identity(value: object, context: str) -> dict[str, object]:
    decoder = exact_keys(
        value,
        {
            "repository",
            "commit",
            "extractor_path",
            "extractor_sha256",
            "helper_path",
            "helper_sha256",
            "execution_model",
        },
        context,
    )
    for key in ("repository", "extractor_path", "helper_path", "execution_model"):
        bounded_string(decoder[key], f"{context}.{key}")
    commit = decoder["commit"]
    require(
        isinstance(commit, str)
        and len(commit) == 40
        and all(character in "0123456789abcdef" for character in commit),
        f"{context}: invalid Git commit",
    )
    checked_sha256(decoder["extractor_sha256"], f"{context}.extractor_sha256")
    checked_sha256(decoder["helper_sha256"], f"{context}.helper_sha256")
    return decoder


def check_import_validation(
    validation: dict[str, object], *, names: list[str], expected: dict[str, object]
) -> None:
    exact_keys(
        validation,
        {
            "schema_version",
            "scope",
            "authority",
            "font_count",
            "bdf_exact_count",
            "bfd_exact_count",
            "standard_character_name_evidence",
            "fonts",
        },
        "source validation",
    )
    require(validation["schema_version"] == 1, "unsupported source validation")
    bounded_string(validation["scope"], "source validation.scope")
    bounded_string(validation["authority"], "source validation.authority")
    for key in ("font_count", "bdf_exact_count", "bfd_exact_count"):
        _integer(validation[key], f"source validation: invalid {key}", minimum=0)
    require(validation["font_count"] == len(names), "validation font count changed")
    require(validation["bdf_exact_count"] == expected["bdf_exact_count"], "BDF witness count changed")
    require(validation["bfd_exact_count"] == expected["bfd_exact_count"], "BFD witness count changed")

    evidence = exact_keys(
        validation["standard_character_name_evidence"],
        {"font", "reference_filename", "reference_sha256", "scope", "characters"},
        "standard character-name evidence",
    )
    for key in ("font", "reference_filename", "scope"):
        bounded_string(evidence[key], f"standard character-name evidence.{key}")
    filename = str(evidence["reference_filename"])
    require(Path(filename).name == filename and "\\" not in filename, "unsafe character-name witness filename")
    checked_sha256(evidence["reference_sha256"], "standard character-name evidence.reference_sha256")
    characters = evidence["characters"]
    require(isinstance(characters, list), "standard character-name evidence lacks characters")
    codes: list[int] = []
    for index, item in enumerate(characters):
        context = f"standard character-name evidence.characters[{index}]"
        record = exact_keys(item, {"raw_code", "raw_octal", "name"}, context)
        code = _integer(record["raw_code"], f"{context}: invalid raw code", minimum=0)
        require(record["raw_octal"] == f"{code:o}", f"{context}: stale octal code")
        bounded_string(record["name"], f"{context}.name", limit=128)
        codes.append(code)
    require(codes == sorted(codes) and len(codes) == len(set(codes)), "character-name codes are not sorted and unique")

    records = validation["fonts"]
    require(isinstance(records, list), "source validation lacks font records")
    observed_names: list[str] = []
    for index, item in enumerate(records):
        context = f"source validation.fonts[{index}]"
        record = exact_keys(item, {"font", "bdf", "bfd"}, context)
        observed_names.append(bounded_string(record["font"], f"{context}.font", limit=64))
        for kind in ("bdf", "bfd"):
            witness_context = f"{context}.{kind}"
            witness = exact_keys(
                record[kind], {"filename", "size_bytes", "sha256", "differences"}, witness_context
            )
            witness_name = bounded_string(witness["filename"], f"{witness_context}.filename", limit=255)
            require(Path(witness_name).name == witness_name and "\\" not in witness_name, f"{witness_context}: unsafe filename")
            _integer(witness["size_bytes"], f"{witness_context}: invalid size", minimum=1)
            checked_sha256(witness["sha256"], f"{witness_context}.sha256")
            differences = witness["differences"]
            require(isinstance(differences, list) and len(differences) <= 512, f"{witness_context}: invalid differences")
            for difference in differences:
                bounded_string(difference, f"{witness_context}.differences", limit=1024)
    require(observed_names == names, "validation font inventory differs")


def check_source_closure(
    distributed_manifest: Path,
) -> tuple[dict[str, object], dict[str, object], dict[str, dict[str, object]]]:
    manifest = load_json(SOURCE_MANIFEST)
    catalog = load_json(SOURCE_CATALOG)
    validation = load_json(SOURCE_VALIDATION)
    require(
        distributed_manifest.read_bytes() == SOURCE_MANIFEST.read_bytes(),
        "distribution source manifest is stale",
    )
    exact_keys(
        manifest,
        {"schema_version", "profile", "publication_boundary", "source", "decoder", "expected", "files"},
        "source manifest",
    )
    exact_keys(
        catalog,
        {
            "schema_version",
            "profile",
            "scope_note",
            "source",
            "decoder",
            "font_count",
            "slot_count",
            "defined_glyph_count",
            "variable_width_font_count",
            "normalized_geometry_sha256",
            "fonts",
        },
        "source catalog",
    )
    require(manifest["schema_version"] == 1, "unsupported source manifest")
    require(catalog["schema_version"] == 1, "unsupported source catalog")
    bounded_string(manifest["profile"], "source manifest.profile")
    bounded_string(manifest["publication_boundary"], "source manifest.publication_boundary")
    bounded_string(catalog["profile"], "source catalog.profile")
    bounded_string(catalog["scope_note"], "source catalog.scope_note")
    require(manifest["profile"] == catalog["profile"], "source profile identity differs")
    source_identity = check_source_identity(manifest["source"], "source manifest.source")
    decoder_identity = check_decoder_identity(manifest["decoder"], "source manifest.decoder")
    require(check_source_identity(catalog["source"], "source catalog.source") == source_identity, "catalog source identity differs")
    require(check_decoder_identity(catalog["decoder"], "source catalog.decoder") == decoder_identity, "catalog decoder identity differs")
    expected = exact_keys(
        manifest["expected"],
        {
            "font_count",
            "slot_count",
            "defined_glyph_count",
            "variable_width_font_count",
            "normalized_geometry_sha256",
            "bdf_exact_count",
            "bfd_exact_count",
        },
        "source manifest.expected",
    )
    for key in (
        "font_count",
        "slot_count",
        "defined_glyph_count",
        "variable_width_font_count",
        "bdf_exact_count",
        "bfd_exact_count",
    ):
        _integer(expected[key], f"source manifest.expected: invalid {key}", minimum=0)
    checked_sha256(expected["normalized_geometry_sha256"], "source manifest.expected.normalized_geometry_sha256")
    for key in ("font_count", "slot_count", "defined_glyph_count", "variable_width_font_count"):
        _integer(catalog[key], f"source catalog: invalid {key}", minimum=0)
    checked_sha256(catalog["normalized_geometry_sha256"], "source catalog.normalized_geometry_sha256")
    file_records = manifest["files"]
    require(isinstance(file_records, list), "source manifest lacks file closure")

    declared: dict[str, dict[str, object]] = {}
    for index, item in enumerate(file_records):
        record = exact_keys(item, {"path", "size_bytes", "sha256"}, f"source manifest.files[{index}]")
        relative_path = safe_relative(record["path"], "source")
        relative = relative_path.as_posix()
        require(relative != "SOURCE-MANIFEST.json", "source manifest lists itself")
        require(relative not in declared, f"duplicate source path: {relative}")
        require(
            checked_sha256(record["sha256"], f"source digest for {relative}") == record["sha256"],
            f"invalid source digest: {relative}",
        )
        _integer(record["size_bytes"], f"invalid source size: {relative}", minimum=1)
        declared[relative] = record

    observed = _regular_tree(SOURCE_ROOT)
    observed.pop("SOURCE-MANIFEST.json", None)
    require(set(observed) == set(declared), "source file closure differs from manifest")
    for relative, path in observed.items():
        record = declared[relative]
        require(path.stat().st_size == record["size_bytes"], f"source size changed: {relative}")
        require(sha256(path) == record["sha256"], f"source SHA-256 changed: {relative}")

    catalog_fonts = catalog["fonts"]
    require(isinstance(catalog_fonts, list), "source catalog lacks font records")
    fonts: list[dict[str, object]] = []
    source_font_paths: set[str] = set()
    for index, item in enumerate(catalog_fonts):
        entry = exact_keys(
            item,
            {"font", "path", "size_bytes", "sha256", "slot_count", "defined_glyph_count"},
            f"source catalog.fonts[{index}]",
        )
        name = bounded_string(entry["font"], f"source catalog.fonts[{index}].font", limit=64)
        relative = safe_relative(entry["path"], "source font").as_posix()
        require(relative == f"fonts/{safe_filename(name)}.json", f"font record path differs: {relative}")
        require(relative not in source_font_paths, f"duplicate source font path: {relative}")
        source_font_paths.add(relative)
        require(relative in declared, f"source catalog names undeclared file: {relative}")
        path = SOURCE_ROOT / relative
        record = load_json(path)
        validate_source_font(record, path)
        _integer(entry["size_bytes"], f"catalog size invalid: {relative}", minimum=1)
        _integer(entry["slot_count"], f"catalog slot count invalid: {relative}", minimum=1)
        _integer(entry["defined_glyph_count"], f"catalog glyph count invalid: {relative}", minimum=0)
        checked_sha256(entry["sha256"], f"catalog digest invalid: {relative}")
        require(record["name"] == name, f"font identity differs: {relative}")
        require(path.stat().st_size == entry["size_bytes"], f"catalog size differs: {relative}")
        require(sha256(path) == entry["sha256"], f"catalog hash differs: {relative}")
        require(entry["size_bytes"] == declared[relative]["size_bytes"], f"catalog/manifest size differs: {relative}")
        require(entry["sha256"] == declared[relative]["sha256"], f"catalog/manifest hash differs: {relative}")
        require(len(record["slots"]) == entry["slot_count"], f"slot count differs: {relative}")
        require(
            sum(bool(slot["exists"]) for slot in record["slots"])
            == entry["defined_glyph_count"],
            f"defined glyph count differs: {relative}",
        )
        fonts.append(record)
    require(
        set(declared) == {"catalog.json", "validation.json", *source_font_paths},
        "source manifest contains files outside the closed font corpus",
    )
    names = [str(record["name"]) for record in fonts]
    require(names == sorted(names), "source fonts are not sorted by name")
    require(len(names) == len(set(names)), "duplicate source font name")

    counts: dict[str, object] = {
        "font_count": len(fonts),
        "slot_count": sum(len(record["slots"]) for record in fonts),
        "defined_glyph_count": sum(
            bool(slot["exists"]) for record in fonts for slot in record["slots"]
        ),
        "variable_width_font_count": sum(
            bool(record["metrics"]["variable_width"]) for record in fonts
        ),
        "normalized_geometry_sha256": canonical_sha256(source_geometry(fonts)),
    }
    for key, value in counts.items():
        require(expected.get(key) == value, f"source invariant changed: {key}")
        require(catalog.get(key) == value, f"source catalog invariant changed: {key}")

    check_import_validation(validation, names=names, expected=expected)
    return manifest, catalog, {str(record["name"]): record for record in fonts}


def _one(lines: list[str], prefix: str, path: Path) -> str:
    values = [line[len(prefix) :] for line in lines if line.startswith(prefix)]
    require(len(values) == 1, f"{path}: expected exactly one {prefix.strip()}")
    return values[0]


def parse_bdf(path: Path) -> dict[str, object]:
    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as error:
        raise DistCheckError(f"cannot read {path}: {error}") from error
    require(lines[:1] == ["STARTFONT 2.1"], f"{path}: not BDF 2.1")
    require(lines[-1:] == ["ENDFONT"], f"{path}: missing ENDFONT")
    size = tuple(map(int, _one(lines, "SIZE ", path).split()))
    font_box = tuple(map(int, _one(lines, "FONTBOUNDINGBOX ", path).split()))
    require(len(size) == 3, f"{path}: malformed SIZE")
    require(len(font_box) == 4, f"{path}: malformed FONTBOUNDINGBOX")

    property_starts = [
        index for index, line in enumerate(lines) if line.startswith("STARTPROPERTIES ")
    ]
    require(len(property_starts) == 1, f"{path}: expected one STARTPROPERTIES")
    property_start = property_starts[0]
    try:
        property_end = lines.index("ENDPROPERTIES", property_start + 1)
    except ValueError as error:
        raise DistCheckError(f"{path}: missing ENDPROPERTIES") from error
    declared_properties = int(lines[property_start].split()[1])
    property_lines = lines[property_start + 1 : property_end]
    require(len(property_lines) == declared_properties, f"{path}: property count mismatch")
    properties: dict[str, str | int] = {}
    for line in property_lines:
        require(" " in line, f"{path}: malformed property")
        key, raw_value = line.split(" ", 1)
        require(key not in properties, f"{path}: duplicate property {key}")
        if raw_value.startswith('"'):
            require(raw_value.endswith('"'), f"{path}: unterminated property {key}")
            value: str | int = raw_value[1:-1]
        else:
            value = int(raw_value)
        properties[key] = value

    declared_glyphs = int(_one(lines, "CHARS ", path))
    glyphs: dict[int, dict[str, object]] = {}
    encountered_codes: list[int] = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("STARTCHAR "):
            index += 1
            continue
        try:
            end = lines.index("ENDCHAR", index + 1)
        except ValueError as error:
            raise DistCheckError(f"{path}: unterminated glyph") from error
        block = lines[index : end + 1]

        def glyph_one(prefix: str) -> str:
            values = [line[len(prefix) :] for line in block if line.startswith(prefix)]
            require(len(values) == 1, f"{path}: glyph lacks one {prefix.strip()}")
            return values[0]

        encoding_fields = glyph_one("ENCODING ").split()
        require(len(encoding_fields) == 1, f"{path}: secondary encoding is unsupported")
        encoding = int(encoding_fields[0])
        require(encoding >= 0, f"{path}: negative encoding {encoding}")
        require(encoding not in glyphs, f"{path}: duplicate encoding {encoding}")
        swidth = tuple(map(int, glyph_one("SWIDTH ").split()))
        dwidth = tuple(map(int, glyph_one("DWIDTH ").split()))
        box = tuple(map(int, glyph_one("BBX ").split()))
        require(len(swidth) == 2, f"{path}: malformed SWIDTH at {encoding}")
        require(len(dwidth) == 2, f"{path}: malformed DWIDTH at {encoding}")
        require(len(box) == 4, f"{path}: malformed BBX at {encoding}")
        width, height, _, _ = box
        require(width >= 0 and height >= 0, f"{path}: negative BBX at {encoding}")
        try:
            bitmap_index = block.index("BITMAP")
        except ValueError as error:
            raise DistCheckError(f"{path}: missing BITMAP at {encoding}") from error
        bitmap = block[bitmap_index + 1 : -1]
        require(len(bitmap) == height, f"{path}: bitmap height mismatch at {encoding}")
        byte_width = max(1, (width + 7) // 8)
        padding = byte_width * 8 - width
        logical_rows: list[int] = []
        for row in bitmap:
            require(
                len(row) == byte_width * 2 and HEX_ROW.fullmatch(row) is not None,
                f"{path}: malformed bitmap row at {encoding}",
            )
            value = int(row, 16)
            require(
                padding == 0 or value & ((1 << padding) - 1) == 0,
                f"{path}: nonzero bitmap padding at {encoding}",
            )
            logical_rows.append(value >> padding)
        startchar = block[0].split(" ", 1)[1]
        require(startchar == f"C{encoding:04X}", f"{path}: STARTCHAR drift at {encoding}")
        glyphs[encoding] = {
            "startchar": startchar,
            "swidth": swidth,
            "dwidth": dwidth,
            "bbx": box,
            "rows": tuple(logical_rows),
        }
        encountered_codes.append(encoding)
        index = end + 1
    require(len(glyphs) == declared_glyphs, f"{path}: CHARS count mismatch")
    require(encountered_codes == sorted(encountered_codes), f"{path}: glyphs are not sorted")
    return {
        "font": _one(lines, "FONT ", path),
        "size": size,
        "font_box": font_box,
        "properties": properties,
        "glyphs": glyphs,
    }


def _round_ratio(numerator: int, denominator: int) -> int:
    require(numerator >= 0 and denominator > 0, "invalid scalable-width ratio")
    return (2 * numerator + denominator) // (2 * denominator)


def check_bdf_profile(
    path: Path,
    bdf: dict[str, object],
    artifact: dict[str, object],
    *,
    unicode_profile: bool,
) -> None:
    profile = artifact.get("bdf_profile")
    require(isinstance(profile, dict), f"{path}: missing BDF profile")
    require(bdf["font"] == profile.get("xlfd_name"), f"{path}: XLFD drift")
    fields = str(bdf["font"])[1:].split("-")
    require(str(bdf["font"]).startswith("-") and len(fields) == 14, f"{path}: malformed XLFD")
    require(
        bdf["size"]
        == (
            profile.get("pixel_size"),
            profile.get("resolution_x"),
            profile.get("resolution_y"),
        ),
        f"{path}: SIZE/profile disagreement",
    )
    properties = bdf["properties"]
    expected_properties = {
        "FOUNDRY": profile.get("foundry"),
        "FAMILY_NAME": profile.get("family_name"),
        "WEIGHT_NAME": profile.get("weight_name"),
        "SLANT": profile.get("slant"),
        "SETWIDTH_NAME": profile.get("setwidth_name"),
        "ADD_STYLE_NAME": profile.get("add_style_name"),
        "PIXEL_SIZE": profile.get("pixel_size"),
        "POINT_SIZE": profile.get("point_size"),
        "RESOLUTION_X": profile.get("resolution_x"),
        "RESOLUTION_Y": profile.get("resolution_y"),
        "SPACING": profile.get("spacing"),
        "AVERAGE_WIDTH": profile.get("average_width"),
        "CHARSET_REGISTRY": profile.get("charset_registry"),
        "CHARSET_ENCODING": profile.get("charset_encoding"),
    }
    require(set(properties) == set(expected_properties) | {"FONT_ASCENT", "FONT_DESCENT"}, f"{path}: BDF property inventory drift")
    for key, value in expected_properties.items():
        require(properties.get(key) == value, f"{path}: BDF property {key} drift")
    expected_charset = ("ISO10646", "1") if unicode_profile else ("Misc", "FontSpecific")
    require(
        (properties["CHARSET_REGISTRY"], str(properties["CHARSET_ENCODING"]))
        == expected_charset,
        f"{path}: character-set identity drift",
    )
    glyphs = bdf["glyphs"]
    require(len(glyphs) == artifact.get("glyph_count"), f"{path}: artifact glyph count drift")
    require(len(glyphs) == profile.get("source_glyph_count"), f"{path}: source glyph count drift")
    require(len(glyphs) == profile.get("bdf_glyph_count"), f"{path}: BDF glyph count drift")
    require(profile.get("omitted_nonrendering_placeholder_count") == 0, f"{path}: unexpected omitted glyph")
    require(properties["FONT_ASCENT"] >= 0 and properties["FONT_DESCENT"] >= 0, f"{path}: invalid line metrics")
    require(
        properties["FONT_ASCENT"] + properties["FONT_DESCENT"] == bdf["size"][0],
        f"{path}: line metrics do not equal pixel size",
    )
    advances = {glyph["dwidth"][0] for glyph in glyphs.values()}
    if len(advances) != 1:
        spacing = "P"
    else:
        advance = next(iter(advances))
        ascent = properties["FONT_ASCENT"]
        descent = properties["FONT_DESCENT"]
        charcell = all(
            glyph["bbx"][2] >= 0
            and glyph["bbx"][2] + glyph["bbx"][0] <= advance
            and glyph["bbx"][3] >= -descent
            and glyph["bbx"][3] + glyph["bbx"][1] <= ascent
            for glyph in glyphs.values()
        )
        spacing = "C" if charcell else "M"
    require(properties["SPACING"] == spacing, f"{path}: SPACING drift")
    average_width = _round_ratio(
        sum(abs(glyph["dwidth"][0]) for glyph in glyphs.values()) * 10,
        len(glyphs),
    )
    require(properties["AVERAGE_WIDTH"] == average_width, f"{path}: AVERAGE_WIDTH drift")
    for code, glyph in glyphs.items():
        require(glyph["dwidth"][1] == 0, f"{path}: vertical DWIDTH at {code}")
        scalable = _round_ratio(abs(glyph["dwidth"][0]) * 1000, bdf["size"][0])
        if glyph["dwidth"][0] < 0:
            scalable = -scalable
        require(glyph["swidth"] == (scalable, 0), f"{path}: SWIDTH drift at {code}")
    boxes = [glyph["bbx"] for glyph in glyphs.values()]
    minimum_x = min(box[2] for box in boxes)
    maximum_x = max(box[2] + box[0] for box in boxes)
    minimum_y = min(box[3] for box in boxes)
    maximum_y = max(box[3] + box[1] for box in boxes)
    require(
        bdf["font_box"]
        == (maximum_x - minimum_x, maximum_y - minimum_y, minimum_x, minimum_y),
        f"{path}: FONTBOUNDINGBOX drift",
    )


def expected_crop(record: dict[str, object]) -> list[dict[str, object]]:
    metrics = record["metrics"]
    glyphs: list[dict[str, object]] = []
    for slot in record["slots"]:
        if not slot["exists"]:
            continue
        width = int(slot["raster_width"])
        full_rows = [int(row, 16) for row in slot["rows"]]
        points = [
            (x, y)
            for y, row in enumerate(full_rows)
            for x in range(width)
            if row & (1 << (width - x - 1))
        ]
        if not points:
            glyphs.append(
                {
                    "code": slot["code"],
                    "bitmap_width": 1,
                    "advance": slot["advance"],
                    "x_offset": -slot["left_kern"],
                    "y_offset": 0,
                    "rows": [0],
                }
            )
            continue
        left = min(x for x, _ in points)
        right = max(x for x, _ in points) + 1
        top = min(y for _, y in points)
        bottom = max(y for _, y in points) + 1
        rows: list[int] = []
        for row in full_rows[top:bottom]:
            value = 0
            for x in range(left, right):
                value = (value << 1) | bool(row & (1 << (width - x - 1)))
            rows.append(int(value))
        glyphs.append(
            {
                "code": slot["code"],
                "bitmap_width": right - left,
                "advance": slot["advance"],
                "x_offset": -slot["left_kern"] + left,
                "y_offset": metrics["baseline"] - bottom,
                "rows": rows,
            }
        )
    return glyphs


def font_geometry(
    source: dict[str, object], glyphs: list[dict[str, object]]
) -> dict[str, object]:
    return {
        "character_height": source["metrics"]["character_height"],
        "raster_height": source["metrics"]["raster_height"],
        "baseline": source["metrics"]["baseline"],
        "glyphs": glyphs,
    }


def _compare_bdf_glyph(
    path: Path, code: int, observed: dict[str, object], expected: dict[str, object]
) -> None:
    require(observed["dwidth"] == (expected["advance"], 0), f"{path}: advance drift at {code}")
    require(
        observed["bbx"]
        == (
            expected["bitmap_width"],
            len(expected["rows"]),
            expected["x_offset"],
            expected["y_offset"],
        ),
        f"{path}: bearing or extent drift at {code}",
    )
    require(observed["rows"] == tuple(expected["rows"]), f"{path}: set pixels drift at {code}")


def _validate_mapping(mapping: dict[str, object], names: set[str], source_manifest: dict[str, object]) -> None:
    exact_keys(
        mapping,
        {
            "schema_version",
            "mapping_id",
            "unicode_version",
            "profile",
            "policy",
            "standard_repertoire",
            "bmp_private_use",
            "assignments",
            "expected",
        },
        "Unicode mapping",
    )
    require(mapping["schema_version"] == 1, "unsupported Unicode mapping")
    for key in ("mapping_id", "unicode_version", "profile"):
        bounded_string(mapping[key], f"Unicode mapping.{key}", limit=128)
    policy = exact_keys(
        mapping["policy"],
        {"raw_profile_authority", "derivative_boundary", "unknown_character_policy", "private_use_interoperability"},
        "Unicode mapping.policy",
    )
    for key, value in policy.items():
        bounded_string(value, f"Unicode mapping.policy.{key}")
    assignments = exact_keys(mapping["assignments"], names, "Unicode mapping.assignments")
    private = exact_keys(
        mapping["bmp_private_use"],
        {"first", "last", "block_size", "prior_reservations", "blocks"},
        "Unicode mapping.bmp_private_use",
    )
    standard = exact_keys(
        mapping["standard_repertoire"],
        {"identity_ranges", "explicit", "evidence"},
        "Unicode mapping.standard_repertoire",
    )
    expected = exact_keys(
        mapping["expected"],
        {"font_count", "standard_font_count", "private_use_font_count", "source_normalized_geometry_sha256"},
        "Unicode mapping.expected",
    )
    for name, repertoire in assignments.items():
        bounded_string(name, "Unicode assignment font", limit=64)
        bounded_string(repertoire, f"Unicode assignment for {name}", limit=64)
    require(set(assignments) == names, "mapping assignments are not closed over source fonts")
    for key in ("font_count", "standard_font_count", "private_use_font_count"):
        _integer(expected[key], f"mapping expected {key} is invalid", minimum=0)
    checked_sha256(expected["source_normalized_geometry_sha256"], "mapping source geometry digest")
    require(expected["font_count"] == len(names), "mapping font count drift")
    require(
        expected["source_normalized_geometry_sha256"]
        == source_manifest["expected"]["normalized_geometry_sha256"],
        "mapping is pinned to another source geometry",
    )
    identity_ranges = standard["identity_ranges"]
    explicit = standard["explicit"]
    evidence = standard["evidence"]
    require(isinstance(identity_ranges, list), "mapping identity ranges must be a list")
    require(isinstance(explicit, dict), "mapping explicit entries must be an object")
    require(isinstance(evidence, list) and evidence, "mapping standard evidence must be a non-empty list")
    for statement in evidence:
        bounded_string(statement, "mapping standard evidence")
    identity_codes: set[int] = set()
    for index, item in enumerate(identity_ranges):
        record = exact_keys(item, {"first", "last", "unicode_delta"}, f"identity range {index}")
        first_code = _integer(record["first"], f"identity range {index}: invalid first", minimum=0)
        last_code = _integer(record["last"], f"identity range {index}: invalid last", minimum=first_code)
        delta = _integer(record["unicode_delta"], f"identity range {index}: invalid delta")
        require(last_code <= 0x10FFFF, f"identity range {index}: raw code too large")
        for code in range(first_code, last_code + 1):
            scalar = code + delta
            require(0 <= scalar <= 0x10FFFF and not 0xD800 <= scalar <= 0xDFFF, f"identity range {index}: invalid scalar")
            require(not 0xE000 <= scalar <= 0xF8FF, f"identity range {index}: standard mapping enters BMP PUA")
            require(code not in identity_codes, f"identity ranges overlap at raw code {code}")
            identity_codes.add(code)
    for raw, value in explicit.items():
        require(isinstance(raw, str) and raw.isdecimal() and raw == str(int(raw)), f"invalid explicit raw code {raw!r}")
        scalar = _integer(value, f"invalid explicit scalar for raw code {raw}", minimum=0)
        require(scalar <= 0x10FFFF and not 0xD800 <= scalar <= 0xDFFF, f"invalid explicit scalar for raw code {raw}")
        require(not 0xE000 <= scalar <= 0xF8FF, f"explicit standard mapping enters BMP PUA at raw code {raw}")
        require(int(raw) not in identity_codes, f"explicit mapping overlaps identity range at raw code {raw}")

    blocks = private["blocks"]
    require(
        (
            _integer(private["first"], "invalid BMP PUA first", minimum=0),
            _integer(private["last"], "invalid BMP PUA last", minimum=0),
            _integer(private["block_size"], "invalid BMP PUA block size", minimum=1),
        )
        == (0xE000, 0xF8FF, 128),
        "BMP PUA contract drift",
    )
    require(isinstance(blocks, dict) and bool(blocks), "mapping lacks PUA blocks")
    reservations = private["prior_reservations"]
    require(isinstance(reservations, list), "mapping prior reservations must be a list")
    reservation_ranges: list[tuple[int, int, str]] = []
    for index, item in enumerate(reservations):
        record = exact_keys(item, {"project", "first", "last", "range"}, f"prior reservation {index}")
        project = bounded_string(record["project"], f"prior reservation {index}.project", limit=128)
        reserved_first = _integer(record["first"], f"prior reservation {index}: invalid first", minimum=0xE000)
        reserved_last = _integer(record["last"], f"prior reservation {index}: invalid last", minimum=reserved_first)
        require(reserved_last <= 0xF8FF, f"prior reservation {index}: outside BMP PUA")
        require(record["range"] == f"U+{reserved_first:04X}-U+{reserved_last:04X}", f"prior reservation {index}: stale range")
        reservation_ranges.append((reserved_first, reserved_last, project))
    ranges: list[tuple[int, int, str]] = []
    for name, item in blocks.items():
        bounded_string(name, "PUA block name", limit=64)
        record = exact_keys(item, {"first", "range", "label"}, f"PUA block {name}")
        start = _integer(record["first"], f"invalid PUA block {name}")
        end = start + 127
        require(start % 128 == 0 and 0xE000 <= start <= end <= 0xF8FF, f"invalid PUA block range {name}")
        require(record["range"] == f"U+{start:04X}-U+{end:04X}", f"stale PUA block label {name}")
        bounded_string(record["label"], f"PUA block {name}.label")
        ranges.append((start, end, str(name)))
    for index, left in enumerate(ranges):
        for right in ranges[index + 1 :]:
            require(left[1] < right[0] or right[1] < left[0], f"overlapping PUA blocks {left[2]} and {right[2]}")
    for start, end, name in ranges:
        for reserved_start, reserved_end, project in reservation_ranges:
            require(end < reserved_start or reserved_end < start, f"PUA block {name} overlaps prior reservation {project}")
    used_blocks = set(assignments.values()) - {"standard"}
    require(used_blocks == set(blocks), "PUA blocks are unused or unassigned")
    counts = Counter(assignments.values())
    require(counts["standard"] == expected["standard_font_count"], "standard assignment count drift")
    require(len(names) - counts["standard"] == expected["private_use_font_count"], "private-use assignment count drift")


def _validate_font_identities(
    identities: dict[str, object], names: set[str], source_manifest: dict[str, object]
) -> dict[str, dict[str, object]]:
    exact_keys(
        identities,
        {
            "schema_version",
            "mapping_id",
            "profile",
            "source",
            "policy",
            "x_named_sizes",
            "families",
            "faces",
            "desktop_style_disambiguators",
            "assignments",
            "expected",
        },
        "font identities",
    )
    require(identities["schema_version"] == 1, "unsupported font-identity mapping")
    for key in ("mapping_id", "profile"):
        bounded_string(identities[key], f"font identities.{key}", limit=128)
    source = exact_keys(
        identities["source"],
        {"profile", "vlod_sha256", "x_size_evidence"},
        "font identities.source",
    )
    bounded_string(source["profile"], "font identities.source.profile", limit=128)
    checked_sha256(source["vlod_sha256"], "font identities.source.vlod_sha256")
    require(
        source["vlod_sha256"] == source_manifest["source"]["sha256"],
        "font identities are pinned to another Genera world",
    )
    x_size_evidence = exact_keys(
        source["x_size_evidence"],
        {"path", "sha256"},
        "font identities.source.x_size_evidence",
    )
    bounded_string(
        x_size_evidence["path"],
        "font identities.source.x_size_evidence.path",
        limit=256,
    )
    checked_sha256(
        x_size_evidence["sha256"],
        "font identities.source.x_size_evidence.sha256",
    )
    policy = exact_keys(
        identities["policy"],
        {
            "artifact_identity",
            "primary_identity",
            "desktop_identity",
            "desktop_disambiguation",
            "specialty_identity",
            "size_identity",
            "unmapped_identity",
        },
        "font identities.policy",
    )
    for key, value in policy.items():
        bounded_string(value, f"font identities.policy.{key}")

    named_sizes = identities["x_named_sizes"]
    require(isinstance(named_sizes, dict) and named_sizes, "font identities lack X named sizes")
    for name, x_size in named_sizes.items():
        bounded_string(name, "logical size", limit=64)
        _integer(x_size, f"logical X size {name}: invalid size", minimum=1)
    require(
        len(set(named_sizes.values())) == len(named_sizes),
        "logical sizes do not have unique nominal X sizes",
    )

    families = identities["families"]
    require(isinstance(families, dict) and families, "font identities lack families")
    desktop_family_names: set[str] = set()
    for name, value in families.items():
        bounded_string(name, "typographic family ID", limit=64)
        family = exact_keys(value, {"family_name", "kind"}, f"typographic family {name}")
        bounded_string(family["family_name"], f"typographic family {name}.family_name", limit=128)
        require(
            family["kind"] in {"text", "specialty", "unmapped"},
            f"typographic family {name}: invalid kind",
        )
        require(
            family["family_name"] not in desktop_family_names,
            f"typographic family {name}: duplicate desktop family name",
        )
        desktop_family_names.add(family["family_name"])

    faces = identities["faces"]
    require(isinstance(faces, dict) and faces, "font identities lack faces")
    for name, value in faces.items():
        bounded_string(name, "typographic face ID", limit=64)
        face = exact_keys(
            value,
            {"weight_name", "slant", "setwidth_name", "add_style_name"},
            f"typographic face {name}",
        )
        for key in ("weight_name", "slant", "setwidth_name"):
            bounded_string(face[key], f"typographic face {name}.{key}", limit=64)
        add_style = face["add_style_name"]
        require(
            isinstance(add_style, str)
            and len(add_style) <= 64
            and all(character.isprintable() for character in add_style),
            f"typographic face {name}.add_style_name: invalid string",
        )
        require(
            face["weight_name"] in {"Medium", "Bold"},
            f"typographic face {name}: unsupported weight",
        )
        require(face["slant"] in {"R", "I"}, f"typographic face {name}: invalid slant")
        require(
            face["setwidth_name"] in {"Condensed", "Normal", "Expanded"},
            f"typographic face {name}: unsupported set width",
        )

    disambiguators = identities["desktop_style_disambiguators"]
    require(
        isinstance(disambiguators, dict),
        "font identities desktop style disambiguators must be an object",
    )
    require(
        set(disambiguators) <= names,
        "desktop style disambiguator references an unknown font",
    )
    for name, value in disambiguators.items():
        bounded_string(value, f"desktop style disambiguator for {name}", limit=64)

    assignments = exact_keys(identities["assignments"], names, "font identities.assignments")
    status_counts: Counter[str] = Counter()
    primary_count = 0
    role_only_count = 0
    used_families: set[str] = set()
    resolved: dict[str, dict[str, object]] = {}
    for name, value in assignments.items():
        assignment = exact_keys(
            value,
            {"mapping_status", "primary", "typographic_family", "typographic_face"},
            f"font identity {name}",
        )
        status = assignment["mapping_status"]
        require(
            status in {"mapped", "role-mapped", "unmapped"},
            f"font identity {name}: invalid mapping status",
        )
        status_counts[str(status)] += 1
        family_id = assignment["typographic_family"]
        face_id = assignment["typographic_face"]
        require(family_id in families, f"font identity {name}: unknown typographic family")
        require(face_id in faces, f"font identity {name}: unknown typographic face")
        require(
            families[family_id]["kind"]
            == {"mapped": "text", "role-mapped": "specialty", "unmapped": "unmapped"}[status],
            f"font identity {name}: mapping status and family kind differ",
        )
        used_families.add(str(family_id))
        primary = assignment["primary"]
        logical_name = None
        nominal_x_size = None
        if primary is not None:
            primary_count += 1
            selector = exact_keys(
                primary,
                {"family", "face", "size", "character_set"},
                f"font identity {name}.primary",
            )
            for key, item in selector.items():
                bounded_string(item, f"font identity {name}.primary.{key}", limit=64)
            require(selector["family"] in families, f"font identity {name}: unknown logical family")
            require(selector["face"] in faces, f"font identity {name}: unknown logical face")
            require(selector["size"] in named_sizes, f"font identity {name}: unknown logical size")
            require(
                selector["face"] == face_id,
                f"font identity {name}: primary and desktop face differ",
            )
            if status == "mapped":
                require(
                    selector["family"] == family_id,
                    f"font identity {name}: primary and desktop family differ",
                )
            logical_name = "/".join(
                (str(selector["family"]), str(selector["face"]), str(selector["size"]))
            )
            nominal_x_size = named_sizes[selector["size"]]
        if status == "role-mapped" and primary is None:
            role_only_count += 1
        require(status != "mapped" or primary is not None, f"font identity {name}: mapped font lacks primary style")
        require(status != "unmapped" or primary is None, f"font identity {name}: unmapped font has primary style")
        family = families[family_id]
        face = faces[face_id]
        disambiguator = disambiguators.get(name)
        add_style_name = " ".join(
            value for value in (disambiguator, face["add_style_name"]) if value
        )
        resolved[str(name)] = {
            "mapping_status": status,
            "logical_name": logical_name,
            "primary": dict(primary) if primary is not None else None,
            "nominal_x_size": nominal_x_size,
            "desktop_style_disambiguator": disambiguator,
            "typographic": {
                "family_name": family["family_name"],
                "weight_name": face["weight_name"],
                "slant": face["slant"],
                "setwidth_name": face["setwidth_name"],
                "add_style_name": add_style_name,
            },
        }

    expected = exact_keys(
        identities["expected"],
        {
            "font_count",
            "mapped_font_count",
            "primary_style_count",
            "role_only_count",
            "unmapped_font_count",
            "typographic_family_count",
            "desktop_style_disambiguator_count",
        },
        "font identities.expected",
    )
    for key, value in expected.items():
        _integer(value, f"font identities.expected.{key}: invalid count", minimum=0)
    require(expected["font_count"] == len(assignments), "font-identity count changed")
    require(
        expected["mapped_font_count"] == status_counts["mapped"] + status_counts["role-mapped"],
        "mapped font-identity count changed",
    )
    require(expected["primary_style_count"] == primary_count, "primary style count changed")
    require(expected["role_only_count"] == role_only_count, "role-only font count changed")
    require(expected["unmapped_font_count"] == status_counts["unmapped"], "unmapped font count changed")
    require(
        expected["typographic_family_count"] == len(used_families) == len(families),
        "typographic family closure changed",
    )
    require(
        expected["desktop_style_disambiguator_count"] == len(disambiguators),
        "desktop style disambiguator count changed",
    )
    return resolved


def expected_mapping(
    mapping: dict[str, object], font_name: str, raw_code: int
) -> tuple[int, str]:
    repertoire = mapping["assignments"][font_name]
    if repertoire != "standard":
        require(0 <= raw_code < 128, f"{font_name}: PUA code exceeds its 128-scalar block")
        return mapping["bmp_private_use"]["blocks"][repertoire]["first"] + raw_code, "private-use"
    standard = mapping["standard_repertoire"]
    explicit = standard.get("explicit")
    ranges = standard.get("identity_ranges")
    require(isinstance(explicit, dict), "standard mapping lacks explicit entries")
    require(isinstance(ranges, list), "standard mapping lacks identity ranges")
    if str(raw_code) in explicit:
        scalar = _integer(explicit[str(raw_code)], f"invalid standard scalar for {raw_code}")
    else:
        matches = [
            record
            for record in ranges
            if isinstance(record, dict)
            and type(record.get("first")) is int
            and type(record.get("last")) is int
            and record["first"] <= raw_code <= record["last"]
        ]
        require(len(matches) == 1, f"standard repertoire has no unique mapping for raw code {raw_code:#o}")
        delta = _integer(matches[0].get("unicode_delta"), f"invalid standard delta for {raw_code}")
        scalar = raw_code + delta
    require(0 <= scalar <= 0x10FFFF and not 0xD800 <= scalar <= 0xDFFF, f"invalid Unicode scalar U+{scalar:04X}")
    require(not 0xE000 <= scalar <= 0xF8FF, f"standard mapping assigns BMP PUA at raw code {raw_code:#o}")
    return scalar, "standard"


def parse_fonts_dir(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="ascii").splitlines()
    require(bool(lines), f"{path}: empty fonts.dir")
    entries: dict[str, str] = {}
    for line_number, line in enumerate(lines[1:], 2):
        require(" " in line, f"{path}:{line_number}: malformed fonts.dir entry")
        filename, xlfd = line.split(" ", 1)
        require(filename not in entries, f"{path}:{line_number}: duplicate filename")
        entries[filename] = xlfd
    require(int(lines[0]) == len(entries), f"{path}: fonts.dir count mismatch")
    return entries


def parse_aliases(path: Path) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("!"):
            continue
        try:
            fields = shlex.split(stripped, posix=True)
        except ValueError as error:
            raise DistCheckError(f"{path}:{line_number}: malformed alias") from error
        require(len(fields) == 2, f"{path}:{line_number}: alias needs two fields")
        alias, target = fields
        require(alias not in aliases, f"{path}:{line_number}: duplicate alias {alias}")
        aliases[alias] = target
    return aliases


def check_x_indexes(
    output: Path,
    profile: str,
    artifacts: list[dict[str, object]],
    *,
    unicode_profile: bool,
) -> tuple[dict[str, str], dict[str, str]]:
    directory = output / profile / "bdf"
    expected_dir = {
        Path(str(record["bdf_path"])).name: str(record["bdf_profile"]["xlfd_name"])
        for record in artifacts
    }
    require(len(expected_dir) == len(artifacts), f"{profile}: duplicate BDF filename")
    observed_dir = parse_fonts_dir(directory / "fonts.dir")
    require(observed_dir == expected_dir, f"{profile}: fonts.dir mappings drift")
    expected_aliases: dict[str, str] = {}
    prefix = "genera-unicode" if unicode_profile else "genera"
    for record in artifacts:
        stem = safe_filename(str(record["name"]))
        target = str(record["bdf_profile"]["xlfd_name"])
        expected_aliases[f"{prefix}-{stem}"] = target
        expected_aliases[f"{prefix}-runtime-{stem}"] = target
    observed_aliases = parse_aliases(directory / "fonts.alias")
    require(observed_aliases == expected_aliases, f"{profile}: fonts.alias mappings drift")
    require(set(observed_aliases.values()) <= set(observed_dir.values()), f"{profile}: alias target is not indexed")
    return observed_dir, observed_aliases


def check_checksums(output: Path, observed_files: dict[str, Path]) -> None:
    checksum_path = output / "SHA256SUMS"
    lines = checksum_path.read_text(encoding="ascii").splitlines()
    records: dict[str, str] = {}
    order: list[str] = []
    for line_number, line in enumerate(lines, 1):
        require("  " in line, f"SHA256SUMS:{line_number}: malformed record")
        digest, raw_relative = line.split("  ", 1)
        require(HEX_DIGEST.fullmatch(digest) is not None, f"SHA256SUMS:{line_number}: malformed digest")
        relative = safe_relative(raw_relative, "checksum").as_posix()
        require(relative != "SHA256SUMS", "SHA256SUMS must not checksum itself")
        require(relative not in records, f"SHA256SUMS:{line_number}: duplicate path {relative}")
        records[relative] = digest
        order.append(relative)
    expected = set(observed_files) - {"SHA256SUMS"}
    require(set(records) == expected, "SHA256SUMS file coverage drift")
    require(order == sorted(order), "SHA256SUMS paths are not sorted")
    for relative, digest in records.items():
        require(sha256(observed_files[relative]) == digest, f"checksum mismatch for {relative}")


def _check_external_profile(
    output: Path,
    profile: str,
    entries: dict[str, str],
    bdftopcf: str,
    mkfontdir: str,
    temporary: Path,
) -> int:
    directory = temporary / profile
    directory.mkdir(parents=True)
    expected: dict[str, str] = {}
    for filename, xlfd in sorted(entries.items()):
        source = output / profile / "bdf" / filename
        destination = directory / f"{Path(filename).stem}.pcf"
        process = subprocess.run(
            [bdftopcf, str(source), "-o", str(destination)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        require(process.returncode == 0, f"bdftopcf rejected {source}: {process.stderr.strip()}")
        expected[destination.name] = xlfd.casefold()
    process = subprocess.run(
        [mkfontdir, str(directory)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    require(process.returncode == 0, f"mkfontdir rejected {profile}: {process.stderr.strip()}")
    generated = {
        filename: xlfd.casefold()
        for filename, xlfd in parse_fonts_dir(directory / "fonts.dir").items()
    }
    require(generated == expected, f"mkfontdir indexed different {profile} PCF identities")
    return len(expected)


def check_external_tools(
    output: Path, raw_entries: dict[str, str], unicode_entries: dict[str, str]
) -> dict[str, object]:
    bdftopcf = shutil.which("bdftopcf")
    mkfontdir = shutil.which("mkfontdir")
    require(bdftopcf is not None, "bdftopcf is required for external validation")
    require(mkfontdir is not None, "mkfontdir is required for external validation")
    with tempfile.TemporaryDirectory(prefix="genera-font-dist-check-") as directory:
        temporary = Path(directory)
        raw_count = _check_external_profile(
            output, "raw", raw_entries, bdftopcf, mkfontdir, temporary
        )
        unicode_count = _check_external_profile(
            output, "unicode", unicode_entries, bdftopcf, mkfontdir, temporary
        )
    return {
        "bdftopcf": True,
        "mkfontdir": True,
        "raw_pcf_count": raw_count,
        "unicode_pcf_count": unicode_count,
    }


def _artifact_names(catalog: dict[str, object], label: str) -> list[str]:
    artifacts = catalog.get("fonts")
    require(isinstance(artifacts, list), f"{label} catalog lacks font artifacts")
    require(all(isinstance(record, dict) for record in artifacts), f"{label} catalog has invalid font record")
    names = [str(record.get("name")) for record in artifacts]
    require(names == sorted(names), f"{label} catalog fonts are not sorted")
    require(len(names) == len(set(names)), f"{label} catalog has duplicate font name")
    return names


def check_distribution(output: Path, *, external_tools: bool = False) -> dict[str, object]:
    output = output.resolve()
    observed_files = _regular_tree(output)
    required_top = {
        "BUILD-MANIFEST.json",
        "FONT-IDENTITIES.json",
        "SOURCE-MANIFEST.json",
        "SHA256SUMS",
    }
    require(required_top <= set(observed_files), "distribution lacks required top-level manifests")
    source_manifest, source_catalog, source_fonts = check_source_closure(
        output / "SOURCE-MANIFEST.json"
    )
    build_manifest = load_json(output / "BUILD-MANIFEST.json")
    raw_catalog = load_json(output / "raw" / "catalog.json")
    unicode_catalog = load_json(output / "unicode" / "catalog.json")
    mapping = load_json(UNICODE_MAPPING)
    font_identities = load_json(FONT_IDENTITIES)
    corpus_profile = load_json(CORPUS_PROFILE)
    require(
        (output / "unicode" / "UNICODE-MAPPING.json").read_bytes()
        == UNICODE_MAPPING.read_bytes(),
        "distribution Unicode mapping is stale",
    )
    require(
        (output / "FONT-IDENTITIES.json").read_bytes() == FONT_IDENTITIES.read_bytes(),
        "distribution font identities are stale",
    )
    names = set(source_fonts)
    _validate_mapping(mapping, names, source_manifest)
    resolved_identities = _validate_font_identities(
        font_identities, names, source_manifest
    )

    require(build_manifest.get("schema_version") == 1, "unsupported build manifest")
    require(raw_catalog.get("schema_version") == 1, "unsupported raw catalog")
    require(unicode_catalog.get("schema_version") == 1, "unsupported Unicode catalog")
    exact_keys(
        corpus_profile,
        {
            "schema_version",
            "profile",
            "source_manifest_sha256",
            "unicode_mapping_sha256",
            "font_identities_sha256",
            "source_normalized_geometry_sha256",
            "expected",
        },
        "corpus profile",
    )
    require(corpus_profile["schema_version"] == 1, "unsupported corpus profile")
    require(
        build_manifest.get("profile") == corpus_profile["profile"],
        "build/corpus profile identity drift",
    )
    require(
        corpus_profile["source_normalized_geometry_sha256"]
        == source_manifest["expected"]["normalized_geometry_sha256"],
        "corpus profile is pinned to another source geometry",
    )
    checked_sha256(corpus_profile["source_manifest_sha256"], "corpus profile.source_manifest_sha256")
    checked_sha256(corpus_profile["unicode_mapping_sha256"], "corpus profile.unicode_mapping_sha256")
    checked_sha256(corpus_profile["font_identities_sha256"], "corpus profile.font_identities_sha256")
    require(
        corpus_profile["source_manifest_sha256"] == sha256(SOURCE_MANIFEST),
        "corpus profile is pinned to another source manifest",
    )
    require(
        corpus_profile["unicode_mapping_sha256"] == sha256(UNICODE_MAPPING),
        "corpus profile is pinned to another Unicode mapping",
    )
    require(
        corpus_profile["font_identities_sha256"] == sha256(FONT_IDENTITIES),
        "corpus profile is pinned to other logical font identities",
    )
    require(
        build_manifest.get("corpus_profile_sha256") == sha256(CORPUS_PROFILE),
        "build manifest has a stale corpus profile",
    )
    reviewed = exact_keys(
        corpus_profile["expected"],
        {
            "font_count",
            "source_slot_count",
            "raw_glyph_count",
            "unicode_glyph_count",
            "standard_unicode_glyph_count",
            "private_use_unicode_glyph_count",
            "content_class_counts",
            "raw_alias_count",
            "unicode_alias_count",
            "specimen_count",
            "raw_geometry_inventory_sha256",
            "unicode_geometry_inventory_sha256",
            "font_identity_counts",
            "desktop_identity_count",
        },
        "corpus profile.expected",
    )
    for key, value in reviewed.items():
        require(build_manifest.get(key) == value, f"reviewed build invariant changed: {key}")
    require(build_manifest.get("source") == source_manifest.get("source"), "build source identity drift")
    require(build_manifest.get("source_manifest_sha256") == sha256(SOURCE_MANIFEST), "build source-manifest hash drift")
    require(
        build_manifest.get("unicode_mapping")
        == {"id": mapping.get("mapping_id"), "sha256": sha256(UNICODE_MAPPING)},
        "build Unicode mapping identity drift",
    )
    require(
        build_manifest.get("font_identities")
        == {
            "id": font_identities.get("mapping_id"),
            "sha256": sha256(FONT_IDENTITIES),
        },
        "build logical-font mapping identity drift",
    )
    require(raw_catalog.get("source_manifest_sha256") == sha256(SOURCE_MANIFEST), "raw catalog source hash drift")
    require(unicode_catalog.get("mapping_id") == mapping.get("mapping_id"), "Unicode catalog mapping id drift")
    require(unicode_catalog.get("mapping_sha256") == sha256(UNICODE_MAPPING), "Unicode catalog mapping hash drift")
    for label, catalog in (("raw", raw_catalog), ("Unicode", unicode_catalog)):
        require(
            catalog.get("font_identity_mapping_id") == font_identities.get("mapping_id"),
            f"{label} catalog logical-font mapping ID drift",
        )
        require(
            catalog.get("font_identity_mapping_sha256") == sha256(FONT_IDENTITIES),
            f"{label} catalog logical-font mapping hash drift",
        )

    raw_names = _artifact_names(raw_catalog, "raw")
    unicode_names = _artifact_names(unicode_catalog, "Unicode")
    require(raw_names == unicode_names == sorted(names), "raw/Unicode/source font inventories differ")
    raw_artifacts = raw_catalog["fonts"]
    unicode_artifacts = unicode_catalog["fonts"]
    desktop_identities: dict[tuple[object, ...], str] = {}
    for name in sorted(names):
        typography = resolved_identities[name]["typographic"]
        key = (
            typography["family_name"],
            typography["weight_name"],
            typography["slant"],
            typography["setwidth_name"],
            typography["add_style_name"],
            source_fonts[name]["metrics"]["character_height"],
        )
        require(
            key not in desktop_identities,
            f"desktop font identity collision: {desktop_identities.get(key)} and {name}",
        )
        desktop_identities[key] = name
    require(
        build_manifest.get("desktop_identity_count") == len(desktop_identities),
        "desktop font identity count drift",
    )
    expected_files = {
        "BUILD-MANIFEST.json",
        "FONT-IDENTITIES.json",
        "SOURCE-MANIFEST.json",
        "SHA256SUMS",
        "raw/catalog.json",
        "raw/bdf/fonts.dir",
        "raw/bdf/fonts.alias",
        "unicode/catalog.json",
        "unicode/UNICODE-MAPPING.json",
        "unicode/bdf/fonts.dir",
        "unicode/bdf/fonts.alias",
    }
    for name in ("NOTICE.md", "LICENSE"):
        repository_path = ROOT / name
        if repository_path.is_file():
            expected_files.add(name)
            require((output / name).read_bytes() == repository_path.read_bytes(), f"distribution {name} is stale")

    raw_inventory: list[dict[str, object]] = []
    unicode_inventory: list[dict[str, object]] = []
    standard_glyph_count = 0
    private_glyph_count = 0
    content_counts: Counter[str] = Counter()
    for raw_artifact, unicode_artifact in zip(raw_artifacts, unicode_artifacts):
        name = str(raw_artifact["name"])
        require(unicode_artifact.get("name") == name, f"profile identity drift at {name}")
        logical_identity = resolved_identities[name]
        require(
            raw_artifact.get("logical_identity") == logical_identity,
            f"{name}: raw logical identity drift",
        )
        require(
            unicode_artifact.get("logical_identity") == logical_identity,
            f"{name}: Unicode logical identity drift",
        )
        typography = logical_identity["typographic"]
        for label, artifact in (("raw", raw_artifact), ("Unicode", unicode_artifact)):
            profile = artifact.get("bdf_profile")
            require(isinstance(profile, dict), f"{name}: {label} BDF profile missing")
            for key in (
                "family_name",
                "weight_name",
                "slant",
                "setwidth_name",
                "add_style_name",
            ):
                require(
                    profile.get(key) == typography[key],
                    f"{name}: {label} typographic {key} drift",
                )
        source = source_fonts[name]
        stem = safe_filename(name)
        expected_paths = {
            "source_record": f"fonts/{stem}.json",
            "raw_bdf": f"raw/bdf/{stem}.bdf",
            "raw_json": f"raw/json/{stem}.json",
            "raw_sheet": f"raw/sheets/{stem}.png",
            "unicode_bdf": f"unicode/bdf/{stem}.bdf",
        }
        require(raw_artifact.get("source_record") == expected_paths["source_record"], f"{name}: source path drift")
        require(raw_artifact.get("bdf_path") == expected_paths["raw_bdf"], f"{name}: raw BDF path drift")
        require(raw_artifact.get("json_path") == expected_paths["raw_json"], f"{name}: raw JSON path drift")
        require(raw_artifact.get("sheet_path") == expected_paths["raw_sheet"], f"{name}: sheet path drift")
        require(unicode_artifact.get("bdf_path") == expected_paths["unicode_bdf"], f"{name}: Unicode BDF path drift")
        specimen_relative = safe_relative(unicode_artifact.get("specimen_path"), "specimen").as_posix()
        require(specimen_relative.startswith("unicode/specimens/"), f"{name}: specimen outside Unicode profile")
        expected_files.update(
            {
                expected_paths["raw_bdf"],
                expected_paths["raw_json"],
                expected_paths["raw_sheet"],
                expected_paths["unicode_bdf"],
                specimen_relative,
            }
        )
        require(
            (output / expected_paths["raw_json"]).read_bytes()
            == (SOURCE_ROOT / expected_paths["source_record"]).read_bytes(),
            f"{name}: normalized source JSON changed in distribution",
        )
        raw_path = output / expected_paths["raw_bdf"]
        unicode_path = output / expected_paths["unicode_bdf"]
        require(raw_artifact.get("bdf_sha256") == sha256(raw_path), f"{name}: raw BDF hash stale")
        require(raw_artifact.get("sheet_sha256") == sha256(output / expected_paths["raw_sheet"]), f"{name}: sheet hash stale")
        require(unicode_artifact.get("bdf_sha256") == sha256(unicode_path), f"{name}: Unicode BDF hash stale")
        require(unicode_artifact.get("specimen_sha256") == sha256(output / specimen_relative), f"{name}: specimen hash stale")
        require((output / expected_paths["raw_sheet"]).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), f"{name}: malformed sheet PNG")
        require((output / specimen_relative).read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), f"{name}: malformed specimen PNG")

        expected_raw_glyphs = expected_crop(source)
        raw_geometry = font_geometry(source, expected_raw_glyphs)
        require(raw_artifact.get("glyph_count") == len(expected_raw_glyphs), f"{name}: raw glyph count drift")
        require(raw_artifact.get("geometry_sha256") == canonical_sha256(raw_geometry), f"{name}: raw geometry digest drift")
        raw_bdf = parse_bdf(raw_path)
        check_bdf_profile(raw_path, raw_bdf, raw_artifact, unicode_profile=False)
        require(raw_bdf["size"][0] == source["metrics"]["character_height"], f"{name}: raw character height drift")
        require(raw_bdf["properties"]["FONT_ASCENT"] == source["metrics"]["baseline"], f"{name}: raw baseline drift")
        expected_by_code = {glyph["code"]: glyph for glyph in expected_raw_glyphs}
        require(set(raw_bdf["glyphs"]) == set(expected_by_code), f"{name}: raw repertoire drift")
        for code, expected_glyph in expected_by_code.items():
            _compare_bdf_glyph(raw_path, code, raw_bdf["glyphs"][code], expected_glyph)
        raw_inventory.append({"name": name, "geometry": raw_geometry})

        repertoire = mapping["assignments"][name]
        require(unicode_artifact.get("repertoire") == repertoire, f"{name}: repertoire assignment drift")
        resolved: list[dict[str, object]] = []
        unicode_glyphs: list[dict[str, object]] = []
        used_scalars: set[int] = set()
        for glyph in expected_raw_glyphs:
            scalar, kind = expected_mapping(mapping, name, int(glyph["code"]))
            require(scalar not in used_scalars, f"{name}: Unicode mapping is not injective")
            used_scalars.add(scalar)
            resolved.append(
                {
                    "raw_code": glyph["code"],
                    "raw_octal": f"{glyph['code']:o}",
                    "unicode": scalar,
                    "unicode_hex": f"U+{scalar:04X}",
                    "kind": kind,
                }
            )
            unicode_glyphs.append(glyph | {"code": scalar})
        require(unicode_artifact.get("resolved_mappings") == resolved, f"{name}: resolved Unicode mapping drift")
        unicode_geometry = font_geometry(source, unicode_glyphs)
        display_geometry = font_geometry(
            source, [{key: value for key, value in glyph.items() if key != "code"} for glyph in unicode_glyphs]
        )
        require(unicode_artifact.get("glyph_count") == len(unicode_glyphs), f"{name}: Unicode glyph count drift")
        require(unicode_artifact.get("geometry_sha256") == canonical_sha256(unicode_geometry), f"{name}: Unicode geometry digest drift")
        require(unicode_artifact.get("display_geometry_sha256") == canonical_sha256(display_geometry), f"{name}: Unicode display-geometry digest drift")
        standard_count = sum(item["kind"] == "standard" for item in resolved)
        private_count = len(resolved) - standard_count
        require(unicode_artifact.get("standard_glyph_count") == standard_count, f"{name}: standard glyph count drift")
        require(unicode_artifact.get("private_use_glyph_count") == private_count, f"{name}: PUA glyph count drift")
        standard_glyph_count += standard_count
        private_glyph_count += private_count

        unicode_bdf = parse_bdf(unicode_path)
        check_bdf_profile(unicode_path, unicode_bdf, unicode_artifact, unicode_profile=True)
        require(raw_bdf["size"] == unicode_bdf["size"], f"{name}: Unicode SIZE changed")
        require(raw_bdf["font_box"] == unicode_bdf["font_box"], f"{name}: Unicode bounding box changed")
        require(set(raw_bdf["properties"]) == set(unicode_bdf["properties"]), f"{name}: Unicode property inventory changed")
        allowed_property_changes = {"CHARSET_REGISTRY", "CHARSET_ENCODING"}
        for key in raw_bdf["properties"]:
            if key not in allowed_property_changes:
                require(raw_bdf["properties"][key] == unicode_bdf["properties"][key], f"{name}: Unicode changed property {key}")
        require(set(unicode_bdf["glyphs"]) == used_scalars, f"{name}: Unicode repertoire drift")
        for mapping_record in resolved:
            raw_code = int(mapping_record["raw_code"])
            scalar = int(mapping_record["unicode"])
            raw_glyph = raw_bdf["glyphs"][raw_code]
            unicode_glyph = unicode_bdf["glyphs"][scalar]
            require(
                {key: raw_glyph[key] for key in ("swidth", "dwidth", "bbx", "rows")}
                == {key: unicode_glyph[key] for key in ("swidth", "dwidth", "bbx", "rows")},
                f"{name}: raw-to-Unicode geometry changed at raw code {raw_code:#o}",
            )
        visible = {code for code, glyph in unicode_bdf["glyphs"].items() if any(glyph["rows"])}
        content_class = "latin" if visible & LATIN_LETTERS else "symbols"
        require(unicode_artifact.get("content_class") == content_class, f"{name}: content class drift")
        content_counts[content_class] += 1
        pangram_codes = {ord(character) for character in PANGRAM}
        expected_specimen_kind = "pangram" if content_class == "latin" and pangram_codes <= set(unicode_bdf["glyphs"]) else "glyph-sheet"
        require(unicode_artifact.get("specimen_kind") == expected_specimen_kind, f"{name}: specimen selection drift")
        require(f"unicode/specimens/{content_class}/" in specimen_relative, f"{name}: specimen class path drift")
        unicode_inventory.append({"name": name, "geometry": unicode_geometry})

    require(set(observed_files) == expected_files, "distribution file closure drift")
    check_checksums(output, observed_files)
    require(raw_catalog.get("font_count") == len(raw_artifacts), "raw catalog font count drift")
    require(unicode_catalog.get("font_count") == len(unicode_artifacts), "Unicode catalog font count drift")
    raw_glyph_count = sum(int(record["glyph_count"]) for record in raw_artifacts)
    unicode_glyph_count = sum(int(record["glyph_count"]) for record in unicode_artifacts)
    require(raw_catalog.get("glyph_count") == raw_glyph_count, "raw catalog glyph count drift")
    require(unicode_catalog.get("glyph_count") == unicode_glyph_count, "Unicode catalog glyph count drift")
    require(unicode_catalog.get("standard_glyph_count") == standard_glyph_count, "standard glyph total drift")
    require(unicode_catalog.get("private_use_glyph_count") == private_glyph_count, "PUA glyph total drift")
    require(unicode_catalog.get("content_class_counts") == dict(sorted(content_counts.items())), "content-class totals drift")
    raw_inventory_sha = canonical_sha256(raw_inventory)
    unicode_inventory_sha = canonical_sha256(unicode_inventory)
    require(raw_catalog.get("geometry_inventory_sha256") == raw_inventory_sha, "raw geometry inventory drift")
    require(unicode_catalog.get("geometry_inventory_sha256") == unicode_inventory_sha, "Unicode geometry inventory drift")

    raw_entries, raw_aliases = check_x_indexes(
        output, "raw", raw_artifacts, unicode_profile=False
    )
    unicode_entries, unicode_aliases = check_x_indexes(
        output, "unicode", unicode_artifacts, unicode_profile=True
    )
    require(raw_catalog.get("x_indexes") == {"font_count": len(raw_entries), "alias_count": len(raw_aliases)}, "raw index counts drift")
    require(unicode_catalog.get("x_indexes") == {"font_count": len(unicode_entries), "alias_count": len(unicode_aliases)}, "Unicode index counts drift")
    require(build_manifest.get("font_count") == len(raw_artifacts), "build font count drift")
    require(build_manifest.get("source_slot_count") == source_catalog.get("slot_count"), "build source slot count drift")
    require(build_manifest.get("raw_glyph_count") == raw_glyph_count, "build raw glyph count drift")
    require(build_manifest.get("unicode_glyph_count") == unicode_glyph_count, "build Unicode glyph count drift")
    require(build_manifest.get("standard_unicode_glyph_count") == standard_glyph_count, "build standard glyph count drift")
    require(build_manifest.get("private_use_unicode_glyph_count") == private_glyph_count, "build PUA glyph count drift")
    require(build_manifest.get("content_class_counts") == dict(sorted(content_counts.items())), "build content class drift")
    require(build_manifest.get("raw_alias_count") == len(raw_aliases), "build raw alias count drift")
    require(build_manifest.get("unicode_alias_count") == len(unicode_aliases), "build Unicode alias count drift")
    require(build_manifest.get("specimen_count") == len(unicode_artifacts), "build specimen count drift")
    require(build_manifest.get("raw_geometry_inventory_sha256") == raw_inventory_sha, "build raw geometry digest drift")
    require(build_manifest.get("unicode_geometry_inventory_sha256") == unicode_inventory_sha, "build Unicode geometry digest drift")

    external = (
        check_external_tools(output, raw_entries, unicode_entries)
        if external_tools
        else {"requested": False}
    )
    return {
        "status": "ok",
        "output": str(output),
        "font_count": len(raw_artifacts),
        "raw_glyph_count": raw_glyph_count,
        "unicode_glyph_count": unicode_glyph_count,
        "standard_unicode_glyph_count": standard_glyph_count,
        "private_use_unicode_glyph_count": private_glyph_count,
        "file_count": len(observed_files),
        "external_tools": external,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--external-tools",
        action="store_true",
        help="compile all BDFs with bdftopcf and independently index the PCFs with mkfontdir",
    )
    args = parser.parse_args()
    try:
        result = check_distribution(args.output, external_tools=args.external_tools)
    except (DistCheckError, OSError, UnicodeError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
