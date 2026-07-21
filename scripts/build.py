#!/usr/bin/env python3
"""Build raw and Unicode Genera 8.5 resident bitmap-font profiles."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import string

from lisp_machine_fonts import (
    BitmapFont,
    Glyph,
    bdf_profile,
    prepare_output_directory,
    safe_filename,
    write_bdf,
    write_sheet,
    write_text_specimen,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "sources" / "genera-8.5"
SOURCE_MANIFEST = SOURCE_ROOT / "SOURCE-MANIFEST.json"
SOURCE_CATALOG = SOURCE_ROOT / "catalog.json"
SOURCE_VALIDATION = SOURCE_ROOT / "validation.json"
UNICODE_MAPPING = ROOT / "config" / "unicode-mapping.json"
FONT_IDENTITIES = ROOT / "config" / "font-identities.json"
CORPUS_PROFILE = ROOT / "config" / "corpus-profile.json"
DEFAULT_OUTPUT = ROOT / "dist"
PANGRAM = "The five boxing Lisp wizards jump quickly."
LATIN_LETTERS = frozenset(map(ord, string.ascii_letters))
OWNED_OUTPUT_NAMES = {
    "raw",
    "unicode",
    "release",
    "packages",
    "BUILD-MANIFEST.json",
    "FONT-IDENTITIES.json",
    "SOURCE-MANIFEST.json",
    "NOTICE.md",
    "LICENSE",
    "SHA256SUMS",
}


class BuildError(ValueError):
    """The requested build violates a closed source or mapping contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise BuildError(message)


def require_exact_keys(value: object, keys: set[str], context: str) -> dict:
    require(isinstance(value, dict), f"{context}: expected an object")
    observed = set(value)
    require(
        observed == keys,
        f"{context}: schema keys differ; "
        f"missing={sorted(keys - observed)}, unexpected={sorted(observed - keys)}",
    )
    return value


def require_integer(value: object, context: str, *, minimum: int | None = None) -> int:
    require(type(value) is int, f"{context}: expected an integer")
    result = int(value)
    if minimum is not None:
        require(result >= minimum, f"{context}: value is below {minimum}")
    return result


def require_bounded_string(value: object, context: str, *, limit: int = 512) -> str:
    require(isinstance(value, str) and 0 < len(value) <= limit, f"{context}: invalid string")
    require(all(character.isprintable() for character in value), f"{context}: non-printable text")
    return value


def require_sha256(value: object, context: str) -> str:
    require(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value),
        f"{context}: invalid SHA-256",
    )
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


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BuildError(f"cannot load {path}: {error}") from error
    require(isinstance(value, dict), f"{path}: expected a JSON object")
    return value


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def validate_source_identity(value: object, context: str) -> dict:
    source = require_exact_keys(
        value, {"filename", "size_bytes", "sha256", "format"}, context
    )
    filename = require_bounded_string(source["filename"], f"{context}.filename", limit=128)
    require(Path(filename).name == filename and "\\" not in filename, f"{context}: unsafe filename")
    require_integer(source["size_bytes"], f"{context}.size_bytes", minimum=1)
    require_sha256(source["sha256"], f"{context}.sha256")
    require_bounded_string(source["format"], f"{context}.format", limit=128)
    return source


def validate_decoder_identity(value: object, context: str) -> dict:
    decoder = require_exact_keys(
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
        require_bounded_string(decoder[key], f"{context}.{key}")
    require(
        isinstance(decoder["commit"], str)
        and len(decoder["commit"]) == 40
        and all(character in "0123456789abcdef" for character in decoder["commit"]),
        f"{context}.commit: invalid Git commit",
    )
    require_sha256(decoder["extractor_sha256"], f"{context}.extractor_sha256")
    require_sha256(decoder["helper_sha256"], f"{context}.helper_sha256")
    return decoder


def validate_import_validation(
    validation: dict, *, expected_names: list[str], expected: dict
) -> None:
    require_exact_keys(
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
    require_bounded_string(validation["scope"], "source validation.scope")
    require_bounded_string(validation["authority"], "source validation.authority")
    for key in ("font_count", "bdf_exact_count", "bfd_exact_count"):
        require_integer(validation[key], f"source validation.{key}", minimum=0)
    require(validation["font_count"] == len(expected_names), "validation font count changed")
    require(validation["bdf_exact_count"] == expected["bdf_exact_count"], "validation BDF count changed")
    require(validation["bfd_exact_count"] == expected["bfd_exact_count"], "validation BFD count changed")

    evidence = require_exact_keys(
        validation["standard_character_name_evidence"],
        {"font", "reference_filename", "reference_sha256", "scope", "characters"},
        "standard character-name evidence",
    )
    for key in ("font", "reference_filename", "scope"):
        require_bounded_string(evidence[key], f"standard character-name evidence.{key}")
    require(
        Path(evidence["reference_filename"]).name == evidence["reference_filename"]
        and "\\" not in evidence["reference_filename"],
        "standard character-name evidence has an unsafe filename",
    )
    require_sha256(evidence["reference_sha256"], "standard character-name evidence.reference_sha256")
    characters = evidence["characters"]
    require(isinstance(characters, list), "standard character-name evidence lacks characters")
    character_codes: list[int] = []
    for index, character in enumerate(characters):
        context = f"standard character-name evidence.characters[{index}]"
        record = require_exact_keys(character, {"raw_code", "raw_octal", "name"}, context)
        code = require_integer(record["raw_code"], f"{context}.raw_code", minimum=0)
        require(record["raw_octal"] == f"{code:o}", f"{context}: stale octal code")
        require_bounded_string(record["name"], f"{context}.name", limit=128)
        character_codes.append(code)
    require(
        character_codes == sorted(character_codes)
        and len(character_codes) == len(set(character_codes)),
        "standard character-name evidence codes are not sorted and unique",
    )

    fonts = validation["fonts"]
    require(isinstance(fonts, list), "source validation lacks font records")
    validation_names: list[str] = []
    for index, item in enumerate(fonts):
        context = f"source validation.fonts[{index}]"
        record = require_exact_keys(item, {"font", "bdf", "bfd"}, context)
        validation_names.append(require_bounded_string(record["font"], f"{context}.font", limit=64))
        for kind in ("bdf", "bfd"):
            witness_context = f"{context}.{kind}"
            witness = require_exact_keys(
                record[kind], {"filename", "size_bytes", "sha256", "differences"}, witness_context
            )
            filename = require_bounded_string(witness["filename"], f"{witness_context}.filename", limit=255)
            require(Path(filename).name == filename and "\\" not in filename, f"{witness_context}: unsafe filename")
            require_integer(witness["size_bytes"], f"{witness_context}.size_bytes", minimum=1)
            require_sha256(witness["sha256"], f"{witness_context}.sha256")
            differences = witness["differences"]
            require(isinstance(differences, list), f"{witness_context}: differences must be a list")
            require(len(differences) <= 512, f"{witness_context}: too many differences")
            for difference in differences:
                require_bounded_string(difference, f"{witness_context}.differences", limit=1024)
    require(validation_names == expected_names, "validation font inventory differs")


def verify_source_tree() -> tuple[dict, dict, list[dict]]:
    manifest = load_json(SOURCE_MANIFEST)
    catalog = load_json(SOURCE_CATALOG)
    validation = load_json(SOURCE_VALIDATION)
    require_exact_keys(
        manifest,
        {
            "schema_version",
            "profile",
            "publication_boundary",
            "source",
            "decoder",
            "expected",
            "files",
        },
        "source manifest",
    )
    require_exact_keys(
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
    require_bounded_string(manifest["profile"], "source manifest.profile")
    require_bounded_string(manifest["publication_boundary"], "source manifest.publication_boundary")
    require_bounded_string(catalog["profile"], "source catalog.profile")
    require_bounded_string(catalog["scope_note"], "source catalog.scope_note")
    require(catalog["profile"] == manifest["profile"], "source profile identity differs")
    for key in (
        "font_count",
        "slot_count",
        "defined_glyph_count",
        "variable_width_font_count",
    ):
        require_integer(catalog[key], f"source catalog.{key}", minimum=0)
    require_sha256(catalog["normalized_geometry_sha256"], "source catalog.normalized_geometry_sha256")
    source = validate_source_identity(manifest["source"], "source manifest.source")
    decoder = validate_decoder_identity(manifest["decoder"], "source manifest.decoder")
    require(validate_source_identity(catalog["source"], "source catalog.source") == source, "catalog source identity differs")
    require(validate_decoder_identity(catalog["decoder"], "source catalog.decoder") == decoder, "catalog decoder identity differs")
    expected = require_exact_keys(
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
        require_integer(expected[key], f"source manifest.expected.{key}", minimum=0)
    require_sha256(expected["normalized_geometry_sha256"], "source manifest.expected.normalized_geometry_sha256")
    files = manifest["files"]
    require(isinstance(files, list), "source manifest lacks file closure")

    declared: dict[str, dict] = {}
    for index, item in enumerate(files):
        record = require_exact_keys(
            item, {"path", "size_bytes", "sha256"}, f"source manifest.files[{index}]"
        )
        relative = record["path"]
        require(isinstance(relative, str) and relative, "invalid source path")
        require(relative not in declared, f"duplicate source path: {relative}")
        path = Path(relative)
        require(
            not path.is_absolute()
            and "\\" not in relative
            and path.as_posix() == relative
            and "." not in path.parts
            and ".." not in path.parts,
            f"unsafe source path: {relative}",
        )
        require_integer(record["size_bytes"], f"source size for {relative}", minimum=1)
        require_sha256(record["sha256"], f"source digest for {relative}")
        declared[relative] = record
    observed: dict[str, Path] = {}
    for path in SOURCE_ROOT.rglob("*"):
        relative = path.relative_to(SOURCE_ROOT).as_posix()
        require(not path.is_symlink(), f"source tree contains a symlink: {relative}")
        if path.is_dir() or path == SOURCE_MANIFEST:
            continue
        require(path.is_file(), f"source tree contains a special file: {relative}")
        observed[relative] = path
    require(set(observed) == set(declared), "source file closure differs from manifest")
    for relative, path in observed.items():
        record = declared[relative]
        require(path.stat().st_size == record.get("size_bytes"), f"source size changed: {relative}")
        require(sha256(path) == record.get("sha256"), f"source SHA-256 changed: {relative}")

    font_records = []
    catalog_fonts = catalog["fonts"]
    require(isinstance(catalog_fonts, list), "source catalog lacks fonts")
    catalog_paths: list[str] = []
    for index, item in enumerate(catalog_fonts):
        entry = require_exact_keys(
            item,
            {"font", "path", "size_bytes", "sha256", "slot_count", "defined_glyph_count"},
            f"source catalog.fonts[{index}]",
        )
        name = require_bounded_string(entry["font"], f"source catalog.fonts[{index}].font", limit=64)
        path_value = entry["path"]
        require(
            isinstance(path_value, str)
            and path_value == f"fonts/{safe_filename(name)}.json",
            f"{name}: source catalog font path differs",
        )
        require(path_value in declared, f"{name}: source catalog path is not manifested")
        for key in ("size_bytes", "slot_count", "defined_glyph_count"):
            require_integer(entry[key], f"{name}: invalid catalog {key}", minimum=0)
        require_sha256(entry["sha256"], f"{name}: invalid catalog SHA-256")
        require(entry["size_bytes"] == declared[path_value]["size_bytes"], f"{name}: catalog size differs from manifest")
        require(entry["sha256"] == declared[path_value]["sha256"], f"{name}: catalog hash differs from manifest")
        record = load_json(SOURCE_ROOT / path_value)
        validate_source_font(record, SOURCE_ROOT / path_value)
        require(record["name"] == name, f"font identity differs: {path_value}")
        require(len(record["slots"]) == entry["slot_count"], f"{name}: catalog slot count differs")
        require(sum(slot["exists"] for slot in record["slots"]) == entry["defined_glyph_count"], f"{name}: catalog glyph count differs")
        font_records.append(record)
        catalog_paths.append(path_value)
    names = [record["name"] for record in font_records]
    require(names == sorted(names), "source fonts are not sorted by name")
    require(len(names) == len(set(names)), "duplicate source font name")
    require(
        set(declared) == {"catalog.json", "validation.json", *catalog_paths},
        "source manifest contains files outside the closed font corpus",
    )
    validate_import_validation(validation, expected_names=names, expected=expected)

    geometry = normalized_source_geometry(font_records)
    observed_geometry = canonical_sha256(geometry)
    count_values = {
        "font_count": len(font_records),
        "slot_count": sum(len(record["slots"]) for record in font_records),
        "defined_glyph_count": sum(
            slot["exists"] for record in font_records for slot in record["slots"]
        ),
        "variable_width_font_count": sum(
            bool(record["metrics"]["variable_width"]) for record in font_records
        ),
        "normalized_geometry_sha256": observed_geometry,
    }
    for key, value in count_values.items():
        require(expected.get(key) == value, f"source invariant changed: {key}")
        require(catalog.get(key) == value, f"source catalog invariant changed: {key}")
    return manifest, catalog, font_records


def validate_source_font(record: dict, path: Path) -> None:
    require_exact_keys(
        record,
        {"schema_version", "name", "source_format", "source_object", "metrics", "slots"},
        str(path),
    )
    require(record["schema_version"] == 1, f"{path}: unsupported schema")
    require_bounded_string(record["name"], f"{path}: name", limit=64)
    require(
        record["source_format"] == "little-endian VLM VLOD V2 resident FONT array",
        f"{path}: source format differs",
    )
    source_object = require_exact_keys(
        record["source_object"], {"header_vma", "fill_pointer", "special_high_codes"}, f"{path}: source_object"
    )
    header_vma = source_object["header_vma"]
    require(
        isinstance(header_vma, str)
        and len(header_vma) == 10
        and header_vma.startswith("0x")
        and all(character in "0123456789abcdef" for character in header_vma[2:]),
        f"{path}: invalid source object address",
    )
    fill_pointer = require_integer(source_object["fill_pointer"], f"{path}: fill pointer", minimum=1)
    special_high_codes = source_object["special_high_codes"]
    require(isinstance(special_high_codes, list), f"{path}: invalid special high codes")
    for code in special_high_codes:
        require_integer(code, f"{path}: invalid special high code", minimum=fill_pointer)
    require(
        special_high_codes == sorted(set(special_high_codes)),
        f"{path}: special high codes are not sorted and unique",
    )
    metrics = require_exact_keys(
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
    require(isinstance(slots, list) and slots, f"{path}: missing slots")
    for key in (
        "character_height", "default_character_width", "raster_height",
        "raster_width", "baseline", "blinker_width", "blinker_height",
    ):
        require_integer(metrics[key], f"{path}: invalid metric {key}", minimum=0)
        require(metrics[key] <= 4096, f"{path}: unreasonable metric {key}")
    require(type(metrics["variable_width"]) is bool, f"{path}: invalid variable-width flag")
    require(metrics["character_height"] > 0, f"{path}: non-positive character height")
    require(metrics["raster_height"] > 0, f"{path}: non-positive raster height")
    require(0 <= metrics["baseline"] <= metrics["character_height"], f"{path}: invalid baseline")
    codes = []
    for index, item in enumerate(slots):
        slot = require_exact_keys(
            item,
            {"code", "code_octal", "exists", "advance", "left_kern", "raster_width", "raster_height", "rows"},
            f"{path}: slots[{index}]",
        )
        code = require_integer(slot["code"], f"{path}: invalid slot code", minimum=0)
        require(code <= 0x10FFFF, f"{path}: slot code exceeds scalar-sized bound")
        require(slot["code_octal"] == f"{code:o}", f"{path}: stale octal code")
        width = require_integer(slot["raster_width"], f"{path}: invalid raster width", minimum=0)
        height = require_integer(slot["raster_height"], f"{path}: invalid raster height", minimum=0)
        require(width <= 4096 and height <= 4096, f"{path}: unreasonable raster dimensions")
        rows = slot["rows"]
        require(height == metrics["raster_height"], f"{path}: slot raster height changed")
        require(type(slot["exists"]) is bool, f"{path}: invalid existence flag")
        advance = require_integer(slot["advance"], f"{path}: invalid advance")
        left_kern = require_integer(slot["left_kern"], f"{path}: invalid left kern")
        require(abs(advance) <= 4096 and abs(left_kern) <= 4096, f"{path}: unreasonable glyph metric")
        require(isinstance(rows, list), f"{path}: invalid rows")
        require(len(rows) == (height if slot["exists"] else 0), f"{path}: row count differs")
        digits = max(1, (width + 3) // 4)
        for row in rows:
            require(
                isinstance(row, str)
                and len(row) == digits
                and all(character in "0123456789ABCDEF" for character in row),
                f"{path}: malformed raster row",
            )
            require(int(row, 16) < (1 << width) if width else int(row, 16) == 0, f"{path}: row exceeds raster width")
        codes.append(code)
    require(codes == sorted(codes) and len(codes) == len(set(codes)), f"{path}: slot codes are not unique and sorted")
    require(
        codes == [*range(fill_pointer), *special_high_codes],
        f"{path}: slots do not match the resident fill pointer and special-high codes",
    )


def normalized_source_geometry(records: list[dict]) -> list[dict]:
    return [
        {
            "name": record["name"],
            "metrics": record["metrics"],
            "slots": [
                {
                    key: slot[key]
                    for key in (
                        "code", "exists", "advance", "left_kern",
                        "raster_width", "raster_height", "rows",
                    )
                }
                for slot in record["slots"]
            ],
        }
        for record in records
    ]


def load_mapping(font_names: set[str], source_manifest: dict) -> dict:
    mapping = load_json(UNICODE_MAPPING)
    require_exact_keys(
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
        require_bounded_string(mapping[key], f"Unicode mapping.{key}", limit=128)
    policy = require_exact_keys(
        mapping["policy"],
        {
            "raw_profile_authority",
            "derivative_boundary",
            "unknown_character_policy",
            "private_use_interoperability",
        },
        "Unicode mapping.policy",
    )
    for key, value in policy.items():
        require_bounded_string(value, f"Unicode mapping.policy.{key}")
    assignments = require_exact_keys(mapping["assignments"], font_names, "Unicode mapping.assignments")
    private = require_exact_keys(
        mapping["bmp_private_use"],
        {"first", "last", "block_size", "prior_reservations", "blocks"},
        "Unicode mapping.bmp_private_use",
    )
    standard = require_exact_keys(
        mapping["standard_repertoire"],
        {"identity_ranges", "explicit", "evidence"},
        "Unicode mapping.standard_repertoire",
    )
    expected = require_exact_keys(
        mapping["expected"],
        {
            "font_count",
            "standard_font_count",
            "private_use_font_count",
            "source_normalized_geometry_sha256",
        },
        "Unicode mapping.expected",
    )
    for name, repertoire in assignments.items():
        require_bounded_string(name, "Unicode assignment font", limit=64)
        require_bounded_string(repertoire, f"Unicode assignment for {name}", limit=64)
    require(set(assignments) == font_names, "Unicode assignments are not closed over the source fonts")
    for key in ("font_count", "standard_font_count", "private_use_font_count"):
        require_integer(expected[key], f"Unicode mapping.expected.{key}", minimum=0)
    require_sha256(expected["source_normalized_geometry_sha256"], "Unicode mapping.expected.source_normalized_geometry_sha256")
    require(expected["font_count"] == len(font_names), "mapping font count changed")
    require(
        expected.get("source_normalized_geometry_sha256")
        == source_manifest["expected"]["normalized_geometry_sha256"],
        "mapping is pinned to a different source geometry",
    )
    counts = Counter(assignments.values())
    require(counts["standard"] == expected["standard_font_count"], "standard-font assignment count changed")
    require(len(font_names) - counts["standard"] == expected["private_use_font_count"], "PUA-font assignment count changed")

    identity_ranges = standard["identity_ranges"]
    explicit = standard["explicit"]
    evidence = standard["evidence"]
    require(isinstance(identity_ranges, list), "mapping identity ranges must be a list")
    require(isinstance(explicit, dict), "mapping explicit repertoire must be an object")
    require(isinstance(evidence, list) and evidence, "mapping standard evidence must be a non-empty list")
    for statement in evidence:
        require_bounded_string(statement, "Unicode mapping standard evidence")
    raw_identity_codes: set[int] = set()
    for index, item in enumerate(identity_ranges):
        record = require_exact_keys(item, {"first", "last", "unicode_delta"}, f"identity range {index}")
        range_first = require_integer(record["first"], f"identity range {index}.first", minimum=0)
        range_last = require_integer(record["last"], f"identity range {index}.last", minimum=range_first)
        delta = require_integer(record["unicode_delta"], f"identity range {index}.unicode_delta")
        require(range_last <= 0x10FFFF, f"identity range {index}: raw code too large")
        for code in range(range_first, range_last + 1):
            scalar = code + delta
            require(0 <= scalar <= 0x10FFFF and not 0xD800 <= scalar <= 0xDFFF, f"identity range {index}: invalid scalar")
            require(not 0xE000 <= scalar <= 0xF8FF, f"identity range {index}: standard mapping enters BMP PUA")
            require(code not in raw_identity_codes, f"identity ranges overlap at raw code {code}")
            raw_identity_codes.add(code)
    for raw, scalar_value in explicit.items():
        require(
            isinstance(raw, str) and raw.isdecimal() and raw == str(int(raw)),
            f"invalid explicit raw code: {raw!r}",
        )
        scalar = require_integer(scalar_value, f"explicit Unicode mapping for raw code {raw}", minimum=0)
        require(scalar <= 0x10FFFF and not 0xD800 <= scalar <= 0xDFFF, f"invalid explicit scalar for raw code {raw}")
        require(not 0xE000 <= scalar <= 0xF8FF, f"explicit standard mapping enters BMP PUA at raw code {raw}")
        require(int(raw) not in raw_identity_codes, f"explicit mapping overlaps identity range at raw code {raw}")

    first = require_integer(private["first"], "BMP PUA first", minimum=0)
    last = require_integer(private["last"], "BMP PUA last", minimum=first)
    block_size = require_integer(private["block_size"], "BMP PUA block size", minimum=1)
    blocks = private["blocks"]
    require((first, last, block_size) == (0xE000, 0xF8FF, 128), "unexpected BMP PUA contract")
    require(isinstance(blocks, dict) and blocks, "mapping lacks PUA blocks")
    reservations = private["prior_reservations"]
    require(isinstance(reservations, list), "mapping prior reservations must be a list")
    reservation_ranges: list[tuple[int, int, str]] = []
    for index, item in enumerate(reservations):
        record = require_exact_keys(item, {"project", "first", "last", "range"}, f"prior reservation {index}")
        project = require_bounded_string(record["project"], f"prior reservation {index}.project", limit=128)
        reservation_first = require_integer(record["first"], f"prior reservation {index}.first", minimum=first)
        reservation_last = require_integer(record["last"], f"prior reservation {index}.last", minimum=reservation_first)
        require(reservation_last <= last, f"prior reservation {index} is outside BMP PUA")
        require(record["range"] == f"U+{reservation_first:04X}-U+{reservation_last:04X}", f"prior reservation {index}: stale range label")
        reservation_ranges.append((reservation_first, reservation_last, project))
    block_ranges = []
    for name, item in blocks.items():
        require_bounded_string(name, "PUA block name", limit=64)
        record = require_exact_keys(item, {"first", "range", "label"}, f"PUA block {name}")
        start = require_integer(record["first"], f"PUA block {name}.first", minimum=first)
        require(start % block_size == 0, f"unaligned PUA block: {name}")
        end = start + block_size - 1
        require(first <= start <= end <= last, f"PUA block outside BMP PUA: {name}")
        require(record["range"] == f"U+{start:04X}-U+{end:04X}", f"PUA block {name}: stale range label")
        require_bounded_string(record["label"], f"PUA block {name}.label")
        block_ranges.append((start, end, name))
    for index, left_range in enumerate(block_ranges):
        for right_range in block_ranges[index + 1 :]:
            require(left_range[1] < right_range[0] or right_range[1] < left_range[0], f"overlapping PUA blocks: {left_range[2]} and {right_range[2]}")
    for start, end, name in block_ranges:
        for reserved_start, reserved_end, project in reservation_ranges:
            require(end < reserved_start or reserved_end < start, f"PUA block {name} overlaps prior reservation {project}")
    for repertoire in set(assignments.values()) - {"standard"}:
        require(repertoire in blocks, f"assignment references unknown PUA block: {repertoire}")
    require(
        set(blocks) == set(assignments.values()) - {"standard"},
        "PUA blocks are unused or unassigned",
    )
    return mapping


def load_font_identities(font_names: set[str], source_manifest: dict) -> dict:
    identities = load_json(FONT_IDENTITIES)
    require_exact_keys(
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
        require_bounded_string(identities[key], f"font identities.{key}", limit=128)

    source = require_exact_keys(
        identities["source"],
        {"profile", "vlod_sha256", "x_size_evidence"},
        "font identities.source",
    )
    require_bounded_string(source["profile"], "font identities.source.profile", limit=128)
    require_sha256(source["vlod_sha256"], "font identities.source.vlod_sha256")
    require(
        source["vlod_sha256"] == source_manifest["source"]["sha256"],
        "font identities are pinned to another Genera world",
    )
    x_size_evidence = require_exact_keys(
        source["x_size_evidence"],
        {"path", "sha256"},
        "font identities.source.x_size_evidence",
    )
    require_bounded_string(
        x_size_evidence["path"],
        "font identities.source.x_size_evidence.path",
        limit=256,
    )
    require_sha256(
        x_size_evidence["sha256"],
        "font identities.source.x_size_evidence.sha256",
    )

    policy = require_exact_keys(
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
        require_bounded_string(value, f"font identities.policy.{key}")

    named_sizes = identities["x_named_sizes"]
    require(isinstance(named_sizes, dict) and named_sizes, "font identities lack X named sizes")
    for name, x_size in named_sizes.items():
        require_bounded_string(name, "logical size", limit=64)
        require_integer(x_size, f"logical X size {name}", minimum=1)
    require(
        len(set(named_sizes.values())) == len(named_sizes),
        "logical sizes do not have unique nominal X sizes",
    )

    families = identities["families"]
    require(isinstance(families, dict) and families, "font identities lack families")
    desktop_family_names: set[str] = set()
    for name, value in families.items():
        require_bounded_string(name, "typographic family ID", limit=64)
        family = require_exact_keys(
            value, {"family_name", "kind"}, f"typographic family {name}"
        )
        require_bounded_string(
            family["family_name"], f"typographic family {name}.family_name", limit=128
        )
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
        require_bounded_string(name, "typographic face ID", limit=64)
        face = require_exact_keys(
            value,
            {"weight_name", "slant", "setwidth_name", "add_style_name"},
            f"typographic face {name}",
        )
        for key in ("weight_name", "slant", "setwidth_name"):
            require_bounded_string(face[key], f"typographic face {name}.{key}", limit=64)
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
        set(disambiguators) <= font_names,
        "desktop style disambiguator references an unknown font",
    )
    for name, value in disambiguators.items():
        require_bounded_string(
            value, f"desktop style disambiguator for {name}", limit=64
        )

    assignments = require_exact_keys(
        identities["assignments"], font_names, "font identities.assignments"
    )
    status_counts: Counter[str] = Counter()
    primary_count = 0
    used_families: set[str] = set()
    for name, value in assignments.items():
        assignment = require_exact_keys(
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
        family_name = assignment["typographic_family"]
        face_name = assignment["typographic_face"]
        require(family_name in families, f"font identity {name}: unknown typographic family")
        require(face_name in faces, f"font identity {name}: unknown typographic face")
        require(
            families[family_name]["kind"]
            == {"mapped": "text", "role-mapped": "specialty", "unmapped": "unmapped"}[status],
            f"font identity {name}: mapping status and family kind differ",
        )
        used_families.add(str(family_name))
        primary = assignment["primary"]
        if primary is not None:
            primary_count += 1
            selector = require_exact_keys(
                primary,
                {"family", "face", "size", "character_set"},
                f"font identity {name}.primary",
            )
            for key, item in selector.items():
                require_bounded_string(item, f"font identity {name}.primary.{key}", limit=64)
            require(selector["family"] in families, f"font identity {name}: unknown logical family")
            require(selector["face"] in faces, f"font identity {name}: unknown logical face")
            require(selector["size"] in named_sizes, f"font identity {name}: unknown logical size")
            require(
                selector["face"] == face_name,
                f"font identity {name}: primary and desktop face differ",
            )
            if status == "mapped":
                require(
                    selector["family"] == family_name,
                    f"font identity {name}: primary and desktop family differ",
                )
        require(
            status != "mapped" or primary is not None,
            f"font identity {name}: mapped font lacks a primary style",
        )
        require(
            status != "unmapped" or primary is None,
            f"font identity {name}: unmapped font has a primary style",
        )

    expected = require_exact_keys(
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
        require_integer(value, f"font identities.expected.{key}", minimum=0)
    require(expected["font_count"] == len(assignments), "font-identity count changed")
    require(
        expected["mapped_font_count"]
        == status_counts["mapped"] + status_counts["role-mapped"],
        "mapped font-identity count changed",
    )
    require(expected["primary_style_count"] == primary_count, "primary style count changed")
    require(
        expected["role_only_count"]
        == sum(
            assignment["mapping_status"] == "role-mapped"
            and assignment["primary"] is None
            for assignment in assignments.values()
        ),
        "role-only font count changed",
    )
    require(
        expected["unmapped_font_count"] == status_counts["unmapped"],
        "unmapped font-identity count changed",
    )
    require(
        expected["typographic_family_count"] == len(used_families) == len(families),
        "typographic family closure changed",
    )
    require(
        expected["desktop_style_disambiguator_count"] == len(disambiguators),
        "desktop style disambiguator count changed",
    )
    return identities


def resolved_font_identity(name: str, identities: dict) -> dict[str, object]:
    assignment = identities["assignments"][name]
    primary = assignment["primary"]
    logical_name = None
    nominal_x_size = None
    if primary is not None:
        logical_name = "/".join(
            (primary["family"], primary["face"], primary["size"])
        )
        nominal_x_size = identities["x_named_sizes"][primary["size"]]
    family = identities["families"][assignment["typographic_family"]]
    face = identities["faces"][assignment["typographic_face"]]
    disambiguator = identities["desktop_style_disambiguators"].get(name)
    add_style_name = " ".join(
        value for value in (disambiguator, face["add_style_name"]) if value
    )
    return {
        "mapping_status": assignment["mapping_status"],
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


def verify_desktop_identity_uniqueness(
    source_fonts: list[dict[str, object]], identities: dict
) -> int:
    """Reject resident strikes that desktop font clients cannot distinguish."""
    seen: dict[tuple[object, ...], str] = {}
    for record in source_fonts:
        name = str(record["name"])
        identity = resolved_font_identity(name, identities)
        typography = identity["typographic"]
        key = (
            typography["family_name"],
            typography["weight_name"],
            typography["slant"],
            typography["setwidth_name"],
            typography["add_style_name"],
            record["metrics"]["character_height"],
        )
        require(
            key not in seen,
            f"desktop font identity collision: {seen.get(key)} and {name}",
        )
        seen[key] = name
    return len(seen)


def standard_scalar(mapping: dict, code: int) -> int:
    standard = mapping["standard_repertoire"]
    explicit = standard["explicit"]
    if str(code) in explicit:
        return int(explicit[str(code)])
    for record in standard["identity_ranges"]:
        if int(record["first"]) <= code <= int(record["last"]):
            return code + int(record["unicode_delta"])
    raise BuildError(f"standard repertoire has no Unicode mapping for raw code {code:#o}")


def unicode_scalar(mapping: dict, font_name: str, code: int) -> tuple[int, str]:
    repertoire = mapping["assignments"][font_name]
    if repertoire == "standard":
        return standard_scalar(mapping, code), "standard"
    require(0 <= code < 128, f"{font_name}: PUA code exceeds one 128-scalar block: {code}")
    return int(mapping["bmp_private_use"]["blocks"][repertoire]["first"]) + code, "private-use"


def crop_font(record: dict) -> BitmapFont:
    metrics = record["metrics"]
    glyphs = []
    for slot in record["slots"]:
        if not slot["exists"]:
            continue
        width = slot["raster_width"]
        full_rows = [int(row, 16) for row in slot["rows"]]
        points = [
            (x, y)
            for y, row in enumerate(full_rows)
            for x in range(width)
            if row & (1 << (width - x - 1))
        ]
        if not points:
            glyphs.append(
                Glyph(
                    code=slot["code"],
                    bitmap_width=1,
                    advance=slot["advance"],
                    x_offset=-slot["left_kern"],
                    y_offset=0,
                    rows=(0,),
                )
            )
            continue
        left = min(x for x, _ in points)
        right = max(x for x, _ in points) + 1
        top = min(y for _, y in points)
        bottom = max(y for _, y in points) + 1
        cropped_rows = []
        for row in full_rows[top:bottom]:
            value = 0
            for x in range(left, right):
                value = (value << 1) | bool(row & (1 << (width - x - 1)))
            cropped_rows.append(int(value))
        glyphs.append(
            Glyph(
                code=slot["code"],
                bitmap_width=right - left,
                advance=slot["advance"],
                x_offset=-slot["left_kern"] + left,
                y_offset=metrics["baseline"] - bottom,
                rows=tuple(cropped_rows),
            )
        )
    return BitmapFont(
        name=record["name"],
        character_height=metrics["character_height"],
        raster_height=metrics["raster_height"],
        baseline=metrics["baseline"],
        glyphs=tuple(glyphs),
        source_format="little-endian VLM VLOD V2 resident FONT array",
        source_name="Genera-8-5.vlod",
        metadata={
            "profile": "Genera 8.5 base-world resident",
            "source_object": record["source_object"],
            "resident_metrics": metrics,
        },
    )


def unicode_font(raw_font: BitmapFont, mapping: dict) -> tuple[BitmapFont, list[dict]]:
    glyphs = []
    resolved = []
    used = set()
    for glyph in raw_font.glyphs:
        scalar, kind = unicode_scalar(mapping, raw_font.name, glyph.code)
        require(0 <= scalar <= 0x10FFFF and not 0xD800 <= scalar <= 0xDFFF, f"invalid Unicode scalar U+{scalar:04X}")
        require(scalar not in used, f"{raw_font.name}: Unicode mapping is not injective")
        used.add(scalar)
        glyphs.append(
            Glyph(
                code=scalar,
                bitmap_width=glyph.bitmap_width,
                advance=glyph.advance,
                x_offset=glyph.x_offset,
                y_offset=glyph.y_offset,
                rows=glyph.rows,
            )
        )
        resolved.append(
            {
                "raw_code": glyph.code,
                "raw_octal": f"{glyph.code:o}",
                "unicode": scalar,
                "unicode_hex": f"U+{scalar:04X}",
                "kind": kind,
            }
        )
    return (
        BitmapFont(
            name=raw_font.name,
            character_height=raw_font.character_height,
            raster_height=raw_font.raster_height,
            baseline=raw_font.baseline,
            glyphs=tuple(glyphs),
            source_format="Unicode derivative of a Genera 8.5 resident FONT array",
            source_name=raw_font.source_name,
            metadata=raw_font.metadata | {"mapping_id": mapping["mapping_id"]},
        ),
        resolved,
    )


def geometry(font: BitmapFont, *, include_codes: bool = True) -> dict:
    return {
        "character_height": font.character_height,
        "raster_height": font.raster_height,
        "baseline": font.baseline,
        "glyphs": [
            {
                **({"code": glyph.code} if include_codes else {}),
                "bitmap_width": glyph.bitmap_width,
                "advance": glyph.advance,
                "x_offset": glyph.x_offset,
                "y_offset": glyph.y_offset,
                "rows": list(glyph.rows),
            }
            for glyph in font.glyphs
        ],
    }


def visible_codes(font: BitmapFont) -> set[int]:
    return {glyph.code for glyph in font.glyphs if any(glyph.rows)}


def write_x_indexes(directory: Path, artifacts: list[dict], *, unicode: bool) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    entries = sorted(
        (Path(record["bdf_path"]).name, record["bdf_profile"]["xlfd_name"])
        for record in artifacts
    )
    (directory / "fonts.dir").write_text(
        "\n".join([str(len(entries)), *(f"{filename} {xlfd}" for filename, xlfd in entries)]) + "\n",
        encoding="ascii",
    )
    aliases: dict[str, str] = {}
    for record in artifacts:
        stem = safe_filename(record["name"])
        prefix = "genera-unicode" if unicode else "genera"
        for alias in (f"{prefix}-runtime-{stem}", f"{prefix}-{stem}"):
            require(alias not in aliases, f"duplicate alias: {alias}")
            aliases[alias] = record["bdf_profile"]["xlfd_name"]
    comment = (
        "! Genera 8.5 resident Unicode aliases"
        if unicode
        else "! Genera 8.5 resident raw-code aliases"
    )
    (directory / "fonts.alias").write_text(
        "\n".join([comment, *(f'"{alias}" "{target}"' for alias, target in sorted(aliases.items()))]) + "\n",
        encoding="ascii",
    )
    return {"font_count": len(entries), "alias_count": len(aliases)}


def build(output: Path, *, clean: bool) -> dict:
    for required_notice in ("NOTICE.md", "LICENSE"):
        require(
            (ROOT / required_notice).is_file(),
            f"required distribution notice is missing: {required_notice}",
        )
    source_manifest, source_catalog, source_fonts = verify_source_tree()
    mapping = load_mapping({record["name"] for record in source_fonts}, source_manifest)
    font_identities = load_font_identities(
        {record["name"] for record in source_fonts}, source_manifest
    )
    desktop_identity_count = verify_desktop_identity_uniqueness(
        source_fonts, font_identities
    )
    prepare_output_directory(output, clean=clean, owned_names=OWNED_OUTPUT_NAMES)
    raw_root = output / "raw"
    unicode_root = output / "unicode"
    for directory in (
        raw_root / "bdf", raw_root / "json", raw_root / "sheets",
        unicode_root / "bdf", unicode_root / "specimens" / "latin",
        unicode_root / "specimens" / "symbols",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    raw_artifacts = []
    unicode_artifacts = []
    resolution_counts = Counter()
    content_counts = Counter()
    raw_geometry_inventory = []
    unicode_geometry_inventory = []
    for record in source_fonts:
        name = record["name"]
        stem = safe_filename(name)
        logical_identity = resolved_font_identity(name, font_identities)
        typography = logical_identity["typographic"]
        raw_font = crop_font(record)
        require(len(raw_font.glyphs) == sum(slot["exists"] for slot in record["slots"]), f"{name}: exported glyph count changed")
        raw_profile = bdf_profile(
            raw_font,
            foundry="Misc",
            family_name=typography["family_name"],
            weight_name=typography["weight_name"],
            slant=typography["slant"],
            setwidth_name=typography["setwidth_name"],
            add_style_name=typography["add_style_name"],
            typographic_classification_policy=(
                "reviewed Genera logical-font mapping; raster metrics remain measured"
            ),
        )
        raw_bdf = raw_root / "bdf" / f"{stem}.bdf"
        raw_json = raw_root / "json" / f"{stem}.json"
        raw_sheet = raw_root / "sheets" / f"{stem}.png"
        write_bdf(raw_font, raw_bdf, "Misc", profile=raw_profile)
        shutil.copyfile(SOURCE_ROOT / "fonts" / f"{stem}.json", raw_json)
        write_sheet(raw_font, raw_sheet, columns=16, scale=2, label_radix=8)
        raw_geometry_sha = canonical_sha256(geometry(raw_font))
        raw_geometry_inventory.append({"name": name, "geometry": geometry(raw_font)})
        raw_record = {
            "name": name,
            "profile": "genera-8.5-resident-raw",
            "source_record": f"fonts/{stem}.json",
            "bdf_path": f"raw/bdf/{stem}.bdf",
            "json_path": f"raw/json/{stem}.json",
            "sheet_path": f"raw/sheets/{stem}.png",
            "glyph_count": len(raw_font.glyphs),
            "logical_identity": logical_identity,
            "geometry_sha256": raw_geometry_sha,
            "bdf_sha256": sha256(raw_bdf),
            "sheet_sha256": sha256(raw_sheet),
            "bdf_profile": raw_profile,
        }
        raw_artifacts.append(raw_record)

        derived, resolved = unicode_font(raw_font, mapping)
        require(geometry(raw_font, include_codes=False) == geometry(derived, include_codes=False), f"{name}: Unicode derivation changed geometry")
        unicode_profile = bdf_profile(
            derived,
            foundry="Misc",
            family_name=typography["family_name"],
            weight_name=typography["weight_name"],
            slant=typography["slant"],
            setwidth_name=typography["setwidth_name"],
            add_style_name=typography["add_style_name"],
            typographic_classification_policy=(
                "reviewed Genera logical-font mapping; raster metrics remain measured"
            ),
            charset_registry="ISO10646",
            charset_encoding="1",
        )
        unicode_bdf = unicode_root / "bdf" / f"{stem}.bdf"
        write_bdf(derived, unicode_bdf, "Misc", profile=unicode_profile)
        visible = visible_codes(derived)
        content_class = "latin" if visible & LATIN_LETTERS else "symbols"
        content_counts[content_class] += 1
        required_pangram = {ord(character) for character in PANGRAM}
        if content_class == "latin" and required_pangram <= {glyph.code for glyph in derived.glyphs}:
            specimen = unicode_root / "specimens" / "latin" / f"{stem}.png"
            specimen_metadata = write_text_specimen(
                derived, PANGRAM, specimen, max_advance=640, scale=2, padding=3
            )
            specimen_kind = "pangram"
        else:
            specimen = unicode_root / "specimens" / content_class / f"{stem}.png"
            write_sheet(derived, specimen, columns=16, scale=2, label_radix=16)
            specimen_metadata = None
            specimen_kind = "glyph-sheet"
        resolution_counts.update(item["kind"] for item in resolved)
        unicode_geometry_inventory.append({"name": name, "geometry": geometry(derived)})
        unicode_artifacts.append(
            {
                "name": name,
                "profile": "genera-8.5-resident-unicode",
                "repertoire": mapping["assignments"][name],
                "content_class": content_class,
                "logical_identity": logical_identity,
                "bdf_path": f"unicode/bdf/{stem}.bdf",
                "specimen_path": specimen.relative_to(output).as_posix(),
                "specimen_kind": specimen_kind,
                "specimen_metadata": specimen_metadata,
                "glyph_count": len(derived.glyphs),
                "standard_glyph_count": sum(item["kind"] == "standard" for item in resolved),
                "private_use_glyph_count": sum(item["kind"] == "private-use" for item in resolved),
                "geometry_sha256": canonical_sha256(geometry(derived)),
                "display_geometry_sha256": canonical_sha256(geometry(derived, include_codes=False)),
                "bdf_sha256": sha256(unicode_bdf),
                "specimen_sha256": sha256(specimen),
                "bdf_profile": unicode_profile,
                "resolved_mappings": resolved,
            }
        )

    raw_indexes = write_x_indexes(raw_root / "bdf", raw_artifacts, unicode=False)
    unicode_indexes = write_x_indexes(unicode_root / "bdf", unicode_artifacts, unicode=True)
    raw_catalog = {
        "schema_version": 1,
        "profile": "Genera 8.5 resident raw-code bitmap fonts",
        "source_manifest_sha256": sha256(SOURCE_MANIFEST),
        "font_identity_mapping_id": font_identities["mapping_id"],
        "font_identity_mapping_sha256": sha256(FONT_IDENTITIES),
        "font_count": len(raw_artifacts),
        "glyph_count": sum(record["glyph_count"] for record in raw_artifacts),
        "geometry_inventory_sha256": canonical_sha256(raw_geometry_inventory),
        "x_indexes": raw_indexes,
        "fonts": raw_artifacts,
    }
    unicode_catalog = {
        "schema_version": 1,
        "profile": "Genera 8.5 resident Unicode derivative bitmap fonts",
        "mapping_id": mapping["mapping_id"],
        "mapping_sha256": sha256(UNICODE_MAPPING),
        "font_identity_mapping_id": font_identities["mapping_id"],
        "font_identity_mapping_sha256": sha256(FONT_IDENTITIES),
        "font_count": len(unicode_artifacts),
        "glyph_count": sum(record["glyph_count"] for record in unicode_artifacts),
        "standard_glyph_count": resolution_counts["standard"],
        "private_use_glyph_count": resolution_counts["private-use"],
        "content_class_counts": dict(sorted(content_counts.items())),
        "geometry_inventory_sha256": canonical_sha256(unicode_geometry_inventory),
        "x_indexes": unicode_indexes,
        "fonts": unicode_artifacts,
    }
    write_json(raw_root / "catalog.json", raw_catalog)
    write_json(unicode_root / "catalog.json", unicode_catalog)
    shutil.copyfile(UNICODE_MAPPING, unicode_root / "UNICODE-MAPPING.json")
    shutil.copyfile(FONT_IDENTITIES, output / "FONT-IDENTITIES.json")
    shutil.copyfile(SOURCE_MANIFEST, output / "SOURCE-MANIFEST.json")
    for name in ("NOTICE.md", "LICENSE"):
        shutil.copyfile(ROOT / name, output / name)

    build_manifest = {
        "schema_version": 1,
        "profile": "Genera 8.5 base-world resident font distribution",
        "source": source_manifest["source"],
        "source_manifest_sha256": sha256(SOURCE_MANIFEST),
        "unicode_mapping": {
            "id": mapping["mapping_id"],
            "sha256": sha256(UNICODE_MAPPING),
        },
        "font_identities": {
            "id": font_identities["mapping_id"],
            "sha256": sha256(FONT_IDENTITIES),
        },
        "font_identity_counts": {
            key: font_identities["expected"][key]
            for key in (
                "mapped_font_count",
                "primary_style_count",
                "role_only_count",
                "unmapped_font_count",
                "typographic_family_count",
                "desktop_style_disambiguator_count",
            )
        },
        "desktop_identity_count": desktop_identity_count,
        "font_count": len(raw_artifacts),
        "source_slot_count": source_catalog["slot_count"],
        "raw_glyph_count": raw_catalog["glyph_count"],
        "unicode_glyph_count": unicode_catalog["glyph_count"],
        "standard_unicode_glyph_count": unicode_catalog["standard_glyph_count"],
        "private_use_unicode_glyph_count": unicode_catalog["private_use_glyph_count"],
        "content_class_counts": unicode_catalog["content_class_counts"],
        "raw_alias_count": raw_indexes["alias_count"],
        "unicode_alias_count": unicode_indexes["alias_count"],
        "specimen_count": len(unicode_artifacts),
        "raw_geometry_inventory_sha256": raw_catalog["geometry_inventory_sha256"],
        "unicode_geometry_inventory_sha256": unicode_catalog["geometry_inventory_sha256"],
    }
    corpus_profile = load_json(CORPUS_PROFILE)
    require_exact_keys(
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
    require(corpus_profile["profile"] == build_manifest["profile"], "corpus profile identity changed")
    require_sha256(corpus_profile["source_manifest_sha256"], "corpus profile.source_manifest_sha256")
    require_sha256(corpus_profile["unicode_mapping_sha256"], "corpus profile.unicode_mapping_sha256")
    require_sha256(corpus_profile["font_identities_sha256"], "corpus profile.font_identities_sha256")
    require(
        corpus_profile["source_manifest_sha256"] == sha256(SOURCE_MANIFEST),
        "corpus profile is pinned to a different source manifest",
    )
    require(
        corpus_profile["unicode_mapping_sha256"] == sha256(UNICODE_MAPPING),
        "corpus profile is pinned to a different Unicode mapping",
    )
    require(
        corpus_profile["font_identities_sha256"] == sha256(FONT_IDENTITIES),
        "corpus profile is pinned to different logical font identities",
    )
    require(
        corpus_profile["source_normalized_geometry_sha256"]
        == source_manifest["expected"]["normalized_geometry_sha256"],
        "corpus profile is pinned to a different normalized source geometry",
    )
    reviewed = require_exact_keys(
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
        require(build_manifest.get(key) == value, f"reviewed corpus invariant changed: {key}")
    build_manifest["corpus_profile_sha256"] = sha256(CORPUS_PROFILE)
    write_json(output / "BUILD-MANIFEST.json", build_manifest)
    checksum_paths = sorted(
        path for path in output.rglob("*") if path.is_file() and path.name != "SHA256SUMS"
    )
    (output / "SHA256SUMS").write_text(
        "".join(f"{sha256(path)}  {path.relative_to(output).as_posix()}\n" for path in checksum_paths),
        encoding="ascii",
    )
    return build_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()
    try:
        manifest = build(args.output.resolve(), clean=args.clean)
    except (BuildError, OSError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
