# Packaging and installation

The native release packages are built from the two checked generic archives.
The stable AUR recipe instead rebuilds those archives from the exact tagged
public source. Neither path consumes the licensed VLOD or installs committed
normalized source records, raw-code BDFs, or glyph sheets. Each format
produces separate, co-installable `genera-fonts-latin` and
`genera-fonts-symbols` packages.

## Native package builders

Set the same version and source epoch used for the release archives:

```sh
make package-deb  VERSION=v0.1.1 SOURCE_DATE_EPOCH=1784671066
make package-rpm  VERSION=v0.1.1 SOURCE_DATE_EPOCH=1784671066
make package-arch VERSION=v0.1.1 SOURCE_DATE_EPOCH=1784671066
make package-void VERSION=v0.1.1 SOURCE_DATE_EPOCH=1784671066
# or build all four
make packages VERSION=v0.1.1 SOURCE_DATE_EPOCH=1784671066
```

DEB and RPM use the host packaging tools. Arch uses `makepkg` and a locked,
owner-only canonical build path so `.BUILDINFO` does not capture a random
temporary directory. Void uses `xbps-create`; when it is unavailable locally,
the builder can use Docker or Podman. Outputs and adjacent SHA-256 files are
written under `dist/packages/<format>/`.

To install both collections in clean native containers and verify their
package databases, installed-file closure, Fontconfig configuration, and OTB
discoverability, run the targets below. The installed-file checker also
compares every OTB's exact
Fontconfig family, style, weight, slant, width, and bitmap pixel size with the
release's reviewed logical identity:

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

## Arch User Repository

The AUR package base
[`genera-fonts`](https://aur.archlinux.org/pkgbase/genera-fonts) builds the
tagged source release and produces `genera-fonts-latin` and
`genera-fonts-symbols`. It does not download or redistribute the native Arch
packages attached to the GitHub release.

Build and install both split packages with the standard AUR workflow:

```sh
git clone https://aur.archlinux.org/genera-fonts.git
cd genera-fonts
makepkg --syncdeps --install
```

The packages install only Unicode BDF and Unicode-derived OTB fonts below
`/usr/share/fonts/genera-fonts/<group>/`. Arch discovers those directories
recursively and refreshes its font cache through existing package-manager
hooks, so no package-owned Fontconfig configuration or cache hook is needed.

## Distribution recipes

`packaging/aur/` contains the published stable AUR source recipe and
`packaging/void/` contains local Void templates. Both name the public upstream
`https://github.com/htayj/genera-fonts`. No Gentoo overlay is included in this
initial profile.

## License metadata

Package metadata expresses both payloads: BSD-3-Clause for repository-authored
and CADR-derived tooling/documentation, and the separately documented U.S.
“typeface as typeface” basis for the historical font shapes. The latter is not
presented as an upstream open-source license. Every package installs both
`LICENSE` and `NOTICE.md`.

The generic archives intentionally omit the tracked specimen gallery. The
gallery remains a review surface in the source repository; release archives
contain only installable Unicode BDF/OTB fonts, indexes, notices, and closed
provenance metadata. That metadata includes `FONT-IDENTITIES.json`, so package
consumers can distinguish resident strike names, Genera logical selectors,
device-specific nominal X sizes, and measured bitmap sizes.
