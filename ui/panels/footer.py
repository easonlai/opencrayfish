"""ui.panels.footer — soul + archive bottom row + auto-refresh footer caption."""
from __future__ import annotations

import streamlit as st

from ._paths import REFRESH_SECONDS, REPO_ROOT, SOUL_FILE
from ._readers import read_archive_tail


def render_soul_and_archive(cfg) -> None:
    """Render the two-column Soul + Memory archive row."""
    a, b = st.columns(2)
    with a:
        st.subheader("Soul (read-only)")
        if SOUL_FILE.exists():
            st.code(SOUL_FILE.read_text(encoding="utf-8"), language="markdown")
        else:
            st.warning("soul.md not found.")
    with b:
        st.subheader("Memory archive (last 2 KB)")
        archive = read_archive_tail(REPO_ROOT / cfg.memory.archive_path)
        st.code(archive, language="markdown")


def render_footer(state: dict, *, autorefresh_available: bool) -> None:
    """Render the final timestamp caption + manual refresh fallback."""
    refresh_caption = (
        f"Auto-refresh every {REFRESH_SECONDS}s"
        if autorefresh_available
        else (
            "Auto-refresh component missing — "
            "`pip install streamlit-autorefresh` for live updates"
        )
    )
    st.caption(
        f"Snapshot @ {state.get('now', '?')} · "
        f"Started @ {state.get('started_at', '?')} · "
        f"{refresh_caption}"
    )
    if not autorefresh_available:
        st.button(
            "🔄 Refresh now",
            on_click=st.rerun,
            key="dashboard_refresh_btn",
        )
