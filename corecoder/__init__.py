"""CoreCoder - Minimal AI coding agent inspired by Claude Code's architecture."""

__version__ = "0.6.0"

from corecoder.agent import Agent
from corecoder.config import Config
from corecoder.llm import LLM

__all__ = ["Agent", "Config", "__version__"]
