"""Thin, explicit MCP client for the McDonald's China MCP server.

Deliberately narrow. Only read-only tools are exposed here; there is no
order-creation, cancellation, coupon-claiming or lottery-drawing helper
anywhere in this project, and `call()` refuses those tool names outright.
See README section "安全边界 / Safety boundaries".

Auth comes from the `MCD_MCP_TOKEN` environment variable (a `.env` file at the
repo root is read as a convenience). The token is never written to disk by this
module and never appears in any committed file.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_URL = "https://mcp.mcd.cn"
PROTOCOL_VERSION = "2025-06-18"
ROOT = Path(__file__).resolve().parent.parent.parent

#: Tool name fragments that must never be called by this project.
FORBIDDEN_FRAGMENTS = ("create", "cancel", "draw", "auto-bind", "bind")


class McpError(RuntimeError):
    pass


def load_dotenv(path: Path | None = None) -> None:
    """Load KEY=VALUE lines from .env without overwriting real env vars."""
    target = path or (ROOT / ".env")
    if not target.exists():
        return
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


class McpClient:
    def __init__(self, url: str | None = None, token: str | None = None) -> None:
        load_dotenv()
        self.url = url or os.environ.get("MCD_MCP_URL", DEFAULT_URL)
        self.token = token or os.environ.get("MCD_MCP_TOKEN", "")
        if not self.token:
            raise McpError("MCD_MCP_TOKEN 未设置（可写入仓库根目录的 .env）")
        self._session_id: str | None = None
        self._next_id = 0
        self._ready = False

    # ---- transport ---------------------------------------------------
    def _post(self, payload: dict) -> dict:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        request = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self._session_id = session
                content_type = (response.headers.get("Content-Type") or "").lower()
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:400]
            raise McpError(f"HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise McpError(f"无法连接 {self.url}: {exc.reason}") from exc
        return _decode(raw, content_type)

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._next_id += 1
        payload: dict = {"jsonrpc": "2.0", "id": self._next_id, "method": method}
        if params is not None:
            payload["params"] = params
        data = self._post(payload)
        if "error" in data:
            raise McpError(f"{method} 失败: {data['error']}")
        return data.get("result", {})

    def _notify(self, method: str) -> None:
        self._post({"jsonrpc": "2.0", "method": method})

    # ---- MCP surface --------------------------------------------------
    def initialize(self) -> dict:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcd-pickup-vs-delivery", "version": "0.1.0"},
            },
        )
        self._notify("notifications/initialized")
        self._ready = True
        return result

    def list_tools(self) -> list[dict]:
        if not self._ready:
            self.initialize()
        return self._rpc("tools/list").get("tools", [])

    def call(self, name: str, arguments: dict | None = None) -> dict:
        """Call one read-only tool and return its parsed business payload."""
        if any(fragment in name.lower() for fragment in FORBIDDEN_FRAGMENTS):
            raise McpError(
                f"拒绝调用写操作工具 {name!r}：本项目为只读分析工具，不会下单、领券或抽奖。"
            )
        if not self._ready:
            self.initialize()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        return _unwrap(result)


def _unwrap(result: dict) -> dict:
    """Prefer structuredContent; fall back to the JSON embedded in `content`."""
    structured = result.get("structuredContent")
    if isinstance(structured, dict) and structured:
        return structured
    text = "".join(chunk.get("text", "") for chunk in result.get("content", []))
    marker = "## Original Response"
    if marker in text:
        candidate = text.split(marker, 1)[1].strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}


def _decode(raw: str, content_type: str) -> dict:
    """Decode a plain JSON body or an SSE stream of JSON-RPC events."""
    text = raw.strip()
    if "text/event-stream" not in content_type and text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    events = []
    for block in text.split("\n\n"):
        lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if not lines:
            continue
        try:
            events.append(json.loads("\n".join(lines)))
        except json.JSONDecodeError:
            continue
    for event in reversed(events):
        if any(key in event for key in ("id", "result", "error")):
            return event
    return events[-1] if events else {}
