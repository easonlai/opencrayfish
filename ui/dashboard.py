"""ui.dashboard — Live Vital Signs Dashboard for OpenCrayFish.

Run with:  streamlit run ui/dashboard.py

Reads `state/vitals.json` (published every pulse by `core.heartbeat`),
`logs/daily/<today>.log`, `memory/archive.md`, and `soul.md`. Pure read-only.
Auto-refreshes every 5 seconds via the `streamlit-autorefresh` component
(non-blocking; falls back to a manual reload button when the component
isn’t installed).
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# Streamlit launches with ui/ as the script dir; add the repo root so
# `from core...` and `from tools...` resolve.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd  # noqa: E402  (Streamlit ships with pandas)
import streamlit as st  # noqa: E402
import yaml  # noqa: E402  (already a runtime dep — see requirements.txt)

from core.config import Config  # noqa: E402

# Optional dep — same pattern used by `ui/web_chat.py`. When present we
# use it to schedule a non-blocking rerun on a fixed cadence; when not,
# the dashboard renders a manual refresh button so the operator still
# has a way forward without `pip install`-ing anything new.
try:
    from streamlit_autorefresh import st_autorefresh  # type: ignore
    _AUTOREFRESH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _AUTOREFRESH_AVAILABLE = False

REFRESH_SECONDS = 5
STATE_FILE = _REPO_ROOT / "state" / "vitals.json"
PROACTIVE_FEED = _REPO_ROOT / "state" / "proactive.jsonl"
REFLECTION_FEED = _REPO_ROOT / "state" / "reflection.jsonl"
DELIBERATION_FEED = _REPO_ROOT / "state" / "deliberation.jsonl"
VITALS_EVENTS_FEED = _REPO_ROOT / "state" / "vitals_events.jsonl"
# Recurring research-task scheduler state. Written atomically by
# core.scheduler.TaskScheduler._save() — read-only here.
TASKS_FILE = _REPO_ROOT / "state" / "tasks.yaml"
# Tool registry inventory snapshot. Published once at boot by main.py
# after registering each Tool with ToolRegistry.
TOOLS_FILE = _REPO_ROOT / "state" / "tools.json"
# Skill registry inventory + per-invocation audit feed. Published by
# main.py after registering each Skill with SkillRegistry; the feed
# is appended by SkillRegistry.invoke() on every call.
SKILLS_FILE = _REPO_ROOT / "state" / "skills.json"
SKILLS_FEED = _REPO_ROOT / "state" / "skills.jsonl"
SOUL_FILE = _REPO_ROOT / "soul.md"
# Python-logging rotating file (set up in main.py). Holds the new
# `CHAT <event> ...` live-chat trail emitted from core.brain + connectors.
AGENT_LOG = _REPO_ROOT / "state" / "logs" / "agent.log"

# JSONL rotation discovery — mirrors the filename pattern produced by
# `core.jsonl_writer.RotatingJsonlWriter` (`<stem>-YYYY-MM-DD.jsonl`).
# The base paths above point to the LEGACY un-rotated filenames; the
# helpers below transparently fan out to every rotated sibling so the
# dashboard sees the same data the writers actually produce.
_ROTATED_FILENAME_RE = re.compile(
    r"^(?P<base>.+)-(?P<date>\d{4}-\d{2}-\d{2})\.jsonl$"
)

# Mirrors `_DESIGNATION_RE` in core.brain so the dashboard shows the same
# name the agent uses when it introduces itself in chat.
_SOUL_DESIGNATION_RE = re.compile(
    r"\*\*Designation\*\*\s*:\s*(?P<value>.+)", re.IGNORECASE
)
_SOUL_CODENAME_RE = re.compile(
    r"\*\*Codename\*\*\s*:\s*(?P<value>.+)", re.IGNORECASE
)


# ---------- helpers ----------------------------------------------------------

def _rotated_jsonl_paths(base_path: Path) -> list[Path]:
    """Return every file the rotating writer might be feeding from this base.

    Newest-first. Includes:
      * `<stem>-YYYY-MM-DD.jsonl` rotated siblings (the current scheme), and
      * `base_path` itself if it exists (legacy un-rotated file from
        deployments that pre-date the rotation cutover).

    Mirrors `core.jsonl_writer.RotatingJsonlWriter.sibling_paths()` so the
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
            m = _ROTATED_FILENAME_RE.match(p.name)
            if not m or m.group("base") != stem:
                continue
            out.append((m.group("date"), p))
    # Sort newest-last so the concatenated tail keeps chronological order.
    out.sort(key=lambda t: t[0])
    paths = [p for _, p in out]
    # Append the legacy un-rotated path last so its lines (if any) read as
    # the most recent — covers operators who tail the old name and don't
    # want their existing data to disappear after upgrading.
    if base_path.exists() and base_path not in paths:
        paths.append(base_path)
    return paths


def _rotated_jsonl_tail(base_path: Path, limit: int) -> list[dict]:
    """Return the most recent `limit` JSONL records across all rotated
    siblings of `base_path`. Chronological order (newest LAST).

    Walks rotated files newest-last, accumulates lines from the end, and
    stops once we have at least `limit`. Cheap because we only read whole
    files (jsonl rows are small) — no seek/tail tricks needed for the
    sizes these feeds produce on a Pi 5.
    """
    paths = _rotated_jsonl_paths(base_path)
    if not paths:
        return []
    collected: list[str] = []
    # Walk from newest backwards so we can stop early once we have `limit`.
    for p in reversed(paths):
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        # Prepend this file's lines so chronological order is preserved
        # in the final list (older files before newer ones).
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


def _rotated_jsonl_all(base_path: Path) -> list[dict]:
    """Return every parseable record across all rotated siblings.

    Chronological order (oldest first). Used by callers that need a
    timestamp-bounded slice (e.g. last-24h) rather than a tail count.
    """
    out: list[dict] = []
    for p in _rotated_jsonl_paths(base_path):
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


def _read_state() -> dict | None:
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _read_soul_identity() -> tuple[str | None, str | None]:
    """Return (designation, codename) parsed from soul.md.

    NOTE: as of the config-driven rename refactor, soul.md no longer carries
    a `**Designation**:` line — the agent's name is configured in
    `config.yaml` under `system.individual_designation` and injected by
    SoulHandler at runtime. This helper still tries to read it (in case an
    operator left a literal line in soul.md as a comment/legacy), but the
    dashboard's display path falls back to the live `state.designation`
    field (which comes from the running agent's config) when this returns
    None — see `designation = soul_designation or designation_state` below.
    """
    if not SOUL_FILE.exists():
        return None, None
    text = SOUL_FILE.read_text(encoding="utf-8")
    desig = None
    code = None
    if (m := _SOUL_DESIGNATION_RE.search(text)):
        desig = m.group("value").strip().strip("*_`")
    if (m := _SOUL_CODENAME_RE.search(text)):
        code = m.group("value").strip().strip("*_`")
    return desig or None, code or None


def _read_chat_log_tail(lines: int = 30) -> list[str]:
    """Tail recent agent-activity events from the Python rotating log.

    The brain emits a structured `CHAT <event> key=value` trail for every
    live-chat turn (entry/empathy/ltm/search/exit + timings). The
    connectors emit `TG ...` and `WEB ...` per-message events. The
    scheduler emits `TASK ...` per fire and the tool registry emits
    `TOOL ...` per call. Surfacing all of them here is the only way the
    operator can audit the whole agent surface from the dashboard — the
    heartbeat's `logs/daily/*.log` only carries pulse-level events.
    """
    if not AGENT_LOG.exists():
        return []
    try:
        all_lines = AGENT_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    # Match any of the structured event prefixes. Keep this list in sync
    # with the `log.info("<PREFIX> ...")` strings in core / connectors / tools.
    _prefixes = (" CHAT ", " TG ", " WEB ", " TASK ", " TOOL ", " SKILL ")
    chat_only = [
        ln for ln in all_lines if any(p in ln for p in _prefixes)
    ]
    return chat_only[-lines:]


def _read_log_tail(log_dir: Path, lines: int = 40, *, tz: str | None = None) -> list[str]:
    """Tail the heartbeat log for the agent's *current* day.

    The file name is computed in the agent's configured timezone (matches
    `core.heartbeat._append_log`); without this the dashboard reads the
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


def _read_archive_tail(path: Path, max_chars: int = 2000) -> str:
    if not path.exists():
        return "(archive is empty — Sleep Metabolism hasn't run yet)"
    return path.read_text(encoding="utf-8")[-max_chars:]


def _read_proactive_feed(limit: int = 5) -> list[dict]:
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


def _proactive_source_badge(source: str) -> str:
    """Render a small inline badge showing how a proactive topic was chosen.

    Sources map to icons so the operator can see at a glance whether the
    agent is closing real conversation gaps (`stm_gap`) or just revisiting
    long-term interests (`learned_preference`).
    """
    return {
        "stm_gap": "`🧩 stm-gap`",
        "learned_preference": "`💭 preference`",
        "manual": "`🎛️ manual`",
        "skipped": "`⏭️ skipped`",
    }.get(source or "", "")


def _read_reflection_feed(limit: int = 8) -> list[dict]:
    """Newest-first tail of recent self-reflection critiques.

    Reads every `state/reflection-YYYY-MM-DD.jsonl` rotated sibling plus
    the legacy `state/reflection.jsonl` (if present) so the dashboard
    stays accurate across midnight + the rotation cutover.
    """
    records = _rotated_jsonl_tail(REFLECTION_FEED, limit)
    return list(reversed(records))


def _read_deliberation_feed(limit: int = 5) -> list[dict]:
    """Newest-first list of recent cognitive-loop deliberations.

    Each entry captures one full THINK → PLAN → ACT → (REFINE) cycle:
    intent, sub-questions, plan steps with verbs, evidence summaries,
    refine decisions, and total latency. Lets the operator see WHY the
    agent answered the way it did, not just WHAT it said.
    """
    records = _rotated_jsonl_tail(DELIBERATION_FEED, limit)
    return list(reversed(records))


def _verb_badge(verb: str) -> str:
    """Inline icon for a PLAN verb so the operator can scan plans visually."""
    return {
        "SEARCH": "🌐 SEARCH",
        "RECALL": "📚 RECALL",
        "ANSWER": "💭 ANSWER",
    }.get(verb or "", verb or "?")


def _read_vitals_events(limit: int = 30) -> list[dict]:
    """Newest-first list of recent stress ENTER/EXIT events.

    Each event is one transition of `vitals.is_stressed`. The dashboard
    uses this for a chronological stress timeline so the operator can see
    when the agent was hot, for how long, and what the peak readings were.
    """
    if not VITALS_EVENTS_FEED.exists():
        return []
    out: list[dict] = []
    for ln in VITALS_EVENTS_FEED.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return list(reversed(out))


def _read_mood_log_tail(lines: int = 20) -> list[str]:
    """Tail recent `MOOD ...` events from the rotating chat log.

    These are emitted by `core.emotions.Emotions.nudge_many` (with a
    `source` label) and `decay()` (only on transitions). Surfacing them
    lets the operator trace WHY the mood vector moved.
    """
    if not AGENT_LOG.exists():
        return []
    try:
        all_lines = AGENT_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    mood_only = [ln for ln in all_lines if " MOOD " in ln]
    return mood_only[-lines:]


def _read_errors_warnings_tail(lines: int = 20) -> list[str]:
    """Tail recent ERROR / WARNING log lines from `state/logs/agent.log`.

    The chat-activity panel filters by structured event prefixes
    (`CHAT`, `TG`, `WEB`, `TASK`, `TOOL`, `SKILL`) and consequently hides
    raw Python warnings + stack traces. This helper surfaces THOSE so an
    operator can spot a failing dependency, a tripped circuit breaker,
    or a soul-protection error without `tail -f`ing the file.
    """
    if not AGENT_LOG.exists():
        return []
    try:
        all_lines = AGENT_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    # Match the standard logging format used in main.py:
    #   "<ts> [<LEVEL>] <name>: <message>"
    levels = ("[ERROR]", "[WARNING]", "[CRITICAL]")
    flagged = [ln for ln in all_lines if any(lvl in ln for lvl in levels)]
    return flagged[-lines:]


def _read_tasks() -> list[dict]:
    """Load the recurring-task registry from `state/tasks.yaml`.

    Mirrors `core.scheduler.TaskScheduler.load`'s parse but returns plain
    dicts so the dashboard doesn't depend on the live class. Empty list
    when the file is missing, malformed, or the scheduler is disabled.
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


def _read_tools_inventory() -> list[dict]:
    """Load the registered-tool snapshot from `state/tools.json`.

    Published by `main.py::_publish_tools_inventory` after every Tool is
    registered with `ToolRegistry`. Empty when the agent has not booted
    yet (or the publish step failed — see agent.log).
    """
    if not TOOLS_FILE.exists():
        return []
    try:
        payload = json.loads(TOOLS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    tools = payload.get("tools") if isinstance(payload, dict) else None
    return tools if isinstance(tools, list) else []


def _read_skills_inventory() -> list[dict]:
    """Load the registered-skill snapshot from `state/skills.json`.

    Published by `main.py::_publish_skills_inventory` whenever the
    SkillRegistry changes (initial boot + any dynamic register/
    unregister). Empty list when the file is missing or unparseable.
    """
    if not SKILLS_FILE.exists():
        return []
    try:
        payload = json.loads(SKILLS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    skills = payload.get("skills") if isinstance(payload, dict) else None
    return skills if isinstance(skills, list) else []


def _read_skills_activity(limit: int = 20) -> list[dict]:
    """Tail recent SkillRegistry.invoke() audit entries (newest-first).

    Each JSONL line is one invocation: ts, skill, ok, latency_ms,
    tools_used, kwargs_keys, error[:200]. Surfaced in the Skills panel
    so operators can see WHICH skills the agent actually picked.
    """
    records = _rotated_jsonl_tail(SKILLS_FEED, limit)
    return list(reversed(records))


def _format_task_interval(seconds: int) -> str:
    """Human-readable interval — mirrors `core.scheduler.format_interval`
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


def _read_reflections_since(cutoff: datetime) -> list[dict]:
    """Mirror of `ReflectionEngine.read_recent` so the dashboard can preview
    LEARNED_PREFERENCES promotions over the SAME 24 h window that
    `core.heartbeat._consolidate_reflections` will use at 02:00.

    Walks every rotated `state/reflection-YYYY-MM-DD.jsonl` sibling so the
    window stays accurate across the previous-day boundary.
    """
    out: list[dict] = []
    for d in _rotated_jsonl_all(REFLECTION_FEED):
        try:
            ts = datetime.fromisoformat(d["ts"])
        except (KeyError, ValueError, TypeError):
            continue
        if ts < cutoff:
            continue
        out.append(d)
    return out


def _humanize(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    return str(timedelta(seconds=seconds)).split(".")[0]


def _staleness(state: dict) -> tuple[str, str]:
    """Return (status_label, color) based on how recently the state was written."""
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


# ---------- main view --------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title="OpenCrayFish — Vital Signs",
        page_icon="🐚",
        layout="wide",
    )

    cfg = Config.load(_REPO_ROOT / "config.yaml")

    # Schedule the next non-blocking rerun BEFORE we render anything.
    # The component returns the run-counter (unused) and triggers a
    # rerun every REFRESH_SECONDS without holding the Streamlit thread
    # in `time.sleep(...)` (which would block widget interaction and
    # skip frames on slow renders). Falls back to a manual button when
    # the optional dep isn't installed.
    if _AUTOREFRESH_AVAILABLE:
        st_autorefresh(interval=REFRESH_SECONDS * 1000, key="dashboard_refresh")
    state = _read_state()

    # ===== Header banner ======================================================
    if state is None:
        st.title("🐚 OpenCrayFish — Vital Signs")
        st.error(
            f"No state snapshot at `{STATE_FILE.relative_to(_REPO_ROOT)}`. "
            "Is `python main.py` running? The first pulse takes up to 30 s."
        )
        st.caption(
            f"Auto-refreshing every {REFRESH_SECONDS}s."
            if _AUTOREFRESH_AVAILABLE
            else (
                f"Auto-refresh component not installed — "
                f"`pip install streamlit-autorefresh` for live updates. "
                f"Use the button below to refresh manually."
            )
        )
        if not _AUTOREFRESH_AVAILABLE:
            st.button("🔄 Refresh now", on_click=st.rerun)
        return

    designation_state = state.get("designation", "")
    soul_designation, soul_codename = _read_soul_identity()
    # config.yaml `system.individual_designation` is the single source of
    # truth (published live via state["designation"]). soul.md may still
    # carry a legacy literal — use it only as a fallback if the heartbeat
    # state hasn't published a designation yet.
    designation = designation_state or soul_designation or "Unknown"
    status_label, status_color = _staleness(state)
    sleeping = state.get("is_sleeping", False)
    stressed = bool(state.get("vitals") and state["vitals"].get("stressed"))

    badges = [f":{status_color}[**{status_label}**]"]
    if sleeping:
        badges.append(":blue[**SLEEPING 💤**]")
    elif stressed:
        badges.append(":red[**STRESSED 🔥**]")
    else:
        badges.append(":green[**ON DUTY**]")
    # Brain (SLM) life sign — treat the inference link as a vital. When
    # the breaker is tripped this is the equivalent of a stroke for an
    # organic being: the body still pulses, but cognition is offline.
    brain_block = state.get("brain") or {}
    brain_online = bool(brain_block.get("online", True))
    brain_backend = brain_block.get("backend") or "unknown"
    if not brain_online:
        recovery = brain_block.get("recovery_seconds")
        recovery_hint = (
            f" — retry in ~{int(recovery)}s" if isinstance(recovery, (int, float)) and recovery > 0 else ""
        )
        badges.append(f":red[**🧠 BRAIN OFFLINE**{recovery_hint}]")
    else:
        badges.append(f":green[**🧠 brain online** `{brain_backend}`]")
    badges.append(
        f"Duty window: `{state.get('duty_window', '?')}` ({state.get('timezone', '?')})"
    )

    st.title(f"🐚 {designation} — Vital Signs")
    # Subtitle: codename from soul.md + per-instance designation from config
    # so the operator still sees WHICH deployment they're looking at.
    subtitle_bits: list[str] = []
    if soul_codename:
        subtitle_bits.append(f"Codename: **{soul_codename}**")
    if designation_state and designation_state != designation:
        subtitle_bits.append(f"Instance: `{designation_state}`")
    if subtitle_bits:
        st.caption(" · ".join(subtitle_bits))
    st.markdown(" · ".join(badges))

    # ===== Top metric row =====================================================
    vitals = state.get("vitals") or {}
    cols = st.columns(7)
    cols[0].metric("CPU", f"{vitals.get('cpu', 0):.0f}%" if vitals else "—")
    cols[1].metric("RAM", f"{vitals.get('ram', 0):.0f}%" if vitals else "—")
    temp = vitals.get("temp") if vitals else None
    cols[2].metric("Temp", f"{temp:.1f}°C" if temp is not None else "n/a")
    # Brain (SLM) availability metric. When offline, show backend name as
    # the delta so the operator knows WHICH endpoint to revive.
    brain_label = "🟢 online" if brain_online else "🔴 OFFLINE"
    brain_delta = brain_backend if brain_online else (
        brain_block.get("last_error") or "no inference"
    )
    cols[3].metric(
        "Brain",
        brain_label,
        delta=brain_delta,
        delta_color="off" if brain_online else "inverse",
    )
    cols[4].metric("Pulses", state.get("pulse_count", 0))
    cols[5].metric("Proactive", state.get("proactive_count", 0))
    cols[6].metric("Stress events", state.get("stress_count", 0))

    # ===== Idle countdown =====================================================
    idle = int(state.get("idle_seconds", 0))
    threshold = int(state.get("idle_threshold_seconds", 1800))
    progress = min(idle / threshold, 1.0) if threshold else 0
    st.markdown(
        f"**Idle for {_humanize(idle)}** of {_humanize(threshold)} "
        "until next autonomous research."
    )
    st.progress(progress)

    # ===== Two-column body ====================================================
    left, right = st.columns([3, 2])

    with left:
        st.subheader("Vitals over time (last ~1 hr)")
        history = state.get("history") or []
        if history:
            df = pd.DataFrame(history)
            df["ts"] = pd.to_datetime(df["ts"])
            df = df.set_index("ts")
            # Coerce to numeric — `temp` is None on hosts without a thermal
            # sensor (e.g. dev macOS), which makes pandas infer `object` dtype
            # and trips Streamlit's melter ("columns ... with mixed types").
            chart_cols = [c for c in ("cpu", "ram", "temp") if c in df.columns]
            chart_df = df[chart_cols].apply(pd.to_numeric, errors="coerce")
            chart_df = chart_df.dropna(axis=1, how="all")
            if not chart_df.empty and len(chart_df.columns):
                st.line_chart(chart_df, y=list(chart_df.columns))
            else:
                st.caption("No numeric vitals to chart yet.")
        else:
            st.caption("Building history… first chart appears after a few pulses.")

        st.subheader("Heartbeat log (today)")
        log_lines = _read_log_tail(
            _REPO_ROOT / cfg.memory.log_path,
            lines=50,
            tz=cfg.system.timezone,
        )
        if not log_lines:
            st.caption("No log entries yet.")
        else:
            st.markdown("```\n" + "\n".join(log_lines) + "\n```")
            highlights = [
                ln for ln in log_lines
                if "PROACTIVE" in ln
                or "Stress" in ln
                or "VITALS" in ln
                or "Sleep Metabolism" in ln
                or "Awakening" in ln
            ]
            with st.expander(f"Notable events only ({len(highlights)})"):
                if highlights:
                    for ln in highlights:
                        if "PROACTIVE" in ln:
                            st.markdown(f":violet[{ln}]")
                        elif "VITALS stress=ENTER" in ln or "Stress" in ln:
                            st.markdown(f":red[{ln}]")
                        elif "VITALS stress=EXIT" in ln:
                            st.markdown(f":green[{ln}]")
                        else:
                            st.markdown(f":blue[{ln}]")
                else:
                    st.caption("_(no notable events yet today)_")

        st.subheader("💬 Live chat activity (last 30 events)")
        st.caption(
            "Per-turn trail from `core.brain` + Telegram connector — "
            "`CHAT enter / empathy / ltm / search PATH=… / exit` and `TG msg / reply`. "
            "Source: `state/logs/agent.log`."
        )
        chat_lines = _read_chat_log_tail(lines=30)
        if not chat_lines:
            st.info(
                "No live-chat events yet. Send a Telegram message to populate. "
                "(Requires the new structured logging in core/brain.py and "
                "connectors/telegram.py.)"
            )
        else:
            # Compact stats from the most recent ~30 turns: how often did we
            # actually search vs. trust the SLM? Useful sanity-check after the
            # Plan A bias change.
            exits = [ln for ln in chat_lines if "CHAT exit" in ln]
            web_yes = sum(1 for ln in exits if "web_searched=True" in ln)
            web_no = sum(1 for ln in exits if "web_searched=False" in ln)
            no_search = sum(1 for ln in chat_lines if "decision=NO_SEARCH" in ln)
            search_yes = sum(1 for ln in chat_lines if "decision=SEARCH" in ln)
            short_circuit = sum(1 for ln in chat_lines if "PATH=ltm_short_circuit" in ln)
            failed = sum(1 for ln in chat_lines if "search FAILED" in ln)
            stat_cols = st.columns(5)
            stat_cols[0].metric("Turns", len(exits))
            stat_cols[1].metric("Web-grounded", web_yes, delta=f"-{web_no} skipped" if web_no else None)
            stat_cols[2].metric("Triage SEARCH", search_yes, delta=f"vs {no_search} NO" if no_search else None)
            stat_cols[3].metric("LTM short-circuit", short_circuit)
            stat_cols[4].metric("Search FAILED", failed, delta="errors" if failed else None)

            # Color the lines so the operator can spot regressions at a glance.
            rendered: list[str] = []
            for ln in chat_lines:
                if "CHAT exit" in ln:
                    rendered.append(f":green[{ln}]")
                elif (
                    "search FAILED" in ln
                    or "TASK fire failed" in ln
                    or "TASK deliver failed" in ln
                    or "TASK synthesize failed" in ln
                    or "TASK search failed" in ln
                    or ("TOOL call name" in ln and "status=fail" in ln)
                ):
                    rendered.append(f":red[{ln}]")
                elif "decision=NO_SEARCH" in ln:
                    rendered.append(f":orange[{ln}]")
                elif "decision=SEARCH" in ln or "PATH=explicit" in ln:
                    rendered.append(f":violet[{ln}]")
                elif "PATH=ltm_short_circuit" in ln:
                    rendered.append(f":blue[{ln}]")
                elif "TASK fire" in ln or "TASK done" in ln \
                        or "TASK report broadcast" in ln \
                        or "TASK added" in ln or "TASK updated" in ln \
                        or "TASK cancelled" in ln:
                    rendered.append(f":violet[{ln}]")
                elif "TOOL " in ln:
                    rendered.append(f":blue[{ln}]")
                elif "TG msg" in ln or "TG reply" in ln \
                        or "WEB msg" in ln or "WEB reply" in ln:
                    rendered.append(f":gray[{ln}]")
                else:
                    rendered.append(ln)
            st.markdown("\n\n".join(rendered))

        # ----- Errors & warnings (raw log filter) ---------------------------
        # Surfaces ERROR / WARNING / CRITICAL lines that the structured
        # chat filter above intentionally drops — provider timeouts, soul
        # protection rejections, tool exceptions, scheduler load failures.
        # This panel is hidden behind an expander so it's only "loud" when
        # something is actually wrong (count badge in the header).
        err_lines = _read_errors_warnings_tail(lines=20)
        err_count = len(err_lines)
        err_critical = sum(1 for ln in err_lines if "[CRITICAL]" in ln)
        err_error = sum(1 for ln in err_lines if "[ERROR]" in ln)
        err_warn = err_count - err_critical - err_error
        with st.expander(
            (
                f"⚠️ Errors & warnings (last 20 — "
                f"{err_critical} critical, {err_error} error, {err_warn} warn)"
            ),
            expanded=(err_critical > 0 or err_error > 0),
        ):
            st.caption(
                "Raw level-filtered tail of `state/logs/agent.log`. "
                "Empty = healthy. Use this to spot a tripped circuit "
                "breaker, a SearXNG outage, or a soul-protection rejection "
                "the structured chat panel would otherwise hide."
            )
            if not err_lines:
                st.success("No errors or warnings in the recent log tail.")
            else:
                for ln in err_lines:
                    if "[CRITICAL]" in ln:
                        st.markdown(f":red[**{ln}**]")
                    elif "[ERROR]" in ln:
                        st.markdown(f":red[{ln}]")
                    else:
                        st.markdown(f":orange[{ln}]")

    with right:
        st.subheader("Mood vector (5-D)")
        mood = state.get("mood") or {}
        if mood:
            mood_df = pd.DataFrame(
                {"channel": list(mood.keys()), "intensity": list(mood.values())}
            ).set_index("channel")
            st.bar_chart(mood_df, height=220)
            # Two-tier mood readout: dominant (almost always calm because of
            # baseline) + the genuinely active non-baseline channel which is
            # what actually colours the agent's behaviour right now.
            active_channel = state.get("mood_active_channel") or "none"
            active_intensity = float(state.get("mood_active_intensity") or 0.0)
            mood_emoji = {
                "joy": "😊", "anger": "😠", "sorrow": "😔",
                "excitement": "✨", "calm": "🟦", "none": "⚪️",
            }.get(active_channel, "")
            if active_channel != "none" and active_intensity >= 0.15:
                st.caption(
                    f"Dominant: **{state.get('mood_dominant', '?').title()}** · "
                    f"Active: {mood_emoji} **{active_channel.title()}** "
                    f"({active_intensity:.2f})"
                )
            else:
                st.caption(
                    f"Dominant: **{state.get('mood_dominant', '?').title()}** · "
                    f"Active: ⚪️ baseline (no strong stimulus)"
                )

            # Mood-over-time trajectory using the same history feed as vitals.
            # We need at least 3 samples for a meaningful line chart.
            mood_history_cols = [
                "mood_joy", "mood_anger", "mood_sorrow",
                "mood_excitement", "mood_calm",
            ]
            history = state.get("history") or []
            if (
                len(history) >= 3
                and any(c in (history[-1] or {}) for c in mood_history_cols)
            ):
                mh_df = pd.DataFrame(history)
                mh_df["ts"] = pd.to_datetime(mh_df["ts"])
                mh_df = mh_df.set_index("ts")
                present = [c for c in mood_history_cols if c in mh_df.columns]
                mh_chart = mh_df[present].apply(pd.to_numeric, errors="coerce")
                # Friendly names on the chart.
                mh_chart = mh_chart.rename(
                    columns={
                        "mood_joy": "joy", "mood_anger": "anger",
                        "mood_sorrow": "sorrow", "mood_excitement": "excitement",
                        "mood_calm": "calm",
                    }
                )
                st.line_chart(mh_chart, height=180)
                st.caption("Mood trajectory (last ~1 hr)")
        else:
            st.caption("Mood not yet sampled.")

        # ===== Vitals stress timeline ======================================
        st.subheader("⚡ Vitals stress events")
        events = _read_vitals_events(limit=10)
        active_now = bool(state.get("stress_active"))
        if active_now:
            started = state.get("stress_started_at")
            st.error(
                f"🔥 Currently STRESSED since {started or '?'} — "
                "EXHAUSTION DIRECTIVE active, cognitive loop bypassed."
            )
        if not events:
            st.caption("No stress transitions recorded yet. ✅")
        else:
            for ev in events:
                ts = ev.get("ts", "?")
                kind = ev.get("kind", "?")
                if kind == "stress_enter":
                    temp = ev.get("temp")
                    ram = ev.get("ram")
                    temp_s = f"{temp:.1f}°C" if temp is not None else "n/a"
                    ram_s = f"{ram:.1f}%" if ram is not None else "n/a"
                    st.markdown(
                        f":red[**🔥 ENTER**] `{ts}` — "
                        f"temp={temp_s}, ram={ram_s}"
                    )
                elif kind == "stress_exit":
                    dur = int(ev.get("duration_s") or 0)
                    peak_t = ev.get("peak_temp")
                    peak_r = ev.get("peak_ram") or 0.0
                    peak_t_s = f"{peak_t:.1f}°C" if peak_t is not None else "n/a"
                    st.markdown(
                        f":green[**✅ EXIT**] `{ts}` — "
                        f"duration={dur}s, peak temp={peak_t_s}, peak ram={peak_r:.1f}%"
                    )
                else:
                    st.markdown(f"`{ts}` — {kind}")

        # ===== Mood event log =============================================
        st.subheader("🧬 Mood event log (last 20)")
        st.caption(
            "Atomic mood movements traced back to their cause "
            "(empathy_*, vitals_stress, …). Source: `state/logs/agent.log`."
        )
        mood_lines = _read_mood_log_tail(lines=20)
        if not mood_lines:
            st.caption("No mood events yet — talk to the agent or wait for a stress cycle.")
        else:
            rendered = []
            for ln in mood_lines:
                if "source=vitals_stress" in ln:
                    rendered.append(f":red[{ln}]")
                elif "source=empathy_negative" in ln:
                    rendered.append(f":orange[{ln}]")
                elif "source=empathy_positive" in ln:
                    rendered.append(f":green[{ln}]")
                elif "source=empathy_mixed" in ln:
                    rendered.append(f":violet[{ln}]")
                elif "source=empathy_urgent" in ln:
                    rendered.append(f":blue[{ln}]")
                elif "MOOD transition" in ln:
                    rendered.append(f":gray[{ln}]")
                else:
                    rendered.append(ln)
            st.markdown("\n\n".join(rendered))

        st.subheader("Short-Term Memory")
        st.metric(
            "Conversation turns held",
            f"{state.get('stm_size', 0)} / {state.get('stm_max', 0)}",
        )
        pending = state.get("stm_pending", 0)
        if pending:
            st.caption(
                f"✍️ {pending} turn(s) buffered in RAM — will flush to disk on next idle window."
            )
        st.caption("Cleared nightly during Sleep Metabolism.")

        st.subheader("Last autonomous research")
        topic = state.get("last_proactive_topic")
        if topic:
            source = state.get("last_proactive_source") or ""
            badge = _proactive_source_badge(source)
            st.markdown(f"**Topic:** {topic}  {badge}")
            st.caption(f"At: {state.get('last_proactive_at', '?')}")
        else:
            st.caption(
                "None yet — fires after 30 min idle. Source is chosen by "
                "the two-stage selector (STM gap → Learned Preference)."
            )

    # ===== Autonomous learning feed (proof of internet activity) =============
    st.divider()
    st.subheader("🔬 Autonomous learning feed (last 5)")
    st.caption(
        "Each entry is a permanent record from `state/proactive.jsonl`. "
        "Trigger one immediately from Telegram with `/research [optional topic]`."
    )
    events = _read_proactive_feed(limit=5)
    if not events:
        st.info(
            "No autonomous research yet. Either wait until idle > 30 min, "
            "or send `/research` in Telegram to verify the pipeline now."
        )
    else:
        for ev in events:
            tag = " · *manual*" if ev.get("manual") else ""
            badge = _proactive_source_badge(ev.get("source") or "")
            label = f"📡 {ev.get('ts', '?')} — {ev.get('topic', '?')}{tag} {badge}".rstrip()
            with st.expander(label, expanded=(ev is events[0])):
                decisions = ev.get("triage_decisions") or []
                if decisions:
                    st.markdown(f"**Triage trail ({len(decisions)} candidate(s)):**")
                    for d in decisions:
                        verdict = d.get("verdict", "?")
                        emoji = {
                            "unknown": "🔍",
                            "known_by_slm": "✅",
                            "in_ltm": "📚",
                        }.get(verdict, "❔")
                        st.caption(f"{emoji} `{verdict}` — {d.get('topic', '?')}")
                hits = ev.get("hits") or []
                st.markdown(f"**Web hits ({len(hits)}):**")
                if not hits:
                    st.caption("_(SearXNG returned no results)_")
                for h in hits:
                    title = h.get("title") or h.get("url") or "(untitled)"
                    url = h.get("url", "")
                    if url:
                        st.markdown(f"- [{title}]({url})")
                    else:
                        st.markdown(f"- {title}")
                    snip = h.get("snippet")
                    if snip:
                        st.caption(snip)
                st.markdown("**Reflection:**")
                st.write(ev.get("reflection", "_(missing)_"))
                # Surface the REFINE pass so the operator can see when the
                # agent caught its own hallucinations on idle reflections.
                refine_verdict = (ev.get("refine_verdict") or "").upper()
                if refine_verdict == "REWRITE":
                    st.success(
                        "🛠️ REFINE rewrote this reflection — the original draft "
                        "made claims the snippets did not support."
                    )
                    draft = ev.get("draft_reflection")
                    if draft:
                        with st.expander("Original draft (rejected)", expanded=False):
                            st.write(draft)
                elif refine_verdict == "OK":
                    st.caption("✅ REFINE: draft accepted as faithful to snippets.")
                elif refine_verdict in ("ERROR", "SKIPPED"):
                    st.caption(f"⚠️ REFINE: {refine_verdict.lower()} (kept draft as-is).")

    # ===== Cognitive deliberations (THINK → PLAN → ACT → REFINE) ============
    st.divider()
    st.subheader("🧠 Cognitive deliberations (last 5)")
    st.caption(
        "Each entry is one full autonomous reasoning cycle the agent ran "
        "before replying: it decomposed the request into sub-questions, "
        "picked a verb (RECALL / SEARCH / ANSWER) for each, executed them "
        "concurrently, and \u2014 when a gap remained \u2014 ran ONE refine round. "
        "Source: `state/deliberation-YYYY-MM-DD.jsonl` (rotated daily)."
    )
    delibs = _read_deliberation_feed(limit=5)
    if not delibs:
        st.info(
            "No deliberations yet. The cognitive loop only runs on real user "
            "turns that aren't chitchat / explicit search / LTM short-circuit. "
            "Send a multi-part question in Telegram to trigger one."
        )
    else:
        for d in delibs:
            engaged = d.get("engaged", True)
            rounds = d.get("refine_rounds", 0)
            total_ms = d.get("total_ms", 0)
            ts = d.get("ts", "?")
            head_icon = "🟢" if engaged else "⚪"
            label = (
                f"{head_icon} {ts} · rounds={rounds} · "
                f"{total_ms} ms · {d.get('backend', '?')}"
            )
            with st.expander(label, expanded=(d is delibs[0])):
                if not engaged:
                    st.warning(
                        f"Loop bypassed: `{d.get('bypass_reason') or 'unknown'}`"
                    )
                st.markdown(
                    f"**User input:** {(d.get('user_input') or '')[:300]}"
                )
                st.markdown(f"**Intent:** {d.get('intent', '_(none)_')}")
                subqs = d.get("sub_questions") or []
                if subqs:
                    st.markdown("**Sub-questions:**")
                    for i, q in enumerate(subqs, start=1):
                        st.markdown(f"  {i}. {q}")
                plan = d.get("plan") or []
                if plan:
                    st.markdown("**Plan:**")
                    for i, p in enumerate(plan, start=1):
                        verb = _verb_badge(p.get("verb", ""))
                        q = p.get("query") or ""
                        line = f"  {i}. `{verb}`"
                        if q:
                            line += f" — _{q}_"
                        line += f"  → sub_q: {p.get('sub_q', '')[:120]}"
                        st.markdown(line)
                evidence = d.get("evidence") or []
                if evidence:
                    st.markdown("**Evidence:**")
                    for i, e in enumerate(evidence, start=1):
                        verb = _verb_badge(e.get("verb", ""))
                        hits = e.get("hits", 0)
                        ms = e.get("elapsed_ms", 0)
                        preview = (e.get("content") or "").strip()
                        if len(preview) > 320:
                            preview = preview[:317] + "..."
                        st.markdown(
                            f"  {i}. `{verb}` · hits={hits} · {ms} ms"
                        )
                        if preview:
                            st.code(preview)
                refine_decisions = d.get("refine_decisions") or []
                if refine_decisions:
                    st.markdown(
                        "**Refine decision(s):** "
                        + " · ".join(f"`{r}`" for r in refine_decisions)
                    )

    # ===== Recurring research tasks ==========================================
    st.divider()
    st.subheader("⏱️ Scheduled research tasks")
    st.caption(
        "Recurring tasks created via natural language (\"check MSFT every "
        "hour\"). Each fire runs the queries through SearXNG, hands the "
        "brief to the SLM, and broadcasts the report to ALL bound "
        "connectors (Telegram + web). Source: `state/tasks.yaml`."
    )
    tasks = _read_tasks()
    if not tasks:
        st.info(
            "No scheduled tasks. Create one from chat — e.g. "
            "*\"check the Microsoft stock and news every hour and "
            "summarise\"*. Tasks survive restarts."
        )
    else:
        active = sum(1 for t in tasks if not t.get("paused"))
        paused = sum(1 for t in tasks if t.get("paused"))
        total_fires = sum(int(t.get("fire_count") or 0) for t in tasks)
        with_error = sum(1 for t in tasks if t.get("last_error"))
        m_cols = st.columns(4)
        m_cols[0].metric("Active", active)
        m_cols[1].metric("Paused", paused)
        m_cols[2].metric("Total fires", total_fires)
        m_cols[3].metric(
            "With last_error", with_error,
            delta="errors" if with_error else None,
        )
        # Sort: active before paused, then most-recently-fired first.
        tasks_sorted = sorted(
            tasks,
            key=lambda t: (
                bool(t.get("paused")),
                t.get("last_run_at") or "",
            ),
            reverse=False,
        )
        for t in tasks_sorted:
            tid = t.get("id", "?")
            topic = t.get("topic", "(no topic)")
            origin = t.get("origin", "?")
            interval = _format_task_interval(t.get("interval_seconds", 0))
            fire_count = int(t.get("fire_count") or 0)
            paused_flag = bool(t.get("paused"))
            state_chip = "⏸ paused" if paused_flag else "▶ active"
            err = t.get("last_error")
            label_extras = []
            if err:
                label_extras.append("⚠️")
            label = (
                f"{state_chip} `{tid}` — *{topic}* — every {interval} "
                f"· {fire_count}× fired · from `{origin}` "
                + " ".join(label_extras)
            )
            with st.expander(label.strip(), expanded=False):
                next_run = t.get("next_run_at") or "?"
                last_run = t.get("last_run_at") or "never"
                st.markdown(
                    f"**Next fire:** `{next_run}`  ·  **Last fire:** `{last_run}`"
                )
                queries = t.get("queries") or []
                if queries:
                    st.markdown(f"**Queries ({len(queries)}):**")
                    for q in queries:
                        st.markdown(f"- `{q}`")
                description = (t.get("description") or "").strip()
                if description:
                    st.markdown(f"**Operator's request:** _{description}_")
                if err:
                    st.error(f"Last error: {err}")
                last_report = (t.get("last_report") or "").strip()
                if last_report:
                    with st.expander("Last report (truncated)", expanded=False):
                        st.markdown(last_report[:2000])

    # ===== Tool registry =====================================================
    st.divider()
    st.subheader("🔌 Tool registry")
    st.caption(
        "Plugins registered with `tools.registry.ToolRegistry` at boot. "
        "Each tool exposes a uniform `call(**kwargs) -> ToolResult` "
        "contract so future PLAN-stage code can dispatch by name. "
        "Source: `state/tools.json` (published by `main.py`)."
    )
    tools_inv = _read_tools_inventory()
    if not tools_inv:
        st.info(
            "Tool inventory not published yet. Start `python main.py` — "
            "the inventory is written once at boot after each Tool is "
            "registered."
        )
    else:
        for tool in tools_inv:
            name = tool.get("name", "?")
            desc = tool.get("description", "")
            side_fx = tool.get("side_effects", False)
            req_conf = tool.get("requires_confirmation", False)
            chips = []
            if side_fx:
                chips.append(":orange[**side-effects**]")
            else:
                chips.append(":green[**read-only**]")
            if req_conf:
                chips.append(":red[**requires confirmation**]")
            with st.expander(
                f"`{name}` — {desc}  ·  " + " · ".join(chips),
                expanded=False,
            ):
                args = tool.get("args_schema") or {}
                if not args:
                    st.caption("(no documented args)")
                else:
                    st.markdown("**Arguments:**")
                    for arg_name, meta in args.items():
                        req = "required" if meta.get("required") else "optional"
                        atype = meta.get("type", "any")
                        adesc = meta.get("desc", "")
                        line = f"- `{arg_name}` ({atype}, {req})"
                        if adesc:
                            line += f" — {adesc}"
                        st.markdown(line)

    # ===== Skill registry ====================================================
    st.divider()
    st.subheader("🎯 Skill registry")
    st.caption(
        "Capabilities registered with `core.skills.SkillRegistry` at boot. "
        "A Skill is the agent-facing layer above Tools — it composes 0..N "
        "Tool calls + its own policy. Phase 2 will dispatch the Cognitive "
        "Loop's PLAN-stage verbs through this registry. Source: "
        "`state/skills.json` + `state/skills-YYYY-MM-DD.jsonl` (published + appended "
        "by `main.py` / `SkillRegistry.invoke`)."
    )
    skills_inv = _read_skills_inventory()
    if not skills_inv:
        st.info(
            "Skill inventory not published yet. Start `python main.py` — "
            "the inventory is written at boot after each Skill is registered."
        )
    else:
        # Cost-tier ordering badge — mirrors SkillRegistry._COST_ORDER so
        # the dashboard renders cheap-first like the (future) PLAN menu.
        _tier_badge = {
            "free": ":green[**free**]",
            "cheap": ":blue[**cheap**]",
            "expensive": ":orange[**expensive**]",
        }
        _tier_order = {"free": 0, "cheap": 1, "expensive": 2}
        for sk in sorted(
            skills_inv,
            key=lambda s: (_tier_order.get(s.get("cost_tier", "expensive"), 99),
                            s.get("name", "~")),
        ):
            name = sk.get("name", "?")
            desc = sk.get("description", "")
            tier = sk.get("cost_tier", "cheap")
            chips = [_tier_badge.get(tier, f"`{tier}`")]
            if sk.get("requires_network"):
                chips.append(":violet[**network**]")
            else:
                chips.append(":gray[**offline-ok**]")
            if sk.get("side_effects"):
                chips.append(":orange[**side-effects**]")
            else:
                chips.append(":green[**read-only**]")
            if sk.get("requires_confirmation"):
                chips.append(":red[**needs ack**]")
            with st.expander(
                f"`{name}` — {desc}  ·  " + " · ".join(chips),
                expanded=False,
            ):
                hints = sk.get("trigger_hints") or []
                if hints:
                    st.markdown("**When to pick this skill:**")
                    for h in hints:
                        st.markdown(f"- {h}")
                args = sk.get("args_schema") or {}
                if not args:
                    st.caption("(no documented args)")
                else:
                    st.markdown("**Arguments:**")
                    for arg_name, meta in args.items():
                        req = "required" if meta.get("required") else "optional"
                        atype = meta.get("type", "any")
                        adesc = meta.get("desc", "")
                        line = f"- `{arg_name}` ({atype}, {req})"
                        if adesc:
                            line += f" — {adesc}"
                        st.markdown(line)

        # Recent invocations — newest first. Populated by every dispatch
        # through `SkillRegistry.invoke(...)`; Brain / Cognition /
        # Heartbeat / Scheduler all route their searches, recalls and
        # reflections through the registry, so this feed is the
        # canonical timeline of what the agent actually used.
        activity = _read_skills_activity(limit=20)
        st.markdown("**Recent invocations** (newest first):")
        if not activity:
            st.caption(
                "(no recent invocations — no `state/skills-*.jsonl` siblings yet)"
            )
        else:
            for entry in activity:
                ok = entry.get("ok", False)
                badge = "🟢" if ok else "🔴"
                ts = entry.get("ts", "?")
                sname = entry.get("skill", "?")
                ms = entry.get("latency_ms", 0)
                tools_used = entry.get("tools_used") or []
                kwargs_keys = entry.get("kwargs_keys") or []
                err = (entry.get("error") or "").strip()
                line = (
                    f"{badge} `{ts}` · **{sname}** · {ms} ms"
                    f" · tools=[{', '.join(tools_used)}]"
                    f" · args=[{', '.join(kwargs_keys)}]"
                )
                if err:
                    line += f"\n  └ error: `{err[:120]}`"
                st.markdown(line)

    # ===== Self-reflection feed (proof of self-learning) =====================
    st.divider()
    st.subheader("🪞 Self-reflection feed (last 8)")
    st.caption(
        "Each entry is the agent grading its OWN reply. Recurring `interest` "
        "topics get auto-promoted to LEARNED_PREFERENCES during Sleep "
        "Metabolism. Source: `state/reflection-YYYY-MM-DD.jsonl` (rotated daily)."
    )
    refls = _read_reflection_feed(limit=8)
    if not refls:
        st.info(
            "No reflections yet. Send any non-trivial message in Telegram — "
            "reflection runs in the background after each reply."
        )
    else:
        # Preview tonight's auto-promotions across the SAME 24 h window
        # that core.heartbeat._consolidate_reflections actually scans.
        from collections import Counter
        agent_now = datetime.now(tz=ZoneInfo(cfg.system.timezone))
        cutoff = agent_now - timedelta(hours=24)
        window = _read_reflections_since(cutoff)
        tally = Counter(
            r.get("interest", "").strip()
            for r in window
            if r.get("interest", "").strip()
        )
        recurring = [t for t, n in tally.most_common(5) if n >= 2]
        if recurring:
            st.success(
                "🌱 Likely promotions tonight (interests recurring ≥2x in "
                f"last 24 h, n={len(window)}): "
                + ", ".join(f"**{t}**" for t in recurring)
            )
        for r in refls:
            q = (r.get("quality") or "?").lower()
            badge = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(q, "⚪")
            kind = r.get("kind", "?")
            ts = r.get("ts", "?")
            label = f"{badge} {ts} · *{kind}* · quality={q}"
            with st.expander(label, expanded=(r is refls[0])):
                st.markdown(f"**Input:** {r.get('input', '')[:300]}")
                st.markdown(
                    f"**Reply:** {r.get('response', '')[:400]}"
                    + (" …" if len(r.get("response", "")) > 400 else "")
                )
                if r.get("web_searched"):
                    st.caption("Grounded by web search this turn.")
                st.markdown(f"**Critique:** {r.get('critique', '')}")
                st.markdown(f"**Lesson:** {r.get('lesson', '')}")
                interest = r.get("interest") or ""
                if interest:
                    st.markdown(f"**Future interest:** `{interest}`")

    # ===== Bottom: Soul + Archive ============================================
    st.divider()
    a, b = st.columns(2)
    with a:
        st.subheader("Soul (read-only)")
        if SOUL_FILE.exists():
            st.code(SOUL_FILE.read_text(encoding="utf-8"), language="markdown")
        else:
            st.warning("soul.md not found.")

    with b:
        st.subheader("Memory archive (last 2 KB)")
        archive = _read_archive_tail(_REPO_ROOT / cfg.memory.archive_path)
        st.code(archive, language="markdown")

    # ===== Footer + auto-refresh =============================================
    refresh_caption = (
        f"Auto-refresh every {REFRESH_SECONDS}s"
        if _AUTOREFRESH_AVAILABLE
        else (
            "Auto-refresh component missing — "
            "`pip install streamlit-autorefresh` for live updates"
        )
    )
    st.caption(
        f"Snapshot @ {state.get('now', '?')} · "
        f"Started @ {state.get('started_at', '?')} · "
        f"{refresh_caption}"
    )
    if not _AUTOREFRESH_AVAILABLE:
        st.button("🔄 Refresh now", on_click=st.rerun, key="dashboard_refresh_btn")


if __name__ == "__main__":
    main()
