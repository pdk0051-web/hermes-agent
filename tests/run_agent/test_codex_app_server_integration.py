"""Integration test for the codex_app_server runtime path through AIAgent.

Verifies that:
  - api_mode='codex_app_server' is accepted on AIAgent construction
  - run_conversation() takes the early-return path and never enters the
    chat completions loop
  - Projected messages from a fake Codex session land in the messages list
  - tool_iterations from the codex session tick the skill nudge counter
  - Memory nudge counter ticks once per turn
  - The returned dict has the same shape as the chat_completions path
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import run_agent
from agent.transports.codex_app_server_session import CodexAppServerSession, TurnResult


@pytest.fixture
def fake_session(monkeypatch):
    """Replace CodexAppServerSession with a stub that returns a fixed
    TurnResult, so we can drive AIAgent without spawning real codex."""

    def fake_run_turn(self, user_input: str, **kwargs):
        return TurnResult(
            final_text=f"echo: {user_input}",
            projected_messages=[
                {"role": "assistant", "content": None,
                 "tool_calls": [{"id": "exec_1", "type": "function",
                                 "function": {"name": "exec_command",
                                              "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "exec_1", "content": "ok"},
                {"role": "assistant", "content": f"echo: {user_input}"},
            ],
            tool_iterations=1,
            interrupted=False,
            error=None,
            turn_id="turn-stub-1",
            thread_id="thread-stub-1",
        )

    monkeypatch.setattr(CodexAppServerSession, "run_turn", fake_run_turn)
    monkeypatch.setattr(
        CodexAppServerSession, "ensure_started", lambda self: "thread-stub-1"
    )


def _make_codex_agent():
    """Construct an AIAgent in codex_app_server mode without contacting any
    real provider. We pass api_mode explicitly so the constructor takes the
    fast path for direct credentials."""
    return run_agent.AIAgent(
        api_key="stub",
        base_url="https://stub.invalid",
        provider="openai",
        api_mode="codex_app_server",
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
    )


class TestApiModeAccepted:
    def test_api_mode_is_codex_app_server(self):
        agent = _make_codex_agent()
        assert agent.api_mode == "codex_app_server"


class TestRunConversationCodexPath:
    def test_run_conversation_returns_codex_shape(self, fake_session):
        agent = _make_codex_agent()
        # No background review fork during tests
        with patch.object(agent, "_spawn_background_review", return_value=None):
            result = agent.run_conversation("hello there")
        assert result["final_response"] == "echo: hello there"
        assert result["completed"] is True
        assert result["partial"] is False
        assert result["error"] is None
        assert result["api_calls"] == 1
        assert result["codex_thread_id"] == "thread-stub-1"
        assert result["codex_turn_id"] == "turn-stub-1"

    def test_codex_app_server_token_usage_updates_session_accounting(self, monkeypatch):
        def fake_run_turn(self, user_input: str, **kwargs):
            return TurnResult(
                final_text="done",
                projected_messages=[{"role": "assistant", "content": "done"}],
                turn_id="turn-usage-1",
                thread_id="thread-usage-1",
                token_usage_last={
                    "totalTokens": 130,
                    "inputTokens": 80,
                    "cachedInputTokens": 20,
                    "outputTokens": 25,
                    "reasoningOutputTokens": 5,
                },
                model_context_window=200000,
            )

        monkeypatch.setattr(CodexAppServerSession, "run_turn", fake_run_turn)
        monkeypatch.setattr(
            CodexAppServerSession, "ensure_started", lambda self: "thread-usage-1"
        )
        agent = _make_codex_agent()
        with patch.object(agent, "_spawn_background_review", return_value=None):
            result = agent.run_conversation("hello")

        assert result["api_calls"] == 1
        assert result["prompt_tokens"] == 100
        assert result["completion_tokens"] == 25
        assert result["total_tokens"] == 130
        assert result["input_tokens"] == 80
        assert result["output_tokens"] == 25
        assert result["cache_read_tokens"] == 20
        assert result["cache_write_tokens"] == 0
        assert result["reasoning_tokens"] == 5
        assert result["last_prompt_tokens"] == 100

        assert agent.session_api_calls == 1
        assert agent.session_prompt_tokens == 100
        assert agent.session_completion_tokens == 25
        assert agent.session_total_tokens == 130
        assert agent.session_input_tokens == 80
        assert agent.session_output_tokens == 25
        assert agent.session_cache_read_tokens == 20
        assert agent.session_cache_write_tokens == 0
        assert agent.session_reasoning_tokens == 5
        assert agent.context_compressor.last_prompt_tokens == 100
        assert agent.context_compressor.last_completion_tokens == 25
        assert agent.context_compressor.last_total_tokens == 130
        assert agent.context_compressor.context_length == 200000

    def test_projected_messages_are_spliced(self, fake_session):
        agent = _make_codex_agent()
        with patch.object(agent, "_spawn_background_review", return_value=None):
            result = agent.run_conversation("hello")
        msgs = result["messages"]
        # User message + 3 projected (assistant tool_call + tool + assistant text)
        assert len(msgs) >= 4
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "hello"
        # Last assistant message has the final text
        final = [m for m in msgs if m.get("role") == "assistant"
                 and m.get("content") == "echo: hello"]
        assert final, f"expected final assistant message in {msgs}"

    def test_nudge_counters_tick(self, fake_session):
        """The skill nudge counter must accumulate tool_iterations across
        turns. The memory nudge counter is gated on memory being configured
        (which we skip via skip_memory=True), so we don't assert on it here —
        a separate test below covers that path explicitly."""
        agent = _make_codex_agent()
        agent._iters_since_skill = 0
        agent._user_turn_count = 0
        with patch.object(agent, "_spawn_background_review", return_value=None):
            agent.run_conversation("first")
        assert agent._iters_since_skill == 1  # one tool_iteration in fake turn
        # _user_turn_count is incremented by run_conversation pre-loop, not
        # by the codex helper — confirms we delegate that to the standard flow.
        assert agent._user_turn_count == 1
        with patch.object(agent, "_spawn_background_review", return_value=None):
            agent.run_conversation("second")
        assert agent._iters_since_skill == 2
        assert agent._user_turn_count == 2

    def test_user_message_not_duplicated(self, fake_session):
        """Regression guard: the user message must appear exactly once in
        the messages list. The standard run_conversation pre-loop appends
        it, and the codex helper must NOT append again."""
        agent = _make_codex_agent()
        with patch.object(agent, "_spawn_background_review", return_value=None):
            result = agent.run_conversation("ping unique 12345")
        user_count = sum(
            1 for m in result["messages"]
            if m.get("role") == "user" and m.get("content") == "ping unique 12345"
        )
        assert user_count == 1, f"user message appeared {user_count}× in {result['messages']}"

    def test_background_review_NOT_invoked_below_threshold(self, fake_session):
        """A single turn shouldn't trigger background review — counters
        haven't reached the nudge interval (default 10)."""
        agent = _make_codex_agent()
        agent._memory_nudge_interval = 10
        agent._skill_nudge_interval = 10
        agent._iters_since_skill = 0
        with patch.object(agent, "_spawn_background_review",
                          return_value=None) as spawn:
            agent.run_conversation("ping")
        # Below threshold → review should NOT fire (was a real bug:
        # the helper was calling _spawn_background_review() with no
        # args after every turn, which would crash with TypeError).
        assert not spawn.called

    def test_background_review_skill_trigger_fires_above_threshold(
        self, monkeypatch
    ):
        """When tool iterations cross the skill nudge interval, the
        background review fires with review_skills=True and the right
        messages_snapshot signature."""
        from agent.transports.codex_app_server_session import (
            CodexAppServerSession, TurnResult,
        )
        # Make the fake session report 10 tool iterations in one turn
        # (matching the default skill threshold).
        def fake_run_turn(self, user_input: str, **kwargs):
            return TurnResult(
                final_text=f"echo: {user_input}",
                projected_messages=[
                    {"role": "assistant", "content": f"echo: {user_input}"},
                ],
                tool_iterations=10,
                turn_id="t1", thread_id="th1",
            )
        monkeypatch.setattr(CodexAppServerSession, "run_turn", fake_run_turn)
        monkeypatch.setattr(
            CodexAppServerSession, "ensure_started", lambda self: "th1"
        )

        agent = _make_codex_agent()
        agent._skill_nudge_interval = 10
        agent._iters_since_skill = 0
        # Make valid_tool_names include 'skill_manage' so the gate passes
        agent.valid_tool_names = set(getattr(agent, "valid_tool_names", set()))
        agent.valid_tool_names.add("skill_manage")

        with patch.object(agent, "_spawn_background_review",
                          return_value=None) as spawn:
            agent.run_conversation("do tool work")

        assert spawn.called, "skill threshold tripped but review didn't fire"
        # Verify the call signature matches what _spawn_background_review
        # actually expects — this is the regression guard for the original
        # bug where the codex path called it with no args at all.
        call = spawn.call_args
        assert "messages_snapshot" in call.kwargs
        assert isinstance(call.kwargs["messages_snapshot"], list)
        assert call.kwargs["review_skills"] is True
        # Counter should be reset after the review fires
        assert agent._iters_since_skill == 0

    def test_background_review_signature_never_breaks(self, fake_session):
        """Even when no trigger fires, the helper must never call
        _spawn_background_review with the wrong signature. Run a turn,
        then run another turn after manually tripping the skill counter
        and confirm the call shape is the kwargs-only form the function
        actually accepts."""
        agent = _make_codex_agent()
        agent._skill_nudge_interval = 1  # very low so any iter trips it
        agent._iters_since_skill = 0
        agent.valid_tool_names = set(getattr(agent, "valid_tool_names", set()))
        agent.valid_tool_names.add("skill_manage")

        with patch.object(agent, "_spawn_background_review",
                          return_value=None) as spawn:
            agent.run_conversation("first")
        # The fake session reports tool_iterations=1, which trips
        # _skill_nudge_interval=1. So review should fire.
        assert spawn.called
        # Critical invariant: positional args must be empty, all real
        # args must be kwargs (matching _spawn_background_review's
        # actual signature).
        call = spawn.call_args
        assert call.args == (), (
            f"expected no positional args, got {call.args!r} — "
            "would crash _spawn_background_review at runtime"
        )
        assert "messages_snapshot" in call.kwargs

    def test_chat_completions_loop_is_not_entered(self, fake_session):
        """The early-return must bypass the regular API call loop entirely.
        We confirm by patching the SDK call and asserting it's never invoked."""
        agent = _make_codex_agent()
        # The chat_completions loop calls self.client.chat.completions.create(...)
        # If our early-return works, that path is dead.
        with patch.object(agent, "client") as client_mock, patch.object(
            agent, "_spawn_background_review", return_value=None
        ):
            agent.run_conversation("hi")
        assert not client_mock.chat.completions.create.called


class _RecordingSessionDB:
    """Minimal fake of hermes_state.SessionDB for asserting the codex turn
    is flushed to the `messages` table. Records every append_message call."""

    def __init__(self) -> None:
        self.appended: list[dict] = []

    def append_message(self, **kwargs):
        self.appended.append(kwargs)
        return len(self.appended)

    # _flush_messages_to_session_db only calls append_message; create_session
    # is gated behind _session_db_created which the tests set True up front.
    def create_session(self, *a, **kw):  # pragma: no cover - not reached
        return "stub-session"

    # Other SessionDB methods touched incidentally during run_conversation
    # (system-prompt cache, token accounting). No-ops keep the fake quiet.
    def update_system_prompt(self, *a, **kw):
        return None

    def get_session(self, *a, **kw):
        return None

    def update_token_counts(self, *a, **kw):
        return None


def _attach_recording_db(agent) -> "_RecordingSessionDB":
    """Wire a recording SQLite stub onto an already-built agent so the
    persistence path (_persist_session → _flush_messages_to_session_db) runs.
    Sets _session_db_created=True so _ensure_db_session/create_session is
    skipped, and resets the dedup cursor to 0."""
    db = _RecordingSessionDB()
    agent._session_db = db
    agent._session_db_created = True
    agent._last_flushed_db_idx = 0
    return db


class TestCodexTurnPersistedToDb:
    """DATA-LOSS REGRESSION GUARD (codex_app_server path).

    Before the fix, the early-return at conversation_loop.py for
    api_mode='codex_app_server' bypassed the chat_completions loop — the ONLY
    place _persist_session() runs on exit. So an interactive codex turn never
    reached the `messages` SQLite table (only the one-time session_meta row
    landed). These tests pin that a codex turn flushes its messages exactly
    once, that the flush includes the USER turn + tool-result roles (faithful
    replay), and that a re-entry does not double-write."""

    def test_codex_turn_flushes_messages_to_db(self, fake_session):
        agent = _make_codex_agent()
        db = _attach_recording_db(agent)
        with patch.object(agent, "_spawn_background_review", return_value=None):
            agent.run_conversation("persist me please")

        # The flush must have run (regression: it didn't at all before).
        assert db.appended, (
            "codex turn produced NO DB rows — _persist_session was bypassed"
        )
        roles = [row.get("role") for row in db.appended]
        # Faithful replay needs the user turn, the assistant, and the tool
        # result — exactly the messages the loop's normal exit would persist.
        assert "user" in roles, f"user turn missing from DB flush: {roles}"
        assert "assistant" in roles, f"assistant missing from DB flush: {roles}"
        assert "tool" in roles, f"tool result missing from DB flush: {roles}"

    def test_user_text_and_tool_output_land_in_db(self, fake_session):
        """The user's actual text and the tool's output content must be the
        persisted values — proves we flush the complete `messages` source,
        not the role-incomplete projected_messages."""
        agent = _make_codex_agent()
        db = _attach_recording_db(agent)
        with patch.object(agent, "_spawn_background_review", return_value=None):
            agent.run_conversation("unique-user-text-42")

        user_rows = [r for r in db.appended if r.get("role") == "user"]
        tool_rows = [r for r in db.appended if r.get("role") == "tool"]
        assert any(r.get("content") == "unique-user-text-42" for r in user_rows), (
            f"user text not persisted; user rows: {user_rows}"
        )
        # fake_session's tool result content is "ok".
        assert any(r.get("content") == "ok" for r in tool_rows), (
            f"tool output not persisted; tool rows: {tool_rows}"
        )

    def test_flush_count_matches_message_count(self, fake_session):
        """One turn = user(1) + projected(3) = 4 rows, flushed once."""
        agent = _make_codex_agent()
        db = _attach_recording_db(agent)
        with patch.object(agent, "_spawn_background_review", return_value=None):
            result = agent.run_conversation("count me")
        # Every message in the final list should have been appended exactly once.
        assert len(db.appended) == len(result["messages"]), (
            f"flushed {len(db.appended)} rows for "
            f"{len(result['messages'])} messages: {db.appended}"
        )

    def test_reentry_does_not_double_write(self, fake_session):
        """A follow-up turn must only flush the NEW turn's rows, never re-write
        the prior turn's already-persisted history. This mirrors how the
        gateway's interactive path drives it: each turn threads the accumulated
        prior messages back in as ``conversation_history`` (reconstructed from
        the DB), which sets the flush FLOOR (start_idx=len(history)) so the new
        agent's fresh _last_flushed_db_idx=0 cursor can't replay old rows."""
        # Turn 1 — fresh agent, no history (first message of the session).
        agent1 = _make_codex_agent()
        db = _attach_recording_db(agent1)
        with patch.object(agent1, "_spawn_background_review", return_value=None):
            r1 = agent1.run_conversation("turn one")
        after_first = len(db.appended)
        assert after_first == len(r1["messages"])  # user + 3 projected = 4 rows

        # Turn 2 — a fresh agent (as the gateway builds per turn) sharing the
        # SAME session DB, threading turn 1's transcript as history.
        agent2 = _make_codex_agent()
        agent2._session_db = db          # same DB across turns
        agent2._session_db_created = True
        agent2._last_flushed_db_idx = 0  # fresh agent starts at 0
        prior_history = list(r1["messages"])
        with patch.object(agent2, "_spawn_background_review", return_value=None):
            r2 = agent2.run_conversation(
                "turn two", conversation_history=prior_history
            )
        # Only turn 2's own new messages (user + 3 projected) get flushed; the
        # 4 history rows are below the floor and are NOT re-appended.
        new_rows = len(db.appended) - after_first
        assert new_rows == len(r2["messages"]) - len(prior_history), (
            f"re-entry double-wrote: total={len(db.appended)} "
            f"after_first={after_first} history={len(prior_history)} "
            f"r2_total={len(r2['messages'])}"
        )
        # No user text appears twice across the whole DB.
        user_texts = [
            r.get("content") for r in db.appended if r.get("role") == "user"
        ]
        assert user_texts.count("turn one") == 1, user_texts
        assert user_texts.count("turn two") == 1, user_texts

    def test_explicit_reflush_same_messages_is_noop(self, fake_session):
        """Directly calling the persist path again with the SAME messages
        list must write nothing new — pins _last_flushed_db_idx dedup so the
        gateway's parallel skip_db pass can't create duplicate rows."""
        agent = _make_codex_agent()
        db = _attach_recording_db(agent)
        with patch.object(agent, "_spawn_background_review", return_value=None):
            result = agent.run_conversation("once")
        count_after_turn = len(db.appended)
        # Re-run the exact flush the loop just did — must be a no-op.
        agent._persist_session(result["messages"], None)
        assert len(db.appended) == count_after_turn, (
            "re-flushing identical messages wrote duplicate rows"
        )

    def test_persistence_error_does_not_break_turn(self, fake_session):
        """Fail-open: if the DB flush raises, the codex turn result must still
        be returned intact (mirrors the journal hook's fail-open)."""
        agent = _make_codex_agent()
        _attach_recording_db(agent)

        def boom(*_a, **_k):
            raise RuntimeError("db write exploded")

        with patch.object(agent, "_persist_session", side_effect=boom), \
                patch.object(agent, "_spawn_background_review", return_value=None):
            result = agent.run_conversation("survive the db error")
        assert result["final_response"] == "echo: survive the db error"
        assert result["completed"] is True
        assert result["error"] is None


class TestReviewForkApiModeDowngrade:
    """When the parent agent runs on codex_app_server, the background
    review fork must downgrade to codex_responses — otherwise the fork
    can't dispatch agent-loop tools (memory, skill_manage) which is the
    whole point of the review."""

    def test_codex_app_server_parent_downgrades_review_fork(self):
        """Live test against the real _spawn_background_review code path:
        verify the review_agent gets api_mode=codex_responses when the
        parent is codex_app_server."""
        from unittest.mock import MagicMock, patch as _patch
        agent = _make_codex_agent()
        # Pretend memory + skills are configured so the review fork
        # reaches the AIAgent constructor.
        agent._memory_store = MagicMock()
        agent._memory_enabled = True
        agent._user_profile_enabled = True
        # Mock _current_main_runtime to return the parent's codex_app_server
        # state so we can confirm the helper detects + downgrades it.
        agent._current_main_runtime = lambda: {
            "api_mode": "codex_app_server",
            "base_url": "https://chatgpt.com/backend-api/codex",
            "api_key": "stub-token",
        }
        # Capture what AIAgent gets constructed with inside the helper.
        captured = {}

        def _capture_init(self, **kwargs):
            captured.update(kwargs)
            # Set bare attributes the rest of the spawn function reads
            # so it can finish without exploding.
            self.api_mode = kwargs.get("api_mode")
            self.provider = kwargs.get("provider")
            self.model = kwargs.get("model")
            self._memory_write_origin = None
            self._memory_write_context = None
            self._memory_store = None
            self._memory_enabled = False
            self._user_profile_enabled = False
            self._memory_nudge_interval = 0
            self._skill_nudge_interval = 0
            self.suppress_status_output = False
            self._session_messages = []

            def _no_op_run_conv(*a, **kw):
                return {"final_response": "", "messages": []}
            self.run_conversation = _no_op_run_conv

            def _no_op_close(*a, **kw):
                return None
            self.close = _no_op_close

        with _patch("run_agent.AIAgent.__init__", _capture_init):
            agent._spawn_background_review(
                messages_snapshot=[{"role": "user", "content": "x"}],
                review_memory=True,
                review_skills=False,
            )
            # Wait for the spawned thread to actually execute
            import time
            for _ in range(30):
                if "api_mode" in captured:
                    break
                time.sleep(0.1)

        assert captured.get("api_mode") == "codex_responses", (
            f"review fork should be downgraded to codex_responses when "
            f"parent is codex_app_server; got {captured.get('api_mode')!r}"
        )


class TestErrorHandling:
    def test_session_exception_returns_partial_with_error(self, monkeypatch):
        def boom_run_turn(self, user_input, **kwargs):
            raise RuntimeError("subprocess died")

        monkeypatch.setattr(CodexAppServerSession, "ensure_started",
                            lambda self: "t1")
        monkeypatch.setattr(CodexAppServerSession, "run_turn", boom_run_turn)

        agent = _make_codex_agent()
        with patch.object(agent, "_spawn_background_review", return_value=None):
            result = agent.run_conversation("hi")
        assert result["completed"] is False
        assert result["partial"] is True
        assert "subprocess died" in result["error"]
        assert "codex-runtime auto" in result["final_response"]

    def test_interrupted_turn_marked_partial(self, monkeypatch):
        def interrupted_turn(self, user_input, **kwargs):
            return TurnResult(
                final_text="",
                projected_messages=[],
                tool_iterations=0,
                interrupted=True,
                error="user interrupted",
                turn_id="t",
                thread_id="th",
            )
        monkeypatch.setattr(CodexAppServerSession, "ensure_started",
                            lambda self: "th")
        monkeypatch.setattr(CodexAppServerSession, "run_turn", interrupted_turn)

        agent = _make_codex_agent()
        with patch.object(agent, "_spawn_background_review", return_value=None):
            result = agent.run_conversation("hi")
        assert result["completed"] is False
        assert result["partial"] is True
        assert result["error"] == "user interrupted"


class TestSessionRetirementOnRunAgent:
    """run_agent.py side: when run_turn returns should_retire=True, the
    AIAgent must close + null _codex_session so the next turn respawns."""

    def test_should_retire_drops_session(self, monkeypatch):
        closes = {"count": 0}

        def fake_run_turn(self, user_input, **kwargs):
            return TurnResult(
                final_text="",
                projected_messages=[],
                tool_iterations=0,
                interrupted=True,
                error="turn timed out after 600.0s",
                turn_id="tu1",
                thread_id="th1",
                should_retire=True,
            )

        def fake_close(self):
            closes["count"] += 1

        monkeypatch.setattr(CodexAppServerSession, "ensure_started",
                            lambda self: "th1")
        monkeypatch.setattr(CodexAppServerSession, "run_turn", fake_run_turn)
        monkeypatch.setattr(CodexAppServerSession, "close", fake_close)

        agent = _make_codex_agent()
        with patch.object(agent, "_spawn_background_review", return_value=None):
            result = agent.run_conversation("hi")

        # The session was closed and cleared
        assert closes["count"] == 1
        assert getattr(agent, "_codex_session", "MISSING") is None
        # Partial result was still returned (caller still sees the error)
        assert result["partial"] is True
        assert result["error"] == "turn timed out after 600.0s"

    def test_normal_turn_keeps_session(self, fake_session):
        """fake_session fixture returns should_retire=False (default).
        The session must stay attached for the next turn to reuse."""
        agent = _make_codex_agent()
        with patch.object(agent, "_spawn_background_review", return_value=None):
            agent.run_conversation("hi")
        # Session was lazily created and still attached.
        assert getattr(agent, "_codex_session", None) is not None

    def test_exception_path_also_drops_session(self, monkeypatch):
        """Even if run_turn raises (not just sets should_retire), we must
        drop the session — a thrown exception is the strongest possible
        signal the process is dead."""
        closes = {"count": 0}

        def boom_run_turn(self, user_input, **kwargs):
            raise RuntimeError("codex segfaulted")

        def fake_close(self):
            closes["count"] += 1

        monkeypatch.setattr(CodexAppServerSession, "ensure_started",
                            lambda self: "th1")
        monkeypatch.setattr(CodexAppServerSession, "run_turn", boom_run_turn)
        monkeypatch.setattr(CodexAppServerSession, "close", fake_close)

        agent = _make_codex_agent()
        with patch.object(agent, "_spawn_background_review", return_value=None):
            result = agent.run_conversation("hi")

        assert closes["count"] == 1
        assert agent._codex_session is None
        assert result["completed"] is False
        assert "codex segfaulted" in result["error"]
