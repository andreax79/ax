"""Session-scoped undo for file mutations.

File-mutating tools record a checkpoint before touching a path; /undo pops the
latest one and restores the previous bytes/tree (or removes the path if it did
not exist). In-memory only: undo history dies with the process, and bash side
effects are not tracked.
"""

from pathlib import Path
from shutil import rmtree

Snapshot = bytes | dict[str, bytes | None] | None
Checkpoint = list[tuple[str, Snapshot]]

# Each stack item is one user-visible mutation, which may touch multiple paths.
_stack: list[Checkpoint] = []


def record(path: Path) -> None:
    """Capture the pre-mutation state of path. Call right before writing."""
    record_many([path])


def record_many(paths: list[Path]) -> None:
    """Capture one mutation that may touch several paths, e.g. a move."""
    _stack.append([(str(path), _snapshot(path)) for path in paths])


def undo() -> str:
    """Restore the most recent checkpoint."""
    if not _stack:
        return "Nothing to undo."
    checkpoint = _stack.pop()
    restored: list[str] = []
    removed: list[str] = []
    for path_str, prior in reversed(checkpoint):
        action = _restore(path_str, prior)
        if action == "removed":
            removed.append(path_str)
        else:
            restored.append(path_str)
    if len(checkpoint) == 1:
        path_str = checkpoint[0][0]
        if removed:
            return f"Removed {path_str} (created this session)."
        return f"Restored {path_str}."
    parts = []
    if restored:
        parts.append("restored " + ", ".join(reversed(restored)))
    if removed:
        parts.append("removed " + ", ".join(reversed(removed)))
    return "Undo complete: " + "; ".join(parts) + "."


def _restore(path_str: str, prior: Snapshot) -> str:
    p = Path(path_str)
    if prior is None:
        if p.is_dir():
            rmtree(p)
        else:
            p.unlink(missing_ok=True)
        return "removed"
    if isinstance(prior, bytes):
        if p.is_dir():
            rmtree(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(prior)
        return "restored"

    if p.exists():
        if p.is_dir():
            rmtree(p)
        else:
            p.unlink()
    p.mkdir(parents=True, exist_ok=True)
    for rel, data in sorted(prior.items(), key=lambda item: item[0].count("/")):
        if rel == ".":
            continue
        child = p / rel
        if data is None:
            child.mkdir(parents=True, exist_ok=True)
        else:
            child.parent.mkdir(parents=True, exist_ok=True)
            child.write_bytes(data)
    return "restored"


def _snapshot(path: Path) -> Snapshot:
    if not path.exists():
        return None
    if not path.is_dir():
        return path.read_bytes()

    snap: dict[str, bytes | None] = {".": None}
    for child in path.rglob("*"):
        rel = child.relative_to(path).as_posix()
        snap[rel] = None if child.is_dir() else child.read_bytes()
    return snap


def pending() -> int:
    return len(_stack)


def clear() -> None:
    _stack.clear()
