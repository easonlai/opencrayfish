"""tools.searxng — Web search tool backed by a self-hosted SearXNG instance.

This module exposes TWO surfaces on the same class:

  1. The original `search(query, *, limit=5) -> list[SearchResult]` API
     kept for direct callers (and tests). No production subsystem calls
     this directly anymore — they all reach SearXNG through
     `skill_registry.invoke("research", ...)` which routes via
     `tool_registry.call("web_search", ...)` which calls the `Tool`
     surface below. The two surfaces share the same httpx client.

  2. The `Tool` plugin contract (`name`, `description`, `args_schema`,
     `call`, `aclose`, …) so SearXNG is registered with
     `tools.registry.ToolRegistry` and dispatched generically by the
     SkillRegistry / future MCP bridge.

Both surfaces share the same underlying httpx client. There is no
duplication and no second instance is created when the same SearXNG
object is registered as a tool.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .base import ToolResult


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


class SearXNG:
    # --- Tool plugin contract (satisfies tools.base.Tool by shape) -----------
    # Stable identifier used by the registry. PLAN-stage SLM prompts will
    # reference this name verbatim, so do not rename it without grep'ing
    # for prompt strings first.
    name: str = "web_search"
    description: str = (
        "Live web search via a self-hosted SearXNG instance. "
        "Use for time-sensitive, niche, or post-training-cutoff facts."
    )
    args_schema: dict[str, dict[str, Any]] = {
        "query": {
            "type": "string",
            "required": True,
            "desc": "3-8 keywords (NOT a full sentence) to search the web for.",
        },
        "limit": {
            "type": "int",
            "required": False,
            "default": 5,
            "desc": "Maximum number of results to return (1-10).",
        },
    }
    side_effects: bool = False
    requires_confirmation: bool = False

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0))

    # --- Original surface (unchanged) ----------------------------------------

    async def search(self, query: str, *, limit: int = 5) -> list[SearchResult]:
        params = {"q": query, "format": "json", "safesearch": 1}
        resp = await self._client.get(f"{self._base_url}/search", params=params)
        resp.raise_for_status()
        data = resp.json()
        out: list[SearchResult] = []
        for entry in data.get("results", [])[:limit]:
            out.append(SearchResult(
                title=str(entry.get("title", "")).strip(),
                url=str(entry.get("url", "")).strip(),
                snippet=str(entry.get("content", "")).strip(),
            ))
        return out

    # --- Tool adapter --------------------------------------------------------

    async def call(self, **kwargs: Any) -> ToolResult:
        """Tool-protocol entrypoint. Wraps `search(...)` in a `ToolResult`.

        Validates kwargs at the boundary so a misbehaving SLM caller can
        never crash the registry: bad/missing `query` → ok=False, no
        network call. `limit` is clamped to [1, 10] silently.
        """
        query = kwargs.get("query", "")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(ok=False, error="missing or empty 'query' argument")
        try:
            limit = int(kwargs.get("limit", 5))
        except (TypeError, ValueError):
            limit = 5
        limit = max(1, min(10, limit))

        try:
            results = await self.search(query.strip(), limit=limit)
        except httpx.HTTPError as exc:
            return ToolResult(
                ok=False,
                error=f"SearXNG HTTP error: {exc.__class__.__name__}: {exc}",
            )
        except Exception as exc:  # defensive: never let the tool raise
            return ToolResult(
                ok=False,
                error=f"{exc.__class__.__name__}: {exc}",
            )

        # Normalize payload to plain dicts so registry consumers don't need
        # to import this module to read results.
        payload = [
            {"title": r.title, "url": r.url, "snippet": r.snippet}
            for r in results
        ]
        return ToolResult(
            ok=True,
            data=payload,
            meta={"hits": len(payload), "query": query.strip(), "limit": limit},
        )

    async def aclose(self) -> None:
        await self._client.aclose()
