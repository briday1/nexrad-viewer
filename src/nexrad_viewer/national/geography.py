"""Bundled, official U.S. Census state outlines for the CONUS map."""

from __future__ import annotations

import gzip
import json
from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=1)
def state_boundaries() -> tuple[tuple[tuple[float, float], ...], ...]:
    """Return exterior and interior rings from the bundled Census GeoJSON."""
    resource = files("nexrad_viewer.national").joinpath("us_states.geojson.gz")
    with resource.open("rb") as stream:
        payload = json.loads(gzip.decompress(stream.read()))
    rings: list[tuple[tuple[float, float], ...]] = []
    for feature in payload["features"]:
        geometry = feature["geometry"]
        coordinates = geometry["coordinates"]
        polygons = coordinates if geometry["type"] == "MultiPolygon" else (coordinates,)
        for polygon in polygons:
            for ring in polygon:
                rings.append(tuple((float(lon), float(lat)) for lon, lat in ring))
    return tuple(rings)


def boundary_trace_coordinates() -> tuple[
    tuple[float | None, ...], tuple[float | None, ...]
]:
    """Flatten rings into Plotly line coordinates with explicit breaks."""
    longitudes: list[float | None] = []
    latitudes: list[float | None] = []
    for ring in state_boundaries():
        longitudes.extend(point[0] for point in ring)
        latitudes.extend(point[1] for point in ring)
        longitudes.append(None)
        latitudes.append(None)
    return tuple(longitudes), tuple(latitudes)


__all__ = ["boundary_trace_coordinates", "state_boundaries"]
