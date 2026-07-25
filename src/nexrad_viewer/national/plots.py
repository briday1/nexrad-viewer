"""Minimal national map presentation for a synchronized radar frame."""

from __future__ import annotations

import plotly.graph_objects as go
from sigvue import add_viewport_heatmap

from ..plots import NEXRAD_COLORSCALE, REFLECTIVITY_COLORMAPS
from ..style import style_plotly
from .analysis import CONUS_BOUNDS, national_mosaic_grid
from .geography import boundary_trace_coordinates
from .models import NationalFrameSelection


def _visible_bounds(
    viewport: dict[str, object] | None,
) -> tuple[float, float, float, float]:
    west, east, south, north = CONUS_BOUNDS

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

    left, right = requested("xaxis", (west, east))
    bottom, top = requested("yaxis", (south, north))
    left, right = max(west, left), min(east, right)
    bottom, top = max(south, bottom), min(north, top)
    if left >= right or bottom >= top:
        return CONUS_BOUNDS
    return left, right, bottom, top


def national_map_figure(
    selection: NationalFrameSelection,
    *,
    width: int,
    maximum_range_km: float,
    colormap: str,
    progressive: bool,
    theme: str,
    render_width: int = 640,
    render_height: int = 360,
    viewport: dict[str, object] | None = None,
) -> go.Figure:
    """Render one aligned frame over quiet Census state boundaries."""
    if colormap not in REFLECTIVITY_COLORMAPS:
        raise ValueError(f"Unknown reflectivity colormap: {colormap}")
    bounds = _visible_bounds(viewport)
    source_height = max(64, round(width * render_height / render_width))
    longitudes, latitudes, reflectivity = national_mosaic_grid(
        selection.scans,
        width=width,
        height=source_height,
        maximum_range_km=maximum_range_km,
        bounds=bounds,
    )
    colorscale = NEXRAD_COLORSCALE if colormap == "NEXRAD" else colormap
    hovertemplate = (
        "Longitude: %{x:.2f}°<br>Latitude: %{y:.2f}°"
        "<br>Reflectivity: %{z:.1f} dBZ<extra></extra>"
    )
    figure = go.Figure()
    if progressive:
        add_viewport_heatmap(
            figure,
            viewport=viewport,
            render_width=render_width,
            render_height=render_height,
            aggregation="max",
            x=longitudes,
            y=latitudes,
            z=reflectivity,
            zmin=-20,
            zmax=75,
            colorscale=colorscale,
            colorbar={
                "title": {"text": "Reflectivity<br>(dBZ)"},
                "len": 0.72,
                "thickness": 14,
            },
            hovertemplate=hovertemplate,
            zsmooth=False,
        )
    else:
        figure.add_trace(
            go.Heatmap(
                x=longitudes,
                y=latitudes,
                z=reflectivity,
                zmin=-20,
                zmax=75,
                colorscale=colorscale,
                colorbar={
                    "title": {"text": "Reflectivity<br>(dBZ)"},
                    "len": 0.72,
                    "thickness": 14,
                },
                hovertemplate=hovertemplate,
                zsmooth=False,
                name="Reflectivity",
            )
        )

    boundary_lon, boundary_lat = boundary_trace_coordinates()
    line_color = "rgba(232,241,243,0.72)" if theme == "dark" else "rgba(20,36,45,0.68)"
    figure.add_trace(
        go.Scatter(
            x=boundary_lon,
            y=boundary_lat,
            mode="lines",
            line={"color": line_color, "width": 0.75},
            hoverinfo="skip",
            showlegend=False,
        )
    )
    figure.add_trace(
        go.Scatter(
            x=tuple(scan.header.longitude_deg for scan in selection.scans),
            y=tuple(scan.header.latitude_deg for scan in selection.scans),
            text=tuple(
                (
                    f"{scan.header.radar_id}<br>"
                    f"Native scan: {scan.header.scan_time:%H:%M:%S} UTC"
                )
                for scan in selection.scans
            ),
            mode="markers",
            marker={
                "color": "#ffd84d",
                "line": {"color": "rgba(0,0,0,0.90)", "width": 0.9},
                "size": 7,
            },
            hovertemplate="%{text}<extra></extra>",
            showlegend=False,
        )
    )
    west, east, south, north = bounds
    figure.update_xaxes(range=[west, east], constrain="domain")
    figure.update_yaxes(range=[south, north], constrain="domain")
    styled = style_plotly(
        figure,
        title=(
            f"CONUS base-reflectivity mosaic · "
            f"{selection.frame.target_time:%Y-%m-%d %H:%M} UTC"
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
        margin={"l": 12, "r": 78, "t": 52, "b": 12},
        uirevision=f"national-map:{selection.day.date.isoformat()}",
    )
    return styled


__all__ = ["national_map_figure"]
