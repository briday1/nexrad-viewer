"""Display-only national mosaic computation from exact native radar gates."""

from __future__ import annotations

from math import cos, radians

import numpy as np

from ..analysis import EARTH_KM_PER_DEGREE, sample_scan_at_offsets
from ..formats.nexrad import NexradLevel3Radial

CONUS_BOUNDS = (-126.0, -66.0, 24.0, 50.0)


def mosaic_shape(
    width: int,
    *,
    bounds: tuple[float, float, float, float] = CONUS_BOUNDS,
) -> tuple[int, int]:
    """Choose a physically sensible map height for a requested width."""
    if width < 128:
        raise ValueError("national mosaic width must be at least 128 pixels")
    west, east, south, north = bounds
    mean_latitude = (south + north) / 2.0
    physical_width = (east - west) * cos(radians(mean_latitude))
    physical_height = north - south
    height = max(64, round(width * physical_height / physical_width))
    return width, height


def national_mosaic_grid(
    scans: tuple[NexradLevel3Radial, ...],
    *,
    width: int,
    height: int | None = None,
    maximum_range_km: float,
    bounds: tuple[float, float, float, float] = CONUS_BOUNDS,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build a max-reflectivity mosaic without interpolating source values."""
    if maximum_range_km <= 0:
        raise ValueError("mosaic radar radius must be positive")
    west, east, south, north = bounds
    if height is None:
        width, height = mosaic_shape(width, bounds=bounds)
    elif height < 64:
        raise ValueError("national mosaic height must be at least 64 pixels")
    longitude_edges = np.linspace(west, east, width + 1, dtype=np.float64)
    latitude_edges = np.linspace(south, north, height + 1, dtype=np.float64)
    longitudes = (longitude_edges[:-1] + longitude_edges[1:]) / 2.0
    latitudes = (latitude_edges[:-1] + latitude_edges[1:]) / 2.0
    mosaic = np.full((height, width), np.nan, dtype=np.float32)

    for scan in scans:
        latitude = scan.header.latitude_deg
        longitude = scan.header.longitude_deg
        radius = min(
            maximum_range_km,
            float(scan.ground_range_edges_km[-1]),
        )
        latitude_span = radius / EARTH_KM_PER_DEGREE
        longitude_span = radius / (
            EARTH_KM_PER_DEGREE * max(0.1, cos(radians(latitude)))
        )
        x0 = max(0, int(np.searchsorted(longitudes, longitude - longitude_span)))
        x1 = min(
            width,
            int(np.searchsorted(longitudes, longitude + longitude_span, side="right")),
        )
        y0 = max(0, int(np.searchsorted(latitudes, latitude - latitude_span)))
        y1 = min(
            height,
            int(np.searchsorted(latitudes, latitude + latitude_span, side="right")),
        )
        if x0 >= x1 or y0 >= y1:
            continue
        local_longitudes, local_latitudes = np.meshgrid(
            longitudes[x0:x1],
            latitudes[y0:y1],
        )
        east_km = (
            (local_longitudes - longitude)
            * EARTH_KM_PER_DEGREE
            * cos(radians(latitude))
        )
        north_km = (local_latitudes - latitude) * EARTH_KM_PER_DEGREE
        sampled = sample_scan_at_offsets(
            scan,
            east_km=east_km,
            north_km=north_km,
        )
        sampled[np.hypot(east_km, north_km) > radius] = np.nan
        target = mosaic[y0:y1, x0:x1]
        measured = np.isfinite(sampled)
        missing = measured & ~np.isfinite(target)
        overlap = measured & np.isfinite(target)
        target[missing] = sampled[missing]
        target[overlap] = np.maximum(target[overlap], sampled[overlap])
    return longitudes, latitudes, mosaic


__all__ = [
    "CONUS_BOUNDS",
    "EARTH_KM_PER_DEGREE",
    "mosaic_shape",
    "national_mosaic_grid",
]
