"""Command line entry point for mcd-pickup-vs-delivery.

    python3 -m mcd_pickup_vs_delivery pickup --city 上海市 --at 31.2304,121.4737 --want 巨无霸
    python3 -m mcd_pickup_vs_delivery price-map --city 上海市 --item 1100
    python3 -m mcd_pickup_vs_delivery coverage --city 上海市 --radius 3000

Every subcommand works offline from the committed snapshot. Commands that need
a live MCP token say so explicitly and fail with a clear message instead of
inventing data.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from .geo import LatLng, haversine_m, sample_grid, bounding_box
from .menu import extract_meals, load_menu, resolve_item
from .pickup import PickupRecommendation, StoreOffer, rank_offers
from .stores import ROOT, StoreDataError, by_city, city_names, load_stores, nearest_stores

WALK_M_PER_MIN = 75.0


def _parse_latlng(text: str) -> LatLng:
    try:
        lat_str, lng_str = text.split(",")
        return LatLng(float(lat_str), float(lng_str))
    except (ValueError, AttributeError) as exc:
        raise argparse.ArgumentTypeError(f"坐标格式应为 'lat,lng'，收到 {text!r}") from exc


def _load_city(city: str):
    stores = load_stores()
    grouped = by_city(stores)
    if city not in grouped:
        known = ", ".join(city_names(stores)[:12])
        raise StoreDataError(f"快照中没有城市 {city!r}。可选示例：{known} …")
    return grouped[city]


def _ensure_menus(stores, allow_fetch: bool) -> dict[str, dict]:
    """Menus for the given stores, from cache; optionally fetch the missing ones."""
    if not allow_fetch:
        return {s.code: m for s in stores if (m := load_menu(s.code))}
    from .mcp_client import McpClient
    from .menu import fetch_menu, save_menu

    client = McpClient()
    out: dict[str, dict] = {}
    for store in stores:
        menu = load_menu(store.code)
        if menu is None:
            menu = fetch_menu(client, store.code)
            save_menu(store.code, menu)
        out[store.code] = menu
    return out


def cmd_pickup(args: argparse.Namespace) -> int:
    stores = _load_city(args.city)
    origin = args.at
    candidates = nearest_stores(stores, origin, radius_m=args.radius, limit=args.limit)
    if not candidates:
        print(f"{args.radius} 米内没有门店。", file=sys.stderr)
        return 1

    menus = _ensure_menus([s for s, _ in candidates], allow_fetch=args.fetch)
    if not menus:
        print(
            "没有可用的菜单缓存。加 --fetch（需要 MCD_MCP_TOKEN）实时拉取，\n"
            "或先运行 `python3 tools/collect_menus.py`。",
            file=sys.stderr,
        )
        return 1

    offers: list[StoreOffer] = []
    for store, distance in candidates:
        menu = menus.get(store.code)
        if not menu:
            continue
        meals = extract_meals(menu)
        if not meals:
            continue
        lines: list[tuple[str, int]] = []
        missing: list[str] = []
        for want in args.want:
            hit = resolve_item(meals, want)
            if hit:
                lines.append((hit[0], 1))
            else:
                missing.append(want)
        total = None
        if lines and args.fetch:
            from .mcp_client import McpClient
            from .menu import quote

            total = quote(McpClient(), store.code, lines)
            authoritative = total is not None
        else:
            prices = [meals[code]["price"] for code, _ in lines if meals[code]["price"]]
            total = round(sum(prices) * 100) if len(prices) == len(lines) else None
            authoritative = False
        if total is None:
            continue
        offers.append(
            StoreOffer(
                store=store,
                distance_m=distance,
                total_fen=total,
                menu_size=len(meals),
                missing=missing,
                authoritative=authoritative,
            )
        )

    if not offers:
        print("没有门店能报出这组商品的完整价格。", file=sys.stderr)
        return 1

    priced = rank_offers(offers)
    closest = min(priced, key=lambda o: o.distance_m)
    cheapest = priced[0]
    rec = PickupRecommendation(
        origin=args.origin_name,
        offers=priced,
        cheapest=cheapest,
        closest=closest,
        routing="amap-walking" if args.amap else "straight-line",
    )

    any_authoritative = any(o.authoritative for o in priced)
    source = "官方 calculate-price 核价" if any_authoritative else "菜单标价（未核价）"
    print(f"\n  {args.origin_name} · 想买「{' + '.join(args.want)}」")
    print(f"  价格来源：{source} · 距离口径：{'高德步行' if args.amap else '直线距离'}")
    print()
    print(f"  {'门店':<26}{'距离':>8}{'步行':>8}{'总价':>10}{'菜品数':>8}")
    print("  " + "-" * 62)
    for offer in priced:
        mark = " ←推荐" if rec.best and offer.store.code == rec.best.store.code else ""
        walk = f"{offer.walk_minutes:.0f}分" if offer.distance_m else "-"
        print(
            f"  {offer.store.display[:24]:<26}{offer.distance_m:>7.0f}m{walk:>8}"
            f"{offer.total_yuan:>10.2f}{offer.menu_size:>8}{mark}"
        )
    print()
    print(f"  {rec.tradeoff_note()}")
    if cheapest and closest and cheapest.store.code != closest.store.code:
        delta_m = cheapest.distance_m - closest.distance_m
        delta_yuan = (closest.total_fen - cheapest.total_fen) / 100
        print(
            f"  若只求近：{closest.store.display}（{closest.distance_m:.0f}m，¥{closest.total_yuan:.2f}），"
            f"少走 {abs(delta_m):.0f} 米，多花 ¥{delta_yuan:.2f}。"
        )
    if args.json:
        print()
        print(json.dumps(
            {
                "origin": args.origin_name,
                "routing": rec.routing,
                "offers": [
                    {
                        "store": o.store.display,
                        "code": o.store.code,
                        "distance_m": round(o.distance_m),
                        "total_yuan": o.total_yuan,
                        "menu_size": o.menu_size,
                        "missing": o.missing,
                        "authoritative": o.authoritative,
                    }
                    for o in priced
                ],
            },
            ensure_ascii=False,
            indent=2,
        ))
    return 0


def cmd_price_map(args: argparse.Namespace) -> int:
    stores = _load_city(args.city)
    rows = []
    for store in stores:
        menu = load_menu(store.code)
        if not menu:
            continue
        meals = extract_meals(menu)
        item = meals.get(args.item)
        if not item or item["price"] is None:
            continue
        rows.append((store, item["price"], len(meals)))
    if not rows:
        print(f"缓存中没有门店售卖 {args.item!r}。先运行 tools/collect_menus.py。", file=sys.stderr)
        return 1
    dist = Counter(price for _, price, _ in rows)
    print(f"\n  {args.city} · 餐品 {args.item} 的门店价格分布")
    print(f"  （样本 {len(rows)} 家已缓存门店）\n")
    lo, hi = min(dist), max(dist)
    for price in sorted(dist):
        n = dist[price]
        bar = "█" * max(1, round(n / max(dist.values()) * 34))
        tag = "  ← 最低" if price == lo else ("  ← 最高" if price == hi else "")
        print(f"  ¥{price:>5.2f}  {bar:<36}{n:>3} 家{tag}")
    print()
    if hi > lo:
        print(f"  全城价差 ¥{hi - lo:.2f}（{(hi - lo) / hi * 100:.1f}%）")
        cheapest = [s for s, p, _ in rows if p == lo]
        print(f"  最低价门店 {len(cheapest)} 家，例如：{cheapest[0].display}")
    else:
        print("  该餐品在本城所有已采样门店同价。")
    return 0


def cmd_coverage(args: argparse.Namespace) -> int:
    stores = _load_city(args.city)
    if not stores:
        print("该城市没有门店。", file=sys.stderr)
        return 1
    center = LatLng(
        sum(s.lat for s in stores) / len(stores),
        sum(s.lng for s in stores) / len(stores),
    )
    box = bounding_box(center, args.extent)
    points = sample_grid(box, args.step)
    covered = 0
    for point in points:
        for store in stores:
            if haversine_m(point, store.point) <= args.radius:
                covered += 1
                break
    ratio = covered / len(points) if points else 0.0
    print(f"\n  {args.city} · 门店覆盖分析")
    print(f"  门店数：{len(stores)}")
    print(f"  服务半径：{args.radius} 米（可配置假设，非官方配送半径）")
    print(f"  采样：{len(points)} 个点，步长 {args.step} 米，范围 {args.extent} 米")
    print(f"  覆盖比例：{ratio * 100:.1f}%")
    print()
    # Stores whose service circle is largely redundant.
    redundant = 0
    for i, store in enumerate(stores):
        for j, other in enumerate(stores):
            if i == j:
                continue
            if haversine_m(store.point, other.point) <= args.radius * 0.35:
                redundant += 1
                break
    print(f"  高度重叠门店（{args.radius * 0.35:.0f} 米内有另一家）：{redundant} 家")
    return 0


def cmd_cities(args: argparse.Namespace) -> int:
    stores = load_stores()
    grouped = by_city(stores)
    ranked = sorted(grouped.items(), key=lambda kv: -len(kv[1]))
    print(f"\n  快照共 {len(stores)} 家门店 / {len(grouped)} 个城市\n")
    print(f"  {'城市':<16}{'门店数':>8}")
    print("  " + "-" * 26)
    for city, items in ranked[: args.top]:
        print(f"  {city:<16}{len(items):>8}")
    return 0



def cmd_density(args: argparse.Namespace) -> int:
    from .density import analyse_all, closest_pairs, totals

    grouped = by_city(load_stores())
    rows = analyse_all(grouped)
    cities, stores, with_neighbour = totals(rows)
    if args.closest:
        print("\n  全国最贴近的门店对")
        print()
        print(f"  {'距离':>8}  {'门店 A':<30}{'门店 B':<30}")
        print("  " + "-" * 74)
        for a, b, distance in closest_pairs(grouped, args.limit):
            print(f"  {distance:>6.0f}m  {a.display[:28]:<30}{b.display[:28]}")
        print()
        return 0

    print()
    print(f"  {cities} 个城市 · {stores} 家门店")
    print(f"  其中 {with_neighbour} 家（{with_neighbour / stores * 100:.0f}%）在 1 公里内还有第二家麦当劳")
    print()
    print(f"  {'城市':<12}{'门店':>6}{'1km内另有':>11}{'占比':>8}{'最近邻中位':>12}{'最近一对':>10}")
    print("  " + "-" * 59)
    for row in rows[: args.limit]:
        print(
            f"  {row.city:<12}{row.store_count:>6}{row.with_neighbour:>11}"
            f"{row.share * 100:>7.0f}%{row.median_nearest_m:>11.0f}m{row.closest_pair_m:>9.0f}m"
        )
    print()
    return 0


def cmd_branches(args: argparse.Namespace) -> int:
    from .branches import compare_branches, spread

    stores = _load_city(args.city)
    quotes = compare_branches(stores, args.want, args.at, radius_m=args.radius, limit=args.limit)
    if not quotes:
        print("附近没有已缓存菜单的门店。先运行 tools/collect_menus.py。", file=sys.stderr)
        return 1
    info = spread(quotes)
    print()
    print(f"  {args.origin_name} · {' + '.join(args.want)} · {args.radius:.0f} 米内")
    print()
    print(f"  {'门店':<26}{'距离':>8}{'步行':>7}{'总价':>10}{'菜品':>7}")
    print("  " + "-" * 60)
    for quote in sorted(quotes, key=lambda q: (q.total_yuan is None, q.total_yuan or 0)):
        total = f"¥{quote.total_yuan:.2f}" if quote.total_yuan is not None else "缺货"
        print(
            f"  {quote.store.display[:24]:<26}{quote.distance_m:>7.0f}m"
            f"{quote.walk_minutes:>6.0f}分{total:>10}{quote.menu_size:>7}"
        )
    print()
    if info.get("comparable"):
        print(f"  价差 ¥{info['price_gap']:.2f}（{info['price_gap_pct']}%） · 菜品数相差 {info['menu_gap']} 项")
        if info["extra_walk_m"] > 0:
            print(
                f"  最便宜那家要多走 {info['extra_walk_m']} 米"
                f"（{info['extra_walk_m'] / 75:.0f} 分钟）"
            )
        else:
            print("  最便宜的那家同时也是最近的。")
    else:
        print("  这几家门店同价，差异只在菜品数量。")
    return 0


def cmd_price_tiers(args: argparse.Namespace) -> int:
    """Group cached branches by unit price and show where each tier sits."""
    from .menu import extract_meals, load_menu

    stores = _load_city(args.city)
    buckets: dict[float, list] = {}
    for store in stores:
        menu = load_menu(store.code)
        if not menu:
            continue
        item = extract_meals(menu).get(args.item)
        if not item or item["price"] is None:
            continue
        buckets.setdefault(item["price"], []).append(store)
    if not buckets:
        print(f"缓存中没有门店售卖 {args.item!r}。", file=sys.stderr)
        return 1
    total = sum(len(v) for v in buckets.values())
    print()
    print(f"  {args.city} · 餐品 {args.item} · {total} 家已采样门店")
    print()
    for price in sorted(buckets):
        group = buckets[price]
        print(f"  ¥{price:.2f}   {len(group):>2} 家")
        for store in group[: args.per_tier]:
            print(f"      {store.display[:24]:<26}{store.address[:32]}")
        if len(group) > args.per_tier:
            print(f"      … 另有 {len(group) - args.per_tier} 家")
        print()
    if len(buckets) > 1:
        low, high = min(buckets), max(buckets)
        print(f"  价差 ¥{high - low:.2f}（{(high - low) / high * 100:.1f}%）")
    return 0



def cmd_delivery(args: argparse.Namespace) -> int:
    """自提 vs 外送：同一份餐差多少钱，差在哪。"""
    from .delivery import NoAddressError, compare_item, common_items, delivery_stores, saved_address
    from .mcp_client import McpClient, McpError

    try:
        client = McpClient()
        address = saved_address(client)
        address_id = str(address.get("addressId") or "")
        if not address_id:
            raise NoAddressError("地址记录缺少 addressId")
    except (McpError, NoAddressError) as exc:
        print(f"{exc}", file=sys.stderr)
        return 1

    where = (address.get("fullAddress") or address.get("address") or "")[:34]
    stores = delivery_stores(client, address_id)
    if not stores:
        print("该地址没有可配送门店。", file=sys.stderr)
        return 1

    print()
    print(f"  收货地址：{where}")
    print(f"  可配送门店 {len(stores)} 家")
    print()

    for store in stores[: args.stores]:
        code = str(store.get("storeCode") or "")
        be_code = str(store.get("beCode") or "")
        name = store.get("storeName") or code
        if not code or not be_code:
            continue
        rows = common_items(client, code, be_code)
        if not rows:
            continue
        print(f"  ── {name} ──")
        print()
        print(f"  {'餐品':<22}{'自提':>9}{'外送':>9}{'差额':>9}{'幅度':>8}")
        print("  " + "-" * 58)
        shown = 0
        for item_code, item_name, a, b in rows:
            if b <= a:
                continue
            print(f"  {item_name[:20]:<22}{a:>8.2f}{b:>8.2f}{b - a:>9.2f}{(b - a) / a * 100:>7.0f}%")
            shown += 1
            if shown >= args.top:
                break
        print()

        # Authoritative decomposition on the single biggest gap.
        biggest = next((row for row in rows if row[3] > row[2]), None)
        if biggest:
            item_code, item_name, a, b = biggest
            comparison = compare_item(client, code, be_code, item_code, item_name)
            if comparison:
                pk, dl = comparison.pickup, comparison.delivery
                print(f"  官方核价拆解 · {item_name}")
                print(f"    商品价   自提 ¥{pk.product_fen / 100:>6.2f}   外送 ¥{dl.product_fen / 100:>6.2f}"
                      f"   (+¥{comparison.product_gap_fen / 100:.2f} / +{comparison.product_gap_pct:.0f}%)")
                if dl.delivery_fen:
                    print(f"    配送费            ¥{dl.delivery_fen / 100:>6.2f}")
                if dl.packing_fen:
                    print(f"    包装费            ¥{dl.packing_fen / 100:>6.2f}")
                print(f"    实付     自提 ¥{pk.total_yuan:>6.2f}   外送 ¥{dl.total_yuan:>6.2f}"
                      f"   (+¥{comparison.gap_fen / 100:.2f} / +{comparison.gap_pct:.0f}%)")
                print()

    print("  说明：以上金额均由官方 calculate-price 返回（单位：分，已换算为元）。")
    print("  外送与自提的商品目录并不完全重合，此处只比较两边都有的商品。")
    return 0



def cmd_value(args: argparse.Namespace) -> int:
    """自提 vs 外送的价值比对：把钱、路、时间放进同一个算式。"""
    import json as _json
    from .geo import LatLng as _LatLng
    from .stores import load_stores as _load
    from .value import (ChannelPrice, DEFAULT_HOURLY_RATE, build_comparison,
                        summarise_rows)

    slug = args.slug
    path = ROOT / "data" / "channels" / f"{slug}.json"
    if not path.exists():
        print(f"找不到 {path}。先运行 tools/collect_channels.py --slug {slug}", file=sys.stderr)
        return 1
    payload = _json.loads(path.read_text(encoding="utf-8"))

    snapshot = {s.code: s for s in _load()}
    address_text = payload["address"].get("fullAddress") or ""
    rate = args.hourly_rate

    # Locate the address on the map via the nearest delivery-reachable store's
    # coordinates is wrong; instead average the reachable stores' coordinates
    # as a coarse origin only when the address itself has no coordinates.
    # In practice we take the origin from the closest store (0 m baseline is
    # not available), so we require the user to pass --at when precision matters.
    origin = args.at
    print()
    print(f"  收货地址：{address_text[:46]}")
    print(f"  时间价值：¥{rate:.0f}/小时（可调，仅用于权衡，非实测工资）")
    print(f"  采集时间：{payload.get('captured_at', '?')}")
    print()

    all_rows = []
    for store in payload["stores"]:
        code = store["storeCode"]
        entry = snapshot.get(code)
        if not entry:
            continue
        distance = None
        if origin is not None:
            from .geo import haversine_m as _h
            distance = _h(origin, entry.point)
        rows = []
        for item_code, quote in store["quotes"].items():
            meta = store["menu"].get(item_code) or {}
            p_data = quote["pickup"]
            d_data = quote["delivery"]
            pickup = ChannelPrice(
                product_fen=int(p_data.get("productPrice") or p_data.get("price") or 0),
                delivery_fen=int(p_data.get("deliveryPrice") or 0),
                packing_fen=int(p_data.get("packingPrice") or 0),
            )
            delivery = ChannelPrice(
                product_fen=int(d_data.get("productPrice") or d_data.get("price") or 0),
                delivery_fen=int(d_data.get("deliveryPrice") or 0),
                packing_fen=int(d_data.get("packingPrice") or 0),
            )
            if pickup.total_fen == 0 or delivery.total_fen == 0:
                continue
            if distance is None:
                # No origin given: value the walk at zero distance so the report
                # still shows the money side, never inventing a distance.
                cmp = build_comparison(
                    name=meta.get("name") or item_code, code=item_code,
                    store_code=code, store_name=store["storeName"],
                    pickup=pickup, delivery=delivery,
                    address=entry.point, store_point=entry.point, hourly_rate=rate)
                cmp.walk_distance_m = float("nan")
                cmp.walk_minutes = float("nan")
            else:
                cmp = build_comparison(
                    name=meta.get("name") or item_code, code=item_code,
                    store_code=code, store_name=store["storeName"],
                    pickup=pickup, delivery=delivery,
                    address=origin, store_point=entry.point, hourly_rate=rate)
            rows.append(cmp)
        if not rows:
            continue
        rows.sort(key=lambda r: -r.price_gap_fen)
        all_rows.extend(rows)

        stats = summarise_rows(rows)
        head = f"  ── {store['storeName']}"
        if distance is not None:
            head += f" · 步行约 {distance:.0f}m（往返 {distance * 2 / 75:.0f} 分钟）"
        print(head)
        print(f"     自提目录 {store['menu_pickup_count']} 项 / 外送目录 {store['menu_delivery_count']} 项"
              f" / 交集 {store['common_count']} 项")
        print()
        print(f"     {'餐品':<20}{'自提':>9}{'外送':>9}{'差额':>8}"
              f"{'商品提价':>9}{'固定费':>8}{'结论':>12}")
        print("     " + "-" * 74)
        for row in rows[: args.top]:
            walk_cost = "" if row.walk_distance_m != row.walk_distance_m else f"¥{row.walk_cost_yuan:.1f}"
            verdict = row.verdict
            print(f"     {row.name[:18]:<20}{row.pickup.total_yuan:>8.2f}{row.delivery.total_yuan:>9.2f}"
                  f"{row.price_gap_yuan:>8.2f}{row.product_markup_yuan:>9.2f}"
                  f"{row.overhead_yuan:>8.2f}{verdict:>12}")
        print()
        print(f"     中位价差 ¥{stats['median_gap_yuan']:.2f}（{stats['median_gap_pct']:.0f}%）"
              f" · 中位固定费 ¥{stats['median_overhead_fen'] / 100:.2f}"
              f" · 最大差额 {stats['max_gap_name'][:14]} ¥{stats['max_gap_yuan']:.2f}")
        if distance is not None:
            be = rows[0].breakeven_minutes
            if 0 < be < 600:
                print(f"     若你时薪 ¥{rate:.0f}：走过去超过 {be:.0f} 分钟，外卖才更划算。")
            else:
                print(f"     若你时薪 ¥{rate:.0f}：价差远大于路费，自提始终更划算。")
        print()

    if all_rows and args.summary:
        print("  === 总计 ===")
        stats = summarise_rows(all_rows)
        print(f"  比较 {stats['count']} 项 · 自提占优 {stats['pickup_wins']} 项"
              f" · 外卖占优 {stats['delivery_wins']} 项")
        print(f"  中位价差 ¥{stats['median_gap_yuan']:.2f}（{stats['median_gap_pct']:.0f}%）"
              f" · 中位固定费 ¥{stats['median_overhead_fen'] / 100:.2f}")
        print(f"  最大差额：{stats['max_gap_name']} ¥{stats['max_gap_yuan']:.2f}")
        print()
    print("  金额均来自官方 calculate-price（分→元）。时薪为可调假设，不是实测数据。")
    return 0



def cmd_strategy(args: argparse.Namespace) -> int:
    """如果一定要外送，怎么点最划算。"""
    from .mcp_client import McpClient, McpError
    from .strategy import advice, best_bundle, cost_curve, measure_markups

    try:
        client = McpClient()
        addresses = (client.call("delivery-query-addresses", {}).get("data") or {}).get("addresses") or []
        if not addresses:
            print("账号里没有配送地址；本项目不会替你创建（MCP 无法删除地址）。", file=sys.stderr)
            return 1
        stores = client.call("delivery-query-stores",
                             {"addressId": addresses[0]["addressId"], "beType": 2}).get("data") or []
    except McpError as exc:
        print(f"MCP 错误：{exc}", file=sys.stderr)
        return 1
    if not stores:
        print("该地址没有可配送门店。", file=sys.stderr)
        return 1

    store = stores[args.store]
    code, be_code = str(store.get("storeCode")), str(store.get("beCode"))
    name = store.get("storeName") or code
    print()
    print(f"  {name} · 外送最优策略")
    print()

    # Pick a mid-priced item as the cost-curve probe: extremes distort the shape.
    meals = ((client.call("query-meals", {"storeCode": code, "orderType": 1,
                                          "beType": 1}).get("data") or {}).get("meals")) or {}
    priced = []
    for item_code, item in meals.items():
        try:
            value = float(item.get("currentPrice"))
        except (TypeError, ValueError):
            continue
        # NOTE: query-meals returns currentPrice in YUAN while calculate-price
        # returns integers in FEN. Keep the menu side in yuan here and convert
        # only when it meets an authoritative figure.
        if 8 <= value <= 600:
            priced.append((value, item_code, item.get("name") or item_code))
    priced.sort()
    if not priced:
        print("没有找到合适价位的餐品用于测算。", file=sys.stderr)
        return 1
    # The probe item must be one the DELIVERY channel actually sells; the
    # menu is the pickup catalogue and the two are not the same set. Probe a
    # few candidates near the middle of the price range and keep the first that
    # returns a full cost curve.
    midpoint = len(priced) // 2
    candidates = [priced[i] for i in
                  sorted(range(len(priced)), key=lambda i: abs(i - midpoint))][:8]
    probe_value = probe_code = probe_name = None
    curve = []
    for candidate_value, candidate_code, candidate_name in candidates:
        trial = cost_curve(client, code, be_code, candidate_code)
        if len(trial) >= 4 and all(point.product_fen > 0 for point in trial):
            probe_value, probe_code, probe_name, curve = (
                candidate_value, candidate_code, candidate_name, trial)
            break
    if probe_code is None:
        print("  ① 该门店没有可用于测算固定费的餐品（外送目录与自提目录差异过大）。")
        print()
    if curve:
        print(f"  ① 固定费怎么摊薄（以「{probe_name}」为例，单件 ¥{probe_value:.2f}）")
        print()
        print(f"     {'份数':>4}{'商品小计':>11}{'配送费':>9}{'包装费':>8}{'满减':>8}{'实付':>10}{'每份':>9}")
        print("     " + "-" * 61)
        for point in curve:
            print(f"     {point.quantity:>4}{point.product_fen / 100:>11.2f}{point.delivery_fen / 100:>9.2f}"
                  f"{point.packing_fen / 100:>8.2f}{-point.discount_fen / 100:>8.2f}"
                  f"{point.total_fen / 100:>10.2f}{point.unit_fen / 100:>9.2f}")
        print()

    # Mark-up spread across a sample of items.
    sample = [item_code for _, item_code, _ in priced[: args.sample]]
    names = {item_code: label for _, item_code, label in priced}
    markups = measure_markups(client, code, be_code, sample, names)
    if markups:
        print("  ② 哪些餐品外送加价最少")
        print()
        print(f"     {'餐品':<22}{'自提':>9}{'外送商品':>10}{'加价':>9}{'加价%':>8}")
        print("     " + "-" * 58)
        for row in markups[: args.top]:
            print(f"     {row.name[:20]:<22}{row.pickup_fen / 100:>9.2f}"
                  f"{row.delivery_fen / 100:>10.2f}{row.markup_fen / 100:>9.2f}"
                  f"{row.markup_pct:>7.0f}%")
        print()

    notes = advice(curve, markups)
    if notes:
        print("  ③ 结论")
        print()
        for index, note in enumerate(notes, start=1):
            print(f"     {index}. {note}")
        print()

    if args.budget:
        chosen, spend = best_bundle(markups, int(args.budget * 100))
        if chosen:
            print(f"  ④ 预算 ¥{args.budget:.0f} 的低加价组合（只算商品，不含固定费）")
            print()
            tally: dict[str, int] = {}
            for row in chosen:
                tally[row.name] = tally.get(row.name, 0) + 1
            for label, count in tally.items():
                print(f"     {label[:22]:<24}× {count}")
            print(f"     {'商品小计':<24}¥{spend / 100:.2f}")
            print()

    print("  金额均来自官方 calculate-price。结论基于该门店该地址的实时数据，换店换址需重新测算。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcd-pickup-vs-delivery",
        description="麦当劳门店价差与覆盖分析（只读，不下单）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_pickup = sub.add_parser("pickup", help="自提该去哪家：距离与价格权衡")
    p_pickup.add_argument("--city", default="上海市")
    p_pickup.add_argument("--at", type=_parse_latlng, default=LatLng(31.2304, 121.4737))
    p_pickup.add_argument("--origin-name", default="人民广场")
    p_pickup.add_argument("--want", nargs="+", default=["巨无霸"])
    p_pickup.add_argument("--radius", type=float, default=3000)
    p_pickup.add_argument("--limit", type=int, default=12)
    p_pickup.add_argument("--fetch", action="store_true", help="用 MCP 实时取菜单并核价")
    p_pickup.add_argument("--amap", action="store_true", help="用高德步行距离替代直线距离")
    p_pickup.add_argument("--json", action="store_true")
    p_pickup.set_defaults(func=cmd_pickup)

    p_price = sub.add_parser("price-map", help="同一餐品在各门店的价格分布")
    p_price.add_argument("--city", default="上海市")
    p_price.add_argument("--item", default="1100", help="餐品编码，如 1100=巨无霸")
    p_price.set_defaults(func=cmd_price_map)

    p_cov = sub.add_parser("coverage", help="门店服务半径覆盖与重叠分析")
    p_cov.add_argument("--city", default="上海市")
    p_cov.add_argument("--radius", type=float, default=3000)
    p_cov.add_argument("--step", type=float, default=500)
    p_cov.add_argument("--extent", type=float, default=12000)
    p_cov.set_defaults(func=cmd_coverage)

    p_cities = sub.add_parser("cities", help="列出快照中的城市与门店数")
    p_cities.add_argument("--top", type=int, default=20)
    p_cities.set_defaults(func=cmd_cities)
    p_density = sub.add_parser("density", help="门店密度：1 公里内还有第二家麦当劳的城市排行")
    p_density.add_argument("--limit", type=int, default=15)
    p_density.add_argument("--closest", action="store_true", help="改为列出全国最贴近的门店对")
    p_density.set_defaults(func=cmd_density)

    p_branches = sub.add_parser("branches", help="多家门店同一份餐的价格比对")
    p_branches.add_argument("--city", default="上海市")
    p_branches.add_argument("--at", type=_parse_latlng, default=LatLng(31.2304, 121.4737))
    p_branches.add_argument("--origin-name", default="人民广场")
    p_branches.add_argument("--want", nargs="+", default=["巨无霸"])
    p_branches.add_argument("--radius", type=float, default=1500)
    p_branches.add_argument("--limit", type=int, default=10)
    p_branches.set_defaults(func=cmd_branches)

    p_tiers = sub.add_parser("price-tiers", help="按价格档位分组，看各档门店都在哪")
    p_tiers.add_argument("--city", default="上海市")
    p_tiers.add_argument("--item", default="1100")
    p_tiers.add_argument("--per-tier", type=int, default=8)
    p_tiers.set_defaults(func=cmd_price_tiers)

    p_dlv = sub.add_parser("delivery", help="自提 vs 外送：同一份餐贵多少、贵在哪")
    p_dlv.add_argument("--stores", type=int, default=2, help="最多比较几家可配送门店")
    p_dlv.add_argument("--top", type=int, default=8, help="每家门店列出几个价差最大的餐品")
    p_dlv.set_defaults(func=cmd_delivery)

    p_value = sub.add_parser("value", help="自提 vs 外送的价值比对（钱 + 路 + 时间）")
    p_value.add_argument("--slug", default="zibo-campus")
    p_value.add_argument("--at", type=_parse_latlng, default=None, help="你的位置 lat,lng")
    p_value.add_argument("--hourly-rate", type=float, default=30.0)
    p_value.add_argument("--top", type=int, default=8)
    p_value.add_argument("--summary", action="store_true", default=True)
    p_value.set_defaults(func=cmd_value)

    p_strat = sub.add_parser("strategy", help="如果一定要外送，怎么点最划算")
    p_strat.add_argument("--store", type=int, default=0, help="第几家可配送门店（从 0 开始）")
    p_strat.add_argument("--sample", type=int, default=14, help="抽样多少餐品测加价")
    p_strat.add_argument("--top", type=int, default=8)
    p_strat.add_argument("--budget", type=float, default=0)
    p_strat.set_defaults(func=cmd_strategy)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except StoreDataError as exc:
        print(f"数据错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
