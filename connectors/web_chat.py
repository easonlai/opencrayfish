"""connectors.web_chat — Local browser chat channel for OpenCrayFish.

A small aiohttp server runs in-process inside `main.py`'s event loop,
sharing the live `Brain` / `Heartbeat` / `ShortTermMemory` with the
Telegram connector. The accompanying Streamlit app `ui/web_chat.py`
talks to it over plain HTTP so the operator can fast-test the SAME
running agent from a browser without Telegram round-trips.

Endpoints (all JSON unless noted):

  POST /chat
    body:    {"message": "...", "emergency": false}
    returns: {"reply", "backend", "stressed", "elapsed_ms",
              "mood_active_channel", "mood_active_intensity"}
    side-effects: appends architect + agent turns to the SAME STM the
                  Telegram channel uses, marks an interaction (resets
                  the idle clock), runs the full Brain._cycle.

  GET /state
    returns: minimal snapshot for the chat header (designation, sleeping,
             mood_active_channel, vitals.is_stressed, backend).

  GET /history?limit=N
    returns: {"turns": [{"role", "content"}, ...]} — newest-last,
             trimmed to the last N (default 20, max 200).

  GET /healthz
    returns: 200 "ok" — for liveness checks.

Security model:
  * Defaults bind 127.0.0.1 — no LAN exposure.
  * Optional shared-secret token via `X-OCF-Token` header. If set in
    config, any request without a matching token gets 401.
  * Sleep Metabolism (02:00-06:00) is honoured by default — non-emergency
    POST /chat returns 423 Locked with a hint to set `emergency=true`.
"""
from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from aiohttp import web

if TYPE_CHECKING:
    from core.brain import Brain
    from core.config import WebChatCfg
    from core.emotions import Emotions
    from core.heartbeat import Heartbeat
    from core.monitor import Monitor
    from core.scheduler import TaskScheduler
    from core.stm import ShortTermMemory

log = logging.getLogger(__name__)

_HISTORY_HARD_CAP = 200
# Origin tag carried by every task created from this connector. Used by
# the scheduler to route deliveries and by `bind_deliver` on recovery.
_ORIGIN: str = "web_chat"


class WebChatConnector:
    """In-process aiohttp bridge that exposes the live agent over HTTP."""

    def __init__(
        self,
        *,
        cfg: "WebChatCfg",
        brain: "Brain",
        heartbeat: "Heartbeat",
        stm: "ShortTermMemory",
        emotions: "Emotions",
        monitor: "Monitor",
        designation: str,
    ) -> None:
        self._cfg = cfg
        self._brain = brain
        self._heartbeat = heartbeat
        self._stm = stm
        self._emotions = emotions
        self._monitor = monitor
        self._designation = designation
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        # Optional — attached after construction by main.py if the task
        # scheduler is enabled. When None, /chat just runs normal think()
        # and the /tasks endpoints return 503.
        self._scheduler: "TaskScheduler | None" = None

    # ---------- lifecycle / wiring -------------------------------------------

    def attach_scheduler(self, scheduler: "TaskScheduler") -> None:
        """Wire in the task scheduler and rebind any recovered web_chat tasks.

        Connectors call this once at boot. The deliver callback below is
        bound here — it appends the report to STM as an "agent" turn so
        the next /history poll surfaces the new message in the browser
        without needing a push-channel (Streamlit polls /history on a
        timer).
        """
        self._scheduler = scheduler
        scheduler.bind_deliver(_ORIGIN, self._deliver_report)

    async def _deliver_report(self, report: str) -> None:
        """Inject a scheduled-task report into STM as if the agent spoke it.

        The web UI polls /history; this is what surfaces the report to
        the operator. We mark the interaction so the heartbeat doesn't
        treat the autonomous fire as idle activity.
        """
        try:
            await self._stm.append("agent", report)
            self._heartbeat.mark_interaction()
            log.info("WEB task report delivered (len=%d)", len(report))
        except Exception:
            log.exception("WEB task delivery failed")

    # ---------- lifecycle -----------------------------------------------------

    async def start(self) -> None:
        """Bind the HTTP server. Idempotent — calling twice is safe."""
        if self._runner is not None:
            return
        app = web.Application(middlewares=[self._auth_middleware])
        app.router.add_post("/chat", self._handle_chat)
        app.router.add_get("/state", self._handle_state)
        app.router.add_get("/history", self._handle_history)
        app.router.add_get("/healthz", self._handle_healthz)
        # Task management endpoints (no-ops when scheduler is disabled —
        # see the 503 handling in the handlers themselves).
        app.router.add_get("/tasks", self._handle_tasks_list)
        app.router.add_post("/tasks/cancel", self._handle_tasks_cancel)
        app.router.add_post("/tasks/pause", self._handle_tasks_pause)
        app.router.add_post("/tasks/resume", self._handle_tasks_resume)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self._cfg.host, self._cfg.port)
        await self._site.start()
        log.info(
            "Web-chat bridge listening on http://%s:%d (auth=%s)",
            self._cfg.host,
            self._cfg.port,
            "shared-secret" if self._cfg.auth_token else "none (loopback)",
        )

    async def stop(self) -> None:
        if self._site is not None:
            try:
                await self._site.stop()
            except Exception:  # pragma: no cover
                log.exception("Web-chat site stop failed")
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception:  # pragma: no cover
                log.exception("Web-chat runner cleanup failed")
        self._site = None
        self._runner = None

    # ---------- middleware ----------------------------------------------------

    @web.middleware
    async def _auth_middleware(
        self, request: web.Request, handler
    ) -> web.StreamResponse:
        # /healthz is always public so a sidecar liveness probe never
        # needs the token.
        if request.path == "/healthz":
            return await handler(request)
        token = self._cfg.auth_token.strip()
        if token:
            sent = request.headers.get("X-OCF-Token", "").strip()
            if sent != token:
                log.warning(
                    "Web-chat auth rejected: path=%s remote=%s",
                    request.path,
                    request.remote,
                )
                return web.json_response(
                    {"error": "unauthorised"}, status=401
                )
        return await handler(request)

    # ---------- handlers ------------------------------------------------------

    async def _handle_healthz(self, _request: web.Request) -> web.Response:
        return web.Response(text="ok")

    async def _handle_state(self, _request: web.Request) -> web.Response:
        # Cheap read of live state. Mirrors fields the dashboard uses so
        # the chat header can show the same readout.
        mood = await self._emotions.snapshot()
        active_channel, active_intensity = mood.dominant_excluding_baseline()
        try:
            vitals = await self._monitor.sample()
            stressed = bool(vitals.is_stressed)
            vitals_text = vitals.describe()
            brain_online = bool(vitals.brain_online)
            brain_backend = vitals.brain_backend
            brain_last_error = vitals.brain_last_error
        except Exception:
            stressed = False
            vitals_text = "(vitals unavailable)"
            brain_online = True
            brain_backend = "unknown"
            brain_last_error = None
        return web.json_response({
            "designation": self._designation,
            "sleeping": self._heartbeat.is_sleeping,
            "stressed": stressed,
            "vitals_describe": vitals_text,
            "mood_dominant": mood.dominant(),
            "mood_active_channel": active_channel,
            "mood_active_intensity": active_intensity,
            "brain_online": brain_online,
            "brain_backend": brain_backend,
            "brain_last_error": brain_last_error,
        })

    async def _handle_history(self, request: web.Request) -> web.Response:
        try:
            limit = int(request.query.get("limit", "20"))
        except ValueError:
            limit = 20
        limit = max(1, min(_HISTORY_HARD_CAP, limit))
        turns = await self._stm.render()
        tail = turns[-limit:]
        return web.json_response({
            "turns": [
                {"role": t.role, "content": t.content}
                for t in tail
            ],
        })

    async def _handle_chat(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                {"error": "invalid JSON body"}, status=400
            )
        if not isinstance(body, dict):
            return web.json_response(
                {"error": "body must be a JSON object"}, status=400
            )
        message = str(body.get("message") or "").strip()
        if not message:
            return web.json_response(
                {"error": "message must be a non-empty string"}, status=400
            )
        emergency = bool(body.get("emergency", False))

        # Sleep gate — same convention as Telegram's /emergency prefix.
        if (
            self._cfg.respect_sleep_metabolism
            and self._heartbeat.is_sleeping
            and not emergency
        ):
            return web.json_response({
                "error": "sleeping",
                "hint": (
                    "Sleep Metabolism is active (02:00-06:00). Set "
                    "`emergency=true` to wake the agent."
                ),
            }, status=423)

        log.info(
            "WEB msg remote=%s emergency=%s len=%d preview=%r",
            request.remote, emergency, len(message), message[:80],
        )
        self._heartbeat.mark_interaction()
        await self._stm.append("architect", message)

        # Task pipeline (only when scheduler is enabled). Four intent
        # paths, ordered by cost so the cheapest filter wins:
        #   1. LIST   ("show me my tasks")            — no SLM call
        #   2. ACTION ("cancel/pause/resume task X")  — SLM only on action verbs
        #   3. MODIFY ("change t8f3a to 2h")          — SLM only on update verbs
        #   4. CREATE ("check news every hour")       — SLM only on interval words
        # Action precedes MODIFY because the verb classes are disjoint
        # but action regex is tighter (its pre-filter requires a task
        # noun OR id token AND a cancel/pause/resume verb). Modify must
        # precede CREATE because a modify request usually contains an
        # interval phrase that would otherwise look like a new
        # scheduling request.
        if self._scheduler is not None:
            # Absolute import: connectors are loaded as top-level modules
            # (`connectors.web_chat`), not from a parent package.
            from core.scheduler import (
                looks_like_task_action_request,
                looks_like_task_query,
                render_task_list,
                format_interval as _fmt_iv,
            )
            if looks_like_task_query(message):
                tasks = await self._scheduler.list_tasks()
                reply = render_task_list(tasks, channel=_ORIGIN)
                await self._stm.append("agent", reply)
                log.info("WEB task list query — returned %d task(s)", len(tasks))
                return web.json_response({
                    "reply": reply,
                    "backend": "scheduler",
                    "stressed": False,
                    "elapsed_ms": 0,
                    "mood_dominant": "n/a",
                    "mood_active_channel": "",
                    "mood_active_intensity": 0.0,
                    "tasks_count": len(tasks),
                })
            current_tasks = await self._scheduler.list_tasks()
            # ACTION path — cancel / pause / resume via natural language.
            if looks_like_task_action_request(message):
                action_req = await self._brain.parse_task_action_intent(
                    message, current_tasks,
                )
                if action_req is not None:
                    if action_req.action == "cancel":
                        removed = await self._scheduler.cancel_task(action_req.task_id)
                        if removed is None:
                            reply = (
                                f"No task with id `{action_req.task_id}` to cancel."
                            )
                        else:
                            reply = (
                                f"❌ Cancelled task `{removed.id}` — *{removed.topic}*."
                            )
                    else:
                        paused = action_req.action == "pause"
                        task = await self._scheduler.pause_task(
                            action_req.task_id, paused=paused,
                        )
                        if task is None:
                            reply = (
                                f"No task with id `{action_req.task_id}` to "
                                f"{action_req.action}."
                            )
                        else:
                            verb = "⏸ Paused" if paused else "▶ Resumed"
                            reply = (
                                f"{verb} task `{task.id}` — *{task.topic}* "
                                f"(every {_fmt_iv(task.interval_seconds)})."
                            )
                    await self._stm.append("agent", reply)
                    log.info(
                        "WEB task action id=%s action=%s",
                        action_req.task_id, action_req.action,
                    )
                    return web.json_response({
                        "reply": reply,
                        "backend": "scheduler",
                        "stressed": False,
                        "elapsed_ms": 0,
                        "mood_dominant": "n/a",
                        "mood_active_channel": "",
                        "mood_active_intensity": 0.0,
                        "task_id": action_req.task_id,
                        "task_action": action_req.action,
                    })
            update = await self._brain.parse_task_modify_intent(
                message, current_tasks,
            )
            if update is not None:
                try:
                    task = await self._scheduler.update_task(
                        update.task_id,
                        topic=update.new_topic,
                        interval_seconds=update.new_interval_seconds,
                        queries=update.new_queries,
                        description=update.new_description,
                    )
                except ValueError as exc:
                    reply = f"Could not update that task: {exc}"
                    await self._stm.append("agent", reply)
                    return web.json_response({
                        "reply": reply,
                        "backend": "scheduler",
                        "stressed": False,
                        "elapsed_ms": 0,
                        "mood_dominant": "n/a",
                        "mood_active_channel": "",
                        "mood_active_intensity": 0.0,
                    })
                if task is None:
                    reply = f"No task with id `{update.task_id}` to update."
                else:
                    parts = [f"Updated task **{task.id}** — *{task.topic}*\n"]
                    if update.new_interval_seconds is not None:
                        parts.append(
                            f"• Cadence: every {_fmt_iv(task.interval_seconds)}"
                        )
                    if update.new_topic is not None:
                        parts.append(f"• Topic: {task.topic}")
                    if update.new_queries is not None:
                        parts.append(f"• Queries: {len(task.queries)}")
                    parts.append("\nNext fire uses the new settings.")
                    reply = "\n".join(parts)
                await self._stm.append("agent", reply)
                log.info("WEB task updated id=%s", update.task_id)
                return web.json_response({
                    "reply": reply,
                    "backend": "scheduler",
                    "stressed": False,
                    "elapsed_ms": 0,
                    "mood_dominant": "n/a",
                    "mood_active_channel": "",
                    "mood_active_intensity": 0.0,
                    "task_id": update.task_id,
                })
            spec = await self._brain.parse_task_intent(message)
            if spec is not None:
                try:
                    task = await self._scheduler.add_task(
                        spec, origin=_ORIGIN,
                    )
                except ValueError as exc:
                    reply = f"Could not schedule that task: {exc}"
                    await self._stm.append("agent", reply)
                    return web.json_response({
                        "reply": reply,
                        "backend": "scheduler",
                        "stressed": False,
                        "elapsed_ms": 0,
                        "mood_dominant": "n/a",
                        "mood_active_channel": "",
                        "mood_active_intensity": 0.0,
                    })
                # Format an operator-facing confirmation. First fire is
                # imminent (next scheduler tick); reports broadcast to
                # every bound connector so mention it here. The web
                # surface has no slash commands, so the management hint
                # below points at natural-language phrasing and the REST
                # endpoint — NOT `/cancel <id>` (which only exists in
                # the Telegram connector).
                reply = (
                    f"Scheduled task **{task.id}** — *{task.topic}*\n\n"
                    f"• Cadence: every {_fmt_iv(task.interval_seconds)}\n"
                    f"• Queries: {len(task.queries)}\n"
                    f"• First report: within ~{self._scheduler_tick_hint()} seconds\n"
                    f"• Reports go to all connected channels (web + Telegram).\n\n"
                    f"To stop it: say *\"cancel {task.id}\"* in chat, or POST "
                    f"`/tasks/cancel` with `{{\"id\": \"{task.id}\"}}`."
                )
                await self._stm.append("agent", reply)
                log.info(
                    "WEB task scheduled id=%s topic=%r interval=%ds",
                    task.id, task.topic, task.interval_seconds,
                )
                return web.json_response({
                    "reply": reply,
                    "backend": "scheduler",
                    "stressed": False,
                    "elapsed_ms": 0,
                    "mood_dominant": "n/a",
                    "mood_active_channel": "",
                    "mood_active_intensity": 0.0,
                    "task_id": task.id,
                })

        t0 = time.perf_counter()
        try:
            trace = await self._brain.think(message)
        except Exception:
            log.exception("Web /chat: Brain.think failed for %r", message[:80])
            return web.json_response({
                "error": "brain_failure",
                "hint": "Brain.think raised — check state/logs/agent.log",
            }, status=500)
        elapsed_ms = int((time.perf_counter() - t0) * 1000)

        reply = trace.filtered.text
        await self._stm.append("agent", reply)

        # Recompute mood AFTER the turn so the UI can show how the
        # exchange moved the channel.
        mood = await self._emotions.snapshot()
        active_channel, active_intensity = mood.dominant_excluding_baseline()
        log.info(
            "WEB reply backend=%s reply_len=%d elapsed_ms=%d",
            trace.backend, len(reply), elapsed_ms,
        )
        return web.json_response({
            "reply": reply,
            "backend": trace.backend,
            "stressed": bool(trace.vitals.is_stressed) if trace.vitals else False,
            "elapsed_ms": elapsed_ms,
            "mood_dominant": mood.dominant(),
            "mood_active_channel": active_channel,
            "mood_active_intensity": active_intensity,
        })

    def _scheduler_tick_hint(self) -> int:
        """Best-effort estimate of seconds until the scheduler's next tick.

        Used in the schedule-confirmation message; the scheduler's wake
        event makes this slightly pessimistic in practice (add_task fires
        on add, so the real first-report latency is dominated by the
        SearXNG + SLM round-trip, not the tick).
        """
        return getattr(self._scheduler, "_tick", 30) if self._scheduler else 30

    # ---------- task-management endpoints ------------------------------------

    async def _handle_tasks_list(self, _request: web.Request) -> web.Response:
        if self._scheduler is None:
            return web.json_response(
                {"error": "scheduler_disabled"}, status=503,
            )
        # Show ALL tasks, not just web-originated ones — reports broadcast
        # to every channel so the operator naturally wants the unified view.
        tasks = await self._scheduler.list_tasks()
        return web.json_response({
            "tasks": [
                {
                    "id": t.id,
                    "topic": t.topic,
                    "interval_seconds": t.interval_seconds,
                    "queries": t.queries,
                    "next_run_at": t.next_run_at,
                    "last_run_at": t.last_run_at,
                    "fire_count": t.fire_count,
                    "paused": t.paused,
                    "last_error": t.last_error,
                } for t in tasks
            ],
        })

    async def _handle_tasks_cancel(self, request: web.Request) -> web.Response:
        return await self._task_id_action(request, action="cancel")

    async def _handle_tasks_pause(self, request: web.Request) -> web.Response:
        return await self._task_id_action(request, action="pause")

    async def _handle_tasks_resume(self, request: web.Request) -> web.Response:
        return await self._task_id_action(request, action="resume")

    async def _task_id_action(
        self, request: web.Request, *, action: str,
    ) -> web.Response:
        """Shared body for /tasks/{cancel,pause,resume}.

        Body: {"id": "<task_id>"}. Returns 404 when the id is unknown,
        503 when the scheduler is disabled, 200 with the resulting task
        object on success.
        """
        if self._scheduler is None:
            return web.json_response(
                {"error": "scheduler_disabled"}, status=503,
            )
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)
        tid = str((body or {}).get("id") or "").strip()
        if not tid:
            return web.json_response({"error": "missing 'id'"}, status=400)
        if action == "cancel":
            removed = await self._scheduler.cancel_task(tid)
            if removed is None:
                return web.json_response({"error": "unknown task id"}, status=404)
            return web.json_response({"cancelled": tid, "topic": removed.topic})
        paused = action == "pause"
        task = await self._scheduler.pause_task(tid, paused=paused)
        if task is None:
            return web.json_response({"error": "unknown task id"}, status=404)
        return web.json_response({
            "id": task.id, "paused": task.paused, "topic": task.topic,
        })
