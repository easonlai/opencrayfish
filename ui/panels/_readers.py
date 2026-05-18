"""ui.panels._readers — pure data-loading helpers for the dashboard panels.

Every function in this module is READ-ONLY and side-effect free: it
loads on-disk state and returns plain Python values (dicts, lists,
strings). Panels import what they need from here so the rendering code
stays focused on Streamlit calls rather than file parsing.

Failure mode: every helper returns an empty / sentinel value (``[]``,
``None``, ``""``) when its source file is missing or unparseable.
NEVER raises. The dashboard must keep rendering even when half the
state files don't exist yet (first boot, pre-rotation cutover).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from ._paths import (
    AGENT_LOG,
    PROACTIVE_FEED,
    ROTATED_FILENAME_RE,
    SKILLS_FEED,
    SKILLS_FILE,
    SOUL_CODENAME_RE,
    SOUL_DESIGNATION_RE,
    SOUL_FILE,
    STATE_FILE,
    TASKS_FILE,
    TOOLS_FILE,
    VITALS_EVENTS_FEED,
)

# ---------------------------------------------------------------------------
# Rotated-JSONL fan-out
# ---------------------------------------------------------------------------

def rotated_jsonl_paths(base_path: Path) -> list[Path]:
    """Return every file the rotating writer might be feeding from this base.

    Newest-first. Includes:
      * ``<stem>-YYYY-MM-DD.jsonl`` rotated siblings (the current scheme), and
      * ``base_path`` itself if it exists (legacy un-rotated file from
        deployments that pre-date the rotation cutover).

    Mirrors ``core.jsonl_writer.RotatingJsonlWriter.sibling_paths()`` so the
    dashboard sees exactly what the writer produces — without depending
    on the live writer instance (separate process).
    """
    if base_path.name.endswith(".jsonl"):
        stem = base_path.name[: -len(".jsonl")]
    else:
        stem = base_path.name
    parent = base_path.parent
    out: list[tuple[str, Path]] = []
    if parent.exists():
        for p in parent.iterdir():
            if not p.is_file():
                continue
            m = ROTATED_FILENAME_RE.match(p.name)
            if not m or m.group("base") != stem:
                continue
            out.append((m.group("date"), p))
    out.sort(key=lambda t: t[0])
    paths = [p for _, p in out]
    if base_path.exists() and base_path not in paths:
        paths.append(base_path)
    return paths


def rotated_jsonl_tail(base_path: Path, limit: int) -> list[dict]:
    """Return the most recent ``limit`` JSONL records across all rotated
    siblings of ``base_path``. Chronological order (newest LAST).
    """
    paths = rotated_jsonl_paths(base_path)
    if not paths:
        return []
    collected: list[str] = []
    for p in reversed(paths):
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        collected = lines + collected
        if len(collected) >= limit:
            break
    if limit > 0:
        collected = collected[-limit:]
    out: list[dict] = []
    for ln in collected:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def rotated_jsonl_all(base_path: Path) -> list[dict]:
    """Return every parseable record across all rotated siblings.

    Chronological order (oldest first). Used by callers that need a
    timestamp-bounded slice (e.g. last-24h) rather than a tail count.
    """
    out: list[dict] = []
    for p in rotated_jsonl_paths(base_path):
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    return out


# ---------------------------------------------------------------------------
# Snapshot files
# ---------------------------------------------------------------------------

def read_state() -> dict | None:
    """Load the live heartbeat snapshot at ``state/vitals.json``."""
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def read_soul_identity() -> tuple[str | None, str | None]:
    """Return ``(designation, codename)`` parsed from soul.md.

    NOTE: as of the config-driven rename refactor, soul.md no longer
    carries a ``**Designation**:`` line — the agent's name is configured
    in ``config.yaml`` under ``system.individual_designation`` and
    injected by SoulHandler at runtime. This helper still tries to read
    it (in case an operator left a literal line in soul.md as a
    comment/legacy), but the dashboard's display path falls back to the
    live ``state.designation`` field (which comes from the running
    agent's config) when this returns None.
    """
    if not SOUL_FILE.exists():
        return None, None
    text = SOUL_FILE.read_text(encoding="utf-8")
    desig = None
    code = None
    if (m := SOUL_DESIGNATION_RE.search(text)):
        desig = m.group("value").strip().strip("*_`")
    if (m := SOUL_CODENAME_RE.search(text)):
        code = m.group("value").strip().strip("*_`")
    return desig or None, code or None


# ---------------------------------------------------------------------------
# Log tails (agent.log filter helpers)
# ---------------------------------------------------------------------------

def read_chat_log_tail(lines: int = 30) -> list[str]:
    """Tail recent agent-activity events from the Python rotating log.

    The brain emits a structured ``CHAT <event> key=value`` trail for
    every live-chat turn (entry/empathy/ltm/search/exit + timings). The
    connectors emit ``TG ...`` and ``WEB ...`` per-message events. The
    scheduler emits ``TASK ...`` per fire and the tool registry emits
    ``TOOL ...`` per call. Surfacing all of them lets the operator
    audit the whole agent surface from the dashboard.
    """
    if not AGENT_LOG.exists():
        return []
    try:
        all_lines = AGENT_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    _prefixes = (" CHAT ", " TG ", " WEB ", " TASK ", " TOOL ", " SKILL ")
    chat_only = [ln for ln in all_lines if any(p in ln for p in _prefixes)]
    return chat_only[-lines:]


def read_log_tail(log_dir: Path, lines: int = 40, *, tz: str | None = None) -> list[str]:
    """Tail the heartbeat log for the agent's *current* day.

    The file name is computed in the agent's configured timezone (matches
    ``core.heartbeat._append_log``); without this the dashboard reads the
    wrong file when its host TZ differs from the agent TZ near midnight.
    """
    if not log_dir.exists():
        return []
    now = datetime.now(tz=ZoneInfo(tz)) if tz else datetime.now()
    today = now.date().isoformat()
    log_file = log_dir / f"{today}.log"
    if not log_file.exists():
        return []
    return log_file.read_text(encoding="utf-8").splitlines()[-lines:]


def read_archive_tail(path: Path, max_chars: int = 2000) -> str:
    if not path.exists():
        return "(archive is empty — Sleep Metabolism hasn't run yet)"
    return path.read_text(encoding="utf-8")[-max_chars:]


def read_mood_log_tail(lines: int = 20) -> list[str]:
    """Tail recent ``MOOD ...`` events from the rotating chat log.

    These are emitted by ``core.emotions.Emotions.nudge_many`` (with a
    ``source`` label) and ``decay()`` (only on transitions). Surfacing
    them lets the operator trace WHY the mood vector moved.
    """
    if not AGENT_LOG.exists():
        return []
    try:
        all_lines = AGENT_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    mood_only = [ln for ln in all_lines if " MOOD " in ln]
    return mood_only[-lines:]


def read_errors_warnings_tail(lines: int = 20) -> list[str]:
    """Tail recent ERROR / WARNING / CRITICAL lines from agent.log.

    The chat-activity panel filters by structured event prefixes
    (``CHAT``, ``TG``, ``WEB``, ``TASK``, ``TOOL``, ``SKILL``) and
    consequently hides raw Python warnings + stack traces. This helper
    surfaces THOSE so an operator can spot a failing dependency, a
    tripped circuit breaker, or a soul-protection error without
    ``tail -f``ing the file.
    """
    if not AGENT_LOG.exists():
        return []
    try:
        all_lines = AGENT_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    levels = ("[ERROR]", "[WARNING]", "[CRITICAL]")
    flagged = [ln for ln in all_lines if any(lvl in ln for lvl in levels)]
    return flagged[-lines:]


# ---------------------------------------------------------------------------
# Feeds
# ---------------------------------------------------------------------------

def read_proactive_feed(limit: int = 5) -> list[dict]:
    """Newest-first list of recent autonomous-learning events."""
    if not PROACTIVE_FEED.exists():
        return []
    out: list[dict] = []
    for ln in PROACTIVE_FEED.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return list(reversed(out))


def read_reflection_feed(limit: int = 8) -> list[dict]:
    """Newest-first tail of recent self-reflection critiques."""
    from ._paths import REFLECTION_FEED
    records = rotated_jsonl_tail(REFLECTION_FEED, limit)
    return list(reversed(records))


def read_deliberation_feed(limit: int = 5) -> list[dict]:
    """Newest-first list of recent cognitive-loop deliberations."""
    from ._paths import DELIBERATION_FEED
    records = rotated_jsonl_tail(DELIBERATION_FEED, limit)
    return list(reversed(records))


def read_vitals_events(limit: int = 30) -> list[dict]:
    """Newest-first list of recent stress ENTER/EXIT events."""
    if not VITALS_EVENTS_FEED.exists():
        return []
    out: list[dict] = []
    for ln in VITALS_EVENTS_FEED.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return list(reversed(out))


def read_tasks() -> list[dict]:
    """Load the recurring-task registry from ``state/tasks.yaml``.

    Returns plain dicts so the dashboard doesn't depend on the live
    ``TaskScheduler`` class. Empty list when the file is missing,
    malformed, or the scheduler is disabled.
    """
    if not TASKS_FILE.exists():
        return []
    try:
        raw = yaml.safe_load(TASKS_FILE.read_text(encoding="utf-8")) or []
    except Exception:
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for entry in raw:
        if isinstance(entry, dict) and entry.get("id"):
            out.append(entry)
    return out


def read_tools_inventory() -> list[dict]:
    """Load the registered-tool snapshot from ``state/tools.json``."""
    if not TOOLS_FILE.exists():
        return []
    try:
        payload = json.loads(TOOLS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    tools = payload.get("tools") if isinstance(payload, dict) else None
    return tools if isinstance(tools, list) else []


def read_skills_inventory() -> list[dict]:
    """Load the registered-skill snapshot from ``state/skills.json``."""
    if not SKILLS_FILE.exists():
        return []
    try:
        payload = json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    skills = payload.get("skills") if isinstance(payload, dict) else None
    return skills if isinstance(skills, list) else []


def read_skills_activity(limit: int = 20) -> list[dict]:
    """Tail recent ``SkillRegistry.invoke()`` audit entries (newest-first)."""
    records = rotated_jsonl_tail(SKILLS_FEED, limit)
    return list(reversed(records))


def read_reflections_since(cutoff: datetime) -> list[dict]:
    """Mirror of ``ReflectionEngine.read_recent`` so the dashboard can
    preview LEARNED_PREFERENCES promotions over the SAME 24 h window
    that ``core.heartbeat._consolidate_reflections`` uses at 02:00.
    """
    from ._paths import REFLECTION_FEED
    out: list[dict] = []
    for d in rotated_jsonl_all(REFLECTION_FEED):
        try:
            ts = datetime.fromisoformat(d["ts"])
        except (KeyError, ValueError, TypeError):
            continue
        if ts < cutoff:
            continue
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def proactive_source_badge(source: str) -> str:
    """Inline badge showing how a proactive topic was chosen."""
    return {
        "stm_gap": "`🧩 stm-gap`",
        "learned_preference": "`💭 preference`",
        "manual": "`🎛️ manual`",
        "skipped": "`⏭️ skipped`",
    }.get(source or "", "")


def verb_badge(verb: str) -> str:
    """Inline icon for a PLAN verb so the operator can scan plans visually."""
    return {
        "SEARCH": "🌐 SEARCH",
        "RECALL": "📚 RECALL",
        "ANSWER": "💭 ANSWER",
    }.get(verb or "", verb or "?")


def format_task_interval(seconds: int) -> str:
    """Human-readable interval — mirrors ``core.scheduler.format_interval``
    so the dashboard renders the same labels operators see in chat.
    """
    seconds = int(seconds or 0)
    if seconds <= 0:
        return "?"
    if seconds % 86400 == 0:
        n = seconds // 86400
        return f"{n} day" if n == 1 else f"{n} days"
    if seconds % 3600 == 0:
        n = seconds // 3600
        return f"{n} hour" if n == 1 else f"{n} hours"
    if seconds % 60 == 0:
        n = seconds // 60
        return f"{n} minute" if n == 1 else f"{n} minutes"
    return f"{seconds}s"


def humanize(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    return str(timedelta(seconds=seconds)).split(".")[0]


def staleness(state: dict) -> tuple[str, str]:
    """Return ``(status_label, color)`` based on how recently the state was written."""
    try:
        last = datetime.fromisoformat(state["now"])
    except (KeyError, ValueError):
        return "UNKNOWN", "gray"
    age = (datetime.now(tz=last.tzinfo) - last).total_seconds()
    if age < 90:
        return "ALIVE", "green"
    if age < 300:
        return "STALE", "orange"
    return "DEAD", "red"
