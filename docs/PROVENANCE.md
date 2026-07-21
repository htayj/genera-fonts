# Provenance and corpus boundary

## Source artifact

The sole historical shape source is the repository owner's licensed local
copy of this exact artifact:

| Field | Value |
| --- | --- |
| Filename | `Genera-8-5.vlod` |
| Size | 54,804,480 bytes |
| SHA-256 | `a8ee5e86cc7e322f7385af3e0cd579d7650d4dcfc3ce328acbf8b25515dd0672` |
| Format | little-endian VLM VLOD V2 |

The VLOD is never added to this repository or to a release. The committed
`sources/genera-8.5/` tree is a narrow font-only import containing normalized
resident `FONT` records, a catalog, a validation report, and a closed source
manifest.

## Decoder lineage and execution boundary

The importer loads the reviewed decoder from the sibling
[`htayj/lisp-machine-container-museum`](https://github.com/htayj/lisp-machine-container-museum)
repository at commit
[`2602eab2ef1bea4800312f71f9185e9261c6fa6c`](https://github.com/htayj/lisp-machine-container-museum/tree/2602eab2ef1bea4800312f71f9185e9261c6fa6c).
The source manifest pins both participating files:

| File | SHA-256 |
| --- | --- |
| `scripts/extract-genera-fonts.py` | `f242612ca77d94099eb2c1aab0837cc4673c51fdd04f926811229c5f1503c334` |
| `scripts/lisp_machine_fonts.py` | `8a2218279a1910db86a42485d3af3baf2ba836088197c9b2427b8476ceef7ecf` |

The importer rejects a different VLOD size or digest and rejects changed
decoder bytes before traversal. The decoder uses a read-only memory map and
inert tagged-object traversal. It does not boot the world, evaluate Lisp,
invoke a target function, load executable objects, or modify the VLOD.

The upstream preservation record describes the decoder and its evidence in
[*Extracting resident fonts from a Genera world*](https://github.com/htayj/lisp-machine-container-museum/blob/2602eab2ef1bea4800312f71f9185e9261c6fa6c/docs/genera/extracting-resident-fonts.md),
the bounded inventory in the [Genera 8.5 resident font
catalog](https://github.com/htayj/lisp-machine-container-museum/blob/2602eab2ef1bea4800312f71f9185e9261c6fa6c/docs/genera/font-catalog.md),
and the world-vs-file distinction in [*Recovering code and assets from a Genera
world*](https://github.com/htayj/lisp-machine-container-museum/blob/2602eab2ef1bea4800312f71f9185e9261c6fa6c/docs/genera/recovering-code-and-assets-from-worlds.md).

## Closed imported corpus

The reviewed import contains:

| Invariant | Value |
| --- | ---: |
| Distinct resident `FONT` objects | 89 |
| Symbol bindings | 90 |
| Represented slots | 11,659 |
| Defined glyphs | 10,836 |
| Variable-width fonts | 71 |
| Normalized geometry SHA-256 | `35dc71287724e5976016ec934530d131042d25518d14e2e8cd40a96ff590eac5` |

`SOURCE-MANIFEST.json` records every imported file's relative path, byte size,
and SHA-256. It also pins the counts and semantic geometry digest above. The
normal build rejects added, removed, renamed, changed, duplicate, unsafe, or
unmanifested source paths before producing a font.

`make check-import` performs the stronger licensed-media check: it regenerates
the entire import into a temporary directory and byte-compares it with the
committed corpus. This command requires the licensed local input. Public
builders use the committed closed corpus and do not need or receive that
input.

## Logical font identity evidence

The physical resident-object name is not necessarily the logical typeface
name. Genera's resident `FONT-SPECS` data identifies families, faces, named
sizes, and character-set roles independently of the selected bitmap strike.
For example, `HL8`, `HL8B`, `HL8I`, and `HL8BI` are respectively the Roman,
Bold, Italic, and Bold Italic faces of logical family `SWISS` at
`VERY-SMALL`; `HL14` is the same `SWISS` Roman family at `LARGE`.

[`config/font-identities.json`](../config/font-identities.json) is the reviewed
machine-readable interpretation of that evidence. It is pinned to the source
VLOD digest and closed over all 89 resident objects. It records 79 primary
family/face/size selectors, seven specialty character-set roles without a
fabricated primary text selector, and three objects (`40VR`, `EINY8`, and
`VT100`) for which no defensible `FONT-SPECS` tuple was established. The
specialty and unmapped objects receive isolated desktop family names rather
than being allowed to match an ordinary text family.

The mapping is descriptive metadata, not copied font source. Resident object
names remain the stable artifact and filename identity. The symbolic named
size comes from resident `FONT-SPECS`; the separate 6/8/10/12/14/18/24 values
come from the matching X display-device crosswalk in
`sys.sct/x11/screen/x-display-device.lisp.~9~`, not from a universal Genera
point-size rule. BDF and OTB pixel size continues to come from each recovered
object's measured line height. The build copies the mapping into
`dist/FONT-IDENTITIES.json`, records its digest in generated manifests, and
verifies both BDF and OTB family/style metadata against it.

## 89 resident objects are not 224 distributed BDFs

The matching licensed `sys.sct` tree contains 275 BFD files totaling 2,605,322
bytes and 224 plain-text X BDF files totaling 3,076,642 bytes. That is a
distribution-file inventory, not the selected profile. It can contain files
that are not resident in the saved base world, historical versions, or another
representation of a resident font.

This repository publishes exactly the 89 distinct resident objects. It does
not claim that those objects are all fonts shipped with Genera, and it does not
copy or publish the 224 supplied BDFs or 275 supplied BFDs. The local supplied
files are used only to compare a resident name's metrics and pixels:

- 88 of 89 matching BFD comparisons are exact; `40VR` differs only by seven
  additional BFD codes, with every shared glyph matching;
- 82 of 89 matching BDF comparisons are exact;
- `40VR`, `CPTFONTCC`, `CPTFONTI`, `MOUSE`, and `TVFONT` differ only in their
  represented code sets; and
- `EUREX21I` and `TVFONTI` have substantive shared-glyph BDF differences.

Those results make the boundary observable. The VLOD object remains the
authority, and a disagreement is recorded rather than replaced with the
licensed file's contents.

## Publication allowlist

The importer and build permit only:

- the one-bit glyph rows of the selected resident fonts;
- per-font and per-glyph display metrics needed to render those glyphs;
- font names, bounded resident-object identifiers, counts, validation facts,
  and cryptographic provenance; and
- mechanical JSON, BDF, OTB, index, checksum, and specimen derivatives of
  that font model.

They do not copy or publish:

- the VLOD itself or any non-font heap object;
- Genera Lisp source, compiled functions, executable code, or system data;
- original BFD/BDF files supplied in `sys.sct`;
- comments, authoring metadata, or source-path information beyond the bounded
  reference basenames, hashes, sizes, and comparison facts recorded for local
  BDF/BFD validation; manuals, documentation, on-line help, or standalone
  pictures, icons, and other raster assets outside the selected resident
  `FONT` arrays; or
- any broader Open Genera distribution payload.

The source manifest states the same boundary in machine-readable form. See
[NOTICE.md](../NOTICE.md) for the separate U.S. publication basis and the
limits of that position.

## Re-import procedure

The default paths assume this repository and `genera-emu` are siblings:

```sh
make import
make check-import
```

The underlying command accepts explicit locations when a maintainer needs
them:

```sh
python3 scripts/import_genera_fonts.py \
  --genera-emu ../genera-emu \
  --vlod ../genera-emu/.lm-home/opengenera/runtime/Genera-8-5.vlod \
  --output sources/genera-8.5 \
  --clean
```

Review the new `SOURCE-MANIFEST.json`, catalog, validation report, full font
name closure, counts, and geometry digest before accepting any change. A new
world digest or decoder revision defines a new provenance event and must not
be described as byte-identical reproduction of this profile.
