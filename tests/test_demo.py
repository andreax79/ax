"""Tests for the offline scripted demo and ScriptedLLM."""

import pytest

from corecoder.llm import LLMResponse, ToolCall
from tests.demo import ScriptedLLM


def test_scripted_llm_plays_turns_in_order():
    llm = ScriptedLLM(
        [
            LLMResponse(content="first"),
            LLMResponse(content="second", tool_calls=[ToolCall(id="c1", name="bash", arguments={"command": "true"})]),
        ]
    )

    r1 = llm.chat(messages=[])
    r2 = llm.chat(messages=[])

    assert r1.content == "first"
    assert r2.content == "second"
    assert r2.tool_calls[0].name == "bash"

    with pytest.raises(RuntimeError):
        llm.chat(messages=[])
