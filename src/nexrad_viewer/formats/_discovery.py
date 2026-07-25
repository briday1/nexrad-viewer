"""Small filesystem discovery primitive shared by headless format readers."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


def discover_files(
    root: str | Path,
    patterns: str | Iterable[str],
    *,
    recursive: bool = True,
) -> tuple[Path, ...]:
    """Return matching regular files as unique, resolved, sorted paths."""
    directory = Path(root).expanduser().resolve()
    requested = (patterns,) if isinstance(patterns, str) else tuple(patterns)
    if not requested:
        raise ValueError("At least one discovery pattern is required")
    match = directory.rglob if recursive else directory.glob

    def visible(path: Path) -> bool:
        try:
            relative = path.relative_to(directory)
        except ValueError:
            return False
        return all(
            not part.startswith(".") and not part.endswith(".part")
            for part in relative.parts
        )

    return tuple(
        sorted(
            {
                path.resolve()
                for pattern in requested
                for path in match(pattern)
                if path.is_file() and visible(path)
            },
            key=lambda path: path.as_posix(),
        )
    )
