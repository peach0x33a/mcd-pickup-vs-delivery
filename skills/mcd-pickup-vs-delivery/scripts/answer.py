#!/usr/bin/env python3
"""输出自提 / 外送的全部硬数据，交给调用方 AI 判断。

设计原则（本 Skill 的架构核心）：

    Skill 负责「取数 + 算准」     →  输出结构化 JSON
    调用方 AI 负责「判断 + 表达」  →  决定推荐哪种方式、怎么讲给用户

所以本脚本**不做推荐、不下结论、不替用户选出行方式**。它只提供：

  · 两条渠道的官方实付金额（calculate-price，单位分）
  · 差额的三项拆解（商品溢价 / 配送费 / 包装费），并校验能否还原总差额
  · 两条渠道各自的商品编码（同一商品两边编码不同，必须分别解析）
  · 各出行方式的距离、时长、费用（每条数据都标明来源与是否估算）
  · 按用户时薪换算的时间成本

调用方拿到数字后自行决定怎么说。判断规则写在 SKILL.md 里，不在代码里——
同一个数字在不同用户情境下该给出不同建议，那是 AI 该判断的事。

用法：
    python3 answer.py --want 巨无霸 --at 36.8076,118.0256            # JSON
    python3 answer.py --want 巨无霸 --at 36.8076,118.0256 --format text
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from resolve_channels import resolve  # noqa: E402

from mcd_pickup_vs_delivery.amap import enabled as amap_enabled  # noqa: E402
from mcd_pickup_vs_delivery.geo import LatLng, haversine_m  # noqa: E402
from mcd_pickup_vs_delivery.mcp_client import McpClient, McpError  # noqa: E402
from mcd_pickup_vs_delivery.multimodal import (  # noqa: E402
    SHORT_TRIP_M, WALK_FIRST_M, estimate_legs, legs_from_amap,
)

DEFAULT_HOURLY_RATE = 30.0

_STORE_COORDS: dict[str, tuple[float, float]] = {}


def _coords_for(store_code: str) -> tuple[float, float] | None:
    """门店坐标。来自仓库内快照，不额外调接口。"""
    if not _STORE_COORDS:
        try:
            from mcd_pickup_vs_delivery.stores import load_stores
            for store in load_stores():
                _STORE_COORDS[store.code] = (store.lat, store.lng)
        except Exception:
            return None
    return _STORE_COORDS.get(store_code)


def _meals(client: McpClient, store: str, order_type: int, be_type: int,
           be_code: str = "") -> dict:
    args = {"storeCode": store, "orderType": order_type, "beType": be_type}
    if be_code:
        args["beCode"] = be_code
    return ((client.call("query-meals", args).get("data") or {}).get("meals")) or {}


def _quote(client: McpClient, store: str, order_type: int, be_type: int,
           be_code: str, lines: list[tuple[str, int]]) -> dict | None:
    args = {"storeCode": store, "orderType": order_type, "beType": be_type,
            "items": [{"productCode": code, "quantity": qty} for code, qty in lines]}
    if be_code:
        args["beCode"] = be_code
    data = client.call("calculate-price", args).get("data") or {}
    return data if isinstance(data.get("price"), (int, float)) else None


def collect(wants: list[str], at: LatLng | None, hourly_rate: float,
            store_index: int = 0) -> dict:
    client = McpClient()

    addresses = (client.call("delivery-query-addresses", {}).get("data") or {}).get("addresses") or []
    result: dict = {
        "query": {
            "wants": wants,
            "hourly_rate": hourly_rate,
            "origin": {"lat": at.lat, "lng": at.lng} if at else None,
        },
        "address": None,
        "stores": [],
        "notes": [],
        "data_sources": {
            "amounts": "calculate-price（官方，单位：分）",
            "fare_estimates": "打车/公交/驾车费用为估算，非实测价格",
            "leg_duration": "高德已配置时为真实路况时长，否则按典型速度估算",
            "hourly_rate": "用户可调假设，非实测工资",
        },
    }
    if not addresses:
        result["notes"].append("账号没有配送地址，只能给自提数据。")
        return result

    address = addresses[0]
    result["address"] = {
        "fullAddress": address.get("fullAddress"),
        "city": address.get("city"),
    }
    stores = client.call("delivery-query-stores",
                         {"addressId": address["addressId"], "beType": 2}).get("data") or []
    if not stores:
        result["notes"].append("该地址不在任何门店配送范围内，只能自提。")
        return result

    store = stores[min(store_index, len(stores) - 1)]
    store_code = str(store.get("storeCode"))
    be_code = str(store.get("beCode"))

    self_menu = _meals(client, store_code, 1, 1)
    dlv_menu = _meals(client, store_code, 2, 2, be_code)

    # 逐商品解析两条渠道的编码：同一商品两边编码不同，见 SKILL.md
    items = []
    pickup_lines: list[tuple[str, int]] = []
    delivery_lines: list[tuple[str, int]] = []
    for want in wants:
        row = resolve(self_menu, dlv_menu, want)
        items.append(row)
        if row["pickup_code"]:
            pickup_lines.append((row["pickup_code"], 1))
        if row["delivery_code"]:
            delivery_lines.append((row["delivery_code"], 1))

    pickup_quote = _quote(client, store_code, 1, 1, "", pickup_lines) if pickup_lines else None

    # 外送只有在每一样商品都有外送编码时才报价。
    # 否则会得到一个缺项的假总价，看起来"外送更便宜"。
    delivery_quote = None
    if delivery_lines and len(delivery_lines) == len(items):
        delivery_quote = _quote(client, store_code, 2, 2, be_code, delivery_lines)
        if delivery_quote and (delivery_quote.get("productPrice") or 0) <= 0:
            delivery_quote = None
            result["notes"].append("外送渠道对这些商品无货，已放弃外送报价。")

    entry: dict = {
        "storeCode": store_code,
        "storeName": store.get("storeName"),
        "menu_sizes": {"pickup": len(self_menu), "delivery": len(dlv_menu)},
        "items": items,
    }

    if pickup_quote:
        entry["pickup"] = {
            "total_fen": int(pickup_quote["price"]),
            "product_fen": int(pickup_quote.get("productPrice") or 0),
            "delivery_fen": int(pickup_quote.get("deliveryPrice") or 0),
            "packing_fen": int(pickup_quote.get("packingPrice") or 0),
        }
    if delivery_quote:
        entry["delivery"] = {
            "total_fen": int(delivery_quote["price"]),
            "product_fen": int(delivery_quote.get("productPrice") or 0),
            "delivery_fen": int(delivery_quote.get("deliveryPrice") or 0),
            "packing_fen": int(delivery_quote.get("packingPrice") or 0),
        }

    # 差额拆解：三项必须能还原总差额，否则数据不自洽，调用方应提高警惕
    if entry.get("pickup") and entry.get("delivery"):
        pk, dl = entry["pickup"], entry["delivery"]
        gap = dl["total_fen"] - pk["total_fen"]
        product_markup = dl["product_fen"] - pk["product_fen"]
        entry["gap"] = {
            "total_fen": gap,
            "product_markup_fen": product_markup,
            "delivery_fee_fen": dl["delivery_fen"],
            "packing_fee_fen": dl["packing_fen"],
            "reconciles": product_markup + dl["delivery_fen"] + dl["packing_fen"] == gap,
        }

    # ---- 出行方式数据 ------------------------------------------------
    if at:
        coords = _coords_for(store_code)
        if coords:
            origin, dest = at, LatLng(*coords)
            distance = haversine_m(origin, dest)
            legs = None
            if amap_enabled():
                try:
                    from mcd_pickup_vs_delivery.amap import AmapClient
                    legs = legs_from_amap(AmapClient(), origin, dest,
                                          address.get("city") or "")
                    entry["legs_source"] = "amap-duration+estimated-fare"
                except Exception:
                    legs = None
            if legs is None:
                legs = estimate_legs(distance)
                entry["legs_source"] = "great-circle+estimated-duration"

            entry["distance_m"] = round(distance)
            entry["legs"] = [
                {
                    "mode": leg.mode,
                    "label": leg.label,
                    "distance_m": round(leg.distance_m),
                    "minutes": round(leg.minutes, 1),
                    "fare_yuan": round(leg.cost_yuan, 2),
                    "fare_estimated": True,
                    "fare_basis": leg.note,
                    "time_cost_yuan": round(leg.minutes * hourly_rate / 60.0, 2),
                    "total_cost_yuan": round(
                        leg.cost_yuan + leg.minutes * hourly_rate / 60.0, 2),
                    "note": leg.note,
                }
                for leg in legs
            ]
            entry["short_trip_note"] = {
                "walk_recommended_under_m": WALK_FIRST_M,
                "motorized_unrealistic_under_m": SHORT_TRIP_M,
                "meaning": "短距离下公交/打车在数学上可能更便宜，但现实不可行；调用方应据此判断。",
            }
        else:
            result["notes"].append(f"门店 {store_code} 无坐标快照，无法计算距离。")

    result["stores"].append(entry)
    return result


def render_text(payload: dict) -> str:
    """人类可读版本：只陈述数据，不给推荐。"""
    lines: list[str] = [""]
    query = payload["query"]
    if payload.get("address"):
        lines.append(f"  收货地址：{payload['address'].get('fullAddress') or '—'}")
    if query.get("origin"):
        lines.append(f"  你的位置：{query['origin']['lat']:.6f},{query['origin']['lng']:.6f}")
    lines.append(f"  时薪假设：¥{query['hourly_rate']:.0f}/小时")
    lines.append("")

    for entry in payload["stores"]:
        lines.append(f"  {entry['storeName']}")
        lines.append("")
        pk, dl = entry.get("pickup"), entry.get("delivery")
        if pk:
            lines.append(f"  自提实付   ¥{pk['total_fen'] / 100:.2f}")
        if dl:
            lines.append(f"  外送实付   ¥{dl['total_fen'] / 100:.2f}")
        gap = entry.get("gap")
        if gap:
            lines.append(f"  差额       ¥{gap['total_fen'] / 100:.2f}")
            lines.append(f"    商品溢价 ¥{gap['product_markup_fen'] / 100:.2f}")
            lines.append(f"    配送费   ¥{gap['delivery_fee_fen'] / 100:.2f}")
            lines.append(f"    包装费   ¥{gap['packing_fee_fen'] / 100:.2f}")
            if not gap["reconciles"]:
                lines.append("    ⚠ 三项未能还原总差额，数据异常")
        lines.append("")

        if entry.get("legs"):
            lines.append(f"  距店 {entry['distance_m']} 米，各方式成本：")
            lines.append("")
            lines.append(f"  {'方式':<10}{'耗时':>8}{'出行费':>9}{'时间成本':>10}{'合计':>9}")
            lines.append("  " + "-" * 48)
            for leg in entry["legs"]:
                lines.append(
                    f"  {leg['label']:<10}{leg['minutes']:>7.0f}分"
                    f"{leg['fare_yuan']:>9.2f}{leg['time_cost_yuan']:>10.2f}"
                    f"{leg['total_cost_yuan']:>9.2f}")
            lines.append("")
            lines.append(f"  数据来源：{entry.get('legs_source')}（出行费均为估算）")
            lines.append("")
        if entry.get("items"):
            lines.append("  商品编码对应关系：")
            for item in entry["items"]:
                lines.append(f"    {str(item['name'])[:20]:<22} 自提 {item['pickup_code'] or '—':>14}"
                             f"  外送 {item['delivery_code'] or '—':>14}  ({item['match']})")
            lines.append("")

    for note in payload.get("notes", []):
        lines.append(f"  注：{note}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="输出自提/外送硬数据，供调用方 AI 判断")
    parser.add_argument("--want", nargs="+", required=True)
    parser.add_argument("--at", default="")
    parser.add_argument("--hourly-rate", type=float, default=DEFAULT_HOURLY_RATE)
    parser.add_argument("--store", type=int, default=0)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args()

    at = None
    if args.at:
        lat, lng = args.at.split(",")
        at = LatLng(float(lat), float(lng))

    try:
        payload = collect(args.want, at, args.hourly_rate, args.store)
    except McpError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1

    if args.format == "text":
        print(render_text(payload))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
