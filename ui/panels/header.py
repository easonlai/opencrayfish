"""ui.panels.header — title banner + status badges + top metrics + idle countdown.

Renders the always-visible top strip of the dashboard:

  * Title (``🐚 <designation> — Vital Signs``)
  * Codename subtitle (when present in soul.md)
  * Status badges (ALIVE/STALE/DEAD, ON DUTY/STRESSED/SLEEPING, BRAIN
    online/OFFLINE, duty window + timezone)
  * 7-column metric row (CPU / RAM / Temp / Brain / Pulses / Proactive /
    Stress)
  * Idle countdown progress bar

Also owns the "no state" empty-state screen that fires when
``state/vitals.json`` doesn't exist yet (first boot, agent not running).
That keeps every "if state is None" branch in one place so the
top-level dashboard.py orchestrator can stay flat.
"""
from __future__ import annotations

import streamlit as st

from ._paths import REFRESH_SECONDS, STATE_FILE
from ._readers import humanize, read_soul_identity, staleness


def render_empty_state(autorefresh_available: bool) -> None:
    """Render the boot-time empty-state screen (no state/vitals.json).

    Shown when ``main.py`` hasn't completed its first pulse yet. Falls
    back to a manual refresh button when the autorefresh component is
    not installed (operators don't have to ``pip install`` anything new
    just to read the dashboard).
    """
    from ._paths import REPO_ROOT
    st.title("🐚 OpenCrayFish — Vital Signs")
    st.error(
        f"No state snapshot at `{STATE_FILE.relative_to(REPO_ROOT)}`. "
        "Is `python main.py` running? The first pulse takes up to 30 s."
    )
    st.caption(
        f"Auto-refreshing every {REFRESH_SECONDS}s."
        if autorefresh_available
        else (
            "Auto-refresh component not installed — "
            "`pip install streamlit-autorefresh` for live updates. "
            "Use the button below to refresh manually."
        )
    )
    if not autorefresh_available:
        st.button("🔄 Refresh now", on_click=st.rerun)


def render(state: dict) -> None:
    """Render the header + metrics + idle countdown for an active agent.

    Caller MUST guard ``state is not None`` and call ``render_empty_state``
    in the negative branch — this function assumes a live snapshot.
    """
    designation_state = state.get("designation", "")
    soul_designation, soul_codename = read_soul_identity()
    # config.yaml ``system.individual_designation`` is the single source
    # of truth (published live via state["designation"]). soul.md may
    # still carry a legacy literal — use it only as a fallback if the
    # heartbeat state hasn't published a designation yet.
    designation = designation_state or soul_designation or "Unknown"
    status_label, status_color = staleness(state)
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
            f" — retry in ~{int(recovery)}s"
            if isinstance(recovery, (int, float)) and recovery > 0
            else ""
        )
        badges.append(f":red[**🧠 BRAIN OFFLINE**{recovery_hint}]")
    else:
        badges.append(f":green[**🧠 brain online** `{brain_backend}`]")
    badges.append(
        f"Duty window: `{state.get('duty_window', '?')}` ({state.get('timezone', '?')})"
    )

    st.title(f"🐚 {designation} — Vital Signs")
    # Subtitle: codename from soul.md + per-instance designation from
    # config so the operator still sees WHICH deployment they're looking
    # at when multiple agents share a soul (forks / sibling instances).
    subtitle_bits: list[str] = []
    if soul_codename:
        subtitle_bits.append(f"Codename: **{soul_codename}**")
    if designation_state and designation_state != designation:
        subtitle_bits.append(f"Instance: `{designation_state}`")
    if subtitle_bits:
        st.caption(" · ".join(subtitle_bits))
    st.markdown(" · ".join(badges))

    # ---- Top metric row -----------------------------------------------
    vitals = state.get("vitals") or {}
    cols = st.columns(7)
    cols[0].metric("CPU", f"{vitals.get('cpu', 0):.0f}%" if vitals else "—")
    cols[1].metric("RAM", f"{vitals.get('ram', 0):.0f}%" if vitals else "—")
    temp = vitals.get("temp") if vitals else None
    cols[2].metric("Temp", f"{temp:.1f}°C" if temp is not None else "n/a")
    # Brain (SLM) availability metric. When offline, show backend name
    # as the delta so the operator knows WHICH endpoint to revive.
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

    # ---- Idle countdown -----------------------------------------------
    idle = int(state.get("idle_seconds", 0))
    threshold = int(state.get("idle_threshold_seconds", 1800))
    progress = min(idle / threshold, 1.0) if threshold else 0
    st.markdown(
        f"**Idle for {humanize(idle)}** of {humanize(threshold)} "
        "until next autonomous research."
    )
    st.progress(progress)
