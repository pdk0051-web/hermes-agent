"""Regression tests for LEOS ledger writes from the contract-gate closure.

DE-FORK Stage 3: the closure-append logic moved out of ``gateway/run.py`` into
the leos-governor plugin (``contract_gate_hook._append_closure``). These tests
now exercise the relocated plugin function. The plugin is loaded EXACTLY as
Hermes loads it (package ``hermes_plugins.leos_governor``) via importlib so its
``from agent.work_journal import ...`` fallback resolves.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN_DIR = Path.home() / ".hermes" / "plugins" / "leos-governor"
_NS = "hermes_plugins"


def _load_contract_gate_hook():
    """Load the relocated ``contract_gate_hook`` module from the leos-governor
    plugin EXACTLY as Hermes does (package ``hermes_plugins.leos_governor``)."""
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


@pytest.fixture
def contract_gate_hook():
    return _load_contract_gate_hook()


def _install_fake_leos_cli(tmp_path):
    leos_root = tmp_path / "LEOS"
    (leos_root / "ledger").mkdir(parents=True)
    (leos_root / "ledger" / "main.jsonl").write_text("", encoding="utf-8")
    (leos_root / "scripts").mkdir()
    (leos_root / "scripts" / "leos.py").write_text("# fake\n", encoding="utf-8")
    (leos_root / ".venv" / "bin").mkdir(parents=True)
    (leos_root / ".venv" / "bin" / "python").write_text("# fake\n", encoding="utf-8")
    return leos_root


def test_contract_gate_closure_uses_leos_cli_not_raw_ledger(
    contract_gate_hook, monkeypatch, tmp_path
):
    leos_root = _install_fake_leos_cli(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LEOS_ROOT", "~/LEOS")

    calls = []

    def fake_run(argv, **kwargs):
        calls.append({"argv": argv, "kwargs": kwargs})
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(
        contract_gate_hook,
        "subprocess",
        SimpleNamespace(run=fake_run, DEVNULL=subprocess.DEVNULL),
        raising=False,
    )

    contract_gate_hook._append_closure(
        "session-1",
        "C4-example-1",
        {"status": "met", "met": ["done"], "unmet": []},
    )

    assert (leos_root / "ledger" / "main.jsonl").read_text(encoding="utf-8") == ""
    assert len(calls) == 1
    argv = calls[0]["argv"]
    assert argv[:4] == [
        str(leos_root / ".venv" / "bin" / "python"),
        str(leos_root / "scripts" / "leos.py"),
        "ledger",
        "append",
    ]
    assert "--class" in argv
    assert argv[argv.index("--class") + 1] == "action"
    assert "--actor" in argv
    assert argv[argv.index("--actor") + 1] == "leo_hermes"
    assert "--event" in argv
    assert argv[argv.index("--event") + 1] == "contract_gate:closure:C4-example-1"
    payload = json.loads(argv[argv.index("--payload") + 1])
    assert payload["kind"] == "contract_closure"
    assert payload["session_id"] == "session-1"
    assert payload["contract_id"] == "C4-example-1"
    assert payload["source"] == "leos-governor.contract_gate"
    assert calls[0]["kwargs"]["cwd"] == str(leos_root)


def test_contract_gate_closure_falls_back_to_journal_without_raw_ledger_write(
    contract_gate_hook,
    monkeypatch,
    tmp_path,
):
    leos_root = _install_fake_leos_cli(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("LEOS_ROOT", "~/LEOS")

    def fake_run(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv, stderr="no writer")

    monkeypatch.setattr(
        contract_gate_hook,
        "subprocess",
        SimpleNamespace(run=fake_run, DEVNULL=subprocess.DEVNULL),
        raising=False,
    )

    import agent.work_journal as work_journal

    journal_dir = tmp_path / "journal"
    monkeypatch.setattr(work_journal, "_resolve_journal_dir", lambda _: journal_dir)

    contract_gate_hook._append_closure(
        "session-2",
        "C4-example-2",
        {"status": "met", "met": ["verified"], "unmet": []},
    )

    assert (leos_root / "ledger" / "main.jsonl").read_text(encoding="utf-8") == ""
    lines = (journal_dir / "session-2.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["kind"] == "contract_closure"
    assert record["session_id"] == "session-2"
    assert record["contract_id"] == "C4-example-2"
    assert record["status"] == "met"
