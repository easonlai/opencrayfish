"""connectors.telegram — Telegram neuro-link.

The Architect speaks to OpenCrayFish through Telegram. During Sleep
Metabolism (02:00-06:00) only the Architect's emergency messages are answered;
all others are deferred until awakening.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from telegram import Update
from telegram.error import NetworkError, RetryAfter, TimedOut
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

if TYPE_CHECKING:
    from core.brain import Brain
    from core.heartbeat import Heartbeat
    from core.intent_router import IntentOutcome, IntentRouter
    from core.scheduler import TaskScheduler
    from core.stm import ShortTermMemory

log = logging.getLogger(__name__)

_EMERGENCY_MARKER = "/emergency"
# Origin tag for tasks created from this connector. Same convention as
# web_chat: the scheduler routes deliveries by origin and bind_deliver
# rebinds recovered tasks to the live connector at boot.
_ORIGIN: str = "telegram"


class TelegramConnector:
    def __init__(
        self,
        *,
        token: str,
        user_id: str,
        brain: Brain,
        heartbeat: Heartbeat,
        stm: ShortTermMemory,
        architect_name: str = "Architect",
        architect_honorific: str = "Boss",
    ) -> None:
        self._token = token
        self._user_id = str(user_id)
        self._brain = brain
        self._heartbeat = heartbeat
        self._stm = stm
        self._architect_name = (architect_name or "Architect").strip() or "Architect"
        self._architect_honorific = (architect_honorific or "").strip()
        self._app: Application | None = None
        # Optional — attached after construction by main.py if the task
        # scheduler is enabled. None means /tasks /cancel /pause /resume
        # respond with a "scheduler disabled" message.
        self._scheduler: TaskScheduler | None = None
        # Task-intent router. Created in attach_scheduler once the
        # scheduler is known; None when the scheduler is disabled so
        # _on_message skips the task pipeline entirely.
        self._intent_router: IntentRouter | None = None
        # Cached chat_id of the architect, populated on first message so
        # the deliver callback can push reports without needing the live
        # Update object. For 1:1 DMs (the OpenCrayFish single-architect
        # deployment model), Telegram's chat_id == user_id, so we pre-fill
        # it from the configured user_id and avoid the "must send a
        # message after every restart" delivery gap. The on-message
        # handler still updates this on every inbound message — handy for
        # the rare case of testing in a group chat (chat_id ≠ user_id).
        self._architect_chat_id: int | None = None
        try:
            uid = int(self._user_id)
            if uid > 0:
                self._architect_chat_id = uid
                log.info(
                    "TG architect chat_id pre-filled from user_id=%d "
                    "(scheduled-task delivery ready immediately).", uid,
                )
        except (TypeError, ValueError):
            log.warning(
                "TG user_id=%r is not a positive integer — "
                "scheduled-task delivery will wait for first inbound message.",
                self._user_id,
            )

    def attach_scheduler(self, scheduler: TaskScheduler) -> None:
        """Wire in the scheduler and register the deliver callback.

        Reports go via `bot.send_message(chat_id, text)`. Until the
        architect sends their first message in this run, `chat_id` is
        unknown — if a recovered task fires before that, the delivery is
        logged + dropped (the task continues firing, so nothing is lost
        once the operator says hello).
        """
        self._scheduler = scheduler
        scheduler.bind_deliver(_ORIGIN, self._deliver_report)
        # Build the shared intent router now that we know the scheduler.
        # See core/intent_router.py for the 4-stage classification logic
        # this connector used to inline directly in _on_message.
        from core.intent_router import IntentRouter
        self._intent_router = IntentRouter(
            brain=self._brain, scheduler=scheduler, origin=_ORIGIN,
        )

    async def _deliver_report(self, report: str) -> None:
        """Push a scheduled-task report to the architect's Telegram chat.

        Wrapped in a 3-attempt retry with exponential backoff for the
        transient network classes (`NetworkError`, `TimedOut`) and an
        honour-the-server-cooldown branch for `RetryAfter`. One
        unhandled `NetworkError` observed in a 1 h field run motivated
        this — reports are produced every 10 min for some tasks and a
        single TCP hiccup should not silently drop a report.

        All non-transient errors still fall through to the broad
        `Exception` log so unexpected failure modes remain visible.
        """
        if self._app is None or self._architect_chat_id is None:
            log.warning(
                "TG task deliver skipped — chat_id=%s app=%s. Architect must "
                "send a message once after boot for delivery to bind.",
                self._architect_chat_id, self._app is not None,
            )
            return

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                await self._app.bot.send_message(
                    chat_id=self._architect_chat_id, text=report,
                )
                if attempt > 1:
                    log.info(
                        "TG task report delivered on attempt %d/%d (len=%d)",
                        attempt, max_attempts, len(report),
                    )
                else:
                    log.info("TG task report delivered (len=%d)", len(report))
                return
            except RetryAfter as exc:
                # Telegram explicitly told us how long to wait — honour it,
                # but cap so we don't sleep forever on a misbehaving server.
                wait_s = min(float(getattr(exc, "retry_after", 5)), 30.0)
                log.warning(
                    "TG deliver hit RetryAfter on attempt %d/%d; sleeping %.1fs.",
                    attempt, max_attempts, wait_s,
                )
                if attempt == max_attempts:
                    log.error("TG task delivery exhausted retries (RetryAfter).")
                    return
                await asyncio.sleep(wait_s)
            except (NetworkError, TimedOut) as exc:
                if attempt == max_attempts:
                    log.error(
                        "TG task delivery failed after %d attempts: %s",
                        max_attempts, exc,
                    )
                    return
                backoff_s = 2 ** (attempt - 1)  # 1s, 2s, 4s
                log.warning(
                    "TG deliver transient error on attempt %d/%d (%s); "
                    "retrying in %ds.",
                    attempt, max_attempts, exc, backoff_s,
                )
                await asyncio.sleep(backoff_s)
            except Exception:
                # Non-transient — fail fast and loud, same as before.
                log.exception("TG task delivery failed")
                return

    def build(self) -> Application:
        app = ApplicationBuilder().token(self._token).build()
        app.add_handler(CommandHandler("start", self._on_start))
        app.add_handler(CommandHandler("emergency", self._on_message))
        app.add_handler(CommandHandler("research", self._on_research))
        app.add_handler(CommandHandler("tasks", self._on_tasks_list))
        app.add_handler(CommandHandler("cancel", self._on_tasks_cancel))
        app.add_handler(CommandHandler("pause", self._on_tasks_pause))
        app.add_handler(CommandHandler("resume", self._on_tasks_resume))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._on_message))
        self._app = app
        return app

    # ---------- handlers ------------------------------------------------------

    async def _on_start(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_architect(update):
            return
        salutation = (
            f"{self._architect_honorific} {self._architect_name}".strip()
            if self._architect_honorific
            else self._architect_name
        )
        # Use the live agent designation (sourced from soul.md / config.yaml
        # via SoulHandler) so the boot greeting matches whatever the agent
        # is currently called \u2014 no hardcoded project name in the user-facing
        # text.
        try:
            # Absolute import: connectors run as top-level modules.
            # Public name post v2.0 split (used to be ``_extract_identity``).
            from core.brain import extract_identity  # local import to avoid cycle
            soul_block = await self._brain._soul.render_identity_block()  # type: ignore[attr-defined]
            designation, _, _ = extract_identity(soul_block)
        except Exception:
            designation = "I"
        await update.effective_chat.send_message(
            f"{designation} is online. Awaiting your directive, {salutation}."
        )

    async def _on_research(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Manually fire a proactive research cycle. Verifies the autonomous
        learning pipeline end-to-end without waiting for the 30-min idle timer.

        Usage:
            /research                -> picks newest Learned Preference as topic
            /research <free text>    -> uses your text as the topic
        """
        if not self._is_architect(update):
            return
        raw = (update.message.text or "").removeprefix("/research").strip() or None
        self._heartbeat.mark_interaction()
        await update.effective_chat.send_message(
            f"🔬 Researching: {raw!r}" if raw else "🔬 Picking a topic from Learned Preferences..."
        )
        try:
            event = await self._heartbeat.trigger_proactive(topic_override=raw)
        except Exception:
            log.exception("Manual /research failed")
            await update.effective_chat.send_message(
                "Research pipeline crashed. Check logs."
            )
            return
        if event is None:
            await update.effective_chat.send_message(
                "No topic available (add a Learned Preference to soul.md first) "
                "or the search failed. Check logs."
            )
            return
        hit_lines = "\n".join(
            f"• {h['title'] or h['url']}\n  {h['url']}" for h in event["hits"][:3]
        ) or "(no results)"
        msg = (
            f"📡 *Topic:* {event['topic']}\n\n"
            f"*Web hits ({len(event['hits'])}):*\n{hit_lines}\n\n"
            f"*Reflection:*\n{event['reflection']}"
        )
        await update.effective_chat.send_message(msg)

    async def _on_message(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_architect(update):
            log.info("Ignoring non-Architect message from %s", update.effective_user)
            return

        text = (update.message.text or "").strip()
        if not text:
            return

        is_emergency = text.lower().startswith(_EMERGENCY_MARKER) or text.startswith("/emergency")
        if self._heartbeat.is_sleeping and not is_emergency:
            await update.effective_chat.send_message(
                "💤 Sleep Metabolism is active (02:00-06:00). Prefix with "
                "/emergency to wake me."
            )
            return

        # Strip the marker so the model sees only the directive content.
        clean = text.removeprefix("/emergency").strip() or text
        log.info(
            "TG msg user=%s emergency=%s len=%d preview=%r",
            update.effective_user.id if update.effective_user else "?",
            is_emergency,
            len(clean),
            clean[:80],
        )
        # Cache the chat_id so scheduled-task deliveries can push without
        # an active Update. Single-user agent: architect chat_id is stable.
        if update.effective_chat is not None:
            self._architect_chat_id = update.effective_chat.id
        self._heartbeat.mark_interaction()
        await self._stm.append("architect", clean)

        # Task intent — delegated to the shared IntentRouter so this
        # connector and the web_chat connector stay in lockstep. The
        # router classifies the message against 4 stages (list / action /
        # modify / create) and returns a structured IntentOutcome; we
        # render it here with Telegram's emoji + Markdown style. When
        # the scheduler is disabled, _intent_router is None and we fall
        # straight through to Brain.think.
        if self._intent_router is not None:
            outcome = await self._intent_router.route(clean)
            if outcome.handled:
                await self._render_intent_outcome(update, outcome)
                return

        try:
            trace = await self._brain.think(clean)
        except Exception:
            log.exception("Brain.think failed for input=%r", clean[:80])
            await update.effective_chat.send_message(
                "I encountered turbulence in thought. Regrouping — please retry."
            )
            return

        await self._stm.append("agent", trace.filtered.text)
        log.info(
            "TG reply backend=%s reply_len=%d",
            trace.backend,
            len(trace.filtered.text),
        )
        await update.effective_chat.send_message(trace.filtered.text)

    # ---------- helpers -------------------------------------------------------

    def _is_architect(self, update: Update) -> bool:
        if update.effective_user is None:
            return False
        return str(update.effective_user.id) == self._user_id

    # ---------- task management commands -------------------------------------

    async def _on_tasks_list(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self._is_architect(update):
            return
        if self._scheduler is None:
            await update.effective_chat.send_message("Task scheduler is disabled.")
            return
        # Show ALL tasks across origins — reports broadcast everywhere so
        # the operator wants the unified view, not just telegram-created ones.
        # Absolute import: connectors are loaded as top-level modules.
        from core.scheduler import render_task_list
        tasks = await self._scheduler.list_tasks()
        await update.effective_chat.send_message(
            render_task_list(tasks), parse_mode="Markdown",
        )

    async def _on_tasks_cancel(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._on_task_id_cmd(update, action="cancel")

    async def _on_tasks_pause(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._on_task_id_cmd(update, action="pause")

    async def _on_tasks_resume(self, update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._on_task_id_cmd(update, action="resume")

    async def _on_task_id_cmd(self, update: Update, *, action: str) -> None:
        """Shared body for /cancel /pause /resume — parses the trailing id
        from the command text and routes to the appropriate scheduler op.
        """
        if not self._is_architect(update):
            return
        if self._scheduler is None:
            await update.effective_chat.send_message("Task scheduler is disabled.")
            return
        text = (update.message.text or "").strip()
        # "/cancel t8f3a" → "t8f3a". Strip the leading slash-command token.
        parts = text.split(maxsplit=1)
        tid = parts[1].strip() if len(parts) > 1 else ""
        if not tid:
            await update.effective_chat.send_message(
                f"Usage: /{action} <task_id>  (see /tasks for ids)"
            )
            return
        if action == "cancel":
            removed = await self._scheduler.cancel_task(tid)
            if removed is None:
                await update.effective_chat.send_message(
                    f"No task with id `{tid}`. Try /tasks."
                )
                return
            await update.effective_chat.send_message(
                f"❌ Cancelled task `{removed.id}` — *{removed.topic}*."
            )
            return
        paused = action == "pause"
        task = await self._scheduler.pause_task(tid, paused=paused)
        if task is None:
            await update.effective_chat.send_message(
                f"No task with id `{tid}`. Try /tasks."
            )
            return
        verb = "⏸ Paused" if paused else "▶ Resumed"
        await update.effective_chat.send_message(
            f"{verb} task `{task.id}` — *{task.topic}*."
        )

    # ---------- intent-router rendering --------------------------------------

    async def _render_intent_outcome(
        self, update: Update, outcome: IntentOutcome,
    ) -> None:
        """Render a structured ``IntentOutcome`` into Telegram messages.

        Wording preserved verbatim from the pre-refactor v1 connector so
        the operator sees the exact same emoji + Markdown they're used
        to. All STM appends happen here (the router never touches STM).
        """
        # Absolute import: connectors are loaded as top-level modules.
        from core.scheduler import format_interval as _fmt_iv
        from core.scheduler import render_task_list

        kind = outcome.kind
        if kind == "list":
            assert outcome.list_result is not None
            tasks = outcome.list_result.tasks
            reply = render_task_list(tasks)
            await self._stm.append("agent", reply)
            await update.effective_chat.send_message(
                reply, parse_mode="Markdown",
            )
            log.info("TG task list query — returned %d task(s)", len(tasks))
            return

        if kind == "action":
            assert outcome.action_result is not None
            ar = outcome.action_result
            if ar.action == "cancel":
                if ar.removed is None:
                    msg = f"No task with id `{ar.task_id}` to cancel."
                else:
                    msg = (
                        f"❌ Cancelled task `{ar.removed.id}` "
                        f"— *{ar.removed.topic}*."
                    )
            else:
                # pause / resume
                if ar.task is None:
                    msg = (
                        f"No task with id `{ar.task_id}` to {ar.action}."
                    )
                else:
                    verb = "⏸ Paused" if ar.action == "pause" else "▶ Resumed"
                    msg = (
                        f"{verb} task `{ar.task.id}` — *{ar.task.topic}* "
                        f"(every {_fmt_iv(ar.task.interval_seconds)})."
                    )
            await self._stm.append("agent", msg)
            await update.effective_chat.send_message(msg, parse_mode="Markdown")
            log.info("TG task action id=%s action=%s", ar.task_id, ar.action)
            return

        if kind == "modify":
            assert outcome.update_result is not None
            ur = outcome.update_result
            if ur.error is not None:
                # Match v1 behaviour: send the error string raw (no
                # Markdown parse) and DO NOT append to STM. The original
                # connector returned immediately on ValueError without
                # logging an agent turn.
                await update.effective_chat.send_message(ur.error)
                return
            if ur.task is None:
                msg = f"No task with id `{ur.task_id}` to update."
            else:
                parts = [f"✏️ Updated task `{ur.task.id}` — *{ur.task.topic}*\n"]
                if ur.changed_interval:
                    parts.append(
                        f"• Cadence: every {_fmt_iv(ur.task.interval_seconds)}"
                    )
                if ur.changed_topic:
                    parts.append(f"• Topic: {ur.task.topic}")
                if ur.changed_queries:
                    parts.append(f"• Queries: {len(ur.task.queries)}")
                parts.append("\nNext fire uses the new settings.")
                msg = "\n".join(parts)
            await self._stm.append("agent", msg)
            await update.effective_chat.send_message(msg, parse_mode="Markdown")
            log.info("TG task updated id=%s", ur.task_id)
            return

        if kind == "create":
            assert outcome.create_result is not None
            cr = outcome.create_result
            if cr.error is not None:
                # Match v1: raw error, no STM append on add_task failure.
                await update.effective_chat.send_message(cr.error)
                return
            task = cr.task
            assert task is not None  # success path
            tick = getattr(self._scheduler, "_tick", 30)
            # Note the broadcast model in the confirmation so the
            # operator isn't confused when the same report shows up in
            # the web UI as well.
            confirm = (
                f"✅ Scheduled task `{task.id}` — *{task.topic}*\n\n"
                f"• Cadence: every {_fmt_iv(task.interval_seconds)}\n"
                f"• Queries: {len(task.queries)}\n"
                f"• First report incoming within ~{tick}s.\n"
                f"• Reports go to all connected channels (Telegram + web).\n\n"
                f"Manage: /tasks  ·  /cancel {task.id}  ·  /pause {task.id}"
            )
            await self._stm.append("agent", confirm)
            await update.effective_chat.send_message(confirm)
            log.info(
                "TG task scheduled id=%s topic=%r interval=%ds",
                task.id, task.topic, task.interval_seconds,
            )
            return

        # kind == "noop" should never reach here (caller gates on .handled),
        # but log defensively rather than silently dropping the message.
        log.warning("TG intent outcome dropped: unexpected kind=%s", kind)
