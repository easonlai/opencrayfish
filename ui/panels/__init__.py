"""ui.panels — Streamlit dashboard split into one module per visual panel.

Each ``render*`` function takes whatever live state it needs and emits
its panel to the current Streamlit container. The top-level
``ui/dashboard.py`` orchestrator is responsible for layout (two-column
body, divider sections) and for calling each panel in order.

Shared infrastructure:
  * ``ui.panels._paths``  — file paths + regex constants
  * ``ui.panels._readers`` — read-only data loaders (JSONL/JSON/YAML)

Adding a new panel: drop a ``<name>.py`` here exposing ``render(...)``,
add an import + call site in ``ui/dashboard.py``. Avoid cross-panel
imports — share via ``_readers`` or pass plain dicts from the
orchestrator.
"""
