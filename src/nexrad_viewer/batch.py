"""Durable single-site GIF rendering through the shared radar compositor."""

from __future__ import annotations

import re
from pathlib import Path

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
from .gif_rendering import (
    NEXRAD_GIF_PALETTE,
    compose_conus_frame,
    render_animation,
)
from .plots import REFLECTIVITY_DOMAIN

RENDER_GIF = "render-full-resolution-gif"


GIF_PALETTE = NEXRAD_GIF_PALETTE


def _frame(
    sequence: NexradLevel3Sequence,
    index: int,
    *,
    width: int,
) -> Image.Image:
    """Render one site with the exact national-frame composition path."""
    scan = open_scan(sequence.headers[index].source_path)
    radius = float(scan.ground_range_edges_km[-1])
    return compose_conus_frame(
        (scan,),
        width=width,
        maximum_range_km=radius,
        timestamp=scan.header.scan_time,
        reflectivity_limits=REFLECTIVITY_DOMAIN,
        timeline_index=index,
    )


def render_sequence_gif(
    sequence: NexradLevel3Sequence,
    target: str | Path,
    *,
    width: int,
    frame_duration_ms: int,
) -> Path:
    """Render every scan through the real Plotly view and write atomically."""
    destination = Path(target).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    return render_animation(
        destination,
        frame_count=sequence.scan_count,
        frame_duration_ms=frame_duration_ms,
        frame_builder=lambda index: _frame(sequence, index, width=width),
    )


class NexradGifBatch(Batch[NexradLevel3Sequence]):
    """One-site animations rendered exactly like the national mosaic."""

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
        width: int,
        frame_duration_ms: int,
    ) -> None:
        if width < 128:
            raise ValueError("site GIF width must be at least 128")
        if (
            isinstance(frame_duration_ms, bool)
            or not isinstance(frame_duration_ms, int)
            or frame_duration_ms < 20
        ):
            raise ValueError("GIF frame duration must be an integer of at least 20 ms")
        self.output_root = Path(output_root).expanduser().resolve()
        self.width = int(width)
        self.frame_duration_ms = frame_duration_ms

    def _filename(self, resource: DataResource) -> str:
        slug = re.sub(
            r"[^a-z0-9]+",
            "-",
            resource.identifier.lower(),
        ).strip("-")
        return (
            f"{slug}-plan-position-nexrad-canonical-dbzbar-"
            f"{self.width}px-{self.frame_duration_ms}ms.gif"
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
            width=self.width,
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
                width=self.width,
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
    "render_sequence_gif",
]
