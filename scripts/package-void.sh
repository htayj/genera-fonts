#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
usage: scripts/package-void.sh [--version VERSION] [--release-dir DIR]
       [--archive FILE ...] [--output-dir DIR] [--runtime docker|podman]
       [--image IMAGE] [--no-container] [--no-fontconfig]
USAGE
}
fail() { echo "package-void: $*" >&2; exit 1; }

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$script_dir/.." && pwd)
raw_version=${VERSION:-}
release_dir=${RELEASE_DIR:-dist/release}
output_dir=${VOID_PACKAGE_DIR:-${DIST_PACKAGE_DIR:-dist/packages}/void}
install_fontconfig=true
runtime=${CONTAINER_RUNTIME:-docker}
image=${VOID_CONTAINER_IMAGE:-docker.io/voidlinux/voidlinux:latest@sha256:26ba972f0c06beadcec4796ec3037e0bec32af4d255edb68a528bd98304c74f4}
allow_container=true
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
        --runtime) [[ $# -ge 2 ]] || fail "--runtime requires a value"; runtime=$2; shift 2 ;;
        --runtime=*) runtime=${1#--runtime=}; shift ;;
        --image) [[ $# -ge 2 ]] || fail "--image requires a value"; image=$2; shift 2 ;;
        --image=*) image=${1#--image=}; shift ;;
        --no-container) allow_container=false; shift ;;
        --fontconfig) install_fontconfig=true; shift ;;
        --no-fontconfig) install_fontconfig=false; shift ;;
        --help|-h) usage; exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
done

[[ -n "$raw_version" ]] || raw_version=$(git -C "$repo_root" describe --tags --always --dirty 2>/dev/null || echo dev)
[[ $raw_version =~ ^[A-Za-z0-9._+~-]+$ ]] || fail "unsafe VERSION: $raw_version"
[[ ${SOURCE_DATE_EPOCH:-} =~ ^[0-9]+$ ]] || fail "SOURCE_DATE_EPOCH must be a non-negative integer"
case "$runtime" in docker|podman) ;; *) fail "--runtime must be docker or podman" ;; esac

if ! command -v xbps-create >/dev/null 2>&1; then
    [[ $allow_container == true ]] || fail "missing tool: xbps-create"
    command -v "$runtime" >/dev/null || fail "missing container runtime: $runtime"
    for path in "$release_dir" "$output_dir" "${archive_inputs[@]}"; do
        [[ -z "$path" ]] && continue
        resolved=$(realpath -m "$path")
        case "$resolved" in "$repo_root"|"$repo_root"/*) ;; *) fail "container fallback requires paths under $repo_root: $path" ;; esac
    done
    release_abs=$(realpath -m "$release_dir")
    output_abs=$(realpath -m "$output_dir")
    cmd=(./scripts/package-void.sh --version "$raw_version"
         --release-dir "${release_abs#"$repo_root"/}"
         --output-dir "${output_abs#"$repo_root"/}" --no-container)
    [[ $install_fontconfig == true ]] || cmd+=(--no-fontconfig)
    for archive in "${archive_inputs[@]}"; do
        archive_abs=$(realpath -m "$archive")
        cmd+=(--archive "${archive_abs#"$repo_root"/}")
    done
    volume_suffix=:rw
    [[ $runtime == podman ]] && volume_suffix=:rw,Z
    "$runtime" run --rm -i -e "SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH" \
        -v "$repo_root:/repo$volume_suffix" -w /repo "$image" sh -lc '
        set -eu
        mkdir -p /etc/xbps.d
        printf "%s\n" "repository=https://repo-default.voidlinux.org/current" > /etc/xbps.d/00-repository-main.conf
        xbps-install -Syu -y xbps || xbps-install -Syu -y xbps
        xbps-install -S -y bash python3
        exec bash -s
    ' <<CONTAINER_COMMAND
set -euo pipefail
$(printf ' %q' "${cmd[@]}")
CONTAINER_COMMAND
    exit $?
fi
for tool in xbps-create sha256sum mktemp cp find touch; do command -v "$tool" >/dev/null || fail "missing tool: $tool"; done

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

package_version=$("$script_dir/package-version.sh" --format void "$raw_version")
revision=1
mkdir -p "$output_dir"
output_dir=$(cd "$output_dir" && pwd -P)
workdir=$(mktemp -d "$output_dir/.voidbuild.XXXXXX")
trap 'rm -rf -- "$workdir"' EXIT

for group in latin symbols; do
    package="genera-fonts-$group"
    root="$workdir/$group-root"
    stage_args=(--format void --group "$group" --destdir "$root" --version "$raw_version" --archive "${archives[$group]}")
    [[ $install_fontconfig == true ]] || stage_args+=(--no-fontconfig)
    "$script_dir/stage-linux-package.sh" "${stage_args[@]}"
    find "$root" -exec touch -h -d "@$SOURCE_DATE_EPOCH" {} +
    if [[ $group == latin ]]; then
        summary="Genera bitmap fonts containing visible Latin letters"
    else
        summary="Genera specialty bitmap fonts without visible Latin letters"
    fi
    pkgver="$package-${package_version}_${revision}"
    expected_name="$pkgver.noarch.xbps"
    rm -f "$output_dir/$expected_name" "$output_dir/$expected_name.sha256"
    xbps_args=(
        -A noarch -n "$pkgver" -s "$summary"
        -S "Unicode Genera BDF typefaces with display-equivalent OTB conversions."
        -D "fontconfig>=0" -H "https://github.com/htayj/genera-fonts"
        -l "BSD-3-Clause AND custom:Typeface-as-Typeface" -m "Genera Fonts maintainers <noreply@example.invalid>"
        -t fonts
    )
    [[ $install_fontconfig == true ]] && xbps_args+=(-F "/etc/fonts/conf.d/75-genera-fonts-$group.conf")
    (cd "$output_dir" && xbps-create "${xbps_args[@]}" "$root" >/dev/null)
    [[ -f "$output_dir/$expected_name" ]] || fail "xbps-create did not produce $expected_name"
    (cd "$output_dir" && sha256sum "$expected_name" > "$expected_name.sha256")
    echo "wrote $output_dir/$expected_name"
done
