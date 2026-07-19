# Font model and conversion

## Profile identity

This project preserves one profile: the complete set of distinct one-bit
`FONT` objects resident in the pinned Genera 8.5 base world. The decoder finds
89 objects and 90 symbol bindings because `TV:*DEFAULT-FONT*` and
`FONTS:CPTFONT` point to the same object. Aliases do not create a second font.

This is a runtime display profile. It answers “what glyph pixels and metrics
does this saved world contain?” It does not answer “what were all of the
authoring files distributed with Genera?” or “what fonts can any Genera 8.5
site load?” The release's separate `sys.sct` tree contains a larger font-file
inventory, discussed in [PROVENANCE.md](PROVENANCE.md), but it is outside the
selected payload.

## Resident `FONT` representation

Each selected object is a named, rank-two, one-bit Lisp array whose named
structure is `FONT`. The normalized source record retains:

- the resident font name and object identity evidence;
- character height, raster height, baseline, default width, and blinker
  dimensions;
- whether per-character widths are present;
- every represented raw character code and existence flag;
- advance, left kern, raster width, and raster height for each slot; and
- every one-bit raster row in hexadecimal.

The decoder understands Genera's array leader, packed raster, existence,
width, indexing, and left-kern tables. It also examines the historical
non-spacing slots retained above an apparent fill pointer. Iterating only to
that fill pointer would lose real glyphs; `CPTFONT`, for example, has 14 such
high slots.

The 89 source records contain 11,659 slots. Of those, 10,836 have the resident
existence flag and become glyphs. Seventy-one fonts carry variable character
widths.

## Display geometry

For a glyph, the resident values have these display meanings:

- `advance` is the horizontal cursor movement after drawing;
- `left_kern` is a signed per-glyph bearing, not pair kerning;
- the initial horizontal drawing position is `cursor_x - left_kern`;
- `baseline` locates the text baseline within the font's line metrics; and
- raster rows are one-bit pixels, with no antialiasing or color plane.

There is no pair-kerning table in this model. A negative or positive bearing
does not imply a pair-specific adjustment.

The BDF exporter crops transparent raster margins to a tight represented box
while translating its x and y offsets so the same pixels remain at the same
baseline-relative display coordinates. Cropping is a storage transformation,
not a change in geometry. A resident slot that exists but has no set pixel is
represented by a one-pixel transparent bitmap carrying the original advance
and bearing; it is not silently deleted or made visible.

No stage traces outlines, smooths pixels, applies hinting, changes scale, or
generates absent characters. OTB files contain a one-bit embedded bitmap
strike derived from the Unicode BDF; they are not outline conversions.

## Raw BDF profile

The raw BDFs retain the original Genera character-code numbers and use
`Misc-FontSpecific`. Their `DWIDTH`, `BBX`, ascent, and descent express the
resident advance, cropped raster placement, baseline, and line height.

The XLFD uses 72 dpi as an explicit interchange convention so one recovered
pixel corresponds to one nominal point. This is not a claim about the physical
resolution of a Lisp Machine display. The historical objects do not provide a
complete modern XLFD weight, slant, or set-width classification, so those
fields remain `Unknown`/`OT`/`Unknown`; the build does not infer typography
from font names. XLFD `P`, `M`, or `C` spacing is computed from represented
advances and raster boxes.

Every font has two raw aliases:

- `genera-runtime-<name>` makes the resident profile explicit; and
- `genera-<name>` is its convenience alias.

The full XLFD name is authoritative. These aliases are for X core fonts, not
Fontconfig family names.

## Unicode derivative

The Unicode derivative changes each glyph's address according to the closed
mapping contract in [UNICODE.md](UNICODE.md). The build checks the geometry
again without character codes and rejects any change to bitmap dimensions,
rows, advance, x offset, y offset, baseline, raster height, or line height.

Unicode BDFs use `ISO10646-1` and parallel aliases:

- `genera-unicode-runtime-<name>`; and
- `genera-unicode-<name>`.

Raw and Unicode alias namespaces are disjoint. A raw code must not be treated
as a Unicode scalar merely because the numbers happen to match.

## Independent reference comparisons

The resident object is authoritative for this profile. Matching local files
from the same licensed release are evidence, not replacement sources.

| Reference | Exact matches | Reviewed difference boundary |
| --- | ---: | --- |
| Binary BFD | 88 of 89 | `40VR` has seven codes in the supplied BFD that are absent from the resident object; shared glyphs match. |
| X BDF | 82 of 89 | Five fonts differ only in represented codes; `EUREX21I` and `TVFONTI` also have shared-glyph differences. |

The five code-set-only BDF cases are `40VR`, `CPTFONTCC`, `CPTFONTI`,
`MOUSE`, and `TVFONT`. The substantive differences are retained as evidence;
the build does not silently substitute supplied BDF pixels for the saved
world's pixels.

## Claims deliberately not made

This model does not claim to reconstruct byte-identical BFD/BDF files, their
comments, complete source-path or authoring metadata, discarded authoring
properties, vector outlines, PostScript device fonts, downloadable Bitstream
outline libraries, or every font distributed with Genera. Bounded reference
basenames and cryptographic comparison facts are retained as evidence; the
payload itself preserves the pixels and display metrics of the resident raster
profile.
