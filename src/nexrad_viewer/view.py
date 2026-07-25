"""Complete interactive view for one native weather-radar scan."""

from __future__ import annotations

import numpy as np
from sigvue import UI
from sigvue.helpers import format_bytes

from .formats.nexrad import NexradSequenceSelection
from .plots import REFLECTIVITY_COLORMAPS, histogram_figure, ppi_figure


def _metadata_rows(
    selection: NexradSequenceSelection,
) -> list[dict[str, object]]:
    scan = selection.scan
    header = scan.header
    return [
        {"Field": "Source format", "Value": "NOAA NEXRAD Level III / NIDS"},
        {
            "Field": "Sequence position",
            "Value": (f"{selection.scan_index + 1} of {selection.sequence.scan_count}"),
        },
        {
            "Field": "Sequence start",
            "Value": selection.sequence.headers[0].scan_time.isoformat(),
        },
        {
            "Field": "Sequence end",
            "Value": selection.sequence.headers[-1].scan_time.isoformat(),
        },
        {"Field": "WMO heading", "Value": header.wmo_heading},
        {
            "Field": "Radar / product",
            "Value": f"{header.radar_id} / {header.product_id}",
        },
        {
            "Field": "Message / packet code",
            "Value": f"{header.message_code} / {scan.packet_code}",
        },
        {"Field": "Scan time", "Value": header.scan_time.isoformat()},
        {"Field": "Generation time", "Value": header.generation_time.isoformat()},
        {"Field": "Radar latitude", "Value": f"{header.latitude_deg:.3f}°"},
        {"Field": "Radar longitude", "Value": f"{header.longitude_deg:.3f}°"},
        {"Field": "Radar altitude", "Value": f"{header.altitude_ft:,} ft MSL"},
        {
            "Field": "VCP / volume scan",
            "Value": f"{header.volume_coverage_pattern} / {header.volume_scan_number}",
        },
        {
            "Field": "Elevation",
            "Value": f"{header.elevation_deg:.1f}° (number {header.elevation_number})",
        },
        {
            "Field": "Native polar shape",
            "Value": f"{scan.radial_count:,} × {scan.range_bin_count:,}",
        },
        {"Field": "Native range resolution", "Value": f"{scan.gate_size_km:.2f} km"},
        {
            "Field": "Native slant-range coverage",
            "Value": f"{scan.slant_range_edges_km[-1]:.2f} km",
        },
        {"Field": "Ground-range scale", "Value": f"{scan.ground_range_scale:.3f}"},
        {
            "Field": "Reflectivity encoding",
            "Value": (
                "code 0 below threshold; code 1 range folded; codes 2–255 measured"
            ),
        },
        {"Field": "Measured conversion", "Value": "dBZ = -32.0 + (code - 2) × 0.5"},
        {"Field": "Compressed file", "Value": format_bytes(header.file_size_bytes)},
        {
            "Field": "Inflated symbology",
            "Value": format_bytes(header.uncompressed_payload_bytes),
        },
        {"Field": "Source path", "Value": str(header.source_path)},
    ]


def _statistics_rows(
    selection: NexradSequenceSelection,
) -> list[dict[str, object]]:
    scan = selection.scan
    counts = scan.code_counts()
    measured_codes = scan.level_codes[scan.measured_gate_mask()]
    measured_dbz = (
        scan.header.minimum_value_dbz
        + (measured_codes.astype(np.float64) - 2) * scan.header.value_increment_dbz
    )
    return [
        {"Statistic": "All native gates", "Value": f"{scan.gate_count:,}"},
        {"Statistic": "Measured gates", "Value": f"{counts['measured']:,}"},
        {
            "Statistic": "Below-threshold gates (code 0)",
            "Value": f"{counts['below_threshold']:,}",
        },
        {
            "Statistic": "Range-folded gates (code 1)",
            "Value": f"{counts['range_folded']:,}",
        },
        {"Statistic": "Padding / absent gates", "Value": f"{counts['padding']:,}"},
        {
            "Statistic": "Minimum measured reflectivity",
            "Value": f"{np.min(measured_dbz):.1f} dBZ",
        },
        {
            "Statistic": "Median measured reflectivity",
            "Value": f"{np.median(measured_dbz):.1f} dBZ",
        },
        {
            "Statistic": "Maximum measured reflectivity",
            "Value": f"{np.max(measured_dbz):.1f} dBZ",
        },
        {
            "Statistic": "Sequence scan",
            "Value": (f"{selection.scan_index + 1} of {selection.sequence.scan_count}"),
        },
        {"Statistic": "Scan time", "Value": scan.header.scan_time.isoformat()},
    ]


def view(
    selection: NexradSequenceSelection,
    ui: UI,
) -> None:
    scan = selection.scan
    for label, value in (
        ("Field", "Base reflectivity"),
        ("Sweep", f"{scan.header.elevation_deg:.1f}°"),
        (
            "Scan",
            f"{selection.scan_index + 1} of {selection.sequence.scan_count}",
        ),
        ("Scan time", f"{scan.header.scan_time:%Y-%m-%d %H:%M:%S} UTC"),
        ("Native gates", f"{scan.gate_count:,}"),
        ("Buffer memory", format_bytes(scan.buffer_nbytes)),
    ):
        ui.stat(label, value)

    maximum_range = float(scan.ground_range_edges_km[-1])
    display_range = float(
        ui.select(
            "weather_radar_ppi_range_km",
            label="PPI display radius (km)",
            default=120,
            options=(60, 120, 230, round(maximum_range, 2)),
            group="Plan-position display",
        )
    )
    display_pixels = int(
        ui.select(
            "weather_radar_ppi_pixels",
            label="PPI render size",
            default=512,
            options=(256, 512, 768),
            group="Plan-position display",
        )
    )
    colormap = ui.colormap(
        "weather_radar_colormap",
        label="Colormap",
        default="Portland",
        options=REFLECTIVITY_COLORMAPS,
        group="Plan-position display",
    )
    render_east_pixels = int(
        ui.select(
            "weather_radar_render_east_pixels",
            label="Render resolution E/W (pixels)",
            default=256,
            options=(128, 256, 512, 768),
            group="Raster rendering details",
        )
    )
    render_north_pixels = int(
        ui.select(
            "weather_radar_render_north_pixels",
            label="Render resolution N/S (pixels)",
            default=256,
            options=(128, 256, 512, 768),
            group="Raster rendering details",
        )
    )
    ui.stat(
        "PPI rendering",
        (
            f"{display_pixels:,} × {display_pixels:,} source → "
            f"{render_east_pixels:,} E/W × "
            f"{render_north_pixels:,} N/S raster · max"
        ),
    )
    with ui.tab("Plan Position", columns=(0.22, 0.78)):
        with ui.group("column"):
            ui.place_parameters(
                "weather_radar_colormap",
                "weather_radar_ppi_range_km",
                "weather_radar_ppi_pixels",
                label="PPI display",
            )
        ui.plot(
            lambda: ppi_figure(
                scan,
                maximum_range_km=display_range,
                pixels=display_pixels,
                colormap=colormap,
                theme=ui.theme,
                render_east_pixels=render_east_pixels,
                render_north_pixels=render_north_pixels,
                viewport=ui.plot_viewport("weather-radar-ppi"),
            ),
            key="weather-radar-ppi",
            axis_navigation="bounded",
        )
    with ui.tab("Distribution", columns=(0.72, 0.28)):
        ui.plot(
            lambda: histogram_figure(selection, ui.theme),
            key="weather-radar-histogram",
            axis_navigation="bounded",
        )
        ui.table(
            lambda: _statistics_rows(selection),
            key="weather-radar-statistics",
        )
    with ui.tab("Metadata"):
        ui.table(
            lambda: _metadata_rows(selection),
            key="weather-radar-metadata",
        )


__all__ = ["view"]
