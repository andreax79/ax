"""The agent's own task list (Claude Code's TodoWrite pattern).

For multi-step work the agent writes the whole checklist at once, then
rewrites it as tasks move from pending to in_progress to done.  The list
lives in process memory for the session, and Agent re-injects it into the
system context on every round (see agent._full_messages), so the model
always works from the current state instead of digging it out of old tool
results.

Sub-agents share the parent's list: they get the parent's tool instances
minus `agent`.  Claude Code gives each sub-agent a list of its own; one
shared list is what lets the whole pattern fit in a file this small.
"""

import typing as t

from ..utils import render_tasks
from .base import Tool, ToolResult

_VALID_STATUS = ("pending", "in_progress", "done")


class TodoWriteTool(Tool):
    name = "todo_write"
    read_only = True
    description = (
        "Manage the session task checklist. Pass the complete list every time; it fully "
        "replaces the old one. Use it to plan multi-step work: write the list up front, "
        "keep one task in_progress while you work on it, and mark it done the moment it "
        "finishes. The current list is re-shown in the system context every round."
    )
    parameters: t.ClassVar[dict[str, t.Any]] = {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "description": "The full checklist, in order. Replaces the current list; pass [] to clear it.",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "What needs to be done, one line",
                        },
                        "status": {
                            "type": "string",
                            "enum": list(_VALID_STATUS),
                            "description": "pending, in_progress, or done",
                        },
                    },
                    "required": ["content", "status"],
                },
            },
        },
        "required": ["tasks"],
    }

    def execute(self, tasks: list) -> str | ToolResult:  # type: ignore
        if not isinstance(tasks, list):
            return "Error: tasks must be a list of {content, status} objects"
        checked = []
        for i, task in enumerate(tasks, 1):
            content = task.get("content") if isinstance(task, dict) else None
            if not isinstance(content, str) or not content.strip():
                return f"Error: task {i} needs a non-empty 'content' string"
            status = task.get("status")
            if status not in _VALID_STATUS:
                return f"Error: task {i} has invalid status {status!r} (use pending, in_progress, or done)"
            checked.append({"content": content.strip(), "status": status})
        # validate everything before replacing, so a bad call leaves the old list intact
        if not checked:
            return ToolResult("Task list cleared.", todo_tasks=checked)
        else:
            return ToolResult("Task list updated:\n" + render_tasks(checked), todo_tasks=checked)
