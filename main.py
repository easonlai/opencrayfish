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
from typing import Any, cast

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
from core.skills import CostTier, SkillContext, SkillRegistry
from core.skills.direct_answer import DirectAnswerSkill
from core.skills.identity import IdentitySkill
from core.skills.proactive_learning import ProactiveLearningSkill
from core.skills.recall import RecallSkill
from core.skills.recurring_research import RecurringResearchSkill
from core.skills.research import ResearchSkill
from core.skills.self_reflect import SelfReflectSkill
from core.soul_handler import SoulHandler
from core.stm import ShortTermMemory
from tools.archive_read import ArchiveRead
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


def _publish_skills_inventory(registry: SkillRegistry) -> None:
    """Atomically dump the registered skill catalogue to `state/skills.json`.

    Mirror of `_publish_tools_inventory` for the Skill layer. Read by
    `ui/dashboard.py` so the Skills panel can render cost-tier chips,
    network/side-effect badges, trigger hints, and the args schema
    WITHOUT needing access to the live registry object (separate
    Streamlit process).
    """
    out_path = Path("state/skills.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "published_at": datetime.now().isoformat(timespec="seconds"),
        "skills": [
            {
                "name": getattr(s, "name", "?"),
                "description": getattr(s, "description", ""),
                "trigger_hints": list(getattr(s, "trigger_hints", []) or []),
                "args_schema": getattr(s, "args_schema", {}),
                "cost_tier": getattr(s, "cost_tier", "cheap"),
                "requires_network": bool(getattr(s, "requires_network", False)),
                "side_effects": bool(getattr(s, "side_effects", False)),
                "requires_confirmation": bool(
                    getattr(s, "requires_confirmation", False)
                ),
            }
            for s in (registry.get(n) for n in registry.names())
            if s is not None
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
            "SKILL Published skills inventory to %s (count=%d)",
            out_path, len(payload["skills"]),
        )
    except Exception:
        log.exception("SKILL Failed to publish skills inventory")


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
    # (`name="web_search"`) so PLAN-stage code can dispatch by name
    # instead of by typed kwarg. Brain / CognitiveLoop / Heartbeat /
    # Scheduler all route their searches through the Skill registry,
    # which in turn dispatches into this Tool registry — `searxng` is
    # no longer wired through any constructor.
    tool_registry = ToolRegistry()
    tool_registry.register(searxng)
    # archive_read \u2014 read-only LTM lookup wrapped as a Tool so the
    # RecallSkill (used by Brain._retrieve_relevant and
    # CognitiveLoop._do_recall) can invoke it through the registry
    # instead of touching the file directly. Same archive_path as the
    # legacy direct readers used, so the canonical snapshot is unchanged.
    tool_registry.register(ArchiveRead(archive_path=cfg.memory.archive_path))
    # Re-publish the tools inventory whenever the registry changes so
    # the dashboard's Tool panel stays accurate even when Skills
    # register tools at runtime (e.g. ToolboxSkill discovery).
    tool_registry.set_change_listener(
        lambda: _publish_tools_inventory(tool_registry)
    )
    _publish_tools_inventory(tool_registry)

    # Reflection engine — wired into Brain (post-reply) and Heartbeat
    # (post-proactive). Disabled cleanly when cfg.reflection.enabled is false.
    reflection_engine: ReflectionEngine | None = None
    if cfg.reflection.enabled:
        reflection_engine = ReflectionEngine(
            provider=provider,
            timezone=cfg.system.timezone,
        )

    # Skill registry. Every Skill below is registered, published to
    # state/skills.json for dashboard discoverability, and dispatched
    # from Brain / CognitiveLoop / Heartbeat / Scheduler via
    # `skill_registry.invoke(...)`. That gives us per-invocation
    # timing, JSONL audit (state/skills.jsonl, date-rotated), and
    # exception isolation for free. The PLAN-stage menu the SLM picks
    # from is generated dynamically from the registry per turn, so
    # registering a new Skill with a `plan_verb` extends what the SLM
    # can choose with zero edits to the orchestrators.
    #
    # SkillContext is BUILT ONCE at boot and reused for every
    # invocation \u2014 it's a frozen dataclass of shared subsystem
    # references, NOT per-call mutable state.
    skill_ctx = SkillContext(
        tools=tool_registry,
        soul=soul,
        stm=stm,
        monitor=monitor,
        provider=provider,
        archive_path=cfg.memory.archive_path,
        designation=cfg.system.individual_designation,
        architect_name=cfg.system.architect_name,
        architect_honorific=cfg.system.architect_honorific,
    )
    skill_registry = SkillRegistry()
    # The skill enable-map lets operators opt OUT of specific skills
    # via config.yaml without code edits. Default (missing key or
    # True) = register; False = skip.
    _skill_enabled = cfg.skills.enabled

    def _maybe_register(skill_obj: Any) -> None:
        if _skill_enabled.get(skill_obj.name, True):
            skill_registry.register(skill_obj)
        else:
            log.info("SKILL %s disabled via cfg.skills.enabled", skill_obj.name)

    _maybe_register(IdentitySkill())
    _maybe_register(RecallSkill())
    _maybe_register(DirectAnswerSkill())
    _maybe_register(ResearchSkill())
    _maybe_register(ProactiveLearningSkill())
    _maybe_register(RecurringResearchSkill())
    # SelfReflectSkill is only useful when ReflectionEngine is alive;
    # we still register a stub (engine=None) so the dashboard surfaces
    # the capability, but execute() returns a graceful no-op.
    _maybe_register(SelfReflectSkill(engine=reflection_engine))

    skill_registry.set_change_listener(
        lambda: _publish_skills_inventory(skill_registry)
    )
    _publish_skills_inventory(skill_registry)
    log.info(
        "SKILL Registered %d skill(s): %s",
        len(skill_registry.names()),
        ", ".join(skill_registry.names()),
    )

    # Cognitive loop — THINK → PLAN → ACT → REFINE deliberation in front of
    # the final synthesize call. Disabled cleanly when cfg.cognition.enabled
    # is false; Brain then falls back to the legacy single-shot path.
    cognitive_loop: CognitiveLoop | None = None
    if cfg.cognition.enabled:
        cognitive_loop = CognitiveLoop(
            provider=provider,
            skill_registry=skill_registry,
            skill_ctx=skill_ctx,
            # Monitor feeds vitals into _active_plan_entries() so a
            # stressed Pi auto-degrades the PLAN menu to local-only
            # skills without the operator having to flip a flag.
            monitor=monitor,
            max_subquestions=cfg.cognition.max_subquestions,
            max_act_rounds=cfg.cognition.max_act_rounds,
            refine_enabled=cfg.cognition.refine_enabled,
            # PLAN-menu filter knobs sourced from cfg.skills.
            # cost_tier_cap is the operator baseline; auto_offline_filter
            # consults provider.is_tripped per-turn so the SLM never
            # picks SEARCH while SearXNG is unreachable.
            cost_tier_cap=cast(CostTier, cfg.skills.default_cost_tier_cap),
            auto_offline_filter=cfg.skills.auto_offline_filter,
            dispatch_answer_via_skill=cfg.cognition.dispatch_answer_via_skill,
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
        skill_registry=skill_registry,
        skill_ctx=skill_ctx,
        architect_name=cfg.system.architect_name,
        architect_honorific=cfg.system.architect_honorific,
        web_search_triage_enabled=cfg.tools.web_search_triage_enabled,
        ltm_short_circuit_enabled=cfg.tools.ltm_short_circuit_enabled,
        ltm_short_circuit_min_score=cfg.tools.ltm_short_circuit_min_score,
        reflection_enabled=cfg.reflection.reflect_on_user_turn,
        cognition=cognitive_loop,
    )

    heartbeat = Heartbeat(
        config=cfg,
        brain=brain,
        soul=soul,
        monitor=monitor,
        emotions=emotions,
        stm=stm,
        skill_registry=skill_registry,
        skill_ctx=skill_ctx,
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
            skill_registry=skill_registry,
            skill_ctx=skill_ctx,
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
        # Skill registry first — Skills may hold references back into
        # Tools and want to release them before the tools themselves
        # close. Order: brain (drain fire-and-forget reflections) →
        # skills → tools.
        await brain.aclose()
        await skill_registry.aclose_all()
        # Tool registry owns the SearXNG client lifecycle now — closing
        # via the registry isolates per-tool failures and will close any
        # additional tools registered in the future without touching this
        # block again.
        await tool_registry.aclose_all()


if __name__ == "__main__":
    asyncio.run(amain())
