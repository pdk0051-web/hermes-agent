# LEOS Agent OS Dashboard Design

Status: Draft approved for implementation planning
Date: 2026-06-05
Owner: PM / Hermes Agent

## Problem

The Agent Console dashboard currently exposes Hermes administration surfaces:
sessions, models, logs, skills, MCP, cron, profiles, and system controls. It
does not expose the higher-level Agent OS state the PM expects from LEOS:
which constitution is active, which contracts govern the current work, which
skills implement those contracts, whether LEOS memory is wired, and what
validation evidence proves the agent is operating under that regime.

Existing LEOS-related repo artifacts are not enough:

- `leos_knowledge` is a context memory provider, not a governance runtime.
- `docs/leos-integration-design.md` describes external integration surfaces,
  not an in-dashboard Agent OS status model.
- The current `agent/codex_runtime.py` LEOS comment concerns Codex tool-progress
  visibility, not LEOS constitution or contract enforcement.

## Product Decision

Use a hybrid model:

- `~/LEOS` remains the canonical source for LEOS constitution, contracts, and
  optional LEOS-native skill definitions.
- Hermes hosts thin adapter skills and status APIs that make those LEOS
  sources executable and observable inside Hermes.
- The dashboard gets a first-class Agent OS view that reports LEOS status,
  not just generic Hermes settings.

This keeps LEOS independent of Hermes while allowing Hermes to prove, at
runtime, that it is operating as a LEOS-compatible agent host.

## Goals

- Show whether LEOS is detected and which canonical files are active.
- Show active constitution and contract identity with version/hash evidence.
- Show Hermes adapter skills that implement LEOS workflows.
- Show memory/runtime wiring relevant to LEOS, including `leos_knowledge`.
- Surface warnings when LEOS is missing, stale, disabled, or only partially
  wired.
- Make Agent Console Dashboard work itself governed by a LEOS contract.

## Non-Goals

- Do not move LEOS canonical doctrine into the Hermes repo.
- Do not make in-process checks a security boundary.
- Do not replace Hermes' existing `/skills`, `/system`, `/sessions`, or `/chat`
  pages.
- Do not reimplement the embedded dashboard chat transcript in React; `/chat`
  remains the PTY-backed `hermes --tui` surface.
- Do not add broad LEOS write tools until audit and approval semantics exist.

## Canonical Source Layout

The status reader should treat these paths as the default LEOS source layout:

```text
~/LEOS/
  constitution/
    constitution.md
  contracts/
    agent-console-dashboard.yaml
    memory.yaml
    governance.yaml
  skills/
    leos-constitution.md
    leos-contract.md
    leos-dashboard-work.md
```

Path configurability through `config.yaml` is out of scope for milestone 1.
Milestone 1 resolves `LEOS_ROOT` first and falls back to `~/LEOS`, with
graceful missing-state reporting.

## Hermes Adapter Skills

Hermes should expose small skills under `~/.hermes/skills` or the active
Hermes profile's skills directory:

```text
leos-constitution/
  SKILL.md
leos-contract/
  SKILL.md
leos-dashboard-work/
  SKILL.md
```

Each adapter skill should be thin:

- Load only the specific LEOS reference needed for the task.
- State the active source path and version/hash when possible.
- Translate LEOS doctrine into Hermes execution rules.
- Produce validation evidence rather than claiming compliance by prompt text.

The adapter skills do not own LEOS doctrine. They are execution surfaces.

## Status API

Add a dashboard-facing endpoint:

```text
GET /api/agent-os/status
```

Response shape:

```json
{
  "agent_os": "leos",
  "detected": true,
  "root": "/Users/leo/LEOS",
  "constitution": {
    "path": "/Users/leo/LEOS/constitution/constitution.md",
    "exists": true,
    "sha256": "..."
  },
  "contracts": [
    {
      "id": "agent-console-dashboard",
      "path": "/Users/leo/LEOS/contracts/agent-console-dashboard.yaml",
      "exists": true,
      "sha256": "...",
      "status": "active"
    }
  ],
  "skills": [
    {
      "name": "leos-dashboard-work",
      "installed": true,
      "enabled": true,
      "source": "hermes-profile"
    }
  ],
  "runtime": {
    "api_mode": "codex_app_server",
    "memory_provider": "leos_knowledge",
    "tool_progress_bridge": "available"
  },
  "validation": {
    "last_checked_at": "2026-06-05T00:00:00Z",
    "evidence": [
      "constitution file hashed",
      "dashboard contract file hashed",
      "leos-dashboard-work skill installed"
    ]
  },
  "warnings": []
}
```

When LEOS is absent, the endpoint must still return `200` with
`detected: false` and actionable warnings. A missing LEOS root is not a server
error.

## Dashboard Surface

Add a first-class Agent OS surface. The preferred first milestone is a new
navigation item:

```text
/agent-os
```

The page should show:

- Agent OS summary: detected state, root path, status badge.
- Constitution panel: path, hash, modified time, missing/stale warnings.
- Contracts panel: active contracts and their validation status.
- Skills panel: required LEOS adapter skills, installed/enabled state.
- Runtime panel: current API mode, memory provider, tool-progress visibility.
- Evidence panel: last status check and concrete validation evidence.

The page should be quiet and operational, not a marketing page. It should use
existing dashboard typography, semantic color tokens, `@nous-research/ui`
components, and lucide icons.

## Data Flow

```text
~/LEOS files
  -> Hermes Agent OS status reader
  -> GET /api/agent-os/status
  -> web/src/lib/api.ts typed client
  -> web/src/pages/AgentOsPage.tsx
  -> Dashboard nav + status panels
```

Adapter skills are discovered through the existing Hermes skill mechanisms.
The status API should inspect skill availability without loading full skill
bodies into the response.

## Error Handling

- Missing `~/LEOS`: return `detected: false`, warning `leos_root_missing`.
- Missing constitution: return `detected: true`, constitution `exists: false`,
  warning `constitution_missing`.
- Missing contracts: return empty contracts list plus `contracts_missing`.
- Missing adapter skill: report skill `installed: false`.
- Disabled adapter skill: report `installed: true`, `enabled: false`.
- Memory provider not set to `leos_knowledge`: report warning, not failure.
- File read errors: include path-level warning without leaking private content.

## Testing

Backend:

- Unit-test status response when LEOS root is absent.
- Unit-test status response with a temporary LEOS root and contract files.
- Unit-test skill detection for installed, missing, and disabled adapter skills.
- Unit-test that response includes hashes but not file contents.

Frontend:

- Typecheck the API client shape.
- Component-test or smoke-test `AgentOsPage` for detected and missing states.
- Verify dashboard build.

Manual/browser:

- Run the dashboard locally.
- Open `/agent-os`.
- Confirm the page shows LEOS status without console errors.
- Confirm `/chat` remains PTY-backed and unaffected.

## Implementation Milestones

1. Add read-only Agent OS status model and `/api/agent-os/status`.
2. Add `AgentOsPage` and navigation item.
3. Add minimal LEOS adapter skills.
4. Wire dashboard work to the `leos-dashboard-work` contract.
5. Expand validation evidence after the first end-to-end pass.

Milestone 1 and 2 are enough to make the dashboard reveal Agent OS state.
Milestone 3 turns the doctrine into reusable execution surfaces.

## Acceptance Criteria

- The dashboard has an Agent OS surface.
- The page clearly distinguishes missing LEOS, partially wired LEOS, and active
  LEOS states.
- The backend status endpoint is read-only and safe to call repeatedly.
- The response includes paths, hashes, runtime wiring, skill state, warnings,
  and validation evidence.
- No LEOS doctrine is duplicated into Hermes as canonical content.
- Existing dashboard pages and embedded chat continue to work.
- Tests prove absent-root and present-root behavior.

## Open Follow-Up

Once the status surface exists, decide whether `agent-console-dashboard.yaml`
should become a stricter executable contract with required checks, expected
skills, and page-level acceptance criteria. That should be a second contract
iteration, not a blocker for making Agent OS state visible.
