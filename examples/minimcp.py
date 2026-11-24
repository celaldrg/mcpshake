"""A tiny stdio MCP server loop for the examples (no SDK, no dependencies).

Handlers raise ValueError for bad input (-> isError result). Any other
exception becomes a JSON-RPC -32603 error, with the traceback in the message,
which is exactly the kind of thing mcpshake is meant to catch.
"""

import json
import sys
import traceback


def serve(name, version, tools, handlers, leak_traces=True):
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if "id" not in msg:
            continue  # notification
        rid, method, params = msg["id"], msg.get("method"), msg.get("params") or {}
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion", "2025-11-25"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": name, "version": version},
                }
            elif method == "tools/list":
                result = {"tools": tools}
            elif method == "ping":
                result = {}
            elif method == "tools/call":
                fn = handlers.get(params.get("name"))
                if fn is None:
                    raise LookupError("unknown tool")
                try:
                    result = fn(**(params.get("arguments") or {}))
                except ValueError as e:
                    result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
            else:
                reply = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}}
                print(json.dumps(reply), flush=True)
                continue
            reply = {"jsonrpc": "2.0", "id": rid, "result": result}
        except Exception as e:  # noqa: BLE001
            text = traceback.format_exc() if leak_traces else f"internal error: {type(e).__name__}"
            reply = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": text}}
        print(json.dumps(reply), flush=True)


def text(s, structured=None):
    out = {"content": [{"type": "text", "text": s}]}
    if structured is not None:
        out["structuredContent"] = structured
    return out
