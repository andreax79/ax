"""Project-local instruction file loading."""

from __future__ import annotations

import os
from pathlib import Path

GUIDANCE_FILES = ("AGENT.md", "AGENTS.md", "CLAUDE.md", ".cursorrules")
MAX_GUIDANCE_CHARS = 20_000


def load_project_guidance(project_root: str | os.PathLike | None) -> tuple[Path | None, str]:
    """Load the highest-priority project guidance file, if one exists.

    These files are repo-controlled hints for the agent.  They are injected into
    the system prompt as lower-priority project guidance, never as rules that can
    override CoreCoder's own safety and tool-use instructions.
    """
    if project_root is None:
        return None, ""

    root = Path(project_root).expanduser().resolve()
    for name in GUIDANCE_FILES:
        path = root / name
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        content = content.strip()
        if not content:
            return path, ""
        if len(content) > MAX_GUIDANCE_CHARS:
            content = content[:MAX_GUIDANCE_CHARS].rstrip() + "\n\n[Project guidance truncated]"
        return path, content

    return None, ""
