"""Interactive REPL - the user-facing terminal interface."""

import argparse
import getpass
import os
import socket
import sys
import typing as t
from datetime import datetime

from prompt_toolkit import prompt as pt_prompt  # type: ignore[import]
from prompt_toolkit.formatted_text import ANSI  # type: ignore[import]
from prompt_toolkit.history import FileHistory  # type: ignore[import]
from prompt_toolkit.key_binding import KeyBindings  # type: ignore[import]
from rich.console import Console  # type: ignore[import]
from rich.markdown import Markdown  # type: ignore[import]
from rich.panel import Panel  # type: ignore[import]
from rich.text import Text  # type: ignore[import]

from . import __version__
from .agent import Agent
from .cmds import (
    cmd_approve,
    cmd_compact,
    cmd_diff,
    cmd_help,
    cmd_model,
    cmd_plan,
    cmd_reset,
    cmd_save,
    cmd_sessions,
    cmd_shell,
    cmd_tokens,
)
from .config import DEFAULT_MODEL, Config
from .hooks import HOOKS_CONFIG_FILE, load_hooks
from .llm import LLM, LiteLLM
from .mcp import MCP_CONFIG_FILE, load_mcp_tools
from .permissions import Permission
from .session import load_session
from .tools import get_tools
from .utils import CONFIG_DIR, is_unix_command, render_tasks

console = Console()

Args = argparse.Namespace
ToolArgs = dict[str, t.Any]


DEFAULT_PROMPT = (
    "\\m "
    "[bold cyan]\\u[/]"
    "@"
    "[bold green]\\h[/]"
    ":"
    "[yellow]\\W[/]"
    "\\p [bold red]❯[/] "
)


def _parse_args() -> Args:
    p = argparse.ArgumentParser(
        prog="ax",
        description="Minimal AI coding agent. Works with any OpenAI-compatible LLM.",
    )
    p.add_argument(
        "-m",
        "--model",
        help=f"Model name (default: $AX_MODEL or {DEFAULT_MODEL})",
    )
    p.add_argument("--base-url", help="API base URL (default: $OPENAI_BASE_URL)")
    p.add_argument("--api-key", help="API key (default: $OPENAI_API_KEY)")
    p.add_argument("-p", "--prompt", help="One-shot prompt (non-interactive mode)")
    p.add_argument(
        "--yes",
        action="store_true",
        help="Auto-approve every tool call (for scripts and CI)",
    )
    p.add_argument("-r", "--resume", metavar="ID", help="Resume a saved session")
    p.add_argument(
        "-v", "--version", action="version", version=f"%(prog)s {__version__}"
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    config = Config.from_env()

    # CLI args override env vars
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    if args.api_key:
        config.api_key = args.api_key

    if not config.api_key:
        console.print("[red bold]No API key found.[/]")
        console.print(
            "Set one of: OPENAI_API_KEY, DEEPSEEK_API_KEY, or AX_API_KEY\n"
            "\nExamples:\n"
            "  # OpenAI\n"
            "  export OPENAI_API_KEY=sk-...\n"
            "\n"
            "  # DeepSeek\n"
            "  export OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://api.deepseek.com\n"
            "\n"
            "  # Ollama (local)\n"
            "  export OPENAI_API_KEY=ollama OPENAI_BASE_URL=http://localhost:11434/v1 AX_MODEL=qwen2.5-coder\n"
        )
        sys.exit(1)

    llm_cls = LiteLLM if config.provider == "litellm" else LLM
    llm = llm_cls(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        max_completion_tokens=config.max_tokens,
    )
    # llm = llm_cls(
    #     model=config.model,
    #     api_key=config.api_key,
    #     base_url=config.base_url,
    #     temperature=config.temperature,
    #     max_tokens=config.max_tokens,
    # )
    # consent layer: ask in the REPL, refuse in one-shot mode, --yes skips it
    if args.yes:
        permission = Permission(allow_all=True)
    elif args.prompt:
        permission = Permission()
    else:
        permission = Permission(ask=_ask_permission)
    agent = Agent(
        llm=llm,
        tools=[*get_tools(), *load_mcp_tools()],
        max_context_tokens=config.max_context_tokens,
        permission=permission,
        hooks=load_hooks(),
    )

    # resume saved session
    if args.resume:
        loaded = load_session(agent, args.resume)
        if loaded:
            # restore the model from the saved session unless overridden by CLI
            if not args.model:
                agent.llm.model = loaded["model"]
                config.model = loaded["model"]
            console.print(
                f"[green]Resumed session: {args.resume} (model: {agent.llm.model})[/green]"
            )
        else:
            console.print(f"[red]Session '{args.resume}' not found.[/red]")
            sys.exit(1)

    # one-shot mode
    if args.prompt:
        _run_once(agent, args.prompt)
        return

    # interactive REPL
    _repl(agent, config)


def _ask_permission(tool_name: str, arguments: ToolArgs) -> str:
    """REPL consent prompt. Anything but a clear yes counts as a no."""
    console.print(
        f"\n[bold yellow]permission requested:[/] [cyan]{tool_name}[/cyan]({_brief(arguments)})"
    )
    try:
        answer = (
            pt_prompt("  [y] allow once  [a] always allow this tool  [n] deny: ")
            .strip()
            .lower()
        )
    except (EOFError, KeyboardInterrupt):
        console.print("[dim]denied[/dim]")
        return "deny"
    if answer in ("y", "yes"):
        return "once"
    if answer in ("a", "always"):
        return "always"
    return "deny"


def _run_once(agent: Agent, prompt: str) -> None:
    """Non-interactive: run one prompt and exit."""
    perm = agent.permission
    if perm is not None and perm.ask is None and not perm.allow_all:
        console.print(
            "[dim]one-shot mode: mutating tools are refused unless you pass --yes[/dim]"
        )

    def on_token(tok: str) -> None:
        print(tok, end="", flush=True)

    def on_tool(name: str, kwargs: ToolArgs) -> None:
        console.print(f"\n[dim]> {name}({_brief(kwargs)})[/dim]")

    try:
        agent.chat(prompt, on_token=on_token, on_tool=on_tool)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(130)
    except Exception as e:  # noqa: BLE001
        # one-shot mode: print whatever went wrong and exit non-zero
        console.print(f"\n[red]Error: {e}[/red]")
        sys.exit(1)
    print()


def _repl(agent: Agent, config: Config) -> None:
    """Interactive read-eval-print loop."""
    perm = agent.permission
    mode = (
        "auto-approve every tool call (--yes)"
        if (perm and perm.allow_all)
        else "ask before mutating tools"
    )
    mcp_count = sum(1 for t in agent.tools if t.name.startswith("mcp__"))
    project_root_line = (
        f"\nProject root: [dim]{agent.project_root}[/dim]" if agent.project_root else ""
    )
    console.print(
        Panel(
            f"[bold]ax[/bold] v{__version__}\n"
            f"Model: [cyan]{config.model}[/cyan]"
            + (f"  Base: [dim]{config.base_url}[/dim]" if config.base_url else "")
            + f"\nPermissions: [cyan]{mode}[/cyan]"
            + project_root_line
            + (
                f"\nHooks: [cyan]{len(agent.hooks.pre)} pre, {len(agent.hooks.post)} post[/cyan] from {HOOKS_CONFIG_FILE}"
                if agent.hooks
                else ""
            )
            + (
                f"\nMCP: [cyan]{mcp_count} tools[/cyan] from {MCP_CONFIG_FILE}"
                if mcp_count
                else ""
            )
            + "\nType [bold]/help[/bold] for commands, [bold]Ctrl+C[/bold] to cancel, [bold]quit[/bold] to exit.",
            border_style="blue",
        )
    )

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    history = FileHistory(CONFIG_DIR / "history")

    # Enter submits, Escape+Enter inserts a newline (for pasting code blocks etc.)
    kb = KeyBindings()

    @kb.add("enter")  # type: ignore[misc]
    def _submit(event: t.Any) -> None:
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")  # type: ignore[misc]
    def _newline(event: t.Any) -> None:
        event.current_buffer.insert_text("\n")

    if not "AX_PROMPT" in os.environ:
        os.environ["AX_PROMPT"] = DEFAULT_PROMPT

    while True:
        try:
            prompt = render_prompt(agent, config)
            user_input = pt_prompt(
                prompt,
                history=history,
                multiline=True,
                key_bindings=kb,
                prompt_continuation="...  ",
            ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye!")
            break

        if not user_input:
            continue

        if user_input.startswith("!"):
            cmd_shell(user_input, agent, config)
            continue

        # built-in commands
        if user_input.lower() in ("quit", "exit", "/quit", "/exit"):
            break
        if user_input == "/help":
            cmd_help(user_input, agent, config)
            continue
        if user_input == "/reset":
            cmd_reset(user_input, agent, config)
            continue
        if user_input == "/plan":
            cmd_plan(user_input, agent, config)
            continue
        if agent.plan_mode and user_input.lower() in ("approve", "/approve"):
            cmd_approve(user_input, agent, config)
            user_input = (
                "approve"  # the approval itself goes to the model, which then executes
            )
        if user_input == "/tokens":
            cmd_tokens(user_input, agent, config)
            continue
        if user_input == "/model" or user_input.startswith("/model "):
            cmd_model(user_input, agent, config)
            continue
        if user_input == "/compact":
            cmd_compact(user_input, agent, config)
            continue
        if user_input == "/save":
            cmd_save(user_input, agent, config)
            continue
        if user_input == "/diff":
            cmd_diff(user_input, agent, config)
            continue
        if user_input == "/sessions":
            cmd_sessions(user_input, agent, config)
            continue

        if is_unix_command(user_input, default=False):
            cmd_shell(user_input, agent, config)
            continue

        # an unknown /command shouldn't be sent to the model as a prompt
        if user_input.startswith("/"):
            console.print(
                f"[yellow]Unknown command: {user_input.split()[0]} (try /help)[/yellow]"
            )
            continue

        # call the agent
        streamed: list[str] = []

        def on_token(tok: str, streamed: list[str] = streamed) -> None:
            streamed.append(tok)
            print(tok, end="", flush=True)

        def on_tool(name: str, kwargs: ToolArgs) -> None:
            # if agent._todo is not None:
            #     todo = agent._todo.render()
            # else:
            #     todo = ""
            todo = render_tasks(agent.todo_tasks)
            console.print(f"[dim]{todo}\n> {name}({_brief(kwargs)})[/dim]")

        try:
            response = agent.chat(user_input, on_token=on_token, on_tool=on_tool)
            if streamed:
                print()  # newline after streamed tokens
            else:
                # response wasn't streamed (came after tool calls)
                console.print(Markdown(response))
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted.[/yellow]")
        except Exception as e:  # noqa: BLE001
            # keep the REPL alive no matter what chat() throws
            console.print(f"\n[red]Error: {e}[/red]")


def _brief_format(k: str, v: t.Any) -> str:
    """Return a brief string representation of a key-value pair."""
    if k in ("path", "file_path"):
        return f"{k}={v!r}"
    else:
        return f"{k}={repr(v)[:40]}"


def _brief(kwargs: ToolArgs, maxlen: int = 140) -> str:
    s = ", ".join(
        _brief_format(k, v)
        for k, v in kwargs.items()
        if k not in ("timeout", "old_string", "new_string")
    )
    return s[:maxlen] + ("..." if len(s) > maxlen else "")


def render_prompt(agent: Agent, config: Config, prompt: str | None = None) -> ANSI:
    """
    Render a Bash-like PS1 using Rich markup.

    Supported Bash escapes:
        \\u  username
        \\h  short hostname
        \\H  full hostname
        \\w  current working directory
        \\W  basename of current directory
        \\t  current time HH:MM:SS
        \\d  current date
        \\n  newline
        \\r  carriage return
        \\p  plan mode indicator (if enabled)

    Rich markup is also supported, e.g.:

        [bold cyan]\\u[/]@[green]\\h[/]:[yellow]\\W[/] [bold]❯[/]
    """
    cprompt: str = prompt or os.environ.get("AX_PROMPT", DEFAULT_PROMPT)  # type: ignore[assignment]

    replacements = {
        "u": getpass.getuser(),  # username
        "h": socket.gethostname().split(".", 1)[0],  # short hostname
        "H": socket.gethostname(),  # full hostname
        "w": os.getcwd(),  # current working directory
        "W": (os.path.basename(os.getcwd()) or os.sep),  # basename of current directory (or / if root)
        "t": datetime.now().strftime("%H:%M:%S"),  # current time
        "d": datetime.now().strftime("%a %b %d"),  # current date
        "n": "\n",  # newline
        "r": "\r",  # carriage return
        "m": config.model,  # model name
        "p": agent.plan_mode and " [dim](plan)[/dim]" or "",  # plan mode indicator
    }

    # First expand Bash-style escapes
    expanded = []
    i = 0

    while i < len(cprompt):
        if cprompt[i] == "\\" and i + 1 < len(cprompt):
            code = cprompt[i + 1]

            if code in replacements:
                expanded.append(replacements[code])
                i += 2
                continue

        expanded.append(cprompt[i])
        i += 1

    # Let Rich parse its markup
    text = Text.from_markup("".join(expanded))

    # Export Rich's rendering to ANSI
    with console.capture() as capture:
        console.print(text, end="")

    return ANSI(capture.get())
