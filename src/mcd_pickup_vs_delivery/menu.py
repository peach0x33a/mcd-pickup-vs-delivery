"""Per-store menu and authoritative pricing, with an on-disk cache.

Menus are large (~100 items/store) and change slowly, so they are cached to
`data/menu_cache/<storeCode>.json`. That cache is what makes the offline demo
possible: a reviewer with no MCP token still gets a real comparison.

Prices shown to the user always come from ``calculate-price`` (official
authority). The menu is used only to (a) know which item codes a store sells
and (b) pre-screen stores before spending an authoritative call on them.
"""
from __future__ import annotations

import json
from pathlib import Path

from .mcp_client import McpClient
from .stores import ROOT

MENU_CACHE_DIR = ROOT / "data" / "menu_cache"

#: Mark-ups are compared in fen; anything closer than this is treated as a tie
#: so that floating-point noise never produces a fake saving.
PRICE_TIE_FEN = 1


def menu_cache_path(store_code: str) -> Path:
    return MENU_CACHE_DIR / f"{store_code}.json"


def load_menu(store_code: str) -> dict | None:
    """Cached menu for a store, or None when not collected yet."""
    path = menu_cache_path(store_code)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def save_menu(store_code: str, menu: dict) -> None:
    MENU_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    menu_cache_path(store_code).write_text(
        json.dumps(menu, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def fetch_menu(client: McpClient, store_code: str) -> dict:
    """Live menu for one store (take-away order type)."""
    payload = client.call("query-meals", {"storeCode": store_code, "orderType": 1, "beType": 1})
    return (payload.get("data") or {})


def extract_meals(menu: dict) -> dict[str, dict]:
    """Flatten the menu into {itemCode: {name, price, tags}}.

    The live shape is ``data.categories[].meals[].code`` (ordering) plus
    ``data.meals{code: {name, currentPrice, ...}}`` (details). Only the latter
    carries a name and a price, so we key off it and ignore the category list.
    """
    raw = (menu or {}).get("meals") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for code, item in raw.items():
        if not isinstance(item, dict):
            continue
        out[str(code)] = {
            "code": str(code),
            "name": item.get("name") or "",
            "price": _yuan(item.get("currentPrice")),
            "original": _yuan(item.get("originalPrice")),
            "discount": item.get("discountType"),
        }
    return out


def _yuan(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve_item(meals: dict[str, dict], query: str) -> tuple[str, str] | None:
    """Find the item code a store uses for a human query like "巨无霸".

    Exact name match wins; otherwise a unique substring match is accepted.
    Returns ``(code, name)`` or None when the store does not sell it.
    """
    query = query.strip()
    if not query:
        return None
    for code, item in meals.items():
        if item["name"] == query:
            return code, item["name"]
    partial = [(c, i["name"]) for c, i in meals.items() if query in i["name"]]
    if len(partial) == 1:
        return partial[0]
    if partial:
        # Prefer the shortest name: "巨无霸" beats "巨无霸三件套" for query "巨无霸".
        partial.sort(key=lambda pair: len(pair[1]))
        return partial[0]
    return None


def quote(client: McpClient, store_code: str, lines: list[tuple[str, int]]) -> int | None:
    """Authoritative total in fen for a basket, via ``calculate-price``.

    ``lines`` is a list of ``(productCode, quantity)``. Returns None when the
    server declines to price the basket (e.g. an item is unavailable there).
    """
    items = [{"productCode": code, "quantity": qty} for code, qty in lines if qty > 0]
    if not items:
        return None
    payload = client.call(
        "calculate-price",
        {"storeCode": store_code, "orderType": 1, "beType": 1, "items": items},
    )
    data = payload.get("data") or {}
    # The server reports integer fen under `price`; keep the strictest field
    # available and never silently mix units.
    for key in ("price", "totalPrice", "payAmount", "productPrice"):
        value = data.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    return None
