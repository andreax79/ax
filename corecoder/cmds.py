"""Interactive REPL - the user-facing terminal interface."""

import os
import shlex
import subprocess

from rich.console import Console  # type: ignore[import]
from rich.panel import Panel  # type: ignore[import]

from .agent import Agent
from .config import Config
from .project_guidance import load_project_guidance
from .prompt import system_prompt
from .session import list_sessions, save_session
from .tools.bash import set_cwd
from .utils import find_project_root

console = Console()


def cmd_reset(user_input: str, agent: Agent, config: Config) -> None:
    """Handle /reset command."""
    agent.reset()
    console.print("[yellow]Conversation reset.[/yellow]")


def cmd_plan(user_input: str, agent: Agent, config: Config) -> None:
    """Handle /plan command."""
    agent.plan_mode = not agent.plan_mode
    if agent.plan_mode:
        console.print(
            "[yellow]Plan mode on.[/yellow] The agent can look but not touch: it will "
            "investigate read-only and present a plan. Type [bold]approve[/bold] to "
            "accept the plan, or [bold]/plan[/bold] again to exit."
        )
    else:
        console.print("[yellow]Plan mode off.[/yellow]")


def cmd_approve(user_input: str, agent: Agent, config: Config) -> None:
    """Handle /approve command."""
    if agent.plan_mode:
        agent.plan_mode = False
        console.print("[yellow]Plan mode off.[/yellow]")
    else:
        console.print("[dim]Not in plan mode.[/dim]")


def cmd_model(user_input: str, agent: Agent, config: Config) -> None:
    """Handle /model command."""
    new_model = user_input[7:].strip() if user_input.startswith("/model ") else ""
    if new_model:
        agent.llm.model = new_model
        config.model = new_model
        console.print(f"Switched to [cyan]{new_model}[/cyan]")
    else:
        console.print(f"Current model: [cyan]{config.model}[/cyan]")


def cmd_tokens(user_input: str, agent: Agent, config: Config) -> None:
    """Handle /tokens command."""
    p = agent.llm.total_prompt_tokens
    c = agent.llm.total_completion_tokens
    line = f"Tokens: [cyan]{p}[/cyan] prompt + [cyan]{c}[/cyan] completion = [bold]{p + c}[/bold] total"
    console.print(line)


def cmd_compact(user_input: str, agent: Agent, config: Config) -> None:
    """Handle /compact command."""
    from .context import estimate_tokens

    before = estimate_tokens(agent.messages)
    compressed = agent.context.maybe_compress(agent.messages, agent.llm)
    after = estimate_tokens(agent.messages)
    if compressed:
        console.print(
            f"[green]Compressed: {before} → {after} tokens ({len(agent.messages)} messages)[/green]"
        )
    else:
        console.print(
            f"[dim]Nothing to compress ({before} tokens, {len(agent.messages)} messages)[/dim]"
        )


def cmd_save(user_input: str, agent: Agent, config: Config) -> None:
    """Handle /save command."""
    sid = save_session(agent, config.model)
    console.print(f"[green]Session saved: {sid}[/green]")
    console.print(f"Resume with: corecoder -r {sid}")


def cmd_diff(user_input: str, agent: Agent, config: Config) -> None:
    """Handle /diff command."""
    if not agent.changed_files:
        console.print("[dim]No files modified this session.[/dim]")
        return
    console.print(
        f"[bold]Files modified this session ({len(agent.changed_files)}):[/bold]"
    )
    for f in sorted(agent.changed_files):
        console.print(f"  [cyan]{f}[/cyan]")


def cmd_sessions(user_input: str, agent: Agent, config: Config) -> None:
    """Handle /sessions command."""
    sessions = list_sessions()
    if not sessions:
        console.print("[dim]No saved sessions.[/dim]")
    else:
        console.print(f"[bold]Saved sessions ({len(sessions)}):[/bold]")
        for s in sessions:
            console.print(
                f"  [cyan]{s['id']}[/cyan] ({s['model']}, {s['saved_at']}) {s['preview']}"
            )


def cmd_shell(user_input: str, agent: Agent, config: Config) -> None:
    """Handle direct shell commands."""
    user_input = user_input.removeprefix("!")
    command = user_input.strip()
    if not command:
        shell = os.environ.get("SHELL") or ("cmd" if os.name == "nt" else "/bin/sh")
        try:
            subprocess.run(shell, shell=False, check=False)
        except FileNotFoundError:
            console.print(f"[red]Shell not found: {shell}[/red]")
        return

    if command == "set" or command.startswith("set "):
        cmd_set(command, agent, config)
        return

    if command == "cd" or command.startswith("cd "):
        target = command[2:].strip()
        if not target and agent is not None:
            target = str(agent.project_root)
        if not target:
            return
        try:
            unquoted = shlex.split(target)
        except ValueError as e:
            console.print(f"[red]cd: {e}[/red]")
            return
        if len(unquoted) == 1:
            target = unquoted[0]
        try:
            os.chdir(os.path.expandvars(os.path.expanduser(target)))
        except OSError as e:
            console.print(f"[red]cd: {e}[/red]")
            return
        cwd = os.getcwd()
        set_cwd(cwd)
        if agent is not None:
            agent.project_root = find_project_root()
            agent.guidance_path, agent.project_guidance = load_project_guidance(
                agent.project_root
            )
            agent._system = system_prompt(
                agent.tools,
                project_root=str(agent.project_root) if agent.project_root else "",
                project_guidance=agent.project_guidance,
                guidance_path=str(agent.guidance_path) if agent.guidance_path else None,
            )
        console.print(f"[dim]cwd: {cwd}[/dim]")
        return

    try:
        proc = subprocess.run(command, shell=True, check=False)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        return
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]Error running command: {e}[/red]")
        return
    if proc.returncode:
        console.print(f"[dim][exit code: {proc.returncode}][/dim]")


def cmd_set(command: str, agent: Agent, config: Config) -> None:
    """Display or set environment variables."""
    args = shlex.split(command)
    if len(args) == 1:
        for name in sorted(os.environ):
            console.print(f"{name}={os.environ[name]}")
        return

    if len(args) == 2 and "=" not in args[1]:
        name = args[1]
        value = os.environ.get(name)
        if value is None:
            console.print(f"[red]set: {name} not set[/red]")
        else:
            console.print(f"{name}={value}")
        return

    for assignment in args[1:]:
        if "=" not in assignment:
            console.print(f"[red]set: expected NAME=VALUE, got {assignment!r}[/red]")
            return
        name, value = assignment.split("=", 1)
        if not name:
            console.print("[red]set: variable name cannot be empty[/red]")
            return
        os.environ[name] = value
        console.print(f"[dim]{name} set[/dim]")


def cmd_help(user_input: str, agent: Agent, config: Config) -> None:
    console.print(
        Panel(
            "[bold]Commands:[/bold]\n"
            "  /help          Show this help\n"
            "  /reset         Clear conversation history\n"
            "  /model         Show current model\n"
            "  /model <name>  Switch model mid-conversation\n"
            "  /tokens        Show token usage\n"
            "  /compact       Compress conversation context\n"
            "  /diff          Show files modified this session\n"
            "  /undo          Revert the most recent file change\n"
            "  /plan          Toggle plan mode: read-only, then a plan to approve\n"
            "  /save          Save session to disk\n"
            "  /sessions      List saved sessions\n"
            "  quit           Exit CoreCoder\n"
            "\n"
            "[bold]Shell:[/bold]\n"
            "  !              Start an interactive shell\n"
            "  !cd <path>     Change current directory\n"
            "  !set [A=B]     Display or set shell variables\n"
            "  !<command>     Run a shell command directly\n"
            "\n"
            "[bold]Input:[/bold]\n"
            "  Enter          Submit message\n"
            "  Esc+Enter      Insert newline (for pasting code)",
            title="CoreCoder Help",
            border_style="dim",
        )
    )
