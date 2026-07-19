#!/usr/bin/env python3
"""Check native-size X core rendering against recovered Genera bitmap geometry.

The pure-Python half of this checker parses BDF directly and produces the
expected foreground pixels and text extents for sequences of integer BDF
encodings.  With ``--external`` it compiles the BDFs to PCF, starts an isolated
Xvfb, draws the same sequences through Xlib, and compares both the framebuffer
and text-extents results.  Raw ``Misc-FontSpecific`` profiles retain their
historical 8-bit ``XDrawString`` path; ``ISO10646-1`` derivatives use
``XDrawString16`` and ``XTextExtents16``.

Only codes that are explicitly present in a BDF are put in conformance probes.
What X does for an absent code (default-character substitution, another
fallback, or no drawing) is intentionally outside this check: missing-code
policy is separate from the geometry of defined glyphs.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
import ctypes.util
from dataclasses import dataclass
from pathlib import Path
import shutil
import shlex
import subprocess
import tempfile
import time
from typing import Iterator, Sequence


ROOT = Path(__file__).resolve().parents[1]
XY_PIXMAP = 1
Z_PIXMAP = 2
ALL_PLANES = ctypes.c_ulong(-1).value


class RenderCheckError(AssertionError):
    """A BDF or X rendering result violates a checked invariant."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RenderCheckError(message)


@dataclass(frozen=True)
class Glyph:
    code: int
    advance: int
    width: int
    height: int
    x_offset: int
    y_offset: int
    rows: tuple[int, ...]

    @property
    def lbearing(self) -> int:
        return self.x_offset

    @property
    def rbearing(self) -> int:
        return self.x_offset + self.width

    @property
    def ascent(self) -> int:
        return self.y_offset + self.height

    @property
    def descent(self) -> int:
        return -self.y_offset

    def ink_bounds(self) -> tuple[int, int, int, int]:
        """Return the PCF/X core ink metrics derived from set pixels.

        ``bdftopcf`` exposes ink bounds, rather than the possibly padded BDF
        BBX, through QueryFont and XTextExtents.  A blank glyph has zero ink
        bounds while retaining its independent logical advance.
        """

        columns: list[int] = []
        raster_rows: list[int] = []
        for row_index, row in enumerate(self.rows):
            for column in range(self.width):
                if row & (1 << (self.width - 1 - column)):
                    columns.append(column)
                    raster_rows.append(row_index)
        if not columns:
            return (0, 0, 0, 0)
        left_column = min(columns)
        right_column = max(columns)
        top_row = min(raster_rows)
        bottom_row = max(raster_rows)
        return (
            self.x_offset + left_column,
            self.x_offset + right_column + 1,
            self.y_offset + self.height - top_row,
            -(self.y_offset + self.height - bottom_row - 1),
        )


@dataclass(frozen=True)
class BdfFont:
    path: Path
    name: str
    ascent: int
    descent: int
    spacing: str
    glyphs: dict[int, Glyph]
    charset_registry: str = "Misc"
    charset_encoding: str = "FontSpecific"

    @property
    def character_height(self) -> int:
        return self.ascent + self.descent

    @property
    def is_iso10646(self) -> bool:
        return (
            self.charset_registry.casefold(),
            self.charset_encoding.casefold(),
        ) == ("iso10646", "1")


@dataclass(frozen=True)
class TextExtents:
    lbearing: int
    rbearing: int
    width: int
    ascent: int
    descent: int


@dataclass(frozen=True)
class Raster:
    pixels: frozenset[tuple[int, int]]
    extents: TextExtents


def _one_line(lines: list[str], prefix: str, path: Path) -> str:
    values = [line[len(prefix) :] for line in lines if line.startswith(prefix)]
    require(len(values) == 1, f"{path}: expected exactly one {prefix.strip()}")
    return values[0]


def parse_bdf(path: Path) -> BdfFont:
    """Parse the BDF fields that determine native one-bit rendering."""

    lines = path.read_text(encoding="ascii").splitlines()
    require(bool(lines) and lines[0] == "STARTFONT 2.1", f"{path}: not BDF 2.1")
    require(lines[-1:] == ["ENDFONT"], f"{path}: missing ENDFONT")
    name = _one_line(lines, "FONT ", path)

    property_start = next(
        (index for index, line in enumerate(lines) if line.startswith("STARTPROPERTIES ")),
        None,
    )
    require(property_start is not None, f"{path}: missing STARTPROPERTIES")
    property_end = lines.index("ENDPROPERTIES", property_start + 1)
    declared_properties = int(lines[property_start].split()[1])
    property_lines = lines[property_start + 1 : property_end]
    require(
        len(property_lines) == declared_properties,
        f"{path}: STARTPROPERTIES count mismatch",
    )
    properties: dict[str, str] = {}
    for line in property_lines:
        key, value = line.split(" ", 1)
        require(key not in properties, f"{path}: duplicate property {key}")
        properties[key] = value[1:-1] if value.startswith('"') and value.endswith('"') else value
    try:
        ascent = int(properties["FONT_ASCENT"])
        descent = int(properties["FONT_DESCENT"])
    except (KeyError, ValueError) as error:
        raise RenderCheckError(f"{path}: invalid FONT_ASCENT/FONT_DESCENT") from error
    require(ascent >= 0 and descent >= 0, f"{path}: negative line metric")
    spacing = properties.get("SPACING", "")
    require(spacing in {"P", "M", "C"}, f"{path}: invalid XLFD SPACING property")

    declared_chars = int(_one_line(lines, "CHARS ", path))
    glyphs: dict[int, Glyph] = {}
    index = 0
    while index < len(lines):
        if not lines[index].startswith("STARTCHAR "):
            index += 1
            continue
        end = lines.index("ENDCHAR", index + 1)
        segment = lines[index : end + 1]

        def glyph_one(prefix: str) -> str:
            values = [line[len(prefix) :] for line in segment if line.startswith(prefix)]
            require(len(values) == 1, f"{path}: glyph missing {prefix.strip()}")
            return values[0]

        encoding_fields = glyph_one("ENCODING ").split()
        require(
            len(encoding_fields) == 1,
            f"{path}: secondary BDF encodings are outside the rendering check",
        )
        code = int(encoding_fields[0])
        require(
            0 <= code <= 0xFFFF,
            f"{path}: encoding {code} is outside the Unicode BMP",
        )
        require(
            not 0xD800 <= code <= 0xDFFF,
            f"{path}: encoding U+{code:04X} is a surrogate, not a Unicode scalar",
        )
        require(code not in glyphs, f"{path}: duplicate encoding {code}")
        dwidth = tuple(map(int, glyph_one("DWIDTH ").split()))
        require(len(dwidth) == 2 and dwidth[1] == 0, f"{path}: non-horizontal DWIDTH")
        box = tuple(map(int, glyph_one("BBX ").split()))
        require(len(box) == 4, f"{path}: malformed BBX at {code}")
        width, height, x_offset, y_offset = box
        require(width >= 0 and height >= 0, f"{path}: negative BBX at {code}")
        bitmap_index = segment.index("BITMAP")
        bitmap_lines = segment[bitmap_index + 1 : -1]
        require(len(bitmap_lines) == height, f"{path}: bitmap height mismatch at {code}")
        byte_width = (width + 7) // 8
        padding = byte_width * 8 - width
        rows: list[int] = []
        for row in bitmap_lines:
            require(
                len(row) == byte_width * 2,
                f"{path}: bitmap row width mismatch at {code}",
            )
            value = int(row, 16) if row else 0
            require(
                padding == 0 or value & ((1 << padding) - 1) == 0,
                f"{path}: nonzero row padding at {code}",
            )
            rows.append(value >> padding)
        glyphs[code] = Glyph(
            code=code,
            advance=dwidth[0],
            width=width,
            height=height,
            x_offset=x_offset,
            y_offset=y_offset,
            rows=tuple(rows),
        )
        index = end + 1

    require(len(glyphs) == declared_chars, f"{path}: CHARS count mismatch")
    require(bool(glyphs), f"{path}: cannot render an empty font")
    return BdfFont(
        path=path,
        name=name,
        ascent=ascent,
        descent=descent,
        spacing=spacing,
        glyphs=glyphs,
        charset_registry=properties.get("CHARSET_REGISTRY", ""),
        charset_encoding=properties.get("CHARSET_ENCODING", ""),
    )


def measure_defined_text(
    font: BdfFont, data: Sequence[int], *, metric_kind: str = "box"
) -> TextExtents:
    """Calculate box or set-pixel ink extents for defined code points.

    PCF can contain both the original BDF character boxes and separately
    computed ink metrics.  Depending on the font-wide accelerator choices,
    XTextExtents may expose either table.  Both are deterministic views of the
    same BDF; the framebuffer comparison below remains exact regardless.
    """

    require(metric_kind in {"box", "ink"}, "metric_kind must be box or ink")

    if not data:
        return TextExtents(0, 0, 0, 0, 0)
    pen = 0
    left: int | None = None
    right: int | None = None
    ascent: int | None = None
    descent: int | None = None
    for code in data:
        require(
            code in font.glyphs,
            f"{font.path}: probe contains undefined code {code} (U+{code:04X})",
        )
        glyph = font.glyphs[code]
        if metric_kind == "box":
            metric_left, metric_right = glyph.lbearing, glyph.rbearing
            metric_ascent, metric_descent = glyph.ascent, glyph.descent
        else:
            metric_left, metric_right, metric_ascent, metric_descent = glyph.ink_bounds()
        glyph_left = pen + metric_left
        glyph_right = pen + metric_right
        left = glyph_left if left is None else min(left, glyph_left)
        right = glyph_right if right is None else max(right, glyph_right)
        ascent = metric_ascent if ascent is None else max(ascent, metric_ascent)
        descent = metric_descent if descent is None else max(descent, metric_descent)
        pen += glyph.advance
    return TextExtents(
        lbearing=left if left is not None else 0,
        rbearing=right if right is not None else 0,
        width=pen,
        ascent=ascent if ascent is not None else 0,
        descent=descent if descent is not None else 0,
    )


def expected_xtext_extent_options(
    font: BdfFont, data: Sequence[int]
) -> frozenset[TextExtents]:
    """Return the two BDF-derived metric views X core fonts may expose.

    PCF preserves both the declared BDF character boxes and computed ink
    bounds.  X servers may expose either table through ``XTextExtents`` based
    on accelerator flags chosen by ``bdftopcf``.  Both candidates are derived
    independently from the checked BDF.  Pixel rendering is still compared
    exactly below, so accepting either metric table does not weaken the glyph
    geometry check.
    """

    return frozenset(
        {
            measure_defined_text(font, data, metric_kind="box"),
            measure_defined_text(font, data, metric_kind="ink"),
        }
    )


def render_defined_text(
    font: BdfFont,
    data: Sequence[int],
    *,
    origin_x: int = 0,
    baseline_y: int = 0,
) -> Raster:
    """Render explicitly defined code points into foreground coordinates."""

    extents = measure_defined_text(font, data)
    pixels: set[tuple[int, int]] = set()
    pen = origin_x
    for code in data:
        glyph = font.glyphs[code]
        top = baseline_y - glyph.y_offset - glyph.height
        for row_index, row in enumerate(glyph.rows):
            for column in range(glyph.width):
                if row & (1 << (glyph.width - 1 - column)):
                    pixels.add((pen + glyph.x_offset + column, top + row_index))
        pen += glyph.advance
    return Raster(frozenset(pixels), extents)


def probe_strings(
    font: BdfFont, *, maximum_codepoints: int = 200
) -> tuple[tuple[int, ...], ...]:
    """Make probes containing every defined BDF encoding exactly once."""

    require(maximum_codepoints > 0, "maximum probe length must be positive")
    codes = tuple(sorted(font.glyphs))
    return tuple(
        codes[index : index + maximum_codepoints]
        for index in range(0, len(codes), maximum_codepoints)
    )


class XCharStruct(ctypes.Structure):
    _fields_ = [
        ("lbearing", ctypes.c_short),
        ("rbearing", ctypes.c_short),
        ("width", ctypes.c_short),
        ("ascent", ctypes.c_short),
        ("descent", ctypes.c_short),
        ("attributes", ctypes.c_ushort),
    ]


class XChar2b(ctypes.Structure):
    """The two-byte X core character representation, most-significant first."""

    _fields_ = [
        ("byte1", ctypes.c_ubyte),
        ("byte2", ctypes.c_ubyte),
    ]


class XFontProp(ctypes.Structure):
    _fields_ = [("name", ctypes.c_ulong), ("card32", ctypes.c_ulong)]


class XFontStruct(ctypes.Structure):
    _fields_ = [
        ("ext_data", ctypes.c_void_p),
        ("fid", ctypes.c_ulong),
        ("direction", ctypes.c_uint),
        ("min_char_or_byte2", ctypes.c_uint),
        ("max_char_or_byte2", ctypes.c_uint),
        ("min_byte1", ctypes.c_uint),
        ("max_byte1", ctypes.c_uint),
        ("all_chars_exist", ctypes.c_int),
        ("default_char", ctypes.c_uint),
        ("n_properties", ctypes.c_int),
        ("properties", ctypes.POINTER(XFontProp)),
        ("min_bounds", XCharStruct),
        ("max_bounds", XCharStruct),
        ("per_char", ctypes.POINTER(XCharStruct)),
        ("ascent", ctypes.c_int),
        ("descent", ctypes.c_int),
    ]


class XErrorEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("resource_id", ctypes.c_ulong),
        ("serial", ctypes.c_ulong),
        ("error_code", ctypes.c_ubyte),
        ("request_code", ctypes.c_ubyte),
        ("minor_code", ctypes.c_ubyte),
    ]


def external_dependencies() -> dict[str, str | None]:
    return {
        "bdftopcf": shutil.which("bdftopcf"),
        "mkfontdir": shutil.which("mkfontdir"),
        "Xvfb": shutil.which("Xvfb"),
        "libX11": ctypes.util.find_library("X11"),
    }


def _require_external_dependencies() -> dict[str, str]:
    dependencies = external_dependencies()
    missing = [name for name, path in dependencies.items() if path is None]
    require(not missing, "external rendering check requires " + ", ".join(missing))
    return {name: str(path) for name, path in dependencies.items()}


def _configure_xlib(library: str):
    x11 = ctypes.CDLL(library)
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XLoadQueryFont.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    x11.XLoadQueryFont.restype = ctypes.POINTER(XFontStruct)
    x11.XFreeFont.argtypes = [ctypes.c_void_p, ctypes.POINTER(XFontStruct)]
    x11.XCreatePixmap.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint]
    x11.XCreatePixmap.restype = ctypes.c_ulong
    x11.XFreePixmap.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XCreateGC.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
    x11.XCreateGC.restype = ctypes.c_void_p
    x11.XFreeGC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    x11.XSetFont.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    x11.XSetForeground.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    x11.XFillRectangle.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
    x11.XDrawString.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    x11.XTextExtents.argtypes = [ctypes.POINTER(XFontStruct), ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(XCharStruct)]
    x11.XDrawString16.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(XChar2b),
        ctypes.c_int,
    ]
    x11.XTextExtents16.argtypes = [
        ctypes.POINTER(XFontStruct),
        ctypes.POINTER(XChar2b),
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(XCharStruct),
    ]
    x11.XGetImage.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_uint, ctypes.c_uint, ctypes.c_ulong, ctypes.c_int]
    x11.XGetImage.restype = ctypes.c_void_p
    x11.XGetPixel.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    x11.XGetPixel.restype = ctypes.c_ulong
    x11.XDestroyImage.argtypes = [ctypes.c_void_p]
    x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
    return x11


@contextmanager
def isolated_xvfb(
    xvfb: str,
    font_directories: Path | Sequence[Path],
    x11,
) -> Iterator[tuple[ctypes.c_void_p, list[tuple[int, int, int]]]]:
    """Start Xvfb on an unused high display and yield its Xlib connection."""

    directories = (
        [font_directories]
        if isinstance(font_directories, Path)
        else list(font_directories)
    )
    require(bool(directories), "Xvfb requires at least one font directory")
    font_path = ",".join(str(path) for path in directories)
    error_handler_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(XErrorEvent))
    errors: list[tuple[int, int, int]] = []

    @error_handler_type
    def record_error(_display, event):
        errors.append((event.contents.error_code, event.contents.request_code, event.contents.minor_code))
        return 0

    x11.XSetErrorHandler.argtypes = [ctypes.c_void_p]
    x11.XSetErrorHandler.restype = ctypes.c_void_p
    previous_handler = x11.XSetErrorHandler(ctypes.cast(record_error, ctypes.c_void_p))
    server: subprocess.Popen[str] | None = None
    display = None
    try:
        for candidate in range(1100, 1200):
            if Path(f"/tmp/.X{candidate}-lock").exists() or Path(f"/tmp/.X11-unix/X{candidate}").exists():
                continue
            candidate_server = subprocess.Popen(
                [
                    xvfb,
                    f":{candidate}",
                    "-screen",
                    "0",
                    "640x480x8",
                    "-nolisten",
                    "tcp",
                    "-fp",
                    font_path,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            deadline = time.monotonic() + 5
            candidate_display = None
            while time.monotonic() < deadline:
                if candidate_server.poll() is not None:
                    break
                candidate_display = x11.XOpenDisplay(f":{candidate}".encode("ascii"))
                if candidate_display:
                    break
                time.sleep(0.05)
            if candidate_display and candidate_server.poll() is None:
                server = candidate_server
                display = candidate_display
                break
            if candidate_display:
                x11.XCloseDisplay(candidate_display)
            candidate_server.terminate()
            try:
                candidate_server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                candidate_server.kill()
                candidate_server.wait(timeout=5)
        require(server is not None and display, "cannot start an isolated rendering Xvfb")
        yield display, errors
    finally:
        if display:
            x11.XCloseDisplay(display)
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
        # Keep the callback alive until after all requests and restore Xlib's
        # process-global handler before returning to another checker/test.
        x11.XSetErrorHandler(previous_handler)


def _x_character_width(font: BdfFont) -> int:
    """Select the reviewed X core API for one generated character-set profile."""

    if font.is_iso10646:
        return 16
    require(
        (
            font.charset_registry.casefold(),
            font.charset_encoding.casefold(),
        )
        == ("misc", "fontspecific"),
        f"{font.path}: unsupported X core charset "
        f"{font.charset_registry}-{font.charset_encoding}",
    )
    require(
        all(code <= 0xFF for code in font.glyphs),
        f"{font.path}: Misc-FontSpecific profile contains a non-8-bit encoding",
    )
    return 8


def _xchar2b_array(data: Sequence[int]):
    """Return an XChar2b array whose byte1 is the most-significant byte."""

    array_type = XChar2b * len(data)
    return array_type(
        *(XChar2b((code >> 8) & 0xFF, code & 0xFF) for code in data)
    )


def _format_probe(font: BdfFont, data: Sequence[int]) -> str:
    if font.is_iso10646:
        return " ".join(f"U+{code:04X}" for code in data)
    return " ".join(f"0x{code:02X}" for code in data)


def _x_text_extents(
    x11,
    font_pointer,
    font: BdfFont,
    data: Sequence[int],
) -> tuple[TextExtents, int, int]:
    direction = ctypes.c_int()
    font_ascent = ctypes.c_int()
    font_descent = ctypes.c_int()
    overall = XCharStruct()
    if _x_character_width(font) == 16:
        buffer16 = _xchar2b_array(data)
        x11.XTextExtents16(
            font_pointer,
            buffer16,
            len(data),
            ctypes.byref(direction),
            ctypes.byref(font_ascent),
            ctypes.byref(font_descent),
            ctypes.byref(overall),
        )
    else:
        raw = bytes(data)
        buffer8 = ctypes.create_string_buffer(raw, len(raw) + 1)
        x11.XTextExtents(
            font_pointer,
            ctypes.cast(buffer8, ctypes.c_void_p),
            len(raw),
            ctypes.byref(direction),
            ctypes.byref(font_ascent),
            ctypes.byref(font_descent),
            ctypes.byref(overall),
        )
    require(direction.value == 0, "X reports a right-to-left core font")
    return (
        TextExtents(
            lbearing=overall.lbearing,
            rbearing=overall.rbearing,
            width=overall.width,
            ascent=overall.ascent,
            descent=overall.descent,
        ),
        font_ascent.value,
        font_descent.value,
    )


def _x_draw_pixels(
    x11,
    display,
    font_pointer,
    font: BdfFont,
    data: Sequence[int],
    expected: TextExtents,
) -> frozenset[tuple[int, int]]:
    margin = 3
    origin_x = margin - min(0, expected.lbearing)
    baseline_y = margin + max(0, expected.ascent)
    width = max(1, origin_x + max(expected.width, expected.rbearing) + margin)
    height = max(1, baseline_y + max(0, expected.descent) + margin)
    root = x11.XDefaultRootWindow(display)
    pixmap = x11.XCreatePixmap(display, root, width, height, 1)
    require(bool(pixmap), "XCreatePixmap failed")
    gc = x11.XCreateGC(display, pixmap, 0, None)
    require(bool(gc), "XCreateGC failed")
    image = None
    try:
        x11.XSetForeground(display, gc, 0)
        x11.XFillRectangle(display, pixmap, gc, 0, 0, width, height)
        x11.XSetForeground(display, gc, 1)
        x11.XSetFont(display, gc, font_pointer.contents.fid)
        if _x_character_width(font) == 16:
            buffer16 = _xchar2b_array(data)
            x11.XDrawString16(
                display,
                pixmap,
                gc,
                origin_x,
                baseline_y,
                buffer16,
                len(data),
            )
        else:
            raw = bytes(data)
            buffer8 = ctypes.create_string_buffer(raw, len(raw) + 1)
            x11.XDrawString(
                display,
                pixmap,
                gc,
                origin_x,
                baseline_y,
                ctypes.cast(buffer8, ctypes.c_void_p),
                len(raw),
            )
        x11.XSync(display, 0)
        image = x11.XGetImage(display, pixmap, 0, 0, width, height, ALL_PLANES, Z_PIXMAP)
        require(bool(image), "XGetImage failed")
        pixels = {
            (x - origin_x, y - baseline_y)
            for y in range(height)
            for x in range(width)
            if x11.XGetPixel(image, x, y) != 0
        }
        return frozenset(pixels)
    finally:
        if image:
            x11.XDestroyImage(image)
        x11.XFreeGC(display, gc)
        x11.XFreePixmap(display, pixmap)


def parse_fonts_alias(path: Path) -> dict[str, str]:
    """Parse the two-field aliases emitted by the reproducible builders."""

    aliases: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("!"):
            continue
        try:
            fields = shlex.split(stripped, posix=True)
        except ValueError as error:
            raise RenderCheckError(f"{path}:{line_number}: malformed alias") from error
        require(len(fields) == 2, f"{path}:{line_number}: alias must have two fields")
        alias, target = fields
        folded = alias.casefold()
        require(folded not in aliases, f"{path}:{line_number}: duplicate alias {alias}")
        aliases[folded] = target
    return aliases


def validate_external(paths: Sequence[Path]) -> dict[str, int]:
    """Compile and compare all defined-glyph probes through X core rendering."""

    require(bool(paths), "no BDF files selected for external rendering")
    dependencies = _require_external_dependencies()
    fonts = [parse_bdf(path) for path in paths]
    with tempfile.TemporaryDirectory(prefix="genera-font-render-") as directory:
        temporary = Path(directory)
        grouped_fonts: dict[Path, list[BdfFont]] = {}
        for font in fonts:
            grouped_fonts.setdefault(font.path.parent.resolve(), []).append(font)
        font_directories: list[Path] = []
        aliases: dict[str, str] = {}
        for group_number, (source_directory, group) in enumerate(
            sorted(grouped_fonts.items(), key=lambda item: str(item[0]))
        ):
            font_directory = temporary / f"font-path-{group_number:02d}"
            font_directory.mkdir()
            font_directories.append(font_directory)
            for font_number, font in enumerate(sorted(group, key=lambda item: str(item.path))):
                subprocess.run(
                    [
                        dependencies["bdftopcf"],
                        str(font.path),
                        "-o",
                        str(font_directory / f"font-{font_number:04d}.pcf"),
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            subprocess.run(
                [dependencies["mkfontdir"], str(font_directory)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            alias_source = source_directory / "fonts.alias"
            if alias_source.is_file():
                group_aliases = parse_fonts_alias(alias_source)
                for alias, target in group_aliases.items():
                    previous = aliases.get(alias)
                    require(
                        previous is None or previous.casefold() == target.casefold(),
                        f"combined X font-path alias collision for {alias}",
                    )
                    aliases[alias] = target
                shutil.copyfile(alias_source, font_directory / "fonts.alias")
        x11 = _configure_xlib(dependencies["libX11"])
        probe_count = 0
        glyph_count = 0
        with isolated_xvfb(dependencies["Xvfb"], font_directories, x11) as (display, x_errors):
            for font in fonts:
                x_errors.clear()
                font_pointer = x11.XLoadQueryFont(display, font.name.encode("ascii"))
                x11.XSync(display, 0)
                require(not x_errors, f"{font.path}: X error loading font: {x_errors}")
                require(bool(font_pointer), f"{font.path}: X cannot load {font.name}")
                try:
                    require(
                        font_pointer.contents.ascent == font.ascent
                        and font_pointer.contents.descent == font.descent,
                        f"{font.path}: XFontStruct line metrics differ from BDF",
                    )
                    for data in probe_strings(font):
                        expected_box_extents = measure_defined_text(font, data, metric_kind="box")
                        expected_extents = expected_xtext_extent_options(font, data)
                        observed_extents, font_ascent, font_descent = (
                            _x_text_extents(x11, font_pointer, font, data)
                        )
                        require(
                            (font_ascent, font_descent) == (font.ascent, font.descent),
                            f"{font.path}: X text-extents line metrics differ from BDF",
                        )
                        require(
                            observed_extents in expected_extents,
                            f"{font.path}: X text extents differ for defined codes "
                            f"{_format_probe(font, data)}: "
                            f"expected one of {sorted(expected_extents, key=repr)}, "
                            f"got {observed_extents}",
                        )
                        expected_pixels = render_defined_text(font, data).pixels
                        observed_pixels = _x_draw_pixels(
                            x11,
                            display,
                            font_pointer,
                            font,
                            data,
                            expected_box_extents,
                        )
                        require(
                            observed_pixels == expected_pixels,
                            f"{font.path}: X framebuffer differs for defined codes "
                            f"{_format_probe(font, data)} "
                            f"(missing {len(expected_pixels - observed_pixels)}, "
                            f"extra {len(observed_pixels - expected_pixels)})",
                        )
                        probe_count += 1
                        glyph_count += len(data)
                        x11.XSync(display, 0)
                        require(not x_errors, f"{font.path}: X rendering error: {x_errors}")
                finally:
                    x11.XFreeFont(display, font_pointer)
            for alias in sorted(aliases):
                x_errors.clear()
                font_pointer = x11.XLoadQueryFont(display, alias.encode("ascii"))
                x11.XSync(display, 0)
                require(not x_errors, f"X error loading combined alias {alias}: {x_errors}")
                require(bool(font_pointer), f"X cannot load combined alias {alias}")
                x11.XFreeFont(display, font_pointer)
        return {
            "font_count": len(fonts),
            "probe_count": probe_count,
            "glyph_count": glyph_count,
            "font_path_count": len(font_directories),
            "alias_count": len(aliases),
        }


def discover_bdfs(output: Path) -> list[Path]:
    """Find BDFs in the raw and Unicode profile directories."""

    profile_directories = (
        output / "raw" / "bdf",
        output / "unicode" / "bdf",
    )
    return sorted(
        path
        for directory in profile_directories
        if directory.is_dir()
        for path in directory.glob("*.bdf")
        if path.is_file()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    parser.add_argument(
        "--external",
        action="store_true",
        help=(
            "require BDF/PCF/Xvfb tools and compare X framebuffer pixels "
            "and 8/16-bit text extents"
        ),
    )
    args = parser.parse_args()
    paths = discover_bdfs(args.output.resolve())
    try:
        require(bool(paths), f"{args.output}: no generated BDF files found")
        fonts = [parse_bdf(path) for path in paths]
        glyph_count = sum(len(font.glyphs) for font in fonts)
        probe_count = sum(len(probe_strings(font)) for font in fonts)
        # Exercise the independent renderer even without an X installation.
        for font in fonts:
            for data in probe_strings(font):
                render_defined_text(font, data)
        print(
            f"render model: {len(fonts)} fonts, {glyph_count} defined glyphs, "
            f"{probe_count} encoding probes; undefined-code substitution excluded"
        )
        if args.external:
            result = validate_external(paths)
            print(
                "X conformance: "
                f"{result['font_count']} fonts, {result['glyph_count']} glyphs, "
                f"{result['probe_count']} framebuffer/extent probes, "
                f"{result['alias_count']} aliases across "
                f"{result['font_path_count']} font paths"
            )
        else:
            print("X conformance: skipped (pass --external to require it)")
    except (OSError, RenderCheckError, subprocess.CalledProcessError) as error:
        print(f"render check failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
