"""Shared indexed-map and GIF composition for every radar batch renderer."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .plots import NEXRAD_COLORSCALE, REFLECTIVITY_DOMAIN


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


NEXRAD_GIF_PALETTE = _palette()


def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except OSError:
        return ImageFont.load_default()


def dbz_indexes(values) -> np.ndarray:
    lower, upper = REFLECTIVITY_DOMAIN
    return 1 + np.rint(
        np.clip((np.asarray(values) - lower) / (upper - lower), 0.0, 1.0)
        * 253.0
    ).astype(np.uint8)


def indexed_reflectivity(
    reflectivity: np.ndarray,
    *,
    minimum_visible_dbz: float | None = None,
) -> Image.Image:
    finite = np.isfinite(reflectivity)
    if minimum_visible_dbz is not None:
        finite &= reflectivity >= minimum_visible_dbz
    indexes = np.zeros(reflectivity.shape, dtype=np.uint8)
    indexes[finite] = dbz_indexes(reflectivity[finite])
    image = Image.fromarray(np.flipud(indexes), mode="P")
    image.putpalette(NEXRAD_GIF_PALETTE)
    return image


def _draw_boundaries(
    draw: ImageDraw.ImageDraw,
    *,
    bounds: tuple[float, float, float, float],
    rings: Iterable[Iterable[tuple[float, float]]],
    width: int,
    height: int,
    map_top: int,
) -> None:
    west, east, south, north = bounds
    for ring in rings:
        points = tuple(
            (
                round((longitude - west) / (east - west) * (width - 1)),
                map_top
                + round((north - latitude) / (north - south) * (height - 1)),
            )
            for longitude, latitude in ring
        )
        if len(points) >= 2:
            draw.line(
                points,
                fill=(232, 241, 243),
                width=max(1, width // 1200),
                joint="curve",
            )


def _draw_colorbar(
    draw: ImageDraw.ImageDraw,
    *,
    width: int,
    top: int,
    limits: tuple[float, float],
) -> None:
    minimum_dbz, maximum_dbz = limits
    span = maximum_dbz - minimum_dbz
    if span < 0:
        raise ValueError("reflectivity colorbar limits must be ordered")
    left = max(18, width // 10)
    right = width - left
    bar_top = top + 8
    bar_height = max(10, width // 75)
    for x in range(left, right):
        fraction = (x - left) / max(1, right - left - 1)
        dbz = minimum_dbz + fraction * span
        index = int(dbz_indexes((dbz,))[0])
        color = tuple(NEXRAD_GIF_PALETTE[index * 3 : index * 3 + 3])
        draw.line((x, bar_top, x, bar_top + bar_height), fill=color)
    tick_step = 10.0 if span <= 60 else 20.0
    interior = (
        np.arange(
            np.ceil(minimum_dbz / tick_step) * tick_step,
            maximum_dbz,
            tick_step,
        )
        if span > 0
        else ()
    )
    ticks = tuple(dict.fromkeys((minimum_dbz, *interior, maximum_dbz)))
    font = _font(max(12, width // 60))
    for tick in ticks:
        x = (
            round(
                left
                + (tick - minimum_dbz)
                / span
                * (right - left)
            )
            if span > 0
            else round((left + right) / 2)
        )
        draw.line(
            (x, bar_top + bar_height, x, bar_top + bar_height + 4),
            fill=(255, 255, 255),
        )
        label = f"{tick:g}"
        box = draw.textbbox((0, 0), label, font=font)
        draw.text(
            (x - (box[2] - box[0]) / 2, bar_top + bar_height + 6),
            label,
            fill=(255, 255, 255),
            font=font,
        )
    draw.text(
        (width / 2, bar_top + bar_height + 32),
        "Reflectivity (dBZ)",
        fill=(255, 255, 255),
        font=font,
        anchor="ma",
    )


def _colorbar_footer_height(width: int) -> int:
    return max(76, width // 13)


def compose_frame(
    map_image: Image.Image,
    *,
    timestamp,
    bounds: tuple[float, float, float, float],
    boundary_rings: Iterable[Iterable[tuple[float, float]]],
    reflectivity_limits: tuple[float, float],
    timeline_index: int,
    site_label: str | None = None,
) -> Image.Image:
    """Compose the one canonical radar GIF frame layout."""
    width, height = map_image.size
    header_height = max(56, width // 18)
    footer_height = _colorbar_footer_height(width)
    canvas = Image.new(
        "RGB",
        (width, height + header_height + footer_height),
        color=(8, 17, 23),
    )
    canvas.paste(map_image.convert("RGB"), (0, header_height))
    draw = ImageDraw.Draw(canvas)
    _draw_boundaries(
        draw,
        bounds=bounds,
        rings=boundary_rings,
        width=width,
        height=height,
        map_top=header_height,
    )
    draw.text(
        (14, 7),
        (
            f"{timestamp:%Y-%m-%d %H:%M:%S} UTC"
            + (f" · {site_label}" if site_label else "")
        ),
        fill=(255, 255, 255),
        font=_font(max(20, width // 30)),
    )
    _draw_colorbar(
        draw,
        width=width,
        top=header_height + height,
        limits=reflectivity_limits,
    )
    index = 1 + timeline_index % 254
    canvas.putpixel(
        (width - 1, header_height - 1),
        tuple(NEXRAD_GIF_PALETTE[index * 3 : index * 3 + 3]),
    )
    return canvas


def compose_reflectivity_frame(
    reflectivity: np.ndarray,
    *,
    timestamp,
    bounds: tuple[float, float, float, float],
    boundary_rings: Iterable[Iterable[tuple[float, float]]],
    reflectivity_limits: tuple[float, float],
    timeline_index: int,
    site_label: str | None = None,
) -> Image.Image:
    """Map reflectivity and compose it through the one canonical frame path."""
    map_image = indexed_reflectivity(
        reflectivity,
        minimum_visible_dbz=reflectivity_limits[0],
    )
    try:
        return compose_frame(
            map_image,
            timestamp=timestamp,
            bounds=bounds,
            boundary_rings=boundary_rings,
            reflectivity_limits=reflectivity_limits,
            timeline_index=timeline_index,
            site_label=site_label,
        )
    finally:
        map_image.close()


def compose_circular_frame(
    reflectivity: np.ndarray,
    *,
    timestamp,
    radar_id: str,
    product_id: str,
    radius_km: float,
    frame_index: int,
    frame_count: int,
) -> Image.Image:
    """Compose the historical radar-centered PPI with native-scale rings."""
    map_image = indexed_reflectivity(reflectivity)
    width, height = map_image.size
    header_height = 68
    footer_height = _colorbar_footer_height(width)
    canvas = Image.new(
        "RGB",
        (width, height + header_height + footer_height),
        color=(8, 17, 23),
    )
    try:
        canvas.paste(map_image.convert("RGB"), (0, header_height))
    finally:
        map_image.close()
    draw = ImageDraw.Draw(canvas)
    title_font = _font(max(13, min(22, width // 42)))
    detail_font = _font(max(11, min(16, width // 56)))
    draw.text(
        (12, 7),
        f"{radar_id} {product_id} {timestamp:%Y-%m-%d %H:%M:%S} UTC",
        fill=(255, 255, 255),
        font=title_font,
    )
    draw.text(
        (12, 36),
        (
            f"Frame {frame_index + 1}/{frame_count} · "
            f"radius {radius_km:g} km · -20 to 75 dBZ"
        ),
        fill=(255, 255, 255),
        font=detail_font,
    )
    center_x = width / 2.0
    center_y = header_height + height / 2.0
    line_width = max(1, width // 640)
    for fraction in (0.25, 0.5, 0.75, 1.0):
        ring = fraction * min(width, height) / 2.0
        draw.ellipse(
            (
                center_x - ring,
                center_y - ring,
                center_x + ring,
                center_y + ring,
            ),
            outline=(255, 255, 255),
            width=line_width,
        )
    marker = max(3, width // 180)
    draw.ellipse(
        (
            center_x - marker,
            center_y - marker,
            center_x + marker,
            center_y + marker,
        ),
        fill=(255, 255, 255),
    )
    _draw_colorbar(
        draw,
        width=width,
        top=header_height + height,
        limits=REFLECTIVITY_DOMAIN,
    )
    # Preserve distinct GIF frames when the meteorological pixels are equal.
    canvas.putpixel(
        (width - 1, header_height - 1),
        tuple(
            NEXRAD_GIF_PALETTE[
                (1 + frame_index % 254) * 3 : (2 + frame_index % 254) * 3
            ]
        ),
    )
    return canvas


def compose_conus_frame(
    scans,
    *,
    width: int,
    maximum_range_km: float,
    timestamp,
    reflectivity_limits: tuple[float, float],
    timeline_index: int,
    site_label: str | None = None,
) -> Image.Image:
    """Render the canonical CONUS frame from one or many radar scans."""
    from .national.analysis import CONUS_BOUNDS, national_mosaic_grid
    from .national.geography import state_boundaries

    _, _, reflectivity = national_mosaic_grid(
        scans,
        width=width,
        maximum_range_km=maximum_range_km,
    )
    return compose_reflectivity_frame(
        reflectivity,
        timestamp=timestamp,
        bounds=CONUS_BOUNDS,
        boundary_rings=state_boundaries(),
        reflectivity_limits=reflectivity_limits,
        timeline_index=timeline_index,
        site_label=site_label,
    )


def render_animation(
    target: str | Path,
    *,
    frame_count: int,
    frame_duration_ms: int,
    frame_builder: Callable[[int], Image.Image],
    cancel: Callable[[], None] | None = None,
) -> Path:
    """Render PNG intermediates and atomically encode one GIF animation."""
    if frame_count <= 0:
        raise ValueError("GIF animation requires at least one frame")
    if frame_duration_ms < 20:
        raise ValueError("GIF frame duration must be at least 20 ms")
    destination = Path(target).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        dir=destination.parent,
        prefix=f".{destination.stem}-frames-",
    ) as frame_directory:
        frame_paths: list[Path] = []
        for index in range(frame_count):
            if cancel is not None:
                cancel()
            image = frame_builder(index)
            frame_path = Path(frame_directory) / f"{index:04d}.png"
            image.save(frame_path, format="PNG")
            image.close()
            frame_paths.append(frame_path)
        if cancel is not None:
            cancel()
        opened = [Image.open(path) for path in frame_paths]
        with NamedTemporaryFile(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".gif",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
        try:
            if cancel is not None:
                cancel()
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
            if cancel is not None:
                cancel()
            temporary.replace(destination)
        finally:
            for image in opened:
                image.close()
            temporary.unlink(missing_ok=True)
    return destination


__all__ = [
    "NEXRAD_GIF_PALETTE",
    "compose_circular_frame",
    "compose_conus_frame",
    "compose_frame",
    "compose_reflectivity_frame",
    "dbz_indexes",
    "indexed_reflectivity",
    "render_animation",
]
