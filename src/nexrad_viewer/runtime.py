"""Runtime paths and generated profiles for CLI and desktop delivery."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Iterator


APPLICATION_NAME = "NEXRAD Viewer"


def application_data_root() -> Path:
    """Return the platform-native writable application data directory."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APPLICATION_NAME
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / APPLICATION_NAME
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "nexrad-viewer"


def _source_checkout_root() -> Path | None:
    candidate = Path(__file__).resolve().parents[2]
    return candidate if (candidate / "browser.toml").is_file() else None


def _bundled_data_root() -> Path | None:
    bundle = getattr(sys, "_MEIPASS", None)
    if not bundle:
        return None
    candidate = Path(bundle) / "data"
    return candidate if candidate.is_dir() else None


def default_data_root(*, desktop: bool = False) -> Path:
    """Choose embedded, checkout, or working-directory radar data."""
    configured = os.environ.get("NEXRAD_VIEWER_DATA_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    bundled = _bundled_data_root()
    if bundled is not None:
        return bundled
    working = Path.cwd() / "data"
    if working.is_dir():
        return working.resolve()
    checkout = _source_checkout_root()
    if checkout is not None:
        return (checkout / "data").resolve()
    return (
        application_data_root() / "data"
        if desktop
        else working.resolve()
    )


def default_output_root(*, desktop: bool = False) -> Path:
    """Choose a durable writable output directory."""
    configured = os.environ.get("NEXRAD_VIEWER_OUTPUT_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    if desktop or getattr(sys, "frozen", False):
        return application_data_root() / "outputs"
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
id = "nexrad"
name = "NEXRAD Base Reflectivity"
description = "Browse and play native NOAA NEXRAD Level III scan sequences."
category = "weather radar"
tags = ["NOAA", "NEXRAD", "Level III", "reflectivity", "real data"]

[workspaces.config]
data_root = {json.dumps(str(data_root.expanduser().resolve()))}
output_root = {json.dumps(str(output_root.expanduser().resolve()))}
gif_radius_km = 120.0
gif_frame_duration_ms = 200
"""


@contextmanager
def runtime_profile(
    *,
    data_root: str | Path | None = None,
    output_root: str | Path | None = None,
    desktop: bool = False,
) -> Iterator[Path]:
    """Create a live profile for the duration of one launcher process."""
    resolved_data = (
        default_data_root(desktop=desktop)
        if data_root is None
        else Path(data_root).expanduser().resolve()
    )
    resolved_output = (
        default_output_root(desktop=desktop)
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
    "application_data_root",
    "default_data_root",
    "default_output_root",
    "profile_text",
    "runtime_profile",
]
