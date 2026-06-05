"""Regression test for the Codex app-server tool-progress bridge.

The codex_app_server runtime runs tools INSIDE the codex subprocess, so the
normal tool_executor.py ``tool_progress_callback`` hooks never fire on that
path.  ``run_codex_app_server_turn`` must therefore install an ``on_event``
hook on the ``CodexAppServerSession`` that translates codex ``item/started``
notifications into ``agent.tool_progress_callback("tool.started", ...)`` calls.

Without this bridge the progress feed silently disappears on messaging
platforms (e.g. Slack) for codex-runtime agents while the heartbeat status
keeps working — exactly the regression this test pins.

The bridge does NOT echo every raw tool.  It collapses runs of similar
activity into a single MEANINGFUL UNIT — each item maps to a (emoji, label)
"phase", LEOS domain first (법·헌법 확인 / 계약 작업 / 원장 기록 …) else a
generic dev phase (코드 살펴보는 중 / 테스트 / 빌드 …) — and emits ONLY when the
phase changes, while a per-turn step counter advances on every item.
"""

import sys
import types

import pytest

import agent.codex_runtime as codex_runtime


def _make_agent(recorder):
    agent = types.SimpleNamespace()
    agent.session_cwd = "/tmp"
    agent.tool_progress_callback = recorder
    # Per-turn progress state the bridge reads/writes. run_codex_app_server_turn
    # also resets these at turn start; seed them so direct on_event drives work
    # even if that reset is ever skipped.
    agent._codex_phase = None
    agent._codex_step_count = 0
    # Generic-dev throttle clock (Problem 2). Seed to 0.0 so the first generic
    # emit always clears the >=30s gate; LEOS phases bypass it entirely.
    agent._codex_last_emit_ts = 0.0
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
            # the function returns early without exercising the heavy post-turn
            # bookkeeping, which is irrelevant to the bridge.
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


def _emit(on_event, item):
    on_event({"method": "item/started", "params": {"item": item}})


def test_codex_runtime_wires_progress_on_event(monkeypatch):
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))

    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    assert callable(on_event), "run_codex_app_server_turn must wire an on_event hook"


def test_turn_start_resets_phase_and_step_count(monkeypatch):
    """The per-turn reset zeroes the step counter, clears the phase, and resets
    the generic-dev throttle clock."""
    agent = _make_agent(lambda *a, **k: None)
    # Dirty the state as if a previous turn ran.
    agent._codex_phase = "계약 작업"
    agent._codex_step_count = 17
    agent._codex_last_emit_ts = 123456.0

    _run_turn_and_capture_on_event(monkeypatch, agent)

    assert agent._codex_phase is None
    assert agent._codex_step_count == 0
    assert agent._codex_last_emit_ts == 0.0


@pytest.mark.parametrize(
    "item,expected_fragment",
    [
        # LEOS contract — command path under ops/contracts.
        (
            {
                "type": "commandExecution",
                "command": "/bin/zsh -lc \"sed -n '1,20p' /Users/leo/LEOS/ops/contracts/x.md\"",
            },
            "계약 작업",
        ),
        # LEOS contract — fileChange path under ops/contracts.
        ({"type": "fileChange", "path": "/Users/leo/LEOS/ops/contracts/x.md"}, "계약 작업"),
        # Statutes / constitution.
        (
            {
                "type": "commandExecution",
                "command": "/bin/zsh -lc \"cat /Users/leo/LEOS/statutes/article-0.md\"",
            },
            "법·헌법 확인",
        ),
        # Ledger.
        (
            {
                "type": "commandExecution",
                "command": "/bin/zsh -lc \"cat /Users/leo/LEOS/ledger/2026.md\"",
            },
            "원장 기록",
        ),
        # Plain reader, non-LEOS → 코드 살펴보는 중.
        (
            {"type": "commandExecution", "command": "/bin/zsh -lc \"sed -n '1,5p' /tmp/x.py\""},
            "코드 살펴보는 중",
        ),
        # Plain searcher, non-LEOS → 코드 살펴보는 중.
        ({"type": "commandExecution", "command": "rg -n foo /tmp"}, "코드 살펴보는 중"),
    ],
)
def test_item_started_emits_meaningful_phase(monkeypatch, item, expected_fragment):
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    _emit(on_event, item)

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "tool.started"
    assert expected_fragment in kwargs.get("tool_name", "")
    # The step counter rides along as the preview.
    assert kwargs.get("preview") == "1단계째"


def test_leos_governance_and_treaties_and_reviews(monkeypatch):
    """A few more LEOS-domain phases map to their labels."""
    cases = [
        ({"type": "fileChange", "path": "/Users/leo/LEOS/ops/governance/gate.md"}, "통치·게이트"),
        ({"type": "fileChange", "path": "/Users/leo/LEOS/treaties/t1.md"}, "조약"),
        ({"type": "fileChange", "path": "/Users/leo/LEOS/reviews/r1.md"}, "감사·검토"),
    ]
    for item, expected in cases:
        calls = []
        agent = _make_agent(lambda *a, **k: calls.append((a, k)))
        on_event = _run_turn_and_capture_on_event(monkeypatch, agent)
        _emit(on_event, item)
        assert len(calls) == 1, expected
        assert expected in calls[0][1].get("tool_name", "")


def test_gateway_path_is_not_misread_as_governance(monkeypatch):
    """'gateway' contains 'gate' but is ordinary dev work, not LEOS governance.
    Reading gateway/*.py must read as 코드 살펴보는 중, never 통치·게이트."""
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    _emit(on_event, {
        "type": "commandExecution",
        "command": "/bin/zsh -lc \"sed -n '1,20p' gateway/run.py\"",
    })

    assert len(calls) == 1
    name = calls[0][1].get("tool_name", "")
    assert "통치·게이트" not in name
    assert "코드 살펴보는 중" in name


def test_non_leos_governance_skill_is_generic_dev(monkeypatch):
    """Problem 1: a skill literally named `agent-os-governance` contains
    'govern' but is NOT LEOS (no /leos/ path, no leos-* marker). Editing it
    must read as 코드 고치는 중, never 통치·게이트."""
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    _emit(on_event, {
        "type": "fileChange",
        "path": "/Users/leo/.hermes/skills/agent-os-governance/SKILL.md",
    })

    assert len(calls) == 1
    name = calls[0][1].get("tool_name", "")
    assert "통치·게이트" not in name
    assert "코드 고치는 중" in name


def test_non_leos_governance_dir_command_is_generic(monkeypatch):
    """Problem 1: a command touching a generic 'governance' dir (no /leos/)
    must read as 코드 살펴보는 중, not a LEOS label."""
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    _emit(on_event, {"type": "commandExecution", "command": "rg -n foo /some/governance/dir"})

    assert len(calls) == 1
    name = calls[0][1].get("tool_name", "")
    assert "통치·게이트" not in name
    assert "코드 살펴보는 중" in name


def test_real_leos_paths_still_classify(monkeypatch):
    """Problem 1: with real /leos/ context present, sub-classification still
    produces the right domain labels."""
    cases = [
        ({"type": "fileChange", "path": "/Users/leo/LEOS/ops/governance/gate.md"}, "통치·게이트"),
        ({"type": "fileChange", "path": "/Users/leo/LEOS/ops/contracts/x.md"}, "계약 작업"),
        ({"type": "fileChange", "path": "/Users/leo/LEOS/statutes/article-0.md"}, "법·헌법 확인"),
        ({"type": "fileChange", "path": "/Users/leo/LEOS/ledger/2026.md"}, "원장 기록"),
    ]
    for item, expected in cases:
        calls = []
        agent = _make_agent(lambda *a, **k: calls.append((a, k)))
        on_event = _run_turn_and_capture_on_event(monkeypatch, agent)
        _emit(on_event, item)
        assert len(calls) == 1, expected
        assert expected in calls[0][1].get("tool_name", ""), expected


def test_generic_dev_phases_are_throttled(monkeypatch):
    """Problem 2: generic dev phase changes are throttled to one line per ~30s.
    A phase change within the window is suppressed; another past the window
    emits. LEOS phases are unaffected (covered separately)."""
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    # Controllable clock. last_emit_ts is seeded to 0.0 by _make_agent, so the
    # first emit clears the gate (1000 - 0.0 >= 30); subsequent emits throttle
    # against the LAST EMIT time, not wall clock.
    clock = {"t": 1000.0}
    monkeypatch.setattr(codex_runtime.time, "time", lambda: clock["t"])

    # First generic phase (reader) → emits, arms throttle at t=1000.
    _emit(on_event, {"type": "commandExecution", "command": "/bin/zsh -lc \"sed -n '1,5p' /tmp/a.py\""})
    assert len(calls) == 1
    assert "코드 살펴보는 중" in calls[0][1].get("tool_name", "")

    # +5s — switch to a DIFFERENT generic phase (file edit) inside the 30s
    # window → SUPPRESSED (no new emit), and _codex_phase must NOT advance.
    clock["t"] = 1005.0
    _emit(on_event, {"type": "fileChange", "path": "/tmp/a.py"})
    assert len(calls) == 1, "generic phase shift within 30s must be throttled"
    assert agent._codex_phase == "코드 살펴보는 중", "suppressed shift must keep last EMITTED phase"

    # +35s from the last EMIT — a generic shift to a DIFFERENT phase (file edit)
    # past the window → emits. (It must differ from the last EMITTED phase
    # "코드 살펴보는 중", which the suppressed shift left in place.)
    clock["t"] = 1035.0
    _emit(on_event, {"type": "fileChange", "path": "/tmp/a.py"})
    assert len(calls) == 2
    assert "코드 고치는 중" in calls[1][1].get("tool_name", "")


def test_leos_phase_bypasses_throttle(monkeypatch):
    """Problem 2: a LEOS milestone emits immediately even inside the 30s
    generic-throttle window."""
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    clock = {"t": 1000.0}
    monkeypatch.setattr(codex_runtime.time, "time", lambda: clock["t"])

    # t=1000 — a generic reader emits and arms the throttle.
    _emit(on_event, {"type": "commandExecution", "command": "/bin/zsh -lc \"sed -n '1,5p' /tmp/a.py\""})
    assert len(calls) == 1

    # t=1005 — a LEOS phase change only 5s later (inside the window) STILL emits.
    clock["t"] = 1005.0
    _emit(on_event, {"type": "fileChange", "path": "/Users/leo/LEOS/ops/contracts/x.md"})
    assert len(calls) == 2
    assert "계약 작업" in calls[1][1].get("tool_name", "")


def test_same_phase_collapses_to_single_emit(monkeypatch):
    """Two consecutive same-phase items emit once; a third, different phase
    emits a second time (meaningful-unit collapsing)."""
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    # Drive the throttle clock so generic-phase SHIFTS are past the 30s gate
    # (collapsing, not throttling, is what's under test here). Start past the
    # gate so the first emit clears it against the 0.0 seed.
    clock = {"t": 1000.0}
    monkeypatch.setattr(codex_runtime.time, "time", lambda: clock["t"])

    # Two readers in a row → both "코드 살펴보는 중" → ONE emit.
    _emit(on_event, {"type": "commandExecution", "command": "/bin/zsh -lc \"sed -n '1,5p' /tmp/a.py\""})
    _emit(on_event, {"type": "commandExecution", "command": "rg -n bar /tmp"})
    assert len(calls) == 1
    assert "코드 살펴보는 중" in calls[0][1].get("tool_name", "")

    # A third item of a DIFFERENT phase (a file edit), well past the throttle
    # window → second emit.
    clock["t"] = 1100.0
    _emit(on_event, {"type": "fileChange", "path": "/tmp/a.py"})
    assert len(calls) == 2
    assert "코드 고치는 중" in calls[1][1].get("tool_name", "")


def test_step_counter_increments_even_without_emit(monkeypatch):
    """The step counter advances on every recognized item, including the ones
    that don't emit because the phase is unchanged."""
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    _emit(on_event, {"type": "commandExecution", "command": "/bin/zsh -lc \"sed -n '1,5p' /tmp/a.py\""})
    _emit(on_event, {"type": "commandExecution", "command": "rg -n bar /tmp"})
    _emit(on_event, {"type": "commandExecution", "command": "cat /tmp/c.py"})

    # Only one emit (all same phase) but three steps counted.
    assert len(calls) == 1
    assert agent._codex_step_count == 3
    # Preview on the single emit reflects the step number at emit time (1).
    assert calls[0][1].get("preview") == "1단계째"


def test_non_item_started_events_ignored(monkeypatch):
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    # Wrong method, and an item/started with no recognizable tool item type.
    on_event({"method": "turn/completed", "params": {}})
    _emit(on_event, {"type": "reasoning"})

    assert calls == []
    # A non-tool item must not advance the step counter either.
    assert agent._codex_step_count == 0


def test_progress_bridge_is_fail_open(monkeypatch):
    """A raising tool_progress_callback must not propagate out of on_event."""

    def _boom(*_a, **_k):
        raise RuntimeError("display blew up")

    agent = _make_agent(_boom)
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    # Must swallow the error — a display failure may never break a codex turn.
    _emit(on_event, {"type": "commandExecution", "command": "git status"})


# ---------------------------------------------------------------------------
# LEOS_STATE marker — explicit, race-free production/contract state transitions.
#
# The agent emits each state transition as a no-op shell command
# `: LEOS_STATE <emoji> <label> [:: <detail>]`. The `:` POSIX no-op carries the
# state INTO codex's commandExecution stream (which the bridge already sees), so
# the state IS the command — race-free, no file to read mid-write. A marker is a
# signal, not work: it emits IMMEDIATELY (bypassing the 30s generic throttle),
# dedups vs the current phase, and does NOT bump the step counter.
# ---------------------------------------------------------------------------


def test_leos_state_marker_emits_label_no_detail(monkeypatch):
    """`: LEOS_STATE 📝 계약 인터뷰` → exactly one emit, tool_name is the label,
    preview is empty, and the step counter does NOT advance (a marker is a
    signal, not a step)."""
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    _emit(on_event, {"type": "commandExecution", "command": ": LEOS_STATE 📝 계약 인터뷰"})

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0] == "tool.started"
    assert kwargs.get("tool_name") == "📝 계약 인터뷰"
    assert kwargs.get("preview") == ""
    # A marker is not real work — the step counter must stay at 0.
    assert agent._codex_step_count == 0


def test_leos_state_marker_with_detail(monkeypatch):
    """`: LEOS_STATE 🏗️ 생산 중 :: dashboard API` → label left of `::`,
    detail right of it."""
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    _emit(on_event, {
        "type": "commandExecution",
        "command": ": LEOS_STATE 🏗️ 생산 중 :: dashboard API",
    })

    assert len(calls) == 1
    kwargs = calls[0][1]
    assert kwargs.get("tool_name") == "🏗️ 생산 중"
    assert kwargs.get("preview") == "dashboard API"
    assert agent._codex_step_count == 0


def test_leos_state_marker_bypasses_generic_throttle(monkeypatch):
    """A LEOS_STATE marker for a NEW state emits IMMEDIATELY even when the 30s
    generic throttle would otherwise suppress it: seed _codex_last_emit_ts to
    'now' and _codex_phase to a generic label, then the marker still emits.
    Contrast: a generic dev phase change under the same clock is throttled."""
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    clock = {"t": 1000.0}
    monkeypatch.setattr(codex_runtime.time, "time", lambda: clock["t"])

    # Seed: a generic phase just emitted at t=1000 (throttle armed, 0s elapsed).
    agent._codex_phase = "코드 살펴보는 중"
    agent._codex_last_emit_ts = 1000.0

    # Contrast — a generic dev phase change 0s later is THROTTLED (no emit).
    _emit(on_event, {"type": "fileChange", "path": "/tmp/a.py"})
    assert len(calls) == 0, "generic phase change within 30s must be throttled"

    # The LEOS_STATE marker for a NEW state STILL emits immediately.
    _emit(on_event, {"type": "commandExecution", "command": ": LEOS_STATE 🎯 계약 이행"})
    assert len(calls) == 1
    assert calls[0][1].get("tool_name") == "🎯 계약 이행"


def test_leos_state_marker_dedups_same_state(monkeypatch):
    """The same marker twice → only one emit (dedup vs the current phase)."""
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    _emit(on_event, {"type": "commandExecution", "command": ": LEOS_STATE ⏳ 계약 미충족"})
    _emit(on_event, {"type": "commandExecution", "command": ": LEOS_STATE ⏳ 계약 미충족"})

    assert len(calls) == 1
    assert calls[0][1].get("tool_name") == "⏳ 계약 미충족"


def test_leos_state_marker_wrapped_in_login_shell(monkeypatch):
    """The wrapped form `/bin/zsh -lc ": LEOS_STATE ✅ 계약 성립"` is dewrapped,
    then detected."""
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    _emit(on_event, {
        "type": "commandExecution",
        "command": "/bin/zsh -lc \": LEOS_STATE ✅ 계약 성립\"",
    })

    assert len(calls) == 1
    assert calls[0][1].get("tool_name") == "✅ 계약 성립"
    assert calls[0][1].get("preview") == ""
    assert agent._codex_step_count == 0


def test_normal_command_still_classifies_and_bumps_counter(monkeypatch):
    """Regression: a normal command (not a marker) still classifies as a phase
    AND bumps the step counter. The marker fast-path must not swallow real
    commands."""
    calls = []
    agent = _make_agent(lambda *a, **k: calls.append((a, k)))
    on_event = _run_turn_and_capture_on_event(monkeypatch, agent)

    _emit(on_event, {"type": "commandExecution", "command": "rg -n foo /tmp"})

    assert len(calls) == 1
    kwargs = calls[0][1]
    assert "코드 살펴보는 중" in kwargs.get("tool_name", "")
    assert kwargs.get("preview") == "1단계째"
    # A real command IS work — the counter advances.
    assert agent._codex_step_count == 1
