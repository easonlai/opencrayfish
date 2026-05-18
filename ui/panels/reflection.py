"""ui.panels.reflection — self-reflection feed (last 8) + tonight's likely promotions."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st

from ._readers import read_reflection_feed, read_reflections_since


def render(cfg) -> None:
    """Render the reflection feed.

    ``cfg`` is the loaded ``core.config.Config`` — needed for the agent
    timezone so the 24h promotion-preview window matches what
    ``core.heartbeat._consolidate_reflections`` actually scans at 02:00
    in the agent's TZ.
    """
    st.subheader("🪞 Self-reflection feed (last 8)")
    st.caption(
        "Each entry is the agent grading its OWN reply. Recurring `interest` "
        "topics get auto-promoted to LEARNED_PREFERENCES during Sleep "
        "Metabolism. Source: `state/reflection-YYYY-MM-DD.jsonl` (rotated daily)."
    )
    refls = read_reflection_feed(limit=8)
    if not refls:
        st.info(
            "No reflections yet. Send any non-trivial message in Telegram — "
            "reflection runs in the background after each reply."
        )
        return
    # Preview tonight's auto-promotions across the SAME 24 h window
    # that core.heartbeat._consolidate_reflections actually scans.
    agent_now = datetime.now(tz=ZoneInfo(cfg.system.timezone))
    cutoff = agent_now - timedelta(hours=24)
    window = read_reflections_since(cutoff)
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
