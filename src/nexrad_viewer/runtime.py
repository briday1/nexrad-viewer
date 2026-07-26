"""Runtime paths and generated profiles for the focused CLI."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

APPLICATION_NAME = "NEXRAD Viewer"


def _source_checkout_root() -> Path | None:
    candidate = Path(__file__).resolve().parents[2]
    return candidate if (candidate / "browser.toml").is_file() else None


def default_data_root() -> Path:
    """Choose configured, checkout, or working-directory radar data."""
    configured = os.environ.get("NEXRAD_VIEWER_DATA_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    working = Path.cwd() / "data"
    if working.is_dir():
        return working.resolve()
    checkout = _source_checkout_root()
    if checkout is not None:
        return (checkout / "data").resolve()
    return working.resolve()


def default_output_root() -> Path:
    """Choose a durable writable output directory."""
    configured = os.environ.get("NEXRAD_VIEWER_OUTPUT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    checkout = _source_checkout_root()
    if checkout is not None:
        return (checkout / "outputs").resolve()
    return (Path.cwd() / "outputs").resolve()


def profile_text(data_root: Path, output_root: Path) -> str:
    """Build the same focused profile with absolute runtime paths."""
    return f"""[browser]
title = "NOAA NEXRAD Viewer"
subtitle = "Exact Level III base-reflectivity sequences"

[[workspaces]]
use = "nexrad_viewer.workspace:create_workspace"
id = "nexrad-sites"
name = "Individual Radar Sites"
flatten_discovery = false
description = "Browse date folders, then play native NOAA NEXRAD Level III site sequences."
category = "weather radar"
tags = ["NOAA", "NEXRAD", "Level III", "sites", "reflectivity", "real data"]

[workspaces.config]
data_root = {json.dumps(str(data_root.expanduser().resolve()))}
output_root = {json.dumps(str(output_root.expanduser().resolve()))}
gif_frame_duration_ms = 200

[[workspaces]]
use = "nexrad_viewer.national.workspace:create_workspace"
id = "nexrad-national"
name = "CONUS Radar Mosaic"
flatten_discovery = true
description = "View each date folder as synchronized national reflectivity frames."
category = "weather radar"
tags = ["NOAA", "NEXRAD", "Level III", "CONUS", "mosaic", "real data"]

[workspaces.config]
data_root = {json.dumps(str(data_root.expanduser().resolve()))}
output_root = {json.dumps(str(output_root.expanduser().resolve()))}
national_frame_interval_minutes = 60.0
national_alignment_tolerance_minutes = 30.0
national_gif_width = 1200
national_gif_minimum_dbz = 20.0
national_gif_frame_duration_ms = 250
"""


@contextmanager
def runtime_profile(
    *,
    data_root: str | Path | None = None,
    output_root: str | Path | None = None,
) -> Iterator[Path]:
    """Create a live profile for the duration of one launcher process."""
    resolved_data = (
        default_data_root()
        if data_root is None
        else Path(data_root).expanduser().resolve()
    )
    resolved_output = (
        default_output_root()
        if output_root is None
        else Path(output_root).expanduser().resolve()
    )
    resolved_output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="nexrad-viewer-") as directory:
        profile = Path(directory) / "browser.toml"
        profile.write_text(
            profile_text(resolved_data, resolved_output),
            encoding="utf-8",
        )
        yield profile


__all__ = [
    "APPLICATION_NAME",
    "default_data_root",
    "default_output_root",
    "profile_text",
    "runtime_profile",
]
