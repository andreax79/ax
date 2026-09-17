"""File and directory deletion."""

import typing as t
from pathlib import Path
from shutil import rmtree

from ..checkpoints import record as _record_checkpoint
from .base import Tool, ToolResult


class DeleteFileTool(Tool):
    name = "delete_file"
    description = "Delete a file or an empty directory. Use recursive=true to delete a directory and all of its contents."
    parameters: t.ClassVar[dict[str, t.Any]] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File or directory to delete",
            },
            "recursive": {
                "type": "boolean",
                "description": "Delete directories recursively (default false)",
            },
        },
        "required": ["path"],
    }

    def execute(self, path: str, recursive: bool = False) -> str | ToolResult:  # type: ignore
        try:
            target = Path(path).expanduser().resolve()
            if not target.exists():
                return f"Error: {path} not found"
            if target.is_dir() and not recursive:
                return f"Error: {path} is a directory (set recursive=true to delete it)"

            _record_checkpoint(target)
            if target.is_dir():
                rmtree(target)
                kind = "directory"
            else:
                target.unlink()
                kind = "file"
            return ToolResult(f"Deleted {kind} {path}", changed_files=[str(target)])
        except Exception as e:  # noqa: BLE001
            return f"Error: {e}"
