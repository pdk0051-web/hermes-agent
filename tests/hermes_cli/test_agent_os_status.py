from __future__ import annotations


def test_agent_os_status_missing_root(monkeypatch, tmp_path):
    from hermes_cli.agent_os_status import build_agent_os_status

    missing = tmp_path / "missing-leos"
    monkeypatch.setenv("LEOS_ROOT", str(missing))

    status = build_agent_os_status(now=lambda: "2026-06-05T00:00:00Z")

    assert status["agent_os"] == "leos"
    assert status["detected"] is False
    assert status["root"] == str(missing)
    assert status["constitution"]["exists"] is False
    assert status["contracts"] == []
    assert "leos_root_missing" in status["warnings"]
    assert status["validation"]["last_checked_at"] == "2026-06-05T00:00:00Z"


def test_agent_os_status_hashes_present_files_without_content(monkeypatch, tmp_path):
    from hermes_cli import agent_os_status

    root = tmp_path / "LEOS"
    constitution = root / "constitution" / "constitution.md"
    contract = root / "contracts" / "agent-console-dashboard.yaml"
    constitution.parent.mkdir(parents=True)
    contract.parent.mkdir(parents=True)
    constitution.write_text("PRIVATE CONSTITUTION BODY\n", encoding="utf-8")
    contract.write_text("PRIVATE CONTRACT BODY\n", encoding="utf-8")
    monkeypatch.setenv("LEOS_ROOT", str(root))
    monkeypatch.setattr(agent_os_status, "_list_skill_records", lambda: [])
    monkeypatch.setattr(agent_os_status, "_disabled_skill_names", lambda: set())
    monkeypatch.setattr(
        agent_os_status,
        "_runtime_status",
        lambda: {
            "api_mode": "codex_app_server",
            "memory_provider": "leos_knowledge",
            "tool_progress_bridge": "available",
        },
    )

    status = agent_os_status.build_agent_os_status(now=lambda: "2026-06-05T00:00:00Z")

    assert status["detected"] is True
    assert status["constitution"]["exists"] is True
    assert len(status["constitution"]["sha256"]) == 64
    assert status["contracts"][0]["id"] == "agent-console-dashboard"
    payload = repr(status)
    assert "PRIVATE CONSTITUTION BODY" not in payload
    assert "PRIVATE CONTRACT BODY" not in payload
    assert "memory_provider_not_leos_knowledge" not in status["warnings"]


def test_agent_os_status_reports_required_skill_states(monkeypatch, tmp_path):
    from hermes_cli import agent_os_status

    root = tmp_path / "LEOS"
    (root / "constitution").mkdir(parents=True)
    (root / "contracts").mkdir(parents=True)
    (root / "constitution" / "constitution.md").write_text("constitution\n", encoding="utf-8")
    monkeypatch.setenv("LEOS_ROOT", str(root))
    monkeypatch.setattr(
        agent_os_status,
        "_list_skill_records",
        lambda: [
            {"name": "leos-constitution"},
            {"name": "leos-dashboard-work"},
        ],
    )
    monkeypatch.setattr(agent_os_status, "_disabled_skill_names", lambda: {"leos-dashboard-work"})
    monkeypatch.setattr(
        agent_os_status,
        "_runtime_status",
        lambda: {
            "api_mode": "codex_app_server",
            "memory_provider": "leos_knowledge",
            "tool_progress_bridge": "available",
        },
    )

    status = agent_os_status.build_agent_os_status()
    by_name = {row["name"]: row for row in status["skills"]}

    assert by_name["leos-constitution"]["installed"] is True
    assert by_name["leos-constitution"]["enabled"] is True
    assert by_name["leos-contract"]["installed"] is False
    assert by_name["leos-dashboard-work"]["installed"] is True
    assert by_name["leos-dashboard-work"]["enabled"] is False
    assert "skill_missing:leos-contract" in status["warnings"]
    assert "skill_disabled:leos-dashboard-work" in status["warnings"]
