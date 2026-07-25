"""Download shared, date-oriented NOAA NEXRAD Level III data.

With no arguments this downloads the repository's compact ten-site,
one-hour example. Supplying ``--date`` downloads the complete UTC day for
every current CONUS NEXRAD site unless ``--sites`` or ``--hours`` narrow it.
All files for a day live directly in ``data/YYYY-MM-DD/`` so both the
individual-site and national mosaic workspaces consume the same native files.
"""

from __future__ import annotations

import argparse
import re
import ssl
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import certifi
from sigvue.helpers import RemoteFile, download_file

NEXRAD_BASE_URL = "https://unidata-nexrad-level3.s3.amazonaws.com"
STATION_CATALOG_URL = "https://www.ncei.noaa.gov/access/homr/file/nexrad-stations.txt"
USER_AGENT = "NEXRAD-Viewer-Examples/0.2"
TLS_CONTEXT = ssl.create_default_context(cafile=certifi.where())
EXAMPLE_DATE = date(2024, 5, 20)
EXAMPLE_HOURS = (3,)
EXAMPLE_SITES = (
    "KTLX",
    "KFDR",
    "KVNX",
    "KICT",
    "KDDC",
    "KINX",
    "KSGF",
    "KEAX",
    "KOAX",
    "KTWX",
)
_MD5 = re.compile(r"[0-9a-fA-F]{32}")
_ARCHIVE_NAME = re.compile(
    r"^(?P<site>[A-Z0-9]{3})_N0B_"
    r"(?P<year>\d{4})_(?P<month>\d{2})_(?P<day>\d{2})_"
    r"(?P<hour>\d{2})_(?P<minute>\d{2})_(?P<second>\d{2})$"
)


@dataclass(frozen=True)
class NexradStation:
    """One current station entry from NOAA/NCEI's HOMR station report."""

    identifier: str
    name: str
    state: str
    country: str
    latitude_deg: float
    longitude_deg: float

    @property
    def archive_id(self) -> str:
        return self.identifier[-3:]

    @property
    def is_conus(self) -> bool:
        return self.country == "UNITED STATES" and self.identifier.startswith("K")


def _request_bytes(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, context=TLS_CONTEXT, timeout=60.0) as response:
        return response.read()


def parse_station_catalog(payload: bytes) -> tuple[NexradStation, ...]:
    """Parse NOAA's fixed-width current NEXRAD station report."""
    lines = payload.decode("utf-8", errors="strict").splitlines()
    header_index = next(
        (
            index
            for index, line in enumerate(lines)
            if line.startswith("NCDCID") and "STNTYPE" in line
        ),
        None,
    )
    if header_index is None:
        raise ValueError("NOAA station report is missing its fixed-width header")
    header = lines[header_index]
    names = (
        "NCDCID",
        "ICAO",
        "WBAN",
        "NAME",
        "COUNTRY",
        "ST",
        "COUNTY",
        "LAT",
        "LON",
        "ELEV",
        "UTC",
        "STNTYPE",
    )
    starts = {name: header.index(name) for name in names}
    ordered = sorted(starts.items(), key=lambda item: item[1])
    stops = {
        name: (ordered[index + 1][1] if index + 1 < len(ordered) else None)
        for index, (name, _) in enumerate(ordered)
    }

    def field(line: str, name: str) -> str:
        return line[starts[name] : stops[name]].strip()

    stations: dict[str, NexradStation] = {}
    for line in lines[header_index + 1 :]:
        if len(line) <= starts["STNTYPE"] or field(line, "STNTYPE") != "NEXRAD":
            continue
        identifier = field(line, "ICAO").upper()
        if len(identifier) != 4:
            continue
        try:
            latitude = float(field(line, "LAT"))
            longitude = float(field(line, "LON"))
        except ValueError:
            continue
        stations[identifier] = NexradStation(
            identifier=identifier,
            name=field(line, "NAME"),
            state=field(line, "ST"),
            country=field(line, "COUNTRY"),
            latitude_deg=latitude,
            longitude_deg=longitude,
        )
    if not stations:
        raise ValueError("NOAA station report did not contain NEXRAD stations")
    return tuple(stations[key] for key in sorted(stations))


def current_stations(*, conus_only: bool = True) -> tuple[NexradStation, ...]:
    """Fetch current station IDs and locations directly from NOAA/NCEI."""
    stations = parse_station_catalog(_request_bytes(STATION_CATALOG_URL))
    return tuple(station for station in stations if not conus_only or station.is_conus)


def _listing_url(prefix: str, continuation_token: str | None = None) -> str:
    query = {"list-type": "2", "prefix": prefix}
    if continuation_token is not None:
        query["continuation-token"] = continuation_token
    return f"{NEXRAD_BASE_URL}/?{urlencode(query)}"


def _parse_listing_page(
    payload: bytes,
    *,
    prefix: str,
) -> tuple[tuple[RemoteFile, ...], str | None]:
    root = ElementTree.fromstring(payload)
    remotes: list[RemoteFile] = []
    for content in root.findall("{*}Contents"):
        key = content.findtext("{*}Key")
        size_text = content.findtext("{*}Size")
        etag = (content.findtext("{*}ETag") or "").strip('"')
        if (
            key is None
            or size_text is None
            or not key.startswith(prefix)
            or Path(key).name != key
        ):
            raise ValueError("Invalid object returned by the NEXRAD archive")
        checksum = f"md5:{etag.lower()}" if _MD5.fullmatch(etag) else None
        remotes.append(
            RemoteFile(
                url=f"{NEXRAD_BASE_URL}/{quote(key)}",
                filename=key,
                size=int(size_text),
                checksum=checksum,
            )
        )
    truncated = root.findtext("{*}IsTruncated") == "true"
    token = root.findtext("{*}NextContinuationToken") if truncated else None
    if truncated and not token:
        raise ValueError("Truncated NEXRAD listing omitted its continuation token")
    return tuple(remotes), token


def _normalize_site(site: str) -> str:
    normalized = site.strip().upper()
    if len(normalized) == 3:
        normalized = f"K{normalized}"
    if len(normalized) != 4 or not normalized.isalnum():
        raise ValueError(f"Invalid four-character radar site: {site}")
    return normalized


def _daily_prefix(site: str, requested_date: date) -> str:
    return f"{_normalize_site(site)[-3:]}_N0B_{requested_date:%Y_%m_%d}_"


def discover_site_day(
    site: str,
    requested_date: date,
) -> tuple[RemoteFile, ...]:
    """Discover all N0B scans for one radar site and one UTC date."""
    prefix = _daily_prefix(site, requested_date)
    remotes: list[RemoteFile] = []
    continuation_token: str | None = None
    while True:
        page, continuation_token = _parse_listing_page(
            _request_bytes(_listing_url(prefix, continuation_token)),
            prefix=prefix,
        )
        remotes.extend(page)
        if continuation_token is None:
            break
    return tuple(sorted(remotes, key=lambda remote: remote.filename))


def _filter_hours(
    remotes: tuple[RemoteFile, ...],
    hours: tuple[int, ...] | None,
) -> tuple[RemoteFile, ...]:
    if hours is None:
        return remotes
    requested = set(hours)
    selected = []
    for remote in remotes:
        match = _ARCHIVE_NAME.fullmatch(remote.filename)
        if match is None:
            raise ValueError(f"Unexpected NEXRAD archive filename: {remote.filename}")
        if int(match.group("hour")) in requested:
            selected.append(remote)
    return tuple(selected)


def _scan_time(remote: RemoteFile) -> datetime:
    match = _ARCHIVE_NAME.fullmatch(remote.filename)
    if match is None:
        raise ValueError(f"Unexpected NEXRAD archive filename: {remote.filename}")
    return datetime(
        int(match.group("year")),
        int(match.group("month")),
        int(match.group("day")),
        int(match.group("hour")),
        int(match.group("minute")),
        int(match.group("second")),
        tzinfo=timezone.utc,
    )


def _sample_cadence(
    remotes: tuple[RemoteFile, ...],
    *,
    requested_date: date,
    hours: tuple[int, ...] | None,
    cadence_minutes: int | None,
) -> tuple[RemoteFile, ...]:
    if cadence_minutes is None:
        return _filter_hours(remotes, hours)
    if cadence_minutes < 1 or cadence_minutes > 24 * 60:
        raise ValueError("cadence_minutes must be between 1 and 1440")
    requested_hours = None if hours is None else set(hours)
    timed = tuple((_scan_time(remote), remote) for remote in remotes)
    if not timed:
        return ()
    selected: dict[str, RemoteFile] = {}
    day_start = datetime.combine(
        requested_date,
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    for offset_minutes in range(0, 24 * 60, cadence_minutes):
        target = day_start + timedelta(minutes=offset_minutes)
        if requested_hours is not None and target.hour not in requested_hours:
            continue
        nearest_time, nearest = min(
            timed,
            key=lambda item: (
                abs((item[0] - target).total_seconds()),
                item[0],
            ),
        )
        # A site outage should leave a missing site, not borrow from another hour.
        if abs((nearest_time - target).total_seconds()) <= cadence_minutes * 30:
            selected[nearest.filename] = nearest
    return tuple(selected[name] for name in sorted(selected))


def discover_day(
    requested_date: date,
    *,
    sites: tuple[str, ...],
    hours: tuple[int, ...] | None = None,
    cadence_minutes: int | None = None,
    workers: int = 8,
) -> tuple[RemoteFile, ...]:
    """Discover a deterministic set of scans for sites across a UTC date."""
    normalized = tuple(dict.fromkeys(_normalize_site(site) for site in sites))
    if not normalized:
        raise ValueError("At least one radar site is required")
    if workers < 1:
        raise ValueError("workers must be a positive integer")
    if hours is not None and any(hour < 0 or hour > 23 for hour in hours):
        raise ValueError("hours must be in the inclusive range 0 through 23")

    def discover(site: str) -> tuple[RemoteFile, ...]:
        return _sample_cadence(
            discover_site_day(site, requested_date),
            requested_date=requested_date,
            hours=hours,
            cadence_minutes=cadence_minutes,
        )

    if workers == 1:
        groups = map(discover, normalized)
    else:
        executor = ThreadPoolExecutor(max_workers=workers)
        groups = executor.map(discover, normalized)
    try:
        return tuple(
            sorted(
                (remote for group in groups for remote in group),
                key=lambda remote: remote.filename,
            )
        )
    finally:
        if workers != 1:
            executor.shutdown()


def _progress(filename: str):
    def report(received: int, total: int | None) -> None:
        status = (
            f"{received / total:6.1%}" if total else f"{received / 1_000_000:.1f} MB"
        )
        print(f"\r{filename}: {status}", end="", flush=True)

    return report


def _download(job: tuple[RemoteFile, Path]) -> Path:
    remote, destination = job
    target = destination / remote.filename
    if target.is_file():
        return download_file(
            remote,
            destination,
            user_agent=USER_AGENT,
            progress=_progress(remote.filename),
        )
    staging = (
        destination.parent.parent
        / f".{destination.parent.name}-{destination.name}-downloads"
    )
    staging.mkdir(parents=True, exist_ok=True)
    staged = download_file(
        remote,
        staging,
        user_agent=USER_AGENT,
        progress=_progress(remote.filename),
    )
    destination.mkdir(parents=True, exist_ok=True)
    staged.replace(target)
    return target


def download_day(
    output: str | Path,
    *,
    requested_date: date,
    sites: tuple[str, ...],
    hours: tuple[int, ...] | None = None,
    cadence_minutes: int | None = None,
    workers: int = 8,
    scans_per_site: int | None = None,
) -> tuple[Path, ...]:
    """Download native scans directly into one shared ISO-date directory."""
    if scans_per_site is not None and scans_per_site < 1:
        raise ValueError("scans_per_site must be positive")
    normalized = tuple(dict.fromkeys(_normalize_site(site) for site in sites))
    remotes = discover_day(
        requested_date,
        sites=normalized,
        hours=hours,
        cadence_minutes=cadence_minutes,
        workers=workers,
    )
    if scans_per_site is not None:
        counts: dict[str, int] = {}
        limited: list[RemoteFile] = []
        for remote in remotes:
            radar = remote.filename[:3]
            if counts.get(radar, 0) >= scans_per_site:
                continue
            counts[radar] = counts.get(radar, 0) + 1
            limited.append(remote)
        remotes = tuple(limited)
    destination = Path(output).expanduser().resolve() / requested_date.isoformat()
    destination.mkdir(parents=True, exist_ok=True)
    jobs = tuple((remote, destination) for remote in remotes)
    if workers == 1:
        return tuple(_download(job) for job in jobs)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return tuple(executor.map(_download, jobs))


def download_remotes(
    output: str | Path,
    *,
    requested_date: date,
    remotes: tuple[RemoteFile, ...],
    workers: int,
) -> tuple[Path, ...]:
    """Download an already-discovered set without repeating archive queries."""
    destination = Path(output).expanduser().resolve() / requested_date.isoformat()
    destination.mkdir(parents=True, exist_ok=True)
    jobs = tuple((remote, destination) for remote in remotes)
    if workers == 1:
        return tuple(_download(job) for job in jobs)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return tuple(executor.map(_download, jobs))


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from error


def _selected_sites(args: argparse.Namespace) -> tuple[str, ...]:
    if args.sites:
        return tuple(_normalize_site(site) for site in args.sites)
    if args.date is None:
        return EXAMPLE_SITES
    return tuple(
        station.identifier
        for station in current_stations(conus_only=not args.all_regions)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data"),
        help="data directory used by browser.toml (default: data)",
    )
    parser.add_argument(
        "--date",
        type=_parse_date,
        help=(
            "UTC day to download as YYYY-MM-DD; this selects every current "
            "CONUS radar and all 24 hours unless narrowed"
        ),
    )
    parser.add_argument(
        "--sites",
        nargs="+",
        metavar="SITE",
        help="three- or four-character radar IDs (for example TLX KTWX)",
    )
    parser.add_argument(
        "--hours",
        nargs="+",
        type=int,
        metavar="UTC_HOUR",
        help="optional UTC hours from 0 through 23",
    )
    parser.add_argument(
        "--cadence-minutes",
        type=int,
        help=(
            "keep the nearest exact native scan at this interval for each site; "
            "dated downloads default to 60"
        ),
    )
    parser.add_argument(
        "--all-scans",
        action="store_true",
        help="download every matching native scan instead of temporal sampling",
    )
    parser.add_argument(
        "--all-regions",
        action="store_true",
        help="include every current NOAA NEXRAD site, not only CONUS sites",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=12,
        help="concurrent listing/download requests (default: 12)",
    )
    parser.add_argument(
        "--scans-per-site",
        type=int,
        help="download at most this many scans from each selected site",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="query and summarize matching scans without downloading",
    )
    args = parser.parse_args()
    if args.all_scans and args.cadence_minutes is not None:
        parser.error("--all-scans and --cadence-minutes are mutually exclusive")
    requested_date = args.date or EXAMPLE_DATE
    hours = (
        tuple(args.hours)
        if args.hours is not None
        else (None if args.date is not None else EXAMPLE_HOURS)
    )
    cadence_minutes = (
        None
        if args.all_scans or args.date is None
        else args.cadence_minutes
        if args.cadence_minutes is not None
        else 60
    )
    sites = _selected_sites(args)
    remotes = discover_day(
        requested_date,
        sites=sites,
        hours=hours,
        cadence_minutes=cadence_minutes,
        workers=args.workers,
    )
    if args.scans_per_site is not None:
        by_site: dict[str, list[RemoteFile]] = {}
        for remote in remotes:
            by_site.setdefault(remote.filename[:3], []).append(remote)
        remotes = tuple(
            remote
            for site in sorted(by_site)
            for remote in by_site[site][: args.scans_per_site]
        )
    total_bytes = sum(remote.size or 0 for remote in remotes)
    print(
        f"{requested_date}: {len(sites)} requested sites, "
        f"{len(remotes):,} scans, {total_bytes / 1_000_000_000:.2f} GB"
    )
    if args.list:
        return
    paths = download_remotes(
        args.output,
        requested_date=requested_date,
        remotes=remotes,
        workers=args.workers,
    )
    print()
    print(
        f"Ready: {len(paths):,} validated scans in "
        f"{args.output.resolve() / requested_date.isoformat()}"
    )


if __name__ == "__main__":
    main()
