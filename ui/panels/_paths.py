"""ui.panels._paths — shared file paths + constants for the dashboard panels.

This module is the single source of truth for WHERE the dashboard reads
its data from. Every panel imports the paths it needs from here so the
on-disk layout can change in exactly one place.

The paths intentionally mirror what the live agent writes:
  * ``state/*.jsonl`` feeds are appended by the runtime;
  * ``state/*.json`` snapshots are atomic-rewritten at boot or on change;
  * ``state/logs/agent.log`` is the rotating Python logger output;
  * ``memory/archive.md`` is the LTM that Sleep Metabolism mutates;
  * ``soul.md`` is the IMMUTABLE_CORE persona doc.

The dashboard NEVER writes any of these files — it is strictly a
read-only mirror of the agent's externalised state.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# Streamlit launches with ui/ as the script dir; add the repo root so
# ``from core...`` and ``from tools...`` resolve. We do this in the
# shared paths module so any panel that imports from here gets it for
# free (the import order doesn't matter — sys.path is process-wide).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Exported so panels (and the top-level dashboard.py) can build paths
# relative to the repo. Don't hard-code paths inside panels — derive
# from this constant.
REPO_ROOT: Path = _REPO_ROOT

# Auto-refresh cadence for the whole dashboard. Kept here (not on the
# autorefresh component module) so a future opts-out path / settings
# panel can read the same value.
REFRESH_SECONDS = 5

# Live-state snapshot files (state/*) ------------------------------------
STATE_FILE = REPO_ROOT / "state" / "vitals.json"
PROACTIVE_FEED = REPO_ROOT / "state" / "proactive.jsonl"
REFLECTION_FEED = REPO_ROOT / "state" / "reflection.jsonl"
DELIBERATION_FEED = REPO_ROOT / "state" / "deliberation.jsonl"
VITALS_EVENTS_FEED = REPO_ROOT / "state" / "vitals_events.jsonl"
# Recurring research-task scheduler state. Written atomically by
# core.scheduler.TaskScheduler._save() — read-only here.
TASKS_FILE = REPO_ROOT / "state" / "tasks.yaml"
# Tool / Skill registry inventories. Published once at boot (and on
# change for skills) by main.py; per-invocation audit feeds appended
# by SkillRegistry.invoke() live alongside.
TOOLS_FILE = REPO_ROOT / "state" / "tools.json"
SKILLS_FILE = REPO_ROOT / "state" / "skills.json"
SKILLS_FEED = REPO_ROOT / "state" / "skills.jsonl"
# IMMUTABLE_CORE persona doc.
SOUL_FILE = REPO_ROOT / "soul.md"
# Python-logging rotating file (configured in main.py). Holds the
# structured ``CHAT / TG / WEB / TASK / TOOL / SKILL / MOOD`` events
# that the live-chat-activity + mood + errors panels filter on.
AGENT_LOG = REPO_ROOT / "state" / "logs" / "agent.log"

# Pattern matched by ``core.jsonl_writer.RotatingJsonlWriter`` siblings:
# ``<stem>-YYYY-MM-DD.jsonl``. The rotated-tail helper in ``_readers``
# fans every base path out to its rotated siblings so panels see the
# same data the writers actually produce.
ROTATED_FILENAME_RE = re.compile(
    r"^(?P<base>.+)-(?P<date>\d{4}-\d{2}-\d{2})\.jsonl$"
)

# Mirror of ``_DESIGNATION_RE`` / ``_CODENAME_RE`` in
# ``core.brain.identity_responder`` so the dashboard shows the same
# name the agent uses when it introduces itself. Kept duplicated rather
# than imported so the dashboard can still load when ``core`` cannot
# import (broken config, missing dep) — the agent identity panel then
# degrades gracefully instead of crashing the whole UI.
SOUL_DESIGNATION_RE = re.compile(
    r"\*\*Designation\*\*\s*:\s*(?P<value>.+)", re.IGNORECASE
)
SOUL_CODENAME_RE = re.compile(
    r"\*\*Codename\*\*\s*:\s*(?P<value>.+)", re.IGNORECASE
)
