"""tools.archive_read — Read-only LTM keyword lookup over `memory/archive.md`.

A small, side-effect-free Tool that wraps the keyword-overlap scoring
used by `core/cognition.py::_do_recall` and `core/brain.py::_retrieve_relevant`
today. Phase 1 introduces it so the new `RecallSkill` can dispatch
through the ToolRegistry like every other capability instead of reading
the archive file directly — keeping the Tool layer the single seam for
file/network I/O.

Behavior is intentionally identical to the inline implementations
(same tokenization: lowercase split, >3-char terms; same scoring:
count of unique terms found per line; same `#` comment skip; same
top-N truncation default of 5). This lets RecallSkill and the legacy
inline code coexist during Phase 1 without behavior drift.

Failures degrade — a missing/unreadable archive returns
`ok=True, data=[]` rather than `ok=False`, matching the existing
"empty recall" semantic that Brain treats as "no LTM hit, continue".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import ToolResult


class ArchiveRead:
    # --- Tool plugin contract (satisfies tools.base.Tool by shape) -----------
    name: str = "archive_read"
    description: str = (
        "Keyword-overlap lookup over the agent's LTM (memory/archive.md). "
        "Returns the top-N matching archive lines for a query."
    )
    args_schema: dict[str, dict[str, Any]] = {
        "query": {
            "type": "string",
            "required": True,
            "desc": "Free-form query. Terms ≤3 chars are ignored.",
        },
        "limit": {
            "type": "int",
            "required": False,
            "default": 5,
            "desc": "Maximum number of archive lines to return (1-20).",
        },
    }
    side_effects: bool = False
    requires_confirmation: bool = False

    def __init__(self, archive_path: str | Path) -> None:
        self._path = Path(archive_path)

    async def call(self, **kwargs: Any) -> ToolResult:
        query = kwargs.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(ok=False, error="missing or empty 'query' argument")
        try:
            limit = int(kwargs.get("limit", 5))
        except (TypeError, ValueError):
            limit = 5
        limit = max(1, min(20, limit))

        # Missing archive → empty result, not failure. Mirrors the legacy
        # `(archive empty)` / `(no archive matches)` semantics that
        # downstream Brain / Cognition code already handles.
        if not self._path.exists():
            return ToolResult(
                ok=True,
                data=[],
                meta={"hits": 0, "reason": "archive_missing"},
            )

        terms = {t for t in query.lower().split() if len(t) > 3}
        if not terms:
            return ToolResult(
                ok=True,
                data=[],
                meta={"hits": 0, "reason": "no_recallable_terms"},
            )

        try:
            content = self._path.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult(
                ok=False,
                error=f"archive read failed: {exc.__class__.__name__}: {exc}",
            )

        hits: list[tuple[int, str]] = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            score = sum(1 for t in terms if t in stripped.lower())
            if score:
                hits.append((score, stripped))
        if not hits:
            return ToolResult(
                ok=True,
                data=[],
                meta={"hits": 0, "reason": "no_match"},
            )
        hits.sort(key=lambda kv: kv[0], reverse=True)
        top = hits[:limit]
        payload = [{"line": line, "score": score} for score, line in top]
        return ToolResult(
            ok=True,
            data=payload,
            meta={
                "hits": len(payload),
                "top_score": top[0][0],
                "query_terms": sorted(terms),
            },
        )

    async def aclose(self) -> None:
        # No resources held; no-op for protocol completeness.
        return None
