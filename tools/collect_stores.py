#!/usr/bin/env python3
"""Collect the committed store snapshot from the live McDonald's MCP server.

READ-ONLY. This script calls only `query-party-city` and `query-party-store`,
both of which are pure queries. It never creates an address, order or coupon.

Why the party endpoints rather than `query-nearby-stores`:
    `query-nearby-stores` returns latitude/longitude of exactly 0.0 for every
    store, which is useless for geometry. The party endpoints return real,
    non-zero coordinates and are the only public MCP route to them.

Usage:
    python3 tools/collect_stores.py                 # full run, all cities
    python3 tools/collect_stores.py --limit-spu 2   # quick sample

Output: data/stores_raw.json  (keyed by storeCode)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mcd_pickup_vs_delivery.mcp_client import McpClient, McpError  # noqa: E402

OUTPUT = ROOT / "data" / "stores_raw.json"

#: Product ids that expose party/tasting events. Each `spuId` yields its own
#: city list, and the union of cities across several ids covers far more of the
#: country than any single id does.
DEFAULT_SPUS = [6971, 1670, 13495, 6370, 16158, 3089, 16164, 16165]

#: The server allows 600 requests/minute; stay well underneath.
REQUEST_PAUSE_S = 0.12


def collect(spu_ids: list[int], limit_spu: int | None) -> dict:
    client = McpClient()
    client.initialize()
    stores: dict[str, dict] = {}
    spus = spu_ids[:limit_spu] if limit_spu else spu_ids

    for spu_index, spu in enumerate(spus, start=1):
        try:
            cities = (client.call("query-party-city", {"spuId": spu}).get("data")) or []
        except McpError as exc:
            print(f"  spu {spu}: 城市列表失败 ({exc})", file=sys.stderr)
            continue
        print(f"[{spu_index}/{len(spus)}] spu {spu}: {len(cities)} 个城市", flush=True)

        for city in cities:
            code = city.get("code")
            if code is None:
                continue
            try:
                payload = client.call(
                    "query-party-store",
                    {
                        "spuId": spu,
                        "code": str(code),
                        "latitude": city.get("latitude"),
                        "longitude": city.get("longitude"),
                    },
                )
            except McpError:
                continue
            for store in payload.get("data") or []:
                key = str(store.get("code") or "")
                if key and key not in stores:
                    stores[key] = store
            time.sleep(REQUEST_PAUSE_S)

    return stores


def main() -> int:
    parser = argparse.ArgumentParser(description="采集麦当劳门店快照（只读）")
    parser.add_argument("--limit-spu", type=int, default=None, help="只跑前 N 个 spuId（快速抽样）")
    parser.add_argument("--output", default=str(OUTPUT))
    args = parser.parse_args()

    stores = collect(DEFAULT_SPUS, args.limit_spu)
    if not stores:
        print("没有采集到任何门店，检查 Token 与网络。", file=sys.stderr)
        return 1

    valid = [s for s in stores.values() if s.get("latitude") and s.get("longitude")]
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(stores, ensure_ascii=False, indent=1), encoding="utf-8")

    cities = {s.get("cityName") for s in valid}
    print(f"\n门店 {len(stores)} 家（有坐标 {len(valid)} 家）/ 城市 {len(cities)} 个")
    print(f"已写入 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
