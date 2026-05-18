"""ui.panels.tools — registered Tool inventory view."""
from __future__ import annotations

import streamlit as st

from ._readers import read_tools_inventory


def render() -> None:
    st.subheader("🔌 Tool registry")
    st.caption(
        "Plugins registered with `tools.registry.ToolRegistry` at boot. "
        "Each tool exposes a uniform `call(**kwargs) -> ToolResult` "
        "contract so future PLAN-stage code can dispatch by name. "
        "Source: `state/tools.json` (published by `main.py`)."
    )
    tools_inv = read_tools_inventory()
    if not tools_inv:
        st.info(
            "Tool inventory not published yet. Start `python main.py` — "
            "the inventory is written once at boot after each Tool is "
            "registered."
        )
        return
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
                continue
            st.markdown("**Arguments:**")
            for arg_name, meta in args.items():
                req = "required" if meta.get("required") else "optional"
                atype = meta.get("type", "any")
                adesc = meta.get("desc", "")
                line = f"- `{arg_name}` ({atype}, {req})"
                if adesc:
                    line += f" — {adesc}"
                st.markdown(line)
