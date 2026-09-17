"""Offline scripted demo: watch the full agent loop with no API key.

Runs one scripted task through the real Agent loop with a ScriptedLLM, so the
thought -> tool call -> observation cycle renders exactly as it would against
a live model. Useful for demos, screenshots, and as a smoke test of the loop.
"""

from corecoder.llm import LLMResponse


class ScriptedLLM:
    """Deterministic offline LLM for demos and smoke tests.

    Plays back a list of LLMResponse turns, one per chat() call, streaming
    each turn's content through on_token. Running out of turns is an error,
    not a silent hang, so a broken loop shows up immediately.
    """

    total_prompt_tokens = 0
    total_completion_tokens = 0

    def __init__(self, script: list[LLMResponse], model: str = "scripted-demo"):
        self._turns = list(script)
        self.model = model

    def chat(self, messages, tools=None, on_token=None) -> LLMResponse:
        if not self._turns:
            raise RuntimeError("ScriptedLLM ran out of turns")
        resp = self._turns.pop(0)
        if on_token and resp.content:
            on_token(resp.content)
        self.total_completion_tokens += len(resp.content.split())
        return resp
