#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
usage: scripts/test-linux-package-container.sh --runtime docker|podman
       --format deb|rpm|arch|void --package FILE [--package FILE ...]
       [--image IMAGE]

Install one or both concrete Genera-fonts group packages together in a clean
Linux container and run the shared installed-file/fontconfig verifier.
USAGE
}

fail() { echo "test-linux-package-container: $*" >&2; exit 1; }
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$script_dir/.." && pwd)
runtime=${CONTAINER_RUNTIME:-docker}
format=
package_paths=()
image=

while [[ $# -gt 0 ]]; do
    case "$1" in
        --runtime) [[ $# -ge 2 ]] || fail "--runtime requires docker or podman"; runtime=$2; shift 2 ;;
        --runtime=*) runtime=${1#--runtime=}; shift ;;
        --format) [[ $# -ge 2 ]] || fail "--format requires a value"; format=$2; shift 2 ;;
        --format=*) format=${1#--format=}; shift ;;
        --package) [[ $# -ge 2 ]] || fail "--package requires a file"; package_paths+=("$2"); shift 2 ;;
        --package=*) package_paths+=("${1#--package=}"); shift ;;
        --image) [[ $# -ge 2 ]] || fail "--image requires a value"; image=$2; shift 2 ;;
        --image=*) image=${1#--image=}; shift ;;
        --help|-h) usage; exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
done
case "$runtime" in docker|podman) ;; *) fail "--runtime must be docker or podman" ;; esac
case "$format" in deb|rpm|arch|void) ;; *) fail "--format must be deb, rpm, arch, or void" ;; esac
[[ ${#package_paths[@]} -gt 0 ]] || fail "at least one --package is required"
command -v "$runtime" >/dev/null 2>&1 || fail "missing container runtime: $runtime"
package_dir=
package_basenames=()
groups=()
for package_path in "${package_paths[@]}"; do
    [[ $package_path == /* ]] || package_path="$PWD/$package_path"
    [[ -f "$package_path" ]] || fail "package does not exist: $package_path"
    current_dir=$(cd "$(dirname "$package_path")" && pwd)
    [[ -z "$package_dir" || "$current_dir" == "$package_dir" ]] || \
        fail "all packages must be in one directory"
    package_dir=$current_dir
    package_basename=$(basename "$package_path")
    [[ "$package_basename" =~ ^[A-Za-z0-9._+~-]+$ ]] || \
        fail "unsafe package filename: $package_basename"
    case "$package_basename" in
        genera-fonts-latin[-_]*.deb|genera-fonts-latin-*.rpm|genera-fonts-latin-*.pkg.tar*|genera-fonts-latin-*.xbps) group=latin ;;
        genera-fonts-symbols[-_]*.deb|genera-fonts-symbols-*.rpm|genera-fonts-symbols-*.pkg.tar*|genera-fonts-symbols-*.xbps) group=symbols ;;
        *) fail "cannot derive latin/symbols group from package name: $package_basename" ;;
    esac
    [[ ! " ${groups[*]} " =~ " $group " ]] || fail "duplicate $group package"
    package_basenames+=("$package_basename")
    groups+=("$group")
done

if [[ -z "$image" ]]; then
    case "$format" in
        deb) image=docker.io/library/debian:bookworm@sha256:9344f8b8992482f80cba753f323adeaf17690076c095ccff6cc9536be98185dc ;;
        rpm) image=docker.io/library/fedora:latest@sha256:6c75d5bf57cb0fa5aa4b92c6a83c86c791644496d9ac230de7711f5b8ec3b898 ;;
        arch) image=docker.io/library/archlinux:base-devel@sha256:212b1e518e94ee9c52be55e8a32da75fcf11e7b5610b80b49479e67880102406 ;;
        void) image=${VOID_CONTAINER_IMAGE:-docker.io/voidlinux/voidlinux:latest@sha256:26ba972f0c06beadcec4796ec3037e0bec32af4d255edb68a528bd98304c74f4} ;;
    esac
fi
volume_suffix=:ro
[[ $runtime == podman ]] && volume_suffix=:ro,Z

echo "Testing ${package_basenames[*]} together in $image with $runtime"
"$runtime" run --rm -i \
    -e "FORMAT=$format" \
    -e "GROUPS=${groups[*]}" \
    -e "PACKAGE_BASENAMES=${package_basenames[*]}" \
    -v "$repo_root:/repo$volume_suffix" \
    -v "$package_dir:/packages$volume_suffix" \
    "$image" sh -s <<'CONTAINER'
set -eu
package_files=
for package_basename in $PACKAGE_BASENAMES; do
    pkg="/packages/$package_basename"
    [ -f "$pkg" ] || { echo "missing mounted package: $pkg" >&2; exit 1; }
    package_files="$package_files $pkg"
done

case "$FORMAT" in
  deb)
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 $package_files
    ;;
  rpm)
    if command -v dnf5 >/dev/null 2>&1; then
      dnf5 -y --setopt=tsflags= install python3 $package_files
    elif command -v dnf >/dev/null 2>&1; then
      dnf -y --setopt=tsflags= install python3 $package_files
    elif command -v microdnf >/dev/null 2>&1; then
      microdnf -y --setopt=tsflags= install python3 $package_files
    else
      echo "missing Fedora package manager" >&2
      exit 1
    fi
    ;;
  arch)
    pacman -Sy --noconfirm --needed bash fontconfig python
    pacman -U --noconfirm $package_files
    ;;
  void)
    mkdir -p /etc/xbps.d
    printf '%s\n' 'repository=https://repo-default.voidlinux.org/current' > /etc/xbps.d/00-repository-main.conf
    xbps-install -Syu -y xbps || xbps-install -Syu -y xbps
    xbps-install -S -y bash fontconfig python3
    mkdir -p /tmp/genera-fonts-repository
    cp $package_files /tmp/genera-fonts-repository/
    for package_basename in $PACKAGE_BASENAMES; do
      xbps-rindex -a "/tmp/genera-fonts-repository/$package_basename" >/dev/null
    done
    package_names=
    for group in $GROUPS; do package_names="$package_names genera-fonts-$group"; done
    xbps-install -R /tmp/genera-fonts-repository -y $package_names
    ;;
esac

for group in $GROUPS; do
  bash /repo/scripts/verify-linux-package-install.sh --format "$FORMAT" --group "$group"
done
CONTAINER
