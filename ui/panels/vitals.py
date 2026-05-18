"""ui.panels.vitals — left column of the two-column body.

Contents:
  * Vitals over time line chart (CPU / RAM / Temp)
  * Heartbeat log tail (today's ``logs/daily/<today>.log``)
  * Live chat activity panel (filtered ``CHAT / TG / WEB / TASK / TOOL /
    SKILL`` events from ``state/logs/agent.log``)
  * Errors & warnings panel (level-filtered tail of the same agent.log)

This is the only panel that consumes the heartbeat-rotated daily logs
in addition to the structured agent.log — both live signals are stacked
here because the operator's mental model is "what is the agent doing
right now", and the vitals chart anchors that timeline visually.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from ._paths import REPO_ROOT
from ._readers import (
    read_chat_log_tail,
    read_errors_warnings_tail,
    read_log_tail,
)


def render(state: dict, cfg) -> None:
    """Render the entire left column of the dashboard body.

    ``cfg`` is the loaded ``core.config.Config`` — needed for the agent
    timezone (so the heartbeat-log filename is computed correctly near
    midnight) and the log_path config.
    """
    _render_vitals_chart(state)
    _render_heartbeat_log(cfg)
    _render_chat_activity()
    _render_errors_warnings()


def _render_vitals_chart(state: dict) -> None:
    """Coerce the history series to numeric + render the line chart.

    ``temp`` is None on hosts without a thermal sensor (e.g. dev macOS),
    which makes pandas infer ``object`` dtype and trips Streamlit's
    melter with "columns ... with mixed types". The numeric coercion +
    ``dropna(axis=1, how="all")`` keeps the chart rendering cleanly on
    dev machines (where it shows just CPU/RAM) and Pi (where it shows
    all three).
    """
    st.subheader("Vitals over time (last ~1 hr)")
    history = state.get("history") or []
    if not history:
        st.caption("Building history… first chart appears after a few pulses.")
        return
    df = pd.DataFrame(history)
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.set_index("ts")
    chart_cols = [c for c in ("cpu", "ram", "temp") if c in df.columns]
    chart_df = df[chart_cols].apply(pd.to_numeric, errors="coerce")
    chart_df = chart_df.dropna(axis=1, how="all")
    if not chart_df.empty and len(chart_df.columns):
        st.line_chart(chart_df, y=list(chart_df.columns))
    else:
        st.caption("No numeric vitals to chart yet.")


def _render_heartbeat_log(cfg) -> None:
    st.subheader("Heartbeat log (today)")
    log_lines = read_log_tail(
        REPO_ROOT / cfg.memory.log_path,
        lines=50,
        tz=cfg.system.timezone,
    )
    if not log_lines:
        st.caption("No log entries yet.")
        return
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
        if not highlights:
            st.caption("_(no notable events yet today)_")
            return
        for ln in highlights:
            if "PROACTIVE" in ln:
                st.markdown(f":violet[{ln}]")
            elif "VITALS stress=ENTER" in ln or "Stress" in ln:
                st.markdown(f":red[{ln}]")
            elif "VITALS stress=EXIT" in ln:
                st.markdown(f":green[{ln}]")
            else:
                st.markdown(f":blue[{ln}]")


def _render_chat_activity() -> None:
    st.subheader("💬 Live chat activity (last 30 events)")
    st.caption(
        "Per-turn trail from `core.brain` + Telegram connector — "
        "`CHAT enter / empathy / ltm / search PATH=… / exit` and `TG msg / reply`. "
        "Source: `state/logs/agent.log`."
    )
    chat_lines = read_chat_log_tail(lines=30)
    if not chat_lines:
        st.info(
            "No live-chat events yet. Send a Telegram message to populate. "
            "(Requires the new structured logging in core/brain.py and "
            "connectors/telegram.py.)"
        )
        return
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
    stat_cols[1].metric(
        "Web-grounded", web_yes,
        delta=f"-{web_no} skipped" if web_no else None,
    )
    stat_cols[2].metric(
        "Triage SEARCH", search_yes,
        delta=f"vs {no_search} NO" if no_search else None,
    )
    stat_cols[3].metric("LTM short-circuit", short_circuit)
    stat_cols[4].metric(
        "Search FAILED", failed,
        delta="errors" if failed else None,
    )

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


def _render_errors_warnings() -> None:
    """Surface ERROR/WARNING/CRITICAL lines hidden by the chat filter.

    Hidden behind an expander so it's only "loud" when something is
    actually wrong (count badge in the header). Auto-expands when at
    least one CRITICAL or ERROR is present.
    """
    err_lines = read_errors_warnings_tail(lines=20)
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
            return
        for ln in err_lines:
            if "[CRITICAL]" in ln:
                st.markdown(f":red[**{ln}**]")
            elif "[ERROR]" in ln:
                st.markdown(f":red[{ln}]")
            else:
                st.markdown(f":orange[{ln}]")
