"""ui.panels.proactive — autonomous learning feed (last 5)."""
from __future__ import annotations

import streamlit as st

from ._readers import proactive_source_badge, read_proactive_feed


def render() -> None:
    st.subheader("🔬 Autonomous learning feed (last 5)")
    st.caption(
        "Each entry is a permanent record from `state/proactive.jsonl`. "
        "Trigger one immediately from Telegram with `/research [optional topic]`."
    )
    events = read_proactive_feed(limit=5)
    if not events:
        st.info(
            "No autonomous research yet. Either wait until idle > 30 min, "
            "or send `/research` in Telegram to verify the pipeline now."
        )
        return
    for ev in events:
        tag = " · *manual*" if ev.get("manual") else ""
        badge = proactive_source_badge(ev.get("source") or "")
        label = (
            f"📡 {ev.get('ts', '?')} — {ev.get('topic', '?')}{tag} {badge}"
        ).rstrip()
        with st.expander(label, expanded=(ev is events[0])):
            decisions = ev.get("triage_decisions") or []
            if decisions:
                st.markdown(
                    f"**Triage trail ({len(decisions)} candidate(s)):**"
                )
                for d in decisions:
                    verdict = d.get("verdict", "?")
                    emoji = {
                        "unknown": "🔍",
                        "known_by_slm": "✅",
                        "in_ltm": "📚",
                    }.get(verdict, "❔")
                    st.caption(
                        f"{emoji} `{verdict}` — {d.get('topic', '?')}"
                    )
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
                st.caption(
                    "✅ REFINE: draft accepted as faithful to snippets."
                )
            elif refine_verdict in ("ERROR", "SKIPPED"):
                st.caption(
                    f"⚠️ REFINE: {refine_verdict.lower()} (kept draft as-is)."
                )
