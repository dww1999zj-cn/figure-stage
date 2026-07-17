"""瑞幸咖啡官方 MCP（Streamable HTTP）客户端。

Endpoint: https://gwmcp.lkcoffee.com/order/user/mcp
每次 tools/call 独立 initialize → call → 结束，避免长会话兼容问题。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx

DEFAULT_MCP_URL = "https://gwmcp.lkcoffee.com/order/user/mcp"
PROTOCOL_VERSION = "2025-03-26"


class LuckinMcpError(RuntimeError):
    pass


def _truthy(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def luckin_enabled() -> bool:
    if not _truthy("LUCKIN_ENABLED"):
        return False
    return bool(luckin_token())


def luckin_token() -> str:
    return (
        os.environ.get("LUCKIN_TOKEN", "").strip()
        or os.environ.get("LUCKIN_MCP_TOKEN", "").strip()
    )


def luckin_mcp_url() -> str:
    return os.environ.get("LUCKIN_MCP_URL", DEFAULT_MCP_URL).strip() or DEFAULT_MCP_URL


def _parse_sse_or_json(text: str) -> dict[str, Any]:
    """Parse JSON body or SSE (data: {...}) into a JSON-RPC object."""
    text = (text or "").strip()
    if not text:
        raise LuckinMcpError("空响应")

    if text.startswith("{") or text.startswith("["):
        data = json.loads(text)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and ("result" in item or "error" in item):
                    return item
            return data[0] if data else {}
        return data

    # SSE: take last JSON payload from data: lines
    payloads: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            payloads.append(obj)
    if not payloads:
        raise LuckinMcpError(f"无法解析 SSE 响应: {text[:300]}")
    for obj in reversed(payloads):
        if "result" in obj or "error" in obj:
            return obj
    return payloads[-1]


def _extract_tool_payload(rpc: dict[str, Any]) -> Any:
    if "error" in rpc and rpc["error"]:
        err = rpc["error"]
        if isinstance(err, dict):
            raise LuckinMcpError(err.get("message") or json.dumps(err, ensure_ascii=False))
        raise LuckinMcpError(str(err))

    result = rpc.get("result")
    if result is None:
        raise LuckinMcpError(f"无 result: {json.dumps(rpc, ensure_ascii=False)[:400]}")

    if isinstance(result, dict) and result.get("isError"):
        content = result.get("content") or []
        msg = _content_to_text(content) or "工具返回 isError"
        raise LuckinMcpError(msg)

    if isinstance(result, dict) and "content" in result:
        text = _content_to_text(result["content"])
        if not text:
            return result
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    return result


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict):
            if item.get("type") == "text" and "text" in item:
                parts.append(str(item["text"]))
            elif "text" in item:
                parts.append(str(item["text"]))
        elif isinstance(item, str):
            parts.append(item)
    return "\n".join(parts).strip()


class LuckinMcpClient:
    """Minimal Streamable HTTP MCP client for Luckin order tools."""

    def __init__(
        self,
        token: str | None = None,
        url: str | None = None,
        timeout: float = 45.0,
    ) -> None:
        self.token = (token if token is not None else luckin_token()).strip()
        self.url = (url if url is not None else luckin_mcp_url()).strip()
        self.timeout = timeout
        if not self.token:
            raise LuckinMcpError("未设置 LUCKIN_TOKEN / LUCKIN_MCP_TOKEN")

    def _headers(self, session_id: str | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": PROTOCOL_VERSION,
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        return headers

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """One-shot: initialize → notifications/initialized → tools/call.

        瑞幸网关 initialize 常不返回 Mcp-Session-Id，仍可直接 tools/call。
        """
        arguments = arguments or {}
        with httpx.Client(timeout=self.timeout) as client:
            session_id = self._initialize(client)
            self._notify_initialized(client, session_id)
            return self._tools_call(client, session_id, name, arguments)

    def _post(
        self,
        client: httpx.Client,
        body: dict[str, Any],
        session_id: str | None = None,
    ) -> tuple[httpx.Response, dict[str, Any] | None]:
        resp = client.post(self.url, headers=self._headers(session_id), json=body)
        if resp.status_code >= 400:
            raise LuckinMcpError(f"HTTP {resp.status_code}: {resp.text[:500]}")
        # notifications may return 202 with empty body
        if not resp.content or not resp.text.strip():
            return resp, None
        return resp, _parse_sse_or_json(resp.text)

    def _initialize(self, client: httpx.Client) -> str | None:
        # Keep capabilities minimal — Luckin Java MCP SDK rejects unknown fields.
        body = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "figure-stage", "version": "1.0.0"},
            },
        }
        resp, rpc = self._post(client, body)
        if rpc and "error" in rpc and rpc["error"]:
            raise LuckinMcpError(f"initialize 失败: {rpc['error']}")
        if rpc is None or "result" not in (rpc or {}):
            raise LuckinMcpError("initialize 无有效 result")
        session_id = resp.headers.get("mcp-session-id") or resp.headers.get("Mcp-Session-Id")
        if not session_id and isinstance(rpc.get("result"), dict):
            session_id = rpc["result"].get("sessionId") or rpc["result"].get("session_id")
        # Luckin may omit session id; callers proceed without it.
        return session_id or None

    def _notify_initialized(self, client: httpx.Client, session_id: str | None) -> None:
        body = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        self._post(client, body, session_id=session_id)

    def _tools_call(
        self,
        client: httpx.Client,
        session_id: str | None,
        name: str,
        arguments: dict[str, Any],
    ) -> Any:
        body = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        _, rpc = self._post(client, body, session_id=session_id)
        if rpc is None:
            raise LuckinMcpError(f"tools/call {name} 空响应")
        return _extract_tool_payload(rpc)

    # ---- typed helpers ----

    def query_shop_list(
        self,
        longitude: float,
        latitude: float,
        dept_name: str | None = None,
    ) -> Any:
        args: dict[str, Any] = {"longitude": longitude, "latitude": latitude}
        if dept_name:
            args["deptName"] = dept_name
        return self.call_tool("queryShopList", args)

    def search_product(self, dept_id: int, query: str) -> Any:
        return self.call_tool(
            "searchProductForMcp",
            {"deptId": int(dept_id), "query": query},
        )

    def query_product_detail(self, dept_id: int, product_id: int) -> Any:
        return self.call_tool(
            "queryProductDetailInfo",
            {"deptId": int(dept_id), "productId": int(product_id)},
        )

    def preview_order(self, dept_id: int, product_list: list[dict[str, Any]]) -> Any:
        return self.call_tool(
            "previewOrder",
            {"deptId": int(dept_id), "productList": product_list},
        )

    def create_order(
        self,
        dept_id: int,
        product_list: list[dict[str, Any]],
        longitude: float,
        latitude: float,
        coupon_code_list: list[str] | None = None,
    ) -> Any:
        args: dict[str, Any] = {
            "deptId": int(dept_id),
            "productList": product_list,
            "longitude": longitude,
            "latitude": latitude,
        }
        if coupon_code_list:
            args["couponCodeList"] = coupon_code_list
        return self.call_tool("createOrder", args)

    def query_order_detail(self, order_id: str) -> Any:
        return self.call_tool("queryOrderDetailInfo", {"orderId": str(order_id)})

    def cancel_order(self, order_id: str) -> Any:
        return self.call_tool("cancelOrder", {"orderId": str(order_id)})


def unwrap_list(data: Any, *keys: str) -> list[Any]:
    """Best-effort extract a list from nested luckin responses."""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in keys:
        val = data.get(key)
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            for nested in ("list", "records", "items", "shopList", "productList", "data"):
                if isinstance(val.get(nested), list):
                    return val[nested]
    for nested in ("data", "list", "records", "items", "shopList", "productList", "result"):
        val = data.get(nested)
        if isinstance(val, list):
            return val
        if isinstance(val, dict):
            found = unwrap_list(val, *keys)
            if found:
                return found
    return []


def dig(data: Any, *path: str, default: Any = None) -> Any:
    cur = data
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


_PRICE_RE = re.compile(r"(\d+(?:\.\d+)?)")


def as_price(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return f"{float(value):.2f}".rstrip("0").rstrip(".")
    text = str(value)
    m = _PRICE_RE.search(text)
    return m.group(1) if m else text
