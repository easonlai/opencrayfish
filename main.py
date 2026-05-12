"""main — Wire all subsystems and run the Heartbeat + Telegram in parallel.

Usage:  python main.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import signal
from datetime import datetime
from pathlib import Path

from connectors.telegram import TelegramConnector
from connectors.web_chat import WebChatConnector
from core.brain import Brain
from core.cognition import CognitiveLoop
from core.config import Config
from core.emotions import Emotions
from core.empathy import EmpathyEngine
from core.heartbeat import Heartbeat
from core.monitor import Monitor
from core.positive_filter import PositiveFilter
from core.provider import Provider
from core.reflection import ReflectionEngine
from core.scheduler import TaskScheduler
from core.soul_handler import SoulHandler
from core.stm import ShortTermMemory
from tools.registry import ToolRegistry
from tools.searxng import SearXNG

# --- Logging setup -----------------------------------------------------------
# Console (stdout) keeps the operator's live view; a rotating file handler
# persists every memory/heartbeat/soul event to disk so we can audit STM
# flushes, dehydrations, soul mutations, archive writes, etc. after the fact.
_LOG_DIR = Path("state/logs")
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_console = logging.StreamHandler()
_console.setFormatter(logging.Formatter(_LOG_FORMAT))
_file = logging.handlers.RotatingFileHandler(
    _LOG_DIR / "agent.log",
    maxBytes=2_000_000,   # ~2 MB per file
    backupCount=5,        # keep last 5 rotations
    encoding="utf-8",
)
_file.setFormatter(logging.Formatter(_LOG_FORMAT))
logging.basicConfig(level=logging.INFO, handlers=[_console, _file])
log = logging.getLogger("opencrayfish")


def _publish_tools_inventory(registry: ToolRegistry) -> None:
    """Atomically dump the registered tool catalogue to `state/tools.json`.

    Read by `ui/dashboard.py`. Includes name / description / args_schema /
    side_effects so the dashboard can show what's plugged in WITHOUT
    needing access to the live registry object (separate process).
    """
    out_path = Path("state/tools.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "published_at": datetime.now().isoformat(timespec="seconds"),
        "tools": [
            {
                "name": getattr(t, "name", "?"),
                "description": getattr(t, "description", ""),
                "args_schema": getattr(t, "args_schema", {}),
                "side_effects": bool(getattr(t, "side_effects", False)),
                "requires_confirmation": bool(
                    getattr(t, "requires_confirmation", False)
                ),
            }
            for t in (registry.get(n) for n in registry.names())
            if t is not None
        ],
    }
    tmp = out_path.with_suffix(".json.tmp")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(out_path)
        log.info(
            "Published tools inventory to %s (count=%d)",
            out_path, len(payload["tools"]),
        )
    except Exception:
        log.exception("Failed to publish tools inventory")


async def amain() -> None:
    cfg = Config.load("config.yaml")

    # Subsystems
    # config.yaml `system.individual_designation` is the single source of
    # truth for the agent's name. SoulHandler injects it into the IDENTITY
    # block at every read — soul.md no longer carries a Designation line
    # of its own and is never mutated on disk.
    soul = SoulHandler(
        "soul.md",
        designation_override=cfg.system.individual_designation,
    )
    # `0.0` for release thresholds means "let Monitor pick (limit - 5)" —
    # see core/monitor.py for the hysteresis state machine.
    monitor = Monitor(
        thermal_limit_c=cfg.hardware.thermal_limit_celsius,
        ram_limit_pct=cfg.hardware.ram_limit_pct,
        thermal_release_c=(
            cfg.hardware.thermal_release_celsius
            if cfg.hardware.thermal_release_celsius > 0
            else None
        ),
        ram_release_pct=(
            cfg.hardware.ram_release_pct
            if cfg.hardware.ram_release_pct > 0
            else None
        ),
        cache_ttl_s=cfg.hardware.vitals_cache_ttl_seconds,
    )
    emotions = Emotions()
    empathy = EmpathyEngine()
    pos_filter = PositiveFilter(
        architect_name=cfg.system.architect_name,
        architect_honorific=cfg.system.architect_honorific,
    )
    provider = Provider.from_config(cfg.hardware)
    # Wire the Provider into the Monitor so SLM availability shows up as
    # a vital sign (the SLM is the agent's brain — its absence is a
    # stroke-level event and must surface on the dashboard).
    monitor.attach_provider(provider)
    stm = ShortTermMemory(
        max_turns=cfg.memory.stm_max_turns,
        journal_path="state/stm_journal.jsonl",
        fsync_on_flush=cfg.system.journal_fsync_on_flush,
    )
    # Crash recovery: replay yesterday's tail-window before any task starts.
    recovered = await stm.recover()
    if recovered:
        log.info("STM crash-recovery rehydrated %d turn(s) from journal.", recovered)
    searxng = SearXNG(base_url=cfg.tools.searxng_url)

    # Tool registry. SearXNG is registered as the first plugin
    # (`name="web_search"`) so future PLAN-stage code can dispatch by name
    # instead of by typed kwarg. Existing call sites still receive
    # `searxng=` directly — the registry is purely additive at this point.
    tool_registry = ToolRegistry()
    tool_registry.register(searxng)
    # Publish a small inventory snapshot so the dashboard (separate
    # process) can render the live tool catalogue without sharing state.
    # Re-published only on registration changes — see ToolRegistry, the
    # current registry is static-at-boot so a one-shot dump is enough.
    _publish_tools_inventory(tool_registry)

    # Reflection engine — wired into Brain (post-reply) and Heartbeat
    # (post-proactive). Disabled cleanly when cfg.reflection.enabled is false.
    reflection_engine: ReflectionEngine | None = None
    if cfg.reflection.enabled:
        reflection_engine = ReflectionEngine(
            provider=provider,
            timezone=cfg.system.timezone,
        )

    # Cognitive loop — THINK → PLAN → ACT → REFINE deliberation in front of
    # the final synthesize call. Disabled cleanly when cfg.cognition.enabled
    # is false; Brain then falls back to the legacy single-shot path.
    cognitive_loop: CognitiveLoop | None = None
    if cfg.cognition.enabled:
        cognitive_loop = CognitiveLoop(
            provider=provider,
            searxng=searxng,
            archive_path=cfg.memory.archive_path,
            max_subquestions=cfg.cognition.max_subquestions,
            max_act_rounds=cfg.cognition.max_act_rounds,
            refine_enabled=cfg.cognition.refine_enabled,
            timezone=cfg.system.timezone,
        )

    brain = Brain(
        soul=soul,
        monitor=monitor,
        emotions=emotions,
        empathy=empathy,
        positive_filter=pos_filter,
        provider=provider,
        stm=stm,
        archive_path=cfg.memory.archive_path,
        architect_name=cfg.system.architect_name,
        architect_honorific=cfg.system.architect_honorific,
        searxng=searxng,
        web_search_triage_enabled=cfg.tools.web_search_triage_enabled,
        ltm_short_circuit_enabled=cfg.tools.ltm_short_circuit_enabled,
        ltm_short_circuit_min_score=cfg.tools.ltm_short_circuit_min_score,
        reflection=(
            reflection_engine if cfg.reflection.reflect_on_user_turn else None
        ),
        cognition=cognitive_loop,
    )

    heartbeat = Heartbeat(
        config=cfg,
        brain=brain,
        soul=soul,
        monitor=monitor,
        emotions=emotions,
        stm=stm,
        searxng=searxng,
        reflection=(
            reflection_engine if cfg.reflection.reflect_on_proactive else None
        ),
    )

    telegram = TelegramConnector(
        token=cfg.api_keys.telegram_token,
        user_id=cfg.api_keys.telegram_user_id,
        brain=brain,
        heartbeat=heartbeat,
        stm=stm,
        architect_name=cfg.system.architect_name,
        architect_honorific=cfg.system.architect_honorific,
    )
    tg_app = telegram.build()

    # Optional in-process web-chat bridge (aiohttp). Defaults to 127.0.0.1
    # so the Streamlit `ui/web_chat.py` app can talk to the SAME live
    # agent without spinning up a second instance. Disabled cleanly when
    # `web_chat.enabled` is false in config.yaml.
    web_chat: WebChatConnector | None = None
    if cfg.web_chat.enabled:
        web_chat = WebChatConnector(
            cfg=cfg.web_chat,
            brain=brain,
            heartbeat=heartbeat,
            stm=stm,
            emotions=emotions,
            monitor=monitor,
            designation=cfg.system.individual_designation,
        )

    # Recurring research-task scheduler. Owns its own asyncio task running
    # in parallel to the heartbeat pulse loop. Each connector that's
    # enabled receives the scheduler and registers its own deliver
    # callback so reports route back through the originating channel.
    # Disabled cleanly when `tasks.enabled` is false.
    scheduler: TaskScheduler | None = None
    if cfg.tasks.enabled:
        scheduler = TaskScheduler(
            config=cfg,
            brain=brain,
            searxng=searxng,
            heartbeat=heartbeat,
            state_path=cfg.tasks.state_path,
            max_active_tasks=cfg.tasks.max_active_tasks,
            results_per_query=cfg.tasks.results_per_query,
            min_interval_seconds=cfg.tasks.min_interval_seconds,
            tick_seconds=cfg.tasks.tick_seconds,
        )
        recovered = await scheduler.load()
        if recovered:
            log.info("Scheduler recovered %d task(s) from %s.",
                     recovered, cfg.tasks.state_path)
        # Hand the scheduler to the connectors so they can offer task
        # commands. Each connector binds its own deliver callback inside
        # `attach_scheduler` so recovered tasks find their channel again.
        telegram.attach_scheduler(scheduler)
        if web_chat is not None:
            web_chat.attach_scheduler(scheduler)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:  # pragma: no cover - Windows
            pass

    log.info("OpenCrayFish booting (designation=%s, backend=%s)",
             cfg.system.individual_designation, provider.active_backend)

    pulse_task = asyncio.create_task(heartbeat.pulse_loop(), name="heartbeat")
    sched_task: asyncio.Task | None = None
    if scheduler is not None:
        sched_task = asyncio.create_task(scheduler.run_loop(), name="scheduler")

    # Telegram lifecycle
    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling()

    if web_chat is not None:
        await web_chat.start()

    try:
        await stop_event.wait()
    finally:
        log.info("Shutdown signal received — winding down.")
        # Final durable flush of any RAM-buffered STM turns before exit.
        try:
            n = await stm.shutdown()
            if n:
                log.info("STM shutdown flush: %d pending turn(s) fsync'd to disk.", n)
        except Exception:
            log.exception("STM shutdown flush failed.")
        await heartbeat.stop()
        await pulse_task
        if scheduler is not None and sched_task is not None:
            await scheduler.stop()
            await sched_task
        if web_chat is not None:
            await web_chat.stop()
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
        await provider.aclose()
        # Tool registry owns the SearXNG client lifecycle now — closing
        # via the registry isolates per-tool failures and will close any
        # additional tools registered in the future without touching this
        # block again.
        await tool_registry.aclose_all()


if __name__ == "__main__":
    asyncio.run(amain())
