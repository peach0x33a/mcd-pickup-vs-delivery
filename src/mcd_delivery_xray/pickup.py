"""Pickup route analysis: which nearby McDonald's is actually worth walking to.

The insight this module encodes is narrow and factual:

    Prices and menus are NOT uniform across McDonald's stores.
    Two stores 200 m apart can differ by ~1 CNY on a Big Mac and by 19 items
    on their menu, while carrying an identical set of coupons.

So "the closest store" and "the cheapest store" are frequently different
stores. This module quantifies the trade-off in a form a person can act on:
*"walk 200 m more, save 1 CNY"*.

Two data sources are kept strictly separate:

* ``menu``  - per-store menu snapshot (``query-meals``), used for the cheap
              screening pass and to know which item codes a store even sells.
* ``priced`` - authoritative per-store totals from ``calculate-price``,
              which is the number we show to the user.

The screening pass exists only to avoid spending an authoritative call on
stores that cannot be competitive. Every number that reaches the output has
been confirmed by ``calculate-price``.

READ-ONLY: this module never creates, cancels or modifies anything.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .geo import haversine_m
from .stores import Store


@dataclass
class BasketLine:
    """One requested item, resolved to whatever code a given store uses."""

    query: str
    quantity: int = 1


@dataclass
class StoreOffer:
    """What one store would charge for the basket, plus how far it is."""

    store: Store
    distance_m: float
    total_fen: int | None
    menu_size: int
    missing: list[str] = field(default_factory=list)
    authoritative: bool = False

    @property
    def total_yuan(self) -> float | None:
        return None if self.total_fen is None else self.total_fen / 100.0

    @property
    def walk_minutes(self) -> float:
        """Rough walking time at a calm 75 m/min, used when no routing API is set."""
        return self.distance_m / 75.0


@dataclass
class PickupRecommendation:
    """The ranked answer, plus the reasoning behind it."""

    origin: str
    offers: list[StoreOffer]
    cheapest: StoreOffer | None
    closest: StoreOffer | None
    routing: str  # "straight-line" or "amap-walking"

    @property
    def best(self) -> StoreOffer | None:
        """The store that wins on distance AND price when possible."""
        usable = [o for o in self.offers if o.total_fen is not None]
        if not usable:
            return None
        # Prefer the cheapest; break ties by distance, then by menu breadth.
        return min(usable, key=lambda o: (o.total_fen, o.distance_m, -o.menu_size))

    def tradeoff_note(self) -> str:
        """A one-line explanation of the gap between closest and cheapest."""
        best, closest = self.best, self.closest
        if not best or not closest or best.total_fen is None or closest.total_fen is None:
            return "无法比较：缺少可用报价。"
        if best.store.code == closest.store.code:
            return "最近的一家同时也是最便宜的，直接去这家。"
        delta_m = best.distance_m - closest.distance_m
        delta_fen = closest.total_fen - best.total_fen
        if delta_fen <= 0:
            return "最近的一家并不更贵，建议直接去最近的那家。"
        minutes = delta_m / 75.0
        return (
            f"多走 {delta_m:.0f} 米（约 {minutes:.0f} 分钟），"
            f"省 {delta_fen / 100:.2f} 元。"
        )


def rank_offers(offers: Iterable[StoreOffer]) -> list[StoreOffer]:
    """Sort by price, then distance. Unpriced stores sort last."""
    return sorted(
        offers,
        key=lambda o: (o.total_fen is None, o.total_fen or 0, o.distance_m),
    )


def apply_routing(
    offers: list[StoreOffer],
    origin_latlng,
    route_fn=None,
) -> list[StoreOffer]:
    """Replace straight-line distances with real walking distances when possible.

    ``route_fn`` is an optional callable ``(origin, dest) -> (metres, minutes)``
    supplied by the AMap adapter. When absent the straight-line distance is kept
    and the caller reports ``routing="straight-line"`` so the output never
    implies more precision than it has.
    """
    if route_fn is None:
        return offers
    routed: list[StoreOffer] = []
    for offer in offers:
        try:
            metres, _minutes = route_fn(origin_latlng, offer.store.point)
        except Exception:
            # A routing failure must degrade to the straight-line estimate
            # rather than dropping the store from the comparison entirely.
            routed.append(offer)
            continue
        routed.append(
            StoreOffer(
                store=offer.store,
                distance_m=metres,
                total_fen=offer.total_fen,
                menu_size=offer.menu_size,
                missing=list(offer.missing),
                authoritative=offer.authoritative,
            )
        )
    return routed
