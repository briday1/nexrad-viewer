"""Build the NEXRAD Viewer desktop artifact with PyInstaller."""

from __future__ import annotations

import argparse
from importlib.resources import as_file, files
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the native NEXRAD Viewer desktop application",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        help="Bundle this data directory into the application",
    )
    parser.add_argument(
        "--without-data",
        action="store_true",
        help="Build a smaller application without embedded radar data",
    )
    args, pyinstaller_args = parser.parse_known_args()
    if args.data_root is not None and args.without_data:
        parser.error("--data-root and --without-data cannot be combined")

    try:
        from PyInstaller.__main__ import run
    except ImportError as exc:  # pragma: no cover - optional desktop extra
        raise SystemExit(
            'Install desktop support first: pip install "nexrad-viewer[desktop]"'
        ) from exc

    if args.data_root is not None:
        os.environ["NEXRAD_VIEWER_BUNDLE_DATA"] = "1"
        os.environ["NEXRAD_VIEWER_DATA_ROOT"] = str(
            args.data_root.expanduser().resolve()
        )
    elif args.without_data:
        os.environ["NEXRAD_VIEWER_BUNDLE_DATA"] = "0"

    resource = files("nexrad_viewer._packaging").joinpath(
        "nexrad_viewer.spec"
    )
    arguments = ["--clean", "--noconfirm", *pyinstaller_args]
    with as_file(resource) as spec_path:
        run([*arguments, str(spec_path)])


if __name__ == "__main__":
    main()
