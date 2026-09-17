import os
from pathlib import Path

_PROJECT_ROOT_MARKERS = (".git", ".gitignore", "pyproject.toml")


def find_project_root(start: str | os.PathLike | None = None) -> Path | None:
    """Find the nearest ancestor that looks like a project root."""
    cur = Path(start or os.getcwd()).expanduser().resolve()
    if cur.is_file():
        cur = cur.parent

    while True:
        if any((cur / marker).exists() for marker in _PROJECT_ROOT_MARKERS):
            return cur
        if cur == cur.parent:
            return None
        cur = cur.parent
