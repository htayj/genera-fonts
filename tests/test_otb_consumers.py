from __future__ import annotations

from io import BytesIO
from pathlib import Path
import sys
import unittest
import tempfile

from fontTools.fontBuilder import FontBuilder
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables.BitmapGlyphMetrics import BigGlyphMetrics
from fontTools.ttLib.tables.DefaultTable import DefaultTable
from fontTools.ttLib.tables.E_B_D_T_ import ebdt_bitmap_format_5
from fontTools.ttLib.tables.E_B_L_C_ import (
    SbitLineMetrics,
    Strike,
    eblc_index_sub_table_2,
    eblc_index_sub_table_5,
)
from fontTools.ttLib.tables._c_m_a_p import CmapSubtable


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_release
import check_otb


METRICS = {
    "height": 0, "width": 0,
    "horiBearingX": -2, "horiBearingY": 1, "horiAdvance": 4,
    "vertBearingX": 2, "vertBearingY": -3, "vertAdvance": 7,
}


def bitmap_fixture(*, sparse: bool = False, empty_first: bool = False) -> TTFont:
    font = TTFont(recalcTimestamp=False, recalcBBoxes=False)
    names = [".notdef", "blankA", "visible", "blankB"] if sparse else [
        ".notdef", "visible", "blankA", "blankB"
    ]
    builder = FontBuilder(font=font)
    builder.setupHead(unitsPerEm=2048, created=0x7C259DC1, modified=0x7C259DC1)
    builder.setupHorizontalHeader()
    builder.setupHorizontalMetrics({name: (1024, 0) for name in names})
    font.setGlyphOrder(names)
    builder.setupPost(keepGlyphNames=False)
    maxp = font["maxp"] = newTable("maxp")
    maxp.tableVersion = 0x00005000
    maxp.numGlyphs = len(names)
    for tag, data in (("glyf", b""), ("loca", bytes(2 * (len(names) + 1)))):
        table = DefaultTable(tag)
        table.data = data
        font[tag] = table
    cmap = font["cmap"] = newTable("cmap")
    cmap.tableVersion = 0
    mapping = CmapSubtable.newSubtable(4)
    mapping.platformID, mapping.platEncID, mapping.language = 3, 1, 0
    mapping.cmap = {0x41: "visible", 0x42: "blankA", 0x43: "blankB"}
    cmap.tables = [mapping]
    builder.setupOS2()
    eblc = font["EBLC"] = newTable("EBLC")
    ebdt = font["EBDT"] = newTable("EBDT")
    eblc.version = ebdt.version = 2.0
    strike = Strike()
    size = strike.bitmapSizeTable
    size.colorRef = 0
    size.ppemX = size.ppemY = 8
    size.bitDepth = size.flags = 1
    for direction in ("hori", "vert"):
        line = SbitLineMetrics()
        for field in (
            "ascender", "descender", "widthMax", "caretSlopeNumerator",
            "caretSlopeDenominator", "caretOffset", "minOriginSB",
            "minAdvanceSB", "maxBeforeBL", "minAfterBL", "pad1", "pad2",
        ):
            setattr(line, field, 0)
        setattr(size, direction, line)
    blank = (eblc_index_sub_table_5 if sparse else eblc_index_sub_table_2)(None, font)
    blank.indexFormat = 5 if sparse else 2
    blank.imageFormat = 5
    blank.imageSize = 0
    blank.names = ["blankA", "blankB"]
    blank.metrics = BigGlyphMetrics()
    for field, value in METRICS.items():
        setattr(blank.metrics, field, value)
    visible = eblc_index_sub_table_5(None, font)
    visible.indexFormat = visible.imageFormat = 5
    visible.imageSize = 1
    visible.names = [".notdef", "visible"]
    visible.metrics = BigGlyphMetrics()
    for field, value in {**METRICS, "height": 1, "width": 1}.items():
        setattr(visible.metrics, field, value)
    strike.indexSubTables = [blank, visible] if empty_first else [visible, blank]
    eblc.strikes = [strike]
    # All blank names deliberately share one decoded object, as fontTools does
    # when identical zero-length EBDT intervals occur in converter output.
    empty = ebdt_bitmap_format_5(b"", font)
    empty.imageData = b""
    bitmaps = {"blankA": empty, "blankB": empty}
    for name in visible.names:
        bitmap = ebdt_bitmap_format_5(b"\x80", font)
        bitmap.imageData = b"\x80"
        bitmaps[name] = bitmap
    ebdt.strikeData = [bitmaps]
    return font


def encoded(font: TTFont) -> bytes:
    stream = BytesIO()
    font.save(stream)
    return stream.getvalue()


class EmptyBitmapRunTests(unittest.TestCase):
    def test_zero_records_rejected_at_eof_and_inside_ebdt(self) -> None:
        for empty_first in (False, True):
            with self.subTest(empty_first=empty_first):
                with TTFont(BytesIO(encoded(bitmap_fixture(empty_first=empty_first)))) as font:
                    with self.assertRaisesRegex(check_otb.OtbCheckError, "empty or out-of-bounds"):
                        check_otb._check_bitmap_locations(font, Path("fixture.otb"))

    def test_metric_only_encoding_preserves_metrics_pixels_and_cmap(self) -> None:
        for sparse in (False, True):
            with self.subTest(sparse=sparse):
                with TTFont(BytesIO(encoded(bitmap_fixture(sparse=sparse))), recalcBBoxes=False) as font:
                    original_cmap = font.getBestCmap().copy()
                    original_order = font.getGlyphOrder()[:]
                    self.assertEqual(build_release._normalize_empty_bitmap_runs(font), 2)
                    first_encoding = encoded(font)
                with TTFont(BytesIO(first_encoding), recalcTimestamp=False, recalcBBoxes=False) as font:
                    check_otb._check_bitmap_locations(font, Path("fixture.otb"))
                    self.assertEqual(font.getBestCmap(), original_cmap)
                    self.assertEqual(font.getGlyphOrder(), original_order)
                    subtables = font["EBLC"].strikes[0].indexSubTables
                    visible, blank = subtables
                    self.assertEqual((visible.indexFormat, visible.imageFormat), (5, 5))
                    self.assertEqual((blank.indexFormat, blank.imageFormat), (1, 7))
                    self.assertEqual([end - start for start, end in blank.locations], [8, 8])
                    bitmaps = font["EBDT"].strikeData[0]
                    for code in (0x42, 0x43):
                        bitmap = bitmaps[font.getBestCmap()[code]]
                        self.assertEqual(bitmap.imageData, b"")
                        for field, expected in METRICS.items():
                            self.assertEqual(getattr(bitmap.metrics, field), expected)
                        self.assertEqual(check_otb._otb_ink(bitmap, blank), (4, set()))
                    visible_bitmap = bitmaps[font.getBestCmap()[0x41]]
                    self.assertEqual(visible_bitmap.imageData, b"\x80")
                    self.assertEqual(check_otb._otb_ink(visible_bitmap, visible), (4, {(-2, 0)}))
                    self.assertEqual(build_release._normalize_empty_bitmap_runs(font), 0)
                    self.assertEqual(encoded(font), first_encoding)

    def test_advances_zero_marks_and_pitch_survive_sfnt_round_trip(self) -> None:
        for visible_advance, blank_advance, expected_pitch in (
            (6, 4, 0), (4, 0, 1), (4, 4, 1), (8, 4, 0),
        ):
            with self.subTest(visible=visible_advance, blank=blank_advance):
                font = bitmap_fixture()
                visible, blank = font["EBLC"].strikes[0].indexSubTables
                visible.metrics.horiAdvance = visible_advance
                blank.metrics.horiAdvance = blank_advance
                # A non-divisor ppem exercises nearest-unit rounding as well.
                font["EBLC"].strikes[0].bitmapSizeTable.ppemX = 7
                build_release._normalize_empty_bitmap_runs(font)
                build_release._normalize_horizontal_metrics(font)
                first_encoding = encoded(font)
                with TTFont(BytesIO(first_encoding), recalcTimestamp=False, recalcBBoxes=False) as decoded:
                    check_otb._check_horizontal_metrics(decoded, Path("fixture.otb"))
                    cmap = decoded.getBestCmap()
                    expected_visible = {6: 1755, 4: 1170, 8: 2341}[visible_advance]
                    expected_blank = {4: 1170, 0: 0}[blank_advance]
                    self.assertEqual(decoded["hmtx"].metrics[cmap[0x41]], (expected_visible, 0))
                    self.assertEqual(decoded["hmtx"].metrics[cmap[0x42]], (expected_blank, 0))
                    self.assertEqual(decoded["post"].isFixedPitch, expected_pitch)
                    self.assertEqual(decoded["hhea"].advanceWidthMax, expected_visible)
                    self.assertEqual(decoded["head"].created, 0x7C259DC1)
                    self.assertEqual(decoded["head"].modified, 0x7C259DC1)
                    build_release._normalize_horizontal_metrics(decoded)
                    self.assertEqual(encoded(decoded), first_encoding)

    def test_checker_rejects_constant_advance_metadata(self) -> None:
        font = bitmap_fixture()
        font["EBLC"].strikes[0].indexSubTables[0].metrics.horiAdvance = 6
        build_release._normalize_empty_bitmap_runs(font)
        with TTFont(BytesIO(encoded(font))) as decoded:
            with self.assertRaisesRegex(check_otb.OtbCheckError, "native bitmap escapement"):
                check_otb._check_horizontal_metrics(decoded, Path("fixture.otb"))

    def test_checker_rejects_inconsistent_advance_summaries(self) -> None:
        for tag, field, value in (
            ("hhea", "advanceWidthMax", 1),
            ("OS/2", "xAvgCharWidth", 1),
            ("post", "isFixedPitch", 0),
        ):
            with self.subTest(tag=tag, field=field):
                font = bitmap_fixture()
                build_release._normalize_empty_bitmap_runs(font)
                build_release._normalize_horizontal_metrics(font)
                setattr(font[tag], field, value)
                with TTFont(BytesIO(encoded(font))) as decoded:
                    with self.assertRaises(check_otb.OtbCheckError):
                        check_otb._check_horizontal_metrics(decoded, Path("fixture.otb"))

    def test_normalization_preserves_empty_outline_tables_and_native_face(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.otb"
            original = bitmap_fixture()
            original_loca = original["loca"].data
            path.write_bytes(encoded(original))
            build_release._normalize_otb(path, 1)
            with TTFont(path) as normalized:
                self.assertEqual(normalized.reader.tables["glyf"].length, 0)
                self.assertEqual(normalized.reader["loca"], original_loca)
                self.assertEqual(normalized["EBLC"].strikes[0].bitmapSizeTable.ppemX, 8)
            try:
                import freetype
            except ImportError:
                return  # Structural preservation is mandatory; consumer gate is optional.
            face = freetype.Face(str(path))
            self.assertFalse(face.is_scalable)
            self.assertEqual(face.num_fixed_sizes, 1)
            face.select_size(0)
            face.load_glyph(2, freetype.FT_LOAD_DEFAULT)
            self.assertEqual(face.glyph.format, freetype.FT_GLYPH_FORMAT_BITMAP)

    def test_requested_epoch_overrides_converter_wall_clock(self) -> None:
        for epoch in (0, 1, 1700000000):
            with self.subTest(epoch=epoch), tempfile.TemporaryDirectory() as directory:
                outputs = []
                for clock in (3800000000, 3800000001):
                    font = bitmap_fixture()
                    font["head"].created = font["head"].modified = clock
                    path = Path(directory) / f"{clock}.otb"
                    path.write_bytes(encoded(font))
                    build_release._normalize_otb(path, epoch)
                    outputs.append(path.read_bytes())
                    with TTFont(path) as normalized:
                        self.assertEqual(normalized["head"].created, epoch + 0x7C259DC0)
                        self.assertEqual(normalized["head"].modified, epoch + 0x7C259DC0)
                self.assertEqual(outputs[0], outputs[1])

    def test_zero_size_with_positive_area_is_not_silently_repaired(self) -> None:
        font = bitmap_fixture()
        blank = font["EBLC"].strikes[0].indexSubTables[-1]
        blank.metrics.width = blank.metrics.height = 1
        with self.assertRaisesRegex(build_release.ReleaseError, "nonempty bitmap dimensions"):
            build_release._normalize_empty_bitmap_runs(font)


if __name__ == "__main__":
    unittest.main()
