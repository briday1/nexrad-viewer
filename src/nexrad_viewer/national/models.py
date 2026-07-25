"""Small framework-neutral models for date-oriented national mosaics."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from ..formats.nexrad import (
    NexradLevel3Header,
    NexradLevel3Radial,
    NexradLevel3Sequence,
)


@dataclass(frozen=True)
class NationalDay:
    """All discoverable radar sequences stored in one ISO-date directory."""

    date: date
    sequences: tuple[NexradLevel3Sequence, ...]

    def __post_init__(self) -> None:
        if not self.sequences:
            raise ValueError("A national radar day requires at least one sequence")
        if len({sequence.radar_id for sequence in self.sequences}) != len(
            self.sequences
        ):
            raise ValueError("A national radar day requires one sequence per site")

    @property
    def site_count(self) -> int:
        return len(self.sequences)

    @property
    def scan_count(self) -> int:
        return sum(sequence.scan_count for sequence in self.sequences)

    @property
    def start_time(self) -> datetime:
        return min(sequence.headers[0].scan_time for sequence in self.sequences)

    @property
    def end_time(self) -> datetime:
        return max(sequence.headers[-1].scan_time for sequence in self.sequences)


@dataclass(frozen=True)
class NationalFrame:
    """Nearest scan from every available site for one common target time."""

    target_time: datetime
    headers: tuple[NexradLevel3Header, ...]

    @property
    def site_count(self) -> int:
        return len(self.headers)

    @property
    def maximum_offset_seconds(self) -> float:
        return max(
            (
                abs((header.scan_time - self.target_time).total_seconds())
                for header in self.headers
            ),
            default=0.0,
        )


@dataclass(frozen=True)
class NationalFrameSelection:
    """The exact native scans loaded for one synchronized national frame."""

    day: NationalDay
    frame_index: int
    frame_count: int
    frame: NationalFrame
    scans: tuple[NexradLevel3Radial, ...]

    @property
    def buffer_nbytes(self) -> int:
        return sum(scan.buffer_nbytes for scan in self.scans)


__all__ = ["NationalDay", "NationalFrame", "NationalFrameSelection"]
