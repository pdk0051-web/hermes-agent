"""OAuth-only transient retry + static fallback + operator notification.

Compression is pinned to the OAuth backend and must never fall through to an
API-key provider.  So the compressor handles failures itself:

  * TRANSIENT failure (timeout / 5xx / dropped stream): retry in-process with
    an escalated request timeout, up to ``_SUMMARY_TRANSIENT_MAX_RETRIES``.
  * PAYMENT / credit exhaustion (402): NOT transient — the same request fails
    again — so skip the retry and go straight to the static fallback.
  * Retries exhausted: insert the deterministic static summary.

The gateway/CLI then notifies the operator about whichever outcome occurred
via :func:`_emit_compression_failure_notices`.
"""

from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from agent.context_compressor import (
    ContextCompressor,
    _SUMMARY_TRANSIENT_MAX_RETRIES,
    _SUMMARY_DEFAULT_TIMEOUT_SECONDS,
    _SUMMARY_TRANSIENT_TIMEOUT_MULTIPLIER,
)
from agent.conversation_compression import _emit_compression_failure_notices


def _msgs():
    return [
        {"role": "user", "content": "do something with the auth module"},
        {"role": "assistant", "content": "working on it"},
        {"role": "user", "content": "and add a test"},
        {"role": "assistant", "content": "done"},
    ]


def _ok_response(text="recovered summary"):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = text
    return resp


class _Timeout(Exception):
    """Transient request timeout (matches _is_timeout via message)."""

    def __init__(self, msg="read timeout while waiting for OAuth backend"):
        super().__init__(msg)


class _Timeout504(Exception):
    status_code = 504

    def __init__(self, msg="upstream timed out"):
        super().__init__(msg)


class _Payment402(Exception):
    status_code = 402

    def __init__(self, msg="insufficient credits"):
        super().__init__(msg)


class TestOAuthTransientRetry:
    """The OAuth backend retries transient failures with an escalated timeout
    even when ``summary_model`` IS the main model (the live LEO config:
    auxiliary.compression.model == main gpt-5.5)."""

    def test_timeout_retries_then_recovers(self):
        with patch("agent.context_compressor.get_model_context_length", return_value=100000):
            c = ContextCompressor(model="gpt-5.5", quiet_mode=True)  # summary_model == "" → main

        # First attempt times out, retry succeeds.
        with patch(
            "agent.context_compressor.call_llm",
            side_effect=[_Timeout(), _ok_response("recovered summary")],
        ) as mock_call:
            result = c._generate_summary(_msgs())

        assert mock_call.call_count == 2
        assert result is not None
        assert "recovered summary" in result
        # Retry bookkeeping recorded for the operator notice.
        assert c._last_summary_retry_count == 1
        assert c._last_summary_retry_error is not None
        # Success clears the hard-failure marker.
        assert c._last_summary_error is None

    def test_retry_uses_escalated_timeout(self):
        """The retry attempt must pass an explicit, larger timeout than the
        configured default so a slow-but-alive backend gets room to finish."""
        with patch("agent.context_compressor.get_model_context_length", return_value=100000):
            c = ContextCompressor(model="gpt-5.5", quiet_mode=True)

        with patch(
            "agent.context_compressor.call_llm",
            side_effect=[_Timeout504(), _ok_response()],
        ) as mock_call:
            c._generate_summary(_msgs())

        assert mock_call.call_count == 2
        # First attempt: no explicit timeout (call_llm reads it from config).
        assert "timeout" not in mock_call.call_args_list[0].kwargs
        # Retry attempt: explicit escalated timeout.
        retry_timeout = mock_call.call_args_list[1].kwargs.get("timeout")
        assert retry_timeout is not None
        expected = _SUMMARY_DEFAULT_TIMEOUT_SECONDS * _SUMMARY_TRANSIENT_TIMEOUT_MULTIPLIER
        assert retry_timeout == pytest.approx(expected)
        assert retry_timeout > _SUMMARY_DEFAULT_TIMEOUT_SECONDS

    def test_retries_are_bounded(self):
        """Transient failures retry at most _SUMMARY_TRANSIENT_MAX_RETRIES
        times, then give up (return None → caller inserts static summary)."""
        with patch("agent.context_compressor.get_model_context_length", return_value=100000):
            c = ContextCompressor(model="gpt-5.5", quiet_mode=True)

        # Always times out — should make 1 initial + N retries, then stop.
        with patch(
            "agent.context_compressor.call_llm",
            side_effect=_Timeout(),
        ) as mock_call:
            result = c._generate_summary(_msgs())

        assert result is None
        assert mock_call.call_count == 1 + _SUMMARY_TRANSIENT_MAX_RETRIES
        # Retry was attempted (operator gets notified), and a hard error is
        # recorded so the caller inserts the static fallback + notice.
        assert c._last_summary_retry_count == _SUMMARY_TRANSIENT_MAX_RETRIES
        assert c._last_summary_error is not None


class TestPaymentErrorSkipsRetry:
    """402 / credit exhaustion is NOT transient — no retry, straight to the
    static fallback.  This is the 402-vs-timeout branch split."""

    def test_payment_402_does_not_retry(self):
        with patch("agent.context_compressor.get_model_context_length", return_value=100000):
            c = ContextCompressor(model="gpt-5.5", quiet_mode=True)

        with patch(
            "agent.context_compressor.call_llm",
            side_effect=_Payment402(),
        ) as mock_call:
            result = c._generate_summary(_msgs())

        # Exactly one attempt — payment errors don't get a retry.
        assert mock_call.call_count == 1
        assert result is None
        assert c._last_summary_retry_count == 0
        assert c._last_summary_error is not None


class TestStaticFallbackAfterRetriesExhausted:
    """End-to-end through compress(): transient retries exhausted → the
    deterministic static summary is inserted (never an API-key fallback)."""

    def _make_msgs(self):
        # 8 messages (system + 7) with protect 2/2 reliably triggers a
        # compaction window — mirrors the proven setup in test_context_compressor.
        return [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "msg 1"},
            {"role": "assistant", "content": "msg 2"},
            {"role": "user", "content": "msg 3"},
            {"role": "assistant", "content": "msg 4"},
            {"role": "user", "content": "msg 5"},
            {"role": "assistant", "content": "msg 6"},
            {"role": "user", "content": "msg 7"},
        ]

    def test_compress_inserts_static_summary_on_exhausted_retries(self):
        with patch("agent.context_compressor.get_model_context_length", return_value=100000):
            c = ContextCompressor(
                model="gpt-5.5",
                quiet_mode=True,
                protect_first_n=2,
                protect_last_n=2,
            )

        with patch(
            "agent.context_compressor.call_llm",
            side_effect=_Timeout(),
        ) as mock_call:
            compressed = c.compress(self._make_msgs())

        # Retries happened (initial + bounded retries), all on the OAuth path.
        assert mock_call.call_count == 1 + _SUMMARY_TRANSIENT_MAX_RETRIES
        # A static fallback summary was inserted (compression still produced a
        # usable message list rather than aborting).
        assert c._last_summary_fallback_used is True
        assert c._last_summary_retry_count == _SUMMARY_TRANSIENT_MAX_RETRIES
        # The static fallback marker is present somewhere in the output.
        joined = "\n".join(
            m.get("content", "") for m in compressed if isinstance(m.get("content"), str)
        )
        assert "deterministic fallback" in joined.lower() or "reference only" in joined.lower()


class _FakeAgent:
    """Minimal agent stand-in for the notification helper.

    Records every _emit_warning call and carries the dedup attributes the
    helper reads/writes.
    """

    def __init__(self, compressor):
        self.context_compressor = compressor
        self.warnings: list[str] = []

    def _emit_warning(self, message: str) -> None:
        self.warnings.append(message)


def _compressor_with_flags(**flags):
    comp = SimpleNamespace(
        _last_summary_error=None,
        _last_summary_retry_count=0,
        _last_summary_retry_error=None,
        _last_aux_model_failure_model=None,
        _last_aux_model_failure_error=None,
    )
    for k, v in flags.items():
        setattr(comp, k, v)
    return comp


class TestCompressionFailureNotices:
    """_emit_compression_failure_notices renders the right operator notice for
    each outcome, and dedups so it doesn't spam on every compaction."""

    def test_static_fallback_after_retries_emits_warning(self):
        comp = _compressor_with_flags(
            _last_summary_error="read timeout while waiting for OAuth backend",
            _last_summary_retry_count=1,
            _last_summary_retry_error="read timeout",
        )
        agent = _FakeAgent(comp)
        _emit_compression_failure_notices(agent)

        assert len(agent.warnings) == 1
        msg = agent.warnings[0]
        assert "압축 실패" in msg
        assert "재시도 1회" in msg
        assert "정적 요약" in msg
        # OAuth-only is made explicit so the operator knows no API-key was used.
        assert "API-key" in msg

    def test_static_fallback_without_retry_emits_plain_warning(self):
        comp = _compressor_with_flags(
            _last_summary_error="insufficient credits",
            _last_summary_retry_count=0,
        )
        agent = _FakeAgent(comp)
        _emit_compression_failure_notices(agent)

        assert len(agent.warnings) == 1
        msg = agent.warnings[0]
        assert "압축 실패" in msg
        assert "정적 요약" in msg
        # No retry happened → don't claim retries.
        assert "재시도" not in msg

    def test_recovered_after_retry_emits_info_notice(self):
        comp = _compressor_with_flags(
            _last_summary_error=None,  # recovered → no hard error
            _last_summary_retry_count=1,
            _last_summary_retry_error="read timeout",
        )
        agent = _FakeAgent(comp)
        _emit_compression_failure_notices(agent)

        assert len(agent.warnings) == 1
        msg = agent.warnings[0]
        assert "일시 실패" in msg
        assert "복구" in msg
        assert "정적 요약 미사용" in msg

    def test_clean_compaction_emits_nothing(self):
        comp = _compressor_with_flags()  # all clean
        agent = _FakeAgent(comp)
        _emit_compression_failure_notices(agent)
        assert agent.warnings == []

    def test_notices_are_deduped_across_compactions(self):
        comp = _compressor_with_flags(
            _last_summary_error="read timeout",
            _last_summary_retry_count=1,
            _last_summary_retry_error="read timeout",
        )
        agent = _FakeAgent(comp)
        _emit_compression_failure_notices(agent)
        # Same failure on the next compaction — should NOT re-warn.
        _emit_compression_failure_notices(agent)
        assert len(agent.warnings) == 1
