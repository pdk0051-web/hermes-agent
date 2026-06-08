from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REQUIRED_LEOS_SKILLS = (
    "leos-constitution",
    "leos-contract",
    "leos-dashboard-work",
)

CONSTITUTION_CANDIDATES = (
    ("constitution", "constitution.md"),
    ("article-0", "_SOUL.md"),
    ("article-0", "_manifest.yaml"),
)

CONTRACT_CANDIDATES = (
    (
        "agent-console-dashboard",
        ("contracts", "agent-console-dashboard.yaml"),
        "active",
    ),
    ("memory", ("contracts", "memory.yaml"), "available"),
    ("governance", ("contracts", "governance.yaml"), "available"),
    (
        "C2-hermes-coexistence-1",
        ("ops", "contracts", "C2-hermes-coexistence-1.yaml"),
        None,
    ),
    ("C5-dashboard-1", ("ops", "contracts", "C5-dashboard-1.yaml"), None),
    ("C2-knowledge-1", ("ops", "contracts", "C2-knowledge-1.yaml"), None),
    (
        "C3-enforced-governance-1",
        ("ops", "contracts", "C3-enforced-governance-1.yaml"),
        None,
    ),
    ("C0-polity-1", ("ops", "contracts", "C0-polity-1.yaml"), None),
)

CANONICAL_CONTRACT_TIERS = frozenset({"C0", "C1", "C2", "C3", "C4"})

LAW_REF_CANDIDATES = (
    ("article_0_soul", ("article-0", "_SOUL.md")),
    ("article_0_manifest", ("article-0", "_manifest.yaml")),
    ("legacy_constitution", ("constitution", "constitution.md")),
    ("prd_index", ("prd", "INDEX.md")),
)

KNOWLEDGE_REF_CANDIDATES = (
    ("knowledge_readme", ("ops", "knowledge", "README.md")),
    ("knowledge_map", ("ops", "knowledge", "index", "knowledge-map.md")),
    ("knowledge_events", ("ops", "knowledge", "index", "knowledge-events.jsonl")),
)

KNOWLEDGE_BUCKETS = (
    ("evergreen", ("ops", "knowledge", "evergreen")),
    ("fleeting", ("ops", "knowledge", "fleeting")),
    ("sources", ("ops", "knowledge", "sources")),
)

LEOS_GOVERNOR_NAME = "leos-governor"
LEOS_GOVERNOR_REQUIRED_HOOKS = (
    "post_tool_call",
    "pre_tool_call",
    "pre_llm_call",
    "on_session_start",
)
LEOS_GOVERNOR_FILES = (
    "plugin.yaml",
    "__init__.py",
    "floor_gate.py",
    "rehydrate.py",
)
LEOS_GOVERNOR_HOOK_FILES = {
    "post_tool_call": "__init__.py",
    "pre_tool_call": "floor_gate.py",
    "pre_llm_call": "rehydrate.py",
    "on_session_start": "rehydrate.py",
}
WORK_FRAME_CONTRACT_ID = "C4-hermes-work-frame-injection-1"
WORK_FRAME_REQUIRED_FIELDS = (
    "law_ref",
    "active_contract_ref",
    "acceptance_ref",
    "knowledge_ref",
    "risk_status",
    "next_governed_act",
)
WORK_FRAME_RENDERER_MARKER = "_render_work_frame_block"

CITIZEN_DIRS = (
    ("citizens", "constitutional"),
    ("citizens", "operating"),
    ("citizens", "shared-services"),
    ("citizens", "subsidiaries"),
)
CITIZEN_REQUIRED_FIELDS = (
    "citizen_id",
    "class",
    "citizenship",
    "authority",
    "action_trust",
)


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _resolve_leos_root() -> Path:
    raw = os.environ.get("LEOS_ROOT")
    if raw and raw.strip():
        return Path(raw).expanduser()
    return Path.home() / "LEOS"


def _resolve_hermes_home() -> Path:
    raw = os.environ.get("HERMES_HOME")
    if raw and raw.strip():
        return Path(raw).expanduser()
    return Path.home() / ".hermes"


def _hash_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _format_mtime(path: Path) -> str:
    return (
        datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _file_record(path: Path) -> dict[str, Any]:
    exists = path.is_file()
    record: dict[str, Any] = {
        "path": str(path),
        "exists": exists,
        "sha256": None,
        "mtime": None,
    }
    if not exists:
        return record
    record["sha256"] = _hash_file(path)
    record["mtime"] = _format_mtime(path)
    return record


def _named_file_record(record_id: str, path: Path) -> dict[str, Any]:
    rec = _file_record(path)
    rec["id"] = record_id
    return rec


def _yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _first_existing_file(root: Path, candidates: tuple[tuple[str, ...], ...]) -> Path:
    fallback = root.joinpath(*candidates[0])
    for parts in candidates:
        path = root.joinpath(*parts)
        if path.is_file():
            return path
    return fallback


def _yaml_scalar(path: Path, key: str) -> str | None:
    if not path.is_file():
        return None
    prefix = f"{key}:"
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                if not line.startswith(prefix):
                    continue
                value = line[len(prefix) :].strip()
                if not value:
                    return None
                if value[0] in {"'", '"'} and value[-1:] == value[0]:
                    value = value[1:-1]
                return value
    except OSError:
        return None
    return None


def _string_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        default = value.get("default")
        return _string_value(default) if default is not None else ""
    if isinstance(value, (list, tuple)):
        return ", ".join(_string_value(item) for item in value if _string_value(item))
    return str(value)


def _contract_record(
    contract_id: str,
    path: Path,
    fallback_status: str | None,
) -> dict[str, Any] | None:
    rec = _file_record(path)
    if not rec["exists"]:
        return None
    rec["id"] = _yaml_scalar(path, "contract_id") or contract_id
    rec["status"] = _yaml_scalar(path, "status") or fallback_status or "available"
    return rec


def _contract_records(root: Path, detected: bool) -> list[dict[str, Any]]:
    if not detected:
        return []
    records: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for contract_id, parts, fallback_status in CONTRACT_CANDIDATES:
        path = root.joinpath(*parts)
        rec = _contract_record(contract_id, path, fallback_status)
        if rec is not None:
            records.append(rec)
            seen_paths.add(path)
    for directory in (root / "contracts", root / "ops" / "contracts"):
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            if path in seen_paths:
                continue
            rec = _contract_record(path.stem, path, None)
            if rec is not None:
                records.append(rec)
                seen_paths.add(path)
    return records


def _is_active_contract(contract: dict[str, Any]) -> bool:
    return str(contract.get("status") or "").strip().lower() == "active"


def _contract_tier(contract_id: str) -> str | None:
    if len(contract_id) < 2 or contract_id[0] != "C" or not contract_id[1].isdigit():
        return None
    tier = contract_id.split("-", 1)[0]
    return tier if tier[1:].isdigit() else None


def _count_visible_files(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(
        1
        for path in directory.rglob("*")
        if path.is_file() and not path.name.startswith(".")
    )


def _law_axis_status(root: Path, detected: bool) -> dict[str, Any]:
    refs: list[dict[str, Any]] = []
    if detected:
        for ref_id, parts in LAW_REF_CANDIDATES:
            rec = _named_file_record(ref_id, root.joinpath(*parts))
            if rec["exists"]:
                refs.append(rec)
        current_prds = sorted((root / "prd" / "current").glob("*_in_force.md"))
        for path in current_prds:
            refs.append(_named_file_record("prd_in_force", path))
    in_force_prd_count = sum(1 for ref in refs if ref["id"] == "prd_in_force")
    status = "visible" if refs else "missing"
    warnings = ["law_axis_missing"] if detected and status == "missing" else []
    return {
        "status": status,
        "refs": refs,
        "in_force_prd_count": in_force_prd_count,
        "warnings": warnings,
    }


def _contract_axis_status(
    detected: bool,
    contracts: list[dict[str, Any]],
) -> dict[str, Any]:
    cascade_tiers: dict[str, int] = {}
    active_count = 0
    coexistence_active = False
    knowledge_active = False
    warnings: list[str] = []
    for contract in contracts:
        contract_id = str(contract.get("id") or "")
        if _is_active_contract(contract):
            active_count += 1
        tier = _contract_tier(contract_id)
        if tier is not None:
            cascade_tiers[tier] = cascade_tiers.get(tier, 0) + 1
            if tier not in CANONICAL_CONTRACT_TIERS:
                warnings.append(f"contract_tier_noncanonical:{contract_id}")
        if contract_id == "C2-hermes-coexistence-1" and _is_active_contract(contract):
            coexistence_active = True
        if contract_id == "C2-knowledge-1" and _is_active_contract(contract):
            knowledge_active = True

    status = "visible" if contracts else "missing"
    if detected and status == "missing":
        warnings.append("contract_axis_missing")
    if detected and contracts and not coexistence_active:
        warnings.append("coexistence_contract_missing_or_inactive")
    if detected and contracts and not knowledge_active:
        warnings.append("knowledge_contract_missing_or_inactive")
    return {
        "status": status,
        "active_count": active_count,
        "cascade_tiers": cascade_tiers,
        "coexistence_active": coexistence_active,
        "knowledge_active": knowledge_active,
        "warnings": warnings,
    }


def _knowledge_axis_status(root: Path, detected: bool) -> dict[str, Any]:
    refs: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    if detected:
        for ref_id, parts in KNOWLEDGE_REF_CANDIDATES:
            rec = _named_file_record(ref_id, root.joinpath(*parts))
            if rec["exists"]:
                refs.append(rec)
        for bucket, parts in KNOWLEDGE_BUCKETS:
            counts[bucket] = _count_visible_files(root.joinpath(*parts))
    else:
        counts = {bucket: 0 for bucket, _parts in KNOWLEDGE_BUCKETS}
    status = "visible" if refs or any(counts.values()) else "missing"
    warnings = ["knowledge_axis_missing"] if detected and status == "missing" else []
    return {
        "status": status,
        "refs": refs,
        "counts": counts,
        "warnings": warnings,
    }


def _loop_status(
    root: Path,
    detected: bool,
    contracts: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "law": _law_axis_status(root, detected),
        "contracts": _contract_axis_status(detected, contracts),
        "knowledge": _knowledge_axis_status(root, detected),
    }


def _leos_governor_status() -> dict[str, Any]:
    plugin_dir = _resolve_hermes_home() / "plugins" / LEOS_GOVERNOR_NAME
    manifest = plugin_dir / "plugin.yaml"
    manifest_data = _yaml_mapping(manifest)
    declared_hooks = manifest_data.get("hooks") or []
    if not isinstance(declared_hooks, list):
        declared_hooks = []
    hooks_declared = [str(hook) for hook in declared_hooks if str(hook).strip()]
    files = {name: _file_record(plugin_dir / name) for name in LEOS_GOVERNOR_FILES}
    hook_files = {
        hook: bool(files[file_name]["exists"])
        for hook, file_name in LEOS_GOVERNOR_HOOK_FILES.items()
    }
    installed = bool(files["plugin.yaml"]["exists"] and files["__init__.py"]["exists"])
    hooks_ready = all(hook in hooks_declared for hook in LEOS_GOVERNOR_REQUIRED_HOOKS)
    files_ready = all(hook_files.values())
    return {
        "name": LEOS_GOVERNOR_NAME,
        "plugin_path": str(plugin_dir),
        "installed": installed,
        "active": installed and hooks_ready and files_ready,
        "version": _string_value(manifest_data.get("version")),
        "hooks_declared": hooks_declared,
        "required_hooks": list(LEOS_GOVERNOR_REQUIRED_HOOKS),
        "hook_files": hook_files,
        "files": files,
    }


def _file_contains(path: Path, needle: str) -> bool:
    if not path.is_file():
        return False
    try:
        return needle in path.read_text(encoding="utf-8")
    except OSError:
        return False


def _active_contract_exists(
    contracts: list[dict[str, Any]],
    contract_id: str,
) -> bool:
    return any(
        str(contract.get("id") or "") == contract_id and _is_active_contract(contract)
        for contract in contracts
    )


def _work_frame_status(
    detected: bool,
    contracts: list[dict[str, Any]],
    bridge: dict[str, Any],
) -> dict[str, Any]:
    warnings: list[str] = []
    contract_active = detected and _active_contract_exists(contracts, WORK_FRAME_CONTRACT_ID)
    plugin_dir = Path(str(bridge.get("plugin_path") or ""))
    rehydrate_path = plugin_dir / "rehydrate.py"
    renderer_declared = _file_contains(rehydrate_path, WORK_FRAME_RENDERER_MARKER)
    field_markers = {
        field: _file_contains(rehydrate_path, field)
        for field in WORK_FRAME_REQUIRED_FIELDS
    }

    if detected and not contract_active:
        warnings.append("work_frame_contract_missing_or_inactive")
    if not bridge.get("active"):
        warnings.append("work_frame_bridge_missing")
    if bridge.get("active") and not renderer_declared:
        warnings.append("work_frame_renderer_missing")
    for field, present in field_markers.items():
        if renderer_declared and not present:
            warnings.append(f"work_frame_required_field_missing:{field}")

    if contract_active and bridge.get("active") and renderer_declared and all(field_markers.values()):
        status = "detected"
    elif contract_active or renderer_declared or bridge.get("active"):
        status = "declared"
    else:
        status = "missing"

    return {
        "status": status,
        "active_contract_ref": WORK_FRAME_CONTRACT_ID if contract_active else None,
        "injected_by": "leos-governor:pre_llm_call" if renderer_declared else None,
        "required_fields": list(WORK_FRAME_REQUIRED_FIELDS),
        "renderer_declared": renderer_declared,
        "field_markers": field_markers,
        "warnings": warnings,
    }


def _contract_gate_config_enabled() -> bool:
    try:
        from hermes_cli.config import cfg_get, load_config

        return bool(cfg_get(load_config(), "contract_gate", "enabled", default=False))
    except Exception:
        return False


def _contract_gate_status() -> dict[str, Any]:
    enabled = _contract_gate_config_enabled()
    warnings: list[str] = []
    if not enabled:
        warnings.append("contract_cascade_gate_off")
    return {
        "status": "enabled" if enabled else "disabled",
        "enabled": enabled,
        "mode": "closure_gate_active_fail_open" if enabled else "visibility_only",
        "source": "config.contract_gate.enabled",
        "fail_open": True,
        "warnings": warnings,
    }


def _citizen_record(path: Path) -> dict[str, Any]:
    data = _yaml_mapping(path)
    rec = _file_record(path)
    citizen_id = _string_value(data.get("citizen_id")) or path.stem.lower()
    rec.update(
        {
            "id": citizen_id,
            "class": _string_value(data.get("class")),
            "citizenship": _string_value(data.get("citizenship")),
            "authority": _string_value(data.get("authority")),
            "action_trust": _string_value(data.get("action_trust")),
            "role_title": _string_value(data.get("role_title")),
            "source": path.parent.name,
            "harnessed": all(_string_value(data.get(field)) for field in CITIZEN_REQUIRED_FIELDS),
        },
    )
    return rec


def _citizen_status(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for parts in CITIZEN_DIRS:
        directory = root.joinpath(*parts)
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.yaml")):
            records.append(_citizen_record(path))
    return records


def _list_skill_records() -> list[dict[str, Any]]:
    try:
        from tools.skills_tool import _find_all_skills

        return list(_find_all_skills(skip_disabled=True))
    except Exception:
        return []


def _disabled_skill_names() -> set[str]:
    try:
        from hermes_cli.config import load_config
        from hermes_cli.skills_config import get_disabled_skills

        return set(get_disabled_skills(load_config()))
    except Exception:
        return set()


def _skill_status(
    name: str,
    records: list[dict[str, Any]],
    disabled: set[str],
) -> dict[str, Any]:
    installed = any(str(row.get("name") or "") == name for row in records)
    return {
        "name": name,
        "installed": installed,
        "enabled": installed and name not in disabled,
        "source": "hermes-profile" if installed else "missing",
    }


def _runtime_status() -> dict[str, Any]:
    try:
        from hermes_cli.config import cfg_get, load_config

        config = load_config()
        api_mode = cfg_get(config, "model", "api_mode", default="") or cfg_get(
            config,
            "api_mode",
            default="",
        )
        memory_provider = cfg_get(config, "memory", "provider", default="") or ""
    except Exception:
        api_mode = ""
        memory_provider = ""
    return {
        "api_mode": str(api_mode),
        "memory_provider": str(memory_provider),
        "tool_progress_bridge": "available",
    }


def _run_leos_cli(
    root: Path,
    args: tuple[str, ...],
    timeout: int = 8,
) -> dict[str, Any]:
    script = root / "scripts" / "leos.py"
    if not script.is_file():
        return {
            "available": False,
            "ok": None,
            "exit_code": None,
            "output": "",
            "error": "leos_cli_missing",
        }
    try:
        proc = subprocess.run(
            ["python3", str(script), *args],
            cwd=str(root),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "available": True,
            "ok": False,
            "exit_code": None,
            "output": "",
            "error": "timeout",
        }
    except OSError as exc:
        return {
            "available": True,
            "ok": False,
            "exit_code": None,
            "output": "",
            "error": f"{type(exc).__name__}: {exc}",
        }
    output = (proc.stdout or "") + (proc.stderr or "")
    return {
        "available": True,
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "output": output.strip(),
        "error": "",
    }


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _failed_watchdog_checks(output: str) -> list[str]:
    failed: list[str] = []
    for line in output.splitlines():
        if "[✗]" not in line and "[x]" not in line.lower():
            continue
        tail = line.split("]", 1)[-1].strip()
        check = tail.split(":", 1)[0].strip()
        if check:
            failed.append(check)
    return failed


def _contract_validation_violations(output: str) -> list[str]:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return []
    violations = payload.get("violations")
    if not isinstance(violations, list):
        return []
    ids: list[str] = []
    for violation in violations:
        if not isinstance(violation, dict):
            continue
        contract_id = _string_value(violation.get("contract_id"))
        if contract_id:
            ids.append(contract_id)
    return ids


def _signed_head_coverage(output: str) -> dict[str, int | None]:
    match = re.search(r"entries\s+1\.\.(\d+)\s+of\s+(\d+)", output)
    if not match:
        return {"sealed_entries": None, "ledger_entries": None}
    return {
        "sealed_entries": int(match.group(1)),
        "ledger_entries": int(match.group(2)),
    }


def _check_record(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": bool(result.get("available")),
        "ok": result.get("ok"),
        "exit_code": result.get("exit_code"),
        "summary": _first_line(_string_value(result.get("output") or result.get("error"))),
    }


def _leos_preflight_status(root: Path, detected: bool) -> dict[str, Any]:
    if not detected:
        return {
            "status": "missing",
            "checks": {},
            "signed_head": {"sealed_entries": None, "ledger_entries": None},
            "warnings": [],
        }

    ledger = _run_leos_cli(root, ("ledger", "verify"))
    watchdog = _run_leos_cli(root, ("watchdog", "check"))
    anchor = _run_leos_cli(root, ("anchor", "verify"))
    contract_validate = _run_leos_cli(root, ("contract", "validate", "--json"))

    checks = {
        "ledger": _check_record(ledger),
        "watchdog": _check_record(watchdog),
        "anchor": _check_record(anchor),
        "contract_validate": _check_record(contract_validate),
    }
    checks["watchdog"]["failed_checks"] = _failed_watchdog_checks(
        _string_value(watchdog.get("output")),
    )
    checks["contract_validate"]["violations"] = _contract_validation_violations(
        _string_value(contract_validate.get("output")),
    )

    signed_head = _signed_head_coverage(_string_value(anchor.get("output")))
    warnings: list[str] = []
    if ledger.get("ok") is False:
        warnings.append("leos_ledger_verify_failing")
    if watchdog.get("ok") is False:
        warnings.append("leos_watchdog_failing")
    if anchor.get("ok") is False:
        warnings.append("leos_anchor_verify_failing")
    if contract_validate.get("ok") is False:
        violations = checks["contract_validate"]["violations"]
        if violations:
            warnings.extend(f"contract_validate_failing:{cid}" for cid in violations)
        else:
            warnings.append("contract_validate_failing")
    sealed = signed_head["sealed_entries"]
    total = signed_head["ledger_entries"]
    if sealed is not None and total is not None and sealed < total:
        warnings.append(f"signed_head_not_current:{sealed}/{total}")

    available = [row for row in checks.values() if row["available"]]
    if not available:
        status = "unavailable"
    elif warnings:
        status = "failing"
        warnings.append("governance_preflight_not_clean")
    elif all(row["ok"] for row in available):
        status = "passing"
    else:
        status = "unknown"

    return {
        "status": status,
        "checks": checks,
        "signed_head": signed_head,
        "warnings": warnings,
    }


def build_agent_os_status(now: Callable[[], str] | None = None) -> dict[str, Any]:
    checked_at = (now or _utc_now)()
    root = _resolve_leos_root()
    warnings: list[str] = []

    detected = root.exists()
    if not detected:
        warnings.append("leos_root_missing")

    constitution = _file_record(_first_existing_file(root, CONSTITUTION_CANDIDATES))
    if detected and not constitution["exists"]:
        warnings.append("constitution_missing")

    contracts = _contract_records(root, detected)
    if detected and not contracts:
        warnings.append("contracts_missing")

    loop = _loop_status(root, detected, contracts)
    for axis in loop.values():
        warnings.extend(axis["warnings"])

    preflight = _leos_preflight_status(root, detected)
    warnings.extend(preflight["warnings"])

    bridge = _leos_governor_status()
    if not bridge["installed"]:
        warnings.append("leos_governor_missing")
    elif not bridge["active"]:
        warnings.append("leos_governor_incomplete")
        declared = set(bridge["hooks_declared"])
        for hook in LEOS_GOVERNOR_REQUIRED_HOOKS:
            if hook not in declared:
                warnings.append(f"leos_governor_hook_missing:{hook}")
        for hook, exists in bridge["hook_files"].items():
            if not exists:
                warnings.append(f"leos_governor_hook_file_missing:{hook}")

    work_frame = _work_frame_status(detected, contracts, bridge)
    warnings.extend(work_frame["warnings"])

    contract_gate = _contract_gate_status()
    warnings.extend(contract_gate["warnings"])

    citizens = _citizen_status(root) if detected else []
    if detected and not citizens:
        warnings.append("citizens_missing")
    for citizen in citizens:
        if not citizen["harnessed"]:
            warnings.append(f"citizen_manifest_incomplete:{citizen['id']}")

    skill_records = _list_skill_records()
    disabled = _disabled_skill_names()
    skills = [_skill_status(name, skill_records, disabled) for name in REQUIRED_LEOS_SKILLS]
    for skill in skills:
        if not skill["installed"]:
            warnings.append(f"skill_missing:{skill['name']}")
        elif not skill["enabled"]:
            warnings.append(f"skill_disabled:{skill['name']}")

    runtime = _runtime_status()
    if runtime["memory_provider"] != "leos_knowledge":
        warnings.append("memory_provider_not_leos_knowledge")

    evidence = []
    if constitution["sha256"]:
        evidence.append("constitution file hashed")
    if contracts:
        evidence.append("contract file hashed")
    if any(skill["installed"] for skill in skills):
        evidence.append("LEOS adapter skill state inspected")
    if loop["law"]["status"] == "visible":
        evidence.append("law axis file state inspected")
    if loop["contracts"]["status"] == "visible":
        evidence.append("contract loop state inspected")
    if loop["knowledge"]["status"] == "visible":
        evidence.append("knowledge axis file state inspected")
    if bridge["installed"]:
        evidence.append("leos-governor plugin manifest hashed")
    if bridge["active"]:
        evidence.append("leos-governor hook files hashed (declared readiness only)")
    if citizens:
        evidence.append("citizen manifests inspected")
    if preflight["status"] != "missing":
        evidence.append("LEOS read-only preflight commands inspected")
    if work_frame["status"] != "missing":
        evidence.append("work_frame surface inspected")
    evidence.append("contract gate config inspected")

    return {
        "agent_os": "leos",
        "detected": detected,
        "root": str(root),
        "constitution": constitution,
        "contracts": contracts,
        "loop": loop,
        "preflight": preflight,
        "bridge": bridge,
        "work_frame": work_frame,
        "contract_gate": contract_gate,
        "citizens": citizens,
        "skills": skills,
        "runtime": runtime,
        "validation": {
            "last_checked_at": checked_at,
            "evidence": evidence,
        },
        "warnings": warnings,
    }
