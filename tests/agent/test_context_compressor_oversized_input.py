"""Regression tests for the long-task compression failure (LEO / Codex gpt-5.5).

Reproduces the production incident observed 2026-06-09 07:28 on the live LEO
runtime (Codex OAuth gpt-5.5, real context window = 272,000 tokens):

    07:28:23 Preflight compression: ~291,291 tokens >= 136,000 threshold (ctx 272,000)
    07:28:41 compression done: 49->48, ~291K -> ~290K        # barely reduced
    07:29:01 API call failed: "Your input exceeds the context window of this model"
    07:29:38 compression done: 47->46, ~268K -> ~52K         # finally fit

The failure mechanism: ``compress()`` returned a transcript that was STILL
above the model's context window (~290K > 272K), so the *next* main API call
exceeded the window and failed.  The residual stayed high because oversized
tool output sat where token-budget tail protection keeps it verbatim, and the
summary pass only collapses the (small) middle region.

These tests pin the invariant that protects against the incident:

    after compress(), the compressed transcript must fit within context_length

plus the documented Phase-1 effect of ``protect_last_n`` on how aggressively
old tool output is pruned before summarization.
"""

import pytest
from unittest.mock import patch, MagicMock

from agent.context_compressor import ContextCompressor
from agent.model_metadata import estimate_messages_tokens_rough


# Real LEO/Codex gpt-5.5 OAuth context window.  The compressor floors the
# threshold at MINIMUM_CONTEXT_LENGTH (64K), so a realistic large window must
# be used for the budgets to match production behavior.
_CTX = 272_000


def _make_compressor(protect_last_n: int) -> ContextCompressor:
    with patch(
        "agent.context_compressor.get_model_context_length", return_value=_CTX
    ):
        return ContextCompressor(
            model="gpt-5.5",
            threshold_percent=0.5,
            protect_first_n=3,
            protect_last_n=protect_last_n,
            summary_target_ratio=0.2,
            quiet_mode=True,
        )


def _mock_summary_response():
    """A successful summary call, mirroring the existing test mocks.

    Important: in the incident the summary LLM call SUCCEEDED — the bug was the
    near-zero reduction, not a summary failure.  So the reproduction must use a
    working summarizer, otherwise the fallback path masks the real behavior.
    """
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = "Compact summary of earlier turns."
    return resp


def _giant_recent_tool_result_session():
    """49-message agentic session ending in one oversized tool result.

    Total ~291K tokens, dominated by a single most-recent tool output of
    ~285K tokens (e.g. a huge file read / command dump).  This is the shape
    that token-budget tail protection keeps verbatim, so summarizing the small
    middle leaves the transcript above the window — exactly the incident.
    """
    msgs = [
        {"role": "system", "content": "SYS " * 50},
        {"role": "user", "content": "Kick off the long multi-step task."},
    ]
    # A handful of small older tool calls (the summarizable middle).
    for i in range(6):
        msgs.append(
            {
                "role": "assistant",
                "content": f"step {i}",
                "tool_calls": [
                    {
                        "id": f"old{i}",
                        "type": "function",
                        "function": {"name": "shell", "arguments": "{}"},
                    }
                ],
            }
        )
        msgs.append(
            {"role": "tool", "tool_call_id": f"old{i}", "content": (f"out{i} " * 200)}
        )
    # The most recent exchange: a single oversized tool result (~285K tokens).
    msgs.append({"role": "user", "content": "Now dump the huge build log."})
    msgs.append(
        {
            "role": "assistant",
            "content": "reading the log",
            "tool_calls": [
                {
                    "id": "GIANT",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"},
                }
            ],
        }
    )
    # ~285K tokens of tool output (~1.14M chars at ~4 chars/token).
    msgs.append({"role": "tool", "tool_call_id": "GIANT", "content": "X" * 1_140_000})
    return msgs


def _old_bulky_tool_output_session(n_pairs: int = 18, chunk: int = 6200):
    """Session whose token mass is OLD tool output (the Phase-1-prunable case).

    Each tool result is ~12K tokens; there are ``n_pairs`` of them up front,
    then a few small recent exchanges.  This is the regime where lowering
    ``protect_last_n`` lets the cheap pre-pass strip old tool output instead of
    protecting it under the message-count floor.
    """
    msgs = [
        {"role": "system", "content": "SYS " * 50},
        {"role": "user", "content": "Kick off the long task."},
    ]
    for i in range(n_pairs):
        msgs.append(
            {
                "role": "assistant",
                "content": f"old {i}",
                "tool_calls": [
                    {
                        "id": f"O{i}",
                        "type": "function",
                        "function": {"name": "shell", "arguments": "{}"},
                    }
                ],
            }
        )
        msgs.append(
            {"role": "tool", "tool_call_id": f"O{i}", "content": (f"OLD{i:05d} " * chunk)}
        )
    for i in range(5):
        msgs.append(
            {
                "role": "assistant",
                "content": f"new {i}",
                "tool_calls": [
                    {
                        "id": f"N{i}",
                        "type": "function",
                        "function": {"name": "shell", "arguments": "{}"},
                    }
                ],
            }
        )
        msgs.append(
            {"role": "tool", "tool_call_id": f"N{i}", "content": (f"new{i:03d} " * 300)}
        )
    return msgs


class TestCompressedResidualFitsWindow:
    """The compressed transcript must fit the model window after compress()."""

    def test_giant_recent_tool_result_compresses_under_window(self):
        """Production incident: one oversized recent tool result.

        Before the fix, compress() returns ~291K tokens (barely reduced) and
        the next main API call fails with 'input exceeds the context window'.
        The compressor must guarantee the result fits the model window so the
        subsequent turn does not blow up.
        """
        c = _make_compressor(protect_last_n=12)
        msgs = _giant_recent_tool_result_session()
        before = estimate_messages_tokens_rough(msgs)
        assert before > _CTX, "scenario must start over the window to be valid"

        with patch(
            "agent.context_compressor.call_llm",
            return_value=_mock_summary_response(),
        ):
            out = c.compress(msgs, current_tokens=before)

        after = estimate_messages_tokens_rough(out)
        assert after <= _CTX, (
            f"compressed transcript still exceeds the {_CTX:,}-token window "
            f"({after:,} tokens) — the next API call would fail with "
            f"'input exceeds the context window'"
        )
        # The most recent user message (the active task) must survive.
        joined = " ".join(
            m.get("content", "")
            for m in out
            if isinstance(m.get("content"), str) and m.get("role") == "user"
        )
        assert "dump the huge build log" in joined

    def test_protect_last_n_does_not_reintroduce_overflow(self):
        """Even with the historical floor (40), the result must fit the window.

        This guards against the floor silently defeating the safety guarantee:
        whatever ``protect_last_n`` is, compress() must not hand back a
        transcript larger than the window.
        """
        c = _make_compressor(protect_last_n=40)
        msgs = _giant_recent_tool_result_session()
        before = estimate_messages_tokens_rough(msgs)

        with patch(
            "agent.context_compressor.call_llm",
            return_value=_mock_summary_response(),
        ):
            out = c.compress(msgs, current_tokens=before)

        after = estimate_messages_tokens_rough(out)
        assert after <= _CTX, (
            f"compressed transcript exceeds the window with protect_last_n=40 "
            f"({after:,} > {_CTX:,})"
        )


class TestSummarizerInputBounded:
    """The summary LLM call must never receive more than the model window."""

    def test_summary_input_within_window(self):
        c = _make_compressor(protect_last_n=12)
        msgs = _giant_recent_tool_result_session()
        before = estimate_messages_tokens_rough(msgs)

        captured = {}

        def _capture(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return _mock_summary_response()

        with patch("agent.context_compressor.call_llm", side_effect=_capture):
            c.compress(msgs, current_tokens=before)

        assert "prompt" in captured, "summary LLM should have been called"
        summary_input_tokens = len(captured["prompt"]) // 4
        assert summary_input_tokens <= _CTX, (
            f"summarizer received {summary_input_tokens:,} tokens, which exceeds "
            f"the {_CTX:,}-token window of the summary model"
        )


class TestProtectLastNPrunesOldToolOutput:
    """Documents the diagnosed Phase-1 mechanism behind the config change.

    With the historical floor (protect_last_n=40) the cheap pre-pass protects
    the most recent 40 messages and barely prunes; the bulk of old tool output
    survives into the summarizer input.  Lowering the floor (12) lets the token
    budget govern, so old tool output is pruned and the summarizer input
    shrinks substantially.
    """

    def test_lower_floor_shrinks_summarizer_input(self):
        msgs = _old_bulky_tool_output_session()
        before = estimate_messages_tokens_rough(msgs)

        def _run(protect_last_n):
            c = _make_compressor(protect_last_n=protect_last_n)
            captured = {}

            def _capture(**kwargs):
                captured["prompt"] = kwargs["messages"][0]["content"]
                return _mock_summary_response()

            with patch("agent.context_compressor.call_llm", side_effect=_capture):
                c.compress([m.copy() for m in msgs], current_tokens=before)
            return len(captured.get("prompt", "")) // 4

        high_floor_input = _run(40)
        low_floor_input = _run(12)

        # Lowering the floor must meaningfully reduce what the summarizer sees.
        assert low_floor_input < high_floor_input, (
            f"expected protect_last_n=12 to shrink summarizer input below "
            f"protect_last_n=40, got {low_floor_input:,} vs {high_floor_input:,}"
        )
