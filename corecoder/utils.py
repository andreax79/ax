import os
import typing as t
from pathlib import Path

_PROJECT_ROOT_MARKERS = (".git", ".gitignore", "pyproject.toml")


def find_project_root(start: str | os.PathLike[str] | None = None) -> Path | None:
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

def render_tasks(tasks: list[dict[str, t.Any]] | None) -> str:
    """The checklist as text; Agent injects this into the system context."""
    if tasks is None:
        return ""
    return "\n".join(f"{i}. [{t['status']}] {t['content']}" for i, t in enumerate(tasks, 1))
