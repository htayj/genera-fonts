#!/usr/bin/env python3
"""Import a closed, font-only corpus from the pinned Genera 8.5 base world.

This is the licensed-media boundary of the project.  It loads the reviewed,
read-only decoder from a sibling ``genera-emu`` checkout, verifies both decoder
files and the VLOD before importing them, and writes only normalized resident
FONT records plus evidence-only catalogs.  It never copies the VLOD, Genera
source, BDF/BFD witnesses, documentation, or executable code into this repo.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GENERA_EMU = ROOT.parent / "genera-emu"
DEFAULT_OUTPUT = ROOT / "sources" / "genera-8.5"

VLOD_FILENAME = "Genera-8-5.vlod"
VLOD_SIZE = 54_804_480
VLOD_SHA256 = "a8ee5e86cc7e322f7385af3e0cd579d7650d4dcfc3ce328acbf8b25515dd0672"
DECODER_COMMIT = "2602eab2ef1bea4800312f71f9185e9261c6fa6c"
DECODER_SHA256 = "f242612ca77d94099eb2c1aab0837cc4673c51fdd04f926811229c5f1503c334"
HELPER_SHA256 = "8a2218279a1910db86a42485d3af3baf2ba836088197c9b2427b8476ceef7ecf"
CPTFONT_REFERENCE_BDF_SHA256 = "b463ccf4dbf1017aa1e4d484fa8bd9dc9decbfd02bde348aa9eb02e4c6eb35fd"

EXPECTED_FONT_NAMES = (
    "40VR", "5X5", "BIGFNT", "BIGFNTB", "BIGFNTBI", "BIGFNTI",
    "BOXFONT", "CPTFONT", "CPTFONTB", "CPTFONTBI", "CPTFONTC",
    "CPTFONTCB", "CPTFONTCC", "CPTFONTI", "DUTCH14", "DUTCH14B",
    "DUTCH14BI", "DUTCH14I", "DUTCH20", "DUTCH20B", "DUTCH20BI",
    "DUTCH20I", "EDSYMBOL12", "EINY7", "EINY8", "EUREX21I",
    "EUREX24I", "HIPPO10", "HIPPO12", "HL10", "HL10B", "HL10BI",
    "HL10I", "HL12", "HL12B", "HL12BI", "HL12I", "HL14", "HL14B",
    "HL14BI", "HL14I", "HL8", "HL8B", "HL8BI", "HL8I", "JESS11",
    "JESS11B", "JESS11I", "JESS13", "JESS13B", "JESS13I", "JESS14",
    "JESS14B", "JESS14I", "MATH10", "MATH12", "MEDFNT", "MEDFNTB",
    "MEDFNTBI", "MEDFNTI", "MOUSE", "NARROW", "SWISS12-CCAPS",
    "SWISS12B-CCAPS", "SWISS20", "SWISS20B", "SWISS20BI", "SWISS20I",
    "SYMBOL10", "SYMBOL12", "TINY", "TR10", "TR10B", "TR10BI",
    "TR10I", "TR12", "TR12B", "TR12BI", "TR12I", "TR8", "TR8B",
    "TR8BI", "TR8I", "TVFONT", "TVFONTB", "TVFONTBI", "TVFONTCB",
    "TVFONTI", "VT100",
)
EXPECTED_DEFINED_GLYPHS = 10_836
EXPECTED_VARIABLE_WIDTH_FONTS = 71
OWNED_OUTPUT_NAMES = {
    "fonts", "catalog.json", "validation.json", "SOURCE-MANIFEST.json"
}


class ImportError(ValueError):
    """The requested import would violate the reviewed corpus boundary."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ImportError(message)


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


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


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


def prepare_output(path: Path, *, clean: bool) -> None:
    if path.exists() and not path.is_dir():
        raise ImportError(f"output path is not a directory: {path}")
    existing = list(path.iterdir()) if path.exists() else []
    unknown = sorted(item.name for item in existing if item.name not in OWNED_OUTPUT_NAMES)
    require(
        not unknown,
        "output directory contains unrecognized entries: " + ", ".join(unknown),
    )
    require(
        clean or not existing,
        "output already contains imported data; pass --clean to replace it",
    )
    if clean:
        for item in existing:
            if item.is_symlink() or item.is_file():
                item.unlink()
            else:
                shutil.rmtree(item)
    path.mkdir(parents=True, exist_ok=True)


def load_decoder(genera_emu: Path):
    scripts = genera_emu / "scripts"
    decoder_path = scripts / "extract-genera-fonts.py"
    helper_path = scripts / "lisp_machine_fonts.py"
    require(decoder_path.is_file(), f"missing decoder: {decoder_path}")
    require(helper_path.is_file(), f"missing decoder helper: {helper_path}")
    require(
        sha256(decoder_path) == DECODER_SHA256,
        "extract-genera-fonts.py differs from the reviewed decoder",
    )
    require(
        sha256(helper_path) == HELPER_SHA256,
        "lisp_machine_fonts.py differs from the reviewed decoder helper",
    )

    helper_spec = importlib.util.spec_from_file_location(
        "lisp_machine_fonts", helper_path
    )
    require(helper_spec is not None and helper_spec.loader is not None, "cannot load helper")
    helper = importlib.util.module_from_spec(helper_spec)
    sys.modules[helper_spec.name] = helper
    helper_spec.loader.exec_module(helper)

    decoder_spec = importlib.util.spec_from_file_location(
        "genera_vlod_font_decoder", decoder_path
    )
    require(
        decoder_spec is not None and decoder_spec.loader is not None,
        "cannot load Genera VLOD decoder",
    )
    decoder = importlib.util.module_from_spec(decoder_spec)
    sys.modules[decoder_spec.name] = decoder
    decoder_spec.loader.exec_module(decoder)
    return decoder, decoder_path, helper_path


def normalized_slot(font, code: int) -> dict[str, object]:
    glyph = font.glyph(code)
    width = int(glyph["raster_width"])
    digits = max(1, (width + 3) // 4)
    rows = []
    for row in glyph["rows"]:
        value = int(row.replace(".", "0").replace("#", "1"), 2) if row else 0
        rows.append(f"{value:0{digits}X}")
    return {
        "code": code,
        "code_octal": f"{code:o}",
        "exists": bool(glyph["exists"]),
        "advance": int(glyph["set_width"]),
        "left_kern": int(glyph["left_kern"]),
        "raster_width": width,
        "raster_height": int(glyph["raster_height"]),
        "rows": rows,
    }


def normalized_font(font) -> dict[str, object]:
    summary = font.summary()
    slots = [normalized_slot(font, code) for code in font.codes()]
    return {
        "schema_version": 1,
        "name": font.name,
        "source_format": "little-endian VLM VLOD V2 resident FONT array",
        "source_object": {
            "header_vma": summary["header_vma"],
            "fill_pointer": summary["fill_pointer"],
            "special_high_codes": summary["special_high_codes"],
        },
        "metrics": {
            "character_height": summary["char_height"],
            "default_character_width": summary["char_width"],
            "raster_height": summary["raster_height"],
            "raster_width": summary["raster_width"],
            "baseline": summary["baseline"],
            "blinker_width": summary["blinker_width"],
            "blinker_height": summary["blinker_height"],
            "variable_width": summary["variable_width"],
        },
        "slots": slots,
    }


def reference_files(directory: Path, suffix: str) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in directory.glob(f"*.{suffix}*"):
        stem = path.name.casefold().split(f".{suffix}", 1)[0]
        require(stem not in result, f"duplicate {suffix.upper()} reference stem: {stem}")
        result[stem] = path
    return result


def build_validation(decoder, fonts: list, bdf_dir: Path, bfd_dir: Path) -> dict:
    require(bdf_dir.is_dir(), f"missing BDF reference directory: {bdf_dir}")
    require(bfd_dir.is_dir(), f"missing BFD reference directory: {bfd_dir}")
    bdfs = reference_files(bdf_dir, "bdf")
    bfds = reference_files(bfd_dir, "bfd")
    records = []
    for font in fonts:
        key = font.name.casefold()
        bdf = bdfs.get(key)
        bfd = bfds.get(key)
        require(bdf is not None, f"no BDF reference for {font.name}")
        require(bfd is not None, f"no BFD reference for {font.name}")
        records.append(
            {
                "font": font.name,
                "bdf": {
                    "filename": bdf.name,
                    "size_bytes": bdf.stat().st_size,
                    "sha256": sha256(bdf),
                    "differences": decoder.validate_bdf(font, bdf),
                },
                "bfd": {
                    "filename": bfd.name,
                    "size_bytes": bfd.stat().st_size,
                    "sha256": sha256(bfd),
                    "differences": decoder.validate_binary_bfd(font, bfd),
                },
            }
        )
    cptfont_bdf = bdfs.get("cptfont")
    require(cptfont_bdf is not None, "missing CPTFONT character-name witness")
    require(
        sha256(cptfont_bdf) == CPTFONT_REFERENCE_BDF_SHA256,
        "CPTFONT character-name witness differs from the reviewed release file",
    )
    cptfont_names = decoder.parse_bdf(cptfont_bdf)
    character_name_evidence = [
        {
            "raw_code": code,
            "raw_octal": f"{code:o}",
            "name": cptfont_names[code]["name"],
        }
        for code in sorted(cptfont_names)
    ]
    return {
        "schema_version": 1,
        "scope": "evidence-only comparison with matching licensed Genera 8.5 font files",
        "authority": "the VLOD resident object remains authoritative for this profile",
        "font_count": len(records),
        "bdf_exact_count": sum(not record["bdf"]["differences"] for record in records),
        "bfd_exact_count": sum(not record["bfd"]["differences"] for record in records),
        "standard_character_name_evidence": {
            "font": "CPTFONT",
            "reference_filename": cptfont_bdf.name,
            "reference_sha256": CPTFONT_REFERENCE_BDF_SHA256,
            "scope": "evidence-only raw-code names; no glyph data is copied",
            "characters": character_name_evidence,
        },
        "fonts": records,
    }


def import_corpus(
    *, genera_emu: Path, vlod: Path, output: Path, clean: bool
) -> dict[str, object]:
    genera_emu = genera_emu.resolve()
    vlod = vlod.resolve()
    output = output.resolve()
    require(vlod.is_file(), f"missing VLOD: {vlod}")
    require(vlod.name == VLOD_FILENAME, f"unexpected VLOD filename: {vlod.name}")
    require(vlod.stat().st_size == VLOD_SIZE, "VLOD byte size differs from reviewed input")
    require(sha256(vlod) == VLOD_SHA256, "VLOD SHA-256 differs from reviewed input")
    decoder, decoder_path, helper_path = load_decoder(genera_emu)
    prepare_output(output, clean=clean)

    with decoder.World(vlod) as world:
        fonts = decoder.find_fonts(world)
        observed_names = tuple(font.name for font in fonts)
        require(observed_names == EXPECTED_FONT_NAMES, "resident font inventory changed")
        records = [normalized_font(font) for font in fonts]
        validation = build_validation(
            decoder,
            fonts,
            genera_emu / ".lm-home/opengenera/runtime/sys.sct/x11/fonts/bdf/genera",
            genera_emu / ".lm-home/opengenera/runtime/sys.sct/fonts/tv",
        )

    defined_glyph_count = sum(
        slot["exists"] for record in records for slot in record["slots"]
    )
    variable_width_count = sum(
        bool(record["metrics"]["variable_width"]) for record in records
    )
    slot_count = sum(len(record["slots"]) for record in records)
    require(defined_glyph_count == EXPECTED_DEFINED_GLYPHS, "defined glyph count changed")
    require(
        variable_width_count == EXPECTED_VARIABLE_WIDTH_FONTS,
        "variable-width font count changed",
    )

    file_records = []
    for record in records:
        relative = Path("fonts") / f"{safe_filename(str(record['name']))}.json"
        path = output / relative
        write_json(path, record)
        file_records.append(
            {
                "font": record["name"],
                "path": relative.as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
                "slot_count": len(record["slots"]),
                "defined_glyph_count": sum(slot["exists"] for slot in record["slots"]),
            }
        )

    geometry = [
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
    normalized_geometry_sha256 = canonical_sha256(geometry)
    catalog = {
        "schema_version": 1,
        "profile": "Genera 8.5 base-world resident screen fonts",
        "scope_note": (
            "This is the complete 89-object resident FONT inventory of the pinned "
            "base world, not every font file distributed with Genera."
        ),
        "source": {
            "filename": VLOD_FILENAME,
            "size_bytes": VLOD_SIZE,
            "sha256": VLOD_SHA256,
            "format": "little-endian VLM VLOD V2",
        },
        "decoder": {
            "repository": "https://github.com/htayj/lisp-machine-container-museum.git",
            "commit": DECODER_COMMIT,
            "extractor_path": "scripts/extract-genera-fonts.py",
            "extractor_sha256": sha256(decoder_path),
            "helper_path": "scripts/lisp_machine_fonts.py",
            "helper_sha256": sha256(helper_path),
            "execution_model": "read-only mmap and inert tagged-object traversal",
        },
        "font_count": len(records),
        "slot_count": slot_count,
        "defined_glyph_count": defined_glyph_count,
        "variable_width_font_count": variable_width_count,
        "normalized_geometry_sha256": normalized_geometry_sha256,
        "fonts": file_records,
    }
    write_json(output / "catalog.json", catalog)
    write_json(output / "validation.json", validation)

    manifest_files = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "SOURCE-MANIFEST.json":
            manifest_files.append(
                {
                    "path": path.relative_to(output).as_posix(),
                    "size_bytes": path.stat().st_size,
                    "sha256": sha256(path),
                }
            )
    source_manifest = {
        "schema_version": 1,
        "profile": catalog["profile"],
        "publication_boundary": (
            "resident font shapes, display metrics, and bounded factual "
            "validation/provenance metadata only; no VLOD, supplied font-file "
            "contents, Genera source, documentation, executable code, or "
            "unrelated world data"
        ),
        "source": catalog["source"],
        "decoder": catalog["decoder"],
        "expected": {
            "font_count": len(records),
            "slot_count": slot_count,
            "defined_glyph_count": defined_glyph_count,
            "variable_width_font_count": variable_width_count,
            "normalized_geometry_sha256": normalized_geometry_sha256,
            "bdf_exact_count": validation["bdf_exact_count"],
            "bfd_exact_count": validation["bfd_exact_count"],
        },
        "files": manifest_files,
    }
    write_json(output / "SOURCE-MANIFEST.json", source_manifest)
    return source_manifest


def compare_trees(expected: Path, actual: Path) -> list[str]:
    expected_files = {
        path.relative_to(expected): path
        for path in expected.rglob("*")
        if path.is_file()
    }
    actual_files = {
        path.relative_to(actual): path
        for path in actual.rglob("*")
        if path.is_file()
    }
    differences = []
    for relative in sorted(expected_files.keys() | actual_files.keys()):
        if relative not in expected_files:
            differences.append(f"unexpected tracked path: {relative}")
        elif relative not in actual_files:
            differences.append(f"missing regenerated path: {relative}")
        elif expected_files[relative].read_bytes() != actual_files[relative].read_bytes():
            differences.append(f"content differs: {relative}")
    return differences


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--genera-emu", type=Path, default=DEFAULT_GENERA_EMU)
    parser.add_argument("--vlod", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--clean", action="store_true")
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate in a temporary directory and compare with --output",
    )
    args = parser.parse_args()
    vlod = args.vlod or (
        args.genera_emu / ".lm-home/opengenera/runtime" / VLOD_FILENAME
    )
    try:
        if args.check:
            require(args.output.is_dir(), f"tracked corpus is missing: {args.output}")
            with tempfile.TemporaryDirectory(prefix="genera-font-import-") as directory:
                regenerated = Path(directory) / "genera-8.5"
                manifest = import_corpus(
                    genera_emu=args.genera_emu,
                    vlod=vlod,
                    output=regenerated,
                    clean=False,
                )
                differences = compare_trees(args.output, regenerated)
                require(not differences, "import is not reproducible:\n" + "\n".join(differences))
        else:
            manifest = import_corpus(
                genera_emu=args.genera_emu,
                vlod=vlod,
                output=args.output,
                clean=args.clean,
            )
    except (ImportError, OSError, ValueError) as error:
        parser.error(str(error))
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "font_count": manifest["expected"]["font_count"],
                "slot_count": manifest["expected"]["slot_count"],
                "defined_glyph_count": manifest["expected"]["defined_glyph_count"],
                "normalized_geometry_sha256": manifest["expected"]["normalized_geometry_sha256"],
                "check": args.check,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
