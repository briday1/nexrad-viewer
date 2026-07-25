"""Minimal, validated NOAA NEXRAD Level III packet-16 reader.

The implementation follows NOAA ROC ICD 2620001, Figure 3-6 for the
Graphic Product Message and Figure 3-11c for the Digital Radial Data Array
packet. It intentionally supports the 256-level base-reflectivity products
used by the weather-radar example and fails explicitly on other packet
families rather than guessing at their encodings.
"""

from __future__ import annotations

import bz2
from datetime import datetime, timedelta, timezone
import gzip
from pathlib import Path
import re
import struct

import numpy as np

from .models import NexradLevel3Header, NexradLevel3Radial


MESSAGE_AND_DESCRIPTION_BYTES = 120
PACKET_CODE_DIGITAL_RADIAL = 16
PRODUCT_CODE_SUPER_RESOLUTION_REFLECTIVITY = 153
COMPRESSION_NONE = 0
COMPRESSION_BZIP2 = 1
MAX_UNCOMPRESSED_PAYLOAD_BYTES = 64 * 1024 * 1024
_WMO_TERMINATOR = b"\r\r\n"
_PRODUCT_LINE = re.compile(r"^[A-Z0-9]{6}$")


class NexradFormatError(ValueError):
    """Raised when a file does not satisfy the supported Level III format."""


def _u16(data: bytes, halfword: int) -> int:
    return struct.unpack_from(">H", data, (halfword - 1) * 2)[0]


def _i16(data: bytes, halfword: int) -> int:
    return struct.unpack_from(">h", data, (halfword - 1) * 2)[0]


def _u32(data: bytes, halfword: int) -> int:
    return struct.unpack_from(">I", data, (halfword - 1) * 2)[0]


def _i32(data: bytes, halfword: int) -> int:
    return struct.unpack_from(">i", data, (halfword - 1) * 2)[0]


def _nexrad_datetime(day: int, seconds: int) -> datetime:
    if day < 1 or not 0 <= seconds < 86_400:
        raise NexradFormatError("invalid NEXRAD date/time field")
    return datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(
        days=day - 1,
        seconds=seconds,
    )


def _unwrap_file(path: Path) -> tuple[bytes, int]:
    raw = path.read_bytes()
    file_size = len(raw)
    if raw.startswith(b"\x1f\x8b"):
        try:
            raw = gzip.decompress(raw)
        except (EOFError, OSError) as error:
            raise NexradFormatError("invalid outer gzip stream") from error
    return raw, file_size


def _message_start(data: bytes) -> tuple[int, str, str, str]:
    """Locate a binary message after the optional WMO transmission header."""
    candidates = [0]
    search_from = 0
    while True:
        marker = data.find(_WMO_TERMINATOR, search_from, min(len(data), 512))
        if marker < 0:
            break
        candidates.append(marker + len(_WMO_TERMINATOR))
        search_from = marker + len(_WMO_TERMINATOR)

    for offset in reversed(candidates):
        if len(data) - offset < MESSAGE_AND_DESCRIPTION_BYTES:
            continue
        message = data[offset:]
        if _i16(message, 10) == -1 and _u16(message, 1) == _u16(message, 16):
            text = data[:offset].decode("ascii", errors="ignore")
            lines = [
                line.strip()
                for line in text.replace("\x01", "").splitlines()
                if line.strip()
            ]
            product_line = next(
                (line for line in reversed(lines) if _PRODUCT_LINE.fullmatch(line)),
                "",
            )
            product_id = product_line[:3] or f"P{_u16(message, 1)}"
            radar_id = product_line[3:] or "UNK"
            heading = lines[0] if lines else ""
            return offset, heading, product_id, radar_id
    raise NexradFormatError("could not locate a Level III message header")


def _parse_header(
    data: bytes,
    *,
    source_path: Path,
    file_size_bytes: int,
) -> tuple[NexradLevel3Header, bytes]:
    offset, wmo_heading, product_id, radar_id = _message_start(data)
    available = data[offset:]
    message_length = _u32(available, 5)
    if message_length < MESSAGE_AND_DESCRIPTION_BYTES:
        raise NexradFormatError("Level III message length is too small")
    if message_length > len(available):
        raise NexradFormatError(
            "Level III message length exceeds the available file bytes"
        )
    message = available[:message_length]
    code = _u16(message, 1)
    if code != _u16(message, 16):
        raise NexradFormatError("message and product codes disagree")
    if code != PRODUCT_CODE_SUPER_RESOLUTION_REFLECTIVITY:
        raise NexradFormatError(
            "only product code 153 super-resolution reflectivity is supported"
        )
    if _i16(message, 10) != -1:
        raise NexradFormatError("missing Product Description divider")

    version_and_spot = _u16(message, 54)
    header = NexradLevel3Header(
        source_path=source_path,
        file_size_bytes=file_size_bytes,
        wmo_heading=wmo_heading,
        product_id=product_id,
        radar_id=radar_id,
        message_code=code,
        message_length_bytes=message_length,
        message_time=_nexrad_datetime(_u16(message, 2), _u32(message, 3)),
        scan_time=_nexrad_datetime(_u16(message, 21), _u32(message, 22)),
        generation_time=_nexrad_datetime(
            _u16(message, 24),
            _u32(message, 25),
        ),
        latitude_deg=_i32(message, 11) / 1_000.0,
        longitude_deg=_i32(message, 13) / 1_000.0,
        altitude_ft=_i16(message, 15),
        operational_mode=_u16(message, 17),
        volume_coverage_pattern=_u16(message, 18),
        sequence_number=_u16(message, 19),
        volume_scan_number=_u16(message, 20),
        elevation_number=_u16(message, 29),
        elevation_deg=_i16(message, 30) / 10.0,
        minimum_value_dbz=_i16(message, 31) / 10.0,
        value_increment_dbz=_i16(message, 32) / 10.0,
        measured_level_count=_u16(message, 33),
        maximum_value_dbz=float(_i16(message, 47)),
        compression_method=_u16(message, 51),
        uncompressed_payload_bytes=_u32(message, 52),
        product_version=version_and_spot >> 8,
        spot_blank=bool(version_and_spot & 0xFF),
        symbology_offset_halfwords=_u32(message, 55),
    )
    if header.measured_level_count != 254:
        raise NexradFormatError("unexpected reflectivity data-level count")
    if header.minimum_value_dbz != -32.0 or header.value_increment_dbz != 0.5:
        raise NexradFormatError("unsupported reflectivity scale/offset")
    return header, message


def read_level3_header(path: str | Path) -> NexradLevel3Header:
    """Read fixed metadata without inflating the product symbology."""
    source_path = Path(path).expanduser().resolve()
    data, file_size = _unwrap_file(source_path)
    header, _ = _parse_header(
        data,
        source_path=source_path,
        file_size_bytes=file_size,
    )
    return header


def _inflate_payload(header: NexradLevel3Header, message: bytes) -> bytes:
    payload_offset = header.symbology_offset_halfwords * 2
    if payload_offset < MESSAGE_AND_DESCRIPTION_BYTES:
        raise NexradFormatError("invalid symbology offset")
    if header.uncompressed_payload_bytes > MAX_UNCOMPRESSED_PAYLOAD_BYTES:
        raise NexradFormatError("declared uncompressed payload is too large")

    if header.compression_method == COMPRESSION_NONE:
        payload = message[payload_offset:]
    elif header.compression_method == COMPRESSION_BZIP2:
        compressed = message[MESSAGE_AND_DESCRIPTION_BYTES:]
        try:
            payload = bz2.decompress(compressed)
        except OSError as error:
            raise NexradFormatError("invalid BZip2 product payload") from error
    else:
        raise NexradFormatError(
            f"unsupported compression method {header.compression_method}"
        )

    if (
        header.uncompressed_payload_bytes
        and len(payload) != header.uncompressed_payload_bytes
    ):
        raise NexradFormatError(
            "inflated payload size does not match the Product Description"
        )
    return payload


def _parse_packet_16(
    payload: bytes,
    *,
    header: NexradLevel3Header,
) -> NexradLevel3Radial:
    if len(payload) < 16:
        raise NexradFormatError("truncated Product Symbology block")
    divider, block_id, block_length, layer_count = struct.unpack_from(
        ">hHIH",
        payload,
        0,
    )
    if divider != -1 or block_id != 1:
        raise NexradFormatError("invalid Product Symbology block header")
    if block_length != len(payload):
        raise NexradFormatError("Product Symbology block length mismatch")
    if layer_count != 1:
        raise NexradFormatError("base reflectivity must contain one data layer")

    layer_divider, layer_length = struct.unpack_from(">hI", payload, 10)
    if layer_divider != -1 or layer_length != len(payload) - 16:
        raise NexradFormatError("invalid Product Symbology layer")
    (
        packet_code,
        first_range_bin,
        range_bin_count,
        i_center,
        j_center,
        range_scale,
        radial_count,
    ) = struct.unpack_from(">HHHhhHH", payload, 16)
    if packet_code != PACKET_CODE_DIGITAL_RADIAL:
        raise NexradFormatError(f"expected packet code 16, received {packet_code}")
    if range_bin_count < 1 or radial_count < 1:
        raise NexradFormatError("digital radial packet has empty dimensions")

    codes = np.zeros((radial_count, range_bin_count), dtype=np.uint8)
    gate_counts = np.empty(radial_count, dtype=np.uint16)
    starts = np.empty(radial_count, dtype=np.float32)
    widths = np.empty(radial_count, dtype=np.float32)
    position = 30
    for radial in range(radial_count):
        if position + 6 > len(payload):
            raise NexradFormatError("truncated digital radial header")
        byte_count, start_tenths, width_tenths = struct.unpack_from(
            ">HHH",
            payload,
            position,
        )
        position += 6
        if position + byte_count > len(payload):
            raise NexradFormatError("truncated digital radial gate data")
        if byte_count not in {range_bin_count, range_bin_count + 1}:
            raise NexradFormatError(
                "radial byte count does not match the declared range bins"
            )
        codes[radial] = np.frombuffer(
            payload,
            dtype=np.uint8,
            count=range_bin_count,
            offset=position,
        )
        gate_counts[radial] = range_bin_count
        starts[radial] = start_tenths / 10.0
        widths[radial] = width_tenths / 10.0
        position += byte_count
    if position != len(payload):
        raise NexradFormatError("unexpected bytes after digital radial packet")

    for array in (codes, gate_counts, starts, widths):
        array.flags.writeable = False
    return NexradLevel3Radial(
        header=header,
        packet_code=packet_code,
        first_range_bin=first_range_bin,
        gate_size_km=0.25,
        i_center_km=i_center / 4.0,
        j_center_km=j_center / 4.0,
        ground_range_scale=range_scale / 1_000.0,
        level_codes=codes,
        radial_gate_counts=gate_counts,
        azimuth_start_deg=starts,
        azimuth_width_deg=widths,
    )


def read_level3_radial(path: str | Path) -> NexradLevel3Radial:
    """Read one code-153 Level III product without changing native gate values."""
    source_path = Path(path).expanduser().resolve()
    data, file_size = _unwrap_file(source_path)
    header, message = _parse_header(
        data,
        source_path=source_path,
        file_size_bytes=file_size,
    )
    return _parse_packet_16(
        _inflate_payload(header, message),
        header=header,
    )


__all__ = [
    "COMPRESSION_BZIP2",
    "COMPRESSION_NONE",
    "NexradFormatError",
    "PACKET_CODE_DIGITAL_RADIAL",
    "PRODUCT_CODE_SUPER_RESOLUTION_REFLECTIVITY",
    "read_level3_header",
    "read_level3_radial",
]
