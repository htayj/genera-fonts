# Arch User Repository recipe

`genera-fonts/` is the source of the stable AUR package base
[`genera-fonts`](https://aur.archlinux.org/pkgbase/genera-fonts). It builds the
exact tagged source release and produces the co-installable
`genera-fonts-latin` and `genera-fonts-symbols` packages.

Both outputs contain only Unicode BDF and OTB typefaces plus the required
manifests and notices; no VLOD, imported source JSON, raw Genera-code font, or
prebuilt native package is installed. Arch discovers the fonts recursively
below `/usr/share/fonts`, so the packages need no runtime dependency, custom
Fontconfig configuration, or cache-update install script.

Regenerate metadata after changing `PKGBUILD`:

```sh
cd packaging/aur/genera-fonts
makepkg --printsrcinfo > .SRCINFO
```

Only the source recipe files belong in the AUR Git repository. Built packages,
source clones, font trees, and file lists are ignored and must not be pushed.
