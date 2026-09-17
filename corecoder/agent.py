"""Core agent loop

This is the heart of CoreCoder.  The pattern is simple:

    user message -> LLM (with tools) -> tool calls? -> execute -> loop
                                      -> text reply? -> return to user

It keeps looping until the LLM responds with plain text (no tool calls),
which means it's done working and ready to report back.
"""

import concurrent.futures
import inspect
import typing as t
from pathlib import Path

from .context import ContextManager
from .llm import LLM, ToolCall
from .project_guidance import load_project_guidance
from .prompt import PLAN_MODE_PROMPT, system_prompt
from .tools import Tool, ToolResult, get_tools
from .tools.agent import AgentTool
from .utils import find_project_root, render_tasks


class Agent:

    llm: LLM
    project_root: Path | None
    guidance_path: Path | None
    project_guidance: str
    tools: list[Tool]
    max_rounds: int
    plan_mode: bool

    def __init__(
        self,
        llm: LLM,
        tools: list[Tool] | None = None,
        max_context_tokens: int = 128_000,
        max_rounds: int = 50,
        permission: t.Any = None,
        hooks: t.Any = None,
    ) -> None:
        self.llm = llm
        self.project_root = find_project_root()
        self.tools = tools if tools is not None else get_tools()
        self.permission = permission
        self.hooks = hooks
        self._tool_by_name = {tool.name: tool for tool in self.tools}
        self.messages: list[dict[str, t.Any]] = []
        self.context = ContextManager(max_tokens=max_context_tokens)
        self.max_rounds = max_rounds
        self.guidance_path, self.project_guidance = load_project_guidance(self.project_root)
        self._system = system_prompt(
            self.tools,
            project_root=str(self.project_root) if self.project_root else "",
            project_guidance=self.project_guidance,
            guidance_path=str(self.guidance_path) if self.guidance_path else None,
        )
        self.plan_mode = False  # toggled by /plan; while on, mutating tools are refused
        self.changed_files: set[Path] = set()  # track files changed this session for /diff

        # wire up sub-agent capability
        for tool in self.tools:
            if isinstance(tool, AgentTool):
                t.cast(t.Any, tool)._parent_agent = self

        self.todo_tasks: list[dict[str, t.Any]] = []

    def _full_messages(self) -> list[dict[str, t.Any]]:
        system = self._system
        # re-injected every round, like the task list below, so a toggle made
        # between turns takes effect on the very next request
        if self.plan_mode:
            system += "\n\n" + PLAN_MODE_PROMPT
        # the task list is re-injected every round, so the model always sees the
        # current state rather than a stale copy buried in old tool results
        if self.todo_tasks is not None:
            rendered = render_tasks(self.todo_tasks)
            if rendered:
                system += "\n\n# Current task list\n" + rendered
        return [{"role": "system", "content": system}] + self.messages

    def _tool_schemas(self) -> list[dict[str, t.Any]]:
        return [t.schema() for t in self.tools]

    def chat(
        self,
        user_input: str,
        on_token: t.Callable[[str], None] | None = None,
        on_tool: t.Callable[[str, dict[str, t.Any]], None] | None = None,
    ) -> str:
        """Process one user message. May involve multiple LLM/tool rounds."""
        self.messages.append({"role": "user", "content": user_input})
        self.context.maybe_compress(self.messages, self.llm)

        for _ in range(self.max_rounds):
            resp = self.llm.chat(
                messages=self._full_messages(),
                tools=self._tool_schemas(),
                on_token=on_token,
            )

            # no tool calls -> LLM is done, return text
            if not resp.tool_calls:
                self.messages.append(resp.message)
                return resp.content

            # tool calls -> execute (parallel when multiple, like Claude Code's
            # StreamingToolExecutor which runs independent tools concurrently)
            self.messages.append(resp.message)

            try:
                if len(resp.tool_calls) == 1:
                    tc = resp.tool_calls[0]
                    if on_tool:
                        on_tool(tc.name, tc.arguments)
                    result = self._pre_hooks(tc) or self._permit(tc)
                    if result is None:
                        result = self._exec_tool(tc)
                        self._post_hooks(tc, result)
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": result,
                        }
                    )
                else:
                    # parallel execution for multiple tool calls
                    results = self._exec_tools_parallel(resp.tool_calls, on_tool)
                    for tc, result in zip(resp.tool_calls, results):
                        self.messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc.id,
                                "content": result,
                            }
                        )
            except KeyboardInterrupt:
                # Ctrl+C mid-execution would leave the assistant tool_calls
                # message without replies, poisoning the next request; backfill
                self._answer_pending_tool_calls(resp.tool_calls)
                raise

            # compress if tool outputs are big
            self.context.maybe_compress(self.messages, self.llm)

        return "(reached maximum tool-call rounds)"

    def _pre_hooks(self, tc: ToolCall) -> str | None:
        """PreToolUse hooks, fired before consent. A string return blocks the
        call and becomes the tool result the model sees; None lets it through."""
        if self.hooks is None:
            return None
        result = self.hooks.run_pre(tc.name, tc.arguments)
        return t.cast(str | None, result)

    def _post_hooks(self, tc: ToolCall, result: str) -> None:
        """PostToolUse hooks observe a finished call; they can never block."""
        if self.hooks is not None:
            self.hooks.run_post(tc.name, tc.arguments, result)

    def _permit(self, tc: ToolCall) -> str | None:
        """Consent check for one call. None means go ahead; a string is the
        refusal, returned as the tool result instead of executing."""
        # plan mode outranks consent, even --yes: while it's on nothing mutates
        tool = self._tool_by_name.get(tc.name)
        read_only = tool.read_only if tool is not None else False
        if read_only:
            return None
        if self.plan_mode and tc.name:
            return (
                "Plan mode is on, so this call was refused: plan mode is "
                "read-only. Do not retry it. Keep investigating with the "
                "read-only tools, then present the plan and stop. The user can "
                'approve it by typing "approve", or exit plan mode with /plan.'
            )
        if self.permission is None:
            return None
        result = self.permission.check(tc.name, tc.arguments)
        return t.cast(str | None, result)

    def _exec_tool(self, tc: ToolCall) -> str:
        """Execute a single tool call, returning the result string."""
        tool = self._tool_by_name.get(tc.name)
        if tool is None:
            return f"Error: unknown tool '{tc.name}'"
        # validate arguments first so a TypeError raised *inside* the tool isn't
        # mislabelled as a bad-arguments error from the caller
        try:
            inspect.signature(tool.execute).bind(**tc.arguments)
        except TypeError as e:
            return f"Error: bad arguments for {tc.name}: {e}"
        # a tool that blows up gets reported back as text, never kills the loop
        try:
            result = tool.execute(**tc.arguments)
            if isinstance(result, ToolResult):
                self.changed_files.update(result.changed_files)
                if result.todo_tasks is not None:
                    self.todo_tasks = result.todo_tasks
                return result.output
            else:
                return result
        except Exception as e:  # noqa: BLE001
            return f"Error executing {tc.name}: {e}"

    def _exec_tools_parallel(
        self,
        tool_calls: list[ToolCall],
        on_tool: t.Callable[[str, dict[str, t.Any]], None] | None = None,
    ) -> list[str]:
        """Run multiple tool calls concurrently using threads.

        This is inspired by Claude Code's StreamingToolExecutor which starts
        executing tools while the model is still generating.  We simplify to:
        when the model returns N tool calls at once, run them in parallel.
        """
        for tc in tool_calls:
            if on_tool:
                on_tool(tc.name, tc.arguments)

        # hooks and consent are settled up front on this thread: prompting
        # from pool workers would interleave several prompts on one terminal
        results: list[str | None] = [self._pre_hooks(tc) or self._permit(tc) for tc in tool_calls]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {i: pool.submit(self._exec_tool, tc) for i, tc in enumerate(tool_calls) if results[i] is None}
            for i, future in futures.items():
                results[i] = future.result()
        final_results = [result if result is not None else "" for result in results]
        for i in futures:
            self._post_hooks(tool_calls[i], final_results[i])
        return final_results

    def _answer_pending_tool_calls(self, tool_calls: list[ToolCall]) -> None:
        """Backfill a tool reply for every call that didn't get one.

        OpenAI-compatible APIs reject a request where an assistant message has
        tool_calls without a matching tool reply for each id, so this keeps the
        history valid when execution is interrupted partway through.
        """
        answered = {m.get("tool_call_id") for m in self.messages if m.get("role") == "tool"}
        for tc in tool_calls:
            if tc.id not in answered:
                self.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": "[interrupted]",
                    }
                )

    def reset(self) -> None:
        """Clear conversation history."""
        self.messages.clear()
