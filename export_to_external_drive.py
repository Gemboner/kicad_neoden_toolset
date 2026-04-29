#!/usr/bin/env python3
"""Copy a file to an external drive.

Usage:
  python export_to_external_drive.py /path/to/file
  python export_to_external_drive.py /path/to/file --drive-path /media/cto/MY_USB
"""

from __future__ import annotations

import argparse
import getpass
import shutil
import sys
from pathlib import Path


def find_external_drive() -> Path:
    user = getpass.getuser()
    roots = [
        Path(f"/media/{user}"),
        Path(f"/run/media/{user}"),
        Path("/Volumes"),
        Path("/mnt"),
    ]

    candidates: list[Path] = []
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for child in sorted(root.iterdir()):
            if child.is_dir() and child.is_mount() and child.exists():
                candidates.append(child)

    if not candidates:
        raise FileNotFoundError(
            "No mounted external drive found. Use --drive-path to set one."
        )

    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy a file to an external drive."
    )
    parser.add_argument("file", help="Path to the file to copy")
    parser.add_argument(
        "--drive-path",
        default=None,
        help="Mounted external drive path (optional). If omitted, auto-detects.",
    )
    args = parser.parse_args()

    src = Path(args.file).expanduser().resolve()
    if not src.exists():
        print(f"ERROR: Source file does not exist: {src}", file=sys.stderr)
        return 1
    if not src.is_file():
        print(f"ERROR: Source path is not a file: {src}", file=sys.stderr)
        return 1

    try:
        drive = (
            Path(args.drive_path).expanduser().resolve()
            if args.drive_path
            else find_external_drive()
        )
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not drive.exists() or not drive.is_dir():
        print(f"ERROR: Drive path is not a directory: {drive}", file=sys.stderr)
        return 1

    destination = drive / src.name
    shutil.copy2(src, destination)
    print(f"Copied: {src}")
    print(f"To:     {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
