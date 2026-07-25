"""Application-specific console wrapper around the Sigvue CLI."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
from pathlib import Path
import sys

from sigvue.web.application import main as sigvue_main

from .runtime import runtime_profile


def _print_application_options() -> None:
    print(
        "NEXRAD Viewer defaults:\n"
        "  --data-root PATH    Radar data directory (default: ./data)\n"
        "  --output-root PATH  Durable GIF output directory "
        "(default: ./outputs)\n"
        "\n"
        "All Sigvue server and batch options are also available.\n"
    )


def main() -> None:
    """Run Sigvue under the NEXRAD-specific command and defaults."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    options, remaining = parser.parse_known_args(sys.argv[1:])

    if "-h" in remaining or "--help" in remaining:
        _print_application_options()
    if options.config is not None and (
        options.data_root is not None or options.output_root is not None
    ):
        parser.error(
            "--config cannot be combined with --data-root or --output-root"
        )
    if options.config is not None:
        profile_context = nullcontext(options.config)
    else:
        profile_context = runtime_profile(
            data_root=options.data_root,
            output_root=options.output_root,
        )

    with profile_context as profile:
        original = sys.argv[1:]
        sys.argv[1:] = ["--config", str(profile), *remaining]
        try:
            sigvue_main()
        finally:
            sys.argv[1:] = original


if __name__ == "__main__":
    main()
