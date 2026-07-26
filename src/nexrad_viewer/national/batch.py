"""Durable high-resolution national mosaic GIF rendering."""

from __future__ import annotations

import re
from math import isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

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

from ..batch import GIF_PALETTE
from ..formats.nexrad import open_scan
from .analysis import CONUS_BOUNDS, national_mosaic_grid
from .geography import state_boundaries
from .models import NationalDay
from .reader import aligned_frames

RENDER_NATIONAL_GIF = "render-national-mosaic-gif"


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except OSError:
        return ImageFont.load_default()


def _indexed_map(
    reflectivity: np.ndarray,
    *,
    minimum_dbz: float,
) -> Image.Image:
    finite = np.isfinite(reflectivity) & (reflectivity >= minimum_dbz)
    indexes = np.zeros(reflectivity.shape, dtype=np.uint8)
    indexes[finite] = 1 + np.rint(
        np.clip((reflectivity[finite] + 20.0) / 95.0, 0.0, 1.0) * 253.0
    ).astype(np.uint8)
    image = Image.fromarray(np.flipud(indexes), mode="P")
    image.putpalette(GIF_PALETTE)
    return image


def _draw_boundaries(
    draw: ImageDraw.ImageDraw,
    *,
    width: int,
    height: int,
    header_height: int,
) -> None:
    west, east, south, north = CONUS_BOUNDS
    for ring in state_boundaries():
        points = tuple(
            (
                round((longitude - west) / (east - west) * (width - 1)),
                header_height
                + round((north - latitude) / (north - south) * (height - 1)),
            )
            for longitude, latitude in ring
        )
        if len(points) >= 2:
            draw.line(points, fill=255, width=max(1, width // 1200))


def _frame_image(
    day: NationalDay,
    frame_index: int,
    *,
    interval_seconds: float,
    tolerance_seconds: float,
    width: int,
    minimum_dbz: float,
) -> Image.Image:
    frames = aligned_frames(
        day,
        interval_seconds=interval_seconds,
        tolerance_seconds=tolerance_seconds,
    )
    frame = frames[frame_index]
    scans = tuple(open_scan(header.source_path) for header in frame.headers)
    maximum_range_km = max(
        float(scan.ground_range_edges_km[-1]) for scan in scans
    )
    _, _, reflectivity = national_mosaic_grid(
        scans,
        width=width,
        maximum_range_km=maximum_range_km,
    )
    map_image = _indexed_map(
        reflectivity,
        minimum_dbz=minimum_dbz,
    )
    height = map_image.height
    header_height = max(66, width // 15)
    canvas = Image.new("P", (width, height + header_height), color=0)
    canvas.putpalette(GIF_PALETTE)
    canvas.paste(map_image, (0, header_height))
    draw = ImageDraw.Draw(canvas)
    _draw_boundaries(
        draw,
        width=width,
        height=height,
        header_height=header_height,
    )
    title_font = _font(max(14, min(24, width // 50)))
    detail_font = _font(max(11, min(17, width // 72)))
    draw.text(
        (14, 7),
        f"CONUS base reflectivity · {frame.target_time:%Y-%m-%d %H:%M} UTC",
        fill=255,
        font=title_font,
    )
    draw.text(
        (14, 38),
        (
            f"Frame {frame_index + 1}/{len(frames)} · "
            f"{frame.site_count}/{day.site_count} sites · "
            f"nearest native scans · {minimum_dbz:g} to 75 dBZ"
        ),
        fill=255,
        font=detail_font,
    )
    # Preserve a frame timeline even when meteorology is visually unchanged.
    canvas.putpixel((width - 1, header_height - 1), 1 + frame_index % 254)
    return canvas


def render_national_gif(
    day: NationalDay,
    target: str | Path,
    *,
    frame_interval_minutes: float,
    alignment_tolerance_minutes: float,
    width: int,
    minimum_dbz: float,
    frame_duration_ms: int,
) -> Path:
    """Render all synchronized frames without interactive viewport reduction."""
    destination = Path(target).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    interval_seconds = frame_interval_minutes * 60.0
    tolerance_seconds = alignment_tolerance_minutes * 60.0
    frames = aligned_frames(
        day,
        interval_seconds=interval_seconds,
        tolerance_seconds=tolerance_seconds,
    )
    if not frames:
        raise ValueError(f"No aligned national frames exist for {day.date}")
    with TemporaryDirectory(
        dir=destination.parent,
        prefix=f".{destination.stem}-frames-",
    ) as frame_directory:
        frame_paths: list[Path] = []
        for index in range(len(frames)):
            image = _frame_image(
                day,
                index,
                interval_seconds=interval_seconds,
                tolerance_seconds=tolerance_seconds,
                width=width,
                minimum_dbz=minimum_dbz,
            )
            frame_path = Path(frame_directory) / f"{index:04d}.gif"
            image.save(frame_path, format="GIF", optimize=False)
            image.close()
            frame_paths.append(frame_path)
        opened = [Image.open(path) for path in frame_paths]
        with NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".gif",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
        try:
            opened[0].save(
                temporary,
                format="GIF",
                save_all=True,
                append_images=opened[1:],
                duration=frame_duration_ms,
                loop=0,
                disposal=2,
                optimize=False,
            )
            temporary.replace(destination)
        finally:
            for image in opened:
                image.close()
            temporary.unlink(missing_ok=True)
    return destination


class NationalGifBatch(Batch[NationalDay]):
    """One deterministic high-resolution mosaic animation per date."""

    item_actions = (
        CapabilityChoice(RENDER_NATIONAL_GIF, "Render national mosaic GIF"),
    )
    workspace_actions = (
        CapabilityChoice(
            RENDER_NATIONAL_GIF,
            "Render all national mosaic GIFs",
        ),
    )

    def __init__(
        self,
        output_root: str | Path,
        *,
        frame_interval_minutes: float,
        alignment_tolerance_minutes: float,
        width: int,
        minimum_dbz: float,
        frame_duration_ms: int,
    ) -> None:
        if frame_interval_minutes <= 0 or alignment_tolerance_minutes < 0:
            raise ValueError("national frame timing values are invalid")
        if width < 128:
            raise ValueError("national GIF width must be at least 128")
        if (
            not isfinite(minimum_dbz)
            or minimum_dbz < -20
            or minimum_dbz > 75
        ):
            raise ValueError("national GIF minimum dBZ must be from -20 to 75")
        if frame_duration_ms < 20:
            raise ValueError("national GIF duration must be at least 20 ms")
        self.output_root = Path(output_root).expanduser().resolve()
        self.frame_interval_minutes = float(frame_interval_minutes)
        self.alignment_tolerance_minutes = float(alignment_tolerance_minutes)
        self.width = int(width)
        self.minimum_dbz = float(minimum_dbz)
        self.frame_duration_ms = int(frame_duration_ms)

    def _filename(self, resource: DataResource) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", resource.identifier.lower()).strip("-")
        cadence = f"{self.frame_interval_minutes:g}".replace(".", "p")
        threshold = f"{self.minimum_dbz:g}".replace(".", "p")
        return (
            f"{slug}-conus-mosaic-{cadence}min-"
            f"min{threshold}dbz-{self.width}px-{self.frame_duration_ms}ms.gif"
        )

    def item_destination(
        self,
        resource: DataResource,
        request: BatchRequest,
    ) -> BatchDestination:
        return BatchDestination(
            self.output_root,
            (self._filename(resource),),
            "National NEXRAD mosaic GIF is ready",
        )

    def workspace_destination(
        self,
        resources: tuple[DataResource, ...],
        request: BatchRequest,
    ) -> BatchDestination:
        return BatchDestination(
            self.output_root,
            tuple(self._filename(resource) for resource in resources),
            "All national NEXRAD mosaic GIFs are ready",
        )

    def run_item(
        self,
        resource: DataResource,
        source_data: NationalDay,
        request: BatchRequest,
        directory: Path,
    ) -> BatchResult:
        output = render_national_gif(
            source_data,
            directory / self._filename(resource),
            frame_interval_minutes=self.frame_interval_minutes,
            alignment_tolerance_minutes=self.alignment_tolerance_minutes,
            width=self.width,
            minimum_dbz=self.minimum_dbz,
            frame_duration_ms=self.frame_duration_ms,
        )
        return BatchResult((output,), "Rendered national NEXRAD mosaic GIF")

    def run_workspace(
        self,
        resources: tuple[DataResource, ...],
        open_resource,
        request: BatchRequest,
        directory: Path,
    ) -> BatchResult:
        outputs = tuple(
            render_national_gif(
                open_resource(resource),
                directory / self._filename(resource),
                frame_interval_minutes=self.frame_interval_minutes,
                alignment_tolerance_minutes=self.alignment_tolerance_minutes,
                width=self.width,
                minimum_dbz=self.minimum_dbz,
                frame_duration_ms=self.frame_duration_ms,
            )
            for resource in resources
        )
        return BatchResult(
            outputs,
            f"Rendered {len(outputs)} national NEXRAD mosaic GIFs",
        )


__all__ = [
    "RENDER_NATIONAL_GIF",
    "NationalGifBatch",
    "render_national_gif",
]
