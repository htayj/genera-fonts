#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
usage: scripts/verify-package-files.sh --group latin|symbols [--prefix PATH]
       [--layout fhs|prefix] [--allow-missing-doc] [--skip-fontconfig]

Verify one installed Genera Fonts package from its release manifest.
USAGE
}

fail() { echo "verify-package-files: $*" >&2; exit 1; }

group=
prefix=/
layout=fhs
skip_fontconfig=false
allow_missing_doc=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --group) [[ $# -ge 2 ]] || fail "--group requires a value"; group=$2; shift 2 ;;
        --group=*) group=${1#--group=}; shift ;;
        --prefix) [[ $# -ge 2 ]] || fail "--prefix requires a path"; prefix=$2; shift 2 ;;
        --prefix=*) prefix=${1#--prefix=}; shift ;;
        --layout) [[ $# -ge 2 ]] || fail "--layout requires a value"; layout=$2; shift 2 ;;
        --layout=*) layout=${1#--layout=}; shift ;;
        --allow-missing-doc) allow_missing_doc=true; shift ;;
        --skip-fontconfig) skip_fontconfig=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
done
case "$group" in latin|symbols) ;; *) fail "--group must be latin or symbols" ;; esac
case "$layout" in fhs|prefix) ;; *) fail "--layout must be fhs or prefix" ;; esac

join_path() {
    local base=${1%/} rest=${2#/}
    if [[ -z "$base" || "$base" == / ]]; then printf '/%s' "$rest"; else printf '%s/%s' "$base" "$rest"; fi
}

layout_prefix=/usr
[[ $layout == prefix ]] && layout_prefix=
font_root=$(join_path "$prefix" "$layout_prefix/share/fonts/genera-fonts/$group")
data_root=$(join_path "$prefix" "$layout_prefix/share/genera-fonts/$group")
doc_root=$(join_path "$prefix" "$layout_prefix/share/doc/genera-fonts-$group")
manifest="$data_root/RELEASE-MANIFEST.json"

for file in \
    "$manifest" \
    "$data_root/SHA256SUMS" \
    "$data_root/metadata/BUILD-MANIFEST.json" \
    "$data_root/metadata/SOURCE-MANIFEST.json" \
    "$data_root/metadata/UNICODE-MAPPING.json"; do
    [[ -s "$file" ]] || fail "missing or empty installed file: $file"
done
for file in "$doc_root/README.release.md" "$doc_root/LICENSE" "$doc_root/NOTICE.md"; do
    if [[ -s "$file" ]]; then
        continue
    fi
    [[ $allow_missing_doc == true ]] || fail "missing or empty installed file: $file"
done

if find "$font_root" "$data_root" "$doc_root" -type f \
        \( -iname '*.vlod' -o -path '*/fonts/raw/*' -o -path '*/fonts/json/*' -o -path '*/sources/*' \) \
        -print -quit | grep -q .; then
    fail "installed payload contains VLOD, source JSON, or raw fonts"
fi

python3 - "$font_root" "$manifest" "$group" <<'PY'
from pathlib import Path
import hashlib
import json
import shlex
import sys

font_root = Path(sys.argv[1])
manifest_path = Path(sys.argv[2])
group = sys.argv[3]
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
if manifest.get("schema_version") != 1 or manifest.get("content_class") != group:
    raise SystemExit("installed release manifest identity differs")
counts = manifest.get("counts")
if not isinstance(counts, dict):
    raise SystemExit("installed release counts are missing")
expected = counts.get("font_count")
if not isinstance(expected, int) or expected <= 0:
    raise SystemExit("installed release font_count is invalid")

bdf_root = font_root / "bdf"
otb_root = font_root / "otb"
bdfs = sorted(bdf_root.glob("*.bdf")) if bdf_root.is_dir() else []
otbs = sorted(otb_root.glob("*.otb")) if otb_root.is_dir() else []
if len(bdfs) != expected or len(otbs) != expected:
    raise SystemExit(
        f"installed font count differs: BDF={len(bdfs)}, OTB={len(otbs)}, expected={expected}"
    )
if {path.stem for path in bdfs} != {path.stem for path in otbs}:
    raise SystemExit("installed OTB/BDF filename closure differs")

font_names = {}
glyph_count = 0
for path in bdfs:
    lines = path.read_text(encoding="ascii").splitlines()
    fonts = [line[5:] for line in lines if line.startswith("FONT ")]
    chars = [line for line in lines if line.startswith("CHARS ")]
    if len(fonts) != 1 or not fonts[0].endswith("-ISO10646-1"):
        raise SystemExit(f"{path}: installed BDF is not ISO10646-1")
    if len(chars) != 1:
        raise SystemExit(f"{path}: malformed CHARS")
    glyph_count += int(chars[0].split()[1])
    if fonts[0] in font_names:
        raise SystemExit(f"{path}: duplicate XLFD")
    font_names[fonts[0]] = path.name
if glyph_count != counts.get("glyph_count"):
    raise SystemExit("installed BDF glyph count differs from release manifest")

dir_lines = (bdf_root / "fonts.dir").read_text(encoding="ascii").splitlines()
if not dir_lines or int(dir_lines[0]) != expected or len(dir_lines[1:]) != expected:
    raise SystemExit("installed fonts.dir count differs")
indexed = {}
for line in dir_lines[1:]:
    filename, separator, xlfd = line.partition(" ")
    if not separator or filename in indexed:
        raise SystemExit("installed fonts.dir is malformed")
    indexed[filename] = xlfd
if set(indexed) != {path.name for path in bdfs}:
    raise SystemExit("installed fonts.dir filename closure differs")
for xlfd, filename in font_names.items():
    if indexed.get(filename) != xlfd:
        raise SystemExit(f"installed fonts.dir XLFD differs for {filename}")

aliases = {}
for line in (bdf_root / "fonts.alias").read_text(encoding="ascii").splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("!"):
        continue
    fields = shlex.split(stripped)
    if len(fields) != 2 or fields[0] in aliases or fields[1] not in font_names:
        raise SystemExit("installed fonts.alias is malformed or dangling")
    aliases[fields[0]] = fields[1]
if len(aliases) != counts.get("alias_count"):
    raise SystemExit("installed alias count differs from release manifest")

for path in otbs:
    if path.read_bytes()[:4] not in {b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"}:
        raise SystemExit(f"{path}: invalid SFNT signature")

artifact_paths = set()
for artifact in manifest.get("artifacts", []):
    files = artifact.get("files", {})
    for kind in ("bdf", "otb"):
        record = files.get(kind, {})
        relative = record.get("path")
        if not isinstance(relative, str):
            raise SystemExit("installed artifact file record is malformed")
        name = Path(relative).name
        installed = (bdf_root if kind == "bdf" else otb_root) / name
        if not installed.is_file() or name in artifact_paths:
            raise SystemExit("installed artifact file closure differs")
        artifact_paths.add(name)
        digest = hashlib.sha256(installed.read_bytes()).hexdigest()
        if digest != record.get("sha256"):
            raise SystemExit(f"{installed}: release hash differs")
if len(artifact_paths) != expected * 2:
    raise SystemExit("installed artifact manifest is not closed")
PY

if [[ "$skip_fontconfig" == true ]]; then
    echo "fontconfig checks skipped for genera-fonts-$group"
    exit 0
fi

config_name="75-genera-fonts-$group.conf"
config_available=$(join_path "$prefix" "$layout_prefix/share/fontconfig/conf.avail/$config_name")
config_enabled=$(join_path "$prefix" "/etc/fonts/conf.d/$config_name")
for file in "$config_available" "$config_enabled"; do
    [[ -s "$file" ]] || fail "missing installed fontconfig file: $file"
done
command -v fc-query >/dev/null 2>&1 || fail "missing required tool: fc-query"
while IFS= read -r -d '' font; do
    family=$(fc-query --format '%{family[0]}\n' "$font" | head -n 1)
    [[ -n "$family" ]] || fail "fc-query returned no family for $font"
done < <(find "$font_root/otb" -type f -name '*.otb' -print0 | sort -z)

echo "genera-fonts-$group installed-file verification passed"
