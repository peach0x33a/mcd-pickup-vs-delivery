#!/usr/bin/env python3
"""Refresh the per-store menu cache from the live McDonald's MCP server.

READ-ONLY: only `query-meals` is called. Results are cached to
`data/menu_cache/<storeCode>.json` and committed, so the offline demo keeps
working for anyone without a token.

Usage:
    python3 tools/collect_menus.py --city 上海市 --limit 40
    python3 tools/collect_menus.py --city 北京市 --limit 20 --from 人民广场
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mcd_pickup_vs_delivery.geo import LatLng  # noqa: E402
from mcd_pickup_vs_delivery.mcp_client import McpClient, McpError  # noqa: E402
from mcd_pickup_vs_delivery.menu import extract_meals, fetch_menu, menu_cache_path, save_menu  # noqa: E402
from mcd_pickup_vs_delivery.stores import by_city, load_stores, nearest_stores  # noqa: E402

REQUEST_PAUSE_S = 0.15

#: A few well-known anchors so the tool is usable without knowing coordinates.
ANCHORS = {
    "人民广场": LatLng(31.2304, 121.4737),
    "陆家嘴": LatLng(31.2397, 121.4998),
    "静安寺": LatLng(31.2231, 121.4451),
    "徐家汇": LatLng(31.1950, 121.4369),
    "天安门": LatLng(39.9087, 116.3975),
    "国贸": LatLng(39.9087, 116.4613),
}


def main() -> int:
    parser = argparse.ArgumentParser(description="刷新门店菜单缓存（只读）")
    parser.add_argument("--city", default="上海市")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--from", dest="origin", default="人民广场", help="锚点名称，或 lat,lng")
    parser.add_argument("--radius", type=float, default=8000)
    parser.add_argument("--refresh", action="store_true", help="忽略缓存，强制重新拉取")
    args = parser.parse_args()

    groups = by_city(load_stores())
    if args.city not in groups:
        print(f"快照中没有城市 {args.city!r}", file=sys.stderr)
        return 1

    if "," in args.origin:
        lat, lng = (float(part) for part in args.origin.split(",", 1))
        origin = LatLng(lat, lng)
    else:
        origin = ANCHORS.get(args.origin)
        if origin is None:
            print(f"未知锚点 {args.origin!r}，可用：{', '.join(ANCHORS)}", file=sys.stderr)
            return 1

    targets = nearest_stores(groups[args.city], origin, radius_m=args.radius, limit=args.limit)
    print(f"{args.city}：将从 {args.origin} 由近到远采样 {len(targets)} 家门店菜单\n")

    client = McpClient()
    client.initialize()
    saved = 0
    for index, (store, distance) in enumerate(targets, start=1):
        if not args.refresh and menu_cache_path(store.code).exists():
            print(f"[{index}/{len(targets)}] {store.display[:24]:26s} 已有缓存，跳过")
            continue
        try:
            menu = fetch_menu(client, store.code)
        except McpError as exc:
            print(f"[{index}/{len(targets)}] {store.display[:24]:26s} 失败：{exc}")
            continue
        meals = extract_meals(menu)
        save_menu(store.code, menu)
        saved += 1
        print(
            f"[{index}/{len(targets)}] {store.display[:24]:26s} {distance:5.0f}m 菜品 {len(meals):3d} 已缓存"
        )
        time.sleep(REQUEST_PAUSE_S)

    total = len(list((ROOT / "data" / "menu_cache").glob("*.json")))
    print(f"\n本次新增 {saved} 家，缓存总计 {total} 家")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
