"""Headless NEXRAD Level III discovery, inspection, and exact scan I/O."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .._discovery import discover_files
from .models import (
    BELOW_THRESHOLD_CODE,
    FIRST_MEASURED_CODE,
    NexradLevel3Header,
    NexradLevel3Radial,
    NexradLevel3Sequence,
    NexradSequenceSelection,
    RANGE_FOLDED_CODE,
)
from .reader import (
    NexradFormatError,
    PACKET_CODE_DIGITAL_RADIAL,
    PRODUCT_CODE_SUPER_RESOLUTION_REFLECTIVITY,
    read_level3_header,
    read_level3_radial,
)


DEFAULT_PATTERNS = ("*_N?B_*", "*.nids", "*.nids.gz")


def discover(
    root: str | Path,
    *,
    pattern: str | tuple[str, ...] = DEFAULT_PATTERNS,
    recursive: bool = True,
) -> tuple[Path, ...]:
    """Discover supported Level III files without inflating their payloads."""
    return discover_files(root, pattern, recursive=recursive)


def inspect(path: str | Path) -> NexradLevel3Header:
    """Read only the fixed Level III metadata headers."""
    return read_level3_header(path)


def open(path: str | Path) -> NexradLevel3Radial:
    """Read one validated radial product, preserving every native gate code."""
    return read_level3_radial(path)


open_scan = open


def discover_sequences(
    root: str | Path,
    *,
    pattern: str | tuple[str, ...] = DEFAULT_PATTERNS,
    recursive: bool = True,
) -> tuple[NexradLevel3Sequence, ...]:
    """Group discovered headers by directory, radar, and product in time order."""
    groups: dict[
        tuple[Path, str, str],
        list[NexradLevel3Header],
    ] = defaultdict(list)
    for path in discover(root, pattern=pattern, recursive=recursive):
        header = inspect(path)
        groups[(path.parent, header.radar_id, header.product_id)].append(header)
    return tuple(
        NexradLevel3Sequence(
            tuple(sorted(headers, key=lambda header: header.scan_time))
        )
        for _, headers in sorted(
            groups.items(),
            key=lambda item: (
                item[0][0].as_posix(),
                item[0][1],
                item[0][2],
            ),
        )
    )


def read_window(
    sequence: NexradLevel3Sequence,
    start: int = 0,
    count: int | None = None,
) -> tuple[NexradLevel3Radial, ...]:
    """Read a bounded scan window from a discovered chronological sequence."""
    if isinstance(start, bool) or not isinstance(start, int):
        raise TypeError("NEXRAD scan-window start must be an integer")
    if count is not None and (
        isinstance(count, bool) or not isinstance(count, int)
    ):
        raise TypeError("NEXRAD scan-window count must be an integer")
    if start < 0 or start > sequence.scan_count:
        raise ValueError("NEXRAD scan-window start is outside the sequence")
    requested = sequence.scan_count - start if count is None else count
    if requested < 0:
        raise ValueError("NEXRAD scan-window count cannot be negative")
    stop = min(sequence.scan_count, start + requested)
    return tuple(
        open(header.source_path)
        for header in sequence.headers[start:stop]
    )


__all__ = [
    "BELOW_THRESHOLD_CODE",
    "DEFAULT_PATTERNS",
    "FIRST_MEASURED_CODE",
    "NexradFormatError",
    "NexradLevel3Header",
    "NexradLevel3Radial",
    "NexradLevel3Sequence",
    "NexradSequenceSelection",
    "PACKET_CODE_DIGITAL_RADIAL",
    "PRODUCT_CODE_SUPER_RESOLUTION_REFLECTIVITY",
    "RANGE_FOLDED_CODE",
    "discover",
    "discover_sequences",
    "inspect",
    "open",
    "open_scan",
    "read_level3_header",
    "read_level3_radial",
    "read_window",
]
