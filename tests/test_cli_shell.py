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

    cli.cmd_shell("!ls -l", None, None)

    assert calls == [(("ls -l",), {"shell": True, "check": False})]


def test_bang_alone_starts_interactive_shell(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setenv("SHELL", "/bin/test-shell")
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    cli.cmd_shell("!", None, None)

    assert calls == [(("/bin/test-shell",), {"shell": False, "check": False})]


def test_bang_cd_changes_corecoder_cwd_and_bash_tool_cwd(tmp_path, monkeypatch):
    start = tmp_path / "start"
    dest = tmp_path / "dest with spaces"
    start.mkdir()
    dest.mkdir()
    monkeypatch.chdir(start)

    cli.cmd_shell(f"!cd {dest}", None, None)

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

    cli.cmd_shell(f"!cd {dest}", agent=agent, config=None)

    assert agent.project_root == dest
    assert agent.guidance_path == guidance_path
    assert "Use the new guidance." in agent._full_messages()[0]["content"]


def test_bang_set_displays_environment_variables(monkeypatch, capsys):
    monkeypatch.setenv("CORECODER_TEST_ALPHA", "one")
    monkeypatch.setenv("CORECODER_TEST_BETA", "two")

    cli.cmd_set("set", None, None)

    out = capsys.readouterr().out
    assert "CORECODER_TEST_ALPHA=one" in out
    assert "CORECODER_TEST_BETA=two" in out


def test_bang_set_assigns_environment_variables(monkeypatch, capsys):
    monkeypatch.delenv("CORECODER_TEST_SET", raising=False)

    cli.cmd_set("set CORECODER_TEST_SET=works", None, None)

    assert os.environ["CORECODER_TEST_SET"] == "works"
    assert "CORECODER_TEST_SET set" in capsys.readouterr().out


def test_bang_set_displays_single_environment_variable(monkeypatch, capsys):
    monkeypatch.setenv("CORECODER_TEST_SINGLE", "value")

    cli.cmd_set("set CORECODER_TEST_SINGLE", None, None)

    assert "CORECODER_TEST_SINGLE=value" in capsys.readouterr().out
