"""Interactive REPL - the user-facing terminal interface."""

import argparse
import os
import shlex
import subprocess
import sys
import typing as t

from prompt_toolkit import prompt as pt_prompt  # type: ignore[import]
from prompt_toolkit.history import FileHistory  # type: ignore[import]
from prompt_toolkit.key_binding import KeyBindings  # type: ignore[import]
from rich.console import Console  # type: ignore[import]
from rich.markdown import Markdown  # type: ignore[import]
from rich.panel import Panel  # type: ignore[import]

from . import __version__
from .agent import Agent
from .config import Config
from .hooks import load_hooks
from .llm import LLM, LiteLLM
from .mcp import load_mcp_tools
from .permissions import Permission
from .project_guidance import load_project_guidance
from .prompt import system_prompt
from .session import list_sessions, load_session, save_session
from .tools import get_tools
from .tools.bash import set_cwd
from .utils import find_project_root, render_tasks

console = Console()

Args = argparse.Namespace
ToolArgs = dict[str, t.Any]


def _parse_args() -> Args:
    p = argparse.ArgumentParser(
        prog="corecoder",
        description="Minimal AI coding agent. Works with any OpenAI-compatible LLM.",
    )
    p.add_argument("-m", "--model", help="Model name (default: $CORECODER_MODEL or gpt-5.5)")
    p.add_argument("--base-url", help="API base URL (default: $OPENAI_BASE_URL)")
    p.add_argument("--api-key", help="API key (default: $OPENAI_API_KEY)")
    p.add_argument("-p", "--prompt", help="One-shot prompt (non-interactive mode)")
    p.add_argument("--yes", action="store_true", help="Auto-approve every tool call (for scripts and CI)")
    p.add_argument("-r", "--resume", metavar="ID", help="Resume a saved session")
    p.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
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
            "Set one of: OPENAI_API_KEY, DEEPSEEK_API_KEY, or CORECODER_API_KEY\n"
            "\nExamples:\n"
            "  # OpenAI\n"
            "  export OPENAI_API_KEY=sk-...\n"
            "\n"
            "  # DeepSeek\n"
            "  export OPENAI_API_KEY=sk-... OPENAI_BASE_URL=https://api.deepseek.com\n"
            "\n"
            "  # Ollama (local)\n"
            "  export OPENAI_API_KEY=ollama OPENAI_BASE_URL=http://localhost:11434/v1 CORECODER_MODEL=qwen2.5-coder\n"
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
            console.print(f"[green]Resumed session: {args.resume} (model: {agent.llm.model})[/green]")
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
    console.print(f"\n[bold yellow]permission requested:[/] [cyan]{tool_name}[/cyan]({_brief(arguments)})")
    try:
        answer = pt_prompt("  [y] allow once  [a] always allow this tool  [n] deny: ").strip().lower()
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
        console.print("[dim]one-shot mode: mutating tools are refused unless you pass --yes[/dim]")

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
    mode = "auto-approve every tool call (--yes)" if (perm and perm.allow_all) else "ask before mutating tools"
    mcp_count = sum(1 for t in agent.tools if t.name.startswith("mcp__"))
    project_root_line = f"\nProject root: [dim]{agent.project_root}[/dim]" if agent.project_root else ""
    console.print(
        Panel(
            f"[bold]CoreCoder[/bold] v{__version__}\n"
            f"Model: [cyan]{config.model}[/cyan]"
            + (f"  Base: [dim]{config.base_url}[/dim]" if config.base_url else "")
            + f"\nPermissions: [cyan]{mode}[/cyan]"
            + project_root_line
            + (
                f"\nHooks: [cyan]{len(agent.hooks.pre)} pre, {len(agent.hooks.post)} post[/cyan] from ~/.corecoder/hooks.json"
                if agent.hooks
                else ""
            )
            + (f"\nMCP: [cyan]{mcp_count} tools[/cyan] from ~/.corecoder/mcp.json" if mcp_count else "")
            + "\nType [bold]/help[/bold] for commands, [bold]Ctrl+C[/bold] to cancel, [bold]quit[/bold] to exit.",
            border_style="blue",
        )
    )

    hist_path = os.path.expanduser("~/.corecoder_history")
    history = FileHistory(hist_path)

    # Enter submits, Escape+Enter inserts a newline (for pasting code blocks etc.)
    kb = KeyBindings()

    @kb.add("enter")  # type: ignore[misc]
    def _submit(event: t.Any) -> None:
        event.current_buffer.validate_and_handle()

    @kb.add("escape", "enter")  # type: ignore[misc]
    def _newline(event: t.Any) -> None:
        event.current_buffer.insert_text("\n")

    while True:
        try:
            user_input = pt_prompt(
                "You (plan) > " if agent.plan_mode else "You > ",
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
            _run_shell_input(user_input, agent)
            continue

        # built-in commands
        if user_input.lower() in ("quit", "exit", "/quit", "/exit"):
            break
        if user_input == "/help":
            _show_help()
            continue
        if user_input == "/reset":
            agent.reset()
            console.print("[yellow]Conversation reset.[/yellow]")
            continue
        if user_input == "/plan":
            agent.plan_mode = not agent.plan_mode
            if agent.plan_mode:
                console.print(
                    "[yellow]Plan mode on.[/yellow] The agent can look but not touch: it will "
                    "investigate read-only and present a plan. Type [bold]approve[/bold] to "
                    "accept the plan, or [bold]/plan[/bold] again to exit."
                )
            else:
                console.print("[yellow]Plan mode off.[/yellow]")
            continue
        if agent.plan_mode and user_input.lower() in ("approve", "/approve"):
            agent.plan_mode = False
            console.print("[yellow]Plan mode off.[/yellow]")
            user_input = "approve"  # the approval itself goes to the model, which then executes
        if user_input == "/tokens":
            p = agent.llm.total_prompt_tokens
            c = agent.llm.total_completion_tokens
            line = f"Tokens: [cyan]{p}[/cyan] prompt + [cyan]{c}[/cyan] completion = [bold]{p + c}[/bold] total"
            console.print(line)
            continue
        if user_input == "/model" or user_input.startswith("/model "):
            new_model = user_input[7:].strip() if user_input.startswith("/model ") else ""
            if new_model:
                agent.llm.model = new_model
                config.model = new_model
                console.print(f"Switched to [cyan]{new_model}[/cyan]")
            else:
                console.print(f"Current model: [cyan]{config.model}[/cyan]")
            continue
        if user_input == "/compact":
            from .context import estimate_tokens

            before = estimate_tokens(agent.messages)
            compressed = agent.context.maybe_compress(agent.messages, agent.llm)
            after = estimate_tokens(agent.messages)
            if compressed:
                console.print(f"[green]Compressed: {before} → {after} tokens ({len(agent.messages)} messages)[/green]")
            else:
                console.print(f"[dim]Nothing to compress ({before} tokens, {len(agent.messages)} messages)[/dim]")
            continue
        if user_input == "/save":
            sid = save_session(agent, config.model)
            console.print(f"[green]Session saved: {sid}[/green]")
            console.print(f"Resume with: corecoder -r {sid}")
            continue
        if user_input == "/diff":
            if not agent.changed_files:
                console.print("[dim]No files modified this session.[/dim]")
            else:
                console.print(f"[bold]Files modified this session ({len(agent.changed_files)}):[/bold]")
                for f in sorted(agent.changed_files):
                    console.print(f"  [cyan]{f}[/cyan]")
            continue
        if user_input == "/sessions":
            sessions = list_sessions()
            if not sessions:
                console.print("[dim]No saved sessions.[/dim]")
            else:
                for s in sessions:
                    console.print(f"  [cyan]{s['id']}[/cyan] ({s['model']}, {s['saved_at']}) {s['preview']}")
            continue

        # an unknown /command shouldn't be sent to the model as a prompt
        if user_input.startswith("/"):
            console.print(f"[yellow]Unknown command: {user_input.split()[0]} (try /help)[/yellow]")
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


def _run_shell_input(user_input: str, agent: Agent | None = None) -> None:
    """Handle REPL lines prefixed with ! as direct shell commands."""
    command = user_input[1:].strip()
    if not command:
        shell = os.environ.get("SHELL") or ("cmd" if os.name == "nt" else "/bin/sh")
        try:
            subprocess.run(shell, shell=False, check=False)
        except FileNotFoundError:
            console.print(f"[red]Shell not found: {shell}[/red]")
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
            agent.guidance_path, agent.project_guidance = load_project_guidance(agent.project_root)
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


def _show_help() -> None:
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
            "  !<command>     Run a shell command directly\n"
            "\n"
            "[bold]Input:[/bold]\n"
            "  Enter          Submit message\n"
            "  Esc+Enter      Insert newline (for pasting code)",
            title="CoreCoder Help",
            border_style="dim",
        )
    )


def _brief_format(k: str, v: t.Any) -> str:
    """Return a brief string representation of a key-value pair."""
    if k in ("path", "file_path"):
        return f"{k}={v!r}"
    else:
        return f"{k}={repr(v)[:40]}"


def _brief(kwargs: ToolArgs, maxlen: int = 140) -> str:
    s = ", ".join(_brief_format(k, v) for k, v in kwargs.items() if k not in ("timeout", "old_string", "new_string"))
    return s[:maxlen] + ("..." if len(s) > maxlen else "")
