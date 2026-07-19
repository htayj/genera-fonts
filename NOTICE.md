# Notices and publication scope

This repository keeps two distinct legal and provenance scopes. Do not read the
root `LICENSE` as a license from Symbolics or as a license to any Open Genera
distribution.

## Project-authored material

The root BSD-3-Clause `LICENSE` applies to the original build, import,
validation, packaging, and specimen-generation software in this repository and
to project-authored documentation and metadata, to the extent those materials
are subject to copyright. It does not change the status of the historical font
designs represented by the recovered records or their mechanically generated
font files and specimens.

Parts of that tooling were adapted from the sibling CADR Fonts project. The
root license retains both the CADR Fonts and Genera Fonts contributor notices.

## Historical font-shape payload

The repository owner has directed publication in the United States of the
recovered raster glyph designs as **typeface as typeface**. That publication
position relies on the U.S. rule identifying “typeface as typeface” as material
not subject to copyright, [37 C.F.R. §
202.1(e)](https://www.copyright.gov/title37/202/37cfr202-1.html), and on the U.S.
Copyright Office's explanation that typeface includes typefonts and letterforms,
[*Compendium of U.S. Copyright Office Practices* § 313.3(D) (3d ed.
2021)](https://www.copyright.gov/comp3/chap300/ch300-copyrightable-authorship.pdf#page=24).

This is the repository owner's stated U.S. publication basis. It is not a
BSD-3-Clause license grant for the historical designs, a representation that
Symbolics granted a license, or a claim that every possible component of a
font file is uncopyrightable. The same Compendium section distinguishes a
typeface from a computer program that generates a typeface: sufficiently
authored program code may be registrable even though protection does not
extend to the generated typeface. The original code in this repository is
licensed separately under BSD-3-Clause.

The publication set is deliberately font-only:

- raster glyph shapes and the display metrics needed to reproduce the
  represented typefaces;
- normalized font records, BDF/OTB conversions, checksums, catalogs, and
  specimens derived only from that font model; and
- factual provenance sufficient to identify and verify the bounded source.

It excludes the Genera VLOD, Genera Lisp or machine code, original BFD/BDF
files supplied with Genera, manuals and on-line help, standalone pictures and
other raster assets outside the selected resident `FONT` arrays, unrelated heap
objects, and every other non-font part of the licensed distribution. The exact
source identity, file closure, and technical boundary are recorded by the
source and release manifests that accompany each build and release.

## Limits

The cited rule and Office practice concern United States copyright law. Other
countries may protect typeface designs, font software, databases, or related
rights differently. This notice takes no position on foreign law, patents,
trademarks, contracts, trade secrets, or any other rights, and it is not legal
advice. Recipients are responsible for evaluating the law that applies to
their use and jurisdiction.

“Symbolics,” “Genera,” “Open Genera,” and related names are used only to
identify the historical system and source artifact. No endorsement or
trademark license is implied.
