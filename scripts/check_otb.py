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


def _expected_style(typographic: dict[str, object]) -> str:
    parts: list[str] = []
    add_style = typographic.get("add_style_name")
    if add_style:
        parts.append(str(add_style).replace(" ", "-"))
    if typographic.get("weight_name") == "Bold":
        parts.append("Bold")
    if typographic.get("slant") == "I":
        parts.append("Italic")
    setwidth = typographic.get("setwidth_name")
    if setwidth != "Normal":
        parts.append(str(setwidth))
    return " ".join(parts) or "Regular"


def _check_identity(
    font: TTFont, otb_path: Path, logical_identity: dict[str, object]
) -> None:
    typographic = logical_identity.get("typographic")
    require(isinstance(typographic, dict), f"{otb_path}: logical typography is missing")
    family = typographic.get("family_name")
    require(isinstance(family, str) and family, f"{otb_path}: logical family is missing")
    style = _expected_style(typographic)
    name_table = font["name"]
    require(name_table.getDebugName(1) == family, f"{otb_path}: OTB family name differs")
    require(name_table.getDebugName(2) == style, f"{otb_path}: OTB style name differs")
    expected_full_name = family if style == "Regular" else f"{family} {style}"
    require(
        name_table.getDebugName(4) == expected_full_name,
        f"{otb_path}: OTB full name differs",
    )

    os2 = font["OS/2"]
    expected_weight = {"Medium": 400, "Bold": 700}.get(
        typographic.get("weight_name")
    )
    expected_width = {"Condensed": 3, "Normal": 5, "Expanded": 7}.get(
        typographic.get("setwidth_name")
    )
    require(expected_weight is not None, f"{otb_path}: unsupported expected weight")
    require(expected_width is not None, f"{otb_path}: unsupported expected width")
    require(os2.usWeightClass == expected_weight, f"{otb_path}: OS/2 weight differs")
    require(os2.usWidthClass == expected_width, f"{otb_path}: OS/2 width differs")
    selection = int(os2.fsSelection)
    italic = typographic.get("slant") == "I"
    bold = typographic.get("weight_name") == "Bold"
    require(bool(selection & 0x01) == italic, f"{otb_path}: OS/2 italic bit differs")
    require(bool(selection & 0x20) == bold, f"{otb_path}: OS/2 bold bit differs")
    require(
        bool(selection & 0x40) == (not italic and not bold),
        f"{otb_path}: OS/2 regular bit differs",
    )


def check_pair(
    bdf_path: Path,
    otb_path: Path,
    logical_identity: dict[str, object],
) -> int:
    bdf = parse_bdf(bdf_path)
    font = TTFont(otb_path, lazy=False)
    try:
        require("name" in font and "OS/2" in font, f"{otb_path}: identity tables missing")
        _check_identity(font, otb_path, logical_identity)
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
        manifests = [name for name in files if name.endswith("/RELEASE-MANIFEST.json")]
        require(len(manifests) == 1, f"{path}: expected one release manifest")
        manifest_stream = archive.extractfile(files[manifests[0]])
        require(manifest_stream is not None, f"{path}: cannot read release manifest")
        manifest = json.loads(manifest_stream.read())
        artifacts = manifest.get("artifacts")
        require(isinstance(artifacts, list), f"{path}: release artifacts are missing")
        identities_by_stem: dict[str, dict[str, object]] = {}
        for artifact in artifacts:
            require(isinstance(artifact, dict), f"{path}: malformed release artifact")
            artifact_files = artifact.get("files")
            identity = artifact.get("logical_identity")
            require(isinstance(artifact_files, dict), f"{path}: artifact files are missing")
            require(isinstance(identity, dict), f"{path}: artifact logical identity is missing")
            bdf_record = artifact_files.get("bdf")
            require(isinstance(bdf_record, dict), f"{path}: artifact BDF record is missing")
            relative = bdf_record.get("path")
            require(isinstance(relative, str), f"{path}: artifact BDF path is missing")
            stem = PurePosixPath(relative).stem
            require(stem not in identities_by_stem, f"{path}: duplicate artifact stem")
            identities_by_stem[stem] = identity

        pair_bytes: list[tuple[str, bytes, bytes, dict[str, object]]] = []
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
            require(stem in identities_by_stem, f"{path}: {stem} lacks logical identity")
            bdf_stream = archive.extractfile(files[bdf_name])
            otb_stream = archive.extractfile(files[otb_name])
            require(
                bdf_stream is not None and otb_stream is not None,
                f"{path}: cannot read packaged font pair",
            )
            pair_bytes.append(
                (
                    bdf_name,
                    bdf_stream.read(),
                    otb_stream.read(),
                    identities_by_stem[stem],
                )
            )
        require(
            set(identities_by_stem) == {PurePosixPath(name).stem for name in bdfs},
            f"{path}: logical identities are not closed over packaged BDFs",
        )

    import tempfile

    glyph_count = 0
    with tempfile.TemporaryDirectory(prefix="genera-fonts-otb-check-") as directory:
        temporary = Path(directory)
        for index, (name, bdf_data, otb_data, logical_identity) in enumerate(pair_bytes):
            bdf_path = temporary / f"{index}.bdf"
            otb_path = temporary / f"{index}.otb"
            bdf_path.write_bytes(bdf_data)
            otb_path.write_bytes(otb_data)
            try:
                glyph_count += check_pair(bdf_path, otb_path, logical_identity)
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
