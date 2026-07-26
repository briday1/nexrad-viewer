"""Exact NEXRAD computations shared by interactive and batch views."""

from __future__ import annotations

from functools import lru_cache
from math import ceil, cos, radians
from pathlib import Path

import numpy as np

from .formats.nexrad import (
    FIRST_MEASURED_CODE,
    NexradLevel3Radial,
    NexradLevel3Sequence,
    open_scan,
)

EARTH_KM_PER_DEGREE = 111.195


def automatic_reflectivity_limits(
    scans: NexradLevel3Radial | tuple[NexradLevel3Radial, ...],
) -> tuple[float, float]:
    """Return robust display limits rounded outward to 5 dBZ."""
    collection = (scans,) if isinstance(scans, NexradLevel3Radial) else scans
    measured_values: list[np.ndarray] = []
    for scan in collection:
        measured = scan.level_codes[scan.measured_gate_mask()]
        if measured.size:
            measured_values.append(
                scan.header.minimum_value_dbz
                + (measured.astype(np.float32) - FIRST_MEASURED_CODE)
                * scan.header.value_increment_dbz
            )
    if not measured_values:
        return -20.0, 75.0
    values = np.concatenate(measured_values)
    lower = max(-20.0, 5.0 * np.floor(np.percentile(values, 1.0) / 5.0))
    upper = min(75.0, 5.0 * np.ceil(np.percentile(values, 99.9) / 5.0))
    if upper - lower < 20.0:
        center = float(np.median(values))
        lower = max(-20.0, 5.0 * np.floor((center - 10.0) / 5.0))
        upper = min(75.0, lower + 20.0)
        lower = max(-20.0, upper - 20.0)
    return float(lower), float(upper)


def cartesian_display_grid(
    scan: NexradLevel3Radial,
    *,
    maximum_range_km: float,
    pixels: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Resample exact polar gates at Cartesian pixel centers for display only."""
    if maximum_range_km <= 0 or pixels < 32:
        raise ValueError("display range and pixel count must be positive")
    edges = np.linspace(
        -maximum_range_km,
        maximum_range_km,
        pixels + 1,
        dtype=np.float64,
    )
    centers = (edges[:-1] + edges[1:]) / 2.0
    x, y = np.meshgrid(centers, centers)
    dbz = sample_scan_at_offsets(scan, east_km=x, north_km=y)
    dbz[np.hypot(x, y) > maximum_range_km] = np.nan
    return centers, centers, dbz


def sample_scan_at_offsets(
    scan: NexradLevel3Radial,
    *,
    east_km: np.ndarray,
    north_km: np.ndarray,
) -> np.ndarray:
    """Sample native polar gates at requested horizontal coordinates.

    The result uses nearest native radial/gate lookup without interpolating
    scientific values. This is the shared display primitive for both an
    individual site's Cartesian PPI and the national mosaic.
    """
    if east_km.shape != north_km.shape:
        raise ValueError("east and north coordinate arrays must have equal shapes")
    ground_range = np.hypot(east_km, north_km)
    slant_range = ground_range / scan.ground_range_scale
    azimuth = np.degrees(np.arctan2(east_km, north_km)) % 360.0

    order = np.argsort(scan.azimuth_start_deg)
    ordered_starts = scan.azimuth_start_deg[order]
    position = np.searchsorted(ordered_starts, azimuth, side="right") - 1
    position[position < 0] = len(order) - 1
    radial = order[position]
    angular_offset = (azimuth - scan.azimuth_start_deg[radial]) % 360.0
    covered = angular_offset <= scan.azimuth_width_deg[radial] + 1e-6

    gate = np.floor(
        (slant_range - scan.first_range_bin * scan.gate_size_km) / scan.gate_size_km
    ).astype(np.int32)
    valid = covered & (gate >= 0) & (gate < scan.range_bin_count)
    safe_gate = np.clip(gate, 0, scan.range_bin_count - 1)
    valid &= safe_gate < scan.radial_gate_counts[radial]
    codes = scan.level_codes[radial, safe_gate]
    measured = valid & (codes >= FIRST_MEASURED_CODE)
    dbz = np.full(east_km.shape, np.nan, dtype=np.float32)
    dbz[measured] = (
        scan.header.minimum_value_dbz
        + (codes[measured].astype(np.float32) - FIRST_MEASURED_CODE)
        * scan.header.value_increment_dbz
    )
    return dbz


def geographic_display_grid(
    scan: NexradLevel3Radial,
    *,
    bounds: tuple[float, float, float, float],
    width: int,
    height: int,
    maximum_range_km: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Sample a native scan directly over the currently visible map bounds."""
    if width < 32 or height < 32 or maximum_range_km <= 0:
        raise ValueError("map dimensions and radar radius must be positive")
    west, east, south, north = bounds
    longitude_edges = np.linspace(west, east, width + 1, dtype=np.float64)
    latitude_edges = np.linspace(south, north, height + 1, dtype=np.float64)
    longitudes = (longitude_edges[:-1] + longitude_edges[1:]) / 2.0
    latitudes = (latitude_edges[:-1] + latitude_edges[1:]) / 2.0
    longitude_grid, latitude_grid = np.meshgrid(longitudes, latitudes)
    east_km = (
        (longitude_grid - scan.header.longitude_deg)
        * EARTH_KM_PER_DEGREE
        * cos(radians(scan.header.latitude_deg))
    )
    north_km = (latitude_grid - scan.header.latitude_deg) * EARTH_KM_PER_DEGREE
    dbz = sample_scan_at_offsets(
        scan,
        east_km=east_km,
        north_km=north_km,
    )
    dbz[np.hypot(east_km, north_km) > maximum_range_km] = np.nan
    return longitudes, latitudes, dbz


def measured_histogram(
    scan: NexradLevel3Radial,
) -> tuple[np.ndarray, np.ndarray, float, float, float]:
    """Return exact measured-bin coordinates, counts, and encoded bounds."""
    valid = scan.valid_gate_mask()
    measured_codes = scan.level_codes[valid & (scan.level_codes >= FIRST_MEASURED_CODE)]
    code_counts = np.bincount(measured_codes, minlength=256)
    present_codes = (
        np.flatnonzero(code_counts[FIRST_MEASURED_CODE:]) + FIRST_MEASURED_CODE
    )
    increment = scan.header.value_increment_dbz
    measured_min = scan.header.minimum_value_dbz
    measured_max = measured_min + (255 - FIRST_MEASURED_CODE) * increment
    histogram_dbz = measured_min + (present_codes - FIRST_MEASURED_CODE) * increment
    return (
        histogram_dbz,
        code_counts[present_codes],
        increment,
        measured_min,
        measured_max,
    )


def _sequence_revision(
    sequence: NexradLevel3Sequence,
) -> tuple[tuple[str, int, int], ...]:
    return tuple(
        (
            str(header.source_path.resolve()),
            header.source_path.stat().st_size,
            header.source_path.stat().st_mtime_ns,
        )
        for header in sequence.headers
    )


@lru_cache(maxsize=64)
def _sequence_histogram_peak(
    revision: tuple[tuple[str, int, int], ...],
) -> int:
    peak = 0
    for filename, _, _ in revision:
        _, counts, _, _, _ = measured_histogram(open_scan(Path(filename)))
        if counts.size:
            peak = max(peak, int(np.max(counts)))
    return peak


def sequence_histogram_count_upper(
    sequence: NexradLevel3Sequence,
) -> int:
    """Return one exact, stable sequence-wide count limit with 5% headroom."""
    peak = _sequence_histogram_peak(_sequence_revision(sequence))
    return max(1, ceil(peak * 1.05))


__all__ = [
    "EARTH_KM_PER_DEGREE",
    "cartesian_display_grid",
    "geographic_display_grid",
    "measured_histogram",
    "sample_scan_at_offsets",
    "sequence_histogram_count_upper",
]
