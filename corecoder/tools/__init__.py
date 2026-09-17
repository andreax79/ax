import importlib
import pkgutil
import sys

from .base import Tool


def get_tools() -> list[Tool]:
    """
    Discover all tools in the package and return a list of Tool instances.
    """
    tools = []
    package = sys.modules[__name__]
    for _, name, is_pkg in pkgutil.iter_modules(package.__path__, package.__name__ + "."):
        if not is_pkg:
            module = importlib.import_module(name)
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, Tool) and attr is not Tool:
                    tools.append(attr())
    return tools


def get_tool(name: str) -> Tool | None:
    """Look up a tool by name."""
    for t in get_tools():
        if t.name == name:
            return t
    return None
