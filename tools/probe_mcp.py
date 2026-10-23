#!/usr/bin/env python3
"""Minimal MCP (Streamable HTTP) client for probing the McDonald's MCP server.

READ-ONLY BY DESIGN: this module exposes only `list_tools()` and `call_tool()`.
There is no order-creation helper anywhere in this project. See README "安全边界".

Usage:
    python3 tools/probe_mcp.py --list
    python3 tools/probe_mcp.py --call delivery-query-stores --args '{"...":"..."}'
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_URL = "https://mcp.mcd.cn"
PROTOCOL_VERSION = "2025-06-18"


def load_env(path: Path = ROOT / ".env") -> None:
    """Load KEY=VALUE pairs from .env without touching the real environment badly."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


class McpError(RuntimeError):
    pass


class McpClient:
    """Tiny Streamable-HTTP MCP client. No third-party dependencies."""

    def __init__(self, url: str | None = None, token: str | None = None) -> None:
        load_env()
        self.url = url or os.environ.get("MCD_MCP_URL", DEFAULT_URL)
        self.token = token or os.environ.get("MCD_MCP_TOKEN", "")
        if not self.token:
            raise McpError("MCD_MCP_TOKEN is missing (expected in .env)")
        self._session_id: str | None = None
        self._next_id = 0
        self._initialized = False

    # ---- transport -------------------------------------------------
    def _post(self, payload: dict) -> tuple[str, dict]:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self._session_id = sid
                ctype = (resp.headers.get("Content-Type") or "").lower()
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise McpError(f"HTTP {exc.code} from {self.url}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise McpError(f"cannot reach {self.url}: {exc.reason}") from exc
        return ctype, _decode_body(raw, ctype)

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        _, data = self._post(payload)
        if not isinstance(data, dict):
            raise McpError(f"unexpected response for {method}: {data!r}")
        if "error" in data:
            raise McpError(f"{method} failed: {data['error']}")
        return data.get("result", {})

    def _notify(self, method: str, params: dict | None = None) -> None:
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        self._post(payload)

    # ---- MCP surface ------------------------------------------------
    def initialize(self) -> dict:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcd-pickup-vs-delivery-probe", "version": "0.1.0"},
            },
        )
        self._notify("notifications/initialized")
        self._initialized = True
        return result

    def list_tools(self) -> list[dict]:
        if not self._initialized:
            self.initialize()
        result = self._rpc("tools/list")
        return result.get("tools", [])

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        if not self._initialized:
            self.initialize()
        return self._rpc("tools/call", {"name": name, "arguments": arguments or {}})


def _decode_body(raw: str, content_type: str) -> dict:
    """Decode either a plain JSON body or an SSE stream carrying JSON-RPC events."""
    text = raw.strip()
    if "text/event-stream" not in content_type and text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    events = []
    for block in text.split("\n\n"):
        data_lines = [ln[5:].lstrip() for ln in block.splitlines() if ln.startswith("data:")]
        if not data_lines:
            continue
        chunk = "\n".join(data_lines).strip()
        if not chunk:
            continue
        try:
            events.append(json.loads(chunk))
        except json.JSONDecodeError:
            continue
    for event in reversed(events):
        if "id" in event or "result" in event or "error" in event:
            return event
    return events[-1] if events else {}


def _summarise_schema(tool: dict) -> str:
    schema = tool.get("inputSchema") or {}
    props = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    parts = []
    for key, spec in props.items():
        typ = spec.get("type", "?")
        desc = (spec.get("description") or "").strip().replace("\n", " ")
        if len(desc) > 90:
            desc = desc[:90] + "…"
        marker = "*" if key in required else ""
        parts.append(f"{key}{marker}:{typ}" + (f"  # {desc}" if desc else ""))
    return "\n      ".join(parts) if parts else "(no properties)"


def main() -> int:
    ap = argparse.ArgumentParser(description="Read-only probe for the McDonald's MCP server")
    ap.add_argument("--list", action="store_true", help="list every tool with its input schema")
    ap.add_argument("--filter", default="", help="only show tools whose name contains this")
    ap.add_argument("--json", action="store_true", help="emit raw JSON instead of a summary")
    ap.add_argument("--call", default="", help="call ONE tool (read-only tools only)")
    ap.add_argument("--args", default="{}", help="JSON arguments for --call")
    args = ap.parse_args()

    client = McpClient()
    try:
        if args.call:
            if any(word in args.call for word in ("create", "draw", "cancel", "auto-bind")):
                print(f"refusing to call write-capable tool: {args.call}", file=sys.stderr)
                return 2
            result = client.call_tool(args.call, json.loads(args.args))
            print(json.dumps(result, ensure_ascii=False, indent=2)[:6000])
            return 0

        info = client.initialize()
        server = (info.get("serverInfo") or {}).get("name", "?")
        version = (info.get("serverInfo") or {}).get("version", "?")
        tools = client.list_tools()
        if args.filter:
            tools = [t for t in tools if args.filter in t["name"]]
        if args.json:
            print(json.dumps(tools, ensure_ascii=False, indent=2))
            return 0

        print(f"server: {server} v{version}")
        print(f"protocol: {info.get('protocolVersion')}")
        print(f"tools: {len(tools)}\n")
        for tool in tools:
            print(f"  {tool['name']}")
            print(f"      {(tool.get('description') or '').strip().splitlines()[0][:160]}")
            print(f"      {_summarise_schema(tool)}")
            print()
        return 0
    except McpError as exc:
        print(f"MCP error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
