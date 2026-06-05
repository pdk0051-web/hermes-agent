"""Tests for the pure LEOS contract-completion evaluator (STORY G001).

Everything here is PURE: ``journal_records`` and ``evidence_exists`` are
injected, and ``load_contract`` is driven against pytest ``tmp_path`` roots —
so no test ever touches the real ``~/LEOS`` tree or the real filesystem for
evidence. Coverage:

* ``evaluate`` — met (done record + evidence), unmet (missing done / missing
  evidence), and fail-open on garbage input.
* ``is_valid_for_takeoff`` — True for a concrete contract with
  acceptance_criteria + evidence_required; False for a vision-tier contract
  with none.
* ``load_contract`` — tolerant: missing id/file → ``None``; explicit path and
  bare-id-by-root lookups round-trip a real yaml.
"""

import textwrap

from agent import contract_gate


# ---------------------------------------------------------------------------
# evaluate()
# ---------------------------------------------------------------------------


def test_evaluate_met_when_done_record_references_criterion_with_evidence():
    contract = {
        "acceptance_criteria": [
            {"id": "AC1", "text": "tests are green", "evidence": "/proof/report.txt"},
            {"id": "AC2", "text": "module compiles"},
        ],
        "evidence_required": True,
    }
    journal = [
        {"status": "in_progress", "criterion_id": "AC1", "summary": "starting"},
        {"status": "done", "criterion_id": "AC1", "summary": "tests passed"},
        {"status": "done", "criterion_id": "AC2", "summary": "compiled clean"},
    ]

    result = contract_gate.evaluate(
        contract,
        journal_records=journal,
        evidence_exists=lambda path: path == "/proof/report.txt",
    )

    assert result["status"] == "met"
    assert result["met"] == ["AC1", "AC2"]
    assert result["unmet"] == []


def test_evaluate_matches_criterion_by_keyword_in_summary():
    # No structural criterion_id on the record — must still match because the
    # criterion text appears in the record summary.
    contract = {"acceptance_criteria": [{"id": "AC1", "text": "ledger closed"}]}
    journal = [{"status": "done", "summary": "the ledger closed cleanly"}]

    result = contract_gate.evaluate(contract, journal_records=journal)

    assert result["status"] == "met"
    assert result["met"] == ["AC1"]


def test_evaluate_unmet_when_no_done_record():
    contract = {"acceptance_criteria": [{"id": "AC1", "text": "do the thing"}]}
    journal = [{"status": "in_progress", "criterion_id": "AC1", "summary": "working"}]

    result = contract_gate.evaluate(contract, journal_records=journal)

    assert result["status"] == "unmet"
    assert result["met"] == []
    assert result["unmet"] == ["AC1"]


def test_evaluate_unmet_when_evidence_missing():
    # Done record references the criterion, but the named evidence path does
    # not exist -> the criterion is NOT satisfied.
    contract = {
        "acceptance_criteria": [
            {"id": "AC1", "text": "artifact built", "evidence": "/out/artifact.bin"},
        ],
    }
    journal = [{"status": "done", "criterion_id": "AC1", "summary": "built it"}]

    result = contract_gate.evaluate(
        contract,
        journal_records=journal,
        evidence_exists=lambda path: False,  # evidence absent
    )

    assert result["status"] == "unmet"
    assert result["unmet"] == ["AC1"]


def test_evaluate_partial_is_unmet():
    contract = {
        "acceptance_criteria": [
            {"id": "AC1", "text": "first"},
            {"id": "AC2", "text": "second"},
        ],
    }
    journal = [{"status": "done", "criterion_id": "AC1", "summary": "first done"}]

    result = contract_gate.evaluate(contract, journal_records=journal)

    assert result["status"] == "unmet"
    assert result["met"] == ["AC1"]
    assert result["unmet"] == ["AC2"]


def test_evaluate_evidence_exists_defaults_to_real_fs(tmp_path):
    # Exercise the default evidence_exists (os.path.exists) without injection.
    real = tmp_path / "evidence.txt"
    real.write_text("ok", encoding="utf-8")
    contract = {
        "acceptance_criteria": [
            {"id": "AC1", "text": "made evidence", "evidence": str(real)},
        ],
    }
    journal = [{"status": "done", "criterion_id": "AC1", "summary": "made it"}]

    result = contract_gate.evaluate(contract, journal_records=journal)

    assert result["status"] == "met"


def test_evaluate_fail_open_on_garbage():
    # Non-dict contract and non-list journal must never crash.
    assert contract_gate.evaluate("not a dict", journal_records="nope") == {
        "status": "unmet",
        "met": [],
        "unmet": [],
    }
    assert contract_gate.evaluate(None, journal_records=None) == {
        "status": "unmet",
        "met": [],
        "unmet": [],
    }
    # A contract whose acceptance_criteria is the wrong type degrades to unmet.
    assert (
        contract_gate.evaluate(
            {"acceptance_criteria": "oops"}, journal_records=[]
        )["status"]
        == "unmet"
    )


def test_evaluate_no_criteria_is_unmet():
    # An empty criteria list is never "met" (nothing to fulfil = not closed).
    assert (
        contract_gate.evaluate({"acceptance_criteria": []}, journal_records=[])[
            "status"
        ]
        == "unmet"
    )


def test_evaluate_tolerates_junk_journal_entries():
    contract = {"acceptance_criteria": [{"id": "AC1", "text": "ship it"}]}
    journal = [
        None,
        "garbage string",
        123,
        {"status": "done", "criterion_id": "AC1", "summary": "shipped"},
    ]

    result = contract_gate.evaluate(contract, journal_records=journal)

    assert result["status"] == "met"


# ---------------------------------------------------------------------------
# is_valid_for_takeoff()
# ---------------------------------------------------------------------------


def test_is_valid_for_takeoff_true_for_concrete_contract():
    contract = {
        "acceptance_criteria": [
            {"id": "AC1", "text": "tests pass", "evidence": "/proof.txt"},
            {"id": "AC2", "text": "docs updated"},
        ],
        "evidence_required": True,
    }
    assert contract_gate.is_valid_for_takeoff(contract) is True


def test_is_valid_for_takeoff_true_with_per_criterion_evidence_only():
    # No top-level evidence_required, but a criterion names its own evidence.
    contract = {
        "acceptance_criteria": [
            {"id": "AC1", "text": "artifact built", "evidence": "/out/a.bin"},
        ],
    }
    assert contract_gate.is_valid_for_takeoff(contract) is True


def test_is_valid_for_takeoff_false_for_vision_tier_contract():
    # A vision/goal-tier contract with no acceptance_criteria cannot be closed.
    vision = {"id": "C0-polity-1", "goal": "establish a self-governing polity"}
    assert contract_gate.is_valid_for_takeoff(vision) is False


def test_is_valid_for_takeoff_false_without_evidence_notion():
    contract = {"acceptance_criteria": [{"id": "AC1", "text": "do something"}]}
    assert contract_gate.is_valid_for_takeoff(contract) is False


def test_is_valid_for_takeoff_false_when_criterion_not_concrete():
    # A criterion with neither id nor text is not concrete.
    contract = {
        "acceptance_criteria": [{"id": "AC1", "text": "real"}, {"note": ""}],
        "evidence_required": True,
    }
    assert contract_gate.is_valid_for_takeoff(contract) is False


def test_is_valid_for_takeoff_fail_open_on_garbage():
    assert contract_gate.is_valid_for_takeoff(None) is False
    assert contract_gate.is_valid_for_takeoff("nope") is False
    assert contract_gate.is_valid_for_takeoff([]) is False


# ---------------------------------------------------------------------------
# load_contract()
# ---------------------------------------------------------------------------


def _write_contract(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")


def test_load_contract_missing_returns_none(tmp_path):
    # Bare id with no matching file under the injected root -> None.
    assert contract_gate.load_contract("C9-nope", roots=[tmp_path]) is None
    # Explicit missing path -> None.
    assert contract_gate.load_contract(tmp_path / "ghost.yaml") is None
    # Empty / None id -> None.
    assert contract_gate.load_contract("") is None
    assert contract_gate.load_contract(None) is None


def test_load_contract_by_id_from_contracts_dir(tmp_path):
    _write_contract(
        tmp_path / "contracts" / "C2-work-1.yaml",
        """
        id: C2-work-1
        acceptance_criteria:
          - id: AC1
            text: do the work
        evidence_required: true
        """,
    )

    contract = contract_gate.load_contract("C2-work-1", roots=[tmp_path])

    assert contract is not None
    assert contract["id"] == "C2-work-1"
    assert contract["acceptance_criteria"][0]["id"] == "AC1"


def test_load_contract_by_id_from_ops_contracts_dir(tmp_path):
    _write_contract(
        tmp_path / "ops" / "contracts" / "C5-ops-1.yaml",
        """
        id: C5-ops-1
        acceptance_criteria: []
        """,
    )

    contract = contract_gate.load_contract("C5-ops-1", roots=[tmp_path])

    assert contract is not None
    assert contract["id"] == "C5-ops-1"


def test_load_contract_explicit_path(tmp_path):
    path = tmp_path / "custom.yaml"
    _write_contract(path, "id: custom\nacceptance_criteria: []\n")

    contract = contract_gate.load_contract(path)

    assert contract is not None
    assert contract["id"] == "custom"


def test_load_contract_non_mapping_yaml_returns_none(tmp_path):
    # A YAML that parses to a list (not a mapping) is not a usable contract.
    path = tmp_path / "contracts" / "bad.yaml"
    _write_contract(path, "- just\n- a\n- list\n")

    assert contract_gate.load_contract("bad", roots=[tmp_path]) is None


def test_load_contract_malformed_yaml_returns_none(tmp_path):
    path = tmp_path / "contracts" / "broken.yaml"
    _write_contract(path, "id: x\n  bad: : indentation: ::\n::::\n")

    assert contract_gate.load_contract("broken", roots=[tmp_path]) is None
