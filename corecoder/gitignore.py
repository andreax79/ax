"""Small .gitignore matcher for project-local tool filtering."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from corecoder.utils import find_project_root


@dataclass(frozen=True)
class IgnoreRule:
    pattern: str
    negated: bool
    directory_only: bool
    anchored: bool


def load_project_gitignore(start: Path) -> tuple[Path | None, list[IgnoreRule]]:
    """Load ignore rules from the nearest project root's .gitignore, if any."""
    root = find_project_root(start)
    if root is None:
        return None, []

    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        return root, []

    rules: list[IgnoreRule] = []
    for raw in gitignore.read_text(encoding="utf-8", errors="ignore").splitlines():
        rule = _parse_rule(raw)
        if rule is not None:
            rules.append(rule)
    return root, rules


def is_ignored(path: Path, root: Path | None, rules: list[IgnoreRule]) -> bool:
    """Return True if path is ignored by rules from root/.gitignore."""
    if root is None or not rules:
        return False

    try:
        rel = path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return False
    if rel == ".":
        return False

    is_dir = path.is_dir()
    ignored = False
    for rule in rules:
        if _matches(rule, rel, is_dir):
            ignored = not rule.negated
    return ignored


def _parse_rule(raw: str) -> IgnoreRule | None:
    line = raw.rstrip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("\\#"):
        line = line[1:]

    negated = line.startswith("!")
    if negated:
        line = line[1:]
    if not line:
        return None

    # A leading slash anchors the rule to the project root. Other slashes are
    # relative to the .gitignore directory too (the project root, here).
    anchored = line.startswith("/") or "/" in line.rstrip("/")
    line = line.lstrip("/")

    directory_only = line.endswith("/")
    line = line.rstrip("/")
    if not line:
        return None

    return IgnoreRule(line, negated, directory_only, anchored)


def _matches(rule: IgnoreRule, rel: str, is_dir: bool) -> bool:
    candidates = [rel] if is_dir else [rel, *_ancestors(rel)]
    if rule.directory_only:
        return any(_match_one(rule, candidate) for candidate in candidates if candidate != rel or is_dir or "/" in rel)
    return any(_match_one(rule, candidate) for candidate in candidates)


def _ancestors(rel: str) -> list[str]:
    parts = rel.split("/")[:-1]
    return ["/".join(parts[:idx]) for idx in range(1, len(parts) + 1)]


def _match_one(rule: IgnoreRule, rel: str) -> bool:
    pattern = rule.pattern
    if rule.anchored:
        return fnmatchcase(rel, pattern)

    # No slash: match file or directory names at any depth.
    name = rel.rsplit("/", 1)[-1]
    return fnmatchcase(name, pattern) or fnmatchcase(rel, pattern)
