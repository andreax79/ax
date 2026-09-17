"""MCP stdio client, distilled to the slice of the protocol an agent uses.

Servers are configured in ~/.corecoder/mcp.json:

    {"mcpServers": {"fs": {"command": "npx", "args": ["-y", "some-fs-server", "/tmp"]}}}

Each server is a subprocess speaking JSON-RPC 2.0, one message per line over
stdin/stdout. Startup handshakes (`initialize`), pulls `tools/list`, and every
remote tool joins the agent as `mcp__<server>__<tool>`, so consent, hooks and
the main loop treat them exactly like the built-ins. A server that hangs or
dies fails that one call as an ordinary error string; it never kills the loop.
"""

import atexit
import json
import logging
import os
import subprocess
import threading
import time
import typing as t
from pathlib import Path

from . import __version__
from .tools.base import Tool

log = logging.getLogger(__name__)

CONFIG_FILE = Path.home() / ".corecoder" / "mcp.json"
PROTOCOL_VERSION = "2025-06-18"
INIT_TIMEOUT = 15  # seconds for initialize + tools/list at startup
CALL_TIMEOUT = 60  # seconds for one tools/call


class MCPError(RuntimeError):
    """Transport or protocol failure talking to one server."""


class MCPClient:
    """One stdio server process: handshake, list, call, shut down.

    A daemon thread owns stdout and parks each response under its request id,
    so calls from parallel tool execution match their own replies; the write
    lock keeps two threads' requests from interleaving on stdin.
    """

    def __init__(
        self,
        name: str,
        command: str,
        args: t.Sequence[str] = (),
        env: t.Mapping[str, str] | None = None,
    ) -> None:
        self.name = name
        self.call_timeout = CALL_TIMEOUT
        self._proc = subprocess.Popen(
            [command, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env={**os.environ, **(env or {})},  # servers inherit the user env, config overrides
            encoding="utf-8",
            errors="replace",
            bufsize=1,  # line buffered: the transport is newline-delimited JSON
        )
        if self._proc.stdin is None or self._proc.stdout is None:
            raise MCPError(f"MCP server {self.name!r} did not open stdio pipes")
        self._stdin = self._proc.stdin
        self._stdout = self._proc.stdout
        self._next_id = 0
        self._dead: MCPError | None = None
        self._responses: dict[int, dict[str, t.Any]] = {}
        self._cond = threading.Condition()
        self._write_lock = threading.Lock()
        threading.Thread(target=self._read_loop, daemon=True).start()
        try:
            self._request(
                "initialize",
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "corecoder", "version": __version__},
                },
                INIT_TIMEOUT,
            )
            self._notify("notifications/initialized")
            listed = self._request("tools/list", {}, INIT_TIMEOUT)
            self.tools = [MCPTool(self, t) for t in listed.get("tools", [])]
        except BaseException:
            self.close()  # a half-started server must not leak
            raise

    def call_tool(self, tool_name: str, arguments: dict[str, t.Any]) -> str:
        """Run one remote tool and return its text content."""
        result = self._request("tools/call", {"name": tool_name, "arguments": arguments}, self.call_timeout)
        text = "\n".join(part.get("text", "") for part in result.get("content", []) if part.get("type") == "text")
        if result.get("isError"):
            raise MCPError(text or f"{tool_name} reported an error")
        return text or json.dumps(result)  # non-text content: hand the model the raw result

    def close(self) -> None:
        """Shut the server down. Safe to call twice."""
        if self._proc.poll() is not None:
            return
        try:
            self._stdin.close()
        except OSError:
            pass
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()

    def _notify(self, method: str) -> None:
        try:
            self._write({"jsonrpc": "2.0", "method": method, "params": {}})
        except MCPError:
            pass  # a notification has no reply to lose; the next request meets the dead server

    def _request(self, method: str, params: dict[str, t.Any], timeout: float) -> dict[str, t.Any]:
        with self._cond:
            if self._dead is not None:
                raise self._dead
            self._next_id += 1
            req_id = self._next_id
        self._write({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        with self._cond:
            while req_id not in self._responses:
                if self._dead is not None:
                    raise self._dead
                left = deadline - time.monotonic()
                if left <= 0:
                    raise MCPError(f"MCP server {self.name!r} gave no answer to {method} in {timeout:g}s")
                self._cond.wait(left)
            msg = self._responses.pop(req_id)
        if "error" in msg:
            raise MCPError(f"MCP server {self.name!r} rejected {method}: {msg['error'].get('message', msg['error'])}")
        return msg.get("result", {})

    def _write(self, msg: dict[str, t.Any]) -> None:
        try:
            with self._write_lock:
                self._stdin.write(json.dumps(msg) + "\n")
                self._stdin.flush()
        except OSError as e:
            raise MCPError(f"MCP server {self.name!r} is not writable: {e}") from e

    def _read_loop(self) -> None:
        try:
            for line in self._stdout:
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    log.warning("MCP server %r sent a non-JSON line, skipped", self.name)
                    continue
                if "id" not in msg:
                    continue  # server notification: nothing here needs an answer
                with self._cond:
                    self._responses[msg["id"]] = msg
                    self._cond.notify_all()
        except (OSError, ValueError):
            pass
        with self._cond:
            # stdout closing means the server is gone for good
            if self._dead is None:
                self._dead = MCPError(f"MCP server {self.name!r} exited")
            self._cond.notify_all()


class MCPTool(Tool):
    """A tool living in an MCP server, registered under a mcp__ name so it can
    never collide with a built-in. Side effects are unknown, so the consent gate asks
    first, like any mutating tool.
    """

    parameters: t.ClassVar[dict[str, t.Any]] = {"type": "object", "properties": {}}

    def __init__(self, client: MCPClient, spec: dict[str, t.Any]) -> None:
        self._client = client
        self._remote_name = str(spec["name"])
        self.name = f"mcp__{client.name}__{self._remote_name}"
        self.description = str(spec.get("description") or "")
        self._parameters = t.cast(dict[str, t.Any], spec.get("inputSchema") or {"type": "object", "properties": {}})

    def schema(self) -> dict[str, t.Any]:
        """OpenAI function-calling schema using the remote tool's JSON schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self._parameters,
            },
        }

    def execute(self, **kwargs: dict[str, t.Any]) -> str:
        return self._client.call_tool(self._remote_name, kwargs)


_live_clients: list[MCPClient] = []


def load_mcp_tools(path: Path = CONFIG_FILE) -> list[Tool]:
    """Start the configured servers and return their tools. A missing file
    means no MCP; a broken file or a server that won't start gets one clear
    warning and the agent carries on with whatever loaded."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (json.JSONDecodeError, OSError) as e:
        log.warning("ignoring %s: %s", path, e)
        return []
    tools: list[Tool] = []
    for name, spec in (data.get("mcpServers") or {}).items():
        try:
            client = MCPClient(name, spec["command"], spec.get("args", []), spec.get("env"))
        except (MCPError, OSError, KeyError, TypeError) as e:
            log.warning("MCP server %r skipped: %s", name, e)
            continue
        _live_clients.append(client)
        tools.extend(client.tools)
    return tools


@atexit.register
def _shutdown() -> None:
    for client in _live_clients:
        client.close()
