"""Base class for all tools."""

import typing as t
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolResult:
    """Result of a tool execution."""

    output: str
    changed_files: list[str] = field(default_factory=list)
    todo_tasks: list[dict[str, t.Any]] | None = None


class Tool(ABC):
    """Minimal tool interface. Subclass this to add new capabilities."""

    name: str
    description: str
    parameters: t.ClassVar[dict[str, t.Any]] # JSON Schema for the function args
    read_only: bool = False  # If True, the tool does not modify files

    @abstractmethod
    def execute(self, **kwargs: dict[str, t.Any]) -> str | ToolResult:
        """Run the tool and return a text result."""
        ...

    def schema(self) -> dict[str, t.Any]:
        """OpenAI function-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def __repr__(self) -> str:
        return f"<Tool {self.name}>"
