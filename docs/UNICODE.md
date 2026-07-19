# Unicode mapping and Private Use Area registry

## Contract

`symbolics-genera-8.5-resident-unicode-v1` is a derivative addressing scheme
for the 89-font resident profile. The raw Genera character codes and recovered
display geometry remain authoritative. Unicode derivation changes addresses
and font identity only; it does not change pixels, advances, bearings,
baselines, or line metrics.

The project follows three rules:

1. Use a standard Unicode scalar only where the Genera standard-character-set
   names, matching release BDF names, and resident `FONT-SPECS` mappings supply
   adequate identity evidence.
2. Never identify a specialty glyph from visual resemblance alone.
3. Route a whole specialty repertoire to a stable project PUA block when no
   authoritative per-character standard mapping is established.

The resulting Unicode BDFs are useful interchange derivatives. They do not
replace the raw `Misc-FontSpecific` records as historical evidence.

## Standard Genera repertoire

Seventy-eight fonts use the reviewed standard repertoire. Raw codes 040–176
octal map identically to U+0020–U+007E. The low 000–037 octal positions and
177 map as follows:

| Raw | Unicode | Name |
| ---: | ---: | --- |
| `000` | U+22C5 | DOT OPERATOR |
| `001` | U+2193 | DOWNWARDS ARROW |
| `002` | U+03B1 | GREEK SMALL LETTER ALPHA |
| `003` | U+03B2 | GREEK SMALL LETTER BETA |
| `004` | U+2227 | LOGICAL AND |
| `005` | U+00AC | NOT SIGN |
| `006` | U+03B5 | GREEK SMALL LETTER EPSILON |
| `007` | U+03C0 | GREEK SMALL LETTER PI |
| `010` | U+03BB | GREEK SMALL LETTER LAMDA |
| `011` | U+03B3 | GREEK SMALL LETTER GAMMA |
| `012` | U+03B4 | GREEK SMALL LETTER DELTA |
| `013` | U+2191 | UPWARDS ARROW |
| `014` | U+00B1 | PLUS-MINUS SIGN |
| `015` | U+2295 | CIRCLED PLUS |
| `016` | U+221E | INFINITY |
| `017` | U+2202 | PARTIAL DIFFERENTIAL |
| `020` | U+2282 | SUBSET OF |
| `021` | U+2283 | SUPERSET OF |
| `022` | U+2229 | INTERSECTION |
| `023` | U+222A | UNION |
| `024` | U+2200 | FOR ALL |
| `025` | U+2203 | THERE EXISTS |
| `026` | U+2297 | CIRCLED TIMES |
| `027` | U+2194 | LEFT RIGHT ARROW |
| `030` | U+2190 | LEFTWARDS ARROW |
| `031` | U+2192 | RIGHTWARDS ARROW |
| `032` | U+2260 | NOT EQUAL TO |
| `033` | U+25CA | LOZENGE |
| `034` | U+2264 | LESS-THAN OR EQUAL TO |
| `035` | U+2265 | GREATER-THAN OR EQUAL TO |
| `036` | U+2261 | IDENTICAL TO |
| `037` | U+2228 | LOGICAL OR |
| `177` | U+222B | INTEGRAL |

The extended standard combining slots map decimal raw codes 193–200 and
202–207 to U+0300, U+0301, U+0302, U+0303, U+0304, U+0306, U+0307, U+0308,
U+030A, U+0327, U+0332, U+030B, U+0328, and U+030C respectively. Raw code 201
has no assignment in version 1.

An unassigned code is a build error; the generator does not fall back to a
same-number scalar.

## PUA continuity with CADR Fonts

CADR Fonts Unicode Mapping version 1 reserves 28 aligned 128-scalar blocks
from U+E000 through U+EDFF. Genera Fonts starts at **U+EE00**, the next aligned
block, and currently reserves eight blocks through U+F1FF. This coordination
keeps the two sibling projects' version 1 mappings disjoint.

For a Genera PUA repertoire with block start `S` and a raw code `r` in the
range 0–127, the mapping is:

```text
Unicode scalar = S + r
```

The raw numerical offset is preserved. For example, `BOXFONT` raw octal `001`
maps to U+EE01. A PUA assignment never synthesizes a glyph for a source slot
that is absent.

| Repertoire | Fonts | Reserved range |
| --- | --- | ---: |
| `boxfont` | `BOXFONT` | U+EE00–U+EE7F |
| `editor-symbols` | `EDSYMBOL12` | U+EE80–U+EEFF |
| `greek` | `HIPPO10`, `HIPPO12` | U+EF00–U+EF7F |
| `math` | `MATH10`, `MATH12` | U+EF80–U+EFFF |
| `mouse` | `MOUSE` | U+F000–U+F07F |
| `arrow` | `NARROW` | U+F080–U+F0FF |
| `symbol` | `SYMBOL10`, `SYMBOL12` | U+F100–U+F17F |
| `vt100` | `VT100` | U+F180–U+F1FF |

The `greek`, `math`, and `symbol` labels describe Genera repertoire roles;
they do not assert one-to-one identity with modern Unicode Greek,
mathematical, or symbol characters. `MOUSE` contains pointer graphics,
`NARROW` is the resident arrow character-set font, `EDSYMBOL12` is the editor
symbol repertoire, and `VT100` supplies alternate terminal graphics. A whole
PUA block avoids silently turning suggestive names or shapes into standard
characters.

## Counts and geometry invariant

The current Unicode profile contains the same 10,836 glyph instances as the
raw profile:

| Address class | Glyph instances |
| --- | ---: |
| Standard Unicode | 9,985 |
| Project PUA | 851 |
| Total | 10,836 |

The reviewed raw geometry inventory digest is
`f7c43ae21c7596f77c5f046d86b2e88a713cca67680818dc719c21991e0dbea9`.
The Unicode geometry inventory, including new code points, is
`c87ed9e221a6cfed824ccfdd26388ae7d22e2b3040a83e15aaf84dfe84f0e8a0`.
The build also compares every raw/Unicode glyph with addresses removed, so a
coordinated generator and digest update cannot turn remapping into an
unnoticed geometry change.

## Interoperability and versioning

PUA scalars are not assigned semantics by the Unicode Consortium. Text using
U+EE00–U+F1FF is interoperable only when its recipient also uses this mapping
version and a corresponding Genera font. Another private convention may use
the same scalars differently.

Mapping version 1 is append-stable:

- a published repertoire block or raw-code offset must not be moved, reused,
  or silently reinterpreted;
- variants within the same reviewed repertoire share the same block and raw
  offset even when their pixels differ;
- newly established semantics may be expressed only through an explicit,
  reviewed mapping revision; and
- an incompatible reassignment requires a new mapping identifier, not an
  in-place edit to version 1.

The normative machine-readable source is
[`config/unicode-mapping.json`](../config/unicode-mapping.json). Every build
copies it to `dist/unicode/UNICODE-MAPPING.json` and records its digest in the
build and release manifests.

## Generated use

Unicode BDFs use `ISO10646-1`; raw BDFs remain `Misc-FontSpecific`. To expose
only the Unicode derivative to an X core server:

```sh
xset +fp "$PWD/dist/unicode/bdf"
xset fp rehash
xlsfonts 'genera-unicode-*'
```

Release OTBs are generated only from these Unicode BDFs. The release checker
requires the same encoded repertoire, advance, and baseline-relative one-bit
pixels for every BDF-derived glyph. The unencoded `.notdef` maintained by the
container format is outside that equivalence claim.
