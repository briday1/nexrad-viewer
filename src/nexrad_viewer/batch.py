"""Durable full-resolution GIF rendering for NEXRAD sequences."""

from __future__ import annotations

import re
from math import ceil, isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
from PIL import Image, ImageDraw, ImageFont
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
from .plots import NEXRAD_COLORSCALE

RENDER_GIF = "render-full-resolution-gif"


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except OSError:
        return ImageFont.load_default()


def _palette() -> list[int]:
    locations = np.asarray(
        [location for location, _ in NEXRAD_COLORSCALE],
        dtype=np.float64,
    )
    stop_colors = np.asarray(
        [
            tuple(int(color[index : index + 2], 16) for index in (1, 3, 5))
            for _, color in NEXRAD_COLORSCALE
        ],
        dtype=np.float64,
    )
    samples = np.linspace(0.0, 1.0, 254)
    colors = (
        np.column_stack(
            [
                np.interp(samples, locations, stop_colors[:, channel])
                for channel in range(3)
            ]
        )
        .round()
        .astype(np.uint8)
    )
    entries = [(8, 17, 23), *map(tuple, colors), (255, 255, 255)]
    return [component for color in entries for component in color]


GIF_PALETTE = _palette()


def full_resolution_pixels(
    sequence: NexradLevel3Sequence,
    radius_km: float,
) -> int:
    """Match Cartesian pixel spacing to the native range-gate spacing."""
    first = open_scan(sequence.headers[0].source_path)
    pixels = max(32, ceil((2.0 * radius_km) / first.gate_size_km))
    return pixels + pixels % 2


def _frame(
    sequence: NexradLevel3Sequence,
    index: int,
    *,
    radius_km: float,
    pixels: int,
) -> Image.Image:
    scan = open_scan(sequence.headers[index].source_path)
    if radius_km > float(scan.ground_range_edges_km[-1]):
        raise ValueError(
            f"GIF radius {radius_km:g} km exceeds scan coverage "
            f"{scan.ground_range_edges_km[-1]:g} km"
        )
    _, _, dbz = cartesian_display_grid(
        scan,
        maximum_range_km=radius_km,
        pixels=pixels,
    )
    finite = np.isfinite(dbz)
    indexes = np.zeros(dbz.shape, dtype=np.uint8)
    indexes[finite] = 1 + np.rint(
        np.clip((dbz[finite] + 20.0) / 95.0, 0.0, 1.0) * 253.0
    ).astype(np.uint8)
    map_image = Image.fromarray(np.flipud(indexes), mode="P")
    map_image.putpalette(GIF_PALETTE)

    header_height = 68
    canvas = Image.new("P", (pixels, pixels + header_height), color=0)
    canvas.putpalette(GIF_PALETTE)
    canvas.paste(map_image, (0, header_height))
    draw = ImageDraw.Draw(canvas)
    title_font = _font(max(13, min(22, pixels // 42)))
    detail_font = _font(max(11, min(16, pixels // 56)))
    draw.text(
        (12, 7),
        (
            f"{scan.header.radar_id} {scan.header.product_id} "
            f"{scan.header.scan_time:%Y-%m-%d %H:%M:%S} UTC"
        ),
        fill=255,
        font=title_font,
    )
    draw.text(
        (12, 36),
        (
            f"Frame {index + 1}/{sequence.scan_count} · "
            f"radius {radius_km:g} km · -20 to 75 dBZ"
        ),
        fill=255,
        font=detail_font,
    )

    center_x = (scan.i_center_km + radius_km) / (2.0 * radius_km) * pixels
    center_y = (radius_km - scan.j_center_km) / (
        2.0 * radius_km
    ) * pixels + header_height
    line_width = max(1, pixels // 640)
    for fraction in (0.25, 0.5, 0.75, 1.0):
        ring = fraction * pixels / 2.0
        draw.ellipse(
            (
                center_x - ring,
                center_y - ring,
                center_x + ring,
                center_y + ring,
            ),
            outline=255,
            width=line_width,
        )
    marker = max(3, pixels // 180)
    draw.ellipse(
        (
            center_x - marker,
            center_y - marker,
            center_x + marker,
            center_y + marker,
        ),
        fill=255,
    )
    # Keep identical meteorological frames distinct in the GIF timeline.
    canvas.putpixel((pixels - 1, header_height - 1), 1 + index % 254)
    return canvas


def render_sequence_gif(
    sequence: NexradLevel3Sequence,
    target: str | Path,
    *,
    radius_km: float,
    frame_duration_ms: int,
) -> Path:
    """Render every scan without viewport raster reduction and write atomically."""
    destination = Path(target).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    pixels = full_resolution_pixels(sequence, radius_km)
    frames = [
        _frame(
            sequence,
            index,
            radius_km=radius_km,
            pixels=pixels,
        )
        for index in range(sequence.scan_count)
    ]
    with NamedTemporaryFile(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".gif",
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
    try:
        frames[0].save(
            temporary,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=frame_duration_ms,
            loop=0,
            disposal=2,
            optimize=False,
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


class NexradGifBatch(Batch[NexradLevel3Sequence]):
    """Item and workspace actions for deterministic PPI animations."""

    item_actions = (CapabilityChoice(RENDER_GIF, "Render full-resolution GIF"),)
    workspace_actions = (
        CapabilityChoice(RENDER_GIF, "Render all full-resolution GIFs"),
    )

    def __init__(
        self,
        output_root: str | Path,
        *,
        radius_km: float,
        frame_duration_ms: int,
    ) -> None:
        if not isfinite(radius_km) or radius_km <= 0:
            raise ValueError("GIF radius must be finite and positive")
        if (
            isinstance(frame_duration_ms, bool)
            or not isinstance(frame_duration_ms, int)
            or frame_duration_ms < 20
        ):
            raise ValueError("GIF frame duration must be an integer of at least 20 ms")
        self.output_root = Path(output_root).expanduser().resolve()
        self.radius_km = float(radius_km)
        self.frame_duration_ms = frame_duration_ms

    def _filename(self, resource: DataResource) -> str:
        slug = re.sub(
            r"[^a-z0-9]+",
            "-",
            resource.identifier.lower(),
        ).strip("-")
        radius = f"{self.radius_km:g}".replace(".", "p")
        return f"{slug}-ppi-{radius}km-{self.frame_duration_ms}ms.gif"

    def item_destination(
        self,
        resource: DataResource,
        request: BatchRequest,
    ) -> BatchDestination:
        return BatchDestination(
            self.output_root,
            (self._filename(resource),),
            "Full-resolution NEXRAD GIF is ready",
        )

    def workspace_destination(
        self,
        resources: tuple[DataResource, ...],
        request: BatchRequest,
    ) -> BatchDestination:
        return BatchDestination(
            self.output_root,
            tuple(self._filename(resource) for resource in resources),
            "All full-resolution NEXRAD GIFs are ready",
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
            radius_km=self.radius_km,
            frame_duration_ms=self.frame_duration_ms,
        )
        return BatchResult(
            (target,),
            "Rendered full-resolution NEXRAD GIF",
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
                radius_km=self.radius_km,
                frame_duration_ms=self.frame_duration_ms,
            )
            for resource in resources
        )
        return BatchResult(
            outputs,
            f"Rendered {len(outputs)} full-resolution NEXRAD GIFs",
        )


__all__ = [
    "RENDER_GIF",
    "NexradGifBatch",
    "full_resolution_pixels",
    "render_sequence_gif",
]
