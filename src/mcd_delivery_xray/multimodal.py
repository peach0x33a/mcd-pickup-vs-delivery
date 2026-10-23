"""多出行方式的时间与成本，用于和外送做价值比较。

核心思路：把「自提」看成一次出行，把不同的出行方式各自的成本算出来，
再和外送的溢价对比。

    ┌ 步行  0 元      + 时间 × 时薪
    │ 骑行  0 元      + 时间 × 时薪
    ├ 公交  票价     + 时间 × 时薪
    ├ 驾车  油费+停车 + 时间 × 时薪
    └ 打车  车费     + 时间 × 时薪(可选，车上可做别的事)
    外送    商品溢价 + 配送费 + 包装费 + 时间 ≈ 0

数据来源的诚实说明（这一点很重要，不要含糊）：

* **时间**：配置了 ``AMAP_KEY`` 时来自高德路径规划 API（``duration`` 字段），
  是真实路况下的时长。没有 Key 时按直线距离与各方式典型速度估算，
  并在输出里标注「估算」。
* **打车费**：高德的「估价路径规划」属于网约车行业解决方案，需要单独的
  行业权限，且要求调用方自己传入起步价/里程费等计价规则，不是通用查价接口。
  所以本项目**不声称拿到真实打车费**，一律按各城市公开计价口径估算并标注。
  高德 MCP 的「打车」能力是唤起 App 发起叫车，不是查询价格。
* **公交票价**：路径规划返回线路与时长，不返回票价；按城市票价规则估算。
"""
from __future__ import annotations

from dataclasses import dataclass

from .geo import LatLng, haversine_m

# ---------------------------------------------------------------- 出行方式

#: 各方式的典型速度（米/分钟）与固定成本。仅在无高德 Key 时用于估算，
#: 数值取自国内城市常见水平，宁可保守也不要夸大某种方式的优势。
MODES: dict[str, dict] = {
    "walk": {
        "label": "步行",
        "speed_m_per_min": 75.0,
        "fixed_yuan": 0.0,
        "note": "不花钱，但要走",
    },
    "bike": {
        "label": "骑行",
        "speed_m_per_min": 250.0,
        "fixed_yuan": 0.0,
        "note": "共享单车按 ¥1.5/次计入",
        "extra_fixed": 1.5,
    },
    "transit": {
        "label": "公交/地铁",
        "speed_m_per_min": 400.0,
        "fixed_yuan": 2.0,
        "note": "按起步票价 ¥2 估算，不含换乘",
    },
    "drive": {
        "label": "驾车",
        "speed_m_per_min": 500.0,
        "fixed_yuan": 0.0,
        "note": "油费按 ¥0.6/公里，停车按 ¥5/次估算",
    },
    "taxi": {
        "label": "打车",
        "speed_m_per_min": 450.0,
        "fixed_yuan": 0.0,
        "note": "按起步价 ¥13 含 3 公里、之后 ¥2.5/公里估算",
    },
}

#: 打车计价口径（用于估算，不是官方实时价）。
TAXI_FLAG_DROP_YUAN = 13.0
TAXI_FLAG_DROP_M = 3000.0
TAXI_PER_KM_YUAN = 2.5

#: 驾车成本口径（用于估算）。
DRIVE_FUEL_PER_KM_YUAN = 0.6
DRIVE_PARKING_YUAN = 5.0


@dataclass
class Leg:
    """一种出行方式的结果。"""

    mode: str
    label: str
    distance_m: float
    minutes: float
    cost_yuan: float
    estimated: bool
    note: str = ""

    @property
    def cost_is_estimated(self) -> bool:
        return self.estimated


def _taxi_fare(distance_m: float) -> float:
    if distance_m <= TAXI_FLAG_DROP_M:
        return TAXI_FLAG_DROP_YUAN
    extra_km = (distance_m - TAXI_FLAG_DROP_M) / 1000.0
    return TAXI_FLAG_DROP_YUAN + extra_km * TAXI_PER_KM_YUAN


def _drive_cost(distance_m: float) -> float:
    return distance_m / 1000.0 * DRIVE_FUEL_PER_KM_YUAN + DRIVE_PARKING_YUAN


def estimate_legs(distance_m: float) -> list[Leg]:
    """无高德 Key 时，按距离估算每种方式的时间与成本。"""
    legs: list[Leg] = []
    for mode, spec in MODES.items():
        minutes = distance_m / spec["speed_m_per_min"]
        if mode == "taxi":
            cost = _taxi_fare(distance_m)
        elif mode == "drive":
            cost = _drive_cost(distance_m)
        elif mode == "bike":
            cost = spec.get("extra_fixed", 0.0)
        else:
            cost = spec["fixed_yuan"]
        legs.append(
            Leg(
                mode=mode,
                label=spec["label"],
                distance_m=distance_m,
                minutes=minutes,
                cost_yuan=cost,
                estimated=True,
                note=spec["note"],
            )
        )
    return legs


def legs_from_amap(client, origin: LatLng, destination: LatLng, city: str = "") -> list[Leg]:
    """有高德 Key 时，用真实路径时长替换估算。

    步行/骑行/驾车/公交的**距离和时长**来自高德路径规划接口。
    公交票价与打车费高德不返回，仍按口径估算，``estimated`` 保持 True，
    以免把估算值冒充成实测值。
    """
    legs: list[Leg] = []
    order = ["walk", "bike", "drive", "transit"]

    for mode in order:
        spec = MODES[mode]
        try:
            if mode == "walk":
                route = client.walking_route(origin, destination)
            elif mode == "bike":
                route = client.bicycling_route(origin, destination)
            elif mode == "drive":
                route = client.driving_route(origin, destination)
            else:
                route = client.transit_route(origin, destination, city)
        except Exception:
            # 某一种方式规划失败时退回估算，而不是整份报告缺失。
            legs.append(estimate_legs(haversine_m(origin, destination))[
                [m for m in MODES].index(mode)])
            continue

        if mode == "taxi":
            cost = _taxi_fare(route.distance_m)
        elif mode == "drive":
            cost = _drive_cost(route.distance_m)
        elif mode == "bike":
            cost = spec.get("extra_fixed", 0.0)
        else:
            cost = spec["fixed_yuan"]

        legs.append(
            Leg(
                mode=mode,
                label=spec["label"],
                distance_m=route.distance_m,
                minutes=route.minutes,
                cost_yuan=cost,
                # 时间是真数据，费用仍是估算 -> 标注为估算，不冒充实测。
                estimated=True,
                note=f"{spec['note']}（时长来自高德，费用为估算）",
            )
        )

    # 打车用驾车时长推算（路况相同），车费估算。
    drive = next((leg for leg in legs if leg.mode == "drive"), None)
    base_distance = drive.distance_m if drive else haversine_m(origin, destination)
    legs.append(
        Leg(
            mode="taxi",
            label=MODES["taxi"]["label"],
            distance_m=base_distance,
            minutes=base_distance / MODES["taxi"]["speed_m_per_min"] * 60 / 60,
            cost_yuan=_taxi_fare(base_distance),
            estimated=True,
            note=MODES["taxi"]["note"],
        )
    )
    return legs


#: 短距离下不推荐机动方式的阈值。200 米坐公交在数学上"最省"，
#: 但在现实里是荒谬建议，所以近处一律以步行/骑行为准。
SHORT_TRIP_M = 1200.0

#: 这个距离内不折腾：直接走路。
WALK_FIRST_M = 800.0
SHORT_TRIP_MODES = ("walk", "bike")


def best_mode(legs: list[Leg], hourly_rate: float, goal: str = "cheapest") -> Leg | None:
    """在所有出行方式里选一个。

    ``goal``:
      - ``cheapest``   总成本最低
      - ``fastest``    耗时最短
      - ``no_taxi``    排除打车（用户不想打车时）
    """
    candidates = [leg for leg in legs if goal != "no_taxi" or leg.mode != "taxi"]
    if not candidates:
        return None
    # 短距离不是"成本比较"问题，是可行性问题：1 公里内没人会坐公交或打车，
    # 哪怕算出来更便宜。所以这里直接按距离裁剪候选，而不是让成本决定。
    if candidates and candidates[0].distance_m <= SHORT_TRIP_M:
        if candidates[0].distance_m <= WALK_FIRST_M:
            walk_only = [leg for leg in candidates if leg.mode == "walk"]
            if walk_only:
                return walk_only[0]
        short = [leg for leg in candidates if leg.mode in SHORT_TRIP_MODES]
        if short:
            candidates = short
    if goal == "fastest":
        return min(candidates, key=lambda leg: leg.minutes)
    return min(candidates, key=lambda leg: leg.cost_yuan + leg.minutes * hourly_rate / 60.0)


def compare_with_delivery(
    legs: list[Leg], delivery_gap_yuan: float, hourly_rate: float
) -> list[dict]:
    """每种出行方式 vs 外送，谁更划算。

    外送的时间成本按 0 计（等待时间不计入），这是对自提偏保守的假设：
    如果连这样自提都不划算，那结论就很稳。
    """
    rows: list[dict] = []
    for leg in legs:
        time_cost = leg.minutes * hourly_rate / 60.0
        total = leg.cost_yuan + time_cost
        # 正数 = 这种方式比外送省的净额；负数 = 这种方式比外送更贵。
        net_vs_delivery = delivery_gap_yuan - total
        rows.append({
            "mode": leg.mode,
            "label": leg.label,
            "minutes": leg.minutes,
            "cost_yuan": leg.cost_yuan,
            "time_cost_yuan": time_cost,
            "total_yuan": total,
            "net_vs_delivery_yuan": net_vs_delivery,
            # 只有「总成本严格低于外送差价」才算自提占优。
            # 用 > 0 而不是 >=，避免打平被算成占优。
            "go_pickup": net_vs_delivery > 0,
            "note": leg.note,
        })
    rows.sort(key=lambda row: row["total_yuan"])
    return rows
