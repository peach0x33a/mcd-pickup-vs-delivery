#!/usr/bin/env python3
"""解析同一样商品在「自提」和「外送」两条渠道各自的商品编码。

为什么需要这个工具：麦当劳对同一个商品在两个渠道使用**不同的商品编码**，
实测存在三种关系（三家门店一致）：

    关系                      例子
    ────────────────────────  ────────────────────────
    外送编码 = "90" + 自提编码   巨无霸 1100 -> 901100
    两边编码无关，仅名字相同     安格斯堡 511781 -> 902060
    两边编码恰好相同            少数商品

因此**绝不能拿一条渠道的编码去查另一条渠道**。用错了不会报错，而是会拿到
一条 `productPrice = 0` 的假报价（只剩配送费），看起来像"外送反而更便宜"。

用法：
    python3 resolve_channels.py --want 巨无霸
    python3 resolve_channels.py --want 巨无霸 锡兰红茶 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mcd_pickup_vs_delivery.mcp_client import McpClient, McpError  # noqa: E402

#: 外送渠道编码相对自提编码的常见前缀。
DELIVERY_PREFIX = "90"


def _meals(client: McpClient, store: str, order_type: int, be_type: int, be_code: str = "") -> dict:
    args = {"storeCode": store, "orderType": order_type, "beType": be_type}
    if be_code:
        args["beCode"] = be_code
    return ((client.call("query-meals", args).get("data") or {}).get("meals")) or {}


def find_by_name(meals: dict, name: str) -> tuple[str, str] | None:
    """按名字找编码：先精确，再子串（取最短名，避免"巨无霸"匹配到"巨无霸三件套"）。"""
    for code, item in meals.items():
        if (item.get("name") or "") == name:
            return code, item["name"]
    partial = [(code, item.get("name") or "") for code, item in meals.items()
               if name in (item.get("name") or "")]
    if not partial:
        return None
    partial.sort(key=lambda pair: len(pair[1]))
    return partial[0]


def resolve(self_menu: dict, dlv_menu: dict, want: str) -> dict:
    """返回同一样商品在两条渠道的编码、名称、标价与匹配方式。

    ``match`` 字段说明这个结果是靠什么对上的，调用方据此判断可信度：
      - ``name``            名字直接匹配（最可靠）
      - ``prefix``          靠 "90" 前缀关系推定（需复核）
      - ``pickup-only``     外送渠道确实没有这件商品
      - ``delivery-only``   只有外送有，自提没有
      - ``missing``         两条渠道都没有
    """
    hit_self = find_by_name(self_menu, want)
    hit_dlv = find_by_name(dlv_menu, want)
    how = "name"

    if hit_self and not hit_dlv:
        prefixed = DELIVERY_PREFIX + hit_self[0]
        if prefixed in dlv_menu:
            hit_dlv = (prefixed, dlv_menu[prefixed].get("name") or want)
            how = "prefix"

    if hit_dlv and not hit_self and hit_dlv[0].startswith(DELIVERY_PREFIX):
        plain = hit_dlv[0][len(DELIVERY_PREFIX):]
        if plain in self_menu:
            hit_self = (plain, self_menu[plain].get("name") or want)
            how = "prefix"

    if not hit_self and not hit_dlv:
        how = "missing"
    elif hit_self and not hit_dlv:
        how = "pickup-only"
    elif hit_dlv and not hit_self:
        how = "delivery-only"

    def price(menu: dict, hit) -> float | None:
        if not hit:
            return None
        try:
            return float(menu[hit[0]].get("currentPrice"))
        except (TypeError, ValueError):
            return None

    return {
        "query": want,
        "name": (hit_self[1] if hit_self else (hit_dlv[1] if hit_dlv else want)),
        "pickup_code": hit_self[0] if hit_self else None,
        "delivery_code": hit_dlv[0] if hit_dlv else None,
        "pickup_price": price(self_menu, hit_self),
        "delivery_price": price(dlv_menu, hit_dlv),
        "match": how,
    }


def store_channels(client: McpClient, index: int = 0) -> tuple[str, str, str]:
    """返回 (storeCode, beCode, storeName) 供调用方复用。"""
    addresses = (client.call("delivery-query-addresses", {}).get("data") or {}).get("addresses") or []
    if not addresses:
        raise McpError("账号里没有配送地址，无法解析外送渠道编码。")
    stores = client.call("delivery-query-stores",
                         {"addressId": addresses[0]["addressId"], "beType": 2}).get("data") or []
    if not stores:
        raise McpError("该地址没有可配送门店。")
    store = stores[index]
    return str(store.get("storeCode")), str(store.get("beCode")), store.get("storeName") or ""


def main() -> int:
    parser = argparse.ArgumentParser(description="解析商品在两条渠道的编码")
    parser.add_argument("--want", nargs="+", required=True)
    parser.add_argument("--store", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    try:
        client = McpClient()
        code, be_code, name = store_channels(client, args.store)
        self_menu = _meals(client, code, 1, 1)
        dlv_menu = _meals(client, code, 2, 2, be_code)
    except McpError as exc:
        print(f"MCP 错误：{exc}", file=sys.stderr)
        return 1

    rows = [resolve(self_menu, dlv_menu, want) for want in args.want]
    if args.json:
        print(json.dumps({"store": name, "items": rows}, ensure_ascii=False, indent=2))
        return 0

    print(f"\n  {name}：自提 {len(self_menu)} 项 / 外送 {len(dlv_menu)} 项\n")
    print(f"  {'商品':<20}{'自提编码':>14}{'外送编码':>14}{'自提价':>9}{'外送价':>9}{'匹配方式':>12}")
    print("  " + "-" * 78)
    for row in rows:
        pickup_code = row["pickup_code"] or "—"
        delivery_code = row["delivery_code"] or "—"
        pickup_price = f"¥{row['pickup_price']:.2f}" if row["pickup_price"] is not None else "—"
        delivery_price = f"¥{row['delivery_price']:.2f}" if row["delivery_price"] is not None else "—"
        print(f"  {row['name'][:18]:<20}{pickup_code:>14}{delivery_code:>14}"
              f"{pickup_price:>9}{delivery_price:>9}{row['match']:>12}")
    print()
    notes = {
        "prefix": "靠 90 前缀推定，建议核对名称",
        "pickup-only": "外送渠道没有这件商品",
        "delivery-only": "只有外送有",
        "missing": "两条渠道都没有，需换关键词",
    }
    for row in rows:
        if row["match"] in notes:
            print(f"  ⚠ {row['query']}：{notes[row['match']]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
