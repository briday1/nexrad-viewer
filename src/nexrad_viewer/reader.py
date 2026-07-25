"""NEXRAD Level III catalog descriptions and workspace readers."""

from __future__ import annotations

from pathlib import Path

from sigvue import DataResource, DiscoveryColumn, Files, Reader

from .formats.nexrad import (
    DEFAULT_PATTERNS,
    NexradLevel3Radial,
    NexradLevel3Sequence,
    discover_sequences,
    read_level3_header,
    read_level3_radial,
)

NEXRAD_DISCOVERY_COLUMNS = (
    DiscoveryColumn("start", "Sequence start", "datetime"),
    DiscoveryColumn("end", "Sequence end", "datetime"),
    DiscoveryColumn("scan_count", "Scans", "number"),
)


def describe_level3(path: Path) -> DataResource:
    header = read_level3_header(path)
    timestamp = header.scan_time
    return DataResource(
        identifier=path.name,
        title=(
            f"{header.radar_id} {header.product_id} · {timestamp:%Y-%m-%d %H:%M:%S} UTC"
        ),
        source=path,
        subtitle=(
            "NOAA NEXRAD Level III base reflectivity · "
            f"{header.elevation_deg:g}° elevation"
        ),
        timestamp=timestamp,
        tags=("NOAA", "NEXRAD", "Level III", header.product_id),
        summary={
            "start": timestamp.isoformat(),
            "end": timestamp.isoformat(),
            "scan_count": 1,
        },
    )


def level3_reader(root: str | Path) -> Files[NexradLevel3Radial]:
    """Create an exact reader for individual Level III scans."""
    return Files(
        root,
        DEFAULT_PATTERNS,
        read_level3_radial,
        describe=describe_level3,
    )


def _describe_sequence(
    root: Path,
    sequence: NexradLevel3Sequence,
) -> DataResource:
    directory = sequence.headers[0].source_path.parent
    relative = directory.relative_to(root)
    navigation_path = () if relative == Path(".") else relative.parts
    start = sequence.headers[0].scan_time
    stop = sequence.headers[-1].scan_time
    prefix = f"{relative.as_posix()}::" if navigation_path else ""
    return DataResource(
        identifier=f"{prefix}{sequence.radar_id}-{sequence.product_id}",
        title=(f"{sequence.radar_id} {sequence.product_id} reflectivity sequence"),
        source=sequence,
        subtitle=(
            f"{sequence.scan_count} scans · "
            f"{start:%Y-%m-%d %H:%M:%S} to {stop:%H:%M:%S} UTC"
        ),
        timestamp=start,
        tags=(
            "NOAA",
            "NEXRAD",
            "Level III",
            sequence.product_id,
            "time sequence",
        ),
        summary={
            "start": start.isoformat(),
            "end": stop.isoformat(),
            "scan_count": sequence.scan_count,
        },
        navigation_path=navigation_path,
    )


def level3_sequence_reader(
    root: str | Path,
) -> Reader[NexradLevel3Sequence, NexradLevel3Sequence]:
    """Group chronological scans by directory, radar, and product."""
    directory = Path(root).expanduser().resolve()

    def revision(sequence: NexradLevel3Sequence):
        return tuple(
            (
                str(header.source_path),
                header.file_size_bytes,
                header.source_path.stat().st_mtime_ns,
            )
            for header in sequence.headers
        )

    return Reader(
        lambda: discover_sequences(directory),
        lambda sequence: sequence,
        describe=lambda sequence: _describe_sequence(directory, sequence),
        revision=revision,
    )


__all__ = [
    "NEXRAD_DISCOVERY_COLUMNS",
    "describe_level3",
    "level3_reader",
    "level3_sequence_reader",
]
