#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
usage: scripts/stage-linux-package.sh --format deb|rpm|arch|gentoo|guix|nix|void
       --group latin|symbols --destdir DIR --version VERSION
       [--archive FILE | --release-dir DIR] [--no-fontconfig]

Validate and safely stage one Genera Fonts release into a Linux package root.
USAGE
}

fail() { echo "stage-linux-package: $*" >&2; exit 1; }

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
format=
group=
destdir=
raw_version=${VERSION:-}
archive=
release_dir=${RELEASE_DIR:-dist/release}
install_fontconfig=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --format) [[ $# -ge 2 ]] || fail "--format requires a value"; format=$2; shift 2 ;;
        --format=*) format=${1#--format=}; shift ;;
        --group) [[ $# -ge 2 ]] || fail "--group requires a value"; group=$2; shift 2 ;;
        --group=*) group=${1#--group=}; shift ;;
        --destdir) [[ $# -ge 2 ]] || fail "--destdir requires a directory"; destdir=$2; shift 2 ;;
        --destdir=*) destdir=${1#--destdir=}; shift ;;
        --version) [[ $# -ge 2 ]] || fail "--version requires a value"; raw_version=$2; shift 2 ;;
        --version=*) raw_version=${1#--version=}; shift ;;
        --archive) [[ $# -ge 2 ]] || fail "--archive requires a file"; archive=$2; shift 2 ;;
        --archive=*) archive=${1#--archive=}; shift ;;
        --release-dir) [[ $# -ge 2 ]] || fail "--release-dir requires a directory"; release_dir=$2; shift 2 ;;
        --release-dir=*) release_dir=${1#--release-dir=}; shift ;;
        --fontconfig) install_fontconfig=true; shift ;;
        --no-fontconfig) install_fontconfig=false; shift ;;
        --help|-h) usage; exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
done

case "$format" in deb|rpm|arch|gentoo|guix|nix|void) ;; *) fail "unsupported --format: $format" ;; esac
case "$group" in latin|symbols) ;; *) fail "--group must be latin or symbols" ;; esac
[[ -n "$destdir" ]] || fail "--destdir is required"
[[ "$destdir" != / ]] || fail "refusing to stage directly into /"
[[ ! -L "$destdir" ]] || fail "--destdir must not be a symbolic link"
if [[ -e "$destdir" ]]; then
    [[ -d "$destdir" ]] || fail "--destdir is not a directory: $destdir"
    if find "$destdir" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
        fail "--destdir must be a fresh empty package root: $destdir"
    fi
else
    mkdir -p "$destdir"
fi
destdir=$(cd "$destdir" && pwd -P)
[[ -n "$raw_version" ]] || fail "--version is required"
[[ $raw_version =~ ^[A-Za-z0-9._+~-]+$ ]] || fail "unsafe VERSION: $raw_version"

if [[ -z "$archive" ]]; then
    archive="$release_dir/Genera-fonts-$group-$raw_version.tar.gz"
fi
[[ -f "$archive" ]] || fail "missing release archive: $archive"
archive=$(cd "$(dirname "$archive")" && pwd -P)/$(basename "$archive")
expected_basename="Genera-fonts-$group-$raw_version.tar.gz"
[[ $(basename "$archive") == "$expected_basename" ]] || fail "archive must be named $expected_basename"

python3 "$script_dir/check_release.py" "$archive" >/dev/null

workdir=$(mktemp -d)
trap 'rm -rf -- "$workdir"' EXIT
expected_root="Genera-fonts-$group-$raw_version"
python3 - "$archive" "$workdir" "$expected_root" <<'PY'
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile

archive = Path(sys.argv[1])
destination = Path(sys.argv[2])
expected_root = sys.argv[3]
seen = set()
with tarfile.open(archive, "r:gz") as source:
    members = source.getmembers()
    if not members:
        raise SystemExit("release archive is empty")
    for member in members:
        name = member.name.rstrip("/")
        path = PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe archive path: {member.name!r}")
        if path.parts[0] != expected_root:
            raise SystemExit(f"archive member is outside {expected_root!r}")
        if name in seen:
            raise SystemExit(f"duplicate archive member: {name}")
        seen.add(name)
        if not (member.isdir() or member.isfile()):
            raise SystemExit(f"unsupported archive member type: {member.name!r}")
    for member in members:
        relative = PurePosixPath(member.name.rstrip("/"))
        target = destination.joinpath(*relative.parts)
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            target.chmod(0o755)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        extracted = source.extractfile(member)
        if extracted is None:
            raise SystemExit(f"cannot read archive member: {member.name!r}")
        with target.open("xb") as output:
            shutil.copyfileobj(extracted, output)
        target.chmod(0o644)
PY

payload="$workdir/$expected_root"
[[ -d "$payload" ]] || fail "archive did not contain $expected_root"
(cd "$payload" && sha256sum -c SHA256SUMS >/dev/null) || fail "internal checksums failed"

install_prefix=/usr
verify_layout=fhs
if [[ "$format" == nix || "$format" == guix ]]; then
    install_prefix=
    verify_layout=prefix
fi
font_root="$destdir$install_prefix/share/fonts/genera-fonts/$group"
data_root="$destdir$install_prefix/share/genera-fonts/$group"
doc_root="$destdir$install_prefix/share/doc/genera-fonts-$group"

copy_tree() {
    local source=$1 destination=$2
    mkdir -p "$destination"
    cp -a "$source"/. "$destination"/
}

copy_tree "$payload/fonts/bdf" "$font_root/bdf"
copy_tree "$payload/fonts/otb" "$font_root/otb"
install -D -m 0644 "$payload/RELEASE-MANIFEST.json" "$data_root/RELEASE-MANIFEST.json"
install -D -m 0644 "$payload/SHA256SUMS" "$data_root/SHA256SUMS"
copy_tree "$payload/metadata" "$data_root/metadata"
install -D -m 0644 "$payload/README.release.md" "$doc_root/README.release.md"
install -D -m 0644 "$payload/LICENSE" "$doc_root/LICENSE"
install -D -m 0644 "$payload/NOTICE.md" "$doc_root/NOTICE.md"

if [[ "$install_fontconfig" == true ]]; then
    config_name="75-genera-fonts-$group.conf"
    config_available="$destdir$install_prefix/share/fontconfig/conf.avail/$config_name"
    config_enabled="$destdir/etc/fonts/conf.d/$config_name"
    mkdir -p "$(dirname "$config_available")" "$(dirname "$config_enabled")"
    if [[ -n "$install_prefix" ]]; then
        configured_font_root="/usr/share/fonts/genera-fonts/$group/otb"
    else
        configured_font_root="$font_root/otb"
    fi
    python3 - "$group" "$configured_font_root" > "$config_available" <<'PY'
import sys
group, font_root = sys.argv[1:]
print('<?xml version="1.0"?>')
print('<!DOCTYPE fontconfig SYSTEM "urn:fontconfig:fonts.dtd">')
print('<fontconfig>')
print(f'  <description>Symbolics Genera {group} bitmap fonts</description>')
print(f'  <dir>{font_root}</dir>')
print('</fontconfig>')
PY
    cp -p "$config_available" "$config_enabled"
fi

find "$destdir" -type d -exec chmod 0755 {} +
find "$destdir" -type f -exec chmod 0644 {} +

other_group=latin
[[ $group == latin ]] && other_group=symbols
[[ ! -e "$destdir$install_prefix/share/fonts/genera-fonts/$other_group" ]] || fail "staging leaked $other_group fonts"
if find "$destdir" -type f \( -iname '*.vlod' -o -path '*/fonts/raw/*' -o -path '*/fonts/json/*' -o -path '*/sources/*' \) -print -quit | grep -q .; then
    fail "staging leaked VLOD, source JSON, or raw fonts"
fi

"$script_dir/verify-package-files.sh" --group "$group" --prefix "$destdir" \
    --layout "$verify_layout" --skip-fontconfig
echo "staged genera-fonts-$group for $format in $destdir"
