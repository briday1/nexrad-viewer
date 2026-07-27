"""Durable single-site GIF rendering through the shared radar compositor."""

from __future__ import annotations

import re
from collections.abc import Callable
from math import ceil
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

from .analysis import cartesian_display_grid
from .formats.nexrad import NexradLevel3Sequence, open_scan
from .gif_rendering import (
    NEXRAD_GIF_PALETTE,
    compose_circular_frame,
    compose_conus_frame,
    render_animation,
)
from .plots import REFLECTIVITY_DOMAIN

RENDER_GIF = "render-full-resolution-gif"
RENDER_CIRCULAR_60KM_GIF = "render-circular-ppi-60km-gif"
RENDER_CIRCULAR_120KM_GIF = "render-circular-ppi-120km-gif"
RENDER_CIRCULAR_230KM_GIF = "render-circular-ppi-230km-gif"
RENDER_CIRCULAR_FULL_GIF = "render-circular-ppi-full-range-gif"

_CIRCULAR_ACTIONS = (
    (
        RENDER_CIRCULAR_60KM_GIF,
        "Render circular PPI GIF · 60 km",
        60.0,
    ),
    (
        RENDER_CIRCULAR_120KM_GIF,
        "Render circular PPI GIF · 120 km",
        120.0,
    ),
    (
        RENDER_CIRCULAR_230KM_GIF,
        "Render circular PPI GIF · 230 km",
        230.0,
    ),
    (
        RENDER_CIRCULAR_FULL_GIF,
        "Render circular PPI GIF · full native range",
        None,
    ),
)
_CIRCULAR_RANGES = {
    action: radius
    for action, _, radius in _CIRCULAR_ACTIONS
}


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
        site_label=scan.header.radar_id,
    )


def render_sequence_gif(
    sequence: NexradLevel3Sequence,
    target: str | Path,
    *,
    width: int,
    frame_duration_ms: int,
    cancel: Callable[[], None] | None = None,
) -> Path:
    """Render every scan through the real Plotly view and write atomically."""
    destination = Path(target).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    return render_animation(
        destination,
        frame_count=sequence.scan_count,
        frame_duration_ms=frame_duration_ms,
        frame_builder=lambda index: _frame(sequence, index, width=width),
        cancel=cancel,
    )


def full_resolution_pixels(
    sequence: NexradLevel3Sequence,
    radius_km: float,
) -> int:
    """Match Cartesian PPI pixels to the finest native gate spacing."""
    pixels = max(
        128,
        *(
            ceil(2.0 * radius_km / open_scan(header.source_path).gate_size_km)
            for header in sequence.headers
        ),
    )
    return pixels + pixels % 2


def _full_range_km(sequence: NexradLevel3Sequence) -> float:
    return max(
        float(open_scan(header.source_path).ground_range_edges_km[-1])
        for header in sequence.headers
    )


def _circular_frame(
    sequence: NexradLevel3Sequence,
    index: int,
    *,
    radius_km: float,
    pixels: int,
) -> Image.Image:
    scan = open_scan(sequence.headers[index].source_path)
    _, _, reflectivity = cartesian_display_grid(
        scan,
        maximum_range_km=radius_km,
        pixels=pixels,
    )
    return compose_circular_frame(
        reflectivity,
        timestamp=scan.header.scan_time,
        radar_id=scan.header.radar_id,
        product_id=scan.header.product_id,
        radius_km=radius_km,
        frame_index=index,
        frame_count=sequence.scan_count,
    )


def render_circular_sequence_gif(
    sequence: NexradLevel3Sequence,
    target: str | Path,
    *,
    radius_km: float | None,
    frame_duration_ms: int,
    cancel: Callable[[], None] | None = None,
) -> Path:
    """Render every scan as a native-gate-scale radar-centered PPI."""
    selected_radius = (
        _full_range_km(sequence)
        if radius_km is None
        else float(radius_km)
    )
    pixels = full_resolution_pixels(sequence, selected_radius)
    destination = Path(target).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    return render_animation(
        destination,
        frame_count=sequence.scan_count,
        frame_duration_ms=frame_duration_ms,
        frame_builder=lambda index: _circular_frame(
            sequence,
            index,
            radius_km=selected_radius,
            pixels=pixels,
        ),
        cancel=cancel,
    )


class NexradGifBatch(Batch[NexradLevel3Sequence]):
    """Canonical maps and selectable-range circular site animations."""

    item_actions = (
        CapabilityChoice(RENDER_GIF, "Render full-width Plan Position GIF"),
        *(
            CapabilityChoice(action, label)
            for action, label, _ in _CIRCULAR_ACTIONS
        ),
    )
    workspace_actions = (
        CapabilityChoice(
            RENDER_GIF,
            "Render all full-width Plan Position GIFs",
        ),
        *(
            CapabilityChoice(
                action,
                label.replace(
                    "Render circular PPI GIF",
                    "Render all circular PPI GIFs",
                ),
            )
            for action, label, _ in _CIRCULAR_ACTIONS
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

    def _filename(self, resource: DataResource, action: str) -> str:
        slug = re.sub(
            r"[^a-z0-9]+",
            "-",
            resource.identifier.lower(),
        ).strip("-")
        if action == RENDER_GIF:
            return (
                f"{slug}-plan-position-nexrad-canonical-dbzbar-"
                f"{self.width}px-{self.frame_duration_ms}ms.gif"
            )
        radius = _CIRCULAR_RANGES[action]
        range_slug = (
            "full-native-range"
            if radius is None
            else f"{radius:g}km"
        )
        return (
            f"{slug}-circular-ppi-{range_slug}-native-gates-"
            f"{self.frame_duration_ms}ms.gif"
        )

    @staticmethod
    def _summary(action: str, *, all_resources: bool = False) -> str:
        prefix = "All " if all_resources else ""
        if action == RENDER_GIF:
            return f"{prefix}full-width Plan Position GIF{'s' if all_resources else ''}"
        radius = _CIRCULAR_RANGES[action]
        range_label = "full native range" if radius is None else f"{radius:g} km"
        return (
            f"{prefix}circular PPI GIF{'s' if all_resources else ''} "
            f"at {range_label}"
        )

    def _render(
        self,
        resource: DataResource,
        sequence: NexradLevel3Sequence,
        request: BatchRequest,
        directory: Path,
    ) -> Path:
        target = directory / self._filename(resource, request.action)
        if request.action == RENDER_GIF:
            return render_sequence_gif(
                sequence,
                target,
                width=self.width,
                frame_duration_ms=self.frame_duration_ms,
                cancel=request.raise_if_cancelled,
            )
        return render_circular_sequence_gif(
            sequence,
            target,
            radius_km=_CIRCULAR_RANGES[request.action],
            frame_duration_ms=self.frame_duration_ms,
            cancel=request.raise_if_cancelled,
        )

    def item_destination(
        self,
        resource: DataResource,
        request: BatchRequest,
    ) -> BatchDestination:
        return BatchDestination(
            self.output_root,
            (self._filename(resource, request.action),),
            f"{self._summary(request.action)} is ready",
        )

    def workspace_destination(
        self,
        resources: tuple[DataResource, ...],
        request: BatchRequest,
    ) -> BatchDestination:
        return BatchDestination(
            self.output_root,
            tuple(
                self._filename(resource, request.action)
                for resource in resources
            ),
            f"{self._summary(request.action, all_resources=True)} are ready",
        )

    def run_item(
        self,
        resource: DataResource,
        source_data: NexradLevel3Sequence,
        request: BatchRequest,
        directory: Path,
    ) -> BatchResult:
        target = self._render(
            resource,
            source_data,
            request,
            directory,
        )
        return BatchResult(
            (target,),
            f"Rendered {self._summary(request.action)}",
        )

    def run_workspace(
        self,
        resources: tuple[DataResource, ...],
        open_resource,
        request: BatchRequest,
        directory: Path,
    ) -> BatchResult:
        outputs = request.each(
            resources,
            lambda resource: self._render(
                resource,
                open_resource(resource),
                request,
                directory,
            ),
        )
        summary = self._summary(
            request.action,
            all_resources=True,
        ).removeprefix("All ")
        return BatchResult(
            outputs,
            f"Rendered {len(outputs)} {summary.lower()}",
        )


__all__ = [
    "GIF_PALETTE",
    "NEXRAD_GIF_PALETTE",
    "RENDER_CIRCULAR_60KM_GIF",
    "RENDER_CIRCULAR_120KM_GIF",
    "RENDER_CIRCULAR_230KM_GIF",
    "RENDER_CIRCULAR_FULL_GIF",
    "RENDER_GIF",
    "NexradGifBatch",
    "full_resolution_pixels",
    "render_circular_sequence_gif",
    "render_sequence_gif",
]
