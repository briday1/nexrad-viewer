"""Complete interactive view for one synchronized national radar frame."""

from __future__ import annotations

from math import ceil

from sigvue import UI
from sigvue.helpers import format_bytes

from ..analysis import automatic_reflectivity_limits
from ..plots import REFLECTIVITY_COLORMAPS
from .models import NationalFrameSelection
from .plots import national_map_figure


def _site_rows(
    selection: NationalFrameSelection,
) -> list[dict[str, object]]:
    target = selection.frame.target_time
    return [
        {
            "Radar": scan.header.radar_id,
            "Native scan": scan.header.scan_time.isoformat(),
            "Offset from target": (
                f"{(scan.header.scan_time - target).total_seconds():+.0f} s"
            ),
            "Latitude": f"{scan.header.latitude_deg:.4f}°",
            "Longitude": f"{scan.header.longitude_deg:.4f}°",
            "Native gates": f"{scan.gate_count:,}",
            "Source path": str(scan.header.source_path),
        }
        for scan in selection.scans
    ]


def view(selection: NationalFrameSelection, ui: UI) -> None:
    """Present one aligned frame; expensive map work remains view-lazy."""
    native_maximum_range = max(
        float(scan.ground_range_edges_km[-1]) for scan in selection.scans
    )
    for label, value in (
        ("Date", selection.day.date.isoformat()),
        (
            "Aligned frame",
            f"{selection.frame_index + 1} of {selection.frame_count}",
        ),
        ("Target time", f"{selection.frame.target_time:%Y-%m-%d %H:%M} UTC"),
        ("Radar sites", f"{selection.frame.site_count} of {selection.day.site_count}"),
        (
            "Maximum time offset",
            f"{selection.frame.maximum_offset_seconds:.0f} s",
        ),
        ("Native scans in date", f"{selection.day.scan_count:,}"),
        ("Buffer memory", format_bytes(selection.buffer_nbytes)),
    ):
        ui.stat(label, value)

    source_width = int(
        ui.number(
            "national_mosaic_width",
            label="Current viewport source width",
            default=1200,
            minimum=128,
            maximum=8192,
            step=128,
            group="National map",
        )
    )
    radius_km = float(
        ui.number(
            "national_radar_radius_km",
            label="Maximum radar radius (km)",
            default=round(native_maximum_range, 2),
            minimum=10,
            maximum=ceil(native_maximum_range * 100) / 100,
            step=10,
            group="National map",
        )
    )
    colormap = ui.colormap(
        "national_colormap",
        label="Colormap",
        default="NEXRAD",
        options=REFLECTIVITY_COLORMAPS,
        group="National map",
    )
    zmin, zmax = ui.limits(
        "national_reflectivity_limits",
        label="Reflectivity limits (dBZ)",
        default=automatic_reflectivity_limits(selection.scans),
        minimum=-20,
        maximum=75,
        step=0.5,
        group="National map",
    )
    progressive = ui.toggle(
        "national_progressive_rendering",
        label="Progressive rendering",
        default=True,
        group="Raster rendering details",
    )
    render_width = int(
        ui.number(
            "national_render_width",
            label="Viewport render width",
            default=640,
            minimum=128,
            maximum=4096,
            step=64,
            group="Raster rendering details",
        )
    )
    render_height = int(
        ui.number(
            "national_render_height",
            label="Viewport render height",
            default=360,
            minimum=64,
            maximum=2160,
            step=36,
            group="Raster rendering details",
        )
    )
    with ui.tab("National Mosaic", columns=(0.20, 0.80)):
        with ui.group("column"):
            ui.place_parameters(
                "national_colormap",
                "national_reflectivity_limits",
                "national_mosaic_width",
                "national_radar_radius_km",
                "national_progressive_rendering",
                "national_render_width",
                "national_render_height",
                label="Map display",
            )
        ui.plot(
            lambda: national_map_figure(
                selection,
                width=source_width,
                maximum_range_km=radius_km,
                colormap=colormap,
                progressive=progressive,
                theme=ui.theme,
                render_width=render_width,
                render_height=render_height,
                viewport=ui.plot_viewport("national-radar-map"),
                reflectivity_limits=(zmin, zmax),
            ),
            key="national-radar-map",
            axis_navigation="bounded",
        )
    with ui.tab("Sites"):
        ui.table(
            lambda: _site_rows(selection),
            key="national-radar-sites",
        )


__all__ = ["view"]
