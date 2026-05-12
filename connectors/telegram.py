"""connectors.telegram — Telegram neuro-link.

The Architect speaks to OpenCrayFish through Telegram. During Sleep
Metabolism (02:00-06:00) only the Architect's emergency messages are answered;
all others are deferred until awakening.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from telegram import Update
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
        brain: "Brain",
        heartbeat: "Heartbeat",
        stm: "ShortTermMemory",
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
        self._scheduler: "TaskScheduler | None" = None
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

    def attach_scheduler(self, scheduler: "TaskScheduler") -> None:
        """Wire in the scheduler and register the deliver callback.

        Reports go via `bot.send_message(chat_id, text)`. Until the
        architect sends their first message in this run, `chat_id` is
        unknown — if a recovered task fires before that, the delivery is
        logged + dropped (the task continues firing, so nothing is lost
        once the operator says hello).
        """
        self._scheduler = scheduler
        scheduler.bind_deliver(_ORIGIN, self._deliver_report)

    async def _deliver_report(self, report: str) -> None:
        """Push a scheduled-task report to the architect's Telegram chat."""
        if self._app is None or self._architect_chat_id is None:
            log.warning(
                "TG task deliver skipped — chat_id=%s app=%s. Architect must "
                "send a message once after boot for delivery to bind.",
                self._architect_chat_id, self._app is not None,
            )
            return
        try:
            await self._app.bot.send_message(
                chat_id=self._architect_chat_id, text=report,
            )
            log.info("TG task report delivered (len=%d)", len(report))
        except Exception:
            log.exception("TG task delivery failed")

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
            from core.brain import _extract_identity  # local import to avoid cycle
            soul_block = await self._brain._soul.render_identity_block()  # type: ignore[attr-defined]
            designation, _, _ = _extract_identity(soul_block)
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

        # Task intent — cheap regex pre-filter inside parse_task_intent
        # short-circuits non-task messages before any SLM call. The
        # listing short-circuit ("show me my tasks") fires FIRST so an
        # operator query never spawns a useless SLM intent-parse.
        if self._scheduler is not None:
            # Absolute import: connectors are loaded as top-level modules.
            from core.scheduler import (
                looks_like_task_action_request,
                looks_like_task_query,
                render_task_list,
                format_interval as _fmt_iv,
            )
            if looks_like_task_query(clean):
                tasks = await self._scheduler.list_tasks()
                reply = render_task_list(tasks)
                await self._stm.append("agent", reply)
                await update.effective_chat.send_message(
                    reply, parse_mode="Markdown",
                )
                log.info("TG task list query — returned %d task(s)", len(tasks))
                return
            current_tasks = await self._scheduler.list_tasks()
            # ACTION path — cancel/pause/resume via natural language.
            # Runs BEFORE the modify path because the verb classes are
            # disjoint (action vs modify) but the action regex is the
            # tighter pre-filter.
            if looks_like_task_action_request(clean):
                action_req = await self._brain.parse_task_action_intent(
                    clean, current_tasks,
                )
                if action_req is not None:
                    if action_req.action == "cancel":
                        removed = await self._scheduler.cancel_task(action_req.task_id)
                        if removed is None:
                            msg = f"No task with id `{action_req.task_id}` to cancel."
                        else:
                            msg = (
                                f"❌ Cancelled task `{removed.id}` — *{removed.topic}*."
                            )
                    else:
                        paused = action_req.action == "pause"
                        task = await self._scheduler.pause_task(
                            action_req.task_id, paused=paused,
                        )
                        if task is None:
                            msg = (
                                f"No task with id `{action_req.task_id}` to "
                                f"{action_req.action}."
                            )
                        else:
                            verb = "⏸ Paused" if paused else "▶ Resumed"
                            msg = (
                                f"{verb} task `{task.id}` — *{task.topic}* "
                                f"(every {_fmt_iv(task.interval_seconds)})."
                            )
                    await self._stm.append("agent", msg)
                    await update.effective_chat.send_message(
                        msg, parse_mode="Markdown",
                    )
                    log.info(
                        "TG task action id=%s action=%s",
                        action_req.task_id, action_req.action,
                    )
                    return
            # Modify intent BEFORE create intent — a request like
            # "change task t8f3a to every 2 hours" contains an interval
            # phrase that would otherwise look like a fresh schedule.
            update_req = await self._brain.parse_task_modify_intent(
                clean, current_tasks,
            )
            if update_req is not None:
                try:
                    task = await self._scheduler.update_task(
                        update_req.task_id,
                        topic=update_req.new_topic,
                        interval_seconds=update_req.new_interval_seconds,
                        queries=update_req.new_queries,
                        description=update_req.new_description,
                    )
                except ValueError as exc:
                    await update.effective_chat.send_message(
                        f"Could not update that task: {exc}"
                    )
                    return
                if task is None:
                    msg = f"No task with id `{update_req.task_id}` to update."
                else:
                    parts = [f"✏️ Updated task `{task.id}` — *{task.topic}*\n"]
                    if update_req.new_interval_seconds is not None:
                        parts.append(
                            f"• Cadence: every {_fmt_iv(task.interval_seconds)}"
                        )
                    if update_req.new_topic is not None:
                        parts.append(f"• Topic: {task.topic}")
                    if update_req.new_queries is not None:
                        parts.append(f"• Queries: {len(task.queries)}")
                    parts.append("\nNext fire uses the new settings.")
                    msg = "\n".join(parts)
                await self._stm.append("agent", msg)
                await update.effective_chat.send_message(
                    msg, parse_mode="Markdown",
                )
                log.info("TG task updated id=%s", update_req.task_id)
                return
            spec = await self._brain.parse_task_intent(clean)
            if spec is not None:
                try:
                    task = await self._scheduler.add_task(
                        spec, origin=_ORIGIN,
                    )
                except ValueError as exc:
                    await update.effective_chat.send_message(
                        f"Could not schedule that task: {exc}"
                    )
                    return
                # Note the broadcast model in the confirmation so the
                # operator isn't confused when the same report shows up
                # in the web UI as well.
                confirm = (
                    f"✅ Scheduled task `{task.id}` — *{task.topic}*\n\n"
                    f"• Cadence: every {_fmt_iv(task.interval_seconds)}\n"
                    f"• Queries: {len(task.queries)}\n"
                    f"• First report incoming within ~{getattr(self._scheduler, '_tick', 30)}s.\n"
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
