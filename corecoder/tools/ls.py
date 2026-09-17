"""Directory listing."""

from pathlib import Path
from typing import ClassVar

from ..gitignore import is_ignored, load_project_gitignore
from .base import Tool


class LsTool(Tool):
    name = "ls"
    read_only = True
    description = (
        "List files and directories in a path. Shows immediate children, directories first, and honors project .gitignore rules."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory to list (default: cwd)",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum entries to show (default 200)",
            },
        },
        "required": [],
    }

    def execute(self, path: str = ".", limit: int = 200) -> str:
        try:
            base = Path(path).expanduser().resolve()
            if not base.exists():
                return f"Error: {path} not found"
            if not base.is_dir():
                return f"Error: {path} is not a directory"

            ignore_root, ignore_rules = load_project_gitignore(base)
            entries = [child for child in base.iterdir() if not is_ignored(child, ignore_root, ignore_rules)]
            entries.sort(key=lambda p: (not p.is_dir(), p.name.lower()))

            shown = entries[:limit]
            lines = [str(base)]
            for entry in shown:
                suffix = "/" if entry.is_dir() else ""
                lines.append(f"{entry.name}{suffix}")

            if len(entries) > limit:
                lines.append(f"... ({len(entries)} entries, showing first {limit})")
            return "\n".join(lines) if shown else f"{base}\n(empty directory)"
        except Exception as e:  # noqa: BLE001
            return f"Error: {e}"
