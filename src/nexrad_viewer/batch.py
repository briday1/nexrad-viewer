"""Durable GIF rendering from the actual interactive Plan Position plot."""

from __future__ import annotations

import re
from io import BytesIO
from math import ceil
from pathlib import Path

import plotly.io as pio
from PIL import Image
from sigvue import (
    Batch,
    BatchDestination,
    BatchRequest,
    BatchResult,
    CapabilityChoice,
    DataResource,
)

from .formats.nexrad import NexradLevel3Sequence, open_scan
from .gif_rendering import NEXRAD_GIF_PALETTE, render_animation
from .plots import ppi_figure

RENDER_GIF = "render-full-resolution-gif"


GIF_PALETTE = NEXRAD_GIF_PALETTE


def full_resolution_pixels(
    sequence: NexradLevel3Sequence,
    radius_km: float | None = None,
) -> int:
    """Match map source spacing to native range-gate spacing."""
    scans = tuple(open_scan(header.source_path) for header in sequence.headers)
    pixels = max(
        128,
        *(
            ceil(
                (
                    2.0
                    * (
                        float(scan.ground_range_edges_km[-1])
                        if radius_km is None
                        else float(radius_km)
                    )
                )
                / scan.gate_size_km
            )
            for scan in scans
        ),
    )
    return pixels + pixels % 2


def _frame(
    sequence: NexradLevel3Sequence,
    index: int,
    *,
    pixels: int,
) -> Image.Image:
    """Render the full dark NEXRAD Plan Position figure."""
    scan = open_scan(sequence.headers[index].source_path)
    radius = float(scan.ground_range_edges_km[-1])
    figure = ppi_figure(
        scan,
        maximum_range_km=radius,
        pixels=pixels,
        colormap="NEXRAD",
        theme="dark",
        render_east_pixels=pixels,
        render_north_pixels=pixels,
        progressive=False,
    )
    figure.update_layout(
        title={
            "text": f"{scan.header.scan_time:%Y-%m-%d %H:%M:%S} UTC",
            "x": 0.012,
            "xanchor": "left",
            "y": 0.985,
            "yanchor": "top",
            "font": {"size": 26},
        },
        margin={"l": 18, "r": 18, "t": 52, "b": 92},
        showlegend=False,
    )
    figure.update_traces(
        colorbar={
            "title": {"text": "Reflectivity (dBZ)", "side": "bottom"},
            "orientation": "h",
            "x": 0.5,
            "xanchor": "center",
            "y": -0.12,
            "yanchor": "top",
            "len": 0.78,
            "thickness": 16,
            "tickmode": "array",
            "tickvals": [-20, 0, 20, 40, 60, 75],
            "ticktext": ["−20", "0", "20", "40", "60", "75"],
        },
        selector={"type": "heatmap"},
    )
    payload = pio.to_image(
        figure,
        format="png",
        width=pixels + 36,
        height=pixels + 144,
        scale=1,
    )
    with Image.open(BytesIO(payload)) as rendered:
        frame = rendered.convert("RGB")
        frame.load()
    return frame


def render_sequence_gif(
    sequence: NexradLevel3Sequence,
    target: str | Path,
    *,
    frame_duration_ms: int,
) -> Path:
    """Render every scan through the real Plotly view and write atomically."""
    destination = Path(target).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    pixels = full_resolution_pixels(sequence)
    return render_animation(
        destination,
        frame_count=sequence.scan_count,
        frame_duration_ms=frame_duration_ms,
        frame_builder=lambda index: _frame(sequence, index, pixels=pixels),
    )


class NexradGifBatch(Batch[NexradLevel3Sequence]):
    """Actual Plan Position plot animations at native-gate-scale resolution."""

    item_actions = (
        CapabilityChoice(RENDER_GIF, "Render full-width Plan Position GIF"),
    )
    workspace_actions = (
        CapabilityChoice(
            RENDER_GIF,
            "Render all full-width Plan Position GIFs",
        ),
    )

    def __init__(
        self,
        output_root: str | Path,
        *,
        frame_duration_ms: int,
    ) -> None:
        if (
            isinstance(frame_duration_ms, bool)
            or not isinstance(frame_duration_ms, int)
            or frame_duration_ms < 20
        ):
            raise ValueError("GIF frame duration must be an integer of at least 20 ms")
        self.output_root = Path(output_root).expanduser().resolve()
        self.frame_duration_ms = frame_duration_ms

    def _filename(self, resource: DataResource) -> str:
        slug = re.sub(
            r"[^a-z0-9]+",
            "-",
            resource.identifier.lower(),
        ).strip("-")
        return (
            f"{slug}-plan-position-fullwidth-nexrad-dark-dbzbar-"
            f"{self.frame_duration_ms}ms.gif"
        )

    def item_destination(
        self,
        resource: DataResource,
        request: BatchRequest,
    ) -> BatchDestination:
        return BatchDestination(
            self.output_root,
            (self._filename(resource),),
            "Full-width Plan Position GIF is ready",
        )

    def workspace_destination(
        self,
        resources: tuple[DataResource, ...],
        request: BatchRequest,
    ) -> BatchDestination:
        return BatchDestination(
            self.output_root,
            tuple(self._filename(resource) for resource in resources),
            "All full-width Plan Position GIFs are ready",
        )

    def run_item(
        self,
        resource: DataResource,
        source_data: NexradLevel3Sequence,
        request: BatchRequest,
        directory: Path,
    ) -> BatchResult:
        target = render_sequence_gif(
            source_data,
            directory / self._filename(resource),
            frame_duration_ms=self.frame_duration_ms,
        )
        return BatchResult(
            (target,),
            "Rendered full-width Plan Position GIF",
        )

    def run_workspace(
        self,
        resources: tuple[DataResource, ...],
        open_resource,
        request: BatchRequest,
        directory: Path,
    ) -> BatchResult:
        outputs = tuple(
            render_sequence_gif(
                open_resource(resource),
                directory / self._filename(resource),
                frame_duration_ms=self.frame_duration_ms,
            )
            for resource in resources
        )
        return BatchResult(
            outputs,
            f"Rendered {len(outputs)} full-width Plan Position GIFs",
        )


__all__ = [
    "GIF_PALETTE",
    "NEXRAD_GIF_PALETTE",
    "RENDER_GIF",
    "NexradGifBatch",
    "full_resolution_pixels",
    "render_sequence_gif",
]
