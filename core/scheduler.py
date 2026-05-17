"""core.scheduler — Recurring research task system.

The operator can issue a request like:

    "check the Microsoft stock price and news every hour and give me an
     insight summary report"

The connector (telegram / web_chat) calls `Brain.parse_task_intent()` first.
If that returns a `TaskSpec`, the connector hands it to this module instead
of running a normal `Brain.think()` cycle. From then on, every
`interval_seconds` the scheduler:

  1. Runs each query in `spec.queries` against SearXNG.
  2. Concatenates the snippets into a single mission brief.
  3. Calls `Brain.synthesize_task_report()` to produce the analysis.
  4. Broadcasts the report to EVERY bound connector (telegram + web_chat).
     The originating connector still owns the task for cancel/list ACL
     purposes, but reports fan out to all live channels so the operator
     sees them wherever they happen to be.

Design constraints (from operator decisions on 2026-05-07, refined 2026-05-08):
  * Free-form NL is the only creation channel — there is no /task slash
    command. (List/cancel are exposed as slash commands AND as NL queries
    — "show my tasks" — because they're deterministic and don't cost an
    SLM call.)
  * Reports broadcast to ALL bound connectors. The deliver registry maps
    origin → callback; on fire, the scheduler iterates and pushes the
    report through every registered connector.
  * SearXNG is the only data source. We do NOT add finance APIs — the
    agent paraphrases prices from news snippets and is honest about
    freshness.
  * Schedules are fixed intervals (1h, 30m, 1d) — no cron, no "at HH:MM".
  * Tasks PAUSE during Sleep Metabolism (02:00-06:00). After wakeup, any
    task whose `next_run_at` slid past in the night fires ONCE as a
    catch-up, then resumes its regular cadence.

Persistence: `state/tasks.yaml`. Survives restarts. Each task carries
its origin so the operator can see who created it; deliver callbacks are
rebound at boot when each connector calls `bind_deliver`.
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Awaitable, Callable, TYPE_CHECKING
from zoneinfo import ZoneInfo

import yaml

from .skills import SkillContext, SkillRegistry

if TYPE_CHECKING:
    from .brain import Brain
    from .config import Config
    from .heartbeat import Heartbeat

log = logging.getLogger(__name__)

# Async callback signature the connector registers per task. The scheduler
# calls this with the rendered report text. Connectors decide what
# "deliver" means (Telegram message, STM injection, etc.).
DeliverFn = Callable[[str], Awaitable[None]]

# Cheap pre-filter: regex over user input to decide whether to invoke the
# (more expensive) SLM intent-parse call. If NONE of these patterns match,
# the message is treated as normal chat — no SLM round-trip wasted.
# Pattern is INTENTIONALLY broad: false positives only cost ONE extra SLM
# call (which then returns NOT_TASK), while false negatives miss the
# operator's actual scheduling request.
_INTERVAL_HINT_RE: re.Pattern[str] = re.compile(
    r"\b(every|each)\s+(\d+\s*)?(minute|min|hour|hr|day|week)s?\b"
    r"|\b(hourly|daily|weekly)\b"
    r"|\b(in|on|at)\s+(an?\s+)?(hourly|daily|weekly)\s+(basis|schedule|cadence)\b"
    r"|\bon\s+a\s+(\d+\s*)?(minute|min|hour|hr|day|week)\s*(ly)?\s*basis\b",
    re.IGNORECASE,
)

# Cheap pre-filter for "show me my tasks" / "what's scheduled" style
# queries. Matched BEFORE the scheduling pre-filter in the connectors so
# a message like "list my hourly tasks" goes to the listing path (deterministic,
# no SLM call) instead of the intent-parser. Intentionally permissive —
# false positives just render an empty/short list.
_TASK_QUERY_RE: re.Pattern[str] = re.compile(
    r"\b(list|show|what|which|any|see|view|display|check)\b[^.?!]{0,40}"
    r"\b(task|tasks|schedule|scheduled|recurring|active|running|tracking)\b"
    r"|\bwhat(?:'s| is| are)\b[^.?!]{0,30}\b(scheduled|running|tracking|tasks?)\b"
    r"|\b(my|the|all)\s+(tasks|scheduled\s+tasks|recurring\s+tasks|schedules?)\b"
    r"|\b(tasks?|schedules?)\s+(list|status)\b",
    re.IGNORECASE,
)

# Cheap pre-filter for "change/update task X to ..." style messages.
# Matched BEFORE the create-intent filter so a request to retune an
# existing task isn't mis-routed to add a NEW task. Requires both an
# update verb AND a task-noun (or a short id) so plain "change my mind"
# doesn't trigger.
_TASK_MODIFY_RE: re.Pattern[str] = re.compile(
    r"\b(change|update|modify|edit|set|adjust|rename|retune|reschedule|switch)\b"
    r"[^.?!]{0,80}"
    r"(\b(task|schedule|scheduled|cadence|interval|frequency|queries)\b"
    r"|\bt[0-9a-f]{4}\b)",
    re.IGNORECASE,
)

# Tighter parser used to convert e.g. "every 30 minutes" / "hourly" / "1d"
# into a number of seconds. Returns None if the spec is malformed.
_INTERVAL_PARSE_RE: re.Pattern[str] = re.compile(
    r"^\s*(?:every\s+)?(?P<num>\d+)?\s*(?P<unit>m|min|minute|h|hr|hour|d|day|w|week)s?\s*$",
    re.IGNORECASE,
)
_UNIT_SECONDS: dict[str, int] = {
    "m": 60, "min": 60, "minute": 60,
    "h": 3600, "hr": 3600, "hour": 3600,
    "d": 86400, "day": 86400,
    "w": 604800, "week": 604800,
}


def looks_like_task_request(text: str) -> bool:
    """Cheap regex pre-filter — True if the message MIGHT be a scheduling request.

    Used by connectors to decide whether to spend an SLM call on intent
    parsing. We err on the side of false positives (a wasted ~200 ms SLM
    call that returns NOT_TASK) over false negatives (missing a real
    request). See `_INTERVAL_HINT_RE` for the patterns matched.
    """
    if not text:
        return False
    return bool(_INTERVAL_HINT_RE.search(text))


def looks_like_task_query(text: str) -> bool:
    """Cheap regex pre-filter — True if the operator is asking to SEE tasks.

    Connectors call this BEFORE `looks_like_task_request` so messages
    like "list my scheduled tasks" or "what tasks are running?" route to
    the deterministic listing path instead of the SLM intent-parser.
    Returns False for scheduling requests (which usually contain interval
    words) so the two filters don't fight over a single message.
    Also returns False when an action verb is present — "end the
    schedule t091f" must route to ACTION, not to LIST, even though the
    "the schedule" fragment would otherwise match.
    """
    if not text:
        return False
    # If it looks like a scheduling request (has interval words), defer
    # to that path — "check news every hour" is creating, not querying.
    if _INTERVAL_HINT_RE.search(text):
        return False
    # If an action verb is present, defer to action — "end the
    # schedule t091f" / "delete the schedule" are NOT listing queries.
    if _TASK_ACTION_RE.search(text):
        return False
    return bool(_TASK_QUERY_RE.search(text))


def looks_like_task_modify_request(text: str) -> bool:
    """Cheap regex pre-filter — True if the message looks like a task EDIT.

    Connectors call this AFTER the listing filter and BEFORE the
    create-intent filter. The two intent paths are distinguished by the
    presence of update verbs ("change", "update", "set", …) plus a task
    noun — a request like *"change task t8f3a to every 2 hours"* will
    match here even though it ALSO contains an interval phrase that
    would otherwise look like a creation request.

    False on the listing patterns so "check my tasks" stays on the list path.
    False on action patterns so "cancel/pause/resume task X" routes to action.
    """
    if not text:
        return False
    if _TASK_QUERY_RE.search(text):
        return False
    if _TASK_ACTION_RE.search(text):
        return False
    return bool(_TASK_MODIFY_RE.search(text))


# Cheap pre-filter for "cancel/pause/resume task X" style messages.
# Connectors call this AFTER the listing filter and BEFORE the modify
# filter — action verbs are mutually exclusive with modify verbs by
# construction (different verb classes), so the order is just for
# fallthrough efficiency. Either an explicit task noun OR a 5-char id
# token (`t[0-9a-f]{4}`) is required so plain "stop it" / "pause"
# don't match.
_TASK_ACTION_RE: re.Pattern[str] = re.compile(
    r"\b(cancel|stop|delete|remove|kill|abort|terminate|end)\b"
    r"[^.?!]{0,80}"
    r"(\b(task|tasks|schedule|scheduled|recurring|tracking|monitoring|watching)\b"
    r"|\bt[0-9a-f]{4}\b)"
    r"|\b(pause|suspend|halt|freeze)\b"
    r"[^.?!]{0,80}"
    r"(\b(task|tasks|schedule|scheduled|recurring|tracking|monitoring|watching)\b"
    r"|\bt[0-9a-f]{4}\b)"
    r"|\b(resume|unpause|restart|continue|reactivate|re-?enable)\b"
    r"[^.?!]{0,80}"
    r"(\b(task|tasks|schedule|scheduled|recurring|tracking|monitoring|watching)\b"
    r"|\bt[0-9a-f]{4}\b)",
    re.IGNORECASE,
)


def looks_like_task_action_request(text: str) -> bool:
    """Cheap regex pre-filter — True if the message asks to cancel/pause/resume.

    Connectors call this between the listing filter and the modify
    filter. Mutually exclusive with modify by construction (different
    verb classes). Does NOT defer to listing — action verbs are a
    stronger signal than the noun-only listing forms, so a message like
    "end the schedule t091f" routes here even though the "the schedule"
    fragment also matches the listing regex. The listing helper
    (`looks_like_task_query`) handles the converse deferral.
    """
    if not text:
        return False
    return bool(_TASK_ACTION_RE.search(text))


# Deterministic NL extractor for explicit cadence phrases. Used by
# `Brain.parse_task_intent` and `Brain.parse_task_modify_intent` to
# OVERRIDE whatever the small triage SLM emits — qwen2:1.5b copies the
# in-context example ("INTERVAL: 1h") rather than respecting the
# operator's literal request, so a message like "every 10 minutes" was
# being scheduled as 1 hour. When this extractor returns a value, the
# SLM's INTERVAL line is ignored. Returns None when the operator's text
# contains no explicit cadence phrase (e.g. "summarise top AI papers
# every morning") — in that case we trust the SLM.
_EXPLICIT_INTERVAL_RE: re.Pattern[str] = re.compile(
    r"\b(?:every|each)\s+"
    r"(?:(?P<num>\d+)\s+|(?P<other>other\s+))?"
    r"(?P<unit>seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?)\b"
    r"|\b(?P<bare>hourly|daily|weekly)\b",
    re.IGNORECASE,
)
_EXPLICIT_UNIT_SECONDS: dict[str, int] = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
    "day": 86400, "days": 86400,
    "week": 604800, "weeks": 604800,
}


def extract_explicit_interval(text: str) -> int | None:
    """Pull a cadence (in seconds) from an operator's literal NL phrasing.

    Recognises:
      * "every N <unit>"  → N * seconds-per-unit
      * "every other <unit>" → 2 * seconds-per-unit
      * "every <unit>" (no number)  → 1 * seconds-per-unit
      * Bare aliases: "hourly" / "daily" / "weekly"

    Returns None when no cadence phrase is present, OR when the parsed
    number is non-positive. The caller is expected to use this value to
    OVERRIDE a small-SLM-emitted interval that copied a prompt example.
    Only the FIRST cadence phrase is returned — chained phrases like
    "every 10 minutes for an hour" are intentionally not handled.
    """
    if not text:
        return None
    m = _EXPLICIT_INTERVAL_RE.search(text)
    if not m:
        return None
    bare = (m.group("bare") or "").lower()
    if bare:
        return {"hourly": 3600, "daily": 86400, "weekly": 604800}[bare]
    unit = (m.group("unit") or "").lower()
    base = _EXPLICIT_UNIT_SECONDS.get(unit)
    if base is None:
        return None
    if m.group("other"):
        return 2 * base
    num_s = m.group("num")
    n = int(num_s) if num_s else 1
    if n <= 0:
        return None
    return n * base


def render_task_list(tasks: list["Task"], *, channel: str = "telegram") -> str:
    """Operator-friendly markdown rendering of the active task set.

    Used by both connectors so the Telegram, web-chat, and slash-command
    paths render identically. Empty list returns a hint about how to
    create a task.

    `channel` controls the management-hint footer at the bottom: Telegram
    users get the slash commands they actually have (`/cancel <id>` …),
    web users get a channel-appropriate hint (natural language or REST)
    so we never tell a web user to type a command that doesn't exist on
    their surface. Defaults to telegram for backwards compatibility with
    older callers.
    """
    if not tasks:
        return (
            "No active scheduled tasks. Ask me in chat to schedule one — e.g.\n"
            "  *\"check Microsoft stock and news every hour and summarise\"*."
        )
    lines = [f"*Active scheduled tasks ({len(tasks)}):*\n"]
    for t in tasks:
        state = "⏸ paused" if t.paused else "▶ active"
        nxt = t.next_run_at[11:16] if t.next_run_at and len(t.next_run_at) >= 16 else "?"
        last = (
            t.last_run_at[11:16]
            if t.last_run_at and len(t.last_run_at) >= 16 else "never"
        )
        lines.append(
            f"• `{t.id}` — *{t.topic}* — every {format_interval(t.interval_seconds)}\n"
            f"   from `{t.origin}` · fired {t.fire_count}× · last {last} · "
            f"next {nxt} · {state}"
        )
        if t.last_error:
            lines.append(f"   ⚠️ last error: {t.last_error}")
    if channel == "web_chat":
        lines.append(
            "\nManage: say *\"cancel <id>\"* / *\"pause <id>\"* / *\"resume <id>\"* "
            "in chat, or POST `/tasks/cancel|pause|resume` with `{\"id\": \"<id>\"}`."
        )
    else:
        lines.append("\nManage: /cancel <id> · /pause <id> · /resume <id>")
    return "\n".join(lines)


def parse_interval_spec(spec: str) -> int | None:
    """Convert "1h", "30m", "every 2 hours", "hourly" → seconds. None on failure.

    Accepted forms:
      * Plain numeric+unit: "1h", "30m", "2d", "1w"
      * With "every": "every 30m", "every 1 hour"
      * Bare aliases: "hourly" → 3600, "daily" → 86400, "weekly" → 604800
    """
    s = (spec or "").strip().lower()
    if not s:
        return None
    if s in ("hourly",):
        return 3600
    if s in ("daily",):
        return 86400
    if s in ("weekly",):
        return 604800
    m = _INTERVAL_PARSE_RE.match(s)
    if not m:
        return None
    num = int(m.group("num") or "1")
    unit = m.group("unit").lower()
    base = _UNIT_SECONDS.get(unit)
    if base is None or num <= 0:
        return None
    return num * base


def format_interval(seconds: int) -> str:
    """Pretty-print an interval back to operator-friendly form."""
    if seconds % 86400 == 0:
        n = seconds // 86400
        return f"{n} day" if n == 1 else f"{n} days"
    if seconds % 3600 == 0:
        n = seconds // 3600
        return f"{n} hour" if n == 1 else f"{n} hours"
    if seconds % 60 == 0:
        n = seconds // 60
        return f"{n} minute" if n == 1 else f"{n} minutes"
    return f"{seconds} seconds"


@dataclass(frozen=True)
class TaskSpec:
    """Structured form of a scheduling request, produced by Brain.parse_task_intent.

    `queries` is the list of SearXNG queries the agent thinks will best
    surface the requested information. For a "MSFT price + news" request
    the SLM might emit ["Microsoft stock price MSFT", "Microsoft news today"].
    """
    topic: str               # Short label, e.g. "Microsoft stock + news"
    queries: list[str]       # SearXNG queries to run on each fire
    interval_seconds: int    # Time between fires
    description: str         # The operator's original NL request, verbatim


@dataclass(frozen=True)
class TaskUpdate:
    """Structured form of a task-modify request, produced by Brain.parse_task_modify_intent.

    `task_id` is required — the SLM resolves whichever way the operator
    referenced the task ("the MSFT task", "t8f3a", "my hourly one").
    All other fields are None for "unchanged"; the scheduler updates only
    the fields that are populated.
    """
    task_id: str
    new_topic: str | None = None
    new_interval_seconds: int | None = None
    new_queries: list[str] | None = None
    new_description: str | None = None


@dataclass(frozen=True)
class TaskAction:
    """Structured form of a cancel/pause/resume request, produced by
    Brain.parse_task_action_intent.

    `action` is the verb the operator used (resolved to one of the three
    canonical names). `task_id` is the resolved id from the live task
    list (the SLM picks it whether the operator referenced the task by
    id, by topic, or by interval).
    """
    task_id: str
    action: str  # "cancel" | "pause" | "resume"


@dataclass
class Task:
    """A live recurring research task. Mutable — `next_run_at` shifts on each fire."""
    id: str
    topic: str
    queries: list[str]
    interval_seconds: int
    description: str
    origin: str              # "telegram" | "web_chat" — who created it (display only)
    created_at: str          # ISO 8601 in agent timezone
    next_run_at: str         # ISO 8601 in agent timezone
    last_run_at: str | None = None
    last_report: str | None = None
    last_error: str | None = None
    paused: bool = False
    fire_count: int = 0


class TaskScheduler:
    """Owns the task registry, persistence, and the firing loop.

    Lifecycle: instantiated once in main.py, started via `run_loop()` as
    its own asyncio task (parallel to heartbeat.pulse_loop), stopped via
    `stop()` on shutdown.
    """

    def __init__(
        self,
        *,
        config: "Config",
        brain: "Brain",
        skill_registry: SkillRegistry,
        skill_ctx: SkillContext,
        heartbeat: "Heartbeat",
        state_path: str | Path = "state/tasks.yaml",
        max_active_tasks: int = 16,
        results_per_query: int = 5,
        min_interval_seconds: int = 300,
        tick_seconds: int = 30,
    ) -> None:
        self._cfg = config
        self._brain = brain
        # Skill dispatch — each fire cycle now invokes the
        # "recurring_research" Skill instead of looping SearXNG calls
        # inline. The Skill returns evidence as
        # `[{"query", "hits", "error"}, ...]` so the per-query render
        # logic stays byte-identical to the legacy gather-and-format path.
        self._skill_registry = skill_registry
        self._skill_ctx = skill_ctx
        self._heartbeat = heartbeat
        self._state_path = Path(state_path)
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._max_active = max_active_tasks
        self._results_per_query = results_per_query
        # Floor on interval — protects SearXNG and the SLM from a
        # misparsed "every 1 minute" that would actually be every-second.
        # Operator can lower in config but should know what they're doing.
        self._min_interval = max(60, min_interval_seconds)
        self._tick = max(5, tick_seconds)
        self._tz = ZoneInfo(config.system.timezone)
        # Sleep window — same source of truth as Heartbeat. Tasks pause
        # during sleep (operator decision: "Pause during sleep, fire after
        # wakeup"). We don't query Heartbeat.is_sleeping directly because
        # the scheduler may want to fire DURING wakeup transitions.
        self._duty_start = _parse_hhmm(config.system.duty_start)
        self._sleep_start = _parse_hhmm(config.system.sleep_start)

        self._registry: dict[str, Task] = {}
        # Per-origin deliver callbacks. Connectors register themselves
        # via bind_deliver() at boot. On fire, the scheduler broadcasts
        # the report to EVERY entry here — reports fan out to all live
        # channels, regardless of which connector created the task.
        self._delivers: dict[str, DeliverFn] = {}
        # Locks: registry mutex protects add/cancel/list. _save_lock
        # serializes YAML disk writes so two concurrent fires can't
        # corrupt the file.
        self._registry_lock = asyncio.Lock()
        self._save_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        # Set when add_task is called so the loop wakes up early.
        self._wake = asyncio.Event()

    # ---------- public surface ------------------------------------------------

    async def load(self) -> int:
        """Read `state/tasks.yaml` and rehydrate tasks.

        Connectors call `bind_deliver(origin, ...)` after boot so the
        scheduler knows where to push reports. A task fires regardless
        of how many delivers are bound — if NONE are bound the report
        is logged with a warning rather than dropped silently.
        """
        if not self._state_path.exists():
            return 0
        try:
            raw = yaml.safe_load(self._state_path.read_text(encoding="utf-8")) or []
        except Exception:
            log.exception("Failed to load tasks file %s — starting empty.", self._state_path)
            return 0
        if not isinstance(raw, list):
            log.warning("Tasks file %s has unexpected shape — starting empty.", self._state_path)
            return 0
        n = 0
        async with self._registry_lock:
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                try:
                    task = Task(**entry)
                except TypeError:
                    log.warning("Skipping malformed task entry: %r", entry)
                    continue
                self._registry[task.id] = task
                n += 1
        log.info("Scheduler loaded %d task(s) from %s.", n, self._state_path)
        return n

    async def add_task(
        self,
        spec: TaskSpec,
        *,
        origin: str,
    ) -> Task:
        """Register a new task. Returns the persisted Task (with id + timestamps).

        `origin` is recorded for display + ACL only — reports fan out to
        ALL bound delivers, not just this one. Rejects (raises ValueError)
        when:
          * registry is at capacity
          * `spec.interval_seconds` is below the configured minimum
          * `spec.queries` is empty
        """
        if not spec.queries:
            raise ValueError("Task must have at least one query.")
        if spec.interval_seconds < self._min_interval:
            raise ValueError(
                f"Interval {spec.interval_seconds}s is below the minimum "
                f"{self._min_interval}s — pick a longer cadence."
            )
        async with self._registry_lock:
            if len(self._registry) >= self._max_active:
                raise ValueError(
                    f"Already running {len(self._registry)} tasks "
                    f"(max={self._max_active}). Cancel one first."
                )
            now = self._now()
            # `_short_id()` returns a 16-bit hex token (~65k space). With
            # max_active=16 the practical collision risk per add is ~0.02%,
            # but re-roll a few times defensively so a clash never silently
            # overwrites a live task.
            tid = _short_id()
            for _ in range(8):
                if tid not in self._registry:
                    break
                tid = _short_id()
            else:  # pragma: no cover — astronomically unlikely
                raise ValueError(
                    "Could not allocate a unique task id after 8 attempts; "
                    "registry may be unhealthy."
                )
            task = Task(
                id=tid,
                topic=spec.topic,
                queries=list(spec.queries),
                interval_seconds=int(spec.interval_seconds),
                description=spec.description,
                origin=origin,
                created_at=now.isoformat(),
                # First fire happens on the next loop tick — operator
                # immediately sees the task working, doesn't have to wait
                # the full interval to find out if their query is broken.
                next_run_at=now.isoformat(),
            )
            self._registry[tid] = task
        await self._save()
        # Wake the loop so the immediate first fire happens within ~1 tick
        # rather than waiting up to `_tick` seconds.
        self._wake.set()
        log.info(
            "TASK added id=%s origin=%s topic=%r interval=%s queries=%d delivers=%d",
            task.id, origin, task.topic,
            format_interval(task.interval_seconds), len(task.queries),
            len(self._delivers),
        )
        return task

    async def cancel_task(self, task_id: str) -> Task | None:
        """Remove a task. Returns the removed Task or None if id was unknown."""
        async with self._registry_lock:
            task = self._registry.pop(task_id, None)
        if task is None:
            return None
        await self._save()
        log.info("TASK cancelled id=%s topic=%r", task.id, task.topic)
        return task

    async def pause_task(self, task_id: str, paused: bool = True) -> Task | None:
        async with self._registry_lock:
            task = self._registry.get(task_id)
            if task is None:
                return None
            task.paused = bool(paused)
        await self._save()
        log.info("TASK %s id=%s topic=%r",
                 "paused" if paused else "resumed", task_id, task.topic)
        return task

    async def update_task(
        self,
        task_id: str,
        *,
        topic: str | None = None,
        interval_seconds: int | None = None,
        queries: list[str] | None = None,
        description: str | None = None,
    ) -> Task | None:
        """Mutate an existing task in place. Returns the updated Task or None.

        Only non-None fields are written. When `interval_seconds` changes,
        `next_run_at` is rescheduled to `now + new_interval` so the new
        cadence takes effect from this moment forward (the operator
        doesn't have to wait for the OLD next-run to elapse first).
        Validates the new interval against `_min_interval` — violations
        raise `ValueError` and the task is left untouched.
        """
        # Validate up front so we don't half-mutate the task. Empty
        # queries list is rejected (a task with no queries can't fire).
        if interval_seconds is not None and interval_seconds < self._min_interval:
            raise ValueError(
                f"Interval {interval_seconds}s is below the minimum "
                f"{self._min_interval}s — pick a longer cadence."
            )
        if queries is not None and not queries:
            raise ValueError("Updated task must have at least one query.")
        async with self._registry_lock:
            task = self._registry.get(task_id)
            if task is None:
                return None
            changed: list[str] = []
            if topic is not None and topic != task.topic:
                task.topic = topic
                changed.append("topic")
            if queries is not None and queries != task.queries:
                task.queries = list(queries)
                changed.append("queries")
            if description is not None and description != task.description:
                task.description = description
                changed.append("description")
            if interval_seconds is not None and interval_seconds != task.interval_seconds:
                task.interval_seconds = int(interval_seconds)
                # Re-baseline next fire from now so the new cadence
                # applies immediately rather than after the old interval.
                task.next_run_at = (
                    self._now() + timedelta(seconds=int(interval_seconds))
                ).isoformat()
                changed.append("interval")
            # Clear last_error ONLY when something actually changed — a true
            # no-op must leave both the in-memory task and the on-disk
            # registry in their pre-call state to avoid divergence.
            if changed:
                task.last_error = None
        if changed:
            await self._save()
            # Wake the loop in case the new interval brought next_run_at
            # forward (rare — typically next_run shifts later).
            self._wake.set()
            log.info(
                "TASK updated id=%s changed=%s topic=%r interval=%s queries=%d",
                task.id, ",".join(changed), task.topic,
                format_interval(task.interval_seconds), len(task.queries),
            )
        else:
            log.info("TASK update id=%s — no fields changed", task.id)
        return task

    async def list_tasks(self, origin: str | None = None) -> list[Task]:
        """Snapshot of current tasks. If `origin` is given, only that connector's.

        NOTE: with broadcast delivery the operator typically wants to see
        ALL tasks regardless of which channel created them — callers
        should pass `origin=None` for the user-facing "my tasks" view.
        """
        async with self._registry_lock:
            tasks = list(self._registry.values())
        if origin is not None:
            tasks = [t for t in tasks if t.origin == origin]
        return tasks

    async def get_task(self, task_id: str) -> Task | None:
        async with self._registry_lock:
            return self._registry.get(task_id)

    def bind_deliver(self, origin: str, deliver: DeliverFn) -> None:
        """Register a connector's deliver callback for broadcast delivery.

        Each origin ("telegram", "web_chat", …) installs ONE callback;
        re-registering replaces the previous one. On every task fire the
        scheduler calls every entry in this dict, so reports fan out to
        all live channels.
        """
        prev = self._delivers.get(origin)
        self._delivers[origin] = deliver
        if prev is None:
            log.info("Scheduler bound deliver for origin=%s (total channels=%d).",
                     origin, len(self._delivers))
        else:
            log.info("Scheduler replaced deliver for origin=%s.", origin)

    def unbind_deliver(self, origin: str) -> None:
        """Drop a connector's deliver callback (e.g. on shutdown). Safe if absent."""
        if self._delivers.pop(origin, None) is not None:
            log.info("Scheduler unbound deliver for origin=%s.", origin)

    # ---------- run loop ------------------------------------------------------

    async def run_loop(self) -> None:
        """Forever loop: every `_tick` seconds (or on wake), fire any task whose
        `next_run_at` has passed. Pauses during the Sleep window.

        On wakeup, multiple tasks may be due simultaneously. We fire them
        SEQUENTIALLY (not concurrently) to avoid hammering SearXNG and the
        SLM. With `_min_interval` ≥ 5 min this is fine.
        """
        log.info(
            "Scheduler starting (tasks=%d tick=%ds min_interval=%ds)",
            len(self._registry), self._tick, self._min_interval,
        )
        try:
            while not self._stop.is_set():
                try:
                    await self._tick_once()
                except Exception:
                    log.exception("Scheduler tick raised — continuing.")
                # Wait until next tick OR wake signal OR stop.
                self._wake.clear()
                await self._sleep_until_next_tick()
        finally:
            log.info("Scheduler stopped.")

    async def _sleep_until_next_tick(self) -> None:
        """Block for up to `_tick` seconds, returning early on wake or stop.

        Uses asyncio.wait so we don't leak the loser tasks (the ones whose
        event didn't fire) into the next iteration. Each loser task is
        cancelled before we return.
        """
        stop_t = asyncio.create_task(self._stop.wait())
        wake_t = asyncio.create_task(self._wake.wait())
        try:
            done, pending = await asyncio.wait(
                {stop_t, wake_t},
                timeout=self._tick,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for p in pending:
                p.cancel()
        except Exception:
            stop_t.cancel(); wake_t.cancel()
            raise

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()  # unblock the loop

    # ---------- internals -----------------------------------------------------

    async def _tick_once(self) -> None:
        now = self._now()
        if not self._is_duty_window(now):
            # Sleep window — don't fire. Tasks whose next_run_at falls in
            # this window will fire ONCE as a catch-up on the first tick
            # after wakeup (current implementation: any task with
            # next_run_at <= now fires, regardless of how far behind).
            return
        # Snapshot the registry under the lock to avoid mutation-during-
        # iteration races with add/cancel.
        async with self._registry_lock:
            due: list[Task] = []
            for task in self._registry.values():
                if task.paused:
                    continue
                try:
                    nra = datetime.fromisoformat(task.next_run_at)
                except ValueError:
                    log.warning("Task id=%s has bad next_run_at=%r — firing now.",
                                task.id, task.next_run_at)
                    due.append(task)
                    continue
                if nra.tzinfo is None:
                    nra = nra.replace(tzinfo=self._tz)
                if nra <= now:
                    due.append(task)
        for task in due:
            try:
                await self._fire(task)
            except Exception as exc:
                log.exception("TASK fire failed id=%s topic=%r",
                              task.id, task.topic)
                # Reschedule even on crash so a transient failure doesn't
                # park the task forever. Operator sees `last_error` in
                # /tasks output. Mutate under the registry lock so a
                # concurrent list_tasks never sees a torn snapshot.
                now = self._now()
                next_run = (
                    now + timedelta(seconds=task.interval_seconds)
                ).isoformat()
                async with self._registry_lock:
                    task.last_error = (
                        f"fire crashed: {type(exc).__name__}: {exc}"
                    )
                    task.last_run_at = now.isoformat()
                    task.next_run_at = next_run
        if due:
            await self._save()

    async def _fire(self, task: Task) -> None:
        """One fire cycle: gather → analyze → broadcast → record."""
        t0 = time.perf_counter()
        log.info("TASK fire id=%s topic=%r queries=%d",
                 task.id, task.topic, len(task.queries))

        # 1. Gather: dispatch the `recurring_research` Skill which runs
        #    every query sequentially. The Skill is `ok=True` even when
        #    every individual query fails (partial degradation matches
        #    the legacy try/except-per-query behavior). Per-query errors
        #    are surfaced through evidence[i]["error"].
        sk_result = await self._skill_registry.invoke(
            "recurring_research",
            self._skill_ctx,
            queries=task.queries,
            results_per_query=self._results_per_query,
        )
        gathered: list[tuple[str, list[dict]]] = []
        if sk_result.ok:
            for entry in sk_result.evidence:
                q_text = str(entry.get("query", ""))
                hits = entry.get("hits") or []
                # `hits` is list[dict] with {title,url,snippet} keys.
                if isinstance(hits, list):
                    gathered.append((q_text, list(hits)))
                else:
                    gathered.append((q_text, []))
                if entry.get("error"):
                    log.warning(
                        "TASK search failed id=%s query=%r error=%s",
                        task.id, q_text, entry.get("error"),
                    )
        else:
            log.warning(
                "TASK recurring_research dispatch failed id=%s error=%s",
                task.id, sk_result.error or "?",
            )
            gathered = [(q, []) for q in task.queries]

        # 2. Render brief for the SLM. Each query gets a section so the
        #    model can cross-reference (e.g. "the price article and the
        #    news article disagree").
        brief_parts: list[str] = []
        any_hit = False
        for q, hits in gathered:
            brief_parts.append(f"### Query: {q}")
            if not hits:
                brief_parts.append("(no results)")
                continue
            any_hit = True
            for h in hits:
                title = h.get("title") or "(untitled)"
                snippet = h.get("snippet") or "(no snippet)"
                url = h.get("url") or ""
                brief_parts.append(f"- **{title}** — {snippet}\n  {url}")
        brief = "\n".join(brief_parts)

        # 3. Analyze: hand the brief + the original directive to Brain.
        #    `synthesize_task_report` wraps the existing proactive_thought
        #    pathway so identity / mood / positive-filter all apply.
        if any_hit:
            try:
                report = await self._brain.synthesize_task_report(
                    topic=task.topic,
                    description=task.description,
                    brief=brief,
                )
            except Exception:
                log.exception("TASK synthesize failed id=%s", task.id)
                report = (
                    f"Scheduled task **{task.topic}** — synthesis failed. "
                    f"Raw findings:\n\n{brief[:1500]}"
                )
        else:
            report = (
                f"Scheduled task **{task.topic}** — no fresh results from "
                f"any of {len(task.queries)} queries. Will retry next cycle."
            )

        # 4. Broadcast: push the report through EVERY bound deliver so
        #    the operator sees it on whichever channel they're on. A
        #    single failure (e.g. Telegram offline) does not block the
        #    other channels. If no delivers are bound at all, log a
        #    warning — task will continue firing.
        delivers = list(self._delivers.items())
        if not delivers:
            log.warning(
                "TASK no deliver callbacks bound id=%s — report dropped, "
                "task will continue firing.", task.id,
            )
        else:
            for origin, deliver in delivers:
                try:
                    await deliver(report)
                    log.info("TASK report broadcast id=%s → %s OK", task.id, origin)
                except Exception:
                    log.exception("TASK deliver failed id=%s origin=%s",
                                  task.id, origin)

        # 5. Record + reschedule. next_run_at = now + interval (NOT
        #    last_run + interval) — this prevents drift from accumulating
        #    when fires are slow or skipped during sleep. Mutate Task
        #    fields under the registry lock so concurrent list_tasks /
        #    cancel calls from the dashboard or connectors never observe
        #    a torn snapshot (e.g. fire_count incremented but
        #    next_run_at not yet updated).
        now = self._now()
        next_run = (now + timedelta(seconds=task.interval_seconds)).isoformat()
        async with self._registry_lock:
            task.last_run_at = now.isoformat()
            task.next_run_at = next_run
            task.last_report = report
            task.last_error = None
            task.fire_count += 1
        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        log.info(
            "TASK done id=%s topic=%r elapsed_ms=%d report_len=%d next=%s",
            task.id, task.topic, elapsed_ms, len(report), task.next_run_at,
        )

    async def _save(self) -> None:
        """Atomic-ish YAML dump of all tasks. Held under `_save_lock` so
        two concurrent fires never interleave writes.
        """
        async with self._save_lock:
            payload = [asdict(task) for task in self._registry.values()]
            tmp = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
            try:
                tmp.write_text(
                    yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
                    encoding="utf-8",
                )
                tmp.replace(self._state_path)
            except Exception:
                log.exception("Failed to persist tasks to %s.", self._state_path)

    # ---------- time helpers --------------------------------------------------

    def _now(self) -> datetime:
        return datetime.now(tz=self._tz)

    def _is_duty_window(self, now: datetime) -> bool:
        """Mirror of `Heartbeat._is_duty_window` — True iff `now` is in
        `[duty_start, sleep_start)` modulo midnight.

        With the default config (`duty_start=06:00, sleep_start=02:00`)
        the duty window WRAPS midnight: 06:00..23:59 + 00:00..02:00.
        Tasks pause during the complement (sleep) window.

        IMPORTANT: this MUST match heartbeat's logic exactly. A previous
        version of this function used the inverted wrap case and caused
        tasks to fire only during sleep — see git history for the bug.
        """
        t = now.time()
        if self._duty_start < self._sleep_start:
            # No wrap, e.g. duty 06:00 → sleep 22:00 same day.
            return self._duty_start <= t < self._sleep_start
        # Wrap-around (default config: duty 06:00 → sleep 02:00 next day).
        return t >= self._duty_start or t < self._sleep_start


# ---------- module-private helpers -----------------------------------------


def _parse_hhmm(s: str) -> dt_time:
    """Parse "HH:MM" → datetime.time. Mirrors heartbeat._parse_hhmm."""
    h, m = s.split(":")
    return dt_time(int(h), int(m))


def _short_id() -> str:
    """Short opaque task ID (e.g. "t8f3a"). Operator-friendly enough to
    quote back via /cancel without copy-paste pain.
    """
    return "t" + secrets.token_hex(2)
