# Releasing

## Release boundary

A release is derived from the committed, manifest-closed 89-font source
profile. It does not require or contain the licensed VLOD or supplied Genera
font files. Re-importing from licensed media is a separate maintainer audit;
normal public builders start from `sources/genera-8.5/`.

The public upstream repository is
[`htayj/genera-fonts`](https://github.com/htayj/genera-fonts), its `origin`
uses SSH, and its default branch is `main`.

The release partition is computed from emitted Unicode content:

- **latin** contains an artifact when it has a visible glyph in Basic Latin
  U+0041–U+005A or U+0061–U+007A; and
- **symbols** is the exact complement.

Filename, family name, PUA label, and specimen kind do not select membership.
The current partition is complete and disjoint: 78 Latin fonts and 11 symbols
fonts, 89 total.

## Generic archives

For version `v0.1.1`, the build writes two deterministic archives and adjacent
checksums under `dist/release/`:

```text
Genera-fonts-latin-v0.1.1.tar.gz
Genera-fonts-latin-v0.1.1.tar.gz.sha256
Genera-fonts-symbols-v0.1.1.tar.gz
Genera-fonts-symbols-v0.1.1.tar.gz.sha256
```

Each archive has one versioned root and carries the selected Unicode
`ISO10646-1` BDFs, OTB conversions made only from those BDFs, closed font
indexes, the mapping/source/release manifests, checksums, the root project
license, and the publication notice:

```text
Genera-fonts-<group>-<version>/
  README.release.md
  LICENSE
  NOTICE.md
  RELEASE-MANIFEST.json
  SHA256SUMS
  fonts/
    bdf/                  selected Unicode BDFs plus fonts.dir/fonts.alias
    otb/                  one-bit OTB conversions of those BDFs
  metadata/
    BUILD-MANIFEST.json
    FONT-IDENTITIES.json
    SOURCE-MANIFEST.json
    UNICODE-MAPPING.json
```

Unicode BDFs and OTBs are the usable fonts. The archives exclude the VLOD,
the imported `sources/genera-8.5/` JSON records, raw-code BDFs, raw sheets,
raw specimens, and every other raw or non-font input. The source manifest is
included as provenance metadata only; it contains hashes and facts, not the
manifested imported payload.

OTB is a one-bit embedded-bitmap SFNT container, not a vector or outline
conversion. After `fonttosfnt` conversion, the builder normalizes per-glyph
`hmtx` advances from the native strike (including zero combining advances),
zeroes contourless `hmtx` bearings, and recomputes `hhea`, OS/2 average-width,
and `post.isFixedPitch` summaries. Zero-area bitmap runs are stored as explicit
metric-only records so blank glyphs remain loadable. None of this changes
source pixels, bitmap bearings, or baseline geometry.

The OTB checker requires valid bitmap record bounds and horizontal table
structure, consistent scaled advances and summaries, the exact Unicode
repertoire, and baseline-relative set pixels of the BDF for every encoded
glyph. It also requires the logical family and style names plus OS/2 weight,
width, and bold/italic selection bits recorded by `FONT-IDENTITIES.json`. A
converter may trim transparent storage rows or columns only when displayed
geometry is unchanged. The Unicode BDF remains the authoritative derivative
record. See [FONT-MODEL.md](FONT-MODEL.md) for normalization and spacing rules.

## Required local gates

From the intended release commit, use the tag spelling as the version and the
commit timestamp as the deterministic archive epoch:

```sh
version=v0.1.1
export SOURCE_DATE_EPOCH=$(git show -s --format=%ct HEAD)

make ci VERSION="$version" SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH"
make release VERSION="$version" SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH"
make check-release VERSION="$version" SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH"
make release-reproducible VERSION="$version" SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH"
```

`make ci` combines the internal tests, closed corpus checks, external BDF/X
checks, deterministic build, specimen closure, release build, release checks,
and release reproducibility. Running the explicit release targets afterward
makes the final artifacts and their checks visible at the handoff boundary.
`make check-release` includes mandatory OTB structure/geometry validation and
Fontconfig consumer recognition: it stages the archives in a temporary tree,
queries every OTB with `fc-query`, and checks exact family/style/weight/slant/
width/pixelsize selection and spacing with isolated `fc-match`. Both archive
groups participate in matching; no system installation or global font cache
update is needed.

Release construction needs Python 3.10 or newer, Python FontTools, and exactly
`fonttosfnt 1.2.5`. Older converter releases change horizontal and bitmap-
strike metrics, so the builder rejects them. The hosted workflow builds 1.2.5
from the checksum-pinned X.Org source archive. Release consumer checks require
Bash, coreutils (including `sha256sum`), and Fontconfig's `fc-query` and
`fc-match`. The full external X gate additionally needs `bdftopcf`, `mkfontdir`,
Xvfb, and Xlib.

For an optional real FreeType all-glyph load and pixel comparison, install the
Python `freetype-py` binding and native FreeType shared library, then run:

```sh
python3 scripts/check_otb.py --freetype \
  "dist/release/Genera-fonts-latin-$version.tar.gz" \
  "dist/release/Genera-fonts-symbols-$version.tar.gz"
```

This supplements, rather than replaces, the mandatory FontTools gate and is
not included in `make ci` or `make check-release`.

## Optional native packages

The checked generic archives are also the input to four native package
builders:

```sh
make package-deb VERSION="$version" SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH"
make package-rpm VERSION="$version" SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH"
make package-arch VERSION="$version" SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH"
make package-void VERSION="$version" SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH"

# Build all formats
make packages VERSION="$version" SOURCE_DATE_EPOCH="$SOURCE_DATE_EPOCH"
```

The corresponding native tools are `dpkg-deb`, `rpmbuild`, `makepkg`, and
`xbps-create`; the Void builder can use its configured container fallback.
Every platform receives two co-installable packages,
`genera-fonts-latin` and `genera-fonts-symbols`. Their common installation
boundary is:

```text
/usr/share/fonts/genera-fonts/<group>/bdf/
/usr/share/fonts/genera-fonts/<group>/otb/
/usr/share/genera-fonts/<group>/
/usr/share/doc/genera-fonts-<group>/
```

A package may install
`75-genera-fonts-<group>.conf` to expose its OTB directory to Fontconfig. Raw
Genera-code BDFs and imported JSON records are never installed. The current
payload counts are 78 Unicode BDFs plus 78 OTBs for `latin`, and 11 plus 11
for `symbols`; these are reviewed results of the content predicate, not
hard-coded format limits.

## Updating the AUR package

The AUR package base `genera-fonts` is a separate, source-only Git repository.
Update it only after the corresponding GitHub tag and release are immutable:

1. bump `pkgver` and reset `pkgrel` in
   `packaging/aur/genera-fonts/PKGBUILD`;
2. replace the tag object, tagged commit, and release timestamp with the exact
   reviewed identities;
3. regenerate the pinned source hash with `makepkg --geninteg`, then regenerate
   `.SRCINFO` with `makepkg --printsrcinfo`;
4. run `pkgctl license check`, `namcap`, a clean Arch build, co-installation,
   release validation, and Fontconfig discovery; and
5. copy only `PKGBUILD`, `.SRCINFO`, `.gitignore`, `LICENSE`,
   `LICENSES/0BSD.txt`, and `REUSE.toml` to the AUR `master` branch, commit a
   meaningful version update, and push.

Do not upload native packages, file lists, generated font trees, or GitHub
release assets to the AUR repository. Confirm the public Git author identity
before pushing because AUR commit history is effectively permanent.

## What the gates prove

A release is acceptable only when the checks establish:

- exact closure over the 89 committed font records and their source manifest;
- exact closure over the 86 reviewed logical/role mappings and three explicit
  unmapped resident objects;
- 11,659 source slots, 10,836 defined raw glyphs, and unchanged reviewed
  semantic geometry digests;
- byte-for-byte preservation of display geometry during Unicode derivation;
- exactly 9,985 standard-scalar and 851 PUA glyph instances;
- the fixed eight-block U+EE00–U+F1FF Genera PUA registry, after CADR's
  U+E000–U+EDFF reservation;
- 78/11 complete, disjoint Latin/symbols membership;
- BDF syntax, unique XLFD identities, closed X indexes, PCF compilation, and
  native X loading of all 178 raw/Unicode names and 356 aliases, checking all
  14 XLFD properties and ascent/descent as well as rendering probes;
- one deterministic specimen for each of the 89 fonts and exact agreement
  with the tracked gallery;
- exact BDF-to-OTB logical identity, repertoire, advance, and one-bit display
  pixels, with valid bitmap records and consistent SFNT horizontal tables;
- Fontconfig query identity and spacing derived from glyph advances, with
  isolated matching selecting every exact family/style/physical strike;
- complete internal and adjacent checksums, safe archive paths, fixed metadata,
  and content allowlists; and
- two isolated release builds that are byte-for-byte identical.

Undefined-character substitution and arbitrary Fontconfig/X fallback are
outside the claim. The gates check characters actually encoded by each selected
font and require exact Genera file selection for the isolated desktop requests;
they do not treat a fallback glyph as Genera output. Optional `--freetype`
validation adds actual native glyph-load evidence only when explicitly run.

## Licensed-media audit before a provenance-changing release

If a release intentionally changes any file under `sources/genera-8.5/`, the
owner should first repeat the licensed-media import audit:

```sh
make check-import
```

If the change is meant to replace the imported corpus rather than verify it,
run `make import`, review the entire diff, then run `make check-import` again.
Confirm the exact VLOD filename, size and digest; decoder file digests; 89-name
closure; comparison report; count invariants; and normalized geometry digest.
Do not accept a changed VLOD or decoder as though it were a routine generator
rebuild.

This audit is intentionally not a public CI prerequisite because it requires
the owner's licensed local media. No CI artifact, log bundle, cache, or release
may upload the VLOD, supplied BFD/BDF witnesses, or any other excluded Genera
payload.

## Rights and notice check

Every archive must carry both [LICENSE](../LICENSE) and
[NOTICE.md](../NOTICE.md). Their scopes must remain distinct:

- BSD-3-Clause covers repository-authored tooling and documentation; and
- the historical glyph designs are published on the repository owner's
  stated U.S. “typeface as typeface” basis, not relicensed under BSD.

Before publication, inspect the archive file list and manifests to confirm
that no VLOD, Genera source or executable, original supplied font file,
documentation/help payload, standalone non-font raster asset, unrelated world object, absolute path, or
temporary build artifact entered either archive.

The notice is not legal advice and makes no foreign-rights claim. Do not
replace its bounded statement with “public domain,” “open source Genera,” or a
blanket claim about all font software.
