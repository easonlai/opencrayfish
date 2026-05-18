"""ui.panels.mood — right column of the two-column body.

Contents (top → bottom):
  * Mood vector (5-D) bar chart + dominant/active captions + trajectory
  * Vitals stress timeline (currently-stressed banner + last 10 events)
  * Mood event log (filtered ``MOOD ...`` lines from agent.log)
  * Short-Term Memory size + buffered turns hint
  * Last autonomous research caption (topic + source badge)

Pairs with ``vitals.py`` (left column). Everything here is internal
emotional / memory state — the WHAT-IT-FEELS dimension of the agent.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ._readers import (
    proactive_source_badge,
    read_mood_log_tail,
    read_vitals_events,
)


def render(state: dict) -> None:
    """Render the entire right column of the dashboard body."""
    _render_mood(state)
    _render_stress_events(state)
    _render_mood_log()
    _render_memory(state)
    _render_last_proactive(state)


def _render_mood(state: dict) -> None:
    st.subheader("Mood vector (5-D)")
    mood = state.get("mood") or {}
    if not mood:
        st.caption("Mood not yet sampled.")
        return
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


def _render_stress_events(state: dict) -> None:
    st.subheader("⚡ Vitals stress events")
    events = read_vitals_events(limit=10)
    active_now = bool(state.get("stress_active"))
    if active_now:
        started = state.get("stress_started_at")
        st.error(
            f"🔥 Currently STRESSED since {started or '?'} — "
            "EXHAUSTION DIRECTIVE active, cognitive loop bypassed."
        )
    if not events:
        st.caption("No stress transitions recorded yet. ✅")
        return
    for ev in events:
        ts = ev.get("ts", "?")
        kind = ev.get("kind", "?")
        if kind == "stress_enter":
            temp = ev.get("temp")
            ram = ev.get("ram")
            temp_s = f"{temp:.1f}°C" if temp is not None else "n/a"
            ram_s = f"{ram:.1f}%" if ram is not None else "n/a"
            st.markdown(
                f":red[**🔥 ENTER**] `{ts}` — temp={temp_s}, ram={ram_s}"
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


def _render_mood_log() -> None:
    st.subheader("🧬 Mood event log (last 20)")
    st.caption(
        "Atomic mood movements traced back to their cause "
        "(empathy_*, vitals_stress, …). Source: `state/logs/agent.log`."
    )
    mood_lines = read_mood_log_tail(lines=20)
    if not mood_lines:
        st.caption(
            "No mood events yet — talk to the agent or wait for a stress cycle."
        )
        return
    rendered: list[str] = []
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


def _render_memory(state: dict) -> None:
    st.subheader("Short-Term Memory")
    st.metric(
        "Conversation turns held",
        f"{state.get('stm_size', 0)} / {state.get('stm_max', 0)}",
    )
    pending = state.get("stm_pending", 0)
    if pending:
        st.caption(
            f"✍️ {pending} turn(s) buffered in RAM — "
            "will flush to disk on next idle window."
        )
    st.caption("Cleared nightly during Sleep Metabolism.")


def _render_last_proactive(state: dict) -> None:
    st.subheader("Last autonomous research")
    topic = state.get("last_proactive_topic")
    if topic:
        source = state.get("last_proactive_source") or ""
        badge = proactive_source_badge(source)
        st.markdown(f"**Topic:** {topic}  {badge}")
        st.caption(f"At: {state.get('last_proactive_at', '?')}")
    else:
        st.caption(
            "None yet — fires after 30 min idle. Source is chosen by "
            "the two-stage selector (STM gap → Learned Preference)."
        )
