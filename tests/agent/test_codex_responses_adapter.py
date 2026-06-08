from types import SimpleNamespace

import pytest

from agent.codex_responses_adapter import (
    _cap_call_id,
    _chat_messages_to_responses_input,
    _format_responses_error,
    _normalize_codex_response,
)


# ---------------------------------------------------------------------------
# _cap_call_id — the ChatGPT Codex OAuth backend mints MCP tool call_ids like
# ``codex_mcp_codex_apps_slack_slack_read_thread_call_<suffix>`` (74 chars for
# a Slack thread-read) that exceed the Responses API's 64-char
# ``input[].call_id`` limit. Echoing one back 400s the turn non-retryably, so
# we deterministically cap over-limit ids while leaving the common ≤64 path
# untouched. A function_call and its function_call_output are paired by
# call_id, so the mapping MUST be deterministic and applied to both sides.
# ---------------------------------------------------------------------------

# The exact 74-char backend-minted id from the bug report.
_LONG_MCP_CALL_ID = "codex_mcp_codex_apps_slack_slack_read_thread_call_JWmMSWefhN9eE5lwl8S33Ekk"


def test_cap_call_id_shortens_overlong_mcp_id_deterministically():
    assert len(_LONG_MCP_CALL_ID) == 74  # guards the fixture against drift

    capped = _cap_call_id(_LONG_MCP_CALL_ID)

    # Within the API limit and shaped like the existing call_ ids.
    assert len(capped) <= 64
    assert capped.startswith("call_")
    # Deterministic: same original always maps to the same capped id (the
    # pairing invariant depends on this).
    assert _cap_call_id(_LONG_MCP_CALL_ID) == capped
    # Idempotent: re-capping an already-capped (≤64) id is a no-op, so the
    # receive-chokepoint cap and the request-build cap can't diverge.
    assert _cap_call_id(capped) == capped


def test_cap_call_id_passes_short_ids_through_unchanged():
    # The common path (codex_exec_* ~31 chars, deterministic call_* ~17 chars,
    # and any boundary id of exactly 64 chars) must be byte-for-byte unchanged
    # so prompt-cache prefixes are preserved.
    for short_id in (
        "call_abc123",
        "codex_exec_0123456789abcdef0123456789",
        "x" * 64,
    ):
        assert _cap_call_id(short_id) == short_id

    # Non-string inputs pass through unchanged (no crash).
    assert _cap_call_id(None) is None  # type: ignore[arg-type]


def test_cap_call_id_pairs_function_call_and_output_with_matching_ids():
    """A function_call and its function_call_output, both built from the SAME
    74-char backend id, must serialize to the SAME ≤64 call_id — otherwise the
    Responses API rejects the request for a call_id mismatch."""
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": _LONG_MCP_CALL_ID,
                    "call_id": _LONG_MCP_CALL_ID,
                    "function": {
                        "name": "slack_read_thread",
                        "arguments": "{}",
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": _LONG_MCP_CALL_ID,
            "content": "thread contents",
        },
    ]

    items = _chat_messages_to_responses_input(messages)

    function_calls = [it for it in items if it.get("type") == "function_call"]
    function_outputs = [it for it in items if it.get("type") == "function_call_output"]
    assert len(function_calls) == 1
    assert len(function_outputs) == 1

    fc_call_id = function_calls[0]["call_id"]
    out_call_id = function_outputs[0]["call_id"]

    # Both within the limit, and — critically — identical so the pair matches.
    assert len(fc_call_id) <= 64
    assert len(out_call_id) <= 64
    assert fc_call_id == out_call_id == _cap_call_id(_LONG_MCP_CALL_ID)


def test_normalize_codex_response_drops_transient_rs_tmp_reasoning_items():
    response = SimpleNamespace(
        status="completed",
        output=[
            SimpleNamespace(
                type="reasoning",
                id="rs_tmp_123",
                encrypted_content="opaque-transient",
                summary=[],
            ),
            SimpleNamespace(
                type="reasoning",
                id="rs_456",
                encrypted_content="opaque-stable",
                summary=[SimpleNamespace(text="stable summary")],
            ),
            SimpleNamespace(
                type="message",
                role="assistant",
                status="completed",
                content=[SimpleNamespace(type="output_text", text="done")],
            ),
        ],
    )

    assistant_message, finish_reason = _normalize_codex_response(response)

    assert finish_reason == "stop"
    assert assistant_message.content == "done"
    assert assistant_message.codex_reasoning_items == [
        {
            "type": "reasoning",
            "encrypted_content": "opaque-stable",
            "id": "rs_456",
            "summary": [{"type": "summary_text", "text": "stable summary"}],
        }
    ]


def test_normalize_codex_response_treats_summary_only_reasoning_as_incomplete():
    response = SimpleNamespace(
        status="completed",
        output=[
            SimpleNamespace(
                type="reasoning",
                id="rs_tmp_789",
                encrypted_content="opaque-transient",
                summary=[SimpleNamespace(text="still thinking")],
            )
        ],
    )

    assistant_message, finish_reason = _normalize_codex_response(response)

    assert finish_reason == "incomplete"
    assert assistant_message.content == ""
    assert assistant_message.reasoning == "still thinking"
    assert assistant_message.codex_reasoning_items is None


# ---------------------------------------------------------------------------
# _format_responses_error — adapted from anomalyco/opencode#28757.
# Provider failures should surface BOTH the code (rate_limit_exceeded /
# context_length_exceeded / internal_error / server_error) and the message,
# so consumers can tell rate limits apart from context-length failures and
# both apart from generic stream drops.
# ---------------------------------------------------------------------------


def test_format_responses_error_combines_code_and_message():
    err = {"code": "rate_limit_exceeded", "message": "Slow down"}
    assert _format_responses_error(err, "failed") == "rate_limit_exceeded: Slow down"


def test_format_responses_error_message_only():
    err = {"message": "Upstream model unavailable"}
    assert _format_responses_error(err, "failed") == "Upstream model unavailable"


def test_format_responses_error_code_only_when_message_empty():
    # Some providers/proxies emit a code with an empty message body. We
    # used to fall back to ``str(error_obj)`` — a dict dump — which leaked
    # ``{'code': 'internal_error', 'message': ''}`` into chat output. Now
    # the bare code is surfaced, which is the meaningful field.
    err = {"code": "internal_error", "message": ""}
    assert _format_responses_error(err, "failed") == "internal_error"


def test_format_responses_error_code_only_when_message_missing():
    err = {"code": "server_error"}
    assert _format_responses_error(err, "failed") == "server_error"


def test_format_responses_error_attribute_style_payload():
    # SDK objects expose ``code``/``message`` as attributes rather than dict
    # keys. The helper must accept both shapes since the Responses SDK
    # returns SimpleNamespace-style objects on ``response.failed``.
    err = SimpleNamespace(code="context_length_exceeded", message="too long")
    assert _format_responses_error(err, "failed") == "context_length_exceeded: too long"


def test_format_responses_error_falls_back_to_status_when_empty():
    assert (
        _format_responses_error(None, "failed")
        == "Responses API returned status 'failed'"
    )
    assert (
        _format_responses_error(None, "cancelled")
        == "Responses API returned status 'cancelled'"
    )


def test_format_responses_error_stringifies_opaque_payload():
    # Last-resort: a provider sent something that isn't a dict and has no
    # code/message attributes. Surface its repr rather than swallow it
    # silently — at least it's visible in logs.
    assert _format_responses_error("opaque sentinel", "failed") == "opaque sentinel"


def test_format_responses_error_ignores_non_string_code_message():
    # Defensive: a malformed gateway could send numbers/objects in these
    # fields. We don't want to crash; we want a best-effort string.
    err = {"code": 500, "message": None}
    assert _format_responses_error(err, "failed") == "500"


def test_normalize_codex_response_failed_includes_code_in_error():
    """Regression: response_status == 'failed' should surface the error
    code, not just the message. Used to leak a bare 'Slow down' string
    that was indistinguishable from a generic stream truncation."""
    # ``output`` non-empty so we don't trip the "no output items" guard
    # before reaching the failed-status branch. Real failed responses
    # often DO carry a partial message item alongside the error.
    response = SimpleNamespace(
        status="failed",
        output=[
            SimpleNamespace(
                type="message",
                role="assistant",
                status="incomplete",
                content=[SimpleNamespace(type="output_text", text="partial")],
            ),
        ],
        error={"code": "rate_limit_exceeded", "message": "Slow down"},
    )
    with pytest.raises(RuntimeError, match=r"^rate_limit_exceeded: Slow down$"):
        _normalize_codex_response(response)


def test_normalize_codex_response_failed_with_message_only():
    """Backwards-compat: a failed response with only a message field
    (no code) should still surface that message verbatim."""
    response = SimpleNamespace(
        status="failed",
        output=[
            SimpleNamespace(
                type="message",
                role="assistant",
                status="incomplete",
                content=[SimpleNamespace(type="output_text", text="partial")],
            ),
        ],
        error={"message": "model error"},
    )
    with pytest.raises(RuntimeError, match=r"^model error$"):
        _normalize_codex_response(response)
