#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
usage: scripts/package-arch.sh [--version VERSION] [--release-dir DIR]
       [--archive FILE ...] [--output-dir DIR] [--no-fontconfig]
USAGE
}
fail() { echo "package-arch: $*" >&2; exit 1; }

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$script_dir/.." && pwd)
raw_version=${VERSION:-}
release_dir=${RELEASE_DIR:-dist/release}
output_dir=${ARCH_PACKAGE_DIR:-${DIST_PACKAGE_DIR:-dist/packages}/arch}
install_fontconfig=true
archive_inputs=()
while [[ $# -gt 0 ]]; do
    case "$1" in
        --version) [[ $# -ge 2 ]] || fail "--version requires a value"; raw_version=$2; shift 2 ;;
        --version=*) raw_version=${1#--version=}; shift ;;
        --release-dir) [[ $# -ge 2 ]] || fail "--release-dir requires a value"; release_dir=$2; shift 2 ;;
        --release-dir=*) release_dir=${1#--release-dir=}; shift ;;
        --archive) [[ $# -ge 2 ]] || fail "--archive requires a file"; archive_inputs+=("$2"); shift 2 ;;
        --archive=*) archive_inputs+=("${1#--archive=}"); shift ;;
        --output-dir|--package-dir) [[ $# -ge 2 ]] || fail "$1 requires a value"; output_dir=$2; shift 2 ;;
        --output-dir=*|--package-dir=*) output_dir=${1#*=}; shift ;;
        --fontconfig) install_fontconfig=true; shift ;;
        --no-fontconfig) install_fontconfig=false; shift ;;
        --help|-h) usage; exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
done

[[ -n "$raw_version" ]] || raw_version=$(git -C "$repo_root" describe --tags --always --dirty 2>/dev/null || echo dev)
[[ $raw_version =~ ^[A-Za-z0-9._+~-]+$ ]] || fail "unsafe VERSION: $raw_version"
[[ ${SOURCE_DATE_EPOCH:-} =~ ^[0-9]+$ ]] || fail "SOURCE_DATE_EPOCH must be a non-negative integer"
export SOURCE_DATE_EPOCH
for tool in makepkg sha256sum flock stat id rm cp find sort touch; do command -v "$tool" >/dev/null || fail "missing tool: $tool"; done

declare -A archives=()
for archive in "${archive_inputs[@]}"; do
    [[ -f "$archive" ]] || fail "missing release archive: $archive"
    archive=$(cd "$(dirname "$archive")" && pwd -P)/$(basename "$archive")
    case $(basename "$archive") in
        "Genera-fonts-latin-$raw_version.tar.gz") group=latin ;;
        "Genera-fonts-symbols-$raw_version.tar.gz") group=symbols ;;
        *) fail "archive name does not match VERSION: $archive" ;;
    esac
    [[ -z ${archives[$group]:-} ]] || fail "duplicate $group archive"
    archives[$group]=$archive
done
for group in latin symbols; do
    archives[$group]=${archives[$group]:-"$release_dir/Genera-fonts-$group-$raw_version.tar.gz"}
    [[ -f ${archives[$group]} ]] || fail "missing $group release archive: ${archives[$group]}"
done

package_version=$("$script_dir/package-version.sh" --format arch "$raw_version")
mkdir -p "$output_dir"
output_dir=$(cd "$output_dir" && pwd -P)
# makepkg records build paths and a hash of the generated PKGBUILD in
# .BUILDINFO.  Serialize access to one owner-only canonical path so identical
# inputs produce identical packages across runs and output directories.
runtime_root=/tmp/genera-fonts-arch-package
[[ ! -L "$runtime_root" ]] || fail "canonical build root is a symlink: $runtime_root"
if [[ ! -e "$runtime_root" ]]; then
    (umask 077; mkdir "$runtime_root") || fail "cannot create $runtime_root"
fi
[[ -d "$runtime_root" ]] || fail "canonical build root is not a directory: $runtime_root"
[[ $(stat -c %u "$runtime_root") == "$(id -u)" ]] || fail "canonical build root has another owner: $runtime_root"
[[ $(stat -c %a "$runtime_root") == 700 ]] || fail "canonical build root must have mode 0700: $runtime_root"
lock_file="$runtime_root/lock"
[[ ! -L "$lock_file" ]] || fail "canonical build lock is a symlink: $lock_file"
exec 9>"$lock_file"
flock 9
workdir="$runtime_root/work"
[[ ! -L "$workdir" ]] || fail "canonical work directory is a symlink: $workdir"
if [[ -e "$workdir" ]]; then
    [[ $(stat -c %u "$workdir") == "$(id -u)" ]] || fail "canonical work directory has another owner: $workdir"
fi
rm -rf -- "$workdir"
mkdir -p "$workdir"
trap 'rm -rf -- "$workdir"' EXIT

for group in latin symbols; do
    package="genera-fonts-$group"
    group_work="$workdir/$group"
    staged="$group_work/staged"
    pkgdest="$group_work/packages"
    builddir="$group_work/build"
    mkdir -p "$group_work" "$pkgdest" "$builddir"
    stage_args=(--format arch --group "$group" --destdir "$staged" --version "$raw_version" --archive "${archives[$group]}")
    [[ $install_fontconfig == true ]] || stage_args+=(--no-fontconfig)
    "$script_dir/stage-linux-package.sh" "${stage_args[@]}"
    find "$staged" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
    printf -v staged_quoted '%q' "$staged"
    if [[ $group == latin ]]; then
        description="Genera bitmap fonts containing visible Latin letters"
    else
        description="Genera specialty bitmap fonts without visible Latin letters"
    fi
    cat > "$group_work/PKGBUILD" <<PKGBUILD
pkgname=$package
pkgver=$package_version
pkgrel=1
pkgdesc='$description'
arch=('any')
url='https://github.com/htayj/genera-fonts'
license=('BSD-3-Clause' 'LicenseRef-Typeface-as-Typeface')
depends=('fontconfig')
options=('!strip')
PKGBUILD
    if [[ $install_fontconfig == true ]]; then
        printf "backup=('etc/fonts/conf.d/75-genera-fonts-%s.conf')\n" "$group" >> "$group_work/PKGBUILD"
    fi
    cat >> "$group_work/PKGBUILD" <<PKGBUILD

package() {
    local staged_root=$staged_quoted
    cp -a "\$staged_root"/. "\$pkgdir"/
}
PKGBUILD
    (cd "$group_work" && BUILDDIR="$builddir" PKGDEST="$pkgdest" makepkg --nodeps --force --noconfirm)
    mapfile -t built < <(find "$pkgdest" -maxdepth 1 -type f -name "$package-*.pkg.tar*" ! -name '*.sig' | sort)
    [[ ${#built[@]} -eq 1 ]] || fail "expected one $group Arch package"
    package_path="$output_dir/$(basename "${built[0]}")"
    rm -f "$package_path" "$package_path.sha256"
    cp -f "${built[0]}" "$package_path"
    (cd "$output_dir" && sha256sum "$(basename "$package_path")" > "$(basename "$package_path").sha256")
    echo "wrote $package_path"
done
