"""One-shot probe: connect to the Microsoft Learn MCP server, list tools
and call one, so we can verify wire-level behaviour before writing the
bridge. NOT a test — run manually: ``.venv/bin/python scripts/_probe_mslearn_mcp.py``.
"""
from __future__ import annotations

import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


URL = "https://learn.microsoft.com/api/mcp"


async def main() -> None:
    async with streamablehttp_client(URL) as (read, write, _get_session_id):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("server info:", init.serverInfo)
            print("capabilities:", init.capabilities)
            tools_resp = await session.list_tools()
            for t in tools_resp.tools:
                print("---")
                print("name:", t.name)
                print("desc:", (t.description or "")[:160])
                print("schema:", json.dumps(t.inputSchema, indent=2)[:600])
            # Try a simple search call against the first tool that looks
            # like a docs search.
            target = None
            for t in tools_resp.tools:
                if "search" in t.name.lower() and "doc" in t.name.lower():
                    target = t.name
                    break
            if target is None and tools_resp.tools:
                target = tools_resp.tools[0].name
            if target is not None:
                print(f"\ncalling {target} with query='Azure Functions Python triggers'")
                res = await session.call_tool(
                    target, {"question": "Azure Functions Python triggers"}
                )
                print("isError:", res.isError)
                for blk in (res.content or [])[:1]:
                    blob = getattr(blk, "text", None) or repr(blk)
                    print("first block (truncated):", blob[:400])


if __name__ == "__main__":
    asyncio.run(main())
