"""Tests for the durable per-turn work-journal write side (Phase 1).

All tests pass an explicit ``journal_dir`` (pytest ``tmp_path``) so they NEVER
touch the real ``~/LEOS`` / ``~/.hermes`` journals. Coverage:

* ``record_turn`` writes a single valid one-line JSON record with the expected
  keys, derived from a fake ``TurnResult``-shaped object.
* ``steps`` collapses consecutive same-kind runs and caps with an overflow
  marker.
* repeated ``record_turn`` calls append (file ends up with N lines).
* ``read_journal`` round-trips the written records and tolerates junk lines.
* fail-open: ``record_turn`` returns ``None`` and writes nothing when the
  target directory cannot be created/written.
"""

import json
from types import SimpleNamespace

from agent import work_journal


def _assistant_tool_call(name, arguments):
    """Build a projected assistant message carrying one tool call."""
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": f"call_{name}",
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ],
    }


def _tool_result(name, content):
    """Build the paired tool-result message (must NOT count as a step)."""
    return {"role": "tool", "tool_call_id": f"call_{name}", "content": content}


def _make_turn(projected_messages, final_text="all done", tool_iterations=0):
    return SimpleNamespace(
        projected_messages=projected_messages,
        final_text=final_text,
        tool_iterations=tool_iterations,
    )


def test_record_turn_writes_one_valid_line(tmp_path):
    turn = _make_turn(
        projected_messages=[
            _assistant_tool_call("exec_command", json.dumps({"command": "ls -la"})),
            _tool_result("exec_command", "file listing..."),
            {"role": "assistant", "content": "here is the result"},
        ],
        final_text="listed the directory",
        tool_iterations=1,
    )

    rec = work_journal.record_turn(
        turn,
        session_id="sess-1",
        contract_id="contract-x",
        turn_index=3,
        journal_dir=tmp_path,
    )

    assert rec is not None
    # Returned dict carries the expected shape.
    assert set(rec.keys()) == {
        "ts",
        "session_id",
        "contract_id",
        "turn_index",
        "summary",
        "steps",
        "tool_iterations",
        "status",
    }
    assert rec["session_id"] == "sess-1"
    assert rec["contract_id"] == "contract-x"
    assert rec["turn_index"] == 3
    assert rec["summary"] == "listed the directory"
    assert rec["status"] == "in_progress"
    assert rec["tool_iterations"] == 1
    # The exec_command tool call becomes one step; the tool result + plain
    # assistant text do NOT add steps.
    assert rec["steps"] == [{"kind": "exec_command", "target": "ls -la"}]

    # Exactly one line was written, and it parses back to the same record.
    path = tmp_path / "sess-1.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == rec


def test_summary_is_truncated(tmp_path):
    long_text = "x" * 1000
    rec = work_journal.record_turn(
        _make_turn([], final_text=long_text),
        session_id="sess-trunc",
        journal_dir=tmp_path,
    )
    assert rec is not None
    assert len(rec["summary"]) <= work_journal._SUMMARY_MAX
    assert rec["summary"].endswith("…")


def test_steps_collapse_consecutive_runs(tmp_path):
    # Three exec_commands in a row, then one apply_patch, then exec again.
    projected = []
    for i in range(3):
        projected.append(
            _assistant_tool_call("exec_command", json.dumps({"command": f"cmd{i}"}))
        )
        projected.append(_tool_result("exec_command", f"out{i}"))
    projected.append(
        _assistant_tool_call("apply_patch", json.dumps({"changes": ["a.py"]}))
    )
    projected.append(_tool_result("apply_patch", "ok"))
    projected.append(
        _assistant_tool_call("exec_command", json.dumps({"command": "final"}))
    )

    rec = work_journal.record_turn(
        _make_turn(projected), session_id="collapse", journal_dir=tmp_path
    )

    assert rec is not None
    kinds = [s["kind"] for s in rec["steps"]]
    # Runs collapse: exec(3)->1, apply_patch->1, exec->1 == 3 steps total.
    assert kinds == ["exec_command", "apply_patch", "exec_command"]
    # First exec target comes from the FIRST item of the collapsed run.
    assert rec["steps"][0]["target"] == "cmd0"


def test_steps_cap_with_overflow_marker(tmp_path):
    # 60 alternating distinct kinds so each is its own (non-collapsed) step,
    # forcing the cap + overflow marker.
    projected = []
    for i in range(60):
        name = "exec_command" if i % 2 == 0 else "apply_patch"
        projected.append(_assistant_tool_call(name, json.dumps({"command": str(i)})))

    rec = work_journal.record_turn(
        _make_turn(projected), session_id="overflow", journal_dir=tmp_path
    )

    assert rec is not None
    # _MAX_STEPS real steps + exactly one overflow marker.
    assert len(rec["steps"]) == work_journal._MAX_STEPS + 1
    overflow = rec["steps"][-1]
    assert overflow["kind"] == "...overflow"
    assert overflow["target"].startswith("+")


def test_multiple_calls_append(tmp_path):
    work_journal.record_turn(
        _make_turn([], final_text="first"),
        session_id="multi",
        journal_dir=tmp_path,
    )
    work_journal.record_turn(
        _make_turn([], final_text="second"),
        session_id="multi",
        journal_dir=tmp_path,
    )

    path = tmp_path / "multi.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["summary"] == "first"
    assert json.loads(lines[1])["summary"] == "second"


def test_read_journal_round_trips(tmp_path):
    work_journal.record_turn(
        _make_turn([], final_text="one"),
        session_id="rt",
        journal_dir=tmp_path,
    )
    work_journal.record_turn(
        _make_turn([], final_text="two"),
        session_id="rt",
        journal_dir=tmp_path,
    )

    records = work_journal.read_journal("rt", journal_dir=tmp_path)
    assert len(records) == 2
    assert [r["summary"] for r in records] == ["one", "two"]


def test_read_journal_tolerates_junk_and_missing(tmp_path):
    # Missing file -> [].
    assert work_journal.read_journal("nope", journal_dir=tmp_path) == []

    # A file with one good line, one blank line, one corrupt line.
    path = tmp_path / "junk.jsonl"
    good = json.dumps({"summary": "ok", "status": "in_progress"})
    path.write_text(good + "\n\nnot-json{{{\n", encoding="utf-8")

    records = work_journal.read_journal("junk", journal_dir=tmp_path)
    assert len(records) == 1
    assert records[0]["summary"] == "ok"


def test_record_turn_fail_open_returns_none(tmp_path):
    # Make the resolved journal dir UNCREATABLE by rooting it under a regular
    # file: mkdir(parents=True) then raises, and the guard must swallow it.
    blocker = tmp_path / "iam_a_file"
    blocker.write_text("not a directory", encoding="utf-8")
    bogus_dir = blocker / "subdir"  # parent is a file -> mkdir fails

    rec = work_journal.record_turn(
        _make_turn([], final_text="should not persist"),
        session_id="fail",
        journal_dir=bogus_dir,
    )

    assert rec is None
    # Nothing was written anywhere under tmp_path beyond the blocker file.
    assert not (bogus_dir / "fail.jsonl").exists()


def test_record_turn_fail_open_on_unwritable_open(tmp_path, monkeypatch):
    # Even if the directory exists, an open() failure must fail-open.
    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("agent.work_journal.open", _boom, raising=False)

    rec = work_journal.record_turn(
        _make_turn([], final_text="nope"),
        session_id="openfail",
        journal_dir=tmp_path,
    )

    assert rec is None
    assert not (tmp_path / "openfail.jsonl").exists()
