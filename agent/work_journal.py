"""Durable per-turn work-journal — the WRITE side (Phase 1).

Each codex app-server turn dumps its ENTIRE tool trace into the persisted
transcript (``messages.extend(turn.projected_messages)`` in
``agent/codex_runtime.py``), which grows the session unboundedly. This module
externalises a LEAN, structured record of each turn into an append-only JSONL
journal so the *meaning* of a turn survives outside the transcript — letting
later phases trim the transcript without losing what happened.

Design rules:
* FAIL-OPEN. A journal failure must NEVER break a turn. The whole write body
  is wrapped so any exception is swallowed and logged at debug.
* Cheap to import. Stdlib only (json, os, pathlib, datetime, logging); we do
  NOT import heavy agent modules at top level.
* Small records. Tool steps collapse consecutive same-kind items and are
  capped, so a 200-iteration turn still yields a compact record.

Directory resolution (write and read share it):
    1. explicit ``journal_dir`` argument, else
    2. ``~/LEOS/journal/`` when ``~/LEOS`` exists and the journal path is
       writable, else
    3. ``~/.hermes/journal/``.
The directory is created (``mkdir -p``) on write.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Truncation budgets — keep records small enough that the journal is cheaper
# than the transcript it externalises.
_SUMMARY_MAX = 400
_TARGET_MAX = 80
_MAX_STEPS = 40


def _journal_path_writable(path: Path) -> bool:
    """Best-effort check that ``path`` can be used for journal writes.

    ``~/LEOS`` may exist but be governed by a stronger OS ownership boundary.
    In that case choosing ``~/LEOS/journal`` makes every journal write fail
    open and also prevents the contract gate from seeing completion records.
    If the journal directory does not exist yet, test the parent directory
    because ``record_turn`` will create the journal directory later.
    """
    try:
        if path.exists():
            return path.is_dir() and os.access(path, os.W_OK | os.X_OK)
        parent = path.parent
        return parent.exists() and os.access(parent, os.W_OK | os.X_OK)
    except Exception:
        return False


def _resolve_journal_dir(journal_dir: Optional[str | os.PathLike]) -> Path:
    """Resolve the journal directory. See module docstring for the order."""
    if journal_dir is not None:
        return Path(journal_dir)
    leos_root = Path.home() / "LEOS"
    leos_journal = leos_root / "journal"
    if leos_root.exists() and _journal_path_writable(leos_journal):
        return leos_journal
    return Path.home() / ".hermes" / "journal"


def _truncate(text: Any, limit: int) -> str:
    """Coerce ``text`` to str and clip to ``limit`` chars with an ellipsis."""
    s = "" if text is None else str(text)
    if len(s) > limit:
        return s[: max(0, limit - 1)] + "…"
    return s


def _step_kind(msg: dict) -> Optional[str]:
    """Short label for one projected message, or None if it is not a step.

    Projected tool invocations are OpenAI-shaped assistant messages carrying a
    ``tool_calls`` list whose ``function.name`` is the tool (``exec_command``,
    ``apply_patch``, ``mcp.<server>.<tool>``, …). Plain assistant text and the
    paired ``role == "tool"`` results are NOT counted as steps — they would
    just double the noise.
    """
    if not isinstance(msg, dict):
        return None
    tool_calls = msg.get("tool_calls")
    if not tool_calls:
        return None
    first = tool_calls[0]
    if not isinstance(first, dict):
        return None
    fn = first.get("function") or {}
    name = fn.get("name")
    return str(name) if name else "tool"


def _step_target(msg: dict) -> str:
    """Preview of what a tool step acted on (truncated), best-effort."""
    tool_calls = msg.get("tool_calls") or []
    if not tool_calls or not isinstance(tool_calls[0], dict):
        return ""
    fn = tool_calls[0].get("function") or {}
    args = fn.get("arguments")
    if args is None:
        return ""
    # ``arguments`` is normally a JSON string; surface a human-meaningful
    # field (command / path) when we can, else the raw preview.
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except Exception:
            return _truncate(args, _TARGET_MAX)
    else:
        parsed = args
    if isinstance(parsed, dict):
        for key in ("command", "path", "file_path", "cwd", "changes"):
            if key in parsed and parsed[key]:
                return _truncate(parsed[key], _TARGET_MAX)
        return _truncate(json.dumps(parsed, ensure_ascii=False), _TARGET_MAX)
    return _truncate(parsed, _TARGET_MAX)


def _build_steps(projected_messages: Any) -> list[dict]:
    """Collapse projected messages into a small, capped list of steps.

    Consecutive items of the same kind collapse into one step (so a long run
    of ``exec_command`` calls is a single entry, not 50). The list is capped
    at ``_MAX_STEPS``; an overflow marker records how many were elided.
    """
    steps: list[dict] = []
    last_kind: Optional[str] = None
    overflow = 0
    if not isinstance(projected_messages, (list, tuple)):
        return steps
    for msg in projected_messages:
        kind = _step_kind(msg)
        if kind is None:
            continue
        if kind == last_kind:
            # Same activity as the previous step — collapse, don't re-emit.
            continue
        last_kind = kind
        if len(steps) >= _MAX_STEPS:
            overflow += 1
            continue
        steps.append({"kind": kind, "target": _step_target(msg)})
    if overflow:
        steps.append({"kind": "...overflow", "target": f"+{overflow} more"})
    return steps


def record_turn(
    turn: Any,
    *,
    session_id: str,
    contract_id: Optional[str] = None,
    turn_index: Optional[int] = None,
    journal_dir: Optional[str | os.PathLike] = None,
) -> Optional[dict]:
    """Append a lean record of ``turn`` to ``<dir>/<session_id>.jsonl``.

    ``turn`` is a ``TurnResult`` (see
    ``agent/transports/codex_app_server_session.py``); the fields read are
    ``final_text`` (turn summary) and ``projected_messages`` (tool trace).
    ``tool_iterations`` is recorded as a raw count for cross-checking.

    Returns the record dict that was written, or ``None`` on any failure.
    FAIL-OPEN: the entire body is guarded so a journal error never breaks the
    turn that called it.
    """
    try:
        directory = _resolve_journal_dir(journal_dir)
        directory.mkdir(parents=True, exist_ok=True)

        projected = getattr(turn, "projected_messages", None)
        rec = {
            "ts": datetime.datetime.now().astimezone().isoformat(),
            "session_id": str(session_id),
            "contract_id": contract_id,
            "turn_index": turn_index,
            "summary": _truncate(getattr(turn, "final_text", "") or "", _SUMMARY_MAX),
            "steps": _build_steps(projected),
            "tool_iterations": getattr(turn, "tool_iterations", None),
            "status": "in_progress",
        }

        path = directory / f"{session_id}.jsonl"
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
        return rec
    except Exception:
        logger.debug("work_journal.record_turn failed (fail-open)", exc_info=True)
        return None


def read_journal(
    session_id: str,
    *,
    journal_dir: Optional[str | os.PathLike] = None,
) -> list[dict]:
    """Read and parse ``<dir>/<session_id>.jsonl`` into a list of records.

    Tolerant of malformed/blank lines (they are skipped). Returns ``[]`` on
    any error, including a missing file.
    """
    records: list[dict] = []
    try:
        directory = _resolve_journal_dir(journal_dir)
        path = directory / f"{session_id}.jsonl"
        if not path.exists():
            return records
        with open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except Exception:
                    # Skip a corrupt line rather than discard the whole file.
                    continue
        return records
    except Exception:
        logger.debug("work_journal.read_journal failed (fail-open)", exc_info=True)
        return []
