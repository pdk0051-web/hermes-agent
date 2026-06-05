"""Tests for the flag-gated journal-curation compaction branch.

Covers ``agent/context_compressor.py`` ``ContextCompressor.compress()`` when
``compression.curation_enabled`` is on/off. The whole point of the feature is
SAFETY: with the flag off (the default) compaction must be a strict no-op
relative to the pre-existing flat-summary behavior, and with the flag on,
ONLY middle messages that confidently map to a *done* work-journal record may
be evicted (replaced by a one-line ``[done] …`` marker) — everything uncertain
is kept and still summarized.

No network: the LLM summarizer (``_generate_summary``) is stubbed so we can
observe exactly which messages it was handed, and the work-journal reader
(``read_journal``) is patched to inject deterministic records.
"""

from unittest.mock import patch

import pytest

from agent.context_compressor import ContextCompressor, SUMMARY_PREFIX


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_compressor(curation: bool):
    """Build a compressor with a tiny head/tail and the flag forced on/off.

    ``_curation_enabled`` is patched directly (rather than threading a fake
    config through ``load_config``) so each test states its intent plainly;
    a separate config-backed test proves the real default is off.
    """
    with patch(
        "agent.context_compressor.get_model_context_length", return_value=100000
    ):
        c = ContextCompressor(
            model="test/model",
            threshold_percent=0.85,
            protect_first_n=1,
            protect_last_n=1,
            quiet_mode=True,
        )
    c._session_id = "sess-curation"
    patcher = patch.object(
        ContextCompressor, "_curation_enabled", staticmethod(lambda: curation)
    )
    patcher.start()
    return c, patcher


def _stub_summary(c):
    """Patch ``_generate_summary`` to a deterministic stub that records its
    input and returns a prefixed summary, so no LLM is called."""
    seen = {}

    def _fake(turns_to_summarize, focus_topic=None):
        seen["turns"] = list(turns_to_summarize)
        return f"{SUMMARY_PREFIX}\nSTUB SUMMARY of {len(turns_to_summarize)} turns"

    p = patch.object(c, "_generate_summary", side_effect=_fake)
    p.start()
    return seen, p


def _conversation():
    """A conversation: system head, a middle of distinct turns, then enough
    later turns that token-budget tail protection leaves a real middle window.

    The middle window (computed by the compressor for this fixture) spans the
    three distinctive assistant turns plus several filler turns; the last
    filler turns and the final user ask fall into the protected tail. Middle
    assistant turns carry distinctive text so a journal record's ``summary``
    can reference them by content, while the filler turns map to nothing and
    therefore must be kept (bias-to-keep) and summarized.
    """
    middle = [
        {
            "role": "assistant",
            "content": "Implemented the rate limiter in middleware and added unit coverage.",
        },
        {
            "role": "assistant",
            "content": "Refactored the database connection pool to reuse sockets across requests.",
        },
        {
            "role": "assistant",
            "content": "Let me think about the caching strategy before deciding anything.",
        },
    ]
    filler = [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": f"filler tail turn number {i} with some words to add tokens " * 4,
        }
        for i in range(8)
    ]
    return [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "head user ask (protected first_n)"},
        *middle,
        *filler,
    ]


# ---------------------------------------------------------------------------
# Safety regression: flag OFF must be a strict no-op
# ---------------------------------------------------------------------------


class TestFlagOffNoOp:
    def test_off_path_matches_flat_behavior_byte_for_byte(self):
        msgs = _conversation()

        # Flat baseline: curation OFF.
        c_off, fp = _make_compressor(curation=False)
        seen_off, sp = _stub_summary(c_off)
        try:
            with patch("agent.work_journal.read_journal") as rj:
                out_off = c_off.compress(list(msgs))
            # Journal must never even be consulted when the flag is off.
            rj.assert_not_called()
        finally:
            sp.stop()
            fp.stop()

        # ON but with a journal that DOES mark middle work done — yet we still
        # expect identical output, because the comparison below is OFF vs OFF
        # is not enough; the real safety claim is "OFF == pre-change flat".
        # Re-run OFF a second time to prove determinism of the flat path.
        c_off2, fp2 = _make_compressor(curation=False)
        seen_off2, sp2 = _stub_summary(c_off2)
        try:
            with patch("agent.work_journal.read_journal", return_value=[
                {"status": "done", "summary": "Implemented the rate limiter in middleware and added unit coverage.", "steps": []},
            ]) as rj2:
                out_off2 = c_off2.compress(list(msgs))
            rj2.assert_not_called()
        finally:
            sp2.stop()
            fp2.stop()

        # Same messages went to the summarizer both times (no eviction).
        assert seen_off["turns"] == seen_off2["turns"]
        # And the full compressed transcript is identical.
        assert out_off == out_off2
        # No journal marker leaked into the summarizer input or output.
        combined = "\n".join(str(m.get("content", "")) for m in out_off)
        assert "[done]" not in combined
        assert "evicted to work-journal" not in combined

    def test_real_default_config_is_strict_noop(self):
        """The load-bearing safety proof for the live session: with the REAL
        (unpatched) flag reader and the REAL default config (curation off),
        a journal stuffed with done records that EXACTLY match middle turns
        still triggers ZERO eviction — output equals the flat path's output.
        """
        from hermes_cli.config import DEFAULT_CONFIG

        msgs = _conversation()

        # Real flag reader, real default config (curation_enabled defaults off).
        with patch(
            "agent.context_compressor.get_model_context_length", return_value=100000
        ):
            c_real = ContextCompressor(
                model="test/model",
                threshold_percent=0.85,
                protect_first_n=1,
                protect_last_n=1,
                quiet_mode=True,
            )
        c_real._session_id = "sess-curation"
        seen_real, sp = _stub_summary(c_real)

        # A journal that WOULD evict if curation were on.
        journal = [
            {
                "status": "done",
                "summary": "Implemented the rate limiter in middleware and added unit coverage.",
                "steps": [{"kind": "apply_patch", "target": "middleware/ratelimit.py"}],
            },
            {
                "status": "done",
                "summary": "Refactored the database connection pool to reuse sockets across requests.",
                "steps": [{"kind": "exec_command", "target": "pytest tests/db"}],
            },
        ]
        try:
            # NOTE: _curation_enabled is NOT patched here. We feed the real
            # default config (no curation_enabled key set → False) so the
            # branch must be skipped and the journal never read.
            with patch(
                "hermes_cli.config.load_config",
                return_value={"compression": dict(DEFAULT_CONFIG["compression"])},
            ):
                with patch("agent.work_journal.read_journal", return_value=journal) as rj:
                    out_real = c_real.compress(list(msgs))
                rj.assert_not_called()
        finally:
            sp.stop()

        # Flat baseline with curation explicitly forced off for byte compare.
        c_flat, fp = _make_compressor(curation=False)
        seen_flat, sp2 = _stub_summary(c_flat)
        try:
            with patch("agent.work_journal.read_journal", return_value=journal):
                out_flat = c_flat.compress(list(msgs))
        finally:
            sp2.stop()
            fp.stop()

        # Byte-for-byte identical to the flat path; no markers; same summarizer input.
        assert out_real == out_flat
        assert seen_real["turns"] == seen_flat["turns"]
        combined = "\n".join(str(m.get("content", "")) for m in out_real)
        assert "[done]" not in combined
        assert "evicted to work-journal" not in combined

    def test_config_default_is_off(self):
        """The real (unpatched) flag reader returns False by default."""
        from hermes_cli.config import DEFAULT_CONFIG

        assert (
            DEFAULT_CONFIG["compression"].get("curation_enabled", False) is False
        )
        # And the live reader honors that default when config lacks the key.
        with patch(
            "hermes_cli.config.load_config", return_value={"compression": {}}
        ):
            assert ContextCompressor._curation_enabled() is False


# ---------------------------------------------------------------------------
# Flag ON: confident done work is evicted; the rest is still summarized
# ---------------------------------------------------------------------------


class TestFlagOnEviction:
    def test_two_done_middle_messages_evicted_rest_summarized(self):
        msgs = _conversation()
        c, fp = _make_compressor(curation=True)
        seen, sp = _stub_summary(c)

        # Journal marks the first two middle assistant turns done (by content
        # reference); the third ("Let me think…") and the user turn are not.
        journal = [
            {
                "status": "done",
                "summary": "Implemented the rate limiter in middleware and added unit coverage.",
                "steps": [{"kind": "apply_patch", "target": "middleware/ratelimit.py"}],
            },
            {
                "status": "completed",
                "summary": "Refactored the database connection pool to reuse sockets across requests.",
                "steps": [{"kind": "exec_command", "target": "pytest tests/db"}],
            },
        ]

        try:
            with patch("agent.work_journal.read_journal", return_value=journal):
                out = c.compress(list(msgs))
        finally:
            sp.stop()
            fp.stop()

        # The two done turns were NOT handed to the summarizer.
        summarized_text = "\n".join(
            str(m.get("content", "")) for m in seen["turns"]
        )
        assert "rate limiter in middleware" not in summarized_text
        assert "connection pool to reuse sockets" not in summarized_text
        # The non-done middle turns WERE still summarized.
        assert "caching strategy before deciding" in summarized_text

        # One-line journal markers appear in the compacted output.
        combined = "\n".join(str(m.get("content", "")) for m in out)
        assert "[done] apply_patch middleware/ratelimit.py" in combined
        assert "[done] exec_command pytest tests/db" in combined

        # Compaction shrank the transcript overall.
        assert len(out) < len(msgs)

        # The real eviction signal: the curated path handed FEWER turns to the
        # summarizer than the flat path would have (exactly the 2 done turns
        # fewer), because those 2 were diverted to markers instead.
        c_flat, fp2 = _make_compressor(curation=False)
        seen_flat, sp2 = _stub_summary(c_flat)
        try:
            with patch("agent.work_journal.read_journal", return_value=journal):
                c_flat.compress(list(msgs))
        finally:
            sp2.stop()
            fp2.stop()
        assert len(seen["turns"]) == len(seen_flat["turns"]) - 2

        # Head (system + first user) and the protected tail preserved. The
        # system message gains the standard compaction note (pre-existing flat
        # behavior), so assert the original content survives as a prefix.
        assert out[0]["content"].startswith(msgs[0]["content"])
        assert out[1]["content"] == msgs[1]["content"]
        assert out[-1]["content"] == msgs[-1]["content"]
        assert out[-1]["content"] == "filler tail turn number 7 with some words to add tokens " * 4
        # The verbatim done-work text is gone from the transcript (only the
        # marker remains — its meaning lives in the journal).
        assert "Implemented the rate limiter in middleware" not in combined

    def test_contract_header_first_message_preserved(self):
        """A contract/header user message in the protected head is never
        evicted even if a done record references it."""
        msgs = _conversation()
        # Make the protected-head user message look like done work in the
        # journal — it must still survive because it is in the head, not the
        # middle window.
        c, fp = _make_compressor(curation=True)
        seen, sp = _stub_summary(c)
        journal = [
            {
                "status": "done",
                "summary": "head user ask (protected first_n)",
                "steps": [{"kind": "noop", "target": "head"}],
            },
            {
                "status": "done",
                "summary": "Implemented the rate limiter in middleware and added unit coverage.",
                "steps": [{"kind": "apply_patch", "target": "middleware/ratelimit.py"}],
            },
        ]
        try:
            with patch("agent.work_journal.read_journal", return_value=journal):
                out = c.compress(list(msgs))
        finally:
            sp.stop()
            fp.stop()

        # Head user message preserved verbatim (not turned into a marker).
        assert out[1]["content"] == "head user ask (protected first_n)"


# ---------------------------------------------------------------------------
# Bias-to-keep: uncertain / unmappable middle messages are kept, not evicted
# ---------------------------------------------------------------------------


class TestBiasToKeep:
    def test_unmappable_middle_message_is_kept(self):
        msgs = _conversation()
        c, fp = _make_compressor(curation=True)
        seen, sp = _stub_summary(c)

        # Journal has a done record, but its summary references NOTHING in the
        # transcript (no content/target overlap) — so no message maps with
        # confidence, and curation must fall back to the flat path entirely.
        journal = [
            {
                "status": "done",
                "summary": "Totally unrelated work on a different subsystem nobody mentioned.",
                "steps": [{"kind": "exec_command", "target": "deploy --prod"}],
            }
        ]
        try:
            with patch("agent.work_journal.read_journal", return_value=journal):
                out = c.compress(list(msgs))
        finally:
            sp.stop()
            fp.stop()

        # Nothing evicted: no markers, and every original middle message was
        # handed to the summarizer (same as the flat path).
        combined = "\n".join(str(m.get("content", "")) for m in out)
        assert "[done]" not in combined
        summarized_text = "\n".join(
            str(m.get("content", "")) for m in seen["turns"]
        )
        assert "rate limiter in middleware" in summarized_text
        assert "connection pool to reuse sockets" in summarized_text
        assert "caching strategy before deciding" in summarized_text

    def test_in_progress_record_does_not_evict(self):
        """A middle message whose only journal match is in-progress is KEPT."""
        msgs = _conversation()
        c, fp = _make_compressor(curation=True)
        seen, sp = _stub_summary(c)
        journal = [
            {
                # exact content match, but NOT a done-like status → keep.
                "status": "in_progress",
                "summary": "Implemented the rate limiter in middleware and added unit coverage.",
                "steps": [{"kind": "apply_patch", "target": "middleware/ratelimit.py"}],
            }
        ]
        try:
            with patch("agent.work_journal.read_journal", return_value=journal):
                out = c.compress(list(msgs))
        finally:
            sp.stop()
            fp.stop()

        combined = "\n".join(str(m.get("content", "")) for m in out)
        assert "[done]" not in combined
        summarized_text = "\n".join(
            str(m.get("content", "")) for m in seen["turns"]
        )
        assert "rate limiter in middleware" in summarized_text


# ---------------------------------------------------------------------------
# Fail-safe: journal missing / empty → fall back to the flat path, no crash
# ---------------------------------------------------------------------------


class TestFailSafe:
    def test_empty_journal_falls_back_to_flat(self):
        msgs = _conversation()
        c, fp = _make_compressor(curation=True)
        seen, sp = _stub_summary(c)
        try:
            with patch("agent.work_journal.read_journal", return_value=[]):
                out = c.compress(list(msgs))
        finally:
            sp.stop()
            fp.stop()

        combined = "\n".join(str(m.get("content", "")) for m in out)
        assert "[done]" not in combined
        # Every middle message still summarized (flat behavior).
        summarized_text = "\n".join(
            str(m.get("content", "")) for m in seen["turns"]
        )
        assert "rate limiter in middleware" in summarized_text
        assert "connection pool to reuse sockets" in summarized_text
        assert "caching strategy before deciding" in summarized_text

    def test_unknown_session_id_falls_back_without_reading_journal(self):
        msgs = _conversation()
        c, fp = _make_compressor(curation=True)
        c._session_id = None  # session id unknown → must not read the journal
        seen, sp = _stub_summary(c)
        try:
            with patch("agent.work_journal.read_journal") as rj:
                out = c.compress(list(msgs))
            rj.assert_not_called()
        finally:
            sp.stop()
            fp.stop()

        combined = "\n".join(str(m.get("content", "")) for m in out)
        assert "[done]" not in combined

    def test_journal_read_error_falls_back_without_crash(self):
        msgs = _conversation()
        c, fp = _make_compressor(curation=True)
        seen, sp = _stub_summary(c)
        try:
            with patch(
                "agent.work_journal.read_journal",
                side_effect=RuntimeError("journal blew up"),
            ):
                out = c.compress(list(msgs))  # must NOT raise
        finally:
            sp.stop()
            fp.stop()

        combined = "\n".join(str(m.get("content", "")) for m in out)
        assert "[done]" not in combined
        summarized_text = "\n".join(
            str(m.get("content", "")) for m in seen["turns"]
        )
        assert "rate limiter in middleware" in summarized_text


# ---------------------------------------------------------------------------
# on_session_start captures the session id used by the curation branch
# ---------------------------------------------------------------------------


def test_on_session_start_captures_session_id():
    with patch(
        "agent.context_compressor.get_model_context_length", return_value=100000
    ):
        c = ContextCompressor(model="test/model", quiet_mode=True)
    assert c._session_id is None
    c.on_session_start("sess-abc", hermes_home="/tmp", platform="cli")
    assert c._session_id == "sess-abc"
