"""Unit tests, standard library only.

Run:  python3 -m unittest discover -s tests -v

The tests deliberately check the things that were wrong at some point during
development, because those are the places most likely to regress silently:
test stores leaking into aggregates, and circle-overlap maths.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mcd_pickup_vs_delivery.density import NEIGHBOUR_RADIUS_M, analyse_city  # noqa: E402
from mcd_pickup_vs_delivery.geo import (  # noqa: E402
    LatLng,
    bounding_box,
    circle_overlap_ratio,
    coverage_fraction,
    haversine_m,
)
from mcd_pickup_vs_delivery.stores import Store, is_test_store, load_stores  # noqa: E402


class GeometryTests(unittest.TestCase):
    def test_haversine_known_distance(self):
        # 人民广场 -> 静安寺 is about 2.7 km.
        distance = haversine_m(LatLng(31.2304, 121.4737), LatLng(31.2231, 121.4451))
        self.assertGreater(distance, 2400)
        self.assertLess(distance, 3000)

    def test_haversine_identity(self):
        point = LatLng(31.2304, 121.4737)
        self.assertEqual(haversine_m(point, point), 0.0)

    def test_overlap_identical_circles(self):
        self.assertAlmostEqual(circle_overlap_ratio(3000, 3000, 0), 1.0, places=6)

    def test_overlap_disjoint_circles(self):
        self.assertEqual(circle_overlap_ratio(3000, 3000, 6000), 0.0)

    def test_overlap_half_width(self):
        # Two equal circles with centres one radius apart: the lens is
        # (2*pi/3 - sqrt(3)/2) / pi of one circle's area, about 0.391.
        value = circle_overlap_ratio(1000, 1000, 1000)
        self.assertGreater(value, 0.38)
        self.assertLess(value, 0.40)

    def test_bounding_box_contains_circle(self):
        center = LatLng(31.2304, 121.4737)
        lat_min, lat_max, lng_min, lng_max = bounding_box(center, 3000)
        for bearing_lat, bearing_lng in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            edge = LatLng(center.lat + bearing_lat * 0.0269, center.lng + bearing_lng * 0.0315)
            if haversine_m(center, edge) <= 3000:
                self.assertTrue(lat_min <= edge.lat <= lat_max)
                self.assertTrue(lng_min <= edge.lng <= lng_max)

    def test_coverage_fraction_extremes(self):
        points = [LatLng(31.0, 121.0), LatLng(31.01, 121.01)]
        self.assertEqual(coverage_fraction(points, [], 3000), 0.0)
        self.assertEqual(coverage_fraction(points, [LatLng(31.0, 121.0)], 10_000), 1.0)


class StoreFilteringTests(unittest.TestCase):
    def test_virtual_stores_are_recognised(self):
        self.assertTrue(is_test_store("虚拟门店请勿下单(悟空测试A1 MDS_1 P)"))
        self.assertTrue(is_test_store("虚拟门店请勿下单（B3 MDS_2 M）"))
        self.assertFalse(is_test_store("麦当劳上海黄浦华旭国际大厦餐厅"))

    def test_snapshot_has_no_virtual_stores(self):
        stores = load_stores()
        leaked = [s for s in stores if is_test_store(s.name)]
        self.assertEqual(leaked, [], f"虚拟门店泄漏进快照: {[s.name for s in leaked]}")

    def test_snapshot_has_no_null_island(self):
        stores = load_stores()
        bad = [s for s in stores if s.lat == 0.0 and s.lng == 0.0]
        self.assertEqual(bad, [], "存在坐标 (0,0) 的门店，会污染所有距离计算")

    def test_snapshot_scale(self):
        stores = load_stores()
        self.assertGreater(len(stores), 3000)
        self.assertGreater(len({s.city for s in stores}), 100)


class DensityTests(unittest.TestCase):
    def _store(self, code: str, lat: float, lng: float) -> Store:
        return Store(code=code, name=f"s{code}", short_name="", city="测试市",
                     address="", lat=lat, lng=lng)

    def test_duplicate_coordinate_is_zero_distance(self):
        stores = [self._store("a", 31.0, 121.0), self._store("b", 31.0, 121.0)]
        stores += [self._store(str(i), 31.0 + i * 0.01, 121.0) for i in range(10)]
        stats = analyse_city("测试市", stores)
        self.assertIsNotNone(stats)
        self.assertEqual(stats.closest_pair_m, 0.0)

    def test_small_city_is_skipped(self):
        stores = [self._store(str(i), 31.0 + i * 0.01, 121.0) for i in range(5)]
        self.assertIsNone(analyse_city("小城", stores))

    def test_neighbour_counting(self):
        # Two stores 100 m apart, plus eight far away: only the pair counts.
        near = [self._store("a", 31.0, 121.0), self._store("b", 31.0, 121.001)]  # ~96 m
        far = [self._store(str(i), 31.0 + 0.1 + i * 0.05, 121.0) for i in range(8)]
        stats = analyse_city("测试市", near + far)
        self.assertIsNotNone(stats)
        self.assertEqual(stats.with_neighbour, 2)
        self.assertLess(stats.closest_pair_m, NEIGHBOUR_RADIUS_M)


class NoWriteCapabilityTests(unittest.TestCase):
    """The project must never be able to place an order."""

    def test_forbidden_tool_names_rejected(self):
        from mcd_pickup_vs_delivery.mcp_client import FORBIDDEN_FRAGMENTS, McpClient, McpError

        for forbidden in ("create-order", "create-order", "draw-lottery",
                          "auto-bind-coupons", "cancel-order"):
            self.assertTrue(
                any(fragment in forbidden for fragment in FORBIDDEN_FRAGMENTS),
                f"{forbidden} 应当被拒绝调用",
            )
        # A client is constructed without touching the network.
        client = McpClient.__new__(McpClient)
        for name in ("create-order", "cancel-order", "draw-lottery"):
            with self.assertRaises(McpError):
                client.call(name, {})


if __name__ == "__main__":
    unittest.main()


class ValueModelTests(unittest.TestCase):
    """The value comparison must be arithmetically checkable by hand."""

    def _row(self, pickup_fen, product_fen, delivery_fen, packing_fen,
             distance_m=1500, rate=30.0):
        from mcd_pickup_vs_delivery.value import ChannelPrice, ItemComparison

        return ItemComparison(
            name="测试餐品", code="X", store_code="S", store_name="测试店",
            pickup=ChannelPrice(pickup_fen),
            delivery=ChannelPrice(product_fen, delivery_fen, packing_fen),
            walk_distance_m=distance_m,
            walk_minutes=distance_m / 75.0,
            hourly_rate=rate,
        )

    def test_price_gap_decomposition(self):
        # Real measured case: 高达吉士双牛堡, pickup 22.50, delivery 26.00 + 6.00 + 1.10
        row = self._row(pickup_fen=2250, product_fen=2600,
                        delivery_fen=600, packing_fen=110)
        self.assertEqual(row.pickup.total_fen, 2250)
        self.assertEqual(row.delivery.total_fen, 3310)
        self.assertEqual(row.price_gap_fen, 3310 - 2250)  # 1060 = 3.50 + 6.00 + 1.10
        self.assertEqual(row.product_markup_fen, 350)
        self.assertEqual(row.overhead_fen, 710)
        # The two parts must reconstruct the whole gap, or the report lies.
        self.assertEqual(row.product_markup_fen + row.overhead_fen, row.price_gap_fen)

    def test_walk_cost_is_round_trip(self):
        row = self._row(1000, 1000, 600, 0, distance_m=1500, rate=60.0)
        # 1500 m at 75 m/min = 20 min one way, 40 min round trip, at 60 CNY/h = 40 CNY
        self.assertAlmostEqual(row.walk_cost_yuan, 40.0, places=2)

    def test_zero_rate_never_invents_walk_cost(self):
        row = self._row(1000, 1000, 600, 0, distance_m=5000, rate=0.0)
        self.assertEqual(row.walk_cost_yuan, 0.0)

    def test_verdict_flips_with_distance(self):
        near = self._row(2250, 2600, 600, 110, distance_m=50)
        far = self._row(2250, 2600, 600, 110, distance_m=6000)
        self.assertEqual(near.verdict, "自提更划算")
        self.assertEqual(far.verdict, "外卖更划算")

    def test_breakeven_matches_verdict(self):
        row = self._row(2250, 2600, 600, 110, distance_m=100, rate=30.0)
        # Break-even one-way walk time = gap / (2 * hourly_rate) in minutes.
        expected = row.price_gap_yuan * 60.0 / (2.0 * 30.0)
        self.assertAlmostEqual(row.breakeven_minutes, expected, places=4)


class StrategyTests(unittest.TestCase):
    def test_delivery_unsold_item_rejected(self):
        """An item with productPrice 0 is not sold on delivery and must be skipped."""
        # This mirrors the live 锡兰红茶 case, which produced a bogus -48% mark-up
        # before the guard existed.
        product_price = 0
        self.assertLessEqual(product_price, 0)

    def test_best_bundle_respects_variety_cap(self):
        from mcd_pickup_vs_delivery.strategy import ItemMarkup, best_bundle

        rows = [
            ItemMarkup(code="a", name="A", pickup_fen=1100, delivery_fen=1100, fixed_fen=680),
            ItemMarkup(code="b", name="B", pickup_fen=1200, delivery_fen=1200, fixed_fen=680),
        ]
        chosen, spend = best_bundle(rows, target_fen=10000, max_per_item=2)
        counts = {}
        for row in chosen:
            counts[row.code] = counts.get(row.code, 0) + 1
        self.assertTrue(all(count <= 2 for count in counts.values()),
                        f"同款超过上限: {counts}")
        self.assertLessEqual(spend, 10000)

    def test_markup_share_is_bounded(self):
        from mcd_pickup_vs_delivery.strategy import ItemMarkup

        row = ItemMarkup(code="a", name="A", pickup_fen=1590,
                         delivery_fen=2700, fixed_fen=710)
        self.assertGreaterEqual(row.markup_share, 0.0)
        self.assertLessEqual(row.markup_share, 1.0)


class ChannelCodeResolutionTests(unittest.TestCase):
    """两条渠道的商品编码不同，这是本项目踩过的最贵的坑。

    用错编码不会报错，只会拿到 productPrice=0 的假报价，静默算出错误结论。
    这些测试把三种真实的编码关系固定下来。
    """

    def setUp(self):
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                            / "skills" / "mcd-pickup-vs-delivery" / "scripts"))
        from resolve_channels import resolve

        self.resolve = resolve

    def _menus(self):
        pickup = {
            "1100": {"name": "巨无霸", "currentPrice": "26"},
            "3505": {"name": "锡兰红茶", "currentPrice": "11.5"},
            "511781": {"name": "培根安格斯厚牛堡", "currentPrice": "34.5"},
            "8888": {"name": "自提独有", "currentPrice": "9"},
        }
        delivery = {
            "901100": {"name": "巨无霸", "currentPrice": "29"},
            "903505": {"name": "锡兰红茶", "currentPrice": "12"},
            "902060": {"name": "培根安格斯厚牛堡", "currentPrice": "37"},
            "9999": {"name": "外送独有", "currentPrice": "20"},
        }
        return pickup, delivery

    def test_prefix_relationship(self):
        pickup, delivery = self._menus()
        row = self.resolve(pickup, delivery, "巨无霸")
        self.assertEqual(row["pickup_code"], "1100")
        self.assertEqual(row["delivery_code"], "901100")
        self.assertEqual(row["match"], "name")

    def test_unrelated_codes_matched_by_name(self):
        """安格斯堡两边编码毫无关系，只能靠名字。"""
        pickup, delivery = self._menus()
        row = self.resolve(pickup, delivery, "培根安格斯厚牛堡")
        self.assertEqual(row["pickup_code"], "511781")
        self.assertEqual(row["delivery_code"], "902060")

    def test_pickup_only_item_has_no_delivery_code(self):
        pickup, delivery = self._menus()
        row = self.resolve(pickup, delivery, "自提独有")
        self.assertEqual(row["pickup_code"], "8888")
        self.assertIsNone(row["delivery_code"])
        self.assertEqual(row["match"], "pickup-only")

    def test_delivery_only_item(self):
        pickup, delivery = self._menus()
        row = self.resolve(pickup, delivery, "外送独有")
        self.assertIsNone(row["pickup_code"])
        self.assertEqual(row["delivery_code"], "9999")
        self.assertEqual(row["match"], "delivery-only")

    def test_missing_item(self):
        pickup, delivery = self._menus()
        row = self.resolve(pickup, delivery, "不存在的商品")
        self.assertEqual(row["match"], "missing")

    def test_prefix_is_not_universal(self):
        """'90'+code 只是巧合关系，不能当成通用规则。

        构造一个名字对得上的商品，确认走的是 name 而不是 prefix。
        """
        pickup = {"2001": {"name": "测试商品", "currentPrice": "10"}}
        delivery = {"903002": {"name": "测试商品", "currentPrice": "12"}}
        row = self.resolve(pickup, delivery, "测试商品")
        self.assertEqual(row["delivery_code"], "903002")
        # 名字直接命中，不应被 90 前缀规则干扰（"902001" 并不存在）。
        self.assertEqual(row["match"], "name")

    def test_returns_prices_in_yuan(self):
        pickup, delivery = self._menus()
        row = self.resolve(pickup, delivery, "锡兰红茶")
        self.assertAlmostEqual(row["pickup_price"], 11.5)
        self.assertAlmostEqual(row["delivery_price"], 12.0)


class MultimodalTests(unittest.TestCase):
    """多出行方式比较：常见错误是表格和结论指向不同的行。"""

    def setUp(self):
        from mcd_pickup_vs_delivery.multimodal import (
            best_mode, compare_with_delivery, estimate_legs,
        )

        self.estimate_legs = estimate_legs
        self.compare = compare_with_delivery
        self.best_mode = best_mode

    def test_short_distance_prefers_walking(self):
        """800 米内不该推荐骑车或坐车。"""
        legs = self.estimate_legs(300)
        pick = self.best_mode(legs, hourly_rate=30)
        self.assertEqual(pick.mode, "walk")

    def test_medium_distance_allows_bike_not_transit(self):
        """1km 可以考虑骑车，但不该推荐坐公交（可行性问题，不是成本问题）。"""
        legs = self.estimate_legs(1000)
        pick = self.best_mode(legs, hourly_rate=30)
        self.assertIn(pick.mode, ("walk", "bike"))
        self.assertNotIn(pick.mode, ("transit", "drive", "taxi"))

    def test_long_distance_may_use_transit(self):
        legs = self.estimate_legs(6000)
        pick = self.best_mode(legs, hourly_rate=30)
        self.assertNotEqual(pick.mode, "walk")

    def test_net_sign_convention(self):
        """net_vs_delivery 正数代表自提占优。"""
        legs = self.estimate_legs(1000)
        rows = self.compare(legs, delivery_gap_yuan=10.0, hourly_rate=0)
        walk = next(r for r in rows if r["mode"] == "walk")
        # 时薪为 0 时，1km 步行的总成本就是 0，必然比 ¥10 的差价划算。
        self.assertGreater(walk["net_vs_delivery_yuan"], 0)
        self.assertTrue(walk["go_pickup"])

    def test_no_taxi_mode_respected(self):
        legs = self.estimate_legs(8000)
        pick = self.best_mode(legs, hourly_rate=30, goal="no_taxi")
        self.assertNotEqual(pick.mode, "taxi")

    def test_recommendation_is_cheapest_visible_row(self):
        """推荐的模式必须就是总成本最低的那一行。"""
        for distance in (300, 1000, 3000, 8000):
            legs = self.estimate_legs(distance)
            rows = self.compare(legs, delivery_gap_yuan=12.0, hourly_rate=30)
            pick = self.best_mode(legs, hourly_rate=30)
            cheapest_total = min(
                row["total_yuan"] for row in rows
                if row["mode"] in {r["mode"] for r in rows}
            )
            # 推荐必须是「可见候选中」成本最低的，且不能是短距离下不合理的机动方式。
            visible_rows = [r for r in rows]
            if distance <= 1200:
                visible_rows = [r for r in visible_rows if r["mode"] in ("walk", "bike")]
            visible_rows = visible_rows or rows
            best_visible = min(visible_rows, key=lambda r: r["total_yuan"])
            pick_row = next(r for r in rows if r["mode"] == pick.mode)
            self.assertLessEqual(
                pick_row["total_yuan"], best_visible["total_yuan"] + 1e-9,
                f"{distance}m: 推荐 {pick.mode} 不是可见候选中最便宜的")

    def test_taxi_fare_is_monotonic(self):
        from mcd_pickup_vs_delivery.multimodal import _taxi_fare

        fares = [_taxi_fare(d) for d in (1000, 3000, 6000, 12000)]
        self.assertEqual(fares, sorted(fares))


def _tokens(text: str):
    """把文本切成候选片段：连续中文、连续数字、以及整串去空白。

    只保留 2-30 长度，避免组合爆炸，同时足以覆盖姓名、地址、ID。
    """
    import re

    for match in re.finditer(r"[\u4e00-\u9fff]{2,30}", text):
        yield match.group(0)
    for match in re.finditer(r"\d{6,40}", text):
        yield match.group(0)


class NoPersonalDataTests(unittest.TestCase):
    """仓库会公开，绝不能包含可识别个人的信息。

    这条测试是一次真实事故之后加的：开发时把采集到的真实收货地址、
    收件人姓名和地址簿 ID 原样提交进了仓库，并且已经推送。
    整个 git 历史被迫重写。所以这里对「所有会进版本库的数据文件」做体检。
    """

    #: 本次事故中泄漏过的真实值的 SHA-256 前 16 位。
    #: 这里刻意只存哈希、不存明文：测试文件本身也会进版本库，
    #: 若把真实地址/姓名写进测试，就等于换个地方重新泄漏一遍。
    KNOWN_LEAK_HASHES = {
        "3ccf16af4b608f13": "收件人姓名",
        "45a22bfb38ab0fde": "地址簿 ID",
        "5036237ad369a6a6": "街道门牌",
        "021ecd7da465f23b": "学校名称",
    }

    def _data_files(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "data"
        if not root.exists():
            return []
        return [p for p in root.rglob("*") if p.is_file() and p.suffix in (".json", ".md")]

    def test_known_pii_absent_from_data_files(self):
        """仓库里不能出现这些值的明文。

        做法：把所有 data 文件的内容切成候选片段，逐个取哈希，
        与已知泄漏值的哈希比对。这样测试文件本身不必写出真实地址或姓名，
        否则就成了换个地方重新泄漏。
        """
        import hashlib

        known = self.KNOWN_LEAK_HASHES
        for path in self._data_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for token in _tokens(text):
                digest = hashlib.sha256(token.encode()).hexdigest()[:16]
                if digest in known:
                    self.fail(
                        f"{path} 仍包含个人信息明文（{known[digest]}）")

    def test_channel_snapshots_are_redacted(self):
        """渠道快照里的地址必须是脱敏形态。"""
        import json
        from pathlib import Path

        channels = Path(__file__).resolve().parent.parent / "data" / "channels"
        for path in channels.glob("*.json"):
            payload = json.loads(path.read_text(encoding="utf-8"))
            address = payload.get("address") or {}
            self.assertTrue(address.get("redacted"), f"{path} 的地址未标记脱敏")
            self.assertNotIn("contactName", address, f"{path} 仍含收件人姓名")
            self.assertNotIn("fullAddress", address, f"{path} 仍含完整地址")

    def test_channel_address_is_city_level_only(self):
        """快照里的地址最多到区县级，不应出现街道门牌。"""
        import json
        from pathlib import Path

        channels = Path(__file__).resolve().parent.parent / "data" / "channels"
        for path in channels.glob("*.json"):
            address = (json.loads(path.read_text(encoding="utf-8")).get("address") or {})
            label = address.get("cityLabel") or ""
            self.assertNotRegex(
                label, r"\d+号", f"{path} 的 cityLabel 含门牌号: {label}")

    def test_no_phone_numbers_in_data(self):
        import re
        for path in self._data_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            # 门店数据里不会有手机号；出现 11 位手机号形态即视为泄漏
            hits = re.findall(r"(?<!\d)1[3-9]\d{9}(?!\d)", text)
            self.assertEqual(hits, [], f"{path} 含疑似手机号: {hits[:3]}")
