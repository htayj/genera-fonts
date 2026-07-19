{
  description = "Reproducibly recovered Symbolics Genera 8.5 resident fonts";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in {
      packages = forAllSystems (system:
        let
          pkgs = import nixpkgs {
            inherit system;
            config.allowUnfreePredicate = pkg:
              builtins.elem (nixpkgs.lib.getName pkg) [
                "genera-fonts-latin"
                "genera-fonts-symbols"
              ];
          };
          python = pkgs.python3.withPackages (ps: [ ps.fonttools ]);
          version = "0+git.${self.shortRev or self.dirtyShortRev or "local"}";
          sourceDateEpoch = self.lastModified or 1;
          releaseAssets = pkgs.stdenvNoCC.mkDerivation {
            pname = "genera-fonts-release-assets";
            inherit version;
            src = self;
            strictDeps = true;
            nativeBuildInputs = [ pkgs.fonttosfnt python ];
            dontConfigure = true;
            buildPhase = ''
              runHook preBuild
              export LC_ALL=C
              export PYTHONDONTWRITEBYTECODE=1
              export SOURCE_DATE_EPOCH=${toString sourceDateEpoch}
              ${python}/bin/python3 scripts/build.py --output dist --clean
              ${python}/bin/python3 scripts/build_release.py \
                --distribution dist --release-dir dist/release \
                --version ${version} --source-date-epoch "$SOURCE_DATE_EPOCH" \
                --fonttosfnt ${pkgs.fonttosfnt}/bin/fonttosfnt
              ${python}/bin/python3 scripts/check_release.py --release-dir dist/release
              ${python}/bin/python3 scripts/check_otb.py \
                dist/release/Genera-fonts-latin-${version}.tar.gz \
                dist/release/Genera-fonts-symbols-${version}.tar.gz
              runHook postBuild
            '';
            installPhase = ''
              runHook preInstall
              mkdir -p "$out"
              cp -a dist/release/. "$out"/
              runHook postInstall
            '';
          };
          mkGeneraFonts = group: pkgs.stdenvNoCC.mkDerivation {
            pname = "genera-fonts-${group}";
            inherit version;
            src = self;
            strictDeps = true;
            nativeBuildInputs = [ pkgs.coreutils pkgs.findutils pkgs.fontconfig pkgs.gnugrep python ];
            dontConfigure = true;
            dontBuild = true;
            installPhase = ''
              runHook preInstall
              bash scripts/stage-linux-package.sh \
                --format nix --group ${group} --destdir "$out" \
                --version ${version} --release-dir ${releaseAssets}
              runHook postInstall
            '';
            doInstallCheck = true;
            installCheckPhase = ''
              runHook preInstallCheck
              bash scripts/verify-package-files.sh --group ${group} --prefix "$out" --layout prefix
              if find "$out" -type f \( -iname '*.vlod' -o -path '*/fonts/raw/*' -o -path '*/fonts/json/*' \) -print -quit | grep -q .; then
                echo "source or raw font material entered the Nix output" >&2
                exit 1
              fi
              runHook postInstallCheck
            '';
            meta = with pkgs.lib; {
              description = "Symbolics Genera 8.5 ${group} bitmap fonts";
              homepage = "https://github.com/htayj/genera-fonts";
              license = [ licenses.bsd3 licenses.unfreeRedistributable ];
              platforms = platforms.linux;
            };
          };
        in rec {
          genera-fonts-latin = mkGeneraFonts "latin";
          genera-fonts-symbols = mkGeneraFonts "symbols";
          default = genera-fonts-latin;
        });

      checks = forAllSystems (system: {
        genera-fonts-latin = self.packages.${system}.genera-fonts-latin;
        genera-fonts-symbols = self.packages.${system}.genera-fonts-symbols;
      });
    };
}
