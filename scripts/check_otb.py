#!/usr/bin/env python3
"""Prove that packaged OTB strikes display every Unicode BDF glyph exactly."""

from __future__ import annotations

import argparse
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import tarfile

try:
    from fontTools.ttLib import TTFont
except ImportError as error:  # pragma: no cover - exercised by release hosts
    raise SystemExit(
        "check_otb.py requires fontTools (for example python3-fonttools)"
    ) from error

class OtbCheckError(AssertionError):
    """A packaged OTB does not preserve its Unicode BDF display geometry."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise OtbCheckError(message)


def parse_bdf(path: Path) -> dict[str, object]:
    """Parse the small BDF subset needed for bitmap/metric comparison."""

    try:
        lines = path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as error:
        raise OtbCheckError(f"cannot read BDF {path}: {error}") from error
    fonts = [line[5:] for line in lines if line.startswith("FONT ")]
    declarations = [line for line in lines if line.startswith("CHARS ")]
    require(len(fonts) == 1 and fonts[0].endswith("-ISO10646-1"), f"{path}: not an ISO10646-1 BDF")
    require(len(declarations) == 1, f"{path}: expected one CHARS declaration")
    try:
        declared = int(declarations[0].split()[1])
    except (IndexError, ValueError) as error:
        raise OtbCheckError(f"{path}: malformed CHARS declaration") from error

    glyphs: dict[int, dict[str, object]] = {}
    index = 0
    while index < len(lines):
        if not lines[index].startswith("STARTCHAR "):
            index += 1
            continue
        start = index
        try:
            end = lines.index("ENDCHAR", start + 1)
        except ValueError as error:
            raise OtbCheckError(f"{path}:{start + 1}: unterminated glyph") from error
        block = lines[start : end + 1]

        def one(prefix: str) -> str:
            values = [line[len(prefix) :] for line in block if line.startswith(prefix)]
            require(len(values) == 1, f"{path}:{start + 1}: expected one {prefix.strip()}")
            return values[0]

        try:
            code = int(one("ENCODING "))
            dwidth = one("DWIDTH ")
            bbx = one("BBX ")
            bitmap_start = block.index("BITMAP") + 1
            bitmap = block[bitmap_start:-1]
            tuple(map(int, dwidth.split()))
            width, height, _, _ = map(int, bbx.split())
            require(len(bitmap) == height, f"{path}:{start + 1}: bitmap height differs")
            require(width >= 0 and height >= 0, f"{path}:{start + 1}: negative BBX")
            for row in bitmap:
                int(row, 16)
        except (IndexError, ValueError) as error:
            raise OtbCheckError(f"{path}:{start + 1}: malformed glyph") from error
        require(code >= 0 and code not in glyphs, f"{path}: duplicate/unencoded glyph {code}")
        glyphs[code] = {"dwidth": dwidth, "bbx": bbx, "bitmap": bitmap}
        index = end + 1
    require(len(glyphs) == declared, f"{path}: CHARS count differs")
    return {"font": fonts[0], "glyphs": glyphs}


def _safe_archive_members(archive: tarfile.TarFile) -> tuple[tarfile.TarInfo, ...]:
    members = tuple(archive.getmembers())
    require(bool(members), f"{archive.name}: empty archive")
    roots: set[str] = set()
    names: set[str] = set()
    for member in members:
        normalized = member.name.rstrip("/")
        require(normalized and normalized not in names, f"{archive.name}: duplicate archive member")
        names.add(normalized)
        path = PurePosixPath(normalized)
        require(
            not path.is_absolute() and ".." not in path.parts,
            f"{archive.name}: unsafe archive path {member.name!r}",
        )
        require(
            normalized == path.as_posix(),
            f"{archive.name}: non-canonical archive path {member.name!r}",
        )
        require(
            member.isdir() or member.isfile(),
            f"{archive.name}: links and special files are not permitted",
        )
        require(bool(path.parts), f"{archive.name}: empty archive path")
        roots.add(path.parts[0])
    require(len(roots) == 1, f"{archive.name}: expected one archive root")
    return members


def _bitmap_metrics(bitmap: object, subtable: object) -> tuple[int, int, int, int, int, object]:
    try:
        metrics = bitmap.metrics
    except AttributeError:
        metrics = subtable.metrics
    if hasattr(metrics, "BearingX"):
        values = (
            metrics.width,
            metrics.height,
            metrics.BearingX,
            metrics.BearingY,
            metrics.Advance,
        )
    else:
        values = (
            metrics.width,
            metrics.height,
            metrics.horiBearingX,
            metrics.horiBearingY,
            metrics.horiAdvance,
        )
    return (*map(int, values), metrics)


def _bdf_ink(glyph: dict[str, object]) -> tuple[int, set[tuple[int, int]]]:
    width, height, x_offset, y_offset = map(int, str(glyph["bbx"]).split())
    advance_fields = tuple(map(int, str(glyph["dwidth"]).split()))
    require(
        len(advance_fields) == 2 and advance_fields[1] == 0,
        "packaged BDF has a non-horizontal advance",
    )
    ink: set[tuple[int, int]] = set()
    for row_number, row_hex in enumerate(glyph["bitmap"]):
        storage_width = len(row_hex) * 4
        row = int(row_hex, 16) >> (storage_width - width)
        for column in range(width):
            if row & (1 << (width - column - 1)):
                ink.add(
                    (
                        x_offset + column,
                        y_offset + height - row_number - 1,
                    )
                )
    return advance_fields[0], ink


def _otb_ink(
    bitmap: object,
    subtable: object,
) -> tuple[int, set[tuple[int, int]]]:
    width, height, bearing_x, bearing_y, advance, metrics = _bitmap_metrics(
        bitmap, subtable
    )
    ink: set[tuple[int, int]] = set()
    for row_number in range(height):
        row_bytes = bitmap.getRow(row_number, 1, metrics=metrics)
        row = int.from_bytes(row_bytes, "big") >> (len(row_bytes) * 8 - width)
        for column in range(width):
            if row & (1 << (width - column - 1)):
                ink.add((bearing_x + column, bearing_y - row_number - 1))
    return advance, ink


def check_pair(bdf_path: Path, otb_path: Path) -> int:
    bdf = parse_bdf(bdf_path)
    font = TTFont(otb_path, lazy=False)
    try:
        require("EBLC" in font and "EBDT" in font, f"{otb_path}: no EBDT strike")
        strikes = font["EBLC"].strikes
        strike_data = font["EBDT"].strikeData
        require(
            len(strikes) == 1 and len(strike_data) == 1,
            f"{otb_path}: expected exactly one embedded bitmap strike",
        )
        size = strikes[0].bitmapSizeTable
        require(size.bitDepth == 1, f"{otb_path}: strike is not one-bit")
        cmap = font.getBestCmap() or {}
        bdf_codes = set(bdf["glyphs"])
        require(
            set(cmap) == bdf_codes,
            f"{otb_path}: Unicode repertoire differs from BDF "
            f"(missing={sorted(bdf_codes - set(cmap))}, "
            f"extra={sorted(set(cmap) - bdf_codes)})",
        )
        name_to_subtable = {
            name: subtable
            for subtable in strikes[0].indexSubTables
            for name in subtable.names
        }
        bitmaps = strike_data[0]
        for code in sorted(bdf_codes):
            name = cmap[code]
            require(name in bitmaps, f"{otb_path}: U+{code:04X} lacks bitmap data")
            require(
                name in name_to_subtable,
                f"{otb_path}: U+{code:04X} lacks an EBLC locator",
            )
            expected_advance, expected_ink = _bdf_ink(bdf["glyphs"][code])
            observed_advance, observed_ink = _otb_ink(
                bitmaps[name], name_to_subtable[name]
            )
            require(
                observed_advance == expected_advance,
                f"{otb_path}: U+{code:04X} advance changed "
                f"from {expected_advance} to {observed_advance}",
            )
            require(
                observed_ink == expected_ink,
                f"{otb_path}: U+{code:04X} baseline-relative ink changed "
                f"(missing={len(expected_ink - observed_ink)}, "
                f"extra={len(observed_ink - expected_ink)})",
            )
        return len(bdf_codes)
    finally:
        font.close()


def check_archive(path: Path) -> dict[str, object]:
    with tarfile.open(path, "r:gz") as archive:
        members = _safe_archive_members(archive)
        files = {member.name: member for member in members if member.isfile()}
        bdfs = sorted(
            name
            for name in files
            if "/fonts/bdf/" in name and name.endswith(".bdf")
        )
        require(bool(bdfs), f"{path}: no packaged Unicode BDFs")
        pair_bytes: list[tuple[str, bytes, bytes]] = []
        for bdf_name in bdfs:
            parts = PurePosixPath(bdf_name).parts
            fonts_index = parts.index("fonts")
            stem = PurePosixPath(bdf_name).stem
            otb_name = str(
                PurePosixPath(*parts[: fonts_index + 1])
                / "otb"
                / f"{stem}.otb"
            )
            require(otb_name in files, f"{path}: missing {otb_name}")
            bdf_stream = archive.extractfile(files[bdf_name])
            otb_stream = archive.extractfile(files[otb_name])
            require(
                bdf_stream is not None and otb_stream is not None,
                f"{path}: cannot read packaged font pair",
            )
            pair_bytes.append((bdf_name, bdf_stream.read(), otb_stream.read()))

    import tempfile

    glyph_count = 0
    with tempfile.TemporaryDirectory(prefix="genera-fonts-otb-check-") as directory:
        temporary = Path(directory)
        for index, (name, bdf_data, otb_data) in enumerate(pair_bytes):
            bdf_path = temporary / f"{index}.bdf"
            otb_path = temporary / f"{index}.otb"
            bdf_path.write_bytes(bdf_data)
            otb_path.write_bytes(otb_data)
            try:
                glyph_count += check_pair(bdf_path, otb_path)
            except OtbCheckError as error:
                raise OtbCheckError(f"{path}:{name}: {error}") from error
    return {
        "archive": str(path),
        "font_count": len(pair_bytes),
        "glyph_count": glyph_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        results = [check_archive(path.resolve()) for path in args.archives]
    except (OSError, OtbCheckError, tarfile.TarError) as error:
        parser.error(str(error))
    print(json.dumps({"status": "ok", "archives": results}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
