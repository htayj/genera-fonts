#!/usr/bin/env python3
"""Build twice in isolated directories and require byte-identical trees."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build.py"


class ReproducibilityError(AssertionError):
    """Two isolated builds produced different output trees."""


def snapshot(root: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            raise ReproducibilityError(f"build emitted a symlink: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ReproducibilityError(f"build emitted a special file: {relative}")
        result[relative] = path.read_bytes()
    return result


def compare_snapshots(first: dict[str, bytes], second: dict[str, bytes]) -> None:
    if first.keys() != second.keys():
        missing = sorted(first.keys() - second.keys())
        added = sorted(second.keys() - first.keys())
        raise ReproducibilityError(
            f"build file sets differ; missing={missing}, added={added}"
        )
    changed = [path for path in sorted(first) if first[path] != second[path]]
    if changed:
        raise ReproducibilityError("non-reproducible files: " + ", ".join(changed))


def run_build(output: Path, *, build_script: Path = BUILD) -> None:
    process = subprocess.run(
        [sys.executable, str(build_script), "--output", str(output)],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if process.returncode:
        detail = process.stderr.strip() or process.stdout.strip()
        raise ReproducibilityError(f"isolated build failed: {detail}")


def check_reproducibility(*, build_script: Path = BUILD) -> dict[str, object]:
    # Separate temporary roots make accidental absolute output-path capture
    # visible while still sharing only the repository's reviewed inputs.
    with tempfile.TemporaryDirectory(prefix="genera-font-build-first-") as first_dir:
        with tempfile.TemporaryDirectory(prefix="genera-font-build-second-") as second_dir:
            first_output = Path(first_dir) / "dist"
            second_output = Path(second_dir) / "dist"
            run_build(first_output, build_script=build_script)
            run_build(second_output, build_script=build_script)
            first = snapshot(first_output)
            second = snapshot(second_output)
            compare_snapshots(first, second)
    return {
        "status": "ok",
        "file_count": len(first),
        "byte_count": sum(len(value) for value in first.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--build-script",
        type=Path,
        default=BUILD,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()
    try:
        result = check_reproducibility(build_script=args.build_script.resolve())
    except (OSError, ReproducibilityError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
