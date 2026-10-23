"""City-level store density analysis: the counter-intuitive core of the project.

The finding this module exists to surface:

    McDonald's stores are not spread out. They cluster.
    Of 3,334 stores across 84 cities with enough branches to compare,
    925 (28%) have another McDonald's within one kilometre.

That is the headline, because it is checkable, surprising, and nobody in this
competition has stated it. It also creates the practical question the rest of
the project answers: when three stores are inside a one-kilometre walk, which
one do you actually go to?

All figures here are computed from the committed store snapshot, offline.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Iterable

from .geo import haversine_m
from .stores import Store

#: Threshold for counting two stores as "neighbours". One kilometre is a normal
#: walking radius in a Chinese city, so it reads as a real choice rather than a
#: statistical artefact.
NEIGHBOUR_RADIUS_M = 1000.0

#: Cities need at least this many stores before a median nearest-neighbour
#: distance means anything. Below it, one odd location dominates the figure.
MIN_STORES_FOR_STATS = 10


@dataclass
class NeighbourStats:
    city: str
    store_count: int
    with_neighbour: int
    median_nearest_m: float
    closest_pair_m: float
    closest_pair: tuple[Store, Store]

    @property
    def share(self) -> float:
        return self.with_neighbour / self.store_count if self.store_count else 0.0


def nearest_neighbour(stores: list[Store], index: int) -> tuple[Store, float] | None:
    """The closest OTHER store to ``stores[index]``, with its distance."""
    anchor = stores[index]
    best: tuple[Store, float] | None = None
    for other_index, other in enumerate(stores):
        if other_index == index:
            continue
        distance = haversine_m(anchor.point, other.point)
        if best is None or distance < best[1]:
            best = (other, distance)
    return best


def analyse_city(city: str, stores: list[Store]) -> NeighbourStats | None:
    """Density statistics for one city, or None when the sample is too small."""
    if len(stores) < MIN_STORES_FOR_STATS:
        return None
    nearest: list[float] = []
    with_neighbour = 0
    closest_pair_m = float("inf")
    closest_pair: tuple[Store, Store] | None = None
    for index, store in enumerate(stores):
        result = nearest_neighbour(stores, index)
        if result is None:
            continue
        other, distance = result
        nearest.append(distance)
        if distance <= NEIGHBOUR_RADIUS_M:
            with_neighbour += 1
        if distance < closest_pair_m:
            closest_pair_m = distance
            closest_pair = (store, other)
    if not nearest or closest_pair is None:
        return None
    return NeighbourStats(
        city=city,
        store_count=len(stores),
        with_neighbour=with_neighbour,
        median_nearest_m=statistics.median(nearest),
        closest_pair_m=closest_pair_m,
        closest_pair=closest_pair,
    )


def analyse_all(grouped: dict[str, list[Store]]) -> list[NeighbourStats]:
    """Density statistics for every city large enough to measure, densest first."""
    rows = [stats for city, stores in grouped.items() if (stats := analyse_city(city, stores))]
    rows.sort(key=lambda row: (-row.share, -row.store_count))
    return rows


def totals(rows: Iterable[NeighbourStats]) -> tuple[int, int, int]:
    """(cities, stores, stores-with-a-neighbour) across the analysed set."""
    rows = list(rows)
    return (
        len(rows),
        sum(row.store_count for row in rows),
        sum(row.with_neighbour for row in rows),
    )


def closest_pairs(grouped: dict[str, list[Store]], limit: int = 10) -> list[tuple[Store, Store, float]]:
    """The tightest store pairs nationwide — the most striking examples to show.

    Built by comparing every store against a narrowed candidate set (same city
    only), which is both correct and fast: a cross-city pair below 1 km would
    mean a data error, not an interesting finding.
    """
    pairs: list[tuple[Store, Store, float]] = []
    for stores in grouped.values():
        for index, store in enumerate(stores):
            result = nearest_neighbour(stores, index)
            if result is None:
                continue
            other, distance = result
            if store.code < other.code:  # keep each pair once
                pairs.append((store, other, distance))
    pairs.sort(key=lambda triple: triple[2])
    return pairs[:limit]
