from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

REQUIRED_LEOS_SKILLS = (
    "leos-constitution",
    "leos-contract",
    "leos-dashboard-work",
)

EXPECTED_CONTRACT_IDS = (
    "agent-console-dashboard",
    "memory",
    "governance",
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


def build_agent_os_status(now: Callable[[], str] | None = None) -> dict[str, Any]:
    checked_at = (now or _utc_now)()
    root = _resolve_leos_root()
    warnings: list[str] = []

    detected = root.exists()
    if not detected:
        warnings.append("leos_root_missing")

    constitution = _file_record(root / "constitution" / "constitution.md")
    if detected and not constitution["exists"]:
        warnings.append("constitution_missing")

    contracts: list[dict[str, Any]] = []
    contracts_dir = root / "contracts"
    if detected and contracts_dir.is_dir():
        for contract_id in EXPECTED_CONTRACT_IDS:
            rec = _file_record(contracts_dir / f"{contract_id}.yaml")
            if rec["exists"]:
                rec["id"] = contract_id
                rec["status"] = (
                    "active" if contract_id == "agent-console-dashboard" else "available"
                )
                contracts.append(rec)
    elif detected:
        warnings.append("contracts_missing")

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

    return {
        "agent_os": "leos",
        "detected": detected,
        "root": str(root),
        "constitution": constitution,
        "contracts": contracts,
        "skills": skills,
        "runtime": runtime,
        "validation": {
            "last_checked_at": checked_at,
            "evidence": evidence,
        },
        "warnings": warnings,
    }
