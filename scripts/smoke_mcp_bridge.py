"""Live smoke test: drive the MCP bridge against the Microsoft Learn
MCP server end-to-end.

Run manually (requires network):

    .venv/bin/python scripts/smoke_mcp_bridge.py

What it covers:
    1. ``make_mcp_bridge`` returns a real bridge from a dict shaped
       like ``cfg.plugins``.
    2. ``connect_all`` registers one OpenCrayFish Tool per upstream
       tool advertised by the MS Learn server.
    3. Each registered Tool round-trips through ``ToolRegistry.call``
       and returns a populated ``ToolResult``.
    4. Bridge ``aclose`` releases the underlying Streamable HTTP
       session without raising.

Exits with code 0 on success, 1 on any failure. NOT a pytest test —
the unit suite covers the bridge with stubs in
``tests/test_mcp_bridge.py``.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from tools import ToolRegistry, make_mcp_bridge

PLUGINS_CFG = {
    "mcp_bridge": {
        "servers": [
            {
                "name": "mslearn",
                "url": "https://learn.microsoft.com/api/mcp",
            }
        ]
    }
}


async def amain() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log = logging.getLogger("smoke.mcp_bridge")

    bridge = make_mcp_bridge(PLUGINS_CFG)
    if bridge is None:
        log.error("make_mcp_bridge returned None — config not picked up")
        return 1

    registry = ToolRegistry()
    try:
        registered = await bridge.connect_all(registry)
        if not registered:
            log.error("connect_all returned 0 tools — server unreachable?")
            return 1
        log.info("registered %d remote tool(s): %s",
                 len(registered), ", ".join(registered))

        # Pick the docs search tool deterministically.
        target = None
        for name in registered:
            if "docs_search" in name:
                target = name
                break
        if target is None:
            target = registered[0]
        manifest = registry.manifest(target)
        log.info("calling %s (description=%r)",
                 target, (manifest.description or "")[:120])

        # Pick the right arg name based on the schema (the spec uses
        # 'query' for some tools, 'question' for others).
        arg_key = next(iter(manifest.args_schema), None)
        if arg_key is None:
            log.error("tool %s exposes no arguments — nothing to call", target)
            return 1
        kwargs = {arg_key: "Azure Functions Python triggers"}
        result = await registry.call(target, **kwargs)

        if not result.ok:
            log.error("call failed: %s (data=%r)", result.error, result.data)
            return 1
        text = (result.data or {}).get("text") or ""
        blocks = (result.data or {}).get("blocks") or []
        log.info("call OK in %.0f ms — %d block(s), text preview: %s",
                 result.latency_ms, len(blocks), text[:200].replace("\n", " "))
        log.info("meta: %s", result.meta)
        return 0
    finally:
        await bridge.aclose()
        log.info("bridge closed cleanly")


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
