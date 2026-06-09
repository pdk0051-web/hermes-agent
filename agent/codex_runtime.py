"""Codex API runtime — App Server and Responses-API streaming paths.

Extracted from :class:`AIAgent` to keep the agent loop file focused.
Each function takes the parent ``AIAgent`` as its first argument
(``agent``).  AIAgent keeps thin forwarder methods for backward
compatibility.

* ``run_codex_app_server_turn`` — drives one turn through the
  ``codex_app_server`` subprocess client (used when a Codex CLI install
  is the active provider).
* ``run_codex_stream`` — streams a Codex Responses API call (the
  ``codex_responses`` api_mode).
* ``run_codex_create_stream_fallback`` — recovery path when the
  Responses ``stream=True`` initial create fails.
"""

from __future__ import annotations

import logging
import os
import time
from types import SimpleNamespace
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def _coerce_usage_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(int(value), 0)
    if isinstance(value, str):
        try:
            return max(int(value), 0)
        except ValueError:
            return 0
    return 0


def _record_codex_app_server_usage(agent, turn) -> dict[str, Any]:
    """Translate Codex app-server token usage into Hermes accounting.

    Codex app-server reports usage via thread/tokenUsage/updated as:
    inputTokens, cachedInputTokens, outputTokens, reasoningOutputTokens,
    totalTokens.

    Hermes' canonical prompt bucket includes uncached input + cached input.
    The Codex app-server protocol does not currently expose cache-write tokens,
    so that bucket remains zero on this runtime.

    Even when Codex omits usage for a turn, Hermes should still count that turn
    as one API call for session/status accounting.
    """
    agent.session_api_calls += 1

    usage = getattr(turn, "token_usage_last", None)
    if not isinstance(usage, dict) or not usage:
        if agent._session_db and agent.session_id:
            try:
                if not agent._session_db_created:
                    agent._ensure_db_session()
                agent._session_db.update_token_counts(
                    agent.session_id,
                    model=agent.model,
                    api_call_count=1,
                )
            except Exception as exc:
                logger.debug(
                    "Codex app-server api-call persistence failed (session=%s): %s",
                    agent.session_id, exc,
                )
        return {}

    from agent.usage_pricing import CanonicalUsage, estimate_usage_cost

    input_tokens = _coerce_usage_int(usage.get("inputTokens"))
    cache_read_tokens = _coerce_usage_int(usage.get("cachedInputTokens"))
    output_tokens = _coerce_usage_int(usage.get("outputTokens"))
    reasoning_tokens = _coerce_usage_int(usage.get("reasoningOutputTokens"))
    reported_total = _coerce_usage_int(usage.get("totalTokens"))

    canonical_usage = CanonicalUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=0,
        reasoning_tokens=reasoning_tokens,
        raw_usage=usage,
    )
    prompt_tokens = canonical_usage.prompt_tokens
    completion_tokens = canonical_usage.output_tokens
    total_tokens = reported_total or canonical_usage.total_tokens
    usage_dict = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "input_tokens": canonical_usage.input_tokens,
        "output_tokens": canonical_usage.output_tokens,
        "cache_read_tokens": canonical_usage.cache_read_tokens,
        "cache_write_tokens": canonical_usage.cache_write_tokens,
        "reasoning_tokens": canonical_usage.reasoning_tokens,
    }

    compressor = getattr(agent, "context_compressor", None)
    if compressor is not None:
        try:
            compressor.update_from_response(usage_dict)
            context_window = getattr(turn, "model_context_window", None)
            if isinstance(context_window, int) and context_window > 0:
                compressor.context_length = context_window
        except Exception:
            logger.debug("codex app-server usage update failed", exc_info=True)

    agent.session_prompt_tokens += prompt_tokens
    agent.session_completion_tokens += completion_tokens
    agent.session_total_tokens += total_tokens
    agent.session_input_tokens += canonical_usage.input_tokens
    agent.session_output_tokens += canonical_usage.output_tokens
    agent.session_cache_read_tokens += canonical_usage.cache_read_tokens
    agent.session_cache_write_tokens += canonical_usage.cache_write_tokens
    agent.session_reasoning_tokens += canonical_usage.reasoning_tokens

    cost_result = estimate_usage_cost(
        agent.model,
        canonical_usage,
        provider=agent.provider,
        base_url=agent.base_url,
        api_key=getattr(agent, "api_key", ""),
    )
    if cost_result.amount_usd is not None:
        agent.session_estimated_cost_usd += float(cost_result.amount_usd)
    agent.session_cost_status = cost_result.status
    agent.session_cost_source = cost_result.source

    if agent._session_db and agent.session_id:
        try:
            if not agent._session_db_created:
                agent._ensure_db_session()
            agent._session_db.update_token_counts(
                agent.session_id,
                input_tokens=canonical_usage.input_tokens,
                output_tokens=canonical_usage.output_tokens,
                cache_read_tokens=canonical_usage.cache_read_tokens,
                cache_write_tokens=canonical_usage.cache_write_tokens,
                reasoning_tokens=canonical_usage.reasoning_tokens,
                estimated_cost_usd=float(cost_result.amount_usd)
                if cost_result.amount_usd is not None else None,
                cost_status=cost_result.status,
                cost_source=cost_result.source,
                billing_provider=agent.provider,
                billing_base_url=agent.base_url,
                billing_mode="subscription_included"
                if cost_result.status == "included" else None,
                model=agent.model,
                api_call_count=1,
            )
        except Exception as exc:
            logger.debug(
                "Codex app-server token persistence failed (session=%s, tokens=%d): %s",
                agent.session_id, total_tokens, exc,
            )

    return {
        **usage_dict,
        "last_prompt_tokens": prompt_tokens,
        "estimated_cost_usd": float(cost_result.amount_usd)
        if cost_result.amount_usd is not None else None,
        "cost_status": cost_result.status,
        "cost_source": cost_result.source,
    }


def run_codex_app_server_turn(
    agent,
    *,
    user_message: str,
    original_user_message: Any,
    messages: List[Dict[str, Any]],
    effective_task_id: str,
    should_review_memory: bool = False,
) -> Dict[str, Any]:
    """Codex app-server runtime path. Hands the entire turn to a `codex
    app-server` subprocess and projects its events back into Hermes'
    messages list so memory/skill review keep working.

    Called from run_conversation() when agent.api_mode == "codex_app_server".
    Returns the same dict shape as the chat_completions path.
    """
    from agent.transports.codex_app_server_session import CodexAppServerSession

    # Per-turn progress state for the meaningful-unit bridge below. Reset at
    # the very start of every turn (before the session runs) so the phase
    # collapsing and step counter start fresh each message. Fail-open.
    try:
        agent._codex_phase = None
        agent._codex_step_count = 0
        agent._codex_last_emit_ts = 0.0
    except Exception:
        pass

    # Lazy session: one CodexAppServerSession per AIAgent instance.
    # Spawned on first turn, reused across turns, closed at AIAgent
    # shutdown (see _cleanup hook).
    if not hasattr(agent, "_codex_session") or agent._codex_session is None:
        cwd = getattr(agent, "session_cwd", None) or os.getcwd()
        # Approval callback: defer to Hermes' standard prompt flow if a
        # CLI thread has installed one. Gateway / cron contexts get the
        # codex-side fail-closed default.
        try:
            from tools.terminal_tool import _get_approval_callback
            approval_callback = _get_approval_callback()
        except Exception:
            approval_callback = None
        # LEOS: surface codex's INTERNAL tool activity (exec / file edits / mcp)
        # as Hermes tool-progress bubbles. The codex_app_server runtime runs
        # tools INSIDE codex, so without this the live trace that the
        # chat_completions path shows is invisible in Slack (every turn looked
        # like api_calls=1 with no progress).
        #
        # Rather than echoing EVERY raw codex tool (read_file/terminal/search —
        # too granular/noisy for a non-technical PM), we collapse runs of
        # similar activity into a single MEANINGFUL UNIT: each item is mapped
        # to a (emoji, label) "phase" — LEOS domain first (법·헌법 확인 / 계약
        # 작업 / 원장 기록 / 통치·게이트 …), else a generic dev phase (코드
        # 살펴보는 중 / 테스트 / 빌드 …) — and a line is emitted ONLY when the
        # phase CHANGES. A per-turn step counter (agent._codex_step_count)
        # increments on every item and rides along as the preview ("N단계째"),
        # giving a real progress signal (the heartbeat's "iteration N/60" is
        # meaningless here — it counts Hermes API calls, which stay 0).
        #
        # Reads the per-turn agent.tool_progress_callback dynamically
        # (reassigned each message) and fires on item/started. Fully fail-open —
        # a display error never breaks the codex turn (caller also wraps
        # on_event).
        def _dewrap_codex_command(raw):
            # Codex runs everything as a shell command (e.g.
            # /bin/zsh -lc "sed -n '1,5p' file"). Strip the login-shell wrapper
            # so the inner command can be classified.
            import re as _re
            s = str(raw or "").strip()
            m = _re.match(r"^\S*(?:zsh|bash|sh|dash)\s+-[a-z]*c\s+(.*)$", s, _re.DOTALL)
            if m:
                inner = m.group(1).strip()
                if len(inner) >= 2 and inner[0] in "\"'" and inner[-1] == inner[0]:
                    inner = inner[1:-1].strip()
                s = inner or s
            return s

        # Reader/searcher/lister command verbs → "코드 살펴보는 중".
        _READ = {"cat", "sed", "head", "tail", "less", "more", "bat", "nl", "awk", "jq"}
        _SEARCH = {"rg", "grep", "egrep", "fgrep", "ag", "ack"}
        _LIST = {"ls", "find", "fd", "tree", "exa", "eza", "stat", "du"}

        def _domain_phase(item, text):
            # DOMAIN classification delegated to plugins (de-fork Stage 6).
            #
            # The LEOS governance labels (⚖️ 법·헌법 확인 / 📜 계약 작업 /
            # 📒 원장 기록 / 🏛️ 통치·게이트 / 🤝 조약 / 🔎 감사·검토 /
            # 🏛️ LEOS 작업, with the /leos/-context gate) used to live inline
            # here. They were LEOS doctrine, not generic dev tooling, so they
            # were extracted into the out-of-tree leos-governor plugin and the
            # core now fires the generic ``classify_codex_progress_phase`` hook
            # instead. A plugin callback receives the raw ``item`` plus the
            # already-dewrapped ``text`` (command / path / mcp blob / skill name)
            # and may return a truthy ``(emoji, label)`` tuple to OVERRIDE the
            # generic label below; the first truthy tuple wins.
            #
            # With NO plugin registered (the shipped default) ``invoke_hook``
            # returns an empty list → ``None`` here → the generic dev classifier
            # runs unchanged. Fully fail-open: importing the hook machinery is
            # local (avoid an import cycle: codex_runtime ← agent ← plugins) and
            # ANY error degrades to the generic label — a label miss must NEVER
            # break a codex turn.
            try:
                from hermes_cli.plugins import invoke_hook as _invoke_hook
                for _res in _invoke_hook(
                    "classify_codex_progress_phase", item=item, text=text
                ):
                    if _res:
                        return _res
            except Exception:
                pass
            return None

        def _codex_phase(item):
            # Map a codex item → (emoji, label, domain). A DOMAIN label from a
            # plugin (via classify_codex_progress_phase) wins; otherwise a
            # generic dev phase based on the item type / command verb. ``domain``
            # is True only when a plugin produced the label — domain labels
            # bypass the generic-dev throttle (they are milestones, not chatter).
            itype = item.get("type") or ""
            if itype == "commandExecution":
                cmd = _dewrap_codex_command(item.get("command"))
                domain = _domain_phase(item, cmd)
                if domain:
                    return (domain[0], domain[1], True)
                tokens = cmd.split()
                base = tokens[0].rsplit("/", 1)[-1] if tokens else ""
                low = cmd.lower()
                if base in _READ or base in _SEARCH or base in _LIST:
                    return ("📂", "코드 살펴보는 중", False)
                if "pytest" in low or "run_tests" in low or base.startswith("test"):
                    return ("🧪", "테스트", False)
                if (base in ("npm", "vite", "webpack", "tsc", "yarn", "pnpm")
                        or "npm run build" in low):
                    return ("🔨", "빌드", False)
                if base == "git":
                    return ("📦", "커밋·git", False)
                return ("💻", "명령 실행", False)
            if itype == "fileChange":
                path = str(item.get("path") or item.get("changes")
                           or item.get("files") or "")
                domain = _domain_phase(item, path)
                if domain:
                    return (domain[0], domain[1], True)
                return ("✏️", "코드 고치는 중", False)
            if itype == "mcpToolCall":
                blob = (str(item.get("server") or "") + " "
                        + str(item.get("tool") or ""))
                domain = _domain_phase(item, blob)
                if domain:
                    return (domain[0], domain[1], True)
                return ("🧰", "도구 사용", False)
            if itype == "dynamicToolCall":
                name = str(item.get("name") or "")
                domain = _domain_phase(item, name)
                if domain:
                    return (domain[0], domain[1], True)
                return ("🧰", "스킬 사용", False)
            # Unknown / non-tool item (e.g. reasoning) — no phase.
            return None

        def _codex_progress_on_event(note):
            try:
                cb = getattr(agent, "tool_progress_callback", None)
                if cb is None or note.get("method") != "item/started":
                    return
                item = (note.get("params") or {}).get("item") or {}
                # LEOS_STATE marker — an EXPLICIT, race-free state transition the
                # agent emits as a no-op shell command (`: LEOS_STATE <emoji>
                # <label> [:: <detail>]`). The `:` POSIX no-op carries the state
                # INTO codex's commandExecution stream (which this bridge already
                # sees), so the state IS the command — nothing for us to read
                # mid-write. Handled BEFORE the step-count bump / classification,
                # only for commandExecution items. A marker is a signal, not work:
                # it does NOT bump _codex_step_count, and it emits IMMEDIATELY
                # (bypassing the 30s generic throttle) while still deduping vs the
                # current phase. NB: `_codex_phase` here is the agent ATTRIBUTE
                # (the last emitted state), NOT the nested `_codex_phase(item)`
                # classifier function above.
                if (item.get("type") or "") == "commandExecution":
                    import re as _re
                    _marker_cmd = _dewrap_codex_command(item.get("command"))
                    _m = _re.match(r"^:?\s*LEOS_STATE\s+(.+)$", _marker_cmd, _re.DOTALL)
                    if _m:
                        _cap = _m.group(1)
                        _label_part, _sep, _detail = _cap.partition("::")
                        label = _label_part.strip()
                        detail = _detail.strip()
                        if label and label != getattr(agent, "_codex_phase", None):
                            now = time.time()
                            agent._codex_phase = label
                            agent._codex_last_emit_ts = now
                            cb("tool.started", tool_name=label, preview=detail)
                        return
                phase = _codex_phase(item)
                if phase is None:
                    return
                # Step counter advances on EVERY recognized item, even when the
                # phase is unchanged / suppressed and no line is emitted.
                agent._codex_step_count = getattr(agent, "_codex_step_count", 0) + 1
                emoji, label, is_domain = phase
                # Meaningful unit: emit only when the activity SHIFTS.
                changed = label != getattr(agent, "_codex_phase", None)
                if not changed:
                    return
                # Throttle generic dev phases to one line per ~30s (real work
                # alternates 탐색↔명령↔테스트 fast → a wall of lines). DOMAIN
                # milestones (a plugin label, e.g. LEOS governance) bypass the
                # throttle and always show.
                now = time.time()
                if is_domain:
                    emit = True
                else:
                    emit = (now - getattr(agent, "_codex_last_emit_ts", 0.0)) >= 30.0
                if emit:
                    # Only advance the compared-against phase on an actual emit,
                    # so a suppressed shift keeps comparing to the last EMITTED
                    # phase (not the suppressed one).
                    agent._codex_phase = label
                    agent._codex_last_emit_ts = now
                    cb(
                        "tool.started",
                        tool_name=f"{emoji} {label}",
                        preview=f"{agent._codex_step_count}단계째",
                    )
                # else: suppressed — do NOT update _codex_phase.
            except Exception:
                pass

        agent._codex_session = CodexAppServerSession(
            cwd=cwd,
            approval_callback=approval_callback,
            on_event=_codex_progress_on_event,
        )

    # NOTE: the user message is ALREADY appended to messages by the
    # standard run_conversation() flow (line ~11823) before the early
    # return reaches us. Do NOT append again — that would duplicate.

    try:
        # Codex turn budget: the library default (600s / 10 min) guillotines any
        # production work longer than ten minutes — turns were coming back as
        # 52-char stubs after "turn timed out after 600.0s". Let a turn run up to
        # the gateway's overall message budget (agent.gateway_timeout, exported
        # as HERMES_AGENT_TIMEOUT; default 1800s), minus a small margin so the
        # codex-side timeout returns partial work gracefully BEFORE the gateway
        # hard-cuts the message.
        try:
            _gw_to = float(os.environ.get("HERMES_AGENT_TIMEOUT") or 1800.0)
        except (TypeError, ValueError):
            _gw_to = 1800.0
        _codex_turn_timeout = max(600.0, _gw_to - 30.0)
        turn = agent._codex_session.run_turn(
            user_input=user_message, turn_timeout=_codex_turn_timeout
        )
    except Exception as exc:
        logger.exception("codex app-server turn failed")
        # Crash → unconditionally drop the session so the next turn
        # respawns from scratch instead of reusing a dead client.
        try:
            agent._codex_session.close()
        except Exception:
            pass
        agent._codex_session = None
        return {
            "final_response": (
                f"Codex app-server turn failed: {exc}. "
                f"Fall back to default runtime with `/codex-runtime auto`."
            ),
            "messages": messages,
            "api_calls": 0,
            "completed": False,
            "partial": True,
            "error": str(exc),
        }

    # If the turn signalled the underlying client is wedged (deadline
    # blown, post-tool watchdog tripped, OAuth refresh died, subprocess
    # exited), retire the session so the next turn respawns codex
    # rather than riding the broken process. Mirrors openclaw beta.8's
    # "retire timed-out app-server clients" fix.
    if getattr(turn, "should_retire", False):
        logger.warning(
            "codex app-server session retired (turn error: %s)",
            turn.error,
        )
        try:
            agent._codex_session.close()
        except Exception:
            pass
        agent._codex_session = None

    # Splice projected messages into the conversation. The projector emits
    # standard {role, content, tool_calls, tool_call_id} entries, which
    # is exactly what curator.py / sessions DB expect.
    if turn.projected_messages:
        messages.extend(turn.projected_messages)

    # Durable work-journal write side (Phase 1). Externalise a LEAN record
    # of this turn's meaning into an append-only journal so later phases can
    # trim the transcript without losing what happened. Additive + fail-open:
    # a journal failure must NEVER break the turn (mirrors the external
    # memory sync guard below).
    try:
        from agent import work_journal

        work_journal.record_turn(
            turn,
            session_id=str(effective_task_id),
            turn_index=getattr(agent, "_codex_turn_index", None),
        )
    except Exception:
        pass

    # Counter ticks for the agent-improvement loop.
    # _turns_since_memory and _user_turn_count are ALREADY incremented
    # in the run_conversation() pre-loop block (lines ~11793-11817) so we
    # do NOT touch them here — that would double-count.
    # Only _iters_since_skill needs explicit increment, since the
    # chat_completions loop bumps it per tool iteration (line ~12110)
    # and that loop is bypassed on this path.
    agent._iters_since_skill = (
        getattr(agent, "_iters_since_skill", 0) + turn.tool_iterations
    )
    usage_result = _record_codex_app_server_usage(agent, turn)
    api_calls = 1

    # Now check the skill nudge AFTER iters were incremented — same
    # pattern the chat_completions path uses (line ~15432).
    should_review_skills = False
    if (
        agent._skill_nudge_interval > 0
        and agent._iters_since_skill >= agent._skill_nudge_interval
        and "skill_manage" in agent.valid_tool_names
    ):
        should_review_skills = True
        agent._iters_since_skill = 0

    # External memory provider sync (mirrors line ~15439). Skipped on
    # interrupt/error to avoid feeding partial transcripts to memory.
    if not turn.interrupted and turn.error is None:
        try:
            agent._sync_external_memory_for_turn(
                original_user_message=original_user_message,
                final_response=turn.final_text,
                interrupted=False,
            )
        except Exception:
            logger.debug("external memory sync raised", exc_info=True)

    # Background review fork — same cadence + signature as the default
    # path (line ~15449). Only fires when a trigger actually tripped AND
    # we have a real final response.
    if (
        turn.final_text
        and not turn.interrupted
        and (should_review_memory or should_review_skills)
    ):
        try:
            agent._spawn_background_review(
                messages_snapshot=list(messages),
                review_memory=should_review_memory,
                review_skills=should_review_skills,
            )
        except Exception:
            logger.debug("background review spawn raised", exc_info=True)

    return {
        "final_response": turn.final_text,
        "messages": messages,
        "api_calls": api_calls,
        "completed": not turn.interrupted and turn.error is None,
        "partial": turn.interrupted or turn.error is not None,
        "error": turn.error,
        "codex_thread_id": turn.thread_id,
        "codex_turn_id": turn.turn_id,
        **usage_result,
    }


# ---------------------------------------------------------------------------
# Event-driven Responses streaming
#
# OpenAI ships its consumer Codex backend (chatgpt.com/backend-api/codex) on
# a different schedule from the openai Python SDK.  The high-level
# ``client.responses.stream(...)`` helper reconstructs a typed Response from
# the terminal ``response.completed`` event's ``response.output`` field, and
# when that field drifts to ``null`` (gpt-5.5, May 2026) the SDK raises
# ``TypeError: 'NoneType' object is not iterable`` mid-iteration.
#
# We sidestep the whole class of failure by going one level lower:
# ``client.responses.create(stream=True)`` returns the raw AsyncIterable of
# SSE events, and we assemble the final response object purely from
# ``response.output_item.done`` events as they arrive.  We never read
# ``response.completed.response.output`` for content reconstruction, so the
# backend can return ``null``, ``[]``, a string, or omit the field entirely
# and we don't care.
#
# This mirrors what the OpenClaw TS implementation does for the same backend
# and is structurally immune to the bug class rather than patched.
# ---------------------------------------------------------------------------


_TERMINAL_EVENT_TYPES = frozenset({
    "response.completed",
    "response.incomplete",
    "response.failed",
})


def _event_field(event: Any, name: str, default: Any = None) -> Any:
    """Field access that handles both attr-style (SDK objects) and dict (raw JSON) events."""
    value = getattr(event, name, None)
    if value is None and isinstance(event, dict):
        value = event.get(name, default)
    return value if value is not None else default


def _raise_stream_error(event: Any) -> None:
    """Raise a ``_StreamErrorEvent`` from a ``type=error`` SSE frame.

    Imported lazily so this module stays importable from places that don't
    pull in ``run_agent`` (e.g. plugin code, doc tools).
    """
    from run_agent import _StreamErrorEvent
    message = (_event_field(event, "message", "") or "stream emitted error event").strip()
    raise _StreamErrorEvent(
        message,
        code=_event_field(event, "code"),
        param=_event_field(event, "param"),
    )


def _consume_codex_event_stream(
    event_iter: Any,
    *,
    model: str,
    on_text_delta=None,
    on_reasoning_delta=None,
    on_first_delta=None,
    on_event=None,
    interrupt_check=None,
) -> SimpleNamespace:
    """Consume a Codex Responses SSE event stream and return a final response.

    The returned object is a ``SimpleNamespace`` shaped like the SDK's typed
    ``Response`` for the fields downstream code actually reads:

    * ``output``: list of output items, assembled from ``response.output_item.done``.
      For tool-call turns this contains the function_call items; for plain-text
      turns it contains a synthesized ``message`` item built from streamed deltas
      if no message item was emitted directly.
    * ``output_text``: assembled text from ``response.output_text.delta`` deltas.
    * ``usage``: copied from the terminal event's ``response.usage`` (when present).
    * ``status``: ``completed`` / ``incomplete`` / ``failed`` (or ``completed`` if
      the stream ended without a terminal frame but produced content).
    * ``id``: ``response.id`` when present.
    * ``incomplete_details``: passed through for ``response.incomplete`` frames.
    * ``error``: passed through for ``response.failed`` frames.
    * ``model``: from kwargs (the wire model name is not authoritative).

    Critically, we never read ``response.output`` from the terminal event for
    content reconstruction — only ``usage``, ``status``, ``id``.  That field
    being ``null`` / ``[]`` / missing is fine.

    Callbacks:

    * ``on_text_delta(str)`` — fires per ``response.output_text.delta``, suppressed
      once a function_call event is seen (so tool-call turns don't bleed text
      into the chat).
    * ``on_reasoning_delta(str)`` — fires per ``response.reasoning.*.delta``.
    * ``on_first_delta()`` — one-shot, fires on the first text delta only.
    * ``on_event(event)`` — fires for every event before any other processing.
      Used for watchdog activity, debug logging, anything wire-shape-agnostic.
    * ``interrupt_check()`` — returns True to break the loop early.
    """
    collected_output_items: List[Any] = []
    collected_text_deltas: List[str] = []
    has_tool_calls = False
    first_delta_fired = False
    terminal_status: str = "completed"
    terminal_usage: Any = None
    terminal_response_id: str = None
    terminal_incomplete_details: Any = None
    terminal_error: Any = None
    saw_terminal = False

    for event in event_iter:
        if on_event is not None:
            try:
                on_event(event)
            except (TimeoutError, InterruptedError):
                # Control-flow signals from watchdog/cancellation hooks must
                # propagate, not get swallowed as "debug noise".
                raise
            except Exception:
                # Genuine bugs in third-party debug/log hooks shouldn't break
                # stream consumption.
                logger.debug("Codex stream on_event hook raised", exc_info=True)
        if interrupt_check is not None and interrupt_check():
            break

        event_type = _event_field(event, "type", "")
        if not isinstance(event_type, str):
            event_type = ""

        # ``error`` SSE frames carry the provider's real failure reason
        # (subscription / quota / model-not-available / rejected-reasoning-replay)
        # but never appear in the terminal set.  Surface them as a structured
        # exception so the credential pool + error classifier see the body.
        if event_type == "error":
            _raise_stream_error(event)

        if "output_text.delta" in event_type or event_type == "response.output_text.delta":
            delta_text = _event_field(event, "delta", "")
            if delta_text:
                collected_text_deltas.append(delta_text)
                if not has_tool_calls:
                    if not first_delta_fired:
                        first_delta_fired = True
                        if on_first_delta is not None:
                            try:
                                on_first_delta()
                            except Exception:
                                logger.debug("Codex stream on_first_delta raised", exc_info=True)
                    if on_text_delta is not None:
                        try:
                            on_text_delta(delta_text)
                        except Exception:
                            logger.debug("Codex stream on_text_delta raised", exc_info=True)
            continue

        if "function_call" in event_type:
            has_tool_calls = True
            # fall through — function_call items still get added on output_item.done

        if "reasoning" in event_type and "delta" in event_type:
            reasoning_text = _event_field(event, "delta", "")
            if reasoning_text and on_reasoning_delta is not None:
                try:
                    on_reasoning_delta(reasoning_text)
                except Exception:
                    logger.debug("Codex stream on_reasoning_delta raised", exc_info=True)
            continue

        if event_type == "response.output_item.done":
            done_item = _event_field(event, "item")
            if done_item is not None:
                collected_output_items.append(done_item)
            continue

        if event_type in _TERMINAL_EVENT_TYPES:
            saw_terminal = True
            resp_obj = _event_field(event, "response")
            if resp_obj is not None:
                terminal_usage = getattr(resp_obj, "usage", None)
                if terminal_usage is None and isinstance(resp_obj, dict):
                    terminal_usage = resp_obj.get("usage")
                rid = getattr(resp_obj, "id", None)
                if rid is None and isinstance(resp_obj, dict):
                    rid = resp_obj.get("id")
                terminal_response_id = rid
                rstatus = getattr(resp_obj, "status", None)
                if rstatus is None and isinstance(resp_obj, dict):
                    rstatus = resp_obj.get("status")
                if isinstance(rstatus, str):
                    terminal_status = rstatus
                if event_type == "response.incomplete":
                    terminal_incomplete_details = getattr(resp_obj, "incomplete_details", None)
                    if terminal_incomplete_details is None and isinstance(resp_obj, dict):
                        terminal_incomplete_details = resp_obj.get("incomplete_details")
                if event_type == "response.failed":
                    terminal_error = getattr(resp_obj, "error", None)
                    if terminal_error is None and isinstance(resp_obj, dict):
                        terminal_error = resp_obj.get("error")
            if event_type == "response.completed":
                terminal_status = terminal_status or "completed"
            elif event_type == "response.incomplete":
                terminal_status = terminal_status or "incomplete"
            elif event_type == "response.failed":
                terminal_status = terminal_status or "failed"
            # Stop on terminal event.
            break

    # Build the final output list.  Prefer items observed via output_item.done;
    # if none arrived but we streamed plain text deltas (no tool calls), synthesize
    # a single message item so downstream normalization has something to work with.
    if collected_output_items:
        output = list(collected_output_items)
    elif collected_text_deltas and not has_tool_calls:
        assembled = "".join(collected_text_deltas)
        output = [SimpleNamespace(
            type="message",
            role="assistant",
            status="completed",
            content=[SimpleNamespace(type="output_text", text=assembled)],
        )]
    else:
        output = []

    # If the stream ended without any terminal event AND produced no usable
    # content (no items, no text deltas), surface that as a RuntimeError so
    # callers can distinguish "stream truncated mid-flight / provider rejected
    # the call" from "stream completed with empty body".  This preserves the
    # signal the SDK's high-level helper used to raise as
    # ``RuntimeError("Didn't receive a `response.completed` event.")``.
    if not saw_terminal and not output:
        raise RuntimeError(
            "Codex Responses stream did not emit a terminal response"
        )

    assembled_text = "".join(collected_text_deltas)

    final = SimpleNamespace(
        output=output,
        output_text=assembled_text,
        usage=terminal_usage,
        status=terminal_status,
        id=terminal_response_id,
        model=model,
        incomplete_details=terminal_incomplete_details,
        error=terminal_error,
    )
    return final


def run_codex_stream(agent, api_kwargs: dict, client: Any = None, on_first_delta=None):
    """Execute one streaming Responses API request and return the final response.

    Uses ``responses.create(stream=True)`` (low-level raw event iteration)
    rather than the high-level ``responses.stream(...)`` helper.  This makes
    us structurally immune to backend drift in the ``response.completed``
    payload shape — we never let the SDK reconstruct a typed object from
    the terminal event's ``output`` field.
    """
    import httpx as _httpx

    active_client = client or agent._ensure_primary_openai_client(reason="codex_stream_direct")
    max_stream_retries = 1
    # Accumulate streamed text so callers / compat shims can read it.
    agent._codex_streamed_text_parts: list = []

    def _on_text_delta(text: str) -> None:
        agent._codex_streamed_text_parts.append(text)
        agent._fire_stream_delta(text)

    def _on_reasoning_delta(text: str) -> None:
        agent._fire_reasoning_delta(text)

    def _on_event(event: Any) -> None:
        # TTFB watchdog and activity touch — runs once per SSE event.
        agent._codex_stream_last_event_ts = time.time()
        agent._touch_activity("receiving stream response")

    def _interrupt_check() -> bool:
        return bool(agent._interrupt_requested)

    for attempt in range(max_stream_retries + 1):
        if agent._interrupt_requested:
            raise InterruptedError("Agent interrupted before Codex stream retry")

        stream_kwargs = dict(api_kwargs)
        stream_kwargs["stream"] = True

        try:
            event_stream = active_client.responses.create(**stream_kwargs)
        except (_httpx.RemoteProtocolError, _httpx.ReadTimeout, _httpx.ConnectError, ConnectionError) as exc:
            if attempt < max_stream_retries:
                logger.debug(
                    "Codex Responses stream connect failed (attempt %s/%s); retrying. %s error=%s",
                    attempt + 1, max_stream_retries + 1,
                    agent._client_log_context(), exc,
                )
                continue
            raise

        try:
            # Compatibility: some mocks/providers return a concrete response
            # instead of an iterable.  Pass it straight through.
            if hasattr(event_stream, "output") and not hasattr(event_stream, "__iter__"):
                return event_stream

            try:
                final = _consume_codex_event_stream(
                    event_stream,
                    model=api_kwargs.get("model"),
                    on_text_delta=_on_text_delta,
                    on_reasoning_delta=_on_reasoning_delta,
                    on_first_delta=on_first_delta,
                    on_event=_on_event,
                    interrupt_check=_interrupt_check,
                )
            except (_httpx.RemoteProtocolError, _httpx.ReadTimeout, _httpx.ConnectError, ConnectionError) as exc:
                if attempt < max_stream_retries:
                    logger.debug(
                        "Codex Responses stream transport failed mid-iteration "
                        "(attempt %s/%s); retrying. %s error=%s",
                        attempt + 1, max_stream_retries + 1,
                        agent._client_log_context(), exc,
                    )
                    continue
                raise

            if final.status in {"incomplete", "failed"}:
                logger.warning(
                    "Codex Responses stream terminal status=%s "
                    "(incomplete_details=%s, error=%s, streamed_chars=%d). %s",
                    final.status, final.incomplete_details, final.error,
                    sum(len(p) for p in agent._codex_streamed_text_parts),
                    agent._client_log_context(),
                )

            return final
        finally:
            close_fn = getattr(event_stream, "close", None)
            if callable(close_fn):
                try:
                    close_fn()
                except Exception:
                    pass


def run_codex_create_stream_fallback(agent, api_kwargs: dict, client: Any = None):
    """Backward-compatible alias for the unified event-driven path.

    Historically this was the fallback when the SDK's high-level
    ``responses.stream(...)`` helper raised on shape drift.  The primary
    path now does exactly what the fallback did, so this just forwards.
    Kept as a public symbol because tests and a small number of call sites
    still reference it by name.
    """
    return run_codex_stream(agent, api_kwargs, client=client)


__all__ = [
    "run_codex_app_server_turn",
    "run_codex_stream",
    "run_codex_create_stream_fallback",
    "_consume_codex_event_stream",
]
