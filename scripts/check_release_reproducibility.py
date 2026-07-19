#!/usr/bin/env python3
"""Build the real release twice and require byte-identical assets."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import tempfile

from build_release import ReleaseError, build_release


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", type=Path, default=Path("dist"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    parser.add_argument("--fonttosfnt", default="fonttosfnt", help=argparse.SUPPRESS)
    args = parser.parse_args()

    try:
        with tempfile.TemporaryDirectory(prefix="genera-fonts-release-repro-") as root:
            root_path = Path(root)
            builds: list[list[Path]] = []
            for build_number in (1, 2):
                builds.append(
                    build_release(
                        distribution=args.distribution,
                        release_dir=root_path / str(build_number),
                        version=args.version,
                        source_date_epoch=args.source_date_epoch,
                        fonttosfnt=args.fonttosfnt,
                    )
                )
            first = {path.name: sha256(path) for path in builds[0]}
            second = {path.name: sha256(path) for path in builds[1]}
            if first != second:
                changed = sorted(
                    name for name in set(first) | set(second) if first.get(name) != second.get(name)
                )
                raise ReleaseError(f"release builds differ: {', '.join(changed)}")
    except (OSError, ReleaseError) as error:
        parser.error(str(error))

    print(f"release reproducibility passed for {len(first)} assets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
