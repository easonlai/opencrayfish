"""core.heartbeat — Pulse & Metabolism (per HEARTBEAT_LOGIC.md).

Two coroutines drive the agent's life:

* `Heartbeat.pulse_loop()`   — runs forever. During Active hours it ticks
  every `system.pulse_interval_seconds` (default 30 s), samples vitals,
  decays emotions, drains the STM pending buffer to disk after
  `system.idle_journal_flush_seconds` of silence, and — once idle exceeds
  `system.idle_proactive_minutes` — issues a Proactive Thought (STM-gap
  topic with LEARNED_PREFERENCES fallback).
* `Heartbeat.metabolism()`   — invoked once when the clock crosses into the
  Sleep window (default 02:00). Merges the day's heartbeat telemetry log
  with the STM conversation journal, asks the SLM to extract 3–5 key facts,
  appends them to `archive.md` (LTM), promotes the top two into
  `soul.md` [CORE_MEMORIES] (Soul Evolution), then mines
  `state/reflection.jsonl` (date-rotated) for recurring interest/lesson
  themes and `state/skills.jsonl` for chronically failing Skills, and
  promotes those signals into [LEARNED_PREFERENCES] / [EMOTIONAL_EVOLUTION].
  Finally purges STM (RAM deque + pending buffer + journal) so tomorrow
  starts clean.

Every pulse also writes `state/vitals.json` so the Streamlit dashboard
(separate process) can render a live view without IPC.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import deque
from dataclasses import asdict
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from .config import Config
from .skills import SkillContext, SkillRegistry

if TYPE_CHECKING:  # avoid runtime cycles
    from .brain import Brain
    from .emotions import Emotions
    from .monitor import Monitor
    from .reflection import ReflectionEngine
    from .soul_handler import SoulHandler
    from .stm import ShortTermMemory

log = logging.getLogger(__name__)

# Heartbeat tuning lives in config.yaml (system.pulse_interval_seconds /
# system.idle_proactive_minutes) per HEARTBEAT_LOGIC.md §1. The two values
# below are observability-only constants — they shape the on-disk snapshot,
# not the agent's behavior.
STATE_HISTORY_LEN: int = 120  # rolling sparkline samples (~1 hr at 30s pulse)
STATE_FILE: Path = Path("state/vitals.json")
PROACTIVE_FEED: Path = Path("state/proactive.jsonl")  # append-only audit trail
# Vitals event log (stress enter/exit transitions only — not per-pulse).
# Read by the dashboard to render a stress timeline.
VITALS_EVENTS_FEED: Path = Path("state/vitals_events.jsonl")

# Words ignored by the cheap LTM substring check in `_is_in_ltm`. These are
# common conversational filler that would otherwise produce false positives
# against archive.md / soul.md (e.g. "the latest model" — "latest" and
# "model" both appear in LTM constantly).
_LTM_STOPWORDS: frozenset[str] = frozenset({
    "about", "after", "again", "also", "another", "because", "been",
    "before", "being", "between", "both", "called", "could", "does",
    "doing", "down", "during", "each", "either", "every", "from",
    "have", "having", "here", "into", "itself", "just", "like",
    "latest", "made", "make", "many", "more", "most", "much", "must",
    "name", "need", "only", "other", "over", "same", "should", "since",
    "some", "such", "sure", "take", "than", "that", "then", "there",
    "these", "they", "thing", "this", "those", "through", "under",
    "used", "using", "very", "want", "were", "what", "when", "where",
    "which", "while", "will", "with", "would", "your",
})


class Heartbeat:
    def __init__(
        self,
        *,
        config: Config,
        brain: "Brain",
        soul: "SoulHandler",
        monitor: "Monitor",
        emotions: "Emotions",
        stm: "ShortTermMemory",
        skill_registry: SkillRegistry,
        skill_ctx: SkillContext,
        reflection: "ReflectionEngine | None" = None,
    ) -> None:
        self._cfg = config
        self._brain = brain
        self._soul = soul
        self._monitor = monitor
        self._emotions = emotions
        self._stm = stm
        # Skill dispatch — proactive research goes through the
        # "proactive_learning" Skill, self-reflection through
        # "self_reflect". The registry mediates every outbound
        # retrieval call so we get JSONL audit + per-invocation timing
        # for free.
        self._skill_registry = skill_registry
        self._skill_ctx = skill_ctx
        # `reflection` is kept as a direct dependency — the consolidation
        # path (`_consolidate_reflections`) still calls
        # `reflection.read_recent(...)` which isn't a Skill (no SLM /
        # network involvement, just a JSONL tail read). Self-reflection
        # itself is now fire-and-forget through the registry.
        self._reflection = reflection
        # Strong references to background skill invocations spawned by
        # `_proactive_research`. asyncio.create_task only registers a
        # weak ref; without this set the GC can collect an in-flight
        # task. Tasks self-remove via add_done_callback; remaining ones
        # are awaited in `stop()`.
        self._inflight: set[asyncio.Task[Any]] = set()

        self._tz = ZoneInfo(config.system.timezone)
        self._duty_start = _parse_hhmm(config.system.duty_start)
        self._sleep_start = _parse_hhmm(config.system.sleep_start)

        # Spec-driven tuning (HEARTBEAT_LOGIC.md §1). Sourced from config
        # so operators can tune without code changes.
        self._pulse_interval_seconds: int = int(config.system.pulse_interval_seconds)
        self._idle_proactive_threshold: timedelta = timedelta(
            minutes=int(config.system.idle_proactive_minutes)
        )
        # Idle threshold for flushing the STM pending buffer to disk.
        # During active conversation, turns sit in a RAM buffer; this
        # heartbeat-triggered flush gives them durability without per-turn
        # SD-card writes. The RAM deque itself stays warm — STM is only
        # purged once nightly by Sleep Metabolism.
        self._idle_journal_flush_threshold: timedelta = timedelta(
            seconds=int(config.system.idle_journal_flush_seconds)
        )

        self._last_interaction_at: datetime = self._now()
        self._is_sleeping: bool = False
        self._stop = asyncio.Event()

        self._log_dir = Path(config.memory.log_path)
        self._archive_path = Path(config.memory.archive_path)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._archive_path.parent.mkdir(parents=True, exist_ok=True)

        # Live-state publisher (read by ui/dashboard.py).
        self._state_path = STATE_FILE
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._history: deque[dict] = deque(maxlen=STATE_HISTORY_LEN)
        self._pulse_count: int = 0
        self._proactive_count: int = 0
        self._stress_count: int = 0
        # Stress transition tracking — we log ENTER/EXIT events instead of
        # spamming a WARNING line every pulse the agent is hot.
        self._was_stressed: bool = False
        self._stress_started_at: datetime | None = None
        self._stress_peak_temp: float | None = None
        self._stress_peak_ram: float = 0.0
        self._last_proactive_topic: str | None = None
        self._last_proactive_at: str | None = None
        self._last_proactive_source: str | None = None
        self._started_at: str = self._now().isoformat()

    # ---------- public surface ------------------------------------------------

    @property
    def is_sleeping(self) -> bool:
        """Telegram connector reads this to gate non-emergency responses."""
        return self._is_sleeping

    def mark_interaction(self) -> None:
        """Reset the idle timer; called when the Architect speaks."""
        self._last_interaction_at = self._now()

    async def pulse_loop(self) -> None:
        log.info(
            "Heartbeat starting (tz=%s, duty=%s, sleep=%s, pulse=%ds, idle_threshold=%dm)",
            self._cfg.system.timezone,
            self._cfg.system.duty_start,
            self._cfg.system.sleep_start,
            self._pulse_interval_seconds,
            int(self._idle_proactive_threshold.total_seconds() // 60),
        )
        try:
            while not self._stop.is_set():
                await self._pulse()
                try:
                    await asyncio.wait_for(
                        self._stop.wait(),
                        timeout=self._pulse_interval_seconds,
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            log.info("Heartbeat stopped.")

    async def stop(self) -> None:
        self._stop.set()
        # Drain background `self_reflect` tasks scheduled by
        # `_proactive_research`. The pulse loop has already returned by
        # the time main.py awaits the next subsystem; waiting here
        # prevents fire-and-forget tasks from racing the registry's
        # aclose_all() and writing partial audit/reflection records.
        if self._inflight:
            pending = list(self._inflight)
            log.info("Heartbeat stop: draining %d background task(s)", len(pending))
            await asyncio.gather(*pending, return_exceptions=True)

    # ---------- pulse ---------------------------------------------------------

    async def _pulse(self) -> None:
        now = self._now()
        in_duty = self._is_duty_window(now)

        if not in_duty:
            if not self._is_sleeping:
                # Just crossed into the Sleep window: run metabolism once.
                self._is_sleeping = True
                log.info("Entering Sleep Metabolism at %s", now.isoformat())
                try:
                    await self.metabolism()
                except Exception:
                    log.exception("Sleep metabolism failed")
            await self._publish_state(vitals=None, idle_seconds=0)
            return

        # Active pulse path.
        if self._is_sleeping:
            log.info("Awakening: Active Pulse resumes at %s", now.isoformat())
            self._is_sleeping = False
            self._last_interaction_at = now  # fresh idle clock

        vitals = await self._monitor.sample(force_refresh=True)
        await self._emotions.decay()
        if vitals.is_stressed:
            # Pillar 3: hardware exhaustion is a multi-channel mood event.
            # Anger up (frustration), sorrow up slightly (fatigue), excitement
            # damped (no enthusiasm when overheating). Positive_filter still
            # guarantees the OUTPUT remains constructive — only the internal
            # vector reflects the suffering. Channel magnitudes live on
            # MoodTuning; applied atomically so decay() in the next pulse
            # doesn't see a half-written state.
            await self._emotions.nudge_many(
                self._emotions.tuning.vitals_stress, source="vitals_stress"
            )
            self._stress_count += 1
            # Track peak readings within the current stress episode for the
            # EXIT event payload.
            if vitals.temperature_c is not None and (
                self._stress_peak_temp is None
                or vitals.temperature_c > self._stress_peak_temp
            ):
                self._stress_peak_temp = vitals.temperature_c
            if vitals.ram_percent > self._stress_peak_ram:
                self._stress_peak_ram = vitals.ram_percent
        # Emit transition events (ENTER on rising edge, EXIT on falling edge)
        # — these are the operator-visible signal; per-pulse stress is just
        # internal accounting now.
        await self._record_stress_transition(now=now, vitals=vitals)
        await self._append_log(f"PULSE {vitals.describe()}")
        self._pulse_count += 1

        # Proactive Thought: idle > threshold triggers autonomous research.
        idle_for = now - self._last_interaction_at
        await self._publish_state(vitals=vitals, idle_seconds=int(idle_for.total_seconds()))

        # Deferred journal flush: drain STM's RAM pending buffer to disk
        # once the agent has been idle long enough. Cheap (single open/
        # write/close), idempotent (no-op when nothing pending), and bounds
        # the data-loss window without thrashing the SD card every turn.
        if (
            self._stm.pending_writes > 0
            and idle_for >= self._idle_journal_flush_threshold
        ):
            try:
                flushed = await self._stm.flush_journal()
                if flushed:
                    log.info(
                        "STM journal flushed: %d turn(s) written after %ds idle.",
                        flushed,
                        int(idle_for.total_seconds()),
                    )
            except Exception:
                log.exception("STM journal flush failed (continuing).")

        if idle_for >= self._idle_proactive_threshold:
            await self._proactive_research()
            self._last_interaction_at = now  # cooldown so we don't spam

    async def trigger_proactive(self, topic_override: str | None = None) -> dict | None:
        """Manually invoke a proactive research cycle (e.g. via `/research`).

        Returns the recorded event dict on success, or None if no topic was
        available / search failed. Does NOT reset the idle clock so the next
        autonomous cycle still fires on schedule.
        """
        return await self._proactive_research(topic_override=topic_override)

    def _yield_to_foreground(self, stage: str, *, topic: str | None = None) -> bool:
        """Return True if the Architect has spoken — caller should bail out.

        Background autonomous research must not hog the NPU while a live
        Architect turn is waiting in the inference queue. The proactive
        cycle calls this between every long-running milestone (topic
        selection, web search, SLM synthesis, REFINE); the first True
        return short-circuits the rest of the cycle. Re-tried on the next
        idle window — no data loss, just a deferred curiosity tick.
        """
        if not self._brain.is_foreground_busy():
            return False
        log.info(
            "PROACTIVE yield_to_foreground stage=%s topic=%r — Architect is active.",
            stage, topic or "(pre-topic)",
        )
        return True

    async def _proactive_research(self, *, topic_override: str | None = None) -> dict | None:
        # Yield BEFORE topic selection too: STM-gap extraction itself
        # makes a small SLM call to self-assess. If the Architect just
        # spoke, defer the whole cycle.
        #
        # Manual `/research [topic]` requests are operator-initiated and
        # bypass EVERY yield checkpoint — silently dropping an explicit
        # operator command would be worse than running it concurrently
        # with another foreground turn. Only the autonomous idle-driven
        # path yields.
        if not topic_override and self._yield_to_foreground("topic_selection"):
            return None

        # Two-stage topic selection. STM gaps win; LEARNED_PREFERENCES is
        # the safety net for quiet days.
        triage_decisions: list[dict] = []
        if topic_override:
            topic = topic_override
            source = "manual"
        else:
            topic, source, triage_decisions = await self._select_research_topic()

        if not topic:
            log.info(
                "Proactive thought skipped: no actionable topic "
                "(triage_decisions=%d).",
                len(triage_decisions),
            )
            return None

        # Same bypass as checkpoint #1: manual `/research` skips this
        # yield. Autonomous idle research yields if Architect spoke
        # while topic selection was in flight.
        if not topic_override and self._yield_to_foreground("pre_search", topic=topic):
            return None

        log.info("PROACTIVE: source=%s topic=%r — searching SearXNG ...", source, topic)
        sk_result = await self._skill_registry.invoke(
            "proactive_learning", self._skill_ctx, topic=topic, limit=3,
        )
        if not sk_result.ok:
            log.warning(
                "SearXNG search failed for %r via proactive_learning Skill: %s",
                topic, sk_result.error or "?",
            )
            return None
        # Skill evidence is list of {title, url, snippet} dicts — mirrors
        # the legacy SearchResult attrs by key.
        results = sk_result.evidence
        log.info(
            "PROACTIVE: %d hits (first=%s)",
            len(results),
            (results[0].get("url") if results else "(none)"),
        )

        digest = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')}" for r in results
        ) or "(no results)"
        idle_minutes = int(self._idle_proactive_threshold.total_seconds() // 60)
        # Mission text differs by source so the SLM understands the context.
        if source == "stm_gap":
            preamble = (
                f"Idle > {idle_minutes} minutes. The operator recently mentioned "
                f"{topic!r} and you self-assessed as not knowing it well."
            )
        elif source == "learned_preference":
            preamble = (
                f"Idle > {idle_minutes} minutes. No fresh conversation gap; "
                f"revisiting a long-term interest: {topic!r}."
            )
        else:  # manual / unknown
            preamble = (
                f"Idle > {idle_minutes} minutes. Autonomous research topic: {topic!r}."
            )
        mission = (
            f"{preamble}\n"
            f"Web findings:\n{digest}\n"
            "Produce a 2-sentence reflection that may later become a Core Memory."
        )
        # Yield BEFORE the largest SLM call in the proactive cycle (~1 s
        # on NPU). Manual `/research` paths skip the yield (operator
        # explicitly asked); autonomous paths defer if the Architect spoke
        # while we were waiting on SearXNG.
        if not topic_override and self._yield_to_foreground("pre_synthesis", topic=topic):
            return None
        try:
            trace = await self._brain.proactive_thought(mission)
        except Exception:
            log.exception("Brain.proactive_thought failed")
            return None

        reflection = trace.filtered.text
        # 4. REFINE — single-pass critique to strip hallucinated specifics
        # before this reflection enters proactive.jsonl and (potentially)
        # gets promoted into Core Memories. Cheap insurance: ~1 SLM call.
        refine_verdict = "skipped"
        original_reflection = reflection
        if self._cfg.proactive.refine_enabled:
            try:
                refine_verdict, reflection = await self._brain.refine_proactive_reflection(
                    topic=topic,
                    snippets=digest,
                    draft=reflection,
                )
            except Exception:
                log.exception("Proactive REFINE wrapper crashed; keeping draft")
                refine_verdict = "error"
                reflection = original_reflection
            log.info(
                "PROACTIVE refine verdict=%s topic=%r changed=%s",
                refine_verdict,
                topic,
                reflection.strip() != original_reflection.strip(),
            )
        await self._append_log(f"PROACTIVE source={source} topic={topic!r} :: {reflection}")
        self._proactive_count += 1
        self._last_proactive_topic = topic
        self._last_proactive_at = self._now().isoformat()
        self._last_proactive_source = source

        # Self-reflection on the proactive cycle (fire-and-forget through
        # the Skill registry so the audit lands in state/skills.jsonl).
        if self._reflection is not None:
            task = asyncio.create_task(
                self._skill_registry.invoke(
                    "self_reflect",
                    self._skill_ctx,
                    kind="proactive",
                    input_text=topic,
                    response=reflection,
                    web_searched=bool(results),
                    backend=trace.backend,
                )
            )
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)

        event = {
            "ts": self._last_proactive_at,
            "topic": topic,
            "source": source,
            "manual": topic_override is not None,
            "triage_decisions": triage_decisions,
            "hits": [
                {
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("snippet", ""),
                }
                for r in results
            ],
            "reflection": reflection,
            "refine_verdict": refine_verdict,
            "draft_reflection": (
                original_reflection
                if refine_verdict == "REWRITE"
                else None
            ),
        }
        await self._record_proactive_event(event)
        return event

    async def _select_research_topic(self) -> tuple[str | None, str, list[dict]]:
        """Pick the next research topic by closing real cognitive gaps first.

        Returns a tuple of (topic, source, triage_decisions) where source is
        one of "stm_gap", "learned_preference", or "skipped".
        triage_decisions is an audit trail of every candidate considered:
          [{"topic": "...", "verdict": "unknown" | "known_by_slm" | "in_ltm"}]
        """
        decisions: list[dict] = []
        cfg = self._cfg.proactive

        if cfg.stm_gap_extraction_enabled:
            try:
                gaps = await self._brain.extract_stm_gaps(
                    limit=int(cfg.max_candidates_per_cycle),
                )
            except Exception:
                log.exception("STM gap extraction crashed; falling back.")
                gaps = []
            log.info("PROACTIVE select: %d STM gap candidate(s).", len(gaps))
            for candidate in gaps:
                # Cheap LTM check first — saves a SLM call when soul/archive
                # already covers the topic.
                if await self._is_in_ltm(candidate):
                    decisions.append({"topic": candidate, "verdict": "in_ltm"})
                    continue
                try:
                    known = await self._brain.triage_knowledge(
                        candidate,
                        known_token=cfg.triage_known_token,
                    )
                except Exception:
                    log.exception("Triage crashed for %r; treating as UNKNOWN.", candidate)
                    known = False
                if known:
                    decisions.append({"topic": candidate, "verdict": "known_by_slm"})
                    continue
                decisions.append({"topic": candidate, "verdict": "unknown"})
                return candidate, "stm_gap", decisions

        if cfg.fallback_to_preferences:
            pref = await self._pick_research_topic()
            if pref:
                return pref, "learned_preference", decisions

        return None, "skipped", decisions

    async def _is_in_ltm(self, topic: str) -> bool:
        """Cheap substring check against soul.md dynamic_growth + archive.md.

        Returns True when any non-trivial word from `topic` appears in LTM,
        which is conservative enough to avoid relearning known material
        without needing another SLM call.
        """
        normalized = (topic or "").lower().strip()
        if not normalized:
            return True
        # Strip stopwords to avoid trivial matches like "the" / "and".
        words = [
            w for w in re.findall(r"[\w-]+", normalized)
            if len(w) > 3 and w not in _LTM_STOPWORDS
        ]
        if not words:
            return False
        try:
            snap = await self._soul.read()
            soul_text = snap.dynamic_growth.lower()
        except Exception:
            log.exception("LTM check: failed reading soul; assuming not in LTM.")
            soul_text = ""
        archive_text = ""
        if self._archive_path.exists():
            try:
                archive_text = self._archive_path.read_text(encoding="utf-8").lower()
            except OSError:
                log.exception("LTM check: failed reading archive.")
        # Hit if MOST salient words appear in either store. Use full topic as
        # a single phrase first (strongest signal), then fall back to a
        # majority-of-words check for paraphrased mentions.
        if normalized in soul_text or normalized in archive_text:
            return True
        hits = sum(1 for w in words if w in soul_text or w in archive_text)
        return hits >= max(2, len(words) // 2 + 1)

    async def _record_proactive_event(self, event: dict) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._append_proactive_blocking, event)

    @staticmethod
    def _append_proactive_blocking(event: dict) -> None:
        PROACTIVE_FEED.parent.mkdir(parents=True, exist_ok=True)
        with PROACTIVE_FEED.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    async def _pick_research_topic(self) -> str | None:
        """Pull the most recent line under [LEARNED_PREFERENCES] in soul.md."""
        snap = await self._soul.read()
        section_re = re.compile(
            r"#\s*\[LEARNED_PREFERENCES\](?P<body>.*?)(?=^#\s*\[|\Z)",
            re.DOTALL | re.MULTILINE,
        )
        m = section_re.search(snap.dynamic_growth)
        if not m:
            return None
        for line in reversed(m.group("body").splitlines()):
            stripped = line.strip().lstrip("-").strip()
            if stripped:
                return stripped
        return None

    # ---------- metabolism ----------------------------------------------------

    async def metabolism(self) -> None:
        """Sleep-window maintenance (HEARTBEAT_LOGIC §2)."""
        log.info("Sleep Metabolism: starting consolidation cycle.")

        # 1. Scan TWO sources of the day's experience:
        #    (a) heartbeat telemetry log  — PULSE / PROACTIVE entries
        #    (b) STM conversation journal — actual user/agent turns
        # Without (b), archive.md would only ever contain sensor data — the
        # agent's lived conversations would never be consolidated to LTM.
        log_text = await self._collect_recent_logs()
        convo_text = await self._collect_conversation_journal()
        merged = "\n\n".join(
            section for section in (
                f"---DAILY LOG (telemetry)---\n{log_text.strip()}" if log_text.strip() else "",
                f"---CONVERSATION JOURNAL---\n{convo_text.strip()}" if convo_text.strip() else "",
            ) if section
        )
        if not merged:
            log.info("Sleep Metabolism: no logs or conversations to consolidate.")
        else:
            # 2. Extract 3–5 key facts via SLM.
            facts = await self._extract_facts(merged)
            # 3. Update archive.md.
            if facts:
                await self._append_archive(facts)

            # 4. Soul Evolution: append candidate Core Memories.
            for fact in facts[:2]:  # most-salient promoted
                try:
                    await self._soul.append_core_memory(fact)
                except Exception:
                    log.exception("Failed to promote core memory: %s", fact)

        # 4b. Reflection consolidation — close the self-learning loop.
        await self._consolidate_reflections()

        # 5. Purge STM (RAM + journal). Day's facts now live in archive/soul.
        n = await self._stm.purge()
        log.info("Sleep Metabolism: STM purged (%d turns dropped, journal truncated).", n)

    async def _collect_recent_logs(self) -> str:
        """Return the heartbeat log covering the just-finished duty cycle.

        Metabolism fires at the boundary into Sleep (default 02:00), AFTER
        the calendar date has already rolled over. Most of the duty cycle
        therefore lives in YESTERDAY's log file, with only the 0–sleep_start
        tail in today's. Read both — yesterday first — so consolidation sees
        the full 20 h of activity, not just the last two hours.
        """
        today = self._now().date()
        yesterday = today - timedelta(days=1)
        chunks: list[str] = []
        for d in (yesterday, today):
            log_file = self._log_dir / f"{d.isoformat()}.log"
            if log_file.exists():
                try:
                    chunks.append(log_file.read_text(encoding="utf-8"))
                except OSError:
                    log.exception("Failed reading log %s", log_file)
        return "\n".join(chunks)

    async def _collect_conversation_journal(self) -> str:
        """Return today's STM journal as a readable transcript for fact
        extraction. The heartbeat log only contains telemetry (PULSE,
        PROACTIVE) — actual user/agent turns live ONLY in the STM journal
        (write-through, since the Working Memory Consolidation refactor).
        Without this, archive.md would consolidate sensor data instead of
        the day's actual conversation.
        """
        # Make sure pending RAM-buffered turns are on disk before we read it.
        # Without this flush, late-evening conversation would be missed by
        # the consolidation pass.
        try:
            await self._stm.flush_journal()
        except Exception:
            log.exception("STM flush before consolidation failed (continuing).")
        journal = self._stm.journal_path
        if journal is None or not journal.exists():
            return ""
        try:
            lines = journal.read_text(encoding="utf-8").splitlines()
        except OSError:
            log.exception("Failed reading STM journal during consolidation.")
            return ""
        out: list[str] = []
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
                role = rec.get("role", "?")
                content = (rec.get("content", "") or "").replace("\n", " ").strip()
            except json.JSONDecodeError:
                continue
            if not content:
                continue
            label = "Architect" if role == "architect" else (
                "Agent" if role == "agent" else role.capitalize()
            )
            out.append(f"{label}: {content}")
        return "\n".join(out)

    async def _extract_facts(self, log_text: str) -> list[str]:
        mission = (
            "Read the day's record below \u2014 it contains both heartbeat "
            "telemetry and the actual conversation between the Architect and "
            "the agent. Extract 3 to 5 concise, factual insights worth "
            "preserving in long-term memory. Prioritize: "
            "(1) preferences or facts the Architect revealed, "
            "(2) commitments or decisions made, "
            "(3) recurring themes the agent should remember tomorrow. "
            "Skip routine sensor readings unless something unusual happened. "
            "Return one fact per line, no numbering, no preamble.\n\n"
            f"---DAY RECORD START---\n{log_text}\n---DAY RECORD END---"
        )
        try:
            trace = await self._brain.proactive_thought(mission)
        except Exception:
            log.exception("Memory consolidation SLM call failed")
            return []

        facts: list[str] = []
        for line in trace.filtered.text.splitlines():
            cleaned = line.strip().lstrip("-•*").strip()
            if cleaned and not cleaned.startswith("#"):
                facts.append(cleaned)
            if len(facts) >= 5:
                break
        log.info(
            "Sleep Metabolism: SLM extracted %d fact(s) from %d-char day record.",
            len(facts[:5]),
            len(log_text),
        )
        return facts[:5]

    async def _append_archive(self, facts: list[str]) -> None:
        stamp = self._now().date().isoformat()
        block = [f"\n## {stamp}"] + [f"- {f}" for f in facts]
        with self._archive_path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(block) + "\n")
        log.info(
            "Sleep Metabolism: archive.md updated (%d fact(s) appended under %s -> %s).",
            len(facts),
            stamp,
            self._archive_path,
        )

    async def _consolidate_reflections(self) -> None:
        """Promote recurring reflection signals into soul.md (DYNAMIC region).

        Closes the self-learning loop:
          * Topics that recurred in `interest` across the last 24 h get added
            to LEARNED_PREFERENCES (deduped against existing entries).
          * The most-recent low-quality reflection's `lesson` is recorded as
            an EMOTIONAL_EVOLUTION entry — the agent's growth log.
        """
        if self._reflection is None:
            log.info("Sleep Metabolism: reflection engine disabled, skipping consolidation.")
            return
        cutoff = self._now() - timedelta(hours=24)
        entries = self._reflection.read_recent(since=cutoff)
        if not entries:
            log.info("Sleep Metabolism: no reflections in the last 24 h.")
            return

        # Aggregate interests (case-insensitive, frequency >= 2).
        from collections import Counter
        interest_counts = Counter(
            e.interest.strip() for e in entries if e.interest.strip()
        )
        recurring = [topic for topic, n in interest_counts.most_common(5) if n >= 2]

        # Read existing preferences to avoid duplicates.
        try:
            snap = await self._soul.read()
            existing = snap.dynamic_growth.lower()
        except Exception:
            log.exception("Sleep Metabolism: could not read soul; skipping consolidation.")
            return

        promoted = 0
        for topic in recurring:
            if topic.lower() in existing:
                continue
            try:
                await self._soul.append_preference(
                    f"{topic} (auto-learned via reflection on "
                    f"{self._now().date().isoformat()})"
                )
                promoted += 1
                log.info("Sleep Metabolism: promoted interest -> preference: %r", topic)
            except Exception:
                log.exception("Failed to promote interest %r", topic)

        # Pick the most recent low-quality reflection's lesson as the day's
        # emotional growth entry — this is what hurt today, learn from it.
        low_quality = [e for e in entries if e.quality == "low" and e.lesson]
        if low_quality:
            lesson = low_quality[-1].lesson
            try:
                await self._soul.append_emotion_event(
                    f"Lesson ({self._now().date().isoformat()}): {lesson}"
                )
                log.info("Sleep Metabolism: recorded growth lesson: %r", lesson[:80])
            except Exception:
                log.exception("Failed to append emotion event for lesson")

        log.info(
            "Sleep Metabolism: reflection consolidation done "
            "(entries=%d, recurring_interests=%d, promoted=%d, low_quality=%d)",
            len(entries),
            len(recurring),
            promoted,
            len(low_quality),
        )

        # ---------- A3: Skill failure consolidation -----------------
        # Read the SkillRegistry audit feed (which ReflectionEngine
        # mirrors via `summarise_skills_recent`) and surface systemic
        # failures into soul.md so the agent carries the memory across
        # restarts. We only flag a Skill when:
        #   * it ran at least 3 times in the lookback window (so a
        #     single transient failure doesn't pollute the soul), AND
        #   * more than half of those runs failed (clear signal of a
        #     broken backend, not normal variance).
        # The note is appended as an emotion event because Sleep
        # Metabolism is the agent's growth log; a chronically broken
        # tool IS an emotional fact for an embodied agent.
        try:
            skills_summary = self._reflection.summarise_skills_recent(since=cutoff)
        except Exception:
            log.exception("Sleep Metabolism: skills summary failed")
            skills_summary = {}
        flagged: list[tuple[str, dict]] = [
            (name, stats)
            for name, stats in skills_summary.items()
            if stats["total"] >= 3 and stats["fail_rate"] > 0.5
        ]
        # Sort by absolute failure count (loudest issues first) — keeps
        # the soul focused on what's hurting the agent most.
        flagged.sort(key=lambda t: t[1]["failed"], reverse=True)
        for name, stats in flagged[:3]:  # cap to top 3 per cycle
            note = (
                f"Sleep Metabolism ({self._now().date().isoformat()}): "
                f"skill '{name}' failed {stats['failed']}/{stats['total']} "
                f"times in the last 24h (fail_rate={stats['fail_rate']:.0%})"
            )
            if stats.get("last_error"):
                note += f" — last error: {stats['last_error']}"
            try:
                await self._soul.append_emotion_event(note)
                log.info(
                    "Sleep Metabolism: flagged skill failure name=%s failed=%d total=%d",
                    name, stats["failed"], stats["total"],
                )
            except Exception:
                log.exception(
                    "Sleep Metabolism: failed to append skill flag name=%s", name,
                )
        if not flagged and skills_summary:
            log.info(
                "Sleep Metabolism: skills audit clean (skills=%d, total_invokes=%d)",
                len(skills_summary),
                sum(s["total"] for s in skills_summary.values()),
            )

    # ---------- helpers -------------------------------------------------------

    async def _append_log(self, line: str) -> None:
        now = self._now()
        log_file = self._log_dir / f"{now.date().isoformat()}.log"
        stamp = now.strftime("%H:%M:%S")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: log_file.open("a", encoding="utf-8").write(f"[{stamp}] {line}\n"),
        )

    async def _record_stress_transition(self, *, now: datetime, vitals) -> None:
        """Detect rising/falling edges of vitals.is_stressed and record them.

        Two outputs per transition:
          * a structured log line in `state/logs/agent.log` (the live chat
            log) so operators tailing the file see the event immediately;
          * an entry in `state/vitals_events.jsonl` so the dashboard can
            render a stress timeline without parsing the rolling text log.

        Per-pulse stress is intentionally NOT logged — only the transitions.
        """
        now_stressed = bool(vitals.is_stressed)
        if now_stressed == self._was_stressed:
            return  # no edge — nothing to record

        if now_stressed:
            # Rising edge — ENTER
            self._was_stressed = True
            self._stress_started_at = now
            self._stress_peak_temp = vitals.temperature_c
            self._stress_peak_ram = vitals.ram_percent
            temp_str = (
                f"{vitals.temperature_c:.1f}°C"
                if vitals.temperature_c is not None
                else "n/a"
            )
            log.warning(
                "VITALS stress=ENTER temp=%s ram=%.1f%% (%s)",
                temp_str,
                vitals.ram_percent,
                vitals.describe(),
            )
            event = {
                "ts": now.isoformat(),
                "kind": "stress_enter",
                "temp": vitals.temperature_c,
                "ram": vitals.ram_percent,
                "cpu": vitals.cpu_percent,
            }
        else:
            # Falling edge — EXIT
            duration_s = 0
            if self._stress_started_at is not None:
                duration_s = int((now - self._stress_started_at).total_seconds())
            peak_str = (
                f"{self._stress_peak_temp:.1f}°C"
                if self._stress_peak_temp is not None
                else "n/a"
            )
            log.info(
                "VITALS stress=EXIT duration=%ds peak_temp=%s peak_ram=%.1f%% (%s)",
                duration_s,
                peak_str,
                self._stress_peak_ram,
                vitals.describe(),
            )
            event = {
                "ts": now.isoformat(),
                "kind": "stress_exit",
                "duration_s": duration_s,
                "peak_temp": self._stress_peak_temp,
                "peak_ram": self._stress_peak_ram,
                "current_temp": vitals.temperature_c,
                "current_ram": vitals.ram_percent,
            }
            self._was_stressed = False
            self._stress_started_at = None
            self._stress_peak_temp = None
            self._stress_peak_ram = 0.0

        # Append to the JSONL feed off-thread so the pulse loop never blocks
        # on a slow disk. Failures degrade gracefully — the log line above is
        # the durable signal; the JSONL is only for the dashboard timeline.
        try:
            VITALS_EVENTS_FEED.parent.mkdir(parents=True, exist_ok=True)
            loop = asyncio.get_running_loop()
            line = json.dumps(event, ensure_ascii=False) + "\n"
            await loop.run_in_executor(
                None,
                lambda: VITALS_EVENTS_FEED.open("a", encoding="utf-8").write(line),
            )
        except Exception:
            log.exception("Failed to append vitals_events.jsonl")

    async def _publish_state(self, *, vitals, idle_seconds: int) -> None:
        """Atomically publish a JSON snapshot of the agent's live state.

        Read by `ui/dashboard.py` every few seconds. Decoupled via filesystem
        so the two processes don't need IPC.
        """
        now = self._now()
        mood = await self._emotions.snapshot()

        sample = None
        if vitals is not None:
            # Embed the current mood snapshot into each history sample so the
            # dashboard can plot mood-over-time on the SAME time axis as
            # cpu/ram/temp without needing a second feed. `brain_online`
            # joins the sample so the dashboard can shade outage windows
            # on the vitals chart (cognition is a vital sign — when the
            # SLM is down the body is alive but the mind is not).
            sample = {
                "ts": now.isoformat(),
                "cpu": vitals.cpu_percent,
                "ram": vitals.ram_percent,
                "temp": vitals.temperature_c,
                "stressed": vitals.is_stressed,
                "brain_online": vitals.brain_online,
                "mood_joy": mood.joy,
                "mood_anger": mood.anger,
                "mood_sorrow": mood.sorrow,
                "mood_excitement": mood.excitement,
                "mood_calm": mood.calm,
            }
            self._history.append(sample)

        active_channel, active_intensity = mood.dominant_excluding_baseline()
        # Brain (SLM) life signs \u2014 reported as a top-level field so the
        # dashboard can render a chip without diving into the vitals
        # sub-object. Defaults keep the snapshot well-formed when no
        # vitals were sampled this pulse (e.g. during sleep).
        brain_block = {
            "online": True,
            "backend": "unknown",
            "last_error": None,
            "recovery_seconds": None,
        }
        if vitals is not None:
            brain_block = {
                "online": vitals.brain_online,
                "backend": vitals.brain_backend,
                "last_error": vitals.brain_last_error,
                "recovery_seconds": vitals.brain_recovery_seconds,
            }

        snapshot = {
            "designation": self._cfg.system.individual_designation,
            "now": now.isoformat(),
            "started_at": self._started_at,
            "is_sleeping": self._is_sleeping,
            "duty_window": f"{self._cfg.system.duty_start}-{self._cfg.system.sleep_start}",
            "timezone": self._cfg.system.timezone,
            "vitals": sample,
            "vitals_describe": vitals.describe() if vitals else "Sleep mode (no sampling)",
            "brain": brain_block,
            "mood": asdict(mood),
            "mood_dominant": mood.dominant(),
            # The "active" mood is the strongest non-baseline channel — far
            # more useful for the operator than `mood_dominant` (which is
            # almost always calm because of the 0.6 baseline).
            "mood_active_channel": active_channel,
            "mood_active_intensity": active_intensity,
            "stress_active": self._was_stressed,
            "stress_started_at": (
                self._stress_started_at.isoformat()
                if self._stress_started_at is not None
                else None
            ),
            "idle_seconds": idle_seconds,
            "idle_threshold_seconds": int(self._idle_proactive_threshold.total_seconds()),
            "pulse_count": self._pulse_count,
            "proactive_count": self._proactive_count,
            "stress_count": self._stress_count,
            "last_proactive_topic": self._last_proactive_topic,
            "last_proactive_at": self._last_proactive_at,
            "last_proactive_source": self._last_proactive_source,
            "stm_size": self._stm.size_estimate(),
            "stm_max": self._cfg.memory.stm_max_turns,
            "stm_pending": self._stm.pending_writes,
            "history": list(self._history),
        }

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._write_state_blocking, snapshot)

    def _write_state_blocking(self, snapshot: dict) -> None:
        # Atomic write: tmp file + os.replace, so the dashboard never reads
        # a half-written JSON document.
        tmp = self._state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._state_path)

    def _now(self) -> datetime:
        return datetime.now(tz=self._tz)

    def _is_duty_window(self, now: datetime) -> bool:
        """True iff `now` falls in [duty_start, sleep_start) modulo midnight.

        Default duty 06:00 → 02:00 wraps midnight; handle both cases.
        """
        t = now.time()
        if self._duty_start < self._sleep_start:
            return self._duty_start <= t < self._sleep_start
        # Wrap-around (default config).
        return t >= self._duty_start or t < self._sleep_start


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(hour=int(hh), minute=int(mm))
