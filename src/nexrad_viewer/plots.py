"""Pure Plotly builders for exact and display-resampled radar views."""

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
from plotly import colors as plotly_colors
from sigvue import add_viewport_heatmap

from .formats.nexrad import (
    FIRST_MEASURED_CODE,
    NexradLevel3Radial,
    NexradSequenceSelection,
)
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
    ground_range = np.hypot(x, y)
    slant_range = ground_range / scan.ground_range_scale
    azimuth = np.degrees(np.arctan2(x, y)) % 360.0

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
    valid = (
        covered
        & (ground_range <= maximum_range_km)
        & (gate >= 0)
        & (gate < scan.range_bin_count)
    )
    safe_gate = np.clip(gate, 0, scan.range_bin_count - 1)
    valid &= safe_gate < scan.radial_gate_counts[radial]
    codes = scan.level_codes[radial, safe_gate]
    measured = valid & (codes >= FIRST_MEASURED_CODE)
    dbz = np.full((pixels, pixels), np.nan, dtype=np.float32)
    dbz[measured] = (
        scan.header.minimum_value_dbz
        + (codes[measured].astype(np.float32) - FIRST_MEASURED_CODE)
        * scan.header.value_increment_dbz
    )
    return centers, centers, dbz


def ppi_figure(
    scan: NexradLevel3Radial,
    *,
    maximum_range_km: float,
    pixels: int,
    colormap: str,
    theme: str,
    render_east_pixels: int = 256,
    render_north_pixels: int = 256,
    viewport: dict[str, object] | None = None,
) -> go.Figure:
    if colormap not in REFLECTIVITY_COLORMAPS:
        raise ValueError(f"Unknown reflectivity colormap: {colormap}")
    x, y, dbz = cartesian_display_grid(
        scan,
        maximum_range_km=maximum_range_km,
        pixels=pixels,
    )
    figure = go.Figure()
    figure.update_xaxes(
        range=[-maximum_range_km, maximum_range_km],
        constrain="domain",
    )
    figure.update_yaxes(
        range=[-maximum_range_km, maximum_range_km],
        scaleanchor="x",
        scaleratio=1,
    )
    add_viewport_heatmap(
        figure,
        viewport=viewport,
        render_width=render_east_pixels,
        render_height=render_north_pixels,
        aggregation="max",
        x=x,
        y=y,
        z=dbz,
        zmin=-20,
        zmax=75,
        colorscale=(NEXRAD_COLORSCALE if colormap == "NEXRAD" else colormap),
        colorbar={
            "title": {"text": "Reflectivity<br>(dBZ)"},
            "len": 0.72,
            "thickness": 14,
        },
        hovertemplate=(
            "East: %{x:.1f} km<br>North: %{y:.1f} km"
            "<br>Reflectivity: %{z:.1f} dBZ<extra></extra>"
        ),
        zsmooth=False,
    )
    figure.add_trace(
        go.Scatter(
            x=(scan.i_center_km,),
            y=(scan.j_center_km,),
            mode="markers",
            marker={
                "color": "white",
                "line": {"color": "black", "width": 1},
                "size": 8,
            },
            name=scan.header.radar_id,
            hovertemplate=f"{scan.header.radar_id}<extra></extra>",
        )
    )
    scale_km = max(1.0, round(maximum_range_km / 4.0))
    scale_fraction = scale_km / (2.0 * maximum_range_km)
    styled = style_plotly(
        figure,
        title=(
            f"{scan.header.radar_id} {scan.header.product_id} "
            f"base reflectivity · {scan.header.scan_time:%Y-%m-%d %H:%M:%S} UTC"
        ),
        theme=theme,
        boxed_axes=False,
    )
    ink = "#e7f1f3" if theme == "dark" else "#13212b"
    legend_background = (
        "rgba(16,37,45,0.76)" if theme == "dark" else "rgba(255,255,255,0.80)"
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
        shapes=[
            {"type": "line", "xref": "paper", "yref": "paper", "x0": 0.055, "x1": 0.055, "y0": 0.055, "y1": 0.12, "line": {"color": ink, "width": 2}},
            {"type": "line", "xref": "paper", "yref": "paper", "x0": 0.055, "x1": 0.12, "y0": 0.055, "y1": 0.055, "line": {"color": ink, "width": 2}},
            {"type": "path", "xref": "paper", "yref": "paper", "path": "M 0.055 0.13 L 0.048 0.117 L 0.062 0.117 Z", "line": {"color": ink, "width": 1}, "fillcolor": ink},
            {"type": "path", "xref": "paper", "yref": "paper", "path": "M 0.13 0.055 L 0.117 0.048 L 0.117 0.062 Z", "line": {"color": ink, "width": 1}, "fillcolor": ink},
            # A scale bar whose paper length corresponds to the full-view range.
            {"type": "line", "xref": "paper", "yref": "paper", "x0": 0.68, "x1": 0.68 + scale_fraction, "y0": 0.07, "y1": 0.07, "line": {"color": ink, "width": 4}},
        ],
        annotations=[
            {"xref": "paper", "yref": "paper", "x": 0.055, "y": 0.145, "text": "<b>N</b>", "showarrow": False, "font": {"color": ink, "size": 11}, "bgcolor": legend_background},
            {"xref": "paper", "yref": "paper", "x": 0.145, "y": 0.055, "text": "<b>E</b>", "showarrow": False, "font": {"color": ink, "size": 11}, "bgcolor": legend_background},
            {"xref": "paper", "yref": "paper", "x": 0.68 + scale_fraction / 2, "y": 0.085, "text": f"<b>{scale_km:g} km</b>", "showarrow": False, "font": {"color": ink, "size": 11}, "bgcolor": legend_background},
        ],
    )
    return styled


def histogram_figure(
    selection: NexradSequenceSelection,
    theme: str,
) -> go.Figure:
    scan = selection.scan
    valid = scan.valid_gate_mask()
    measured_codes = scan.level_codes[
        valid & (scan.level_codes >= FIRST_MEASURED_CODE)
    ]
    code_counts = np.bincount(measured_codes, minlength=256)
    present_codes = (
        np.flatnonzero(code_counts[FIRST_MEASURED_CODE:])
        + FIRST_MEASURED_CODE
    )
    histogram_dbz = (
        scan.header.minimum_value_dbz
        + (present_codes - FIRST_MEASURED_CODE)
        * scan.header.value_increment_dbz
    )
    histogram_counts = code_counts[present_codes]
    increment = scan.header.value_increment_dbz
    measured_min = scan.header.minimum_value_dbz
    measured_max = measured_min + (255 - FIRST_MEASURED_CODE) * increment
    # A histogram bin can never contain more gates than the inflated packet
    # contains bytes.  The sequence-wide maximum is therefore a conservative,
    # metadata-only bound that remains identical while stepping through scans;
    # importantly, it does not require eagerly decoding the entire sequence.
    count_upper = max(
        1,
        max(
            header.uncompressed_payload_bytes
            for header in selection.sequence.headers
        ),
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
        uirevision=(
            "weather-radar-distribution:"
            f"{scan.header.scan_time.isoformat()}"
        )
    )
    return styled


__all__ = [
    "NEXRAD_COLORSCALE",
    "REFLECTIVITY_COLORMAPS",
    "cartesian_display_grid",
    "histogram_figure",
    "ppi_figure",
]
