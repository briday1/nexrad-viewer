"""Durable high-resolution national mosaic GIF rendering."""

from __future__ import annotations

import re
from math import isfinite
from pathlib import Path

import numpy as np
from PIL import Image
from sigvue import (
    Batch,
    BatchDestination,
    BatchRequest,
    BatchResult,
    CapabilityChoice,
    DataResource,
)

from ..formats.nexrad import open_scan
from ..gif_rendering import (
    compose_reflectivity_frame,
    indexed_reflectivity,
    render_animation,
)
from .analysis import CONUS_BOUNDS, national_mosaic_grid
from .geography import state_boundaries
from .models import NationalDay
from .reader import aligned_frames

RENDER_NATIONAL_GIF = "render-national-mosaic-gif"
DBZ_MAXIMUM = 75.0


def _indexed_map(
    reflectivity: np.ndarray,
    *,
    minimum_dbz: float,
) -> Image.Image:
    return indexed_reflectivity(
        reflectivity,
        minimum_visible_dbz=minimum_dbz,
    )


def _frame_image(
    day: NationalDay,
    frame_index: int,
    *,
    interval_seconds: float,
    tolerance_seconds: float,
    width: int,
    minimum_dbz: float,
) -> Image.Image:
    frames = aligned_frames(
        day,
        interval_seconds=interval_seconds,
        tolerance_seconds=tolerance_seconds,
    )
    frame = frames[frame_index]
    scans = tuple(open_scan(header.source_path) for header in frame.headers)
    maximum_range_km = max(
        float(scan.ground_range_edges_km[-1]) for scan in scans
    )
    _, _, reflectivity = national_mosaic_grid(
        scans,
        width=width,
        maximum_range_km=maximum_range_km,
    )
    return compose_reflectivity_frame(
        reflectivity,
        timestamp=frame.target_time,
        bounds=CONUS_BOUNDS,
        boundary_rings=state_boundaries(),
        reflectivity_limits=(minimum_dbz, DBZ_MAXIMUM),
        timeline_index=frame_index,
    )


def render_national_gif(
    day: NationalDay,
    target: str | Path,
    *,
    frame_interval_minutes: float,
    alignment_tolerance_minutes: float,
    width: int,
    minimum_dbz: float,
    frame_duration_ms: int,
) -> Path:
    """Render all synchronized frames without interactive viewport reduction."""
    destination = Path(target).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    interval_seconds = frame_interval_minutes * 60.0
    tolerance_seconds = alignment_tolerance_minutes * 60.0
    frames = aligned_frames(
        day,
        interval_seconds=interval_seconds,
        tolerance_seconds=tolerance_seconds,
    )
    if not frames:
        raise ValueError(f"No aligned national frames exist for {day.date}")
    return render_animation(
        destination,
        frame_count=len(frames),
        frame_duration_ms=frame_duration_ms,
        frame_builder=lambda index: _frame_image(
            day,
            index,
            interval_seconds=interval_seconds,
            tolerance_seconds=tolerance_seconds,
            width=width,
            minimum_dbz=minimum_dbz,
        ),
    )


class NationalGifBatch(Batch[NationalDay]):
    """One deterministic high-resolution mosaic animation per date."""

    item_actions = (
        CapabilityChoice(RENDER_NATIONAL_GIF, "Render national mosaic GIF"),
    )
    workspace_actions = (
        CapabilityChoice(
            RENDER_NATIONAL_GIF,
            "Render all national mosaic GIFs",
        ),
    )

    def __init__(
        self,
        output_root: str | Path,
        *,
        frame_interval_minutes: float,
        alignment_tolerance_minutes: float,
        width: int,
        minimum_dbz: float,
        frame_duration_ms: int,
    ) -> None:
        if frame_interval_minutes <= 0 or alignment_tolerance_minutes < 0:
            raise ValueError("national frame timing values are invalid")
        if width < 128:
            raise ValueError("national GIF width must be at least 128")
        if (
            not isfinite(minimum_dbz)
            or minimum_dbz < -20
            or minimum_dbz > 75
        ):
            raise ValueError("national GIF minimum dBZ must be from -20 to 75")
        if frame_duration_ms < 20:
            raise ValueError("national GIF duration must be at least 20 ms")
        self.output_root = Path(output_root).expanduser().resolve()
        self.frame_interval_minutes = float(frame_interval_minutes)
        self.alignment_tolerance_minutes = float(alignment_tolerance_minutes)
        self.width = int(width)
        self.minimum_dbz = float(minimum_dbz)
        self.frame_duration_ms = int(frame_duration_ms)

    def _filename(self, resource: DataResource) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", resource.identifier.lower()).strip("-")
        cadence = f"{self.frame_interval_minutes:g}".replace(".", "p")
        threshold = f"{self.minimum_dbz:g}".replace(".", "p")
        return (
            f"{slug}-conus-mosaic-nexrad-{cadence}min-"
            f"min{threshold}dbz-{self.width}px-{self.frame_duration_ms}ms.gif"
        )

    def item_destination(
        self,
        resource: DataResource,
        request: BatchRequest,
    ) -> BatchDestination:
        return BatchDestination(
            self.output_root,
            (self._filename(resource),),
            "National NEXRAD mosaic GIF is ready",
        )

    def workspace_destination(
        self,
        resources: tuple[DataResource, ...],
        request: BatchRequest,
    ) -> BatchDestination:
        return BatchDestination(
            self.output_root,
            tuple(self._filename(resource) for resource in resources),
            "All national NEXRAD mosaic GIFs are ready",
        )

    def run_item(
        self,
        resource: DataResource,
        source_data: NationalDay,
        request: BatchRequest,
        directory: Path,
    ) -> BatchResult:
        output = render_national_gif(
            source_data,
            directory / self._filename(resource),
            frame_interval_minutes=self.frame_interval_minutes,
            alignment_tolerance_minutes=self.alignment_tolerance_minutes,
            width=self.width,
            minimum_dbz=self.minimum_dbz,
            frame_duration_ms=self.frame_duration_ms,
        )
        return BatchResult((output,), "Rendered national NEXRAD mosaic GIF")

    def run_workspace(
        self,
        resources: tuple[DataResource, ...],
        open_resource,
        request: BatchRequest,
        directory: Path,
    ) -> BatchResult:
        outputs = tuple(
            render_national_gif(
                open_resource(resource),
                directory / self._filename(resource),
                frame_interval_minutes=self.frame_interval_minutes,
                alignment_tolerance_minutes=self.alignment_tolerance_minutes,
                width=self.width,
                minimum_dbz=self.minimum_dbz,
                frame_duration_ms=self.frame_duration_ms,
            )
            for resource in resources
        )
        return BatchResult(
            outputs,
            f"Rendered {len(outputs)} national NEXRAD mosaic GIFs",
        )


__all__ = [
    "RENDER_NATIONAL_GIF",
    "NationalGifBatch",
    "render_national_gif",
]
