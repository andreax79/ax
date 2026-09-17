"""Fast code search powered by The Silver Searcher (ag)."""

import shutil
import subprocess
import typing as t
from pathlib import Path

from .base import Tool

DEFAULT_MAX_COUNT = 200


class AgTool(Tool):
    name = "ag"
    read_only = True
    description = "Search code using The Silver Searcher (ag). Returns matching lines with file path and line number."
    parameters: t.ClassVar[dict[str, t.Any]] = {
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
            "file_type": {
                "type": "string",
                "description": "Restrict search to an ag file type, e.g. 'python' for ag --python. See: ag --list-file-types",
            },
            "literal": {
                "type": "boolean",
                "description": "Treat pattern as a literal string instead of a regex (default false)",
            },
        },
        "required": ["pattern"],
    }

    def execute(  # type: ignore
        self,
        pattern: str,
        path: str = ".",
        include: str | None = None,
        file_type: str | None = None,
        literal: bool = False,
        max_count: int = DEFAULT_MAX_COUNT,
    ) -> str:
        if shutil.which("ag") is None:
            return "Error: ag (The Silver Searcher) is not installed or not on PATH"

        base = Path(path).expanduser().resolve()
        if not base.exists():
            return f"Error: {path} not found"

        cmd = ["ag", "--nocolor", "--nogroup", "--numbers", "--max-count", str(max_count)]
        if literal:
            cmd.append("--literal")
        if file_type:
            normalized = file_type.strip().lstrip("-")
            if not normalized.replace("-", "").replace("_", "").isalnum():
                return "Error: file_type must be an ag file type name like 'python' or 'js'"
            cmd.append(f"--{normalized}")
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
            if len(lines) > max_count:
                lines = lines[:max_count] + [f"... ({max_count} match limit reached)"]
            return "\n".join(lines)
        if proc.returncode == 1:
            return "No matches found."
        if "Bad regex" in err:
            return f"Invalid regex: {err}"
        if "Unknown file type" in err:
            return f"Unknown file type: {err}"
        return f"Error running ag: {err or f'exit code {proc.returncode}'}"
