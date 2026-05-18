"""ui.panels.deliberation — cognitive deliberations feed (last 5)."""
from __future__ import annotations

import streamlit as st

from ._readers import read_deliberation_feed, verb_badge


def render() -> None:
    st.subheader("🧠 Cognitive deliberations (last 5)")
    st.caption(
        "Each entry is one full autonomous reasoning cycle the agent ran "
        "before replying: it decomposed the request into sub-questions, "
        "picked a verb (RECALL / SEARCH / ANSWER) for each, executed them "
        "concurrently, and — when a gap remained — ran ONE refine round. "
        "Source: `state/deliberation-YYYY-MM-DD.jsonl` (rotated daily)."
    )
    delibs = read_deliberation_feed(limit=5)
    if not delibs:
        st.info(
            "No deliberations yet. The cognitive loop only runs on real user "
            "turns that aren't chitchat / explicit search / LTM short-circuit. "
            "Send a multi-part question in Telegram to trigger one."
        )
        return
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
                    verb = verb_badge(p.get("verb", ""))
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
                    verb = verb_badge(e.get("verb", ""))
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
