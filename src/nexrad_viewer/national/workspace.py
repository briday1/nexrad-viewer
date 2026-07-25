"""Bind date folders and synchronized national frames to Sigvue."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from sigvue import Segment, Workspace
from sigvue.helpers import WorkspaceConfig

from ..formats.nexrad import open_scan
from .batch import NationalGifBatch
from .models import NationalDay, NationalFrameSelection
from .reader import (
    NATIONAL_DISCOVERY_COLUMNS,
    aligned_frames,
    national_day_reader,
)
from .view import view


def create_reader(
    root: str | Path,
    *,
    frame_interval_minutes: float,
    alignment_tolerance_minutes: float,
):
    """Load only the nearest exact scans needed by the selected map frame."""
    interval_seconds = frame_interval_minutes * 60.0
    tolerance_seconds = alignment_tolerance_minutes * 60.0

    @lru_cache(maxsize=32)
    def frames(day: NationalDay):
        return aligned_frames(
            day,
            interval_seconds=interval_seconds,
            tolerance_seconds=tolerance_seconds,
        )

    def segments(day: NationalDay) -> tuple[Segment, ...]:
        aligned = frames(day)
        first = aligned[0].target_time
        return tuple(
            Segment(
                identifier=frame.target_time.isoformat(),
                start_seconds=(frame.target_time - first).total_seconds(),
                duration_seconds=interval_seconds,
                label=f"{frame.target_time:%H:%M} UTC",
            )
            for frame in aligned
        )

    def duration(day: NationalDay) -> float:
        aligned = frames(day)
        if not aligned:
            return interval_seconds
        return (
            aligned[-1].target_time - aligned[0].target_time
        ).total_seconds() + interval_seconds

    def read(day: NationalDay, segment: Segment) -> NationalFrameSelection:
        aligned = frames(day)
        index = next(
            index
            for index, frame in enumerate(aligned)
            if frame.target_time.isoformat() == segment.identifier
        )
        frame = aligned[index]
        return NationalFrameSelection(
            day=day,
            frame_index=index,
            frame_count=len(aligned),
            frame=frame,
            scans=tuple(open_scan(header.source_path) for header in frame.headers),
        )

    return national_day_reader(root).segmented(
        read,
        duration=duration,
        segments=segments,
        time_unit="h",
    )


def create_workspace(config) -> Workspace:
    values = WorkspaceConfig(config)
    interval_minutes = values.floating("national_frame_interval_minutes", 60.0)
    tolerance_minutes = values.floating(
        "national_alignment_tolerance_minutes",
        30.0,
    )
    return Workspace(
        identifier="national-weather-radar",
        name="CONUS Radar Mosaic",
        description=(
            "View each ISO-date folder as time-aligned national base "
            "reflectivity frames assembled from exact native NOAA scans."
        ),
        reader=create_reader(
            values.path("data_root"),
            frame_interval_minutes=interval_minutes,
            alignment_tolerance_minutes=tolerance_minutes,
        ),
        view=view,
        batch=NationalGifBatch(
            values.path("output_root"),
            frame_interval_minutes=interval_minutes,
            alignment_tolerance_minutes=tolerance_minutes,
            width=values.integer("national_gif_width", 1200),
            frame_duration_ms=values.integer(
                "national_gif_frame_duration_ms",
                250,
            ),
        ),
        category="weather radar",
        tags=("NOAA", "NEXRAD", "Level III", "CONUS", "mosaic"),
        discovery_columns=NATIONAL_DISCOVERY_COLUMNS,
        flatten_discovery=True,
    )


__all__ = ["create_reader", "create_workspace"]
