"""Tests for the relocated contract-gate auto-continue hook (de-fork Stage 3).

Covers the leos-governor ``contract_gate_hook`` module that now owns the
contract-completion suppression logic, and its integration with the generic
``resolve_auto_continue`` core hook:

  * plugin-absent == flag-off: with no plugin registered (the shipped default)
    ``invoke_hook("resolve_auto_continue", ...)`` returns ``[]`` → the gateway
    leaves ``_contract_gate_close`` False → auto-continue note stays. (Same
    observable result as the gate being explicitly disabled.)
  * plugin-raises == safe: a callback that raises is swallowed by
    ``PluginManager.invoke_hook`` → contributes no result → no suppression and
    no crash.
  * callback contract: ``on_resolve_auto_continue`` returns ``None`` (no
    suppression) when the flag is off OR the auto-continue guards are false, and
    ``{"suppress": True}`` only on a real CLOSE.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

from hermes_cli.plugins import PluginManager

PLUGIN_DIR = Path.home() / ".hermes" / "plugins" / "leos-governor"
_NS = "hermes_plugins"


def _load_contract_gate_hook():
    if _NS not in sys.modules:
        ns = types.ModuleType(_NS)
        ns.__path__ = []  # type: ignore[attr-defined]
        ns.__package__ = _NS
        sys.modules[_NS] = ns
    mod_name = f"{_NS}.leos_governor"
    if mod_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            mod_name,
            str(PLUGIN_DIR / "__init__.py"),
            submodule_search_locations=[str(PLUGIN_DIR)],
        )
        module = importlib.util.module_from_spec(spec)
        module.__package__ = mod_name
        module.__path__ = [str(PLUGIN_DIR)]  # type: ignore[attr-defined]
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
    cg_name = f"{mod_name}.contract_gate_hook"
    cg_spec = importlib.util.spec_from_file_location(
        cg_name, str(PLUGIN_DIR / "contract_gate_hook.py")
    )
    cg = importlib.util.module_from_spec(cg_spec)
    cg.__package__ = mod_name
    sys.modules[cg_name] = cg
    cg_spec.loader.exec_module(cg)
    return cg


# ---------------------------------------------------------------------------
# plugin-absent == flag-off (integration through invoke_hook)
# ---------------------------------------------------------------------------


def test_plugin_absent_returns_empty_results_like_flag_off():
    """No plugin registered → invoke_hook returns [] → gateway keeps the note.

    Mirrors the exact walk in gateway/run.py: an empty result list leaves
    ``_contract_gate_close`` False (no suppression)."""
    mgr = PluginManager()  # nothing registered
    results = mgr.invoke_hook(
        "resolve_auto_continue",
        session_id="s1",
        gateway=object(),
        is_resume_pending=True,
        has_fresh_tool_tail=False,
    )
    assert results == []

    # Gateway-side reduction of that result list:
    _contract_gate_close = False
    for _r in results:
        if isinstance(_r, dict) and "suppress" in _r:
            _contract_gate_close = bool(_r["suppress"])
            break
    assert _contract_gate_close is False


def test_registered_callback_with_flag_off_yields_no_suppression():
    """Even with the callback registered, flag OFF (default) → no result, so
    the gateway keeps the auto-continue note."""
    cg = _load_contract_gate_hook()
    mgr = PluginManager()
    mgr._hooks.setdefault("resolve_auto_continue", []).append(
        cg.on_resolve_auto_continue
    )

    results = mgr.invoke_hook(
        "resolve_auto_continue",
        session_id="s1",
        gateway=object(),
        is_resume_pending=True,
        has_fresh_tool_tail=False,
    )
    # flag is off by default → callback returns None → dropped → []
    assert results == []


# ---------------------------------------------------------------------------
# plugin-raises == safe
# ---------------------------------------------------------------------------


def test_plugin_raises_is_swallowed_and_safe():
    """A raising callback must not break dispatch and must not suppress.

    ``PluginManager.invoke_hook`` catches per-callback exceptions, so the
    raising callback contributes nothing and the gateway keeps the note."""
    def _boom(**_kw):
        raise RuntimeError("contract gate exploded")

    mgr = PluginManager()
    mgr._hooks.setdefault("resolve_auto_continue", []).append(_boom)

    results = mgr.invoke_hook(
        "resolve_auto_continue",
        session_id="s1",
        gateway=object(),
        is_resume_pending=True,
        has_fresh_tool_tail=True,
    )
    assert results == []  # raise swallowed, no entry

    _contract_gate_close = False
    for _r in results:
        if isinstance(_r, dict) and "suppress" in _r:
            _contract_gate_close = bool(_r["suppress"])
            break
    assert _contract_gate_close is False


def test_callback_internal_raise_returns_none():
    """If ``_should_close`` itself raised, the callback's own try/except must
    still return None (fail-open at the callback boundary too)."""
    cg = _load_contract_gate_hook()

    orig = cg._should_close
    try:
        cg._should_close = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x"))
        out = cg.on_resolve_auto_continue(
            session_id="s1", is_resume_pending=True, has_fresh_tool_tail=False
        )
        assert out is None
    finally:
        cg._should_close = orig


# ---------------------------------------------------------------------------
# callback contract
# ---------------------------------------------------------------------------


def test_callback_returns_none_when_no_autocontinue_conditions():
    """Guard: neither resume-pending nor fresh-tool-tail → None even if the
    gate would otherwise close (the hook must never suppress a normal turn)."""
    cg = _load_contract_gate_hook()
    # Force _should_close True to prove the guard, not the gate, blocks it.
    orig = cg._should_close
    try:
        cg._should_close = lambda *_a, **_k: True
        out = cg.on_resolve_auto_continue(
            session_id="s1", is_resume_pending=False, has_fresh_tool_tail=False
        )
        assert out is None
    finally:
        cg._should_close = orig


def test_callback_suppresses_on_real_close():
    """When an auto-continue guard holds AND the gate decides CLOSE, the
    callback returns the suppression directive."""
    cg = _load_contract_gate_hook()
    orig = cg._should_close
    try:
        cg._should_close = lambda *_a, **_k: True
        out = cg.on_resolve_auto_continue(
            session_id="s1", is_resume_pending=True, has_fresh_tool_tail=False
        )
        assert out == {"suppress": True}
    finally:
        cg._should_close = orig


def test_should_close_flag_off_short_circuits_true():
    """``_should_close`` returns False the instant the flag is falsy (the
    shipped default), without touching the journal/contract machinery."""
    cg = _load_contract_gate_hook()
    orig = cg._contract_gate_enabled
    try:
        cg._contract_gate_enabled = lambda: False
        assert cg._should_close("any-session") is False
    finally:
        cg._contract_gate_enabled = orig


def test_should_close_no_contract_governance_returns_false(monkeypatch):
    """Flag ON but the session's journal has no governing contract id →
    False (the gate is opt-in to contract-governed sessions only)."""
    cg = _load_contract_gate_hook()
    monkeypatch.setattr(cg, "_contract_gate_enabled", lambda: True)
    # Patch the read_journal that _should_close imports lazily.
    import agent.work_journal as wj
    monkeypatch.setattr(wj, "read_journal", lambda _sid: [{"summary": "x"}])
    assert cg._should_close("session-without-contract") is False
