"""Typed, framework-neutral models for NEXRAD Level III radial products."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median

import numpy as np


BELOW_THRESHOLD_CODE = 0
RANGE_FOLDED_CODE = 1
FIRST_MEASURED_CODE = 2


@dataclass(frozen=True)
class NexradLevel3Header:
    """Metadata stored in the WMO and Product Description headers."""

    source_path: Path
    file_size_bytes: int
    wmo_heading: str
    product_id: str
    radar_id: str
    message_code: int
    message_length_bytes: int
    message_time: datetime
    scan_time: datetime
    generation_time: datetime
    latitude_deg: float
    longitude_deg: float
    altitude_ft: int
    operational_mode: int
    volume_coverage_pattern: int
    sequence_number: int
    volume_scan_number: int
    elevation_number: int
    elevation_deg: float
    minimum_value_dbz: float
    value_increment_dbz: float
    measured_level_count: int
    maximum_value_dbz: float
    compression_method: int
    uncompressed_payload_bytes: int
    product_version: int
    spot_blank: bool
    symbology_offset_halfwords: int


@dataclass(frozen=True)
class NexradLevel3Radial:
    """One exact native Level III polar field.

    ``level_codes`` retains every unsigned byte from packet 16. Codes zero
    and one therefore remain distinct from measured values instead of being
    collapsed into a shared floating-point sentinel. ``radial_gate_counts``
    identifies native gates when a future product contains padded rows.
    """

    header: NexradLevel3Header
    packet_code: int
    first_range_bin: int
    gate_size_km: float
    i_center_km: float
    j_center_km: float
    ground_range_scale: float
    level_codes: np.ndarray
    radial_gate_counts: np.ndarray
    azimuth_start_deg: np.ndarray
    azimuth_width_deg: np.ndarray

    @property
    def radial_count(self) -> int:
        return int(self.level_codes.shape[0])

    @property
    def range_bin_count(self) -> int:
        return int(self.level_codes.shape[1])

    @property
    def gate_count(self) -> int:
        return int(np.sum(self.radial_gate_counts, dtype=np.int64))

    @property
    def slant_range_edges_km(self) -> np.ndarray:
        """Native slant-range gate edges derived from the encoded bin index."""
        start = self.first_range_bin * self.gate_size_km
        return (
            start
            + np.arange(
                self.range_bin_count + 1,
                dtype=np.float64,
            )
            * self.gate_size_km
        )

    @property
    def slant_range_centers_km(self) -> np.ndarray:
        edges = self.slant_range_edges_km
        return (edges[:-1] + edges[1:]) / 2.0

    @property
    def ground_range_edges_km(self) -> np.ndarray:
        return self.slant_range_edges_km * self.ground_range_scale

    @property
    def ground_range_centers_km(self) -> np.ndarray:
        return self.slant_range_centers_km * self.ground_range_scale

    @property
    def azimuth_center_deg(self) -> np.ndarray:
        return (self.azimuth_start_deg + self.azimuth_width_deg / 2.0) % 360.0

    @property
    def buffer_nbytes(self) -> int:
        """Resident native gate and coordinate buffers loaded by the reader."""
        return (
            int(self.level_codes.nbytes)
            + int(self.radial_gate_counts.nbytes)
            + int(self.azimuth_start_deg.nbytes)
            + int(self.azimuth_width_deg.nbytes)
            + int(self.slant_range_edges_km.nbytes)
            + 120  # Message and Product Description header bytes.
        )

    def valid_gate_mask(self) -> np.ndarray:
        bins = np.arange(self.range_bin_count, dtype=np.int32)
        return bins[np.newaxis, :] < self.radial_gate_counts[:, np.newaxis]

    def measured_gate_mask(self) -> np.ndarray:
        return self.valid_gate_mask() & (self.level_codes >= FIRST_MEASURED_CODE)

    def reflectivity_dbz(self) -> np.ndarray:
        """Decode measured gates exactly; categorical gates remain NaN."""
        result = np.full(self.level_codes.shape, np.nan, dtype=np.float32)
        measured = self.measured_gate_mask()
        result[measured] = (
            self.header.minimum_value_dbz
            + (self.level_codes[measured].astype(np.float32) - FIRST_MEASURED_CODE)
            * self.header.value_increment_dbz
        )
        return result

    def radial_reflectivity_dbz(self, radial_index: int) -> np.ndarray:
        """Decode one native radial without allocating the full float field."""
        if not 0 <= radial_index < self.radial_count:
            raise IndexError("radial index is outside the product")
        count = int(self.radial_gate_counts[radial_index])
        codes = self.level_codes[radial_index, :count]
        result = np.full(count, np.nan, dtype=np.float32)
        measured = codes >= FIRST_MEASURED_CODE
        result[measured] = (
            self.header.minimum_value_dbz
            + (codes[measured].astype(np.float32) - FIRST_MEASURED_CODE)
            * self.header.value_increment_dbz
        )
        return result

    def code_counts(self) -> dict[str, int]:
        valid = self.valid_gate_mask()
        codes = self.level_codes
        return {
            "measured": int(np.count_nonzero(valid & (codes >= FIRST_MEASURED_CODE))),
            "below_threshold": int(
                np.count_nonzero(valid & (codes == BELOW_THRESHOLD_CODE))
            ),
            "range_folded": int(np.count_nonzero(valid & (codes == RANGE_FOLDED_CODE))),
            "padding": int(codes.size - np.count_nonzero(valid)),
        }


@dataclass(frozen=True)
class NexradLevel3Sequence:
    """Chronological scans sharing one radar site and product."""

    headers: tuple[NexradLevel3Header, ...]

    def __post_init__(self) -> None:
        if not self.headers:
            raise ValueError("A NEXRAD sequence requires at least one scan")
        identity = {(header.radar_id, header.product_id) for header in self.headers}
        if len(identity) != 1:
            raise ValueError("A NEXRAD sequence must use one radar and product")
        times = tuple(header.scan_time for header in self.headers)
        if times != tuple(sorted(times)) or len(times) != len(set(times)):
            raise ValueError("NEXRAD sequence scan times must be strictly increasing")

    def __str__(self) -> str:
        directory = self.headers[0].source_path.parent
        return (
            f"{directory} ({self.radar_id} {self.product_id}, {self.scan_count} scans)"
        )

    @property
    def radar_id(self) -> str:
        return self.headers[0].radar_id

    @property
    def product_id(self) -> str:
        return self.headers[0].product_id

    @property
    def scan_count(self) -> int:
        return len(self.headers)

    @property
    def elapsed_seconds(self) -> tuple[float, ...]:
        start = self.headers[0].scan_time
        return tuple(
            (header.scan_time - start).total_seconds() for header in self.headers
        )

    @property
    def nominal_interval_seconds(self) -> float:
        elapsed = self.elapsed_seconds
        if len(elapsed) == 1:
            return 1.0
        return float(
            median(
                stop - start
                for start, stop in zip(
                    elapsed[:-1],
                    elapsed[1:],
                    strict=True,
                )
            )
        )


@dataclass(frozen=True)
class NexradSequenceSelection:
    """One exact scan selected from a chronological sequence."""

    sequence: NexradLevel3Sequence
    scan_index: int
    scan: NexradLevel3Radial


__all__ = [
    "BELOW_THRESHOLD_CODE",
    "FIRST_MEASURED_CODE",
    "NexradLevel3Header",
    "NexradLevel3Radial",
    "NexradLevel3Sequence",
    "NexradSequenceSelection",
    "RANGE_FOLDED_CODE",
]
