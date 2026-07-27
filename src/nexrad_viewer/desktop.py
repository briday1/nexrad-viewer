"""NEXRAD-branded native launcher backed by Sigvue's shared desktop host."""

from __future__ import annotations

import argparse
import sys
from contextlib import nullcontext
from pathlib import Path

from sigvue.web.desktop import main as sigvue_desktop_main

from .runtime import runtime_profile


def main() -> None:
    """Open the NEXRAD application as its own native desktop tool."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    options, remaining = parser.parse_known_args(sys.argv[1:])
    if options.config is not None and (
        options.data_root is not None or options.output_root is not None
    ):
        parser.error("--config cannot be combined with --data-root or --output-root")
    profile_context = (
        nullcontext(options.config)
        if options.config is not None
        else runtime_profile(
            data_root=options.data_root,
            output_root=options.output_root,
        )
    )
    with profile_context as profile:
        original = sys.argv[1:]
        sys.argv[1:] = ["--config", str(profile), *remaining]
        try:
            sigvue_desktop_main()
        finally:
            sys.argv[1:] = original


if __name__ == "__main__":
    main()
