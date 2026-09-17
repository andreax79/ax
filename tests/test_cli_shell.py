import os
from types import SimpleNamespace

from corecoder import cli
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
