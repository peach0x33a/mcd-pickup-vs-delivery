"""Pickup vs delivery *value* comparison — the core of the project.

A price difference alone is not a decision. "Delivery costs 10 CNY more" means
nothing until you know what you are buying with those 10 CNY: a saved walk, a
saved queue, a saved trip. This module turns the raw price gap into an
answerable question.

The model, stated plainly so it can be argued with:

    cost of picking up  = item price at the store
                        + your own walking time valued at an hourly rate
    cost of delivery    = item price on the delivery channel
                        + delivery fee
                        + packing fee

    delivery wins when  (pickup_walk_minutes * hourly_rate / 60) > price_gap

Both sides are reported in full. The hourly rate is a user-supplied assumption
with a sensible default, never presented as a fact. Nothing here claims to
measure anyone's real wage; it is a knob for reasoning, and the CLI prints it.

Everything is measured against one real address, so the comparison is concrete
rather than abstract. It needs no MCP token once the channel data is collected:
`tools/collect_channels.py` writes the raw payloads into the repo and this
module replays them offline.

READ-ONLY throughout.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .geo import LatLng, haversine_m

#: Walking speed used when no routing API is configured. 75 m/min is a calm,
#: commonly cited urban pace; faster than 90 would flatter pickup too much.
WALK_M_PER_MIN = 75.0

#: Default value of the user's time. Deliberately conservative and shown in the
#: output so nobody mistakes it for a measured figure.
DEFAULT_HOURLY_RATE = 30.0


@dataclass
class ChannelPrice:
    """What one channel charges for one item, decomposed the way the API does."""

    product_fen: int
    delivery_fen: int = 0
    packing_fen: int = 0

    @property
    def total_fen(self) -> int:
        return self.product_fen + self.delivery_fen + self.packing_fen

    @property
    def total_yuan(self) -> float:
        return self.total_fen / 100.0


@dataclass
class ItemComparison:
    """One item, priced both ways, at one store, seen from one address."""

    name: str
    code: str
    store_code: str
    store_name: str
    pickup: ChannelPrice
    delivery: ChannelPrice
    walk_distance_m: float
    walk_minutes: float
    hourly_rate: float = DEFAULT_HOURLY_RATE

    # ---- money ------------------------------------------------
    @property
    def price_gap_fen(self) -> int:
        return self.delivery.total_fen - self.pickup.total_fen

    @property
    def price_gap_yuan(self) -> float:
        return self.price_gap_fen / 100.0

    @property
    def price_gap_pct(self) -> float:
        if self.pickup.total_fen == 0:
            return 0.0
        return self.price_gap_fen / self.pickup.total_fen * 100.0

    @property
    def product_markup_fen(self) -> int:
        """The part of the gap that is the item itself being dearer."""
        return self.delivery.product_fen - self.pickup.product_fen

    @property
    def product_markup_pct(self) -> float:
        if self.pickup.product_fen == 0:
            return 0.0
        return self.product_markup_fen / self.pickup.product_fen * 100.0

    @property
    def overhead_fen(self) -> int:
        """Fixed costs of delivery: the part you pay regardless of what you order."""
        return self.delivery.delivery_fen + self.delivery.packing_fen

    @property
    def product_markup_yuan(self) -> float:
        return self.product_markup_fen / 100.0

    @property
    def overhead_yuan(self) -> float:
        return self.overhead_fen / 100.0

    # ---- the value part ---------------------------------------
    @property
    def walk_cost_yuan(self) -> float:
        """What the walk is worth at the stated hourly rate, round trip.

        Round trip, because you walk there and back. One-way would understate
        the cost of pickup and bias the conclusion toward delivery.
        """
        return self.walk_minutes * 2 * self.hourly_rate / 60.0

    @property
    def walk_cost_fen(self) -> int:
        return round(self.walk_cost_yuan * 100)

    @property
    def net_yuan(self) -> float:
        """Positive means delivery is cheaper once the walk is valued.

        = walk value - price gap
        """
        return self.walk_cost_yuan - self.price_gap_yuan

    @property
    def verdict(self) -> str:
        if self.net_yuan > 1.0:
            return "外卖更划算"
        if self.net_yuan < -1.0:
            return "自提更划算"
        return "两者接近"

    @property
    def breakeven_minutes(self) -> float:
        """Walk time at which the two channels break even, at this rate.

        Tells the reader where the tipping point is, not just which side of it
        this particular store happens to sit on.
        """
        if self.hourly_rate <= 0:
            return float("inf")
        return self.price_gap_yuan * 60.0 / (2.0 * self.hourly_rate)


@dataclass
class StoreOption:
    """All delivery-reachable stores for one address, with comparison rows."""

    store_code: str
    store_name: str
    walk_distance_m: float
    items: list[ItemComparison] = field(default_factory=list)

    @property
    def walk_minutes(self) -> float:
        return self.walk_distance_m / WALK_M_PER_MIN

    @property
    def best_item(self) -> ItemComparison | None:
        """The item with the largest absolute saving if you walk instead."""
        if not self.items:
            return None
        return max(self.items, key=lambda item: item.price_gap_fen)


def summarise_rows(rows: list[ItemComparison]) -> dict:
    """Aggregate across items: how many favour pickup, by how much."""
    if not rows:
        return {"count": 0}
    pickup_wins = [r for r in rows if r.net_yuan < -1.0]
    delivery_wins = [r for r in rows if r.net_yuan > 1.0]
    return {
        "count": len(rows),
        "pickup_wins": len(pickup_wins),
        "delivery_wins": len(delivery_wins),
        "median_gap_yuan": _median([r.price_gap_yuan for r in rows]),
        "median_gap_pct": _median([r.price_gap_pct for r in rows]),
        "median_overhead_fen": _median([r.overhead_fen for r in rows], integer=True),
        "max_gap_yuan": max(r.price_gap_yuan for r in rows),
        "max_gap_name": max(rows, key=lambda r: r.price_gap_fen).name,
    }


def _median(values: list[float], integer: bool = False) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    middle = len(ordered) // 2
    value = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2
    )
    return round(value) if integer else round(value, 2)


def build_comparison(
    *,
    name: str,
    code: str,
    store_code: str,
    store_name: str,
    pickup: ChannelPrice,
    delivery: ChannelPrice,
    address: LatLng,
    store_point: LatLng,
    hourly_rate: float = DEFAULT_HOURLY_RATE,
) -> ItemComparison:
    distance = haversine_m(address, store_point)
    return ItemComparison(
        name=name,
        code=code,
        store_code=store_code,
        store_name=store_name,
        pickup=pickup,
        delivery=delivery,
        walk_distance_m=distance,
        walk_minutes=distance / WALK_M_PER_MIN,
        hourly_rate=hourly_rate,
    )
