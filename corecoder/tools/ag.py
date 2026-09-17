"""Fast code search powered by The Silver Searcher (ag)."""

import shutil
import subprocess
from pathlib import Path
from typing import ClassVar

from .base import Tool


class AgTool(Tool):
    name = "ag"
    description = (
        "Search code using The Silver Searcher (ag). "
        "Returns matching lines with file path and line number."
    )
    parameters: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search (default: cwd)",
            },
            "include": {
                "type": "string",
                "description": "Only search files whose path matches this regex (ag -G)",
            },
            "literal": {
                "type": "boolean",
                "description": "Treat pattern as a literal string instead of a regex (default false)",
            },
        },
        "required": ["pattern"],
    }

    def execute(
        self,
        pattern: str,
        path: str = ".",
        include: str | None = None,
        literal: bool = False,
    ) -> str:
        if shutil.which("ag") is None:
            return "Error: ag (The Silver Searcher) is not installed or not on PATH"

        base = Path(path).expanduser().resolve()
        if not base.exists():
            return f"Error: {path} not found"

        cmd = [
            "ag",
            "--nocolor",
            "--nogroup",
            "--numbers",
            "--max-count",
            "200",
        ]
        if literal:
            cmd.append("--literal")
        if include:
            cmd.extend(["-G", include])
        cmd.extend([pattern, str(base)])

        try:
            proc = subprocess.run(
                cmd,
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return "Error: ag timed out after 30s"
        except Exception as e:  # noqa: BLE001
            return f"Error running ag: {e}"

        out = proc.stdout.strip()
        err = proc.stderr.strip()
        if proc.returncode == 0:
            lines = out.splitlines()
            if len(lines) > 200:
                lines = lines[:200] + ["... (200 match limit reached)"]
            return "\n".join(lines)
        if proc.returncode == 1:
            return "No matches found."
        if "Bad regex" in err:
            return f"Invalid regex: {err}"
        return f"Error running ag: {err or f'exit code {proc.returncode}'}"
