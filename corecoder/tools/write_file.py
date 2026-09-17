"""File creation / overwrite."""

import typing as t
from pathlib import Path

from .base import Tool, ToolResult


class WriteFileTool(Tool):
    name = "write_file"
    description = (
        "Create a new file or completely overwrite an existing one. For small edits to existing files, prefer edit_file instead."
    )
    parameters: t.ClassVar[dict[str, t.Any]] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Path for the file",
            },
            "content": {
                "type": "string",
                "description": "Full file content to write",
            },
        },
        "required": ["file_path", "content"],
    }

    def execute(self, file_path: str, content: str) -> str | ToolResult:  # type: ignore
        try:
            p = Path(file_path).expanduser().resolve()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            n_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            return ToolResult(f"Wrote {n_lines} lines to {file_path}", changed_files=[str(p)])
        except Exception as e:  # noqa: BLE001
            # boundary: the agent gets an error string, not a traceback
            return f"Error: {e}"
