"""Bind exact NEXRAD scan sequences to Sigvue."""

from __future__ import annotations

from pathlib import Path

from sigvue import Segment, Workspace
from sigvue.helpers import WorkspaceConfig

from .batch import NexradGifBatch
from .formats.nexrad import (
    NexradLevel3Sequence,
    NexradSequenceSelection,
    open_scan,
)
from .reader import NEXRAD_DISCOVERY_COLUMNS, level3_sequence_reader
from .view import view


def _segment_durations(sequence: NexradLevel3Sequence) -> tuple[float, ...]:
    elapsed = sequence.elapsed_seconds
    return tuple(
        elapsed[index + 1] - elapsed[index]
        if index + 1 < sequence.scan_count
        else sequence.nominal_interval_seconds
        for index in range(sequence.scan_count)
    )


def _segments(sequence: NexradLevel3Sequence) -> tuple[Segment, ...]:
    elapsed = sequence.elapsed_seconds
    durations = _segment_durations(sequence)
    return tuple(
        Segment(
            identifier=header.source_path.name,
            start_seconds=elapsed[index],
            duration_seconds=durations[index],
            label=f"{header.scan_time:%H:%M:%S} UTC",
        )
        for index, header in enumerate(sequence.headers)
    )


def _duration(sequence: NexradLevel3Sequence) -> float:
    return sequence.elapsed_seconds[-1] + _segment_durations(sequence)[-1]


def _read_scan(
    sequence: NexradLevel3Sequence,
    segment: Segment,
) -> NexradSequenceSelection:
    index = next(
        index
        for index, header in enumerate(sequence.headers)
        if header.source_path.name == segment.identifier
    )
    return NexradSequenceSelection(
        sequence=sequence,
        scan_index=index,
        scan=open_scan(sequence.headers[index].source_path),
    )


def create_reader(root: str | Path):
    """Create a segmented sequence reader that loads only the selected scan."""
    return level3_sequence_reader(root).segmented(
        _read_scan,
        duration=_duration,
        segments=_segments,
        time_unit="min",
    )


def create_workspace(config) -> Workspace:
    """Create the date-folder-preserving individual-site workspace."""
    values = WorkspaceConfig(config)
    return Workspace(
        identifier="weather-radar",
        name="NOAA Weather Radar",
        description=(
            "Explore exact native gates from NOAA NEXRAD Level III "
            "base-reflectivity sequences, with segmented time navigation "
            "and display-only PPI resampling."
        ),
        reader=create_reader(values.path("data_root")),
        view=view,
        batch=NexradGifBatch(
            values.path("output_root"),
            frame_duration_ms=values.integer(
                "gif_frame_duration_ms",
                200,
            ),
        ),
        category="weather radar",
        tags=("NOAA", "NEXRAD", "Level III", "base reflectivity"),
        discovery_columns=NEXRAD_DISCOVERY_COLUMNS,
        flatten_discovery=False,
    )


__all__ = ["create_reader", "create_workspace"]
