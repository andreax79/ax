import os
import re
import shlex
import shutil
import typing as t
from pathlib import Path

import platformdirs

CONFIG_DIR = Path(platformdirs.user_config_dir(appname="ax"))

_PROJECT_ROOT_MARKERS = (".git", ".gitignore", "pyproject.toml")

__all__ = [
    "find_project_root",
    "format_num",
    "is_unix_command",
    "render_tasks",
]


def find_project_root(start: str | os.PathLike[str] | None = None) -> Path | None:
    """Find the nearest ancestor that looks like a project root."""
    cur = Path(start or os.getcwd()).expanduser().resolve()
    if cur.is_file():
        cur = cur.parent

    while True:
        if any((cur / marker).exists() for marker in _PROJECT_ROOT_MARKERS):
            return cur
        if cur == cur.parent:
            return None
        cur = cur.parent


def render_tasks(tasks: list[dict[str, t.Any]] | None) -> str:
    """The checklist as text; Agent injects this into the system context."""
    if tasks is None:
        return ""
    return "\n".join(
        f"{i}. [{t['status']}] {t['content']}" for i, t in enumerate(tasks, 1)
    )


# A reasonably large set of common unix commands/builtins.
# fmt: off
KNOWN_COMMANDS = {
    "set", "env", "printenv",
    "cd", "pwd", "ls", "cat", "mkdir", "rmdir", "rm", "cp", "mv", "chmod", "chown",
    "grep", "find", "sed", "awk", "echo", "touch", "ack", "ag", "locate", "which", "whereis",
    "ps", "kill", "top",
    "df", "du", "tar", "gzip", "gunzip", "curl", "wget", "ssh", "scp", "git",
    "sudo", "man",
    "head", "tail", "less", "more", "vim", "nano", "export", "source",
    "history", "whoami", "uname", "systemctl", "service", "ping",
    "ifconfig", "ip", "netstat",
    "make", "cmake", "gcc", "g++", "java", "python", "python3", "perl", "go", "rustc", "ruby", "php",
    "pip", "cargo", "npm", "yarn", "brew", "apt", "yum", "dnf", "pacman", "zypper", "snap", "flatpak",
    "bash", "zsh", "csh", "fish", "sh", "dash", "ksh", "tcsh",
    "docker", "podman", "kubectl", "helm", "minikube", "vagrant", "ansible", "terraform",
}
# fmt: on

# Words that are common in natural-language sentences but rarely (as the
# FIRST token) in shell commands.
# fmt: off
NL_STOPWORDS = {
    "what", "why", "how", "when", "where", "who", "is", "are", "the",
    "a", "an", "please", "can", "could", "would", "should", "do", "does",
    "did", "i", "you", "we", "they", "it", "this", "that", "tell", "explain",
    "describe", "which", "help", "show", "give",
    "write", "read", "edit", "fix", "refactor", "test", "run", "build",
}
# fmt: on

def is_unix_command(s: str, default: bool = False) -> bool:
    """
    Returns True if `s` looks like a unix command, False if it looks like
    natural-language text, `default` if undecided.
    """
    s = s.strip()
    if not s:
        return False

    # Check the fist character
    if s.startswith("!"):
        return True
    if s.startswith("/"):
        return True
    if s.startswith(" "):
        return False

    # Tokenize safely
    try:
        tokens = shlex.split(s)
    except ValueError:
        tokens = s.split()

    if not tokens:
        return False

    first = tokens[0]
    first_lower = first.lower()

    # Ends with '?' or '.', or has multiple words with natural sentence
    # punctuation -> strongly suggests a phrase/question.
    if s.endswith("?"):
        return False

    # First word is a known NL stopword (what/why/how/is/are/please...)
    if first_lower in NL_STOPWORDS:
        return False

    # First token is a known command name
    if first_lower in KNOWN_COMMANDS:
        return True

    # First token resolves to an executable on PATH
    if shutil.which(first) is not None:
        return True

    # Looks like a path (contains a slash, or starts with ./ ../ ~/)
    if re.match(r"^(\./|\.\./|~/|/)", first) or "/" in first:
        return True

    # Contains shell-specific syntax: pipes, redirects, flags, env vars,
    # command substitution, globbing
    shell_syntax = re.search(r"(\||>>?|<|&&|\|\||\$\(|`|~/|\*|\?|--?\w)", s)
    if shell_syntax:
        return True

    # If it has capital-first-letter sentence style + spaces + ends with punctuation, treat as phrase.
    if re.match(r"^[A-Z][a-z]", s) and len(tokens) > 2:
        return False

    return default


def format_num(size: float, precision: str = "3.1f") -> str:
    """
    Format a number into a human-readable string.
    """
    for unit in ("", "K", "M", "G", "T"):
        if abs(size) < 1000 or unit == "T":
            break
        size = size / 1000
    return f"{size:{precision}}{unit}" if unit else str(int(size))
