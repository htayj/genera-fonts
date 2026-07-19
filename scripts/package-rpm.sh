#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
usage: scripts/package-rpm.sh [--version VERSION] [--release-dir DIR]
       [--archive FILE ...] [--output-dir DIR] [--no-fontconfig]
USAGE
}
fail() { echo "package-rpm: $*" >&2; exit 1; }

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$script_dir/.." && pwd)
raw_version=${VERSION:-}
release_dir=${RELEASE_DIR:-dist/release}
output_dir=${RPM_PACKAGE_DIR:-${DIST_PACKAGE_DIR:-dist/packages}/rpm}
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
for tool in rpmbuild sha256sum mktemp cp mkdir touch; do command -v "$tool" >/dev/null || fail "missing tool: $tool"; done

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

package_version=$("$script_dir/package-version.sh" --format rpm "$raw_version")
mkdir -p "$output_dir"
output_dir=$(cd "$output_dir" && pwd -P)
workdir=$(mktemp -d "$output_dir/.rpmbuild.XXXXXX")
trap 'rm -rf -- "$workdir"' EXIT

for group in latin symbols; do
    package="genera-fonts-$group"
    staged="$workdir/$group-root"
    stage_args=(--format rpm --group "$group" --destdir "$staged" --version "$raw_version" --archive "${archives[$group]}")
    [[ $install_fontconfig == true ]] || stage_args+=(--no-fontconfig)
    "$script_dir/stage-linux-package.sh" "${stage_args[@]}"
    find "$staged" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +

    topdir="$workdir/$group-rpmbuild"
    mkdir -p "$topdir/BUILD" "$topdir/BUILDROOT" "$topdir/RPMS" "$topdir/SOURCES" "$topdir/SPECS" "$topdir/SRPMS"
    spec="$topdir/SPECS/$package.spec"
    if [[ $group == latin ]]; then
        summary="Genera bitmap fonts containing visible Latin letters"
    else
        summary="Genera specialty bitmap fonts without visible Latin letters"
    fi
    cat > "$spec" <<SPEC
%global debug_package %{nil}
Name: $package
Version: $package_version
Release: 1
Summary: $summary
License: BSD-3-Clause AND LicenseRef-Typeface-as-Typeface
URL: https://github.com/htayj/genera-fonts
BuildArch: noarch
Requires: fontconfig
AutoReqProv: no

%description
Unicode-encoded BDF typefaces recovered from the Genera 8.5 resident font set,
with display-equivalent OTB conversions for modern font clients. See the
installed NOTICE.md for the redistribution basis and scope.

%prep
%build
%install
rm -rf "%{buildroot}"
mkdir -p "%{buildroot}"
cp -a "$staged"/. "%{buildroot}"/

%files
/usr/share/fonts/genera-fonts/$group
/usr/share/genera-fonts/$group
%doc /usr/share/doc/$package/README.release.md
%license /usr/share/doc/$package/LICENSE
%license /usr/share/doc/$package/NOTICE.md
SPEC
    if [[ $install_fontconfig == true ]]; then
        cat >> "$spec" <<SPEC
/usr/share/fontconfig/conf.avail/75-genera-fonts-$group.conf
%config(noreplace) /etc/fonts/conf.d/75-genera-fonts-$group.conf
SPEC
    fi

    rpmbuild -bb "$spec" \
        --define "_topdir $topdir" \
        --define "_build_id_links none" \
        --define "use_source_date_epoch_as_buildtime 1" \
        --define "_buildhost reproducible.genera-fonts.invalid"
    built="$topdir/RPMS/noarch/$package-$package_version-1.noarch.rpm"
    [[ -f "$built" ]] || fail "rpmbuild did not produce $built"
    package_path="$output_dir/$(basename "$built")"
    rm -f "$package_path" "$package_path.sha256"
    cp -f "$built" "$package_path"
    (cd "$output_dir" && sha256sum "$(basename "$package_path")" > "$(basename "$package_path").sha256")
    echo "wrote $package_path"
done
