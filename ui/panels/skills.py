"""ui.panels.skills — registered Skill inventory + recent invocations."""
from __future__ import annotations

import streamlit as st

from ._readers import read_skills_activity, read_skills_inventory


def render() -> None:
    st.subheader("🎯 Skill registry")
    st.caption(
        "Capabilities registered with `core.skills.SkillRegistry` at boot. "
        "A Skill is the agent-facing layer above Tools — it composes 0..N "
        "Tool calls + its own policy. The Cognitive Loop's PLAN-stage "
        "verbs are dispatched through this registry. Source: "
        "`state/skills.json` + `state/skills-YYYY-MM-DD.jsonl` "
        "(published + appended by `main.py` / `SkillRegistry.invoke`)."
    )
    skills_inv = read_skills_inventory()
    if not skills_inv:
        st.info(
            "Skill inventory not published yet. Start `python main.py` — "
            "the inventory is written at boot after each Skill is registered."
        )
        return
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
        key=lambda s: (
            _tier_order.get(s.get("cost_tier", "expensive"), 99),
            s.get("name", "~"),
        ),
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
    # reflections through the registry, so this feed is the canonical
    # timeline of what the agent actually used.
    activity = read_skills_activity(limit=20)
    st.markdown("**Recent invocations** (newest first):")
    if not activity:
        st.caption(
            "(no recent invocations — no `state/skills-*.jsonl` siblings yet)"
        )
        return
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
