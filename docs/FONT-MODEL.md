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

## Physical strikes and logical font identities

A resident `FONT` object name is the identity of one physical raster strike.
It is not necessarily a typographic family. The saved world's `FONT-SPECS`
relations independently map device, character set, family, face, and logical
size to those objects. The reviewed mapping is committed as
[`config/font-identities.json`](../config/font-identities.json), closed over
all 89 objects, and copied into every distribution and release.

The primary mappings make the physical/logical split concrete:

| Resident strike | Primary logical identity | Resident line height |
| --- | --- | ---: |
| `HL8` | `SWISS/ROMAN/VERY-SMALL` (nominal X size 8) | 11 px |
| `HL8B` | `SWISS/BOLD/VERY-SMALL` (nominal X size 8) | 11 px |
| `HL8I` | `SWISS/ITALIC/VERY-SMALL` (nominal X size 8) | 11 px |
| `HL14` | `SWISS/ROMAN/LARGE` (nominal X size 14) | 15 px |
| `SWISS20` | `SWISS/ROMAN/VERY-LARGE` (nominal X size 18) | 20 px |

`FIX`, `SWISS`, `DUTCH`, `JESS`, and `EUREX` are the ordinary logical
families. The mapping also preserves the primary Genera faces, including
Bold, Italic, Bold Italic, Condensed, Extra Condensed, Bold Extended, and caps
variants. Where one strike serves fallback selectors as well, the first
resident mapping is the primary desktop identity; for example, `HL14` remains
Roman even though Genera can also use it for a missing Condensed strike.

Ten specialty objects have reviewed character-set or application-style roles.
They receive isolated desktop families so a mouse, arrow, mathematical, Greek,
or editor-symbol repertoire cannot satisfy an ordinary `Fix` or `Swiss` text
request. `40VR`, `EINY8`, and `VT100` have no resident `FONT-SPECS` mapping.
They remain explicitly unmapped and use neutral object-specific desktop
identities; that fallback is packaging metadata, not a historical style claim.

Logical named size is not substituted for physical raster size. `PIXEL_SIZE`,
BDF `SIZE`, and the 72 dpi interchange `POINT_SIZE` continue to follow the
measured `character_height`. `nominal_x_size` in the logical-identity record
preserves the X display device's 6/8/10/12/14/18/24 crosswalk separately.
That crosswalk is device-specific rather than a universal Genera point-size
law; MacIvory has different normal and small-screen QuickDraw mappings.

Desktop metadata must also keep distinct physical objects addressable. The
`TR8BI` and `TR10BI` objects have different logical named sizes and glyph
bitmaps but the same measured 11-pixel line height. Without another field they
collapse to one Fontconfig identity, so their desktop styles carry explicit `Very Small
Strike` and `Small Strike` qualifiers. A closed uniqueness check rejects any
other family/style/pixel collision.

## Raw BDF profile

The raw BDFs retain the original Genera character-code numbers and use
`Misc-FontSpecific`. Their `DWIDTH`, `BBX`, ascent, and descent express the
resident advance, cropped raster placement, baseline, and line height.

The XLFD uses 72 dpi as an explicit interchange convention so one recovered
pixel corresponds to one interchange point. This is not a claim about the
physical resolution of a Lisp Machine display or the logical point-size name.
Family, weight, slant, and set width come only from the closed reviewed mapping,
never from parsing resident-name suffixes. XLFD `P`, `M`, or `C` spacing is
still computed from represented advances and raster boxes.

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

The raw and Unicode BDFs share the same logical family/style metadata. Their
charset fields and alias namespaces distinguish the addressing profiles.
User-facing OTBs therefore expose ordinary desktop styles such as Regular,
Bold, Italic, Bold Italic, Condensed, and Expanded instead of treating every
physical strike as a separate family.

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
