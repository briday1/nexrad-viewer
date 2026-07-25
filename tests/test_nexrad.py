import bz2
from io import BytesIO
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

import numpy as np

from nexrad_viewer.download import (
    CASE_PREFIXES,
    DEFAULT_CASES,
    _parse_listing_page,
    discover_case,
)
from nexrad_viewer.formats.nexrad import (
    NexradFormatError,
    read_level3_header,
    read_level3_radial,
)
from nexrad_viewer.reader import (
    describe_level3,
    level3_sequence_reader,
)
from nexrad_viewer.plots import ppi_figure


def _set_u16(message: bytearray, halfword: int, value: int) -> None:
    struct.pack_into(">H", message, (halfword - 1) * 2, value)


def _set_i16(message: bytearray, halfword: int, value: int) -> None:
    struct.pack_into(">h", message, (halfword - 1) * 2, value)


def _set_u32(message: bytearray, halfword: int, value: int) -> None:
    struct.pack_into(">I", message, (halfword - 1) * 2, value)


def _set_i32(message: bytearray, halfword: int, value: int) -> None:
    struct.pack_into(">i", message, (halfword - 1) * 2, value)


def synthetic_n0b(
    *,
    first_radial_bytes: int = 4,
    scan_seconds: int = 11_454,
    radar_id: str = "TLX",
) -> bytes:
    radial_data = (
        struct.pack(">HHH", first_radial_bytes, 3595, 5)
        + bytes((0, 1, 2, 255))
        + struct.pack(">HHH", 4, 0, 5)
        + bytes((4, 5, 6, 7))
    )
    packet = (
        struct.pack(
            ">HHHhhHH",
            16,
            0,
            4,
            0,
            0,
            999,
            2,
        )
        + radial_data
    )
    symbology = (
        struct.pack(">hHIH", -1, 1, 16 + len(packet), 1)
        + struct.pack(">hI", -1, len(packet))
        + packet
    )
    compressed = bz2.compress(symbology)

    message = bytearray(120)
    _set_u16(message, 1, 153)
    _set_u16(message, 2, 19_864)
    _set_u32(message, 3, 11_473)
    _set_u32(message, 5, 120 + len(compressed))
    _set_u16(message, 7, 1)
    _set_u16(message, 9, 3)
    _set_i16(message, 10, -1)
    _set_i32(message, 11, 35_333)
    _set_i32(message, 13, -97_278)
    _set_i16(message, 15, 1_277)
    _set_u16(message, 16, 153)
    _set_u16(message, 17, 2)
    _set_u16(message, 18, 212)
    _set_u16(message, 19, 2_930)
    _set_u16(message, 20, 32)
    _set_u16(message, 21, 19_864)
    _set_u32(message, 22, scan_seconds)
    _set_u16(message, 24, 19_864)
    _set_u32(message, 25, scan_seconds + 18)
    _set_u16(message, 29, 3)
    _set_i16(message, 30, 5)
    _set_i16(message, 31, -320)
    _set_i16(message, 32, 5)
    _set_u16(message, 33, 254)
    _set_i16(message, 47, 69)
    _set_u16(message, 51, 1)
    _set_u32(message, 52, len(symbology))
    _set_u32(message, 55, 60)
    heading = f"SDUS54 KOUN 200310\r\r\nN0B{radar_id}\r\r\n".encode("ascii")
    return heading + bytes(message) + compressed


class NexradLevel3ReaderTests(unittest.TestCase):
    def test_ten_default_download_cases_have_distinct_fixed_prefixes(self):
        self.assertEqual(10, len(DEFAULT_CASES))
        self.assertEqual(10, len(set(CASE_PREFIXES.values())))
        self.assertTrue(
            all(
                prefix.endswith("2024_05_20_03_")
                for prefix in CASE_PREFIXES.values()
            )
        )

    def test_archive_listing_becomes_size_and_etag_validated_downloads(self):
        prefix = CASE_PREFIXES["tlx-oklahoma-city"]
        payload = f"""<?xml version="1.0" encoding="UTF-8"?>
        <ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
          <IsTruncated>false</IsTruncated>
          <Contents>
            <Key>{prefix}01_13</Key>
            <ETag>"380c679778b106c30118aea036deab71"</ETag>
            <Size>335556</Size>
          </Contents>
        </ListBucketResult>
        """.encode()

        remotes, token = _parse_listing_page(payload, prefix=prefix)

        self.assertIsNone(token)
        self.assertEqual(1, len(remotes))
        self.assertEqual(335_556, remotes[0].size)
        self.assertEqual(
            "md5:380c679778b106c30118aea036deab71",
            remotes[0].checksum,
        )

    def test_case_discovery_uses_live_listing_without_a_manifest(self):
        prefix = CASE_PREFIXES["tlx-oklahoma-city"]
        payload = f"""<?xml version="1.0" encoding="UTF-8"?>
        <ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
          <IsTruncated>false</IsTruncated>
          <Contents>
            <Key>{prefix}01_13</Key>
            <ETag>"380c679778b106c30118aea036deab71"</ETag>
            <Size>335556</Size>
          </Contents>
        </ListBucketResult>
        """.encode()
        response = BytesIO(payload)

        with patch("nexrad_viewer.download.urlopen", return_value=response):
            remotes = discover_case("tlx-oklahoma-city")

        self.assertEqual((f"{prefix}01_13",), tuple(r.filename for r in remotes))

    def test_header_and_packet_16_preserve_native_codes_and_coordinates(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "TLX_N0B_2024_05_20_03_10_54"
            path.write_bytes(synthetic_n0b())

            header = read_level3_header(path)
            scan = read_level3_radial(path)

        self.assertEqual("N0B", header.product_id)
        self.assertEqual("TLX", header.radar_id)
        self.assertEqual(153, header.message_code)
        self.assertEqual("2024-05-20T03:10:54+00:00", header.scan_time.isoformat())
        self.assertEqual((2, 4), scan.level_codes.shape)
        np.testing.assert_array_equal(
            scan.level_codes,
            np.asarray(((0, 1, 2, 255), (4, 5, 6, 7)), dtype=np.uint8),
        )
        np.testing.assert_allclose(scan.azimuth_start_deg, (359.5, 0.0))
        np.testing.assert_allclose(scan.azimuth_width_deg, (0.5, 0.5))
        np.testing.assert_allclose(
            scan.slant_range_edges_km,
            (0.0, 0.25, 0.5, 0.75, 1.0),
        )
        decoded = scan.reflectivity_dbz()
        self.assertTrue(np.isnan(decoded[0, 0]))
        self.assertTrue(np.isnan(decoded[0, 1]))
        self.assertEqual(-32.0, float(decoded[0, 2]))
        self.assertEqual(94.5, float(decoded[0, 3]))
        self.assertEqual(
            {
                "measured": 6,
                "below_threshold": 1,
                "range_folded": 1,
                "padding": 0,
            },
            scan.code_counts(),
        )

    def test_discovery_description_reads_metadata_without_gate_inflation(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "TLX_N0B_2024_05_20_03_10_54"
            path.write_bytes(synthetic_n0b())
            resource = describe_level3(path)

        self.assertEqual(path.name, resource.identifier)
        self.assertIn("TLX N0B", resource.title)
        self.assertEqual(
            resource.timestamp.isoformat(),
            resource.summary["start"],
        )
        json.dumps(resource.summary)
        self.assertEqual(("NOAA", "NEXRAD", "Level III", "N0B"), resource.tags)

    def test_sequence_source_groups_chronological_scans_into_one_item(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "TLX_N0B_2024_05_20_03_10_54").write_bytes(
                synthetic_n0b(),
            )
            (root / "TLX_N0B_2024_05_20_03_20_54").write_bytes(
                synthetic_n0b(scan_seconds=12_054),
            )
            (root / "FDR_N0B_2024_05_20_03_10_05").write_bytes(
                synthetic_n0b(
                    scan_seconds=11_405,
                    radar_id="FDR",
                ),
            )
            reader = level3_sequence_reader(root)
            resources = reader.resources()
            sequences = {
                resource.identifier: reader.open(resource.source)
                for resource in resources
            }

        self.assertEqual(("FDR-N0B", "TLX-N0B"), tuple(sequences))
        self.assertEqual(1, sequences["FDR-N0B"].scan_count)
        self.assertEqual(2, sequences["TLX-N0B"].scan_count)
        self.assertEqual((0.0, 600.0), sequences["TLX-N0B"].elapsed_seconds)
        self.assertEqual(
            ("03:10:54", "03:20:54"),
            tuple(
                header.scan_time.strftime("%H:%M:%S")
                for header in sequences["TLX-N0B"].headers
            ),
        )

    def test_ppi_is_progressive_and_uses_compact_map_legends(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "TLX_N0B_2024_05_20_03_10_54"
            path.write_bytes(synthetic_n0b())
            scan = read_level3_radial(path)

        figure = ppi_figure(
            scan,
            maximum_range_km=1.0,
            pixels=32,
            colormap="NEXRAD",
            theme="light",
        )

        self.assertIsNone(figure.layout.height)
        self.assertTrue(figure._sigvue_viewport_heatmap)
        self.assertIsNone(figure.layout.xaxis.title.text)
        self.assertIsNone(figure.layout.yaxis.title.text)
        self.assertFalse(figure.layout.xaxis.showgrid)
        self.assertFalse(figure.layout.yaxis.showgrid)
        self.assertFalse(figure.layout.xaxis.showticklabels)
        self.assertFalse(figure.layout.yaxis.showticklabels)
        self.assertEqual((-1.0, 1.0), tuple(figure.layout.xaxis.range))
        self.assertEqual((-1.0, 1.0), tuple(figure.layout.yaxis.range))
        self.assertEqual(5, len(figure.layout.shapes))
        self.assertEqual(
            ["<b>N</b>", "<b>E</b>", "<b>1 km</b>"],
            [annotation.text for annotation in figure.layout.annotations],
        )

    def test_radial_byte_count_must_match_declared_gate_count(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bad.nids"
            path.write_bytes(synthetic_n0b(first_radial_bytes=3))
            with self.assertRaisesRegex(
                NexradFormatError,
                "radial byte count",
            ):
                read_level3_radial(path)


if __name__ == "__main__":
    unittest.main()
