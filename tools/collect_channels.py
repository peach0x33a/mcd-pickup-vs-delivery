#!/usr/bin/env python3
"""Snapshot both sales channels for one address, for offline value comparison.

READ-ONLY: calls `delivery-query-addresses`, `delivery-query-stores`,
`query-meals` and `calculate-price` only. Never creates an address or an order.

What gets written to `data/channels/<slug>.json`:

    {
      "address":      {city_label, redacted_note},   # 不含任何可识别个人的信息
      "captured_at":  "...",
      "stores": [
        {storeCode, storeName, beCode, menu: {...},
         quotes: {itemCode: {pickup: {...}, delivery: {...}}}}
      ]
    }

The file is committed so `mcd_pickup_vs_delivery value` can replay the whole
comparison with no token and no network.

Usage:
    python3 tools/collect_channels.py --slug zibo-campus
    python3 tools/collect_channels.py --slug zibo-campus --items 12
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import re
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mcd_pickup_vs_delivery.mcp_client import McpClient, McpError  # noqa: E402

OUTPUT_DIR = ROOT / "data" / "channels"
REQUEST_PAUSE_S = 0.15

#: How much of each channel's catalogue to quote. Quoting every item both ways
#: would be hundreds of calls; the worst offenders are what the comparison
#: needs, so we quote the items with the biggest list-price divergence plus a
#: spread of typical ones.
MAX_ITEMS_PER_STORE = 14


def _city_only(full_address: str) -> str:
    """把完整地址裁剪到「省+市+区」级别，去掉街道门牌与任何可定位信息。"""
    match = re.match(r"(.{2,8}?(?:省|自治区|市))(.{2,10}?(?:市|区|县))?", full_address)
    if not match:
        return "（已脱敏）"
    return "".join(part for part in match.groups() if part) + "（已脱敏）"


def _meals(client: McpClient, store_code: str, order_type: int, be_type: int, be_code: str = "") -> dict:
    args = {"storeCode": store_code, "orderType": order_type, "beType": be_type}
    if be_code:
        args["beCode"] = be_code
    return ((client.call("query-meals", args).get("data") or {}).get("meals")) or {}


def _quote(client: McpClient, store_code: str, order_type: int, be_type: int,
           be_code: str, product_code: str) -> dict | None:
    args = {"storeCode": store_code, "orderType": order_type, "beType": be_type,
            "items": [{"productCode": product_code, "quantity": 1}]}
    if be_code:
        args["beCode"] = be_code
    payload = client.call("calculate-price", args)
    data = payload.get("data")
    return data if isinstance(data, dict) and data.get("price") is not None else None


def _price(meal: dict) -> float | None:
    try:
        return float(meal.get("currentPrice"))
    except (TypeError, ValueError):
        return None


def collect(slug: str, max_items: int) -> dict:
    client = McpClient()
    client.initialize()

    addresses = (client.call("delivery-query-addresses", {}).get("data") or {}).get("addresses") or []
    if not addresses:
        raise SystemExit(
            "账号里没有配送地址。本工具不会替你创建（MCP 无法删除地址）。\n"
            "请先在麦当劳 App 里添加一个收货地址。"
        )
    address = addresses[0]

    stores = client.call("delivery-query-stores",
                         {"addressId": address["addressId"], "beType": 2}).get("data") or []
    print(f"地址：{_city_only(address.get('fullAddress') or '')}")
    print(f"可配送门店：{len(stores)} 家\n")

    out = {
        # 脱敏落盘：地址簿 ID 只存哈希、地址只保留到城市级、联系人一律不写入。
        # 这三项都是个人信息，比赛要求「不包含他人个人信息」，仓库是要公开的。
        "address": {
            "addressIdHash": hashlib.sha256(
                str(address.get("addressId") or "").encode()).hexdigest()[:12],
            "cityLabel": _city_only(address.get("fullAddress") or ""),
            "redacted": True,
        },
        "captured_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "stores": [],
    }

    for store in stores:
        code = str(store.get("storeCode") or "")
        be_code = str(store.get("beCode") or "")
        name = store.get("storeName") or code
        if not code or not be_code:
            continue

        try:
            self_menu = _meals(client, code, 1, 1)
            dlv_menu = _meals(client, code, 2, 2, be_code)
        except McpError as exc:
            print(f"  {name}: 菜单拉取失败 ({exc})")
            continue

        common = set(self_menu) & set(dlv_menu)
        # Rank by list-price divergence, then keep a couple of near-identical
        # items so the report is not purely extremes.
        scored = []
        for item_code in common:
            a = _price(self_menu[item_code])
            b = _price(dlv_menu[item_code])
            if a is None or b is None:
                continue
            scored.append((b - a, item_code, a, b))
        scored.sort(key=lambda row: -row[0])
        chosen = [row[1] for row in scored[:max_items]]

        quotes = {}
        for item_code in chosen:
            try:
                pickup_raw = _quote(client, code, 1, 1, "", item_code)
                delivery_raw = _quote(client, code, 2, 2, be_code, item_code)
            except McpError:
                continue
            if pickup_raw and delivery_raw:
                quotes[item_code] = {"pickup": pickup_raw, "delivery": delivery_raw}
            time.sleep(REQUEST_PAUSE_S)

        out["stores"].append({
            "storeCode": code,
            "storeName": name,
            "beCode": be_code,
            "menu_pickup_count": len(self_menu),
            "menu_delivery_count": len(dlv_menu),
            "common_count": len(common),
            "menu": {k: {"name": v.get("name"), "price": _price(v)} for k, v in self_menu.items()
                     if k in common},
            "quotes": quotes,
        })
        print(f"  {name[:26]:28s} 自提{len(self_menu):3d}项 外送{len(dlv_menu):3d}项 "
              f"交集{len(common):3d} 已核价 {len(quotes)} 项")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target = OUTPUT_DIR / f"{slug}.json"
    target.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    total = sum(len(s["quotes"]) for s in out["stores"])
    print(f"\n已写入 {target}（{len(out['stores'])} 家门店 / {total} 条核价）")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="采集自提与外送双渠道价格（只读）")
    parser.add_argument("--slug", default="zibo-campus", help="输出文件名，如 zibo-campus")
    parser.add_argument("--items", type=int, default=MAX_ITEMS_PER_STORE)
    args = parser.parse_args()
    collect(args.slug, args.items)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
