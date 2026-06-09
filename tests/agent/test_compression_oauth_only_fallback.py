"""OAuth-only guard for context compression in auxiliary_client.call_llm.

Context compression is pinned to the user's OAuth Codex backend.  When that
backend fails — for ANY reason, transient or terminal — compression must NEVER
silently fall through to an API-key provider (OpenRouter / Nous).  The compressor
(agent/context_compressor.py) owns recovery for this task: retry with an
escalated timeout, notify the operator, then a deterministic static summary.

These tests pin that contract at the call_llm boundary:
  * task="compression": the cross-provider fallback helpers (_try_payment_fallback,
    _try_configured_fallback_chain, _try_main_agent_model_fallback) are NEVER
    invoked, and the original Codex error is re-raised to the compressor.
  * Other auxiliary tasks (e.g. web_extract) keep their existing payment/
    connection fallback behaviour — the guard is scoped to compression only.
"""

from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from agent.auxiliary_client import call_llm, async_call_llm


class _Payment402(Exception):
    status_code = 402

    def __init__(self, msg="insufficient credits / payment required"):
        super().__init__(msg)


class _Timeout504(Exception):
    status_code = 504

    def __init__(self, msg="upstream request timeout"):
        super().__init__(msg)


def _codex_client_raising(exc):
    """A MagicMock OpenAI-like client whose chat.completions.create raises exc."""
    client = MagicMock()
    client.base_url = "https://chatgpt.com/backend-api/codex"
    client.api_key = "codex-oauth-token"
    client.chat.completions.create.side_effect = exc
    return client


class TestCompressionNeverFallsToApiKeyProvider:
    """The headline guarantee: OAuth compression failure → no API-key fallback."""

    @pytest.mark.parametrize("exc", [_Payment402(), _Timeout504()])
    def test_compression_payment_or_timeout_does_not_invoke_fallback_chain(self, exc):
        client = _codex_client_raising(exc)

        with (
            patch(
                "agent.auxiliary_client._resolve_task_provider_model",
                return_value=("openai-codex", "gpt-5.5", None, None, "codex_responses"),
            ),
            patch("agent.auxiliary_client._get_cached_client", return_value=(client, "gpt-5.5")),
            patch("agent.auxiliary_client._try_payment_fallback") as mock_pay,
            patch("agent.auxiliary_client._try_configured_fallback_chain") as mock_cfg,
            patch("agent.auxiliary_client._try_main_agent_model_fallback") as mock_main,
            # _try_openrouter / _try_nous are reached only via the helpers above;
            # patch them too so a leak would be unmistakable.
            patch("agent.auxiliary_client._try_openrouter") as mock_or,
            patch("agent.auxiliary_client._try_nous") as mock_nous,
        ):
            with pytest.raises(type(exc)):
                call_llm(
                    task="compression",
                    messages=[{"role": "user", "content": "summarize this"}],
                )

        # The original Codex OAuth backend is retried at most once on the SAME
        # provider for a transient transport blip (5xx/timeout — upstream PR
        # #16587's shared same-provider retry), but a terminal payment/credit
        # error is tried exactly once.  Either way it stays on the OAuth
        # backend — the headline guarantee below is that NO API-key provider is
        # ever consulted for compression.
        _expected_calls = 2 if isinstance(exc, _Timeout504) else 1
        assert client.chat.completions.create.call_count == _expected_calls
        # No cross-provider fallback was attempted for compression.
        mock_pay.assert_not_called()
        mock_cfg.assert_not_called()
        mock_main.assert_not_called()
        mock_or.assert_not_called()
        mock_nous.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc", [_Payment402(), _Timeout504()])
    async def test_async_compression_does_not_invoke_fallback_chain(self, exc):
        client = MagicMock()
        client.base_url = "https://chatgpt.com/backend-api/codex"
        client.api_key = "codex-oauth-token"
        client.chat.completions.create = AsyncMock(side_effect=exc)

        with (
            patch(
                "agent.auxiliary_client._resolve_task_provider_model",
                return_value=("openai-codex", "gpt-5.5", None, None, "codex_responses"),
            ),
            patch("agent.auxiliary_client._get_cached_client", return_value=(client, "gpt-5.5")),
            patch("agent.auxiliary_client._try_payment_fallback") as mock_pay,
            patch("agent.auxiliary_client._try_configured_fallback_chain") as mock_cfg,
            patch("agent.auxiliary_client._try_main_agent_model_fallback") as mock_main,
            patch("agent.auxiliary_client._try_openrouter") as mock_or,
            patch("agent.auxiliary_client._try_nous") as mock_nous,
        ):
            with pytest.raises(type(exc)):
                await async_call_llm(
                    task="compression",
                    messages=[{"role": "user", "content": "summarize this"}],
                )

        # Same-provider retry once on a transient blip (timeout); exactly once
        # on a terminal payment error.  Never switches off the OAuth backend.
        _expected_calls = 2 if isinstance(exc, _Timeout504) else 1
        assert client.chat.completions.create.await_count == _expected_calls
        mock_pay.assert_not_called()
        mock_cfg.assert_not_called()
        mock_main.assert_not_called()
        mock_or.assert_not_called()
        mock_nous.assert_not_called()


class TestOtherTasksStillFallBack:
    """The guard is scoped to compression — other tasks keep payment fallback."""

    def test_web_extract_payment_error_still_consults_fallback_chain(self):
        client = _codex_client_raising(_Payment402())

        # A successful fallback client so call_llm returns instead of raising.
        fb_client = MagicMock()
        fb_client.base_url = "https://openrouter.ai/api/v1"
        fb_client.chat.completions.create.return_value = {"ok": True}

        with (
            patch(
                "agent.auxiliary_client._resolve_task_provider_model",
                return_value=("openai-codex", "gpt-5.5", None, None, "codex_responses"),
            ),
            patch("agent.auxiliary_client._get_cached_client", return_value=(client, "gpt-5.5")),
            patch("agent.auxiliary_client._mark_provider_unhealthy"),
            # Explicit-provider path: configured chain consulted first, then main.
            patch(
                "agent.auxiliary_client._try_configured_fallback_chain",
                return_value=(None, None, ""),
            ) as mock_cfg,
            patch(
                "agent.auxiliary_client._try_main_agent_model_fallback",
                return_value=(fb_client, "fb-model", "main-agent(openrouter)"),
            ) as mock_main,
            patch("agent.auxiliary_client._validate_llm_response", side_effect=lambda resp, _task: resp),
        ):
            result = call_llm(
                task="web_extract",
                messages=[{"role": "user", "content": "extract this"}],
            )

        assert result == {"ok": True}
        # web_extract DID consult the fallback chain (contrast with compression).
        mock_cfg.assert_called_once()
        mock_main.assert_called_once()
        assert fb_client.chat.completions.create.call_count == 1
