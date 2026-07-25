"""Pure Plotly builders for exact and display-resampled radar views."""

from __future__ import annotations

from math import cos, radians

import plotly.graph_objects as go
from plotly import colors as plotly_colors
from sigvue import add_viewport_heatmap

from .analysis import (
    EARTH_KM_PER_DEGREE,
    geographic_display_grid,
    measured_histogram,
    sequence_histogram_count_upper,
)
from .formats.nexrad import NexradLevel3Radial, NexradSequenceSelection
from .national.geography import boundary_trace_coordinates
from .style import COLORS, style_plotly

NEXRAD_COLORSCALE = (
    (0.00, "#646464"),
    (0.15, "#04e9e7"),
    (0.28, "#019ff4"),
    (0.40, "#0300f4"),
    (0.48, "#02fd02"),
    (0.58, "#01c501"),
    (0.66, "#008e00"),
    (0.72, "#fdf802"),
    (0.78, "#e5bc00"),
    (0.84, "#fd9500"),
    (0.89, "#fd0000"),
    (0.94, "#d40000"),
    (0.97, "#bc0000"),
    (1.00, "#f800fd"),
)


def _register_nexrad_preview() -> None:
    """Make the exact custom scale available to Sigvue's visual picker."""
    sampled = plotly_colors.sample_colorscale(
        [list(stop) for stop in NEXRAD_COLORSCALE],
        [index / 100 for index in range(101)],
        colortype="rgb",
    )
    plotly_colors.sequential.NEXRAD = sampled


_register_nexrad_preview()

REFLECTIVITY_COLORMAPS = (
    "NEXRAD",
    "Turbo",
    "Viridis",
    "Cividis",
    "Plasma",
    "Inferno",
    "Magma",
    "Jet",
    "Rainbow",
    "Portland",
    "Hot",
)


def ppi_figure(
    scan: NexradLevel3Radial,
    *,
    maximum_range_km: float,
    pixels: int,
    colormap: str,
    theme: str,
    render_east_pixels: int = 256,
    render_north_pixels: int = 256,
    progressive: bool = True,
    viewport: dict[str, object] | None = None,
) -> go.Figure:
    if colormap not in REFLECTIVITY_COLORMAPS:
        raise ValueError(f"Unknown reflectivity colormap: {colormap}")
    latitude = scan.header.latitude_deg
    longitude = scan.header.longitude_deg
    latitude_span = maximum_range_km / EARTH_KM_PER_DEGREE
    longitude_span = maximum_range_km / (EARTH_KM_PER_DEGREE * cos(radians(latitude)))
    base_bounds = (
        longitude - longitude_span,
        longitude + longitude_span,
        latitude - latitude_span,
        latitude + latitude_span,
    )

    def requested(axis: str, fallback: tuple[float, float]):
        if not isinstance(viewport, dict):
            return fallback
        state = viewport.get(axis)
        values = state.get("range") if isinstance(state, dict) else None
        if (
            not isinstance(values, (tuple, list))
            or len(values) != 2
            or not all(isinstance(value, (int, float)) for value in values)
        ):
            return fallback
        return tuple(sorted((float(values[0]), float(values[1]))))

    west, east, south, north = base_bounds
    left, right = requested("xaxis", (west, east))
    bottom, top = requested("yaxis", (south, north))
    left, right = max(west, left), min(east, right)
    bottom, top = max(south, bottom), min(north, top)
    bounds = (
        (left, right, bottom, top) if left < right and bottom < top else base_bounds
    )
    source_height = max(
        32,
        round(pixels * render_north_pixels / render_east_pixels),
    )
    longitudes, latitudes, dbz = geographic_display_grid(
        scan,
        bounds=bounds,
        width=pixels,
        height=source_height,
        maximum_range_km=maximum_range_km,
    )
    figure = go.Figure()
    figure.update_xaxes(
        range=[bounds[0], bounds[1]],
        constrain="domain",
    )
    figure.update_yaxes(
        range=[bounds[2], bounds[3]],
        scaleanchor="x",
        scaleratio=1 / cos(radians(latitude)),
        constrain="domain",
    )
    colorscale = NEXRAD_COLORSCALE if colormap == "NEXRAD" else colormap
    heatmap_options = {
        "x": longitudes,
        "y": latitudes,
        "z": dbz,
        "zmin": -20,
        "zmax": 75,
        "colorscale": colorscale,
        "colorbar": {
            "title": {"text": "Reflectivity<br>(dBZ)"},
            "len": 0.72,
            "thickness": 14,
        },
        "hovertemplate": (
            "Longitude: %{x:.3f}°<br>Latitude: %{y:.3f}°"
            "<br>Reflectivity: %{z:.1f} dBZ<extra></extra>"
        ),
        "zsmooth": False,
    }
    if progressive:
        add_viewport_heatmap(
            figure,
            viewport=viewport,
            render_width=render_east_pixels,
            render_height=render_north_pixels,
            aggregation="max",
            **heatmap_options,
        )
    else:
        figure.add_trace(go.Heatmap(**heatmap_options, name="Reflectivity"))
    boundary_lon, boundary_lat = boundary_trace_coordinates()
    map_line = "rgba(232,241,243,0.72)" if theme == "dark" else "rgba(20,36,45,0.68)"
    figure.add_trace(
        go.Scatter(
            x=boundary_lon,
            y=boundary_lat,
            mode="lines",
            line={"color": map_line, "width": 0.8},
            hoverinfo="skip",
            showlegend=False,
        )
    )
    figure.add_trace(
        go.Scatter(
            x=(longitude,),
            y=(latitude,),
            mode="markers",
            marker={
                "color": "#ffd84d",
                "line": {"color": "black", "width": 1},
                "size": 9,
            },
            name=scan.header.radar_id,
            hovertemplate=f"{scan.header.radar_id}<extra></extra>",
        )
    )
    styled = style_plotly(
        figure,
        title=(
            f"{scan.header.radar_id} {scan.header.product_id} "
            f"base reflectivity · {scan.header.scan_time:%Y-%m-%d %H:%M:%S} UTC"
        ),
        theme=theme,
        boxed_axes=False,
    )
    styled.update_xaxes(
        title_text=None,
        showgrid=False,
        showline=False,
        showticklabels=False,
        ticks="",
        zeroline=False,
    )
    styled.update_yaxes(
        title_text=None,
        showgrid=False,
        showline=False,
        showticklabels=False,
        ticks="",
        zeroline=False,
    )
    styled.update_layout(
        hovermode="closest",
        margin={"l": 18, "r": 76, "t": 52, "b": 18},
        uirevision=(f"weather-radar-map:{scan.header.radar_id}:{maximum_range_km:g}"),
    )
    return styled


def histogram_figure(
    selection: NexradSequenceSelection,
    theme: str,
) -> go.Figure:
    scan = selection.scan
    (
        histogram_dbz,
        histogram_counts,
        increment,
        measured_min,
        measured_max,
    ) = measured_histogram(scan)
    count_upper = sequence_histogram_count_upper(
        selection.sequence,
    )
    figure = go.Figure(
        go.Bar(
            x=histogram_dbz,
            y=histogram_counts,
            width=increment * 0.92,
            marker={"color": COLORS[1]},
            hovertemplate="%{x:.1f} dBZ<br>%{y:,} gates<extra></extra>",
            name="Native gates",
        )
    )
    figure.update_xaxes(
        title_text="Exact native reflectivity (dBZ)",
        range=[measured_min - increment / 2, measured_max + increment / 2],
        autorange=False,
        fixedrange=True,
    )
    figure.update_yaxes(
        title_text="Gate count",
        range=[0, count_upper],
        autorange=False,
        rangemode="tozero",
        fixedrange=True,
    )
    styled = style_plotly(
        figure,
        title="Measured-gate reflectivity distribution",
        theme=theme,
        boxed_axes=True,
    )
    styled.update_layout(
        uirevision=(f"weather-radar-distribution:{scan.header.scan_time.isoformat()}")
    )
    return styled


__all__ = [
    "NEXRAD_COLORSCALE",
    "REFLECTIVITY_COLORMAPS",
    "histogram_figure",
    "ppi_figure",
]
