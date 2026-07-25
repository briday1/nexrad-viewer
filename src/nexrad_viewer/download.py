"""Discover and download fixed NOAA NEXRAD Level III example cases."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import ssl
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

import certifi

from sigvue.helpers import RemoteFile, download_file


NEXRAD_BASE_URL = "https://unidata-nexrad-level3.s3.amazonaws.com"
USER_AGENT = "NEXRAD-Viewer/0.1"
TLS_CONTEXT = ssl.create_default_context(cafile=certifi.where())
CASE_PREFIXES = {
    "tlx-oklahoma-city": "TLX_N0B_2024_05_20_03_",
    "fdr-frederick": "FDR_N0B_2024_05_20_03_",
    "vnx-vance": "VNX_N0B_2024_05_20_03_",
    "ict-wichita": "ICT_N0B_2024_05_20_03_",
    "ddc-dodge-city": "DDC_N0B_2024_05_20_03_",
    "inx-tulsa": "INX_N0B_2024_05_20_03_",
    "sgf-springfield": "SGF_N0B_2024_05_20_03_",
    "eax-kansas-city": "EAX_N0B_2024_05_20_03_",
    "oax-omaha": "OAX_N0B_2024_05_20_03_",
    "twx-topeka": "TWX_N0B_2024_05_20_03_",
}
DEFAULT_CASES = tuple(CASE_PREFIXES)
_MD5 = re.compile(r"[0-9a-fA-F]{32}")


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


def discover_case(case: str) -> tuple[RemoteFile, ...]:
    """Discover every archived scan belonging to one fixed example case."""
    try:
        prefix = CASE_PREFIXES[case]
    except KeyError as error:
        raise ValueError(f"Unknown NEXRAD case: {case}") from error

    remotes: list[RemoteFile] = []
    continuation_token: str | None = None
    while True:
        request = Request(
            _listing_url(prefix, continuation_token),
            headers={"User-Agent": USER_AGENT},
        )
        with urlopen(
            request,
            context=TLS_CONTEXT,
            timeout=30.0,
        ) as response:
            page, continuation_token = _parse_listing_page(
                response.read(),
                prefix=prefix,
            )
        remotes.extend(page)
        if continuation_token is None:
            break
    if not remotes:
        raise RuntimeError(f"No archived scans found for case: {case}")
    return tuple(sorted(remotes, key=lambda remote: remote.filename))


def _progress(filename: str):
    def report(received: int, total: int | None) -> None:
        status = (
            f"{received / total:6.1%}"
            if total
            else f"{received / 1_000_000:.1f} MB"
        )
        print(f"\r{filename}: {status}", end="", flush=True)

    return report


def _download(job: tuple[RemoteFile, Path]) -> Path:
    remote, output = job
    return download_file(
        remote,
        output,
        user_agent=USER_AGENT,
        progress=_progress(remote.filename),
    )


def download_scans(
    output: str | Path,
    *,
    cases: tuple[str, ...] = DEFAULT_CASES,
    workers: int = 8,
    scans_per_case: int | None = None,
) -> tuple[Path, ...]:
    """Download selected cases with archive size and ETag verification."""
    unknown = tuple(case for case in cases if case not in CASE_PREFIXES)
    if unknown:
        raise ValueError(f"Unknown NEXRAD case: {', '.join(unknown)}")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    if scans_per_case is not None and (
        isinstance(scans_per_case, bool)
        or not isinstance(scans_per_case, int)
        or scans_per_case < 1
    ):
        raise ValueError("scans_per_case must be a positive integer")

    destination = Path(output).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    jobs: list[tuple[RemoteFile, Path]] = []
    for case in cases:
        remotes = discover_case(case)
        if scans_per_case is not None:
            remotes = remotes[:scans_per_case]
        case_directory = destination / case
        jobs.extend((remote, case_directory) for remote in remotes)

    if workers == 1:
        return tuple(_download(job) for job in jobs)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return tuple(executor.map(_download, jobs))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data"),
        help="data directory used by browser.toml (default: data)",
    )
    parser.add_argument(
        "--cases",
        nargs="+",
        choices=DEFAULT_CASES,
        default=DEFAULT_CASES,
        help="example cases to download (default: all ten)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="concurrent downloads (default: 8)",
    )
    parser.add_argument(
        "--scans-per-case",
        type=int,
        help="download at most this many scans from each selected case",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="query and list the available scans without downloading",
    )
    args = parser.parse_args()
    if args.list:
        total_scans = 0
        total_bytes = 0
        for case in args.cases:
            remotes = discover_case(case)
            size = sum(remote.size or 0 for remote in remotes)
            total_scans += len(remotes)
            total_bytes += size
            print(f"{case}: {len(remotes)} scans, {size / 1_000_000:.1f} MB")
        print(f"Total: {total_scans} scans, {total_bytes / 1_000_000:.1f} MB")
        return
    paths = download_scans(
        args.output,
        cases=tuple(args.cases),
        workers=args.workers,
        scans_per_case=args.scans_per_case,
    )
    print()
    print(f"Ready: {len(paths)} validated scans in {args.output.resolve()}")


if __name__ == "__main__":
    main()
