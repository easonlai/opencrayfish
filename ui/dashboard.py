"""ui.dashboard — Live Vital Signs Dashboard for OpenCrayFish.

Run with:  ``streamlit run ui/dashboard.py``

This module is intentionally THIN — it only owns:
  * Streamlit page config
  * The autorefresh component wiring (or its manual-button fallback)
  * The empty-state guard when ``state/vitals.json`` is missing
  * The orchestration of the panel render order

Every visual panel lives in ``ui/panels/`` and is invoked from ``main()``
below. Shared data-loading helpers and file-path constants live in
``ui/panels/_readers.py`` and ``ui/panels/_paths.py``. To add a new
panel, drop ``ui/panels/<name>.py`` exposing ``render(...)`` and add an
import + call here.

Pure read-only: the dashboard NEVER writes any of the agent's state
files. It is a mirror, not a control surface.
"""
# ruff: noqa: I001  (import order matters — ui.panels must come before
# core.* so the sys.path side-effect in ui.panels._paths fires first)
from __future__ import annotations

import streamlit as st

from ui.panels import (
    deliberation,
    footer,
    header,
    mood,
    proactive,
    reflection,
    skills,
    tasks,
    tools,
    vitals,
)
from ui.panels._paths import REFRESH_SECONDS, REPO_ROOT
from ui.panels._readers import read_state

# Side-effect import: ``ui.panels._paths`` prepends the repo root to
# ``sys.path`` at import time so ``from core...`` resolves below. Keep
# this Config import AFTER the panels import to preserve that ordering.
from core.config import Config  # noqa: E402

# Optional dep — same pattern used by ``ui/web_chat.py``. When present
# we use it to schedule a non-blocking rerun on a fixed cadence; when
# absent, the dashboard renders a manual refresh button so the operator
# still has a way forward without ``pip install``-ing anything new.
try:
    from streamlit_autorefresh import st_autorefresh  # type: ignore
    _AUTOREFRESH_AVAILABLE = True
except ImportError:  # pragma: no cover
    _AUTOREFRESH_AVAILABLE = False


def main() -> None:
    st.set_page_config(
        page_title="OpenCrayFish — Vital Signs",
        page_icon="🐚",
        layout="wide",
    )

    cfg = Config.load(REPO_ROOT / "config.yaml")

    # Schedule the next non-blocking rerun BEFORE we render anything.
    # The component returns the run-counter (unused) and triggers a
    # rerun every REFRESH_SECONDS without holding the Streamlit thread
    # in ``time.sleep(...)`` (which would block widget interaction and
    # skip frames on slow renders).
    if _AUTOREFRESH_AVAILABLE:
        st_autorefresh(
            interval=REFRESH_SECONDS * 1000,
            key="dashboard_refresh",
        )

    state = read_state()
    if state is None:
        header.render_empty_state(_AUTOREFRESH_AVAILABLE)
        return

    # ----- Header banner + metrics + idle countdown -------------------
    header.render(state)

    # ----- Two-column body --------------------------------------------
    left, right = st.columns([3, 2])
    with left:
        vitals.render(state, cfg)
    with right:
        mood.render(state)

    # ----- Stacked feed sections --------------------------------------
    st.divider()
    proactive.render()
    st.divider()
    deliberation.render()
    st.divider()
    tasks.render()
    st.divider()
    tools.render()
    st.divider()
    skills.render()
    st.divider()
    reflection.render(cfg)

    # ----- Bottom: Soul + Archive + footer ----------------------------
    st.divider()
    footer.render_soul_and_archive(cfg)
    footer.render_footer(state, autorefresh_available=_AUTOREFRESH_AVAILABLE)


if __name__ == "__main__":
    main()
