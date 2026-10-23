"""Store-to-store price comparison for one basket, across nearby branches.

This is the "which branch do I actually walk to" half of the project. It reuses
the menu cache, so it runs with no token and no network.

Two facts make the comparison meaningful, both verified against the live API:

1. Menu breadth differs by branch. In 人民广场 the five branches within 412 m
   carried 120 / 117 / 115 / 117 / 101 items respectively.
2. Unit prices differ by branch. Big Mac across 33 sampled Shanghai branches
   takes three distinct prices (25.50 / 26.00 / 26.50 CNY).

Coupons, by contrast, did *not* differ across those five branches, so the
difference is in the shelf price itself, not in the promotions shown.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .geo import haversine_m
from .menu import extract_meals, load_menu, resolve_item
from .stores import Store


@dataclass
class BranchQuote:
    store: Store
    distance_m: float
    item_prices: dict[str, float] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    menu_size: int = 0

    @property
    def total_yuan(self) -> float | None:
        if self.missing or not self.item_prices:
            return None
        return round(sum(self.item_prices.values()), 2)

    @property
    def walk_minutes(self) -> float:
        return self.distance_m / 75.0


def compare_branches(
    stores: list[Store],
    wants: list[str],
    origin,
    radius_m: float = 3000.0,
    limit: int = 10,
) -> list[BranchQuote]:
    """Price one basket at every branch with a cached menu inside ``radius_m``."""
    quotes: list[BranchQuote] = []
    candidates = [(s, haversine_m(origin, s.point)) for s in stores]
    candidates = [(s, d) for s, d in candidates if d <= radius_m]
    candidates.sort(key=lambda pair: pair[1])

    for store, distance in candidates[:limit]:
        menu = load_menu(store.code)
        if not menu:
            continue
        meals = extract_meals(menu)
        if not meals:
            continue
        prices: dict[str, float] = {}
        missing: list[str] = []
        for want in wants:
            hit = resolve_item(meals, want)
            if hit is None:
                missing.append(want)
                continue
            code, name = hit
            price = meals[code]["price"]
            if price is None:
                missing.append(want)
            else:
                prices[name] = price
        quotes.append(
            BranchQuote(
                store=store,
                distance_m=distance,
                item_prices=prices,
                missing=missing,
                menu_size=len(meals),
            )
        )
    return quotes


def spread(quotes: list[BranchQuote]) -> dict:
    """Summarise the price and range differences across the quoted branches."""
    complete = [q for q in quotes if q.total_yuan is not None]
    if len(complete) < 2:
        return {"complete": len(complete), "comparable": len(complete) < 2}
    cheapest = min(complete, key=lambda q: (q.total_yuan, q.distance_m))
    dearest = max(complete, key=lambda q: q.total_yuan)
    closest = min(complete, key=lambda q: q.distance_m)
    sizes = [q.menu_size for q in complete]
    return {
        "comparable": True,
        "complete": len(complete),
        "cheapest": cheapest,
        "dearest": dearest,
        "closest": closest,
        "price_gap": round(dearest.total_yuan - cheapest.total_yuan, 2),
        "price_gap_pct": round(
            (dearest.total_yuan - cheapest.total_yuan) / dearest.total_yuan * 100, 1
        ),
        "menu_min": min(sizes),
        "menu_max": max(sizes),
        "menu_gap": max(sizes) - min(sizes),
        "extra_walk_m": round(cheapest.distance_m - closest.distance_m),
    }
