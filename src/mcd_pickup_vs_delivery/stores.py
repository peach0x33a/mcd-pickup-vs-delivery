"""Store data access: load the cached snapshot and offer city-level queries.

The cache in `data/stores_raw.json` is produced by `tools/collect_stores.py`
and contains one record per (storeCode) with a real, non-zero latitude and
longitude. It is committed on purpose: every feature of this project must work
without an MCP token, so a reviewer can run the demo offline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .geo import LatLng, haversine_m

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SNAPSHOT = ROOT / "data" / "stores_raw.json"


class StoreDataError(RuntimeError):
    pass


#: Store names that the server uses for internal testing. They carry a real
#: city and real coordinates, so they are indistinguishable from genuine stores
#: unless filtered by name. Leaving them in skews every aggregate badly: all 11
#: sit on one coordinate in 吴忠市, which fabricated a "82% of stores have a
#: neighbour within 1 km" result for that city before this filter existed.
TEST_STORE_MARKERS = ("虚拟门店", "请勿下单", "测试门店")


def is_test_store(name: str) -> bool:
    return any(marker in name for marker in TEST_STORE_MARKERS)



@dataclass(frozen=True)
class Store:
    code: str
    name: str
    short_name: str
    city: str
    address: str
    lat: float
    lng: float
    distance_hint: int = 0

    @property
    def point(self) -> LatLng:
        return LatLng(self.lat, self.lng)

    @property
    def display(self) -> str:
        return self.short_name or self.name


def load_stores(path: Path | str | None = None) -> list[Store]:
    """Load every store from the snapshot, skipping records without coordinates.

    Stores reporting latitude/longitude of exactly 0.0 are dropped: the live
    `query-nearby-stores` endpoint returns 0.0 for every store, and a point at
    (0, 0) would poison every distance calculation downstream.
    """
    target = Path(path) if path else DEFAULT_SNAPSHOT
    if not target.exists():
        raise StoreDataError(
            f"store snapshot not found: {target}\n"
            "run `python3 tools/collect_stores.py` (needs MCD_MCP_TOKEN) to rebuild it"
        )
    payload = json.loads(target.read_text(encoding="utf-8"))
    raw_items = payload.values() if isinstance(payload, dict) else payload

    stores: list[Store] = []
    for item in raw_items:
        name = str(item.get("name") or "")
        if is_test_store(name):
            continue
        lat = _as_float(item.get("latitude"))
        lng = _as_float(item.get("longitude"))
        if lat is None or lng is None or (lat == 0.0 and lng == 0.0):
            continue
        stores.append(
            Store(
                code=str(item.get("code") or ""),
                name=str(item.get("name") or ""),
                short_name=str(item.get("shortName") or ""),
                city=str(item.get("cityName") or ""),
                address=str(item.get("address") or ""),
                lat=lat,
                lng=lng,
                distance_hint=int(_as_float(item.get("distance")) or 0),
            )
        )
    if not stores:
        raise StoreDataError(f"snapshot {target} contained no usable stores")
    return stores


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def by_city(stores: Iterable[Store]) -> dict[str, list[Store]]:
    """Group stores by city name."""
    grouped: dict[str, list[Store]] = {}
    for store in stores:
        grouped.setdefault(store.city, []).append(store)
    return grouped


def city_names(stores: Iterable[Store]) -> list[str]:
    return sorted({s.city for s in stores if s.city})


def nearest_stores(
    stores: Iterable[Store], origin: LatLng, radius_m: float | None = None, limit: int | None = None
) -> list[tuple[Store, float]]:
    """Stores sorted by great-circle distance from `origin`.

    Uses one consistent metric for every store, which is the only correct way
    to compare stores against each other: the API's own `distance` field is
    measured from whatever landmark the `keyword` resolved to, so it shifts
    when the keyword changes and is therefore not comparable across calls.
    """
    ranked = [(store, haversine_m(origin, store.point)) for store in stores]
    if radius_m is not None:
        ranked = [pair for pair in ranked if pair[1] <= radius_m]
    ranked.sort(key=lambda pair: pair[1])
    return ranked[:limit] if limit else ranked


def centroid(stores: Iterable[Store]) -> LatLng:
    points = [s.point for s in stores]
    if not points:
        raise StoreDataError("cannot compute a centroid of zero stores")
    return LatLng(sum(p.lat for p in points) / len(points), sum(p.lng for p in points) / len(points))
