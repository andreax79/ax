import os
from types import SimpleNamespace

from corecoder import cli
from corecoder.agent import Agent
from corecoder.llm import LLM
from corecoder.tools.bash import BashTool


def test_bang_command_runs_direct_shell_command(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    cli._run_shell_input("!ls -l")

    assert calls == [(('ls -l',), {"shell": True, "check": False})]


def test_bang_alone_starts_interactive_shell(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("SHELL", "/bin/test-shell")
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    cli._run_shell_input("!")

    assert calls == [(('/bin/test-shell',), {"shell": False, "check": False})]


def test_bang_cd_changes_corecoder_cwd_and_bash_tool_cwd(tmp_path, monkeypatch):
    start = tmp_path / "start"
    dest = tmp_path / "dest with spaces"
    start.mkdir()
    dest.mkdir()
    monkeypatch.chdir(start)

    cli._run_shell_input(f"!cd {dest}")

    assert os.getcwd() == str(dest)
    assert BashTool().execute("pwd") == str(dest)


def test_bang_cd_refreshes_agent_project_guidance(tmp_path, monkeypatch):
    start = tmp_path / "start"
    dest = tmp_path / "dest"
    start.mkdir()
    dest.mkdir()
    (dest / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    guidance_path = dest / "AGENT.md"
    guidance_path.write_text("Use the new guidance.\n", encoding="utf-8")
    monkeypatch.chdir(start)
    agent = Agent(llm=LLM.__new__(LLM), tools=[])

    cli._run_shell_input(f"!cd {dest}", agent=agent)

    assert agent.project_root == dest
    assert agent.guidance_path == guidance_path
    assert "Use the new guidance." in agent._full_messages()[0]["content"]
