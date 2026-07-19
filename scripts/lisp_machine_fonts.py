#!/usr/bin/env python3
"""Shared, dependency-free output helpers for Lisp Machine bitmap fonts.

The format-specific extractors normalize their input to ``BitmapFont`` and
``Glyph`` objects.  This module writes transparent archival data (JSON and
BDF) plus deterministic PNG specimen sheets without requiring Pillow.

Initially ported through the BSD-licensed ``CADR-fonts`` exporter, whose
lineage begins at ``genera-emu`` commit
``2602eab2ef1bea4800312f71f9185e9261c6fa6c``.  The BDF writer in this
repository additionally emits complete X Logical Font Description (XLFD)
names and properties.  The source formats carry pixel metrics, but not
typographic family/weight/slant classifications or a physical design
resolution, so those packaging choices are deliberately conservative and
explicit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import shutil
import struct
from typing import Iterable
import zlib


@dataclass(frozen=True)
class Glyph:
    """One bitmap glyph, with rows stored most-significant pixel first."""

    code: int
    bitmap_width: int
    advance: int
    x_offset: int
    y_offset: int
    rows: tuple[int, ...]


@dataclass(frozen=True)
class BitmapFont:
    """A bitmap font in the common representation used by the exporters."""

    name: str
    character_height: int
    raster_height: int
    baseline: int
    glyphs: tuple[Glyph, ...]
    source_format: str
    source_name: str
    metadata: dict[str, object] = field(default_factory=dict)


def safe_filename(name: str) -> str:
    """Return a stable lowercase filename component."""

    result = []
    previous_hyphen = False
    for character in name.lower():
        if character.isascii() and character.isalnum():
            result.append(character)
            previous_hyphen = False
        elif not previous_hyphen:
            result.append("-")
            previous_hyphen = True
    return "".join(result).strip("-") or "unnamed-font"


def prepare_output_directory(
    path: Path, *, clean: bool, owned_names: Iterable[str]
) -> None:
    """Create an empty output directory without deleting unrecognized data.

    Existing extractor-owned top-level paths require explicit ``clean=True``.
    Unknown entries are always rejected, including in clean mode, so a typo in
    an output path cannot turn this helper into a general directory remover.
    """

    owned = set(owned_names)
    if path.exists() and not path.is_dir():
        raise ValueError(f"output path is not a directory: {path}")
    existing = list(path.iterdir()) if path.exists() else []
    unknown = sorted(child.name for child in existing if child.name not in owned)
    if unknown:
        raise ValueError(
            "output directory contains unrecognized entries: "
            + ", ".join(unknown)
        )
    if existing and not clean:
        raise ValueError(
            "output directory already contains generated files; pass --clean "
            "to replace them"
        )
    if clean:
        for child in existing:
            if child.is_symlink() or child.is_file():
                child.unlink()
            else:
                shutil.rmtree(child)
    path.mkdir(parents=True, exist_ok=True)


def _row_hex(row: int, width: int) -> str:
    digits = max(1, (width + 3) // 4)
    return f"{row:0{digits}X}"


def write_json(font: BitmapFont, path: Path) -> None:
    """Write a lossless, human-inspectable normalized font record."""

    path.parent.mkdir(parents=True, exist_ok=True)
    record = asdict(font)
    record["glyphs"] = [
        {
            "code": glyph.code,
            "code_octal": f"{glyph.code:o}",
            "bitmap_width": glyph.bitmap_width,
            "advance": glyph.advance,
            "x_offset": glyph.x_offset,
            "y_offset": glyph.y_offset,
            "rows": [_row_hex(row, glyph.bitmap_width) for row in glyph.rows],
        }
        for glyph in font.glyphs
    ]
    path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _round_ratio(numerator: int, denominator: int) -> int:
    """Round a non-negative rational to nearest, with exact halves upward."""

    if numerator < 0 or denominator <= 0:
        raise ValueError("_round_ratio requires a non-negative rational")
    return (2 * numerator + denominator) // (2 * denominator)


def _xlfd_field(value: str) -> str:
    """Return an XLFD field without delimiter or wildcard characters."""

    result = "".join(
        " " if character in '-?*,\"\\' else character for character in value
    )
    return " ".join(result.split())


def _nonrendering_placeholder(glyph: Glyph) -> bool:
    """True for a source slot that X's bitmap renderer cannot materialize.

    Alto font descriptors contain slots with zero advance, zero raster width,
    and no set pixels.  They are retained losslessly in JSON, but a PCF made
    from a zero-width BDF glyph causes the Xorg bitmap renderer to fail the
    entire font with BadAlloc.  Omitting such a no-op slot from BDF preserves
    both ink and escapement while keeping the installable profile usable.
    """

    return (
        glyph.advance == 0
        and glyph.bitmap_width == 0
        and not any(glyph.rows)
    )


def _bdf_glyphs(font: BitmapFont) -> tuple[Glyph, ...]:
    return tuple(glyph for glyph in font.glyphs if not _nonrendering_placeholder(glyph))


def _spacing_code(font: BitmapFont, glyphs: tuple[Glyph, ...]) -> tuple[str, str]:
    """Classify escapement using the XLFD P/M/C definitions."""

    advances = {glyph.advance for glyph in glyphs}
    if len(advances) != 1:
        return "P", "logical character advances vary"

    advance = next(iter(advances))
    descent = max(0, font.character_height - font.baseline)
    charcell = all(
        0 <= glyph.x_offset
        and glyph.x_offset + glyph.bitmap_width <= advance
        and glyph.y_offset >= -descent
        and glyph.y_offset + len(glyph.rows) <= font.baseline
        for glyph in glyphs
    )
    if charcell:
        return (
            "C",
            "constant advances and every represented raster fits the character cell",
        )
    return (
        "M",
        "constant advances with at least one represented raster outside the character cell",
    )


def bdf_profile(
    font: BitmapFont,
    *,
    foundry: str,
    family_name: str,
    add_style_name: str = "",
    weight_name: str = "Unknown",
    slant: str = "OT",
    setwidth_name: str = "Unknown",
    resolution_x: int = 72,
    resolution_y: int = 72,
    charset_registry: str = "Misc",
    charset_encoding: str = "FontSpecific",
) -> dict[str, object]:
    """Build the complete XLFD/BDF metadata for one recovered bitmap font.

    Seventy-two dpi is an explicit interchange convention, not a claim about
    a physical Lisp Machine display.  It makes one source pixel equal one nominal
    point while retaining the source's integer device widths exactly.
    """

    if not font.glyphs:
        raise ValueError(f"font {font.name!r} contains no glyphs")
    if resolution_x <= 0 or resolution_y <= 0:
        raise ValueError("BDF resolution must be positive for a bitmap font")
    if font.character_height <= 0:
        raise ValueError("BDF pixel size must be positive")

    x_foundry = _xlfd_field(foundry)
    x_family = _xlfd_field(family_name)
    x_weight = _xlfd_field(weight_name)
    x_slant = _xlfd_field(slant).upper()
    x_setwidth = _xlfd_field(setwidth_name)
    x_add_style = _xlfd_field(add_style_name)
    x_registry = _xlfd_field(charset_registry)
    x_encoding = _xlfd_field(charset_encoding)
    bdf_glyphs = _bdf_glyphs(font)
    if not bdf_glyphs:
        raise ValueError(f"font {font.name!r} has no renderable BDF glyphs")
    spacing, spacing_reason = _spacing_code(font, bdf_glyphs)
    average_width = _round_ratio(
        sum(abs(glyph.advance) for glyph in bdf_glyphs) * 10,
        len(bdf_glyphs),
    )
    fields = (
        x_foundry,
        x_family,
        x_weight,
        x_slant,
        x_setwidth,
        x_add_style,
        str(font.character_height),
        str(font.character_height * 10),
        str(resolution_x),
        str(resolution_y),
        spacing,
        str(average_width),
        x_registry,
        x_encoding,
    )
    if any(not field for index, field in enumerate(fields) if index != 5):
        raise ValueError("all XLFD fields except ADD_STYLE_NAME must be non-empty")
    xlfd = "-" + "-".join(fields)
    if xlfd.count("-") != 14 or len(xlfd) > 255:
        raise ValueError(f"invalid generated XLFD name: {xlfd!r}")

    return {
        "xlfd_name": xlfd,
        "foundry": x_foundry,
        "family_name": x_family,
        "weight_name": x_weight,
        "slant": x_slant,
        "setwidth_name": x_setwidth,
        "add_style_name": x_add_style,
        "pixel_size": font.character_height,
        "point_size": font.character_height * 10,
        "resolution_x": resolution_x,
        "resolution_y": resolution_y,
        "spacing": spacing,
        "spacing_reason": spacing_reason,
        "average_width": average_width,
        "charset_registry": x_registry,
        "charset_encoding": x_encoding,
        "source_code_encoding": (
            "primary BDF encoding; Unicode scalar value"
            if (x_registry, x_encoding) == ("ISO10646", "1")
            else "primary BDF encoding; numeric value is the original Lisp Machine character code"
        ),
        "outline_kind": "1-bit bitmap",
        "source_glyph_count": len(font.glyphs),
        "bdf_glyph_count": len(bdf_glyphs),
        "omitted_nonrendering_placeholder_count": len(font.glyphs) - len(bdf_glyphs),
        "placeholder_policy": (
            "zero-advance, zero-width, no-ink source slots remain in JSON but "
            "are omitted from BDF because the Xorg PCF renderer rejects them"
        ),
        "resolution_policy": (
            "72 dpi interchange convention; source specifies pixels, not physical resolution"
        ),
        "typographic_classification_policy": (
            "Unknown/OT/Unknown because the source carries no authoritative "
            "weight, slant, or setwidth metadata"
        ),
    }


def write_bdf(
    font: BitmapFont,
    path: Path,
    foundry: str,
    *,
    profile: dict[str, object] | None = None,
) -> None:
    """Write an XLFD-conforming BDF 2.1 representation.

    Lisp Machine character codes are not Unicode.  They are emitted as BDF
    primary encodings under ``Misc-FontSpecific`` so X can address the glyphs
    by their original numeric codes without assigning a modern character-set
    identity.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    glyphs = sorted(_bdf_glyphs(font), key=lambda item: item.code)
    if not glyphs:
        raise ValueError(f"font {font.name!r} contains no glyphs")
    if font.character_height < 1:
        raise ValueError(f"font {font.name!r} has a non-positive character height")

    minimum_x = min(glyph.x_offset for glyph in glyphs)
    maximum_x = max(glyph.x_offset + glyph.bitmap_width for glyph in glyphs)
    minimum_y = min(glyph.y_offset for glyph in glyphs)
    maximum_y = max(glyph.y_offset + len(glyph.rows) for glyph in glyphs)
    bounding_width = maximum_x - minimum_x
    bounding_height = maximum_y - minimum_y
    descent = max(0, font.character_height - font.baseline)
    if profile is None:
        profile = bdf_profile(font, foundry=foundry, family_name=font.name)
    bdf_name = str(profile["xlfd_name"])
    properties = [
        f'FOUNDRY "{profile["foundry"]}"',
        f'FAMILY_NAME "{profile["family_name"]}"',
        f'WEIGHT_NAME "{profile["weight_name"]}"',
        f'SLANT "{profile["slant"]}"',
        f'SETWIDTH_NAME "{profile["setwidth_name"]}"',
        f'ADD_STYLE_NAME "{profile["add_style_name"]}"',
        f'PIXEL_SIZE {profile["pixel_size"]}',
        f'POINT_SIZE {profile["point_size"]}',
        f'RESOLUTION_X {profile["resolution_x"]}',
        f'RESOLUTION_Y {profile["resolution_y"]}',
        f'SPACING "{profile["spacing"]}"',
        f'AVERAGE_WIDTH {profile["average_width"]}',
        f'CHARSET_REGISTRY "{profile["charset_registry"]}"',
        f'CHARSET_ENCODING "{profile["charset_encoding"]}"',
        f"FONT_ASCENT {font.baseline}",
        f"FONT_DESCENT {descent}",
    ]

    lines = [
        "STARTFONT 2.1",
        f"COMMENT Recovered from {font.source_name} ({font.source_format})",
        "COMMENT One-bit source bitmap; no outlines or synthesized scaling",
        "COMMENT SIZE uses a documented 72 dpi interchange convention",
        (
            "COMMENT ENCODING values are Unicode scalar values"
            if (profile["charset_registry"], profile["charset_encoding"])
            == ("ISO10646", "1")
            else "COMMENT ENCODING values are original Lisp Machine character codes, not Unicode"
        ),
        (
            "COMMENT Zero-width non-rendering source placeholders omitted from BDF: "
            f'{profile["omitted_nonrendering_placeholder_count"]}'
        ),
        f"FONT {bdf_name}",
        (
            f'SIZE {font.character_height} {profile["resolution_x"]} '
            f'{profile["resolution_y"]}'
        ),
        (
            f"FONTBOUNDINGBOX {bounding_width} {bounding_height} "
            f"{minimum_x} {minimum_y}"
        ),
        f"STARTPROPERTIES {len(properties)}",
        *properties,
        "ENDPROPERTIES",
        f"CHARS {len(glyphs)}",
    ]

    for glyph in glyphs:
        byte_width = max(1, (glyph.bitmap_width + 7) // 8)
        padding = byte_width * 8 - glyph.bitmap_width
        scalable_width = _round_ratio(
            abs(glyph.advance) * 1000, font.character_height
        ) * (-1 if glyph.advance < 0 else 1)
        lines.extend(
            [
                f"STARTCHAR C{glyph.code:04X}",
                f"ENCODING {glyph.code}",
                f"SWIDTH {scalable_width} 0",
                f"DWIDTH {glyph.advance} 0",
                (
                    f"BBX {glyph.bitmap_width} {len(glyph.rows)} "
                    f"{glyph.x_offset} {glyph.y_offset}"
                ),
                "BITMAP",
            ]
        )
        row_mask = (1 << glyph.bitmap_width) - 1 if glyph.bitmap_width else 0
        for row in glyph.rows:
            lines.append(f"{((row & row_mask) << padding):0{byte_width * 2}X}")
        lines.append("ENDCHAR")
    lines.append("ENDFONT")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


# A tiny code-label alphabet.  It is deliberately program data, not a bundled
# third-party font, so specimen rendering remains dependency-free.
_LABEL_GLYPHS = {
    "0": (0b111, 0b101, 0b101, 0b101, 0b111),
    "1": (0b010, 0b110, 0b010, 0b010, 0b111),
    "2": (0b110, 0b001, 0b111, 0b100, 0b111),
    "3": (0b110, 0b001, 0b111, 0b001, 0b110),
    "4": (0b101, 0b101, 0b111, 0b001, 0b001),
    "5": (0b111, 0b100, 0b110, 0b001, 0b110),
    "6": (0b011, 0b100, 0b111, 0b101, 0b111),
    "7": (0b111, 0b001, 0b010, 0b010, 0b010),
    "8": (0b111, 0b101, 0b111, 0b101, 0b111),
    "9": (0b111, 0b101, 0b111, 0b001, 0b110),
    "A": (0b010, 0b101, 0b111, 0b101, 0b101),
    "B": (0b110, 0b101, 0b110, 0b101, 0b110),
    "C": (0b011, 0b100, 0b100, 0b100, 0b011),
    "D": (0b110, 0b101, 0b101, 0b101, 0b110),
    "E": (0b111, 0b100, 0b110, 0b100, 0b111),
    "F": (0b111, 0b100, 0b110, 0b100, 0b100),
}


class _Canvas:
    def __init__(self, width: int, height: int, background: tuple[int, int, int]):
        self.width = width
        self.height = height
        self.pixels = bytearray(background * (width * height))

    def pixel(self, x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            offset = (y * self.width + x) * 3
            self.pixels[offset : offset + 3] = bytes(color)

    def rectangle(
        self, x: int, y: int, width: int, height: int, color: tuple[int, int, int]
    ) -> None:
        for row in range(max(0, y), min(self.height, y + height)):
            start = (row * self.width + max(0, x)) * 3
            end = (row * self.width + min(self.width, x + width)) * 3
            self.pixels[start:end] = bytes(color) * ((end - start) // 3)

    def png_bytes(self) -> bytes:
        scanlines = bytearray()
        row_size = self.width * 3
        for row in range(self.height):
            scanlines.append(0)
            start = row * row_size
            scanlines.extend(self.pixels[start : start + row_size])

        def chunk(kind: bytes, payload: bytes) -> bytes:
            body = kind + payload
            return struct.pack(">I", len(payload)) + body + struct.pack(
                ">I", zlib.crc32(body) & 0xFFFFFFFF
            )

        return b"".join(
            [
                b"\x89PNG\r\n\x1a\n",
                chunk(
                    b"IHDR",
                    struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0),
                ),
                chunk(b"IDAT", zlib.compress(bytes(scanlines), level=9)),
                chunk(b"IEND", b""),
            ]
        )


def _draw_scaled_pixel(
    canvas: _Canvas,
    x: int,
    y: int,
    scale: int,
    color: tuple[int, int, int],
) -> None:
    canvas.rectangle(x, y, scale, scale, color)


def _draw_label(canvas: _Canvas, text: str, x: int, y: int) -> None:
    color = (80, 84, 92)
    for character in text:
        rows = _LABEL_GLYPHS[character]
        for row_number, row in enumerate(rows):
            for column in range(3):
                if row & (1 << (2 - column)):
                    canvas.pixel(x + column, y + row_number, color)
        x += 4


def _wrapped_text_lines(
    text: str,
    glyphs_by_code: dict[int, Glyph],
    max_advance: int,
) -> list[str]:
    """Wrap text greedily at spaces, falling back to character boundaries.

    Newlines are forced breaks.  A space used as a wrap opportunity is not
    carried to either output line, matching ordinary word-wrapping behavior.
    Words longer than the available advance are split at the last fitting
    character so every returned line observes ``max_advance``.
    """

    def advance(value: str) -> int:
        return sum(glyphs_by_code[ord(character)].advance for character in value)

    lines: list[str] = []
    for paragraph in text.split("\n"):
        remainder = paragraph
        while advance(remainder) > max_advance:
            fitting_end = 0
            fitting_advance = 0
            last_space = -1
            for index, character in enumerate(remainder):
                candidate_advance = (
                    fitting_advance + glyphs_by_code[ord(character)].advance
                )
                if candidate_advance > max_advance:
                    break
                fitting_end = index + 1
                fitting_advance = candidate_advance
                if character == " ":
                    last_space = index
            if fitting_end == 0:
                character = remainder[0]
                raise ValueError(
                    f"glyph U+{ord(character):04X} advance "
                    f"{glyphs_by_code[ord(character)].advance} exceeds specimen "
                    f"maximum {max_advance}"
                )
            if last_space > 0:
                lines.append(remainder[:last_space])
                remainder = remainder[last_space + 1 :]
            else:
                lines.append(remainder[:fitting_end])
                remainder = remainder[fitting_end:]
        lines.append(remainder)
    return lines


def write_text_specimen(
    font: BitmapFont,
    text: str,
    path: Path,
    *,
    max_advance: int,
    scale: int = 2,
    padding: int = 3,
) -> dict[str, object]:
    """Render wrapped Unicode text with the font's exact bitmap geometry.

    ``max_advance`` and ``padding`` are measured in unscaled source pixels.
    The specimen line height is the font's character height plus a documented
    two-pixel presentation gap.  The RGB PNG canvas expands around the
    logical text cells to retain rasters with negative bearings or ink
    above/below the nominal line, then adds equal outer padding.  Returned
    JSON-compatible metadata distinguishes the content extent, padded native
    canvas, and final scaled PNG dimensions.
    """

    if max_advance < 1 or scale < 1 or padding < 0:
        raise ValueError(
            "max advance and scale must be positive; padding must be nonnegative"
        )
    if font.character_height < 1:
        raise ValueError(f"font {font.name!r} has a non-positive character height")
    if not text or not text.replace("\n", ""):
        raise ValueError("text specimen must contain at least one character")

    glyphs_by_code: dict[int, Glyph] = {}
    for glyph in font.glyphs:
        if glyph.code in glyphs_by_code:
            raise ValueError(
                f"font {font.name!r} has duplicate glyph code U+{glyph.code:04X}"
            )
        glyphs_by_code[glyph.code] = glyph
    missing_codes = sorted(
        {ord(character) for character in text if character != "\n"}
        - glyphs_by_code.keys()
    )
    if missing_codes:
        missing = ", ".join(f"U+{code:04X}" for code in missing_codes)
        raise ValueError(f"font {font.name!r} lacks specimen characters: {missing}")

    wrapped_lines = _wrapped_text_lines(text, glyphs_by_code, max_advance)
    vsp = 2
    line_height = font.character_height + vsp
    nominal_height = (
        font.character_height + (len(wrapped_lines) - 1) * line_height
    )
    minimum_x = 0
    maximum_x = 0
    minimum_y = 0
    maximum_y = nominal_height
    placements: list[tuple[Glyph, int, int]] = []
    line_records: list[dict[str, object]] = []

    for line_index, line in enumerate(wrapped_lines):
        pen_x = 0
        baseline = font.baseline + line_index * line_height
        for character in line:
            glyph = glyphs_by_code[ord(character)]
            glyph_x = pen_x + glyph.x_offset
            glyph_top = baseline - glyph.y_offset - len(glyph.rows)
            placements.append((glyph, glyph_x, glyph_top))
            minimum_x = min(minimum_x, pen_x, pen_x + glyph.advance, glyph_x)
            maximum_x = max(
                maximum_x,
                pen_x,
                pen_x + glyph.advance,
                glyph_x + glyph.bitmap_width,
            )
            minimum_y = min(minimum_y, glyph_top)
            maximum_y = max(maximum_y, glyph_top + len(glyph.rows))
            pen_x += glyph.advance
        minimum_x = min(minimum_x, pen_x)
        maximum_x = max(maximum_x, pen_x)
        line_records.append(
            {
                "index": line_index,
                "text": line,
                "advance": pen_x,
                "baseline": baseline,
            }
        )

    content_native_width = maximum_x - minimum_x
    content_native_height = maximum_y - minimum_y
    if content_native_width < 1 or content_native_height < 1:
        raise ValueError("text specimen has no positive raster extent")
    canvas_native_width = content_native_width + padding * 2
    canvas_native_height = content_native_height + padding * 2
    canvas = _Canvas(
        canvas_native_width * scale,
        canvas_native_height * scale,
        (250, 250, 248),
    )
    for glyph, glyph_x, glyph_top in placements:
        for row_number, row in enumerate(glyph.rows):
            for column in range(glyph.bitmap_width):
                if row & (1 << (glyph.bitmap_width - column - 1)):
                    _draw_scaled_pixel(
                        canvas,
                        (padding + glyph_x - minimum_x + column) * scale,
                        (padding + glyph_top - minimum_y + row_number) * scale,
                        scale,
                        (18, 20, 22),
                    )

    for record in line_records:
        canvas_baseline = padding + int(record["baseline"]) - minimum_y
        record["canvas_baseline"] = canvas_baseline
        record["pixel_baseline"] = canvas_baseline * scale

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canvas.png_bytes())
    return {
        "text": text,
        "max_native_advance": max_advance,
        "scale": scale,
        "native_padding": padding,
        "vsp": vsp,
        "native_line_height": line_height,
        "content_native_bounds": {
            "left": minimum_x,
            "top": minimum_y,
            "right": maximum_x,
            "bottom": maximum_y,
        },
        "content_native_width": content_native_width,
        "content_native_height": content_native_height,
        "canvas_native_width": canvas_native_width,
        "canvas_native_height": canvas_native_height,
        "width": canvas.width,
        "height": canvas.height,
        "line_count": len(line_records),
        "lines": line_records,
    }


def write_sheet(
    font: BitmapFont,
    path: Path,
    *,
    columns: int = 16,
    scale: int = 2,
    label_radix: int = 16,
) -> None:
    """Write a deterministic grid of every represented glyph as RGB PNG."""

    if columns < 1 or scale < 1:
        raise ValueError("columns and scale must be positive")
    if label_radix not in {8, 16}:
        raise ValueError("label radix must be 8 or 16")
    glyphs = sorted(font.glyphs, key=lambda item: item.code)
    if not glyphs:
        raise ValueError(f"font {font.name!r} contains no glyphs")

    minimum_x = min(0, *(glyph.x_offset for glyph in glyphs))
    maximum_x = max(
        max(glyph.advance, glyph.x_offset + glyph.bitmap_width) for glyph in glyphs
    )
    raster_width = maximum_x - minimum_x
    glyph_tops = [
        font.baseline - glyph.y_offset - len(glyph.rows) for glyph in glyphs
    ]
    minimum_top = min(0, *glyph_tops)
    maximum_bottom = max(
        font.raster_height,
        *(
            glyph_top + len(glyph.rows)
            for glyph_top, glyph in zip(glyph_tops, glyphs)
        ),
    )
    raster_height = maximum_bottom - minimum_top
    if label_radix == 8:
        label_digits = max(3, max(len(f"{glyph.code:o}") for glyph in glyphs))
    else:
        label_digits = max(2, max(len(f"{glyph.code:X}") for glyph in glyphs))
    label_width = label_digits * 4 - 1
    padding = 3
    cell_width = max(raster_width * scale, label_width) + padding * 2
    cell_height = raster_height * scale + 5 + padding * 3
    row_count = (len(glyphs) + columns - 1) // columns
    canvas = _Canvas(
        cell_width * columns + 1,
        cell_height * row_count + 1,
        (250, 250, 248),
    )

    for index, glyph in enumerate(glyphs):
        cell_x = (index % columns) * cell_width
        cell_y = (index // columns) * cell_height
        canvas.rectangle(cell_x, cell_y, cell_width, 1, (214, 216, 218))
        canvas.rectangle(cell_x, cell_y, 1, cell_height, (214, 216, 218))

        origin_x = cell_x + padding + (-minimum_x) * scale
        raster_top = cell_y + padding
        baseline_y = raster_top + (font.baseline - minimum_top) * scale
        canvas.rectangle(
            cell_x + 1, baseline_y, cell_width - 1, 1, (226, 214, 214)
        )
        glyph_x = origin_x + glyph.x_offset * scale
        glyph_top = glyph_tops[index]
        glyph_y = raster_top + (glyph_top - minimum_top) * scale
        for row_number, row in enumerate(glyph.rows):
            for column in range(glyph.bitmap_width):
                if row & (1 << (glyph.bitmap_width - column - 1)):
                    _draw_scaled_pixel(
                        canvas,
                        glyph_x + column * scale,
                        glyph_y + row_number * scale,
                        scale,
                        (18, 20, 22),
                    )
        label = (
            f"{glyph.code:0{label_digits}o}"
            if label_radix == 8
            else f"{glyph.code:0{label_digits}X}"
        )
        label_x = cell_x + (cell_width - label_width) // 2
        _draw_label(canvas, label, label_x, cell_y + cell_height - padding - 5)

    canvas.rectangle(0, canvas.height - 1, canvas.width, 1, (214, 216, 218))
    canvas.rectangle(canvas.width - 1, 0, 1, canvas.height, (214, 216, 218))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canvas.png_bytes())


def write_font_outputs(
    font: BitmapFont,
    output_directory: Path,
    *,
    foundry: str,
    sheet_columns: int = 16,
    sheet_scale: int = 2,
    include_json: bool = True,
    sheet_label_radix: int = 16,
    bdf_metadata: dict[str, object] | None = None,
) -> dict[str, str]:
    """Write all standard outputs and return their relative paths."""

    stem = safe_filename(font.name)
    destinations = {
        "bdf": output_directory / "bdf" / f"{stem}.bdf",
        "sheet": output_directory / "sheets" / f"{stem}.png",
    }
    if include_json:
        destinations["json"] = output_directory / "json" / f"{stem}.json"
        write_json(font, destinations["json"])
    write_bdf(font, destinations["bdf"], foundry, profile=bdf_metadata)
    write_sheet(
        font,
        destinations["sheet"],
        columns=sheet_columns,
        scale=sheet_scale,
        label_radix=sheet_label_radix,
    )
    return {
        kind: str(path.relative_to(output_directory))
        for kind, path in destinations.items()
    }


def write_catalog(records: Iterable[dict[str, object]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(list(records), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
