"""ui.panels.tasks — recurring research-task registry view."""
from __future__ import annotations

import streamlit as st

from ._readers import format_task_interval, read_tasks


def render() -> None:
    st.subheader("⏱️ Scheduled research tasks")
    st.caption(
        "Recurring tasks created via natural language (\"check MSFT every "
        "hour\"). Each fire runs the queries through SearXNG, hands the "
        "brief to the SLM, and broadcasts the report to ALL bound "
        "connectors (Telegram + web). Source: `state/tasks.yaml`."
    )
    tasks = read_tasks()
    if not tasks:
        st.info(
            "No scheduled tasks. Create one from chat — e.g. "
            "*\"check the Microsoft stock and news every hour and "
            "summarise\"*. Tasks survive restarts."
        )
        return
    active = sum(1 for t in tasks if not t.get("paused"))
    paused = sum(1 for t in tasks if t.get("paused"))
    total_fires = sum(int(t.get("fire_count") or 0) for t in tasks)
    with_error = sum(1 for t in tasks if t.get("last_error"))
    m_cols = st.columns(4)
    m_cols[0].metric("Active", active)
    m_cols[1].metric("Paused", paused)
    m_cols[2].metric("Total fires", total_fires)
    m_cols[3].metric(
        "With last_error",
        with_error,
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
        interval = format_task_interval(t.get("interval_seconds", 0))
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
