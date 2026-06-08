"""PARITY GATE for the contract-gate auto-continue de-fork (Stage 3).

This test drives the **REAL** ``GatewayRunner._run_agent`` resume block — the
``if _is_resume_pending or _has_fresh_tool_tail:`` guard in ``gateway/run.py``
that prepends the ``[System note: Your previous turn ...]`` auto-continue
message — NOT a hand-copied mirror of that logic.

How the real path is driven (the smallest real seam):
  * ``run_agent.AIAgent`` is patched so the agent's ``run_conversation`` simply
    CAPTURES the (already-mutated) message that the real ``_run_agent`` body
    feeds into it.  Everything UP TO that call — the freshness gate, the
    ``_is_resume_pending`` / ``_has_fresh_tool_tail`` computation, the real
    ``_build_gateway_agent_history`` conversion, the contract-gate decision and
    the System-note prepend — runs as the actual production code object.
  * The heavy per-turn collaborators (model/runtime/reasoning resolution, agent
    cache signature, session-env, native-image consumption) are stubbed to
    cheap no-ops so the body reaches the resume block without standing up a real
    provider.  They do NOT touch the block under test.

Contract: with the contract gate OFF (the shipped default — config key
``contract_gate.enabled`` absent / falsy, and the relocated plugin not
registered), the auto-continue System note MUST be prepended for BOTH the
resume-pending and the fresh-tool-tail case, and NO exception may escape.

This file is the de-fork GATE: it must be byte-identically green BEFORE the
SC1/SC2 move (helper call in run.py) and AFTER it (plugin-hook call in run.py).
"""

import asyncio
import time
from datetime import datetime
from unittest.mock import patch

import pytest

from gateway.config import Platform
from gateway.session import SessionEntry, SessionSource
from tests.gateway.restart_test_helpers import make_restart_runner


RESUME_NOTE_MARK = "previous turn in this session was interrupted"
TOOL_TAIL_NOTE_MARK = "haven't responded to yet"


def _seed_runner_for_run_agent(runner):
    """Give the bare ``make_restart_runner`` runner the per-turn attributes and
    collaborator stubs ``_run_agent`` reads BEFORE the resume block.

    None of these touch the resume-note logic — they exist only so the real
    method body can reach line ~18354 (the guarded contract-gate block) without
    constructing a real provider/agent.
    """
    runner._ephemeral_system_prompt = None
    runner._service_tier = None
    runner._session_db = None
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._prefill_messages = None
    runner._fallback_model = None
    runner._pending_skills_reload_notes = {}
    runner._pending_native_image_paths = {}

    runner._resolve_session_agent_runtime = lambda **k: ("m", {"provider": "p"})
    runner._resolve_turn_agent_config = lambda *a, **k: {
        "model": "m", "runtime": {}, "request_overrides": {},
    }
    runner._resolve_session_reasoning_config = lambda **k: None
    runner._load_service_tier = lambda: None
    runner._agent_config_signature = lambda *a, **k: "sig"
    runner._extract_cache_busting_config = lambda *a, **k: ()
    runner._thread_metadata_for_source = lambda *a, **k: None
    runner._init_cached_agent_for_turn = lambda *a, **k: None
    runner._enforce_agent_cache_cap = lambda *a, **k: None
    runner._consume_pending_native_image_paths = lambda *a, **k: []
    runner._set_session_env = lambda *a, **k: []
    runner._clear_session_env = lambda *a, **k: None


def _drive_run_agent(history, resume_entry):
    """Run the REAL ``_run_agent`` and return the message handed to the agent.

    ``run_conversation`` is replaced with a capture stub; the returned value is
    the message string AFTER the production resume block has (or has not)
    prepended the auto-continue System note.
    """
    runner, _adapter = make_restart_runner()
    _seed_runner_for_run_agent(runner)

    source = SessionSource(
        platform=Platform.TELEGRAM, chat_id="c1", chat_type="dm", user_id="u1"
    )
    session_key = "agent:main:telegram:dm:c1"
    runner.session_store._entries = (
        {session_key: resume_entry} if resume_entry is not None else {}
    )

    captured: dict = {}

    class _CaptureAgent:
        def __init__(self, *a, **k):
            pass

        def run_conversation(self, msg, **kw):
            captured["message"] = msg
            return {
                "final_response": "ok",
                "messages": [],
                "api_calls": 0,
                "completed": True,
            }

    with patch("run_agent.AIAgent", _CaptureAgent):
        coro = runner._run_agent(
            message="USERMSG",
            context_prompt="",
            history=history,
            source=source,
            session_id="sid",
            session_key=session_key,
        )
        asyncio.run(coro)

    assert "message" in captured, (
        "the real _run_agent never reached agent.run_conversation — the seam "
        "broke; this parity test is no longer driving the production path"
    )
    return captured["message"]


def _fresh_resume_entry(reason="restart_timeout"):
    now = datetime.now()
    return SessionEntry(
        session_key="agent:main:telegram:dm:c1",
        session_id="sid",
        created_at=now,
        updated_at=now,
        resume_pending=True,
        resume_reason=reason,
        last_resume_marked_at=now,
    )


# ---------------------------------------------------------------------------
# THE GATE: default (gate OFF) → auto-continue note IS prepended, no exception
# ---------------------------------------------------------------------------


def test_resume_pending_prepends_system_note_with_gate_off():
    """Flag OFF (default) → the resume-pending System note is prepended.

    Exercises the REAL ``_is_resume_pending`` branch of ``_run_agent``.
    """
    history = [
        {"role": "assistant", "content": "in progress", "timestamp": time.time()},
    ]
    msg = _drive_run_agent(history, _fresh_resume_entry())

    assert msg.startswith("[System note:"), msg
    assert RESUME_NOTE_MARK in msg
    assert "gateway restart" in msg
    assert msg.endswith("USERMSG")


def test_fresh_tool_tail_prepends_system_note_with_gate_off():
    """Flag OFF (default) → the fresh-tool-tail System note is prepended.

    Exercises the REAL ``_has_fresh_tool_tail`` branch of ``_run_agent``,
    including the real ``_build_gateway_agent_history`` conversion that decides
    the last role is ``tool``.
    """
    history = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "function": {"name": "x", "arguments": "{}"}},
            ],
            "timestamp": time.time() - 1,
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "result",
            "timestamp": time.time(),
        },
    ]
    msg = _drive_run_agent(history, resume_entry=None)

    assert msg.startswith("[System note:"), msg
    assert TOOL_TAIL_NOTE_MARK in msg
    assert msg.endswith("USERMSG")


def test_no_resume_no_tool_tail_leaves_message_untouched():
    """Sanity: when neither resume condition holds, the message is passed
    through verbatim — the gate/guard must not fire spuriously."""
    history = [
        {"role": "user", "content": "hi", "timestamp": time.time() - 2},
        {"role": "assistant", "content": "hey", "timestamp": time.time() - 1},
    ]
    msg = _drive_run_agent(history, resume_entry=None)
    assert msg == "USERMSG"


def test_resume_block_raises_no_exception_either_branch():
    """Belt-and-braces: driving BOTH branches must not raise (the resume block
    and its contract-gate consult are fail-open by contract)."""
    # resume-pending branch
    _drive_run_agent(
        [{"role": "assistant", "content": "x", "timestamp": time.time()}],
        _fresh_resume_entry(),
    )
    # fresh-tool-tail branch
    _drive_run_agent(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "c1", "function": {"name": "x", "arguments": "{}"}},
                ],
                "timestamp": time.time() - 1,
            },
            {
                "role": "tool",
                "tool_call_id": "c1",
                "content": "result",
                "timestamp": time.time(),
            },
        ],
        resume_entry=None,
    )
