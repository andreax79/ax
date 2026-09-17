"""File and directory move/rename."""

import typing as t
from pathlib import Path
from shutil import move

from .base import Tool, ToolResult


class MoveFileTool(Tool):
    name = "move_file"
    description = "Move or rename a file or directory. Creates destination parent directories as needed."
    parameters: t.ClassVar[dict[str, t.Any]] = {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "description": "Existing file or directory to move",
            },
            "destination": {
                "type": "string",
                "description": "New path for the file or directory",
            },
            "overwrite": {
                "type": "boolean",
                "description": "Overwrite an existing destination file (default false)",
            },
        },
        "required": ["source", "destination"],
    }

    def execute(self, source: str, destination: str, overwrite: bool = False) -> str | ToolResult:  # type: ignore
        try:
            src = Path(source).expanduser().resolve()
            dst = Path(destination).expanduser().resolve()

            if not src.exists():
                return f"Error: {source} not found"
            if src == dst:
                return "Error: source and destination are the same path"
            if dst.exists() and not overwrite:
                return f"Error: {destination} already exists (set overwrite=true to replace a file)"
            if dst.exists() and dst.is_dir():
                return f"Error: {destination} is a directory"
            if src.is_dir() and dst.exists():
                return "Error: cannot overwrite an existing path with a directory"

            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst.unlink()
            move(str(src), str(dst))
            kind = "directory" if dst.is_dir() else "file"
            return ToolResult(f"Moved {kind} {source} to {destination}", changed_files=[src, dst])
        except Exception as e:  # noqa: BLE001
            return f"Error: {e}"
