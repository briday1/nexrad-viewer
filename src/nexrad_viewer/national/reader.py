"""Date discovery and nearest-time alignment for the national workspace."""

from __future__ import annotations

import warnings
from bisect import bisect_left
from datetime import date, datetime, timedelta
from pathlib import Path

from sigvue import DataResource, DiscoveryColumn, Reader

from ..formats.nexrad import NexradLevel3Sequence, discover_sequences
from .models import NationalDay, NationalFrame

NATIONAL_DISCOVERY_COLUMNS = (
    DiscoveryColumn("start", "Coverage start", "datetime"),
    DiscoveryColumn("end", "Coverage end", "datetime"),
    DiscoveryColumn("site_count", "Radar sites", "number"),
    DiscoveryColumn("scan_count", "Native scans", "number"),
)


def _date_directories(root: Path) -> tuple[tuple[date, Path], ...]:
    dated: list[tuple[date, Path]] = []
    if not root.is_dir():
        return ()
    for path in root.iterdir():
        if not path.is_dir():
            continue
        try:
            requested_date = date.fromisoformat(path.name)
        except ValueError:
            continue
        dated.append((requested_date, path))
    return tuple(sorted(dated))


def discover_days(root: str | Path) -> tuple[NationalDay, ...]:
    """Discover each top-level ISO date directory as one national dataset."""
    directory = Path(root).expanduser().resolve()
    days: list[NationalDay] = []
    for requested_date, date_directory in _date_directories(directory):
        try:
            sequences = tuple(
                sequence
                for sequence in discover_sequences(
                    date_directory,
                    recursive=False,
                )
                if sequence.product_id == "N0B"
            )
        except (OSError, ValueError) as error:
            warnings.warn(
                f"Skipping unreadable NEXRAD date folder {date_directory}: {error}",
                RuntimeWarning,
                stacklevel=2,
            )
            continue
        if sequences:
            days.append(NationalDay(requested_date, sequences))
    return tuple(days)


def _floor_time(value: datetime, interval_seconds: float) -> datetime:
    return value - timedelta(seconds=value.timestamp() % interval_seconds)


def _nearest_header(
    sequence: NexradLevel3Sequence,
    target: datetime,
    tolerance_seconds: float,
):
    times = tuple(header.scan_time for header in sequence.headers)
    position = bisect_left(times, target)
    candidates = tuple(
        sequence.headers[index]
        for index in (position - 1, position)
        if 0 <= index < len(sequence.headers)
    )
    if not candidates:
        return None
    nearest = min(
        candidates,
        key=lambda header: (
            abs((header.scan_time - target).total_seconds()),
            header.scan_time,
        ),
    )
    if abs((nearest.scan_time - target).total_seconds()) > tolerance_seconds:
        return None
    return nearest


def aligned_frames(
    day: NationalDay,
    *,
    interval_seconds: float,
    tolerance_seconds: float,
) -> tuple[NationalFrame, ...]:
    """Align each site to fixed target times using its nearest native scan."""
    if interval_seconds <= 0:
        raise ValueError("frame interval must be positive")
    if tolerance_seconds < 0:
        raise ValueError("alignment tolerance cannot be negative")
    first = _floor_time(day.start_time, interval_seconds)
    last = _floor_time(day.end_time, interval_seconds)
    count = int((last - first).total_seconds() // interval_seconds) + 1
    frames: list[NationalFrame] = []
    for index in range(count):
        target = first + timedelta(seconds=index * interval_seconds)
        headers = tuple(
            header
            for sequence in day.sequences
            if (
                header := _nearest_header(
                    sequence,
                    target,
                    tolerance_seconds,
                )
            )
            is not None
        )
        if headers:
            frames.append(
                NationalFrame(
                    target,
                    tuple(sorted(headers, key=lambda header: header.radar_id)),
                )
            )
    return tuple(frames)


def _describe(day: NationalDay) -> DataResource:
    return DataResource(
        identifier=day.date.isoformat(),
        title=f"{day.date.isoformat()} national reflectivity mosaic",
        source=day,
        subtitle=(
            f"{day.site_count} radar sites · {day.scan_count:,} native scans · "
            f"{day.start_time:%H:%M}–{day.end_time:%H:%M} UTC"
        ),
        timestamp=day.start_time,
        tags=("NOAA", "NEXRAD", "Level III", "national mosaic", "time aligned"),
        summary={
            "start": day.start_time.isoformat(),
            "end": day.end_time.isoformat(),
            "site_count": day.site_count,
            "scan_count": day.scan_count,
        },
    )


def national_day_reader(root: str | Path) -> Reader[NationalDay, NationalDay]:
    """Expose each date folder as one unopened national mosaic item."""
    directory = Path(root).expanduser().resolve()

    def revision(day: NationalDay):
        return tuple(
            (
                str(header.source_path),
                header.file_size_bytes,
                header.source_path.stat().st_mtime_ns,
            )
            for sequence in day.sequences
            for header in sequence.headers
        )

    return Reader(
        lambda: discover_days(directory),
        lambda day: day,
        describe=_describe,
        revision=revision,
    )


__all__ = [
    "NATIONAL_DISCOVERY_COLUMNS",
    "aligned_frames",
    "discover_days",
    "national_day_reader",
]
