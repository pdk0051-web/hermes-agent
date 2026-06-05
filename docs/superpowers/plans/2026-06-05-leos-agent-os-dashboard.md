# LEOS Agent OS Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a read-only LEOS Agent OS status surface to Hermes and show it in the Agent Console dashboard.

**Architecture:** Keep LEOS doctrine canonical under `LEOS_ROOT` or `~/LEOS`. Add a focused Python status builder that hashes canonical files, inspects Hermes skill/runtime wiring, and returns warnings without reading private file contents into the response. Expose it through `GET /api/agent-os/status`, then add a typed React dashboard page and nav item.

**Tech Stack:** Python 3, FastAPI, pytest, React 19, TypeScript, Vite, `@nous-research/ui`, lucide-react.

---

## File Structure

- Create `hermes_cli/agent_os_status.py`
  - Owns LEOS root resolution, file hashing, contract discovery, required skill inspection, runtime summary, and warning generation.
  - No FastAPI imports. This keeps it unit-testable.
- Modify `hermes_cli/web_server.py`
  - Adds `GET /api/agent-os/status` that delegates to `build_agent_os_status()`.
  - Does not inline status-building logic.
- Create `tests/hermes_cli/test_agent_os_status.py`
  - Unit-tests missing-root, present-root, hashing, skill state, and content non-leakage.
- Modify `tests/hermes_cli/test_web_server.py`
  - Adds a small endpoint smoke test using the existing authenticated `TestClient`.
- Modify `web/src/lib/api.ts`
  - Adds TypeScript interfaces and `api.getAgentOsStatus()`.
- Create `web/src/pages/AgentOsPage.tsx`
  - Renders Agent OS summary, constitution, contracts, skills, runtime, validation, and warnings.
- Modify `web/src/App.tsx`
  - Adds route `/agent-os` and a sidebar nav item.
- Create `/Users/leo/.hermes/skills/leos-constitution/SKILL.md`
  - Local Hermes adapter skill that loads LEOS constitution from `LEOS_ROOT` or `~/LEOS`.
- Create `/Users/leo/.hermes/skills/leos-contract/SKILL.md`
  - Local Hermes adapter skill that requires an explicit LEOS contract before scoped work.
- Create `/Users/leo/.hermes/skills/leos-dashboard-work/SKILL.md`
  - Local Hermes adapter skill for Agent Console dashboard work governed by `agent-console-dashboard.yaml`.

Adapter skills live in the active Hermes profile, not in the Hermes repo, because `tools.skills_tool` treats `~/.hermes/skills` as the single runtime source of truth after bundled skill seeding.

---

### Task 1: Backend Status Builder

**Files:**
- Create: `hermes_cli/agent_os_status.py`
- Test: `tests/hermes_cli/test_agent_os_status.py`

- [ ] **Step 1: Write failing tests for missing LEOS root**

Create `tests/hermes_cli/test_agent_os_status.py` with:

```python
from __future__ import annotations

from pathlib import Path


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
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/hermes_cli/test_agent_os_status.py::test_agent_os_status_missing_root -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'hermes_cli.agent_os_status'`.

- [ ] **Step 3: Add minimal status builder**

Create `hermes_cli/agent_os_status.py`:

```python
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
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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
    stat = path.stat()
    record["sha256"] = _hash_file(path)
    record["mtime"] = datetime.fromtimestamp(stat.st_mtime, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
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


def _skill_status(name: str, records: list[dict[str, Any]], disabled: set[str]) -> dict[str, Any]:
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
        api_mode = cfg_get(config, "model", "api_mode", default="") or cfg_get(config, "api_mode", default="")
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
                rec["status"] = "active" if contract_id == "agent-console-dashboard" else "available"
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
```

- [ ] **Step 4: Run missing-root test**

Run:

```bash
pytest tests/hermes_cli/test_agent_os_status.py::test_agent_os_status_missing_root -q
```

Expected: PASS.

- [ ] **Step 5: Add present-root and non-leakage tests**

Append to `tests/hermes_cli/test_agent_os_status.py`:

```python
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
```

- [ ] **Step 6: Run backend unit tests**

Run:

```bash
pytest tests/hermes_cli/test_agent_os_status.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit backend status builder**

Run:

```bash
git add hermes_cli/agent_os_status.py tests/hermes_cli/test_agent_os_status.py
git commit -m "Expose LEOS Agent OS status as read-only state" \
  -m "Constraint: LEOS doctrine remains canonical outside the Hermes repo." \
  -m "Confidence: high" \
  -m "Scope-risk: narrow" \
  -m "Directive: Keep this status builder read-only; do not include doctrine file contents in responses." \
  -m "Tested: pytest tests/hermes_cli/test_agent_os_status.py -q"
```

---

### Task 2: Dashboard Endpoint

**Files:**
- Modify: `hermes_cli/web_server.py`
- Modify: `tests/hermes_cli/test_web_server.py`

- [ ] **Step 1: Write failing endpoint smoke test**

Add this method inside `TestWebServerEndpoints` in `tests/hermes_cli/test_web_server.py`:

```python
    def test_get_agent_os_status(self, monkeypatch, tmp_path):
        import hermes_cli.agent_os_status as agent_os_status

        root = tmp_path / "LEOS"
        (root / "constitution").mkdir(parents=True)
        (root / "constitution" / "constitution.md").write_text("constitution\n", encoding="utf-8")
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

        resp = self.client.get("/api/agent-os/status")

        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_os"] == "leos"
        assert data["detected"] is True
        assert data["constitution"]["exists"] is True
        assert "PRIVATE" not in repr(data)
```

- [ ] **Step 2: Run endpoint test to verify it fails**

Run:

```bash
pytest tests/hermes_cli/test_web_server.py::TestWebServerEndpoints::test_get_agent_os_status -q
```

Expected: FAIL with status `404`.

- [ ] **Step 3: Add endpoint**

In `hermes_cli/web_server.py`, add this near the existing status/system endpoint group after `get_status()` or before `/api/system/stats`:

```python
@app.get("/api/agent-os/status")
async def get_agent_os_status():
    from hermes_cli.agent_os_status import build_agent_os_status

    return build_agent_os_status()
```

- [ ] **Step 4: Run endpoint test**

Run:

```bash
pytest tests/hermes_cli/test_web_server.py::TestWebServerEndpoints::test_get_agent_os_status -q
```

Expected: PASS.

- [ ] **Step 5: Run focused backend tests together**

Run:

```bash
pytest tests/hermes_cli/test_agent_os_status.py tests/hermes_cli/test_web_server.py::TestWebServerEndpoints::test_get_agent_os_status -q
```

Expected: PASS.

- [ ] **Step 6: Commit endpoint**

Run:

```bash
git add hermes_cli/web_server.py tests/hermes_cli/test_web_server.py
git commit -m "Surface LEOS Agent OS status through the dashboard API" \
  -m "Constraint: Dashboard API must stay read-only for Agent OS status." \
  -m "Confidence: high" \
  -m "Scope-risk: narrow" \
  -m "Directive: Keep status calculation delegated to hermes_cli.agent_os_status." \
  -m "Tested: pytest tests/hermes_cli/test_agent_os_status.py tests/hermes_cli/test_web_server.py::TestWebServerEndpoints::test_get_agent_os_status -q"
```

---

### Task 3: Frontend API Types

**Files:**
- Modify: `web/src/lib/api.ts`

- [ ] **Step 1: Add Agent OS types**

Add these interfaces near the other exported response interfaces in `web/src/lib/api.ts`:

```ts
export interface AgentOsFileStatus {
  path: string;
  exists: boolean;
  sha256: string | null;
  mtime: string | null;
}

export interface AgentOsContractStatus extends AgentOsFileStatus {
  id: string;
  status: string;
}

export interface AgentOsSkillStatus {
  name: string;
  installed: boolean;
  enabled: boolean;
  source: string;
}

export interface AgentOsRuntimeStatus {
  api_mode: string;
  memory_provider: string;
  tool_progress_bridge: string;
}

export interface AgentOsValidationStatus {
  last_checked_at: string;
  evidence: string[];
}

export interface AgentOsStatusResponse {
  agent_os: "leos";
  detected: boolean;
  root: string;
  constitution: AgentOsFileStatus;
  contracts: AgentOsContractStatus[];
  skills: AgentOsSkillStatus[];
  runtime: AgentOsRuntimeStatus;
  validation: AgentOsValidationStatus;
  warnings: string[];
}
```

- [ ] **Step 2: Add API client method**

Add this method near the existing status/system methods in the `api` object:

```ts
  getAgentOsStatus: () =>
    fetchJSON<AgentOsStatusResponse>("/api/agent-os/status"),
```

- [ ] **Step 3: Run TypeScript check**

Run:

```bash
cd web && npm run build
```

Expected: build passes or fails only because `AgentOsStatusResponse` is unused. If unused-locals is enforced, Task 4 will use it.

---

### Task 4: Agent OS Dashboard Page

**Files:**
- Create: `web/src/pages/AgentOsPage.tsx`

- [ ] **Step 1: Create page component**

Create `web/src/pages/AgentOsPage.tsx`:

```tsx
import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  FileCheck2,
  FileText,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  XCircle,
} from "lucide-react";
import { Badge } from "@nous-research/ui/ui/components/badge";
import { Button } from "@nous-research/ui/ui/components/button";
import { Card, CardContent, CardHeader, CardTitle } from "@nous-research/ui/ui/components/card";
import { Spinner } from "@nous-research/ui/ui/components/spinner";
import { api, type AgentOsStatusResponse } from "@/lib/api";
import { PluginSlot } from "@/plugins";

function shortHash(value: string | null): string {
  return value ? value.slice(0, 12) : "not available";
}

function statusTone(ok: boolean): "success" | "destructive" {
  return ok ? "success" : "destructive";
}

function StateBadge({ ok, label }: { ok: boolean; label: string }) {
  return <Badge tone={statusTone(ok)}>{label}</Badge>;
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid gap-1 border-b border-border/60 py-2 last:border-b-0 sm:grid-cols-[9rem_1fr]">
      <dt className="text-xs text-text-tertiary">{label}</dt>
      <dd className="min-w-0 break-words font-mono-ui text-xs text-text-secondary">{value || "not set"}</dd>
    </div>
  );
}

export default function AgentOsPage() {
  const [status, setStatus] = useState<AgentOsStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    setError(null);
    api
      .getAgentOsStatus()
      .then(setStatus)
      .catch((err) => setError(String(err)))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  const installedSkills = useMemo(
    () => status?.skills.filter((skill) => skill.installed).length ?? 0,
    [status],
  );

  if (loading && !status) {
    return (
      <div className="flex min-h-64 items-center justify-center text-text-secondary">
        <Spinner />
      </div>
    );
  }

  if (error) {
    return (
      <Card>
        <CardContent className="flex items-start gap-3 py-4">
          <XCircle className="mt-0.5 h-4 w-4 text-destructive" />
          <div>
            <p className="text-sm text-text-primary">Agent OS status failed to load.</p>
            <p className="mt-1 break-words text-xs text-text-secondary">{error}</p>
            <Button className="mt-3" ghost onClick={load}>
              <RefreshCw className="h-4 w-4" />
              Retry
            </Button>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (!status) return null;

  return (
    <div className="flex min-w-0 max-w-full flex-col gap-4">
      <PluginSlot name="agent-os:top" />

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-text-secondary" />
              <CardTitle>Agent OS</CardTitle>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <StateBadge ok={status.detected} label={status.detected ? "LEOS detected" : "LEOS missing"} />
              <Button ghost size="icon" onClick={load} aria-label="Refresh Agent OS status">
                {loading ? <Spinner /> : <RefreshCw />}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="grid gap-4 md:grid-cols-3">
          <div>
            <p className="text-xs text-text-tertiary">Root</p>
            <p className="mt-1 break-words font-mono-ui text-xs text-text-secondary">{status.root}</p>
          </div>
          <div>
            <p className="text-xs text-text-tertiary">Contracts</p>
            <p className="mt-1 text-sm text-text-primary">{status.contracts.length} visible</p>
          </div>
          <div>
            <p className="text-xs text-text-tertiary">Adapter skills</p>
            <p className="mt-1 text-sm text-text-primary">{installedSkills}/{status.skills.length} installed</p>
          </div>
        </CardContent>
      </Card>

      {status.warnings.length > 0 && (
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-4 w-4 text-warning" />
              <CardTitle>Warnings</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {status.warnings.map((warning) => (
              <Badge key={warning} tone="warning" className="normal-case">
                {warning}
              </Badge>
            ))}
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <FileText className="h-4 w-4 text-text-secondary" />
              <CardTitle>Constitution</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <dl>
              <DetailRow label="Path" value={status.constitution.path} />
              <DetailRow label="Exists" value={status.constitution.exists ? "yes" : "no"} />
              <DetailRow label="SHA-256" value={shortHash(status.constitution.sha256)} />
              <DetailRow label="Modified" value={status.constitution.mtime ?? "not available"} />
            </dl>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Cpu className="h-4 w-4 text-text-secondary" />
              <CardTitle>Runtime</CardTitle>
            </div>
          </CardHeader>
          <CardContent>
            <dl>
              <DetailRow label="API mode" value={status.runtime.api_mode} />
              <DetailRow label="Memory" value={status.runtime.memory_provider} />
              <DetailRow label="Tool progress" value={status.runtime.tool_progress_bridge} />
              <DetailRow label="Checked" value={status.validation.last_checked_at} />
            </dl>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <FileCheck2 className="h-4 w-4 text-text-secondary" />
            <CardTitle>Contracts</CardTitle>
          </div>
        </CardHeader>
        <CardContent className="grid gap-2">
          {status.contracts.length === 0 ? (
            <p className="text-sm text-text-secondary">No LEOS contracts are visible.</p>
          ) : (
            status.contracts.map((contract) => (
              <div key={contract.id} className="rounded border border-border p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-mono-ui text-xs text-text-primary">{contract.id}</span>
                  <Badge tone="secondary" className="normal-case">{contract.status}</Badge>
                </div>
                <p className="mt-2 break-words font-mono-ui text-xs text-text-secondary">{contract.path}</p>
                <p className="mt-1 font-mono-ui text-xs text-text-tertiary">sha256 {shortHash(contract.sha256)}</p>
              </div>
            ))
          )}
        </CardContent>
      </Card>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-text-secondary" />
              <CardTitle>Adapter Skills</CardTitle>
            </div>
          </CardHeader>
          <CardContent className="grid gap-2">
            {status.skills.map((skill) => (
              <div key={skill.name} className="flex flex-wrap items-center justify-between gap-2 border-b border-border/60 py-2 last:border-b-0">
                <span className="font-mono-ui text-xs text-text-primary">{skill.name}</span>
                <div className="flex items-center gap-2">
                  {skill.installed && skill.enabled ? (
                    <CheckCircle2 className="h-4 w-4 text-success" />
                  ) : (
                    <XCircle className="h-4 w-4 text-destructive" />
                  )}
                  <Badge tone={skill.installed && skill.enabled ? "success" : "destructive"} className="normal-case">
                    {skill.installed ? (skill.enabled ? "enabled" : "disabled") : "missing"}
                  </Badge>
                </div>
              </div>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Validation Evidence</CardTitle>
          </CardHeader>
          <CardContent>
            {status.validation.evidence.length === 0 ? (
              <p className="text-sm text-text-secondary">No validation evidence has been collected yet.</p>
            ) : (
              <ul className="grid gap-2">
                {status.validation.evidence.map((item) => (
                  <li key={item} className="flex items-center gap-2 text-sm text-text-secondary">
                    <CheckCircle2 className="h-4 w-4 text-success" />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <PluginSlot name="agent-os:bottom" />
    </div>
  );
}
```

- [ ] **Step 2: Build to catch TypeScript and JSX errors**

Run:

```bash
cd web && npm run build
```

Expected: FAIL only if imports/types need adjustment; fix exact compiler errors before moving on.

---

### Task 5: Route and Navigation

**Files:**
- Modify: `web/src/App.tsx`

- [ ] **Step 1: Add page import**

In `web/src/App.tsx`, add:

```ts
import AgentOsPage from "@/pages/AgentOsPage";
```

- [ ] **Step 2: Add icon import**

Add `Orbit` to the lucide-react import list:

```ts
  Orbit,
```

- [ ] **Step 3: Add route**

Add to `BUILTIN_ROUTES_CORE`:

```ts
  "/agent-os": AgentOsPage,
```

Place it after `"/sessions": SessionsPage,`.

- [ ] **Step 4: Add nav item**

Add to `BUILTIN_NAV_REST` after Sessions:

```ts
  {
    path: "/agent-os",
    label: "Agent OS",
    icon: Orbit,
  },
```

- [ ] **Step 5: Add plugin icon map entry**

Add to `ICON_MAP`:

```ts
  Orbit,
```

- [ ] **Step 6: Build dashboard**

Run:

```bash
cd web && npm run build
```

Expected: PASS.

- [ ] **Step 7: Commit frontend page**

Run:

```bash
git add web/src/lib/api.ts web/src/pages/AgentOsPage.tsx web/src/App.tsx
git commit -m "Show LEOS Agent OS status in the dashboard" \
  -m "Constraint: The dashboard must reveal Agent OS state without replacing the PTY-backed chat surface." \
  -m "Confidence: medium" \
  -m "Scope-risk: moderate" \
  -m "Directive: Keep this page operational and status-focused; do not turn it into explanatory marketing copy." \
  -m "Tested: cd web && npm run build"
```

---

### Task 6: Local LEOS Adapter Skills

**Files:**
- Create: `/Users/leo/.hermes/skills/leos-constitution/SKILL.md`
- Create: `/Users/leo/.hermes/skills/leos-contract/SKILL.md`
- Create: `/Users/leo/.hermes/skills/leos-dashboard-work/SKILL.md`

- [ ] **Step 1: Create skill directories**

Run:

```bash
mkdir -p /Users/leo/.hermes/skills/leos-constitution
mkdir -p /Users/leo/.hermes/skills/leos-contract
mkdir -p /Users/leo/.hermes/skills/leos-dashboard-work
```

Expected: directories exist under the active Hermes profile.

- [ ] **Step 2: Create `leos-constitution` skill**

Create `/Users/leo/.hermes/skills/leos-constitution/SKILL.md`:

```markdown
---
name: leos-constitution
description: Load and apply the canonical LEOS constitution from LEOS_ROOT or ~/LEOS before work that must follow LEOS Agent OS doctrine, governance, or operating principles.
---

# LEOS Constitution

Use this skill when the user asks for LEOS-governed work, Agent OS behavior,
constitution checks, governance posture, or contract compliance.

## Source

Resolve the canonical constitution in this order:

1. `$LEOS_ROOT/constitution/constitution.md`
2. `~/LEOS/constitution/constitution.md`

Do not treat this skill file as canonical doctrine. It is only a Hermes adapter
that points to the LEOS source of truth.

## Workflow

1. Verify the canonical constitution file exists.
2. Read only the sections needed for the current task.
3. State the source path used.
4. Apply the constitution as operating constraints for the current task.
5. If the file is missing, report `constitution_missing` and continue under the
   safest available Hermes defaults.

## Evidence

When reporting compliance, include concrete evidence:

- source path
- relevant section title or contract anchor
- validation action taken
- remaining gap or warning
```

- [ ] **Step 3: Create `leos-contract` skill**

Create `/Users/leo/.hermes/skills/leos-contract/SKILL.md`:

```markdown
---
name: leos-contract
description: Select, read, and enforce a LEOS contract from LEOS_ROOT or ~/LEOS/contracts for scoped work; use when tasks require acceptance criteria, validation evidence, or governance state.
---

# LEOS Contract

Use this skill when work must be governed by a named LEOS contract, acceptance
criteria, validation evidence, or Agent OS state transition.

## Source

Resolve contracts from:

1. `$LEOS_ROOT/contracts/<contract-id>.yaml`
2. `~/LEOS/contracts/<contract-id>.yaml`

If no contract id is named, infer the smallest plausible contract from the task.
For Agent Console dashboard work, use `agent-console-dashboard`.

## Workflow

1. Identify the active contract id.
2. Verify the contract file exists.
3. Read the contract before implementation.
4. Extract target result, constraints, required skills, and validation checks.
5. During implementation, preserve the contract as the active acceptance
   criteria.
6. Before completion, report validation evidence and unresolved gaps.

## Missing Contract

If the contract file is absent, report `contract_missing:<contract-id>`.
Do not invent contract content. Use the user's latest instruction and the
Hermes repo spec as fallback acceptance criteria.
```

- [ ] **Step 4: Create `leos-dashboard-work` skill**

Create `/Users/leo/.hermes/skills/leos-dashboard-work/SKILL.md`:

```markdown
---
name: leos-dashboard-work
description: Govern Hermes Agent Console dashboard work under the LEOS agent-console-dashboard contract; use for /agent-os, dashboard status, LEOS visibility, and Agent OS console changes.
---

# LEOS Dashboard Work

Use this skill for Hermes Agent Console dashboard work that must reveal or obey
LEOS Agent OS state.

## Active Contract

Contract id: `agent-console-dashboard`

Resolve it from:

1. `$LEOS_ROOT/contracts/agent-console-dashboard.yaml`
2. `~/LEOS/contracts/agent-console-dashboard.yaml`

## Operating Rules

1. Keep LEOS canonical doctrine outside the Hermes repo.
2. Use Hermes adapter skills and APIs as execution surfaces only.
3. Do not duplicate private LEOS document bodies in dashboard API responses.
4. Show source paths, hashes, warnings, and validation evidence.
5. Keep `/chat` PTY-backed; do not rebuild the transcript in React.
6. Treat missing LEOS artifacts as warnings, not dashboard crashes.

## Verification

For dashboard changes, verify:

- backend status endpoint tests
- dashboard TypeScript build
- `/agent-os` browser smoke
- no console errors
- `/chat` still loads the embedded TUI surface
```

- [ ] **Step 5: Verify skill discovery sees all three adapters**

Run:

```bash
python - <<'PY'
from tools.skills_tool import _find_all_skills
names = {row["name"] for row in _find_all_skills(skip_disabled=True)}
required = {"leos-constitution", "leos-contract", "leos-dashboard-work"}
missing = required - names
print({"missing": sorted(missing), "found": sorted(required & names)})
raise SystemExit(1 if missing else 0)
PY
```

Expected: exits `0` and prints all three skills under `found`.

- [ ] **Step 6: Recheck Agent OS status now reports installed skills**

Run:

```bash
python - <<'PY'
from hermes_cli.agent_os_status import build_agent_os_status
status = build_agent_os_status()
print(status["skills"])
missing = [s["name"] for s in status["skills"] if not s["installed"]]
raise SystemExit(1 if missing else 0)
PY
```

Expected: exits `0`; all required LEOS adapter skills have `installed: True`.

---

### Task 7: Final Verification

**Files:**
- Verify only; no edits expected.

- [ ] **Step 1: Run focused backend tests**

Run:

```bash
pytest tests/hermes_cli/test_agent_os_status.py tests/hermes_cli/test_web_server.py::TestWebServerEndpoints::test_get_agent_os_status -q
```

Expected: PASS.

- [ ] **Step 2: Run dashboard build**

Run:

```bash
cd web && npm run build
```

Expected: PASS.

- [ ] **Step 3: Run dashboard locally**

Start backend:

```bash
python -m hermes_cli.main web --no-open
```

Expected: server starts on port `9119` or prints the active dashboard URL.

- [ ] **Step 4: Start Vite dev server**

In another terminal:

```bash
cd web && npm run dev -- --host 127.0.0.1
```

Expected: Vite prints a local URL, usually `http://127.0.0.1:5173`.

- [ ] **Step 5: Browser smoke**

Open `/agent-os` in the local dashboard.

Expected:

- Page loads without console errors.
- LEOS detected or missing state is clear.
- Constitution path/hash area never shows file contents.
- Required skill statuses show enabled, disabled, or missing.
- `/chat` still opens the embedded PTY-backed TUI.

- [ ] **Step 6: Final status**

Run:

```bash
git status --short
```

Expected: repo changes are committed except any intentionally local `/Users/leo/.hermes/skills/leos-*` skill files and pre-existing unrelated dirty files.

---

## Self-Review Notes

- Spec coverage: This plan covers read-only status model, `/api/agent-os/status`, dashboard `/agent-os`, runtime/memory/skill warnings, validation evidence, and local LEOS adapter skill installation.
- Placeholder scan: No intentionally unresolved markers or unspecified test steps are present.
- Type consistency: Backend uses snake_case JSON keys; frontend interfaces match those keys exactly.
