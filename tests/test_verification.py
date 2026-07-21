from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_script(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


importer = load_script("genera_font_importer_tests", "import_genera_fonts.py")
builder = load_script("genera_font_builder_tests", "build.py")
checker = load_script("genera_font_dist_checker_tests", "check_dist.py")
repro = load_script("genera_font_repro_checker_tests", "check_reproducibility.py")
gallery = load_script(
    "genera_font_specimen_gallery_tests", "update_specimen_gallery.py"
)
release_builder = load_script(
    "genera_font_release_builder_tests", "build_release.py"
)
otb_checker = load_script("genera_font_otb_checker_tests", "check_otb.py")


def source_font_fixture() -> dict[str, object]:
    return {
        "schema_version": 1,
        "name": "FIXTURE",
        "source_format": "little-endian VLM VLOD V2 resident FONT array",
        "source_object": {
            "header_vma": "0x80000001",
            "fill_pointer": 2,
            "special_high_codes": [],
        },
        "metrics": {
            "character_height": 5,
            "default_character_width": 4,
            "raster_height": 4,
            "raster_width": 5,
            "baseline": 4,
            "blinker_width": 1,
            "blinker_height": 1,
            "variable_width": False,
        },
        "slots": [
            {
                "code": 0,
                "code_octal": "0",
                "exists": True,
                "advance": 5,
                "left_kern": 1,
                "raster_width": 5,
                "raster_height": 4,
                "rows": ["10", "08", "1F", "00"],
            },
            {
                "code": 1,
                "code_octal": "1",
                "exists": True,
                "advance": 3,
                "left_kern": 0,
                "raster_width": 3,
                "raster_height": 4,
                "rows": ["0", "0", "0", "0"],
            },
        ],
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ImporterBoundaryTests(unittest.TestCase):
    def test_reviewed_inventory_matches_the_committed_closed_corpus(self) -> None:
        catalog = json.loads(
            (ROOT / "sources" / "genera-8.5" / "catalog.json").read_text(
                encoding="utf-8"
            )
        )
        names = tuple(record["font"] for record in catalog["fonts"])
        self.assertEqual(names, importer.EXPECTED_FONT_NAMES)
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(catalog["defined_glyph_count"], importer.EXPECTED_DEFINED_GLYPHS)
        self.assertEqual(
            catalog["variable_width_font_count"],
            importer.EXPECTED_VARIABLE_WIDTH_FONTS,
        )

    def test_vlod_filename_and_size_are_checked_before_decoder_loading(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wrong_name = root / "world.vlod"
            wrong_name.write_bytes(b"x")
            with self.assertRaisesRegex(importer.ImportError, "unexpected VLOD filename"):
                importer.import_corpus(
                    genera_emu=root,
                    vlod=wrong_name,
                    output=root / "output-a",
                    clean=False,
                )
            reviewed_name = root / importer.VLOD_FILENAME
            reviewed_name.write_bytes(b"x")
            with self.assertRaisesRegex(importer.ImportError, "VLOD byte size"):
                importer.import_corpus(
                    genera_emu=root,
                    vlod=reviewed_name,
                    output=root / "output-b",
                    clean=False,
                )

    def test_decoder_hash_boundary_rejects_unreviewed_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            scripts = Path(directory) / "scripts"
            scripts.mkdir()
            (scripts / "extract-genera-fonts.py").write_text("pass\n", encoding="utf-8")
            (scripts / "lisp_machine_fonts.py").write_text("pass\n", encoding="utf-8")
            with self.assertRaisesRegex(importer.ImportError, "reviewed decoder"):
                importer.load_decoder(Path(directory))

    def test_clean_never_removes_unrecognized_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            output.mkdir()
            witness = output / "user-data"
            witness.write_text("keep\n", encoding="utf-8")
            with self.assertRaisesRegex(importer.ImportError, "unrecognized entries"):
                importer.prepare_output(output, clean=True)
            self.assertEqual(witness.read_text(encoding="utf-8"), "keep\n")

    def test_normalized_slot_preserves_width_metrics_and_rows(self) -> None:
        class Font:
            @staticmethod
            def glyph(code: int) -> dict[str, object]:
                self = code
                return {
                    "raster_width": 5,
                    "rows": ["#....", ".#...", "#####"],
                    "exists": True,
                    "set_width": 7,
                    "left_kern": -2,
                    "raster_height": 3,
                }

        self.assertEqual(
            importer.normalized_slot(Font(), 9),
            {
                "code": 9,
                "code_octal": "11",
                "exists": True,
                "advance": 7,
                "left_kern": -2,
                "raster_width": 5,
                "raster_height": 3,
                "rows": ["10", "08", "1F"],
            },
        )

    def test_tree_comparison_reports_added_missing_and_changed_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "expected"
            actual = root / "actual"
            expected.mkdir()
            actual.mkdir()
            (expected / "same").write_bytes(b"same")
            (actual / "same").write_bytes(b"same")
            (expected / "changed").write_bytes(b"before")
            (actual / "changed").write_bytes(b"after")
            (expected / "missing").write_bytes(b"missing")
            (actual / "added").write_bytes(b"added")
            differences = importer.compare_trees(expected, actual)
            self.assertEqual(
                differences,
                [
                    "unexpected tracked path: added",
                    "content differs: changed",
                    "missing regenerated path: missing",
                ],
            )


class BuildInputTests(unittest.TestCase):
    def test_duplicate_json_keys_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "duplicate.json"
            path.write_text('{"schema_version": 1, "schema_version": 1}\n')
            with self.assertRaisesRegex(builder.BuildError, "duplicate JSON key"):
                builder.load_json(path)

    def test_source_font_rejects_malformed_rows_and_slot_order(self) -> None:
        fixture = source_font_fixture()
        builder.validate_source_font(fixture, Path("fixture.json"))
        checker.validate_source_font(fixture, Path("fixture.json"))
        cases: list[tuple[str, dict[str, object], str]] = []

        overflow = copy.deepcopy(fixture)
        overflow["slots"][0]["rows"][0] = "20"
        cases.append(("overflow", overflow, "row exceeds raster width"))

        lowercase = copy.deepcopy(fixture)
        lowercase["slots"][0]["rows"][0] = "0a"
        cases.append(("lowercase", lowercase, "malformed raster row"))

        duplicate = copy.deepcopy(fixture)
        duplicate["slots"][1]["code"] = 0
        duplicate["slots"][1]["code_octal"] = "0"
        cases.append(("duplicate", duplicate, "slot codes are not unique"))

        missing_rows = copy.deepcopy(fixture)
        missing_rows["slots"][0]["rows"].pop()
        cases.append(("row-count", missing_rows, "row count differs"))

        extra_payload = copy.deepcopy(fixture)
        extra_payload["unrelated_payload"] = "must never be copied to dist/raw/json"
        cases.append(("open-schema", extra_payload, "schema keys differ"))

        for label, record, message in cases:
            with self.subTest(label=label):
                with self.assertRaisesRegex(builder.BuildError, message):
                    builder.validate_source_font(record, Path("fixture.json"))

        independent_extra = copy.deepcopy(fixture)
        independent_extra["unrelated_payload"] = "must never enter a public artifact"
        with self.assertRaisesRegex(checker.DistCheckError, "schema keys differ"):
            checker.validate_source_font(independent_extra, Path("fixture.json"))

    def _write_source_tree(self, root: Path) -> tuple[Path, Path]:
        font = source_font_fixture()
        font_path = root / "fonts" / "fixture.json"
        write_json(font_path, font)
        source = {
            "filename": "Genera-8-5.vlod",
            "size_bytes": 1,
            "sha256": "a" * 64,
            "format": "little-endian VLM VLOD V2",
        }
        decoder = {
            "repository": "https://example.invalid/reviewed-decoder.git",
            "commit": "b" * 40,
            "extractor_path": "scripts/extract-genera-fonts.py",
            "extractor_sha256": "c" * 64,
            "helper_path": "scripts/lisp_machine_fonts.py",
            "helper_sha256": "d" * 64,
            "execution_model": "read-only fixture traversal",
        }
        geometry = builder.normalized_source_geometry([font])
        counts = {
            "font_count": 1,
            "slot_count": 2,
            "defined_glyph_count": 2,
            "variable_width_font_count": 0,
            "normalized_geometry_sha256": builder.canonical_sha256(geometry),
        }
        expected = {**counts, "bdf_exact_count": 1, "bfd_exact_count": 1}
        catalog = {
            "schema_version": 1,
            "profile": "fixture resident fonts",
            "scope_note": "closed fixture font corpus",
            "source": source,
            "decoder": decoder,
            **counts,
            "fonts": [
                {
                    "font": "FIXTURE",
                    "path": "fonts/fixture.json",
                    "size_bytes": font_path.stat().st_size,
                    "sha256": sha256(font_path),
                    "slot_count": 2,
                    "defined_glyph_count": 2,
                }
            ],
        }
        catalog_path = root / "catalog.json"
        write_json(catalog_path, catalog)
        witness = {
            "filename": "fixture.bdf",
            "size_bytes": 1,
            "sha256": "e" * 64,
            "differences": [],
        }
        validation = {
            "schema_version": 1,
            "scope": "fixture comparison",
            "authority": "fixture source is authoritative",
            "font_count": 1,
            "bdf_exact_count": 1,
            "bfd_exact_count": 1,
            "standard_character_name_evidence": {
                "font": "FIXTURE",
                "reference_filename": "fixture.bdf",
                "reference_sha256": "e" * 64,
                "scope": "fixture names only",
                "characters": [{"raw_code": 0, "raw_octal": "0", "name": "Fixture"}],
            },
            "fonts": [{"font": "FIXTURE", "bdf": witness, "bfd": witness}],
        }
        validation_path = root / "validation.json"
        write_json(validation_path, validation)
        manifest = {
            "schema_version": 1,
            "profile": "fixture resident fonts",
            "publication_boundary": "font fixture only",
            "source": source,
            "decoder": decoder,
            "expected": expected,
            "files": [
                {
                    "path": "catalog.json",
                    "size_bytes": catalog_path.stat().st_size,
                    "sha256": sha256(catalog_path),
                },
                {
                    "path": "fonts/fixture.json",
                    "size_bytes": font_path.stat().st_size,
                    "sha256": sha256(font_path),
                },
                {
                    "path": "validation.json",
                    "size_bytes": validation_path.stat().st_size,
                    "sha256": sha256(validation_path),
                },
            ],
        }
        manifest_path = root / "SOURCE-MANIFEST.json"
        write_json(manifest_path, manifest)
        return manifest_path, catalog_path

    def test_source_tree_is_closed_over_manifest_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, catalog = self._write_source_tree(root)
            patches = (
                mock.patch.object(builder, "SOURCE_ROOT", root),
                mock.patch.object(builder, "SOURCE_MANIFEST", manifest),
                mock.patch.object(builder, "SOURCE_CATALOG", catalog),
                mock.patch.object(builder, "SOURCE_VALIDATION", root / "validation.json"),
            )
            with patches[0], patches[1], patches[2], patches[3]:
                loaded_manifest, loaded_catalog, fonts = builder.verify_source_tree()
                self.assertEqual(loaded_manifest["expected"]["font_count"], 1)
                self.assertEqual(loaded_catalog["font_count"], 1)
                self.assertEqual([font["name"] for font in fonts], ["FIXTURE"])
                (root / "unexpected").write_text("not declared\n", encoding="utf-8")
                with self.assertRaisesRegex(builder.BuildError, "source file closure"):
                    builder.verify_source_tree()

    def mapping_fixture(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "mapping_id": "fixture",
            "unicode_version": "17.0.0",
            "profile": "fixture Unicode derivative",
            "policy": {
                "raw_profile_authority": "fixture raw profile",
                "derivative_boundary": "addresses only",
                "unknown_character_policy": "do not guess",
                "private_use_interoperability": "fixture convention",
            },
            "assignments": {"A": "standard", "B": "symbols"},
            "bmp_private_use": {
                "first": 0xE000,
                "last": 0xF8FF,
                "block_size": 128,
                "prior_reservations": [],
                "blocks": {
                    "symbols": {
                        "first": 0xEE00,
                        "range": "U+EE00-U+EE7F",
                        "label": "fixture symbols",
                    }
                },
            },
            "standard_repertoire": {
                "identity_ranges": [],
                "explicit": {},
                "evidence": ["fixture evidence"],
            },
            "expected": {
                "font_count": 2,
                "standard_font_count": 1,
                "private_use_font_count": 1,
                "source_normalized_geometry_sha256": "f" * 64,
            },
        }

    def load_mapping(self, mapping: dict[str, object]) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mapping.json"
            write_json(path, mapping)
            with mock.patch.object(builder, "UNICODE_MAPPING", path):
                return builder.load_mapping(
                    {"A", "B"},
                    {"expected": {"normalized_geometry_sha256": "f" * 64}},
                )

    def test_mapping_rejects_overlap_unknown_assignments_and_open_inventory(self) -> None:
        self.assertEqual(self.load_mapping(self.mapping_fixture())["mapping_id"], "fixture")

        overlap = self.mapping_fixture()
        overlap["bmp_private_use"]["blocks"]["overlap"] = {
            "first": 0xEE00,
            "range": "U+EE00-U+EE7F",
            "label": "overlap",
        }
        with self.assertRaisesRegex(builder.BuildError, "overlapping PUA blocks"):
            self.load_mapping(overlap)

        unknown = self.mapping_fixture()
        unknown["assignments"]["B"] = "missing"
        with self.assertRaisesRegex(builder.BuildError, "unknown PUA block"):
            self.load_mapping(unknown)

        open_inventory = self.mapping_fixture()
        open_inventory["assignments"].pop("B")
        with self.assertRaisesRegex(builder.BuildError, "schema keys differ"):
            self.load_mapping(open_inventory)

    def test_font_identity_mapping_is_closed_and_resolves_logical_strikes(self) -> None:
        source_manifest = builder.load_json(
            ROOT / "sources" / "genera-8.5" / "SOURCE-MANIFEST.json"
        )
        source_catalog = builder.load_json(
            ROOT / "sources" / "genera-8.5" / "catalog.json"
        )
        font_names = {record["font"] for record in source_catalog["fonts"]}
        identities = builder.load_font_identities(font_names, source_manifest)

        self.assertEqual(
            identities["expected"],
            {
                "font_count": 89,
                "mapped_font_count": 86,
                "primary_style_count": 79,
                "role_only_count": 7,
                "unmapped_font_count": 3,
                "typographic_family_count": 15,
                "desktop_style_disambiguator_count": 2,
            },
        )
        expected = {
            "HL8": ("SWISS/ROMAN/VERY-SMALL", 8, "Medium", "R"),
            "HL8B": ("SWISS/BOLD/VERY-SMALL", 8, "Bold", "R"),
            "HL8I": ("SWISS/ITALIC/VERY-SMALL", 8, "Medium", "I"),
            "HL8BI": ("SWISS/BOLD-ITALIC/VERY-SMALL", 8, "Bold", "I"),
            "HL14": ("SWISS/ROMAN/LARGE", 14, "Medium", "R"),
        }
        for name, (logical_name, x_size, weight, slant) in expected.items():
            with self.subTest(name=name):
                identity = builder.resolved_font_identity(name, identities)
                self.assertEqual(identity["logical_name"], logical_name)
                self.assertEqual(identity["nominal_x_size"], x_size)
                self.assertEqual(
                    identity["typographic"]["family_name"],
                    "Symbolics Genera Swiss",
                )
                self.assertEqual(identity["typographic"]["weight_name"], weight)
                self.assertEqual(identity["typographic"]["slant"], slant)

        hl8_record = next(
            record for record in source_catalog["fonts"] if record["font"] == "HL8"
        )
        hl14_record = next(
            record for record in source_catalog["fonts"] if record["font"] == "HL14"
        )
        hl8 = builder.load_json(ROOT / "sources/genera-8.5" / hl8_record["path"])
        hl14 = builder.load_json(ROOT / "sources/genera-8.5" / hl14_record["path"])
        self.assertEqual(hl8["metrics"]["character_height"], 11)
        self.assertEqual(hl14["metrics"]["character_height"], 15)

        box = builder.resolved_font_identity("BOXFONT", identities)
        self.assertEqual(box["mapping_status"], "role-mapped")
        self.assertIsNone(box["logical_name"])
        mouse = builder.resolved_font_identity("MOUSE", identities)
        self.assertEqual(mouse["logical_name"], "FIX/ROMAN/NORMAL")
        self.assertEqual(mouse["primary"]["character_set"], "MOUSE")
        self.assertEqual(
            mouse["typographic"]["family_name"], "Symbolics Genera Mouse"
        )
        self.assertEqual(
            builder.resolved_font_identity("CPTFONTB", identities)["typographic"][
                "setwidth_name"
            ],
            "Expanded",
        )
        self.assertEqual(
            builder.resolved_font_identity("CPTFONTCC", identities)["typographic"],
            {
                "family_name": "Symbolics Genera Fix",
                "weight_name": "Medium",
                "slant": "R",
                "setwidth_name": "Condensed",
                "add_style_name": "Extra",
            },
        )
        self.assertEqual(
            builder.resolved_font_identity("TR8BI", identities)["typographic"][
                "add_style_name"
            ],
            "Very Small Strike",
        )
        self.assertEqual(
            builder.resolved_font_identity("TR10BI", identities)["typographic"][
                "add_style_name"
            ],
            "Small Strike",
        )
        self.assertEqual(
            otb_checker._expected_style(
                builder.resolved_font_identity("TR8BI", identities)["typographic"]
            ),
            "Very-Small-Strike Bold Italic",
        )
        unmapped = builder.resolved_font_identity("40VR", identities)
        self.assertEqual(unmapped["mapping_status"], "unmapped")
        self.assertIsNone(unmapped["primary"])

        source_fonts = [
            builder.load_json(ROOT / "sources/genera-8.5" / record["path"])
            for record in source_catalog["fonts"]
        ]
        self.assertEqual(
            builder.verify_desktop_identity_uniqueness(source_fonts, identities),
            89,
        )
        colliding = copy.deepcopy(identities)
        colliding["desktop_style_disambiguators"].clear()
        with self.assertRaisesRegex(
            builder.BuildError, "desktop font identity collision"
        ):
            builder.verify_desktop_identity_uniqueness(source_fonts, colliding)

        self.assertEqual(
            gallery.logical_identity_label(
                builder.resolved_font_identity("HL8", identities)
            ),
            "SWISS/ROMAN/VERY-SMALL",
        )
        self.assertEqual(
            gallery.logical_identity_label(mouse),
            "FIX/ROMAN/NORMAL [MOUSE] · Symbolics Genera Mouse (role-mapped)",
        )
        self.assertEqual(
            gallery.logical_identity_label(box),
            "Symbolics Genera Box Font (role-mapped)",
        )
        self.assertEqual(
            gallery.logical_identity_label(unmapped),
            "unmapped resident strike",
        )

    def test_font_identity_mapping_rejects_an_open_or_inconsistent_inventory(self) -> None:
        source_manifest = builder.load_json(
            ROOT / "sources" / "genera-8.5" / "SOURCE-MANIFEST.json"
        )
        source_catalog = builder.load_json(
            ROOT / "sources" / "genera-8.5" / "catalog.json"
        )
        font_names = {record["font"] for record in source_catalog["fonts"]}
        mapping = builder.load_json(ROOT / "config/font-identities.json")

        cases: list[tuple[str, dict[str, object], str]] = []
        missing = copy.deepcopy(mapping)
        missing["assignments"].pop("HL8")
        cases.append(("missing", missing, "schema keys differ"))
        no_primary = copy.deepcopy(mapping)
        no_primary["assignments"]["HL8"]["primary"] = None
        cases.append(("no-primary", no_primary, "mapped font lacks a primary style"))
        wrong_family = copy.deepcopy(mapping)
        wrong_family["assignments"]["HL8"]["primary"]["family"] = "DUTCH"
        cases.append(
            ("wrong-family", wrong_family, "primary and desktop family differ")
        )
        wrong_boundary = copy.deepcopy(mapping)
        wrong_boundary["assignments"]["HL8"]["typographic_family"] = "BOX"
        cases.append(
            ("wrong-boundary", wrong_boundary, "mapping status and family kind differ")
        )

        for label, candidate, message in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "font-identities.json"
                write_json(path, candidate)
                with mock.patch.object(builder, "FONT_IDENTITIES", path):
                    with self.assertRaisesRegex(builder.BuildError, message):
                        builder.load_font_identities(font_names, source_manifest)

    def test_crop_and_unicode_derivative_preserve_exact_display_geometry(self) -> None:
        raw = builder.crop_font(source_font_fixture())
        self.assertEqual(
            builder.geometry(raw)["glyphs"],
            [
                {
                    "code": 0,
                    "bitmap_width": 5,
                    "advance": 5,
                    "x_offset": -1,
                    "y_offset": 1,
                    "rows": [16, 8, 31],
                },
                {
                    "code": 1,
                    "bitmap_width": 1,
                    "advance": 3,
                    "x_offset": 0,
                    "y_offset": 0,
                    "rows": [0],
                },
            ],
        )
        mapping = {
            "mapping_id": "fixture",
            "assignments": {"FIXTURE": "symbols"},
            "bmp_private_use": {"blocks": {"symbols": {"first": 0xEE00}}},
        }
        derived, resolved = builder.unicode_font(raw, mapping)
        self.assertEqual(
            builder.geometry(raw, include_codes=False),
            builder.geometry(derived, include_codes=False),
        )
        self.assertEqual([glyph.code for glyph in derived.glyphs], [0xEE00, 0xEE01])
        self.assertEqual([record["kind"] for record in resolved], ["private-use"] * 2)

    def test_pua_font_rejects_codes_outside_its_single_block(self) -> None:
        glyph = builder.Glyph(
            code=128,
            bitmap_width=1,
            advance=1,
            x_offset=0,
            y_offset=0,
            rows=(1,),
        )
        font = builder.BitmapFont(
            name="FIXTURE",
            character_height=1,
            raster_height=1,
            baseline=1,
            glyphs=(glyph,),
            source_format="fixture",
            source_name="fixture",
        )
        mapping = {
            "mapping_id": "fixture",
            "assignments": {"FIXTURE": "symbols"},
            "bmp_private_use": {"blocks": {"symbols": {"first": 0xEE00}}},
        }
        with self.assertRaisesRegex(builder.BuildError, "exceeds one 128-scalar block"):
            builder.unicode_font(font, mapping)

    def test_alias_generation_rejects_colliding_safe_names(self) -> None:
        artifacts = [
            {
                "name": "A B",
                "bdf_path": "raw/bdf/a-b-one.bdf",
                "bdf_profile": {"xlfd_name": "-fixture-one"},
            },
            {
                "name": "A-B",
                "bdf_path": "raw/bdf/a-b-two.bdf",
                "bdf_profile": {"xlfd_name": "-fixture-two"},
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(builder.BuildError, "duplicate alias"):
                builder.write_x_indexes(Path(directory), artifacts, unicode=False)


def fixture_bdf(row: str = "F8", *, encoding: int = 65) -> str:
    return f"""STARTFONT 2.1
FONT -Misc-Fixture-Unknown-OT-Unknown--5-50-72-72-C-50-Misc-FontSpecific
SIZE 5 72 72
FONTBOUNDINGBOX 5 1 0 0
STARTPROPERTIES 0
ENDPROPERTIES
CHARS 1
STARTCHAR C{encoding:04X}
ENCODING {encoding}
SWIDTH 1000 0
DWIDTH 5 0
BBX 5 1 0 0
BITMAP
{row}
ENDCHAR
ENDFONT
"""


class VerificationPrimitiveTests(unittest.TestCase):
    def test_release_builder_rejects_an_unreviewed_fonttosfnt_version(self) -> None:
        process = mock.Mock(stdout="fonttosfnt 1.0.4\n", stderr="")
        with mock.patch.object(
            release_builder.shutil, "which", return_value="/tmp/fonttosfnt"
        ), mock.patch.object(release_builder.subprocess, "run", return_value=process):
            with self.assertRaisesRegex(
                release_builder.ReleaseError, "expected 'fonttosfnt 1.2.5'"
            ):
                release_builder._fonttosfnt_version("fonttosfnt")

    def test_bdf_parser_rejects_nonzero_storage_padding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            valid = Path(directory) / "valid.bdf"
            valid.write_text(fixture_bdf(), encoding="ascii")
            parsed = checker.parse_bdf(valid)
            self.assertEqual(parsed["glyphs"][65]["rows"], (31,))
            invalid = Path(directory) / "invalid.bdf"
            invalid.write_text(fixture_bdf("FF"), encoding="ascii")
            with self.assertRaisesRegex(checker.DistCheckError, "nonzero bitmap padding"):
                checker.parse_bdf(invalid)

    def test_checksum_paths_cannot_escape_distribution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            checksum = root / "SHA256SUMS"
            checksum.write_text(f"{'0' * 64}  ../escape\n", encoding="ascii")
            with self.assertRaisesRegex(checker.DistCheckError, "unsafe checksum path"):
                checker.check_checksums(root, {"SHA256SUMS": checksum})

    def test_alias_parser_preserves_quoted_xlfd_spaces(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fonts.alias"
            path.write_text('"fixture" "-Misc-Family With Spaces-R-Normal"\n', encoding="ascii")
            self.assertEqual(
                checker.parse_aliases(path),
                {"fixture": "-Misc-Family With Spaces-R-Normal"},
            )

    def test_reproducibility_comparison_reports_set_and_content_drift(self) -> None:
        with self.assertRaisesRegex(repro.ReproducibilityError, "file sets differ"):
            repro.compare_snapshots({"a": b"a"}, {"b": b"b"})
        with self.assertRaisesRegex(repro.ReproducibilityError, "non-reproducible files: a"):
            repro.compare_snapshots({"a": b"a"}, {"a": b"changed"})
        repro.compare_snapshots({"a": b"same"}, {"a": b"same"})


if __name__ == "__main__":
    unittest.main()
