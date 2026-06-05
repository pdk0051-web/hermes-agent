"""Pure, dependency-light evaluator for LEOS *contract completion*.

A LEOS contract that governs a long autonomous run can carry an
``acceptance_criteria`` list — the concrete, checkable conditions that say
"this work is DONE". This module answers two questions about such a contract,
as PURE functions so they can be unit-tested with injected data and wired into
the gateway behind a flag without dragging in any heavy agent machinery:

* :func:`is_valid_for_takeoff` — is this contract concrete enough to govern a
  long autonomous run at all? (A vision/goal-tier contract with no
  ``acceptance_criteria`` is NOT — it cannot be "closed", only pursued.)
* :func:`evaluate` — given the per-session work-journal, are the acceptance
  criteria FULFILLED (met, with evidence) or still UNMET?

Design rules (mirrors ``agent/work_journal.py``):
* FAIL-OPEN. Malformed input must never crash the caller. Every public
  function swallows unexpected errors and degrades safely (``None`` /
  ``False`` / ``status="unmet"``).
* PURE. The only I/O is :func:`load_contract` (reads a yaml file) and the
  caller-injected ``evidence_exists`` callback in :func:`evaluate`. No heavy
  agent imports at module top — stdlib + ``yaml`` only.
* Tolerant schema. Real contract YAMLs vary: a criterion may be a bare string
  or a dict (``{id, text, evidence}``); ``evidence_required`` may be a
  top-level flag/list or expressed per-criterion. We accept all of these.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

# Directories searched (in order) when ``load_contract`` is given a bare id
# rather than an explicit path. Kept as a module constant so tests can see /
# reason about the lookup order without monkeypatching internals.
_CONTRACT_SUBDIRS = ("contracts", "ops/contracts")

# Journal-record statuses that count as "this unit of work is finished".
_DONE_STATUSES = frozenset({"done", "complete", "completed", "closed"})


def _leos_root() -> Path:
    """Resolve the LEOS root (``~/LEOS``). Isolated for test override."""
    return Path.home() / "LEOS"


def load_contract(
    id_or_path: Any,
    *,
    roots: Optional[Iterable[Any]] = None,
) -> Optional[dict]:
    """Load a contract YAML by id or explicit path. Tolerant — ``None`` on miss.

    ``id_or_path`` is either:
      * an explicit path (``str``/``os.PathLike``) to a ``.yaml`` file that
        exists — read directly; or
      * a bare contract id (e.g. ``"C2-hermes-work-1"``) — searched, in order,
        under each root's ``contracts/`` then ``ops/contracts/`` subdir, with
        and without a ``.yaml`` suffix.

    ``roots`` overrides the search roots (defaults to ``~/LEOS``); injected in
    tests so no real ``~/LEOS`` access is needed.

    Returns the parsed mapping, or ``None`` on ANY problem (missing file,
    unreadable, non-mapping YAML, parse error). Never raises.
    """
    try:
        if id_or_path is None:
            return None

        candidates: list[Path] = []

        # 1) Explicit existing file path wins outright.
        try:
            direct = Path(id_or_path)
        except TypeError:
            direct = None
        if direct is not None and direct.suffix and direct.is_file():
            candidates.append(direct)
        else:
            # 2) Treat as a bare id and build search candidates.
            raw = str(id_or_path).strip()
            if not raw:
                return None
            search_roots = (
                [Path(r) for r in roots]
                if roots is not None
                else [_leos_root()]
            )
            for root in search_roots:
                for sub in _CONTRACT_SUBDIRS:
                    base = root / sub
                    # Accept id given with or without the .yaml extension.
                    if raw.endswith((".yaml", ".yml")):
                        candidates.append(base / raw)
                    else:
                        candidates.append(base / f"{raw}.yaml")
                        candidates.append(base / f"{raw}.yml")
            # Also allow an explicit path that simply didn't exist yet to be
            # tried verbatim (covers absolute/relative non-.yaml inputs).
            if direct is not None and direct not in candidates:
                candidates.append(direct)

        import yaml

        for path in candidates:
            try:
                if not path.is_file():
                    continue
                with open(path, "r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh)
            except Exception:
                continue
            if isinstance(data, dict):
                return data
            # A YAML that parses to a non-mapping is not a usable contract.
            return None
        return None
    except Exception:
        logger.debug("contract_gate.load_contract failed (fail-open)", exc_info=True)
        return None


def _criteria_list(contract: Any) -> list:
    """Return the contract's ``acceptance_criteria`` as a list (else ``[]``)."""
    if not isinstance(contract, dict):
        return []
    crit = contract.get("acceptance_criteria")
    if isinstance(crit, list):
        return crit
    return []


def _criterion_id(criterion: Any, index: int) -> str:
    """Stable identifier for a criterion (its ``id`` if any, else position)."""
    if isinstance(criterion, dict):
        for key in ("id", "key", "name"):
            val = criterion.get(key)
            if val:
                return str(val)
    return f"criterion[{index}]"


def _criterion_text(criterion: Any) -> str:
    """Human-meaningful text of a criterion, for keyword matching."""
    if isinstance(criterion, str):
        return criterion
    if isinstance(criterion, dict):
        for key in ("text", "description", "desc", "criterion", "title"):
            val = criterion.get(key)
            if val:
                return str(val)
    return ""


def _criterion_evidence(criterion: Any) -> Optional[str]:
    """Evidence path a criterion names, if any (``None`` otherwise)."""
    if isinstance(criterion, dict):
        for key in ("evidence", "evidence_path", "artifact", "proof"):
            val = criterion.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


def _is_concrete(criterion: Any, index: int) -> bool:
    """A criterion is concrete when it carries an explicit id OR real text."""
    has_id = False
    if isinstance(criterion, dict):
        has_id = bool(
            criterion.get("id") or criterion.get("key") or criterion.get("name")
        )
    return has_id or bool(_criterion_text(criterion).strip())


def _has_evidence_required(contract: dict, criteria: list) -> bool:
    """True when the contract expresses an ``evidence_required`` notion.

    Accepts either a truthy top-level ``evidence_required`` (bool / list /
    string / mapping) OR at least one criterion that names its own
    ``evidence`` path. This keeps takeoff-validity honest: a contract has to
    say *what proof closes it*, one way or another.
    """
    top = contract.get("evidence_required")
    if isinstance(top, bool):
        if top:
            return True
    elif top:  # non-empty list/str/dict
        return True
    return any(_criterion_evidence(c) is not None for c in criteria)


def is_valid_for_takeoff(contract: Any) -> bool:
    """Is ``contract`` concrete enough to govern a long autonomous run?

    ``True`` only when ALL hold:
      * ``acceptance_criteria`` is a non-empty list, AND
      * every criterion is concrete (has an id or real text), AND
      * the contract expresses an ``evidence_required`` notion (top-level flag
        or per-criterion evidence paths).

    A vision/goal-tier contract with no acceptance criteria → ``False``.
    Fail-open: any unexpected error → ``False`` (never crash, never green-light).
    """
    try:
        if not isinstance(contract, dict):
            return False
        criteria = _criteria_list(contract)
        if not criteria:
            return False
        if not all(_is_concrete(c, i) for i, c in enumerate(criteria)):
            return False
        return _has_evidence_required(contract, criteria)
    except Exception:
        logger.debug(
            "contract_gate.is_valid_for_takeoff failed (fail-open)", exc_info=True
        )
        return False


def _record_status(record: Any) -> str:
    """Lowercased ``status`` of a journal record (``""`` when absent)."""
    if isinstance(record, dict):
        return str(record.get("status") or "").strip().lower()
    return ""


def _record_text(record: Any) -> str:
    """Concatenated text of a journal record used for criterion matching.

    Pulls the fields a record may use to point back at a criterion — an
    explicit ``criterion_id`` / ``criterion`` reference, plus the human
    ``summary`` — so a ``done`` record can be matched either structurally
    (by id) or by keyword (criterion text appearing in the summary).
    """
    if not isinstance(record, dict):
        return ""
    parts: list[str] = []
    for key in (
        "criterion_id",
        "criterion",
        "criteria",
        "acceptance_criterion",
        "ref",
        "summary",
        "text",
        "title",
    ):
        val = record.get(key)
        if val:
            parts.append(str(val))
    return " ".join(parts)


def _record_references(record: Any, crit_id: str, crit_text: str) -> bool:
    """Does ``record`` reference the criterion identified by id/text?

    Matches when the criterion id appears in the record (structural) OR the
    criterion's text appears (case-insensitively) in the record's referencing
    fields (keyword). Empty criterion text never keyword-matches (avoids a
    blank criterion matching every record).
    """
    blob = _record_text(record).lower()
    if not blob:
        return False
    if crit_id and crit_id.lower() in blob:
        return True
    crit_text = (crit_text or "").strip().lower()
    if crit_text and crit_text in blob:
        return True
    return False


def _criterion_satisfied(
    criterion: Any,
    index: int,
    done_records: list,
    evidence_exists: Callable[[str], bool],
) -> bool:
    """One criterion is satisfied iff a ``done`` record references it AND
    (when the criterion names an evidence path) that path exists."""
    crit_id = _criterion_id(criterion, index)
    crit_text = _criterion_text(criterion)
    referencing = [
        r for r in done_records if _record_references(r, crit_id, crit_text)
    ]
    if not referencing:
        return False
    evidence_path = _criterion_evidence(criterion)
    if evidence_path is None:
        return True
    try:
        return bool(evidence_exists(evidence_path))
    except Exception:
        # A broken evidence probe must not green-light closure.
        return False


def evaluate(
    contract: Any,
    *,
    journal_records: Any,
    evidence_exists: Optional[Callable[[str], bool]] = None,
) -> dict:
    """Evaluate acceptance criteria against the work-journal.

    Returns ``{"status": "met"|"unmet", "met": [...], "unmet": [...]}`` where
    ``met``/``unmet`` are the criterion identifiers in each bucket.

    A criterion is satisfied iff there is a ``journal_records`` entry with a
    done-status (``done``/``completed``/…) that references it (by id or by the
    criterion text appearing in the record) AND — if the criterion names an
    ``evidence`` path — ``evidence_exists(path)`` is ``True``.

    ``evidence_exists`` defaults to :func:`os.path.exists`; inject a fake in
    tests so this stays PURE (no real filesystem / LEOS access).

    Overall ``status`` is ``"met"`` only when there is at least one criterion
    and EVERY criterion is satisfied. Fail-open: malformed input (or no
    criteria) → ``status == "unmet"`` with empty buckets; never raises.
    """
    if evidence_exists is None:
        evidence_exists = os.path.exists

    try:
        criteria = _criteria_list(contract)
        records = journal_records if isinstance(journal_records, list) else []
        done_records = [r for r in records if _record_status(r) in _DONE_STATUSES]

        met: list[str] = []
        unmet: list[str] = []
        for i, criterion in enumerate(criteria):
            cid = _criterion_id(criterion, i)
            if _criterion_satisfied(criterion, i, done_records, evidence_exists):
                met.append(cid)
            else:
                unmet.append(cid)

        status = "met" if criteria and not unmet else "unmet"
        return {"status": status, "met": met, "unmet": unmet}
    except Exception:
        logger.debug("contract_gate.evaluate failed (fail-open)", exc_info=True)
        return {"status": "unmet", "met": [], "unmet": []}
