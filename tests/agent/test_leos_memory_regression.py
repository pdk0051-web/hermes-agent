"""Regression coverage for the 2026-06-05 'memory not updating' investigation.

Context: after switching AGENT LEO to the `codex_app_server` runtime and setting
`memory.provider: leos_knowledge`, the user reported memory updates stopped.
The investigation found the storage + provider layers are healthy; the only
real blocker was the built-in `memory_char_limit` (MEMORY.md sat at ~2.1k chars
against the old 2200 limit). These tests pin the behaviours that were verified
by hand so a future upstream bump can't silently re-break them:

  1. The built-in MemoryStore can add+persist to BOTH targets at the configured
     limits (this is what the codex_app_server background-review fork relies on,
     since the `memory` tool is not dispatched inside the codex subprocess).
  2. The char-limit gate is the thing that blocks a write when the file is near
     full — i.e. the symptom is a quota wall, not a broken writer.

These tests are self-contained (tmp HERMES_HOME); they do not touch the live
~/.hermes/memories files.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture()
def tmp_hermes_home(tmp_path, monkeypatch):
    home = tmp_path / "hermes_home"
    (home / "memories").mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(home))
    return home


def _fresh_store(memory_char_limit: int, user_char_limit: int):
    from tools.memory_tool import MemoryStore

    store = MemoryStore(
        memory_char_limit=memory_char_limit,
        user_char_limit=user_char_limit,
    )
    store.load_from_disk()
    return store


def test_memory_store_writes_both_targets_at_configured_limits(tmp_hermes_home):
    """Both 'memory' and 'user' adds persist to disk at the live config limits.

    Mirrors config.yaml memory.{memory_char_limit:3500, user_char_limit:2500}.
    """
    store = _fresh_store(3500, 2500)

    res_mem = store.add("memory", "env fact: regression probe entry")
    res_user = store.add("user", "preference: regression probe entry")

    assert res_mem.get("success") is True, res_mem
    assert res_user.get("success") is True, res_user

    mem_disk = (tmp_hermes_home / "memories" / "MEMORY.md").read_text(encoding="utf-8")
    user_disk = (tmp_hermes_home / "memories" / "USER.md").read_text(encoding="utf-8")
    assert "env fact: regression probe entry" in mem_disk
    assert "preference: regression probe entry" in user_disk


def test_char_limit_is_the_blocker_when_near_full(tmp_hermes_home):
    """A near-full MEMORY.md rejects new adds — this is the real user symptom.

    Raising memory_char_limit (2200 -> 3500, as the user did) is what unblocks
    it, NOT any provider/runtime change.
    """
    # Fill MEMORY.md to just under a tight limit.
    tight = _fresh_store(memory_char_limit=300, user_char_limit=2500)
    filler = "x" * 280
    assert tight.add("memory", filler).get("success") is True

    # A second add now exceeds the tight limit and is rejected.
    blocked = tight.add("memory", "y" * 100)
    assert blocked.get("success") is False
    assert "exceed" in (blocked.get("error") or "").lower()

    # Re-open the SAME on-disk file with a higher limit -> the add succeeds.
    roomy = _fresh_store(memory_char_limit=3500, user_char_limit=2500)
    assert roomy.add("memory", "y" * 100).get("success") is True


def test_leos_knowledge_provider_matches_current_abc_contract(monkeypatch):
    """The external `leos_knowledge` provider still satisfies the 0.15.1 ABC.

    It must load, report available, expose 0 tools (context-only), and accept
    the post-0.15.1 sync_turn(messages=...) signature. A real upstream contract
    break (e.g. a renamed/repurposed hook) would fail here.

    Provider discovery is HERMES_HOME-scoped (it looks under
    ``$HERMES_HOME/plugins/<name>/``). We pin HERMES_HOME to the *real* home so
    the user-installed plugin at ~/.hermes/plugins/leos_knowledge resolves
    regardless of test ordering, and skip when that plugin is absent.
    """
    from hermes_constants import get_hermes_home_override

    real_home = get_hermes_home_override() or os.path.expanduser("~/.hermes")
    monkeypatch.setenv("HERMES_HOME", str(real_home))

    leos_root = os.environ.get("LEOS_ROOT") or os.path.expanduser("~/LEOS")
    if not os.path.exists(os.path.join(leos_root, "scripts", "leos_knowledge.py")):
        pytest.skip("LEOS knowledge engine not present on this machine")
    if not os.path.exists(os.path.join(str(real_home), "plugins", "leos_knowledge", "__init__.py")):
        pytest.skip("leos_knowledge user-plugin not installed under HERMES_HOME")

    from plugins.memory import load_memory_provider

    provider = load_memory_provider("leos_knowledge")
    assert provider is not None, "leos_knowledge provider failed to load"
    assert provider.name == "leos_knowledge"
    assert provider.is_available() is True
    assert provider.get_tool_schemas() == []  # context-only, registers 0 tools

    # New 0.15.1 signature must be accepted (keyword 'messages').
    provider.initialize("sess-test")
    provider.sync_turn("hi", "ok", session_id="sess-test", messages=[])  # must not raise
    provider.on_memory_write("add", "memory", "")  # empty body is a no-op, must not raise
