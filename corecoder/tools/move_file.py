"""File and directory move/rename."""

from pathlib import Path
from shutil import move
from typing import ClassVar

from ..checkpoints import record_many as _record_checkpoint
from .base import Tool
from .edit_file import _changed_files


class MoveFileTool(Tool):
    name = "move_file"
    description = "Move or rename a file or directory. Creates destination parent directories as needed."
    parameters: ClassVar[dict] = {
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

    def execute(self, source: str, destination: str, overwrite: bool = False) -> str:
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
            _record_checkpoint([src, dst])
            if dst.exists():
                dst.unlink()
            move(str(src), str(dst))
            _changed_files.add(str(src))
            _changed_files.add(str(dst))
            kind = "directory" if dst.is_dir() else "file"
            return f"Moved {kind} {source} to {destination}"
        except Exception as e:  # noqa: BLE001
            return f"Error: {e}"
