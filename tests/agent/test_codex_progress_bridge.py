"""Regression test for the Codex app-server tool-progress bridge.

The codex_app_server runtime runs tools INSIDE the codex subprocess, so the
normal tool_executor.py ``tool_progress_callback`` hooks never fire on that
path.  ``run_codex_app_server_turn`` must therefore install an ``on_event``
hook on the ``CodexAppServerSession`` that translates codex ``item/started``
notifications into ``agent.tool_progress_callback("tool.started", ...)`` calls.

Without this bridge the per-tool-call progress feed silently disappears on
messaging platforms (e.g. Slack) for codex-runtime agents while the heartbeat
status keeps working — exactly the regression this test pins.
"""

import sys
import types

import pytest

import agent.codex_runtime as codex_runtime


def _make_agent(recorder):
    agent = types.SimpleNamespace()
    agent.session_cwd = "/tmp"
    agent.tool_progress_callback = recorder
    # Attributes the post-turn bookkeeping reads.
    agent._iters_since_skill = 0
    agent._skill_nudge_interval = 0
    agent.valid_tool_names = set()

    def _noop_sync(**_kwargs):
        return None

    agent._sync_external_memory_for_turn = _noop_sync
    return agent


def _run_turn_and_capture_on_event(monkeypatch, agent):
    """Invoke run_codex_app_server_turn and return the on_event hook it wired."""
    captured = {}

    class _FakeSession:
        def __init__(self, *, cwd, approval_callback=None, on_event=None, **_kw):
            # Capture the bridge at construction time — that is the whole point
            # of this test. We deliberately make run_turn raise afterwards so
            # the function returns early (line ~110) without exercising the
            # heavy post-turn bookkeeping, which is irrelevant to the bridge.
            captured["on_event"] = on_event

        def run_turn(self, *, user_input):
            raise RuntimeError("stop after on_event capture")

        def close(self):
            pass

    fake_mod = types.ModuleType("agent.transports.codex_app_server_session")
    fake_mod.CodexAppServerSession = _FakeSession
    monkeypatch.setitem(
        sys.modules, "agent.transports.codex_app_server_session", fake_mod
    )

    codex_runtime.run_codex_app_server_turn(
        agent,
        user_message="hi",
        original_user_message="hi",
        messages=[],
        effective_task_id="t1",
    )
    return captured.get("on_event")


def test_codex_runtime_wires_progress_on_event(monkeypatch):
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))

    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    assert callable(on_event), "run_codex_app_server_turn must wire an on_event hook"


@pytest.mark.parametrize(
    "item,expected_name",
    [
        (
            {"type": "commandExecution", "command": "/bin/zsh -lc \"sed -n '1,5p' /x/y.py\""},
            "read_file",
        ),
        ({"type": "commandExecution", "command": "rg -n LEOS ."}, "search_files"),
        ({"type": "commandExecution", "command": "git status"}, "terminal"),
        ({"type": "fileChange", "path": "/x/y.py"}, "apply_patch"),
        (
            {"type": "mcpToolCall", "server": "wiki", "tool": "search"},
            "mcp.wiki.search",
        ),
        ({"type": "dynamicToolCall", "name": "skill_view"}, "skill_view"),
    ],
)
def test_item_started_fires_tool_progress(monkeypatch, item, expected_name):
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    on_event({"method": "item/started", "params": {"item": item}})

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "tool.started"
    assert kwargs.get("tool_name") == expected_name


def test_command_preview_strips_shell_wrapper(monkeypatch):
    """commandExecution previews drop the /bin/zsh -lc wrapper and classify."""
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    on_event({
        "method": "item/started",
        "params": {"item": {
            "type": "commandExecution",
            "command": "/bin/zsh -lc \"sed -n '1,220p' /Users/leo/wiki/notes.md\"",
        }},
    })

    assert len(calls) == 1
    _args, kwargs = calls[0]
    assert kwargs.get("tool_name") == "read_file"
    preview = kwargs.get("preview") or ""
    assert "/Users/leo/wiki/notes.md" in preview
    assert "zsh" not in preview and "/bin" not in preview


def test_non_item_started_events_ignored(monkeypatch):
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    # Wrong method, and an item/started with no recognizable item type.
    on_event({"method": "turn/completed", "params": {}})
    on_event({"method": "item/started", "params": {"item": {"type": "reasoning"}}})

    assert calls == []


def test_progress_bridge_is_fail_open(monkeypatch):
    """A raising tool_progress_callback must not propagate out of on_event."""

    def _boom(*_a, **_k):
        raise RuntimeError("display blew up")

    agent = _make_agent(_boom)
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    # Must swallow the error — a display failure may never break a codex turn.
    on_event({"method": "item/started", "params": {"item": {"type": "commandExecution"}}})
