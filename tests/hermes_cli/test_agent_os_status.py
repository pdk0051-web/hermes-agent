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


def test_agent_os_status_detects_current_leos_ops_layout(monkeypatch, tmp_path):
    from hermes_cli import agent_os_status

    root = tmp_path / "LEOS"
    constitution = root / "article-0" / "_SOUL.md"
    contract = root / "ops" / "contracts" / "C5-dashboard-1.yaml"
    constitution.parent.mkdir(parents=True)
    contract.parent.mkdir(parents=True)
    constitution.write_text("PRIVATE ARTICLE 0 BODY\n", encoding="utf-8")
    contract.write_text(
        "contract_id: C5-dashboard-1\n"
        "status: active\n"
        "description: PRIVATE DASHBOARD CONTRACT BODY\n",
        encoding="utf-8",
    )
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

    assert status["constitution"]["path"] == str(constitution)
    assert status["constitution"]["exists"] is True
    assert "constitution_missing" not in status["warnings"]
    assert status["contracts"][0]["id"] == "C5-dashboard-1"
    assert status["contracts"][0]["status"] == "active"
    assert "contracts_missing" not in status["warnings"]
    payload = repr(status)
    assert "PRIVATE ARTICLE 0 BODY" not in payload
    assert "PRIVATE DASHBOARD CONTRACT BODY" not in payload


def test_agent_os_status_reports_governor_bridge_contract_and_citizens(monkeypatch, tmp_path):
    from hermes_cli import agent_os_status

    root = tmp_path / "LEOS"
    constitution = root / "article-0" / "_SOUL.md"
    contract = root / "ops" / "contracts" / "C2-hermes-coexistence-1.yaml"
    hermes_citizen = root / "citizens" / "shared-services" / "Hermes.yaml"
    dino_citizen = root / "citizens" / "shared-services" / "Dino.yaml"
    constitution.parent.mkdir(parents=True)
    contract.parent.mkdir(parents=True)
    hermes_citizen.parent.mkdir(parents=True)
    constitution.write_text("PRIVATE ARTICLE 0 BODY\n", encoding="utf-8")
    contract.write_text(
        "contract_id: C2-hermes-coexistence-1\n"
        "status: active\n"
        "description: PRIVATE COEXISTENCE CONTRACT BODY\n",
        encoding="utf-8",
    )
    hermes_citizen.write_text(
        "citizen_id: hermes\n"
        "class: Shared Service\n"
        "citizenship: Imported\n"
        "authority: A3\n"
        "action_trust: AT2\n",
        encoding="utf-8",
    )
    dino_citizen.write_text(
        "citizen_id: dino\n"
        "class: Shared Service\n"
        "citizenship: Native\n"
        "authority: A2\n"
        "action_trust:\n"
        "  default: AT2\n"
        "role_title: DINO\n",
        encoding="utf-8",
    )
    hermes_home = tmp_path / ".hermes"
    governor = hermes_home / "plugins" / "leos-governor"
    governor.mkdir(parents=True)
    (governor / "plugin.yaml").write_text(
        "name: leos-governor\n"
        "version: 1.0.0\n"
        "hooks:\n"
        "  - post_tool_call\n"
        "  - pre_tool_call\n"
        "  - pre_llm_call\n"
        "  - on_session_start\n",
        encoding="utf-8",
    )
    for name in ("__init__.py", "floor_gate.py", "rehydrate.py"):
        (governor / name).write_text("# bridge hook\n", encoding="utf-8")
    monkeypatch.setenv("LEOS_ROOT", str(root))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
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

    status = agent_os_status.build_agent_os_status(now=lambda: "2026-06-08T00:00:00Z")

    contracts = {row["id"]: row for row in status["contracts"]}
    citizens = {row["id"]: row for row in status["citizens"]}

    assert contracts["C2-hermes-coexistence-1"]["status"] == "active"
    assert status["bridge"]["name"] == "leos-governor"
    assert status["bridge"]["installed"] is True
    assert status["bridge"]["active"] is True
    assert status["bridge"]["version"] == "1.0.0"
    assert status["bridge"]["hook_files"]["pre_tool_call"] is True
    assert status["bridge"]["hook_files"]["pre_llm_call"] is True
    assert citizens["hermes"]["citizenship"] == "Imported"
    assert citizens["hermes"]["authority"] == "A3"
    assert citizens["dino"]["role_title"] == "DINO"
    assert citizens["dino"]["action_trust"] == "AT2"
    assert citizens["dino"]["harnessed"] is True
    assert "leos_governor_missing" not in status["warnings"]
    assert "citizens_missing" not in status["warnings"]
    payload = repr(status)
    assert "PRIVATE ARTICLE 0 BODY" not in payload
    assert "PRIVATE COEXISTENCE CONTRACT BODY" not in payload


def test_agent_os_status_surfaces_law_contract_knowledge_loop(monkeypatch, tmp_path):
    from hermes_cli import agent_os_status

    root = tmp_path / "LEOS"
    article_0 = root / "article-0" / "_SOUL.md"
    prd_index = root / "prd" / "INDEX.md"
    in_force_prd = root / "prd" / "current" / "LEOS-PRD_v1.0.2-1_in_force.md"
    coexistence = root / "ops" / "contracts" / "C2-hermes-coexistence-1.yaml"
    knowledge_contract = root / "ops" / "contracts" / "C2-knowledge-1.yaml"
    polity = root / "ops" / "contracts" / "C0-polity-1.yaml"
    knowledge_readme = root / "ops" / "knowledge" / "README.md"
    knowledge_map = root / "ops" / "knowledge" / "index" / "knowledge-map.md"
    evergreen = root / "ops" / "knowledge" / "evergreen" / "source-synthesis-separation.md"
    fleeting = root / "ops" / "knowledge" / "fleeting" / "reflection.md"
    source = root / "ops" / "knowledge" / "sources" / "pm-correction.md"

    for path in (
        article_0,
        prd_index,
        in_force_prd,
        coexistence,
        knowledge_contract,
        polity,
        knowledge_readme,
        knowledge_map,
        evergreen,
        fleeting,
        source,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)

    article_0.write_text("PRIVATE ARTICLE 0 BODY\n", encoding="utf-8")
    prd_index.write_text("PRIVATE PRD INDEX\n", encoding="utf-8")
    in_force_prd.write_text("PRIVATE PRD BODY\n", encoding="utf-8")
    coexistence.write_text(
        "contract_id: C2-hermes-coexistence-1\nstatus: active\n",
        encoding="utf-8",
    )
    knowledge_contract.write_text(
        "contract_id: C2-knowledge-1\nstatus: active\n",
        encoding="utf-8",
    )
    polity.write_text("contract_id: C0-polity-1\nstatus: active\n", encoding="utf-8")
    knowledge_readme.write_text("PRIVATE KNOWLEDGE README\n", encoding="utf-8")
    knowledge_map.write_text("PRIVATE KNOWLEDGE MAP\n", encoding="utf-8")
    evergreen.write_text("PRIVATE EVERGREEN KNOWLEDGE\n", encoding="utf-8")
    fleeting.write_text("PRIVATE FLEETING KNOWLEDGE\n", encoding="utf-8")
    source.write_text("PRIVATE SOURCE KNOWLEDGE\n", encoding="utf-8")

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

    status = agent_os_status.build_agent_os_status(now=lambda: "2026-06-08T00:00:00Z")

    loop = status["loop"]
    law_ref_ids = {ref["id"] for ref in loop["law"]["refs"]}

    assert loop["law"]["status"] == "visible"
    assert {"article_0_soul", "prd_index", "prd_in_force"}.issubset(law_ref_ids)
    assert loop["law"]["in_force_prd_count"] == 1
    assert loop["contracts"]["status"] == "visible"
    assert loop["contracts"]["active_count"] == 3
    assert loop["contracts"]["cascade_tiers"] == {"C0": 1, "C2": 2}
    assert loop["contracts"]["coexistence_active"] is True
    assert loop["contracts"]["knowledge_active"] is True
    assert loop["knowledge"]["status"] == "visible"
    assert loop["knowledge"]["counts"] == {
        "evergreen": 1,
        "fleeting": 1,
        "sources": 1,
    }
    assert "law axis file state inspected" in status["validation"]["evidence"]
    assert "contract loop state inspected" in status["validation"]["evidence"]
    assert "knowledge axis file state inspected" in status["validation"]["evidence"]
    assert "law_axis_missing" not in status["warnings"]
    assert "contract_axis_missing" not in status["warnings"]
    assert "knowledge_axis_missing" not in status["warnings"]

    payload = repr(status)
    assert "PRIVATE ARTICLE 0 BODY" not in payload
    assert "PRIVATE PRD BODY" not in payload
    assert "PRIVATE KNOWLEDGE MAP" not in payload


def test_agent_os_status_discovers_new_ops_contracts(monkeypatch, tmp_path):
    from hermes_cli import agent_os_status

    root = tmp_path / "LEOS"
    article_0 = root / "article-0" / "_SOUL.md"
    parent = root / "ops" / "contracts" / "C3-hermes-leos-operating-harness-1.yaml"
    child = root / "ops" / "contracts" / "C4-hermes-work-frame-injection-1.yaml"
    article_0.parent.mkdir(parents=True)
    parent.parent.mkdir(parents=True)
    article_0.write_text("PRIVATE ARTICLE 0 BODY\n", encoding="utf-8")
    parent.write_text(
        "contract_id: C3-hermes-leos-operating-harness-1\n"
        "tier: C3\n"
        "tier_name: Results\n"
        "parent_contract_id: C2-hermes-coexistence-1\n"
        "title: PRIVATE HARNESS CONTRACT\n"
        "status: active\n",
        encoding="utf-8",
    )
    child.write_text(
        "contract_id: C4-hermes-work-frame-injection-1\n"
        "tier: C4\n"
        "tier_name: Activities\n"
        "parent_contract_id: C3-hermes-leos-operating-harness-1\n"
        "title: PRIVATE WORK FRAME CONTRACT\n"
        "status: active\n",
        encoding="utf-8",
    )
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

    status = agent_os_status.build_agent_os_status(now=lambda: "2026-06-08T00:00:00Z")

    contracts = {row["id"]: row for row in status["contracts"]}

    assert contracts["C3-hermes-leos-operating-harness-1"]["status"] == "active"
    assert contracts["C4-hermes-work-frame-injection-1"]["status"] == "active"
    assert status["loop"]["contracts"]["cascade_tiers"]["C3"] == 1
    assert status["loop"]["contracts"]["cascade_tiers"]["C4"] == 1
    payload = repr(status)
    assert "PRIVATE HARNESS CONTRACT" not in payload
    assert "PRIVATE WORK FRAME CONTRACT" not in payload


def test_agent_os_status_surfaces_work_frame_injection(monkeypatch, tmp_path):
    from hermes_cli import agent_os_status

    root = tmp_path / "LEOS"
    article_0 = root / "article-0" / "_SOUL.md"
    contracts_dir = root / "ops" / "contracts"
    article_0.parent.mkdir(parents=True)
    contracts_dir.mkdir(parents=True)
    article_0.write_text("PRIVATE ARTICLE 0 BODY\n", encoding="utf-8")
    for contract_id, parent_id in (
        ("C2-hermes-coexistence-1", "C1-polity-1"),
        ("C3-hermes-leos-operating-harness-1", "C2-hermes-coexistence-1"),
        ("C4-hermes-work-frame-injection-1", "C3-hermes-leos-operating-harness-1"),
    ):
        (contracts_dir / f"{contract_id}.yaml").write_text(
            f"contract_id: {contract_id}\n"
            f"parent_contract_id: {parent_id}\n"
            "status: active\n",
            encoding="utf-8",
        )
    hermes_home = tmp_path / ".hermes"
    governor = hermes_home / "plugins" / "leos-governor"
    governor.mkdir(parents=True)
    (governor / "plugin.yaml").write_text(
        "name: leos-governor\n"
        "hooks:\n"
        "  - post_tool_call\n"
        "  - pre_tool_call\n"
        "  - pre_llm_call\n"
        "  - on_session_start\n",
        encoding="utf-8",
    )
    (governor / "__init__.py").write_text("# bridge hook\n", encoding="utf-8")
    (governor / "floor_gate.py").write_text("# floor hook\n", encoding="utf-8")
    (governor / "rehydrate.py").write_text(
        "def _render_work_frame_block(goals):\n"
        "    return 'law_ref active_contract_ref acceptance_ref knowledge_ref risk_status next_governed_act'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LEOS_ROOT", str(root))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
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

    status = agent_os_status.build_agent_os_status(now=lambda: "2026-06-08T00:00:00Z")

    assert status["work_frame"]["status"] == "detected"
    assert status["work_frame"]["active_contract_ref"] == "C4-hermes-work-frame-injection-1"
    assert status["work_frame"]["injected_by"] == "leos-governor:pre_llm_call"
    assert status["work_frame"]["required_fields"] == [
        "law_ref",
        "active_contract_ref",
        "acceptance_ref",
        "knowledge_ref",
        "risk_status",
        "next_governed_act",
    ]
    assert "work_frame_injection_missing" not in status["warnings"]
    assert "work_frame surface inspected" in status["validation"]["evidence"]


def test_agent_os_status_surfaces_contract_gate_visibility(monkeypatch, tmp_path):
    from hermes_cli import agent_os_status

    root = tmp_path / "LEOS"
    article_0 = root / "article-0" / "_SOUL.md"
    article_0.parent.mkdir(parents=True)
    article_0.write_text("PRIVATE ARTICLE 0 BODY\n", encoding="utf-8")
    monkeypatch.setenv("LEOS_ROOT", str(root))
    monkeypatch.setattr(agent_os_status, "_list_skill_records", lambda: [])
    monkeypatch.setattr(agent_os_status, "_disabled_skill_names", lambda: set())
    monkeypatch.setattr(agent_os_status, "_contract_gate_config_enabled", lambda: False)
    monkeypatch.setattr(
        agent_os_status,
        "_runtime_status",
        lambda: {
            "api_mode": "codex_app_server",
            "memory_provider": "leos_knowledge",
            "tool_progress_bridge": "available",
        },
    )

    status = agent_os_status.build_agent_os_status(now=lambda: "2026-06-08T00:00:00Z")

    assert status["contract_gate"]["status"] == "disabled"
    assert status["contract_gate"]["enabled"] is False
    assert status["contract_gate"]["mode"] == "visibility_only"
    assert "contract_cascade_gate_off" in status["warnings"]
    assert "contract gate config inspected" in status["validation"]["evidence"]


def test_agent_os_status_warns_on_noncanonical_contract_tier(monkeypatch, tmp_path):
    from hermes_cli import agent_os_status

    root = tmp_path / "LEOS"
    article_0 = root / "article-0" / "_SOUL.md"
    contract = root / "ops" / "contracts" / "C5-dashboard-1.yaml"
    article_0.parent.mkdir(parents=True)
    contract.parent.mkdir(parents=True)
    article_0.write_text("PRIVATE ARTICLE 0 BODY\n", encoding="utf-8")
    contract.write_text(
        "contract_id: C5-dashboard-1\n"
        "tier: C5\n"
        "tier_name: Results\n"
        "parent_contract_id: C0-polity-1\n"
        "title: PRIVATE NONCANONICAL CONTRACT\n"
        "status: active\n",
        encoding="utf-8",
    )
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

    status = agent_os_status.build_agent_os_status(now=lambda: "2026-06-08T00:00:00Z")

    assert "contract_tier_noncanonical:C5-dashboard-1" in status["warnings"]
    payload = repr(status)
    assert "PRIVATE NONCANONICAL CONTRACT" not in payload


def test_agent_os_status_surfaces_leos_preflight_failures(monkeypatch, tmp_path):
    from hermes_cli import agent_os_status

    root = tmp_path / "LEOS"
    article_0 = root / "article-0" / "_SOUL.md"
    scripts = root / "scripts"
    coexistence = root / "ops" / "contracts" / "C2-hermes-coexistence-1.yaml"
    knowledge = root / "ops" / "contracts" / "C2-knowledge-1.yaml"
    article_0.parent.mkdir(parents=True)
    scripts.mkdir(parents=True)
    coexistence.parent.mkdir(parents=True)
    article_0.write_text("PRIVATE ARTICLE 0 BODY\n", encoding="utf-8")
    (scripts / "leos.py").write_text("# fake cli\n", encoding="utf-8")
    coexistence.write_text(
        "contract_id: C2-hermes-coexistence-1\nstatus: active\n",
        encoding="utf-8",
    )
    knowledge.write_text("contract_id: C2-knowledge-1\nstatus: active\n", encoding="utf-8")

    def fake_run(root_path, args, timeout=8):
        assert root_path == root
        if args == ("ledger", "verify"):
            return {"available": True, "ok": True, "exit_code": 0, "output": "ledger verify: PASS"}
        if args == ("watchdog", "check"):
            return {
                "available": True,
                "ok": False,
                "exit_code": 1,
                "output": "[✗] constitution_sha256\n[✗] enforcement_code_anchor\n",
            }
        if args == ("anchor", "verify"):
            return {
                "available": True,
                "ok": True,
                "exit_code": 0,
                "output": "PM-signed head attests entries 1..227 of 375",
            }
        if args == ("contract", "validate", "--json"):
            return {
                "available": True,
                "ok": False,
                "exit_code": 1,
                "output": (
                    '{"checked":31,"ok":false,"violations":['
                    '{"contract_id":"C5-dashboard-1"},'
                    '{"contract_id":"C6-engine-live-1"}]}'
                ),
            }
        raise AssertionError(args)

    monkeypatch.setenv("LEOS_ROOT", str(root))
    monkeypatch.setattr(agent_os_status, "_run_leos_cli", fake_run)
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

    status = agent_os_status.build_agent_os_status(now=lambda: "2026-06-08T00:00:00Z")

    assert status["preflight"]["status"] == "failing"
    assert status["preflight"]["checks"]["watchdog"]["ok"] is False
    assert status["preflight"]["checks"]["contract_validate"]["violations"] == [
        "C5-dashboard-1",
        "C6-engine-live-1",
    ]
    assert status["preflight"]["signed_head"]["sealed_entries"] == 227
    assert status["preflight"]["signed_head"]["ledger_entries"] == 375
    assert "leos_watchdog_failing" in status["warnings"]
    assert "contract_validate_failing:C5-dashboard-1" in status["warnings"]
    assert "contract_validate_failing:C6-engine-live-1" in status["warnings"]
    assert "signed_head_not_current:227/375" in status["warnings"]
    assert "governance_preflight_not_clean" in status["warnings"]
