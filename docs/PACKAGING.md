# Packaging and installation

All installable packages are built from the two checked generic release
archives. They never consume the licensed VLOD, the committed normalized
source records, raw-code BDFs, or glyph sheets. Each format produces separate,
co-installable `genera-fonts-latin` and `genera-fonts-symbols` packages.

## Native package builders

Set the same version and source epoch used for the release archives:

```sh
make package-deb  VERSION=v0.1.0 SOURCE_DATE_EPOCH=1
make package-rpm  VERSION=v0.1.0 SOURCE_DATE_EPOCH=1
make package-arch VERSION=v0.1.0 SOURCE_DATE_EPOCH=1
make package-void VERSION=v0.1.0 SOURCE_DATE_EPOCH=1
# or build all four
make packages VERSION=v0.1.0 SOURCE_DATE_EPOCH=1
```

DEB and RPM use the host packaging tools. Arch uses `makepkg` and a locked,
owner-only canonical build path so `.BUILDINFO` does not capture a random
temporary directory. Void uses `xbps-create`; when it is unavailable locally,
the builder can use Docker or Podman. Outputs and adjacent SHA-256 files are
written under `dist/packages/<format>/`.

To install both collections in clean native containers and verify their
package databases, installed-file closure, Fontconfig configuration, and OTB
discoverability:

```sh
make test-package-deb
make test-package-rpm
make test-package-arch
make test-package-void
# or all four
make test-packages
```

`CONTAINER_RUNTIME=podman` selects Podman instead of Docker.

## Nix and Guix

The locked flake exposes `genera-fonts-latin` and `genera-fonts-symbols` for
x86_64-linux and aarch64-linux. Nix classifies the historical typeface payload
as `unfreeRedistributable`; permit only these two packages, for example:

```nix
nixpkgs.config.allowUnfreePredicate = pkg:
  builtins.elem (lib.getName pkg) [
    "genera-fonts-latin"
    "genera-fonts-symbols"
  ];
```

Run both locked checks in a clean Nix container with:

```sh
make test-nix-flake-container
```

The local Guix module is
`packaging/guix/genera-fonts/packages/fonts.scm`. It exposes the same two
outputs and records BSD-3-Clause project work separately from the required
typeface publication notice.

## Distribution recipe templates

`packaging/aur/` and `packaging/void/` contain AUR and Void recipe templates.
They name the public upstream `https://github.com/htayj/genera-fonts`. Do not
submit them until a versioned release has been published and the recipe
revisions, source hashes, and release URLs have been updated against that tag.
No Gentoo overlay is included in this initial profile.

## License metadata

Package metadata expresses both payloads: BSD-3-Clause for repository-authored
and CADR-derived tooling/documentation, and the separately documented U.S.
“typeface as typeface” basis for the historical font shapes. The latter is not
presented as an upstream open-source license. Every package installs both
`LICENSE` and `NOTICE.md`.

The generic archives intentionally omit the tracked specimen gallery. The
gallery remains a review surface in the source repository; release archives
contain only installable Unicode BDF/OTB fonts, indexes, notices, and closed
provenance metadata.
