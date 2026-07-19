#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'USAGE'
usage: scripts/verify-linux-package-install.sh --format deb|rpm|arch|void
       --group latin|symbols [--prefix PATH] [--skip-package-db]
       [--skip-fontconfig]
USAGE
}

fail() { echo "verify-linux-package-install: $*" >&2; exit 1; }
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
format=
group=
prefix=/
skip_package_db=false
skip_fontconfig=false
allow_missing_doc=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --format) [[ $# -ge 2 ]] || fail "--format requires a value"; format=$2; shift 2 ;;
        --format=*) format=${1#--format=}; shift ;;
        --group) [[ $# -ge 2 ]] || fail "--group requires a value"; group=$2; shift 2 ;;
        --group=*) group=${1#--group=}; shift ;;
        --prefix) [[ $# -ge 2 ]] || fail "--prefix requires a path"; prefix=$2; shift 2 ;;
        --prefix=*) prefix=${1#--prefix=}; shift ;;
        --skip-package-db) skip_package_db=true; shift ;;
        --skip-fontconfig) skip_fontconfig=true; shift ;;
        --help|-h) usage; exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
done
case "$format" in deb|rpm|arch|void) ;; *) fail "--format must be deb, rpm, arch, or void" ;; esac
case "$group" in latin|symbols) ;; *) fail "--group must be latin or symbols" ;; esac
package="genera-fonts-$group"
need_tool() { command -v "$1" >/dev/null 2>&1 || fail "missing required tool: $1"; }

if [[ $skip_package_db != true ]]; then
    case "$format" in
        deb)
            need_tool dpkg-query
            dpkg-query -W -f='${db:Status-Abbrev} ${binary:Package} ${Version}\n' "$package" | grep -q '^ii ' || fail "$package is not installed according to dpkg"
            ;;
        rpm)
            need_tool rpm
            rpm -q "$package" >/dev/null || fail "$package is not installed according to rpm"
            ;;
        arch)
            need_tool pacman
            pacman -Q "$package" >/dev/null || fail "$package is not installed according to pacman"
            for doc in README.release.md LICENSE NOTICE.md; do
                doc_path="/usr/share/doc/$package/$doc"
                if [[ ! -f "$doc_path" ]]; then
                    pacman -Qlq "$package" | sed 's|^/||' | awk -v expected="${doc_path#/}" '
                        $0 == expected { found = 1 }
                        END { exit(found ? 0 : 1) }
                    ' || fail "$package does not own expected documentation: $doc_path"
                    allow_missing_doc=true
                fi
            done
            ;;
        void)
            need_tool xbps-query
            xbps-query "$package" >/dev/null || fail "$package is not installed according to xbps"
            ;;
    esac
fi

args=(--group "$group" --prefix "$prefix")
[[ $allow_missing_doc == true ]] && args+=(--allow-missing-doc)
[[ $skip_fontconfig == true ]] && args+=(--skip-fontconfig)
bash "$script_dir/verify-package-files.sh" "${args[@]}"
echo "$package $format package verification passed"
