import bz2
import json
import struct
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
from PIL import Image
from plotly import colors as plotly_colors

from nexrad_viewer.batch import NEXRAD_GIF_PALETTE, RENDER_GIF
from nexrad_viewer.formats.nexrad import (
    NexradFormatError,
    read_level3_header,
    read_level3_radial,
)
from nexrad_viewer.national.analysis import national_mosaic_grid
from nexrad_viewer.national.batch import RENDER_NATIONAL_GIF, _indexed_map
from nexrad_viewer.national.reader import aligned_frames, discover_days
from nexrad_viewer.national.workspace import (
    create_workspace as create_national_workspace,
)
from nexrad_viewer.plots import (
    NEXRAD_COLORSCALE,
    bounded_reflectivity_colorscale,
    ppi_figure,
)
from nexrad_viewer.reader import (
    describe_level3,
    level3_sequence_reader,
)
from nexrad_viewer.workspace import create_workspace
from scripts.download_data import (
    EXAMPLE_DATE,
    EXAMPLE_SITES,
    _daily_prefix,
    _parse_listing_page,
    discover_site_day,
    parse_station_catalog,
)


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
    wide_radials: bool = False,
    level_codes: tuple[bytes, bytes] = (
        bytes((0, 1, 2, 255)),
        bytes((4, 5, 6, 7)),
    ),
) -> bytes:
    first_start, second_start, radial_width = (
        (0, 1800, 1800) if wide_radials else (3595, 0, 5)
    )
    radial_data = (
        struct.pack(">HHH", first_radial_bytes, first_start, radial_width)
        + level_codes[0]
        + struct.pack(">HHH", 4, second_start, radial_width)
        + level_codes[1]
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
    def test_ten_default_sites_share_one_date_oriented_archive_prefix(self):
        self.assertEqual(10, len(EXAMPLE_SITES))
        prefixes = tuple(_daily_prefix(site, EXAMPLE_DATE) for site in EXAMPLE_SITES)
        self.assertEqual(10, len(set(prefixes)))
        self.assertTrue(all(prefix.endswith("2024_05_20_") for prefix in prefixes))

    def test_archive_listing_becomes_size_and_etag_validated_downloads(self):
        prefix = _daily_prefix("KTLX", EXAMPLE_DATE)
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

    def test_site_day_discovery_uses_live_listing_without_a_manifest(self):
        prefix = _daily_prefix("KTLX", EXAMPLE_DATE)
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
        with patch("scripts.download_data._request_bytes", return_value=payload):
            remotes = discover_site_day("KTLX", EXAMPLE_DATE)

        self.assertEqual((f"{prefix}01_13",), tuple(r.filename for r in remotes))

    def test_station_catalog_parser_identifies_conus_sites(self):
        header = (
            "NCDCID   ICAO WBAN  NAME                           COUNTRY              "
            "ST COUNTY                         LAT       LON        ELEV   UTC   "
            "STNTYPE                                            "
        )
        row = (
            "30001794 KTLX 12345 OKLAHOMA CITY                  UNITED STATES        "
            "OK CLEVELAND                      35.333361 -97.277761 1277   -6    "
            "NEXRAD                                             "
        )
        stations = parse_station_catalog(
            f"{header}\n{'-' * len(header)}\n{row}\n".encode()
        )

        self.assertEqual(("KTLX",), tuple(station.identifier for station in stations))
        self.assertTrue(stations[0].is_conus)

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
            path.write_bytes(
                synthetic_n0b(
                    wide_radials=True,
                    level_codes=(
                        bytes((2, 100, 255, 100)),
                        bytes((2, 100, 255, 100)),
                    ),
                )
            )
            scan = read_level3_radial(path)

        figure = ppi_figure(
            scan,
            maximum_range_km=1.0,
            pixels=32,
            colormap="NEXRAD",
            theme="light",
            reflectivity_limits=(-10.0, 55.0),
        )

        self.assertIsNone(figure.layout.height)
        self.assertTrue(figure._sigvue_viewport_heatmap)
        self.assertEqual(-10.0, figure.data[0].zmin)
        self.assertEqual(55.0, figure.data[0].zmax)
        visible_dbz = np.asarray(figure.data[0].z, dtype=float)
        self.assertLess(float(np.nanmin(visible_dbz)), -10.0)
        self.assertGreater(float(np.nanmax(visible_dbz)), 55.0)
        cropped = bounded_reflectivity_colorscale("NEXRAD", (-10.0, 55.0))
        expected_floor_color = plotly_colors.sample_colorscale(
            [list(stop) for stop in NEXRAD_COLORSCALE],
            [10.0 / 95.0],
            colortype="rgb",
        )[0]
        self.assertEqual(expected_floor_color, cropped[0][1])
        self.assertIsNone(figure.layout.xaxis.title.text)
        self.assertIsNone(figure.layout.yaxis.title.text)
        self.assertFalse(figure.layout.xaxis.showgrid)
        self.assertFalse(figure.layout.yaxis.showgrid)
        self.assertFalse(figure.layout.xaxis.showticklabels)
        self.assertFalse(figure.layout.yaxis.showticklabels)
        self.assertAlmostEqual(-97.289, figure.layout.xaxis.range[0], places=3)
        self.assertAlmostEqual(-97.267, figure.layout.xaxis.range[1], places=3)
        self.assertAlmostEqual(35.324, figure.layout.yaxis.range[0], places=3)
        self.assertAlmostEqual(35.342, figure.layout.yaxis.range[1], places=3)
        self.assertEqual("domain", figure.layout.xaxis.constrain)
        self.assertEqual("domain", figure.layout.yaxis.constrain)
        self.assertEqual(
            ["heatmap", "scatter", "scatter"], [t.type for t in figure.data]
        )
        self.assertEqual("TLX", figure.data[-1].name)

    def test_histogram_limit_is_exact_for_sequence_and_batch_renders_gif(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "outputs"
            first = root / "TLX_N0B_2024_05_20_03_10_54"
            second = root / "TLX_N0B_2024_05_20_03_20_54"
            first.write_bytes(synthetic_n0b())
            second.write_bytes(
                synthetic_n0b(
                    scan_seconds=12_054,
                    wide_radials=True,
                    level_codes=(
                        bytes((100, 100, 100, 100)),
                        bytes((100, 100, 100, 100)),
                    ),
                )
            )
            workspace = create_workspace(
                {
                    "data_root": root,
                    "output_root": output,
                    "gif_frame_duration_ms": 100,
                }
            )
            resource = workspace.discover_items()[0]
            opened = workspace.open_item(resource.identifier)
            controls = {control.name: control for control in opened.page.controls}
            histogram = opened.page.views[1].callback({})
            destination = workspace.item_batch_destination(
                resource.identifier,
                RENDER_GIF,
            )
            result = workspace.run_item_batch(
                resource.identifier,
                RENDER_GIF,
                output,
            )
            workspace_destination = workspace.workspace_batch_destination(
                RENDER_GIF,
            )
            workspace_result = workspace.run_workspace_batch(
                RENDER_GIF,
                output,
            )
            with Image.open(result.files[0]) as animation:
                frame_count = animation.n_frames
                frame_size = animation.size
                frame_duration = animation.info["duration"]

        self.assertEqual((0, 9), tuple(histogram.layout.yaxis.range))
        reflectivity_limits = controls["weather_radar_reflectivity_limits"]
        self.assertEqual("limits", reflectivity_limits.control_type)
        self.assertLess(
            reflectivity_limits.default[0],
            reflectivity_limits.default[1],
        )
        self.assertFalse(workspace.flatten_discovery)
        self.assertEqual((), resource.navigation_path)
        self.assertEqual(1, len(destination.files))
        self.assertIn("-nexrad-dark-dbzbar-", destination.files[0])
        self.assertEqual(destination.files[0], result.files[0].name)
        self.assertEqual(destination.files, workspace_destination.files)
        self.assertEqual(result.files, workspace_result.files)
        self.assertEqual(2, frame_count)
        self.assertEqual((164, 272), frame_size)
        self.assertEqual(100, frame_duration)

    def test_radial_byte_count_must_match_declared_gate_count(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bad.nids"
            path.write_bytes(synthetic_n0b(first_radial_bytes=3))
            with self.assertRaisesRegex(
                NexradFormatError,
                "radial byte count",
            ):
                read_level3_radial(path)

    def test_national_day_uses_date_folder_and_nearest_exact_site_scans(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            dated = root / "2024-05-20"
            dated.mkdir()
            for radar, second in (("TLX", 11_454), ("FDR", 11_405)):
                (dated / f"{radar}_N0B_2024_05_20_03_10_00").write_bytes(
                    synthetic_n0b(scan_seconds=second, radar_id=radar)
                )
            # Atomic downloader state must be categorically invisible.
            (dated / ".TLX_N0B_2024_05_20_03_20_00.deadbeef.part").write_bytes(b"")
            days = discover_days(root)
            frames = aligned_frames(
                days[0],
                interval_seconds=600,
                tolerance_seconds=300,
            )
            scans = tuple(
                read_level3_radial(header.source_path) for header in frames[0].headers
            )
            longitudes, latitudes, mosaic = national_mosaic_grid(
                scans,
                width=128,
                maximum_range_km=1.0,
            )

        self.assertEqual((date(2024, 5, 20),), tuple(day.date for day in days))
        self.assertEqual(2, days[0].site_count)
        self.assertEqual(1, len(frames))
        self.assertEqual(("FDR", "TLX"), tuple(h.radar_id for h in frames[0].headers))
        self.assertEqual(128, len(longitudes))
        self.assertGreater(len(latitudes), 64)
        self.assertEqual((len(latitudes), 128), mosaic.shape)

    def test_national_gif_hides_echoes_below_its_event_threshold(self):
        reflectivity = np.asarray(
            ((np.nan, -20.0, 19.99, 20.0, 35.0, 90.0),),
            dtype=np.float32,
        )

        image = _indexed_map(reflectivity, minimum_dbz=20.0)
        indexes = np.asarray(image)
        palette = image.getpalette()
        image.close()

        np.testing.assert_array_equal(indexes[0, :3], (0, 0, 0))
        self.assertGreater(int(indexes[0, 3]), 0)
        self.assertGreater(int(indexes[0, 4]), int(indexes[0, 3]))
        self.assertEqual(254, int(indexes[0, 5]))
        self.assertEqual(NEXRAD_GIF_PALETTE, palette)
        self.assertEqual((100, 100, 100), tuple(palette[3:6]))
        self.assertEqual((248, 0, 253), tuple(palette[-6:-3]))

    def test_national_workspace_discovers_one_date_and_renders_durable_gif(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            dated = root / "2024-05-20"
            output = root / "outputs"
            dated.mkdir()
            (dated / "TLX_N0B_2024_05_20_03_10_54").write_bytes(synthetic_n0b())
            (dated / "TLX_N0B_2024_05_20_04_10_54").write_bytes(
                synthetic_n0b(scan_seconds=15_054)
            )
            workspace = create_national_workspace(
                {
                    "data_root": root,
                    "output_root": output,
                    "national_frame_interval_minutes": 60,
                    "national_alignment_tolerance_minutes": 30,
                    "national_gif_width": 128,
                    "national_gif_minimum_dbz": 20,
                    "national_gif_frame_duration_ms": 100,
                }
            )
            resource = workspace.discover_items()[0]
            opened = workspace.open_item(resource.identifier)
            controls = {control.name: control for control in opened.page.controls}
            destination = workspace.item_batch_destination(
                resource.identifier,
                RENDER_NATIONAL_GIF,
            )
            result = workspace.run_item_batch(
                resource.identifier,
                RENDER_NATIONAL_GIF,
                output,
            )
            with Image.open(result.files[0]) as animation:
                frame_count = animation.n_frames

        self.assertEqual("2024-05-20", resource.identifier)
        self.assertEqual(2, len(opened.page.playback.segments))
        radius = controls["national_radar_radius_km"]
        reflectivity_limits = controls["national_reflectivity_limits"]
        colormap = controls["national_colormap"]
        self.assertEqual("float", radius.control_type)
        self.assertEqual(1.0, radius.default)
        self.assertEqual(1.0, radius.maximum)
        self.assertEqual("limits", reflectivity_limits.control_type)
        self.assertLess(
            reflectivity_limits.default[0],
            reflectivity_limits.default[1],
        )
        self.assertEqual("NEXRAD", colormap.default)
        self.assertEqual(1, len(destination.files))
        self.assertIn("-conus-mosaic-nexrad-", destination.files[0])
        self.assertIn("-min20dbz-", destination.files[0])
        self.assertEqual(destination.files[0], result.files[0].name)
        self.assertEqual(2, frame_count)


if __name__ == "__main__":
    unittest.main()
