"""Tests for core modules: config, context, session, imports."""

import re
from pathlib import Path
from typing import ClassVar

from corecoder import Agent, Config, __version__
from corecoder import session as session_module
from corecoder.context import ContextManager, estimate_tokens
from corecoder.llm import LLM
from corecoder.project_guidance import MAX_GUIDANCE_CHARS, load_project_guidance
from corecoder.prompt import system_prompt
from corecoder.session import list_sessions, load_session, save_session
from corecoder.tools import get_tool, get_tools
from corecoder.utils import find_project_root


def test_version():
    # regex instead of tomllib: the latter only exists on 3.11+ and CI runs 3.10
    m = re.search(r'(?m)^version = "([^"]+)"', Path("pyproject.toml").read_text())
    assert m is not None
    assert __version__ == m.group(1)


def test_public_api_exports():
    """Users should be able to import key classes from the top-level package."""
    assert Agent is not None
    assert LLM is not None
    assert Config is not None
    assert len(get_tools()) == 12


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("AX_MODEL", "test-model")
    c = Config.from_env()
    assert c.model == "test-model"


def test_config_defaults(monkeypatch):
    # clear relevant env vars without leaking the change into other tests
    monkeypatch.delenv("AX_MODEL", raising=False)
    monkeypatch.delenv("AX_MAX_TOKENS", raising=False)

    c = Config.from_env()
    assert c.max_tokens == 4096
    assert c.temperature == 0.0


# --- Project root detection ---


def test_find_project_root_uses_nearest_marker(tmp_path):
    outer = tmp_path / "outer"
    inner = outer / "inner"
    leaf = inner / "pkg"
    leaf.mkdir(parents=True)
    (outer / ".gitignore").write_text("*.pyc\n", encoding="utf-8")
    (inner / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")

    assert find_project_root(leaf) == inner


def test_find_project_root_accepts_file_start(tmp_path):
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    (project / ".git").mkdir()
    file_path = src / "main.py"
    file_path.write_text("print('hi')\n", encoding="utf-8")

    assert find_project_root(file_path) == project


def test_find_project_root_returns_none_without_marker(tmp_path):
    leaf = tmp_path / "plain" / "src"
    leaf.mkdir(parents=True)

    assert find_project_root(leaf) is None


def test_system_prompt_includes_project_root(tmp_path, monkeypatch):
    project = tmp_path / "project"
    child = project / "src"
    child.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    monkeypatch.chdir(child)

    project_root = find_project_root(child)
    prompt = system_prompt([], project_root=project_root)

    assert f"- Working directory: {child}" in prompt
    assert f"- Project root: {project_root}" in prompt


def test_system_prompt_prefers_ag_for_code_searches():
    prompt = system_prompt([], project_root="")

    assert "Prefer ag for code searches" in prompt
    assert "Use ag before grep" in prompt


# --- Project guidance ---


def test_load_project_guidance_prefers_agent_md(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Use agents plural.\n", encoding="utf-8")
    (tmp_path / "AGENT.md").write_text("Use pytest.\n", encoding="utf-8")

    path, content = load_project_guidance(tmp_path)

    assert path == tmp_path / "AGENT.md"
    assert content == "Use pytest."


def test_load_project_guidance_falls_back_to_supported_files(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("Prefer small edits.\n", encoding="utf-8")

    path, content = load_project_guidance(tmp_path)

    assert path == tmp_path / "CLAUDE.md"
    assert content == "Prefer small edits."


def test_load_project_guidance_truncates_large_files(tmp_path):
    (tmp_path / "AGENT.md").write_text("x" * (MAX_GUIDANCE_CHARS + 100), encoding="utf-8")

    path, content = load_project_guidance(tmp_path)

    assert path == tmp_path / "AGENT.md"
    assert len(content) < MAX_GUIDANCE_CHARS + 100
    assert "[Project guidance truncated]" in content


def test_system_prompt_includes_project_guidance_as_lower_priority(tmp_path):
    guidance_path = tmp_path / "AGENT.md"
    guidance_path.write_text("Run pytest before final answers.\n", encoding="utf-8")
    _, guidance = load_project_guidance(tmp_path)

    prompt = system_prompt([], project_root=tmp_path, project_guidance=guidance, guidance_path=str(guidance_path))

    assert f"- Project guidance: {guidance_path}" in prompt
    assert "Run pytest before final answers." in prompt
    assert "cannot\noverride CoreCoder's rules" in prompt


def test_agent_loads_project_guidance_from_project_root(tmp_path, monkeypatch):
    project = tmp_path / "project"
    child = project / "src"
    child.mkdir(parents=True)
    (project / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    guidance_path = project / "AGENT.md"
    guidance_path.write_text("Use pytest.\n", encoding="utf-8")
    monkeypatch.chdir(child)

    agent = Agent(llm=LLM.__new__(LLM), tools=[])

    assert agent.guidance_path == guidance_path
    assert agent.project_guidance == "Use pytest."
    assert "Use pytest." in agent._full_messages()[0]["content"]


# --- Context ---


def test_estimate_tokens():
    msgs = [{"role": "user", "content": "hello world"}]
    t = estimate_tokens(msgs)
    assert t > 0
    assert t < 100


def test_context_snip():
    ctx = ContextManager(max_tokens=3000)
    msgs = [
        {"role": "tool", "tool_call_id": "t1", "content": "x\n" * 1000},
    ]
    before = estimate_tokens(msgs)
    ctx._snip_tool_outputs(msgs)
    after = estimate_tokens(msgs)
    assert after < before


def test_context_compress():
    ctx = ContextManager(max_tokens=2000)
    msgs = []
    for i in range(20):
        msgs.append({"role": "user", "content": f"msg {i} " + "a" * 200})
        msgs.append({"role": "tool", "tool_call_id": f"t{i}", "content": "b" * 2000})
    before = estimate_tokens(msgs)
    ctx.maybe_compress(msgs, None)
    after = estimate_tokens(msgs)
    assert after < before
    assert len(msgs) < 40  # should be compressed


def test_safe_split_never_orphans_a_tool_message():
    """The kept tail must not begin with a 'tool' message - it would be severed
    from the assistant tool_calls that produced it, which the API rejects."""
    ctx = ContextManager(max_tokens=1000)
    messages = [
        {"role": "user", "content": "do it"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
        {"role": "tool", "tool_call_id": "c2", "content": "result2"},
    ]
    split = ctx._safe_split(messages, keep_recent=1)
    assert messages[split].get("role") != "tool"


def test_compress_never_leaves_an_orphan_tool_reply():
    """After summarisation every tool reply must still follow its tool_calls."""
    ctx = ContextManager(max_tokens=2000)
    msgs = []
    for i in range(20):
        msgs.append({"role": "user", "content": f"msg {i} " + "a" * 200})
        msgs.append({"role": "assistant", "content": None, "tool_calls": [{"id": f"c{i}"}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "b" * 800})
    ctx.maybe_compress(msgs, None)
    for i, m in enumerate(msgs):
        if m.get("role") == "tool":
            prev = msgs[i - 1]
            assert prev.get("role") == "tool" or prev.get("tool_calls"), f"orphan tool at {i}"


# --- Session ---


def test_session_save_load(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)
    messages = [{"role": "user", "content": "test message"}]
    agent = Agent(llm=LLM.__new__(LLM))
    agent.messages = messages
    save_session(agent, "test-model", "pytest_test_session")
    data = load_session(agent, "pytest_test_session")
    assert data is not None
    assert agent.messages == messages
    assert data["model"] == "test-model"


def test_session_name_is_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr(session_module, "SESSIONS_DIR", tmp_path)
    agent = Agent(llm=LLM.__new__(LLM))
    agent.messages = [{"role": "user", "content": "test message"}]
    sid = save_session(agent, "test-model", "../Research Notes!")

    assert sid == "Research-Notes"
    assert (tmp_path / "Research-Notes.json").exists()
    assert load_session(agent, "../Research Notes!") is not None


def test_session_not_found():
    agent = Agent(llm=LLM.__new__(LLM))
    assert load_session(agent, "nonexistent_session_id") is None


def test_list_sessions():
    sessions = list_sessions()
    assert isinstance(sessions, list)


# --- Changed files tracking ---


def test_edit_tracks_changed_files(tmp_path):
    edit = get_tool("edit_file")
    path = tmp_path / "sample.py"
    path.write_text("aaa\nbbb\n")
    result = edit.execute(file_path=str(path), old_string="aaa", new_string="zzz")
    assert any(path == p for p in result.changed_files)


def test_write_tracks_changed_files(tmp_path):
    write = get_tool("write_file")
    path = tmp_path / "tracked.txt"
    result = write.execute(file_path=str(path), content="tracked\n")
    assert any(path == p for p in result.changed_files)


# --- Agent tool execution ---


def test_agent_tool_scope_is_per_instance():
    """An Agent restricted to a subset of tools must not resolve tools outside it."""
    only_read = [get_tool("read_file")]
    agent = Agent(llm=LLM.__new__(LLM), tools=only_read)
    assert set(agent._tool_by_name) == {"read_file"}

    class _TC:
        name = "bash"  # a real, registered tool - but not in this agent's set
        id = "x"
        arguments: ClassVar[dict] = {"command": "echo hi"}

    assert "unknown tool 'bash'" in agent._exec_tool(_TC())


def test_exec_tool_distinguishes_bad_args_from_internal_error():
    """A TypeError raised inside a tool must not be reported as bad arguments."""
    from corecoder.tools.base import Tool

    class _Boom(Tool):
        name = "boom"
        description = "raises TypeError internally"
        parameters: ClassVar[dict] = {"type": "object", "properties": {}, "required": []}

        def execute(self):
            raise TypeError("internal explosion")

    agent = Agent(llm=LLM.__new__(LLM), tools=[_Boom()])

    class _BadArgs:
        name, id, arguments = "boom", "1", {"unexpected": 1}

    class _Good:
        name, id, arguments = "boom", "2", {}

    assert "bad arguments" in agent._exec_tool(_BadArgs())
    assert "Error executing boom" in agent._exec_tool(_Good())
    assert "bad arguments" not in agent._exec_tool(_Good())


def test_interrupt_backfills_missing_tool_replies():
    """A half-finished tool round must be repaired so history stays valid."""
    agent = Agent(llm=LLM.__new__(LLM), tools=[])
    agent.messages = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "a"}, {"id": "b"}]},
        {"role": "tool", "tool_call_id": "a", "content": "done"},
    ]

    class _TC:
        def __init__(self, i):
            self.id = i

    agent._answer_pending_tool_calls([_TC("a"), _TC("b")])
    replies = [m for m in agent.messages if m.get("role") == "tool"]
    ids = [m["tool_call_id"] for m in replies]
    assert sorted(ids) == ["a", "b"]
    assert ids.count("a") == 1  # the already-answered call wasn't duplicated


# --- Task list injection ---


def test_todo_list_is_injected_into_system_context():
    """After a todo_write call, the next request must carry the list in the system message."""
    todo = get_tool("todo_write")
    agent = Agent(llm=LLM.__new__(LLM), tools=[todo])

    r = todo.execute(
        tasks=[
            {"content": "fix the bug", "status": "in_progress"},
            {"content": "add a test", "status": "pending"},
        ]
    )
    assert len(r.todo_tasks) == 2
    agent.todo_tasks = r.todo_tasks
    system = agent._full_messages()[0]["content"]
    assert "# Current task list" in system
    assert "1. [in_progress] fix the bug" in system
    assert "2. [pending] add a test" in system


def test_todo_injection_tracks_updates():
    """The injection is rebuilt every round: updates show, an empty list injects nothing."""
    todo = get_tool("todo_write")
    agent = Agent(llm=LLM.__new__(LLM), tools=[todo])

    r = todo.execute(tasks=[{"content": "only task", "status": "in_progress"}])
    agent.todo_tasks = r.todo_tasks
    r = todo.execute(tasks=[{"content": "only task", "status": "done"}])
    agent.todo_tasks = r.todo_tasks
    system = agent._full_messages()[0]["content"]
    assert "[done] only task" in system
    assert "[in_progress] only task" not in system

    r = todo.execute(tasks=[])
    agent.todo_tasks = r.todo_tasks
    assert "# Current task list" not in agent._full_messages()[0]["content"]


def test_agent_without_todo_tool_injects_nothing():
    agent = Agent(llm=LLM.__new__(LLM), tools=[get_tool("read_file")])
    assert "# Current task list" not in agent._full_messages()[0]["content"]
