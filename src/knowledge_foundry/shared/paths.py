"""Locate resources in the source checkout independently of the working directory."""

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PACKAGE_ROOT.parents[1]


def run_path(run_directory: Path, relative_path: str | Path) -> Path:
    """Join a run-owned path without resolving symlinks or requiring it to exist."""
    relative_path = Path(relative_path)
    if relative_path.is_absolute() or '..' in relative_path.parts:
        raise ValueError('expected a path relative to the run directory')
    return run_directory / relative_path
