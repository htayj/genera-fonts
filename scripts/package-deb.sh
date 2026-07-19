#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
usage: scripts/package-deb.sh [--version VERSION] [--release-dir DIR]
       [--archive FILE ...] [--output-dir DIR] [--no-fontconfig]
USAGE
}
fail() { echo "package-deb: $*" >&2; exit 1; }

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$script_dir/.." && pwd)
raw_version=${VERSION:-}
release_dir=${RELEASE_DIR:-dist/release}
output_dir=${DEB_PACKAGE_DIR:-${DIST_PACKAGE_DIR:-dist/packages}/deb}
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
for tool in dpkg-deb sha256sum mktemp du awk touch; do command -v "$tool" >/dev/null || fail "missing tool: $tool"; done

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

package_version=$("$script_dir/package-version.sh" --format deb "$raw_version")
mkdir -p "$output_dir"
output_dir=$(cd "$output_dir" && pwd -P)
workdir=$(mktemp -d "$output_dir/.debbuild.XXXXXX")
trap 'rm -rf -- "$workdir"' EXIT

for group in latin symbols; do
    package="genera-fonts-$group"
    root="$workdir/$group-root"
    stage_args=(--format deb --group "$group" --destdir "$root" --version "$raw_version" --archive "${archives[$group]}")
    [[ $install_fontconfig == true ]] || stage_args+=(--no-fontconfig)
    "$script_dir/stage-linux-package.sh" "${stage_args[@]}"

    mkdir -p "$root/DEBIAN"
    installed_roots=("$root/usr")
    [[ -d "$root/etc" ]] && installed_roots+=("$root/etc")
    installed_size=$(du -sk "${installed_roots[@]}" | awk '{total += $1} END {print (total > 0 ? total : 1)}')
    if [[ $group == latin ]]; then
        description="Genera bitmap fonts containing visible Latin letters"
    else
        description="Genera specialty bitmap fonts without visible Latin letters"
    fi
    cat > "$root/DEBIAN/control" <<CONTROL
Package: $package
Version: $package_version-1
Section: fonts
Priority: optional
Architecture: all
Maintainer: Genera Fonts maintainers <noreply@example.invalid>
Installed-Size: $installed_size
Depends: fontconfig
Multi-Arch: foreign
Homepage: https://github.com/htayj/genera-fonts
Description: $description
 Unicode-encoded BDF typefaces recovered from the Genera 8.5 resident font
 set, with display-equivalent OTB conversions for modern font clients.
CONTROL
    chmod 0644 "$root/DEBIAN/control"
    if [[ $install_fontconfig == true ]]; then
        printf '/etc/fonts/conf.d/75-genera-fonts-%s.conf\n' "$group" > "$root/DEBIAN/conffiles"
        chmod 0644 "$root/DEBIAN/conffiles"
    fi
    find "$root" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +

    package_name="${package}_${package_version}-1_all.deb"
    package_path="$output_dir/$package_name"
    rm -f "$package_path" "$package_path.sha256"
    dpkg-deb --root-owner-group --build "$root" "$package_path"
    (cd "$output_dir" && sha256sum "$package_name" > "$package_name.sha256")
    echo "wrote $package_path"
done
