"""Optional AMap (高德地图) enhancement layer.

Design rule for this whole project: **the tool must be fully usable with zero
API keys.** AMap is a pure enhancement, never a requirement.

Without a key:
    distances are great-circle (haversine), clearly labelled "直线距离".

With ``AMAP_KEY`` set (a "Web服务" key, not a JS key):
    distances become real walking routes ("步行距离"), and we can attach a
    walking duration instead of the rough 75 m/min estimate.

Why a separate layer instead of trusting the coordinates directly: the live MCP
endpoint has been observed returning store coordinates that do not match
GCJ-02 landmarks by 110-170 m. Pasting those straight onto a Chinese web map
produces a visibly wrong pin. So when AMap is configured we never place raw MCP
coordinates on the map; we reverse-geocode to a structured address and let AMap
provide the authoritative position for that address.

Docs: https://lbs.amap.com/api/webservice/summary
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .geo import LatLng

AMAP_BASE = "https://restapi.amap.com/v3"
CACHE_DIR_NAME = "amap_cache"


class AmapError(RuntimeError):
    pass


def amap_key() -> str:
    """Read the key from the environment; empty string means 'not configured'."""
    return os.environ.get("AMAP_KEY", "").strip()


def enabled() -> bool:
    return bool(amap_key())


@dataclass
class WalkRoute:
    distance_m: float
    duration_s: float

    @property
    def minutes(self) -> float:
        return self.duration_s / 60.0

    @property
    def note(self) -> str:
        return f"{self.distance_m:.0f} 米 / 约 {self.minutes:.0f} 分钟（高德步行）"


class AmapClient:
    """Minimal AMap Web-API client: walking routes and geocoding only.

    Only the two endpoints this project actually needs are implemented, so the
    surface area (and the chance of misusing it) stays small.
    """

    def __init__(self, key: str | None = None, timeout: float = 10.0) -> None:
        resolved = key if key is not None else amap_key()
        if not resolved:
            raise AmapError("AMAP_KEY 未设置；高德增强层不可用（项目仍可离线运行）")
        self.key = resolved
        self.timeout = timeout
        self._route_cache: dict[tuple[tuple[float, float], tuple[float, float]], WalkRoute] = {}

    def _get(self, path: str, params: dict, version: int = 3) -> dict:
        query = dict(params)
        query["key"] = self.key
        base = AMAP_BASE if version == 3 else f"https://restapi.amap.com/v{version}"
        url = f"{base}/{path}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url, headers={"User-Agent": "mcd-pickup-vs-delivery/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            raise AmapError(f"高德请求失败: {exc}") from exc
        if str(payload.get("status")) != "1":
            info = payload.get("info") or payload.get("infocode") or "unknown"
            raise AmapError(f"高德返回错误: {info}")
        return payload

    def walking_route(self, origin: LatLng, destination: LatLng) -> WalkRoute:
        """Real walking distance between two points."""
        cache_key = ((origin.lat, origin.lng), (destination.lat, destination.lng))
        if cache_key in self._route_cache:
            return self._route_cache[cache_key]
        # AMap expects longitude,latitude order.
        payload = self._get(
            "direction/walking",
            {
                "origin": f"{origin.lng:.6f},{origin.lat:.6f}",
                "destination": f"{destination.lng:.6f},{destination.lat:.6f}",
            },
        )
        paths = ((payload.get("route") or {}).get("paths")) or []
        if not paths:
            raise AmapError("高德未返回步行路径")
        first = paths[0]
        route = WalkRoute(
            distance_m=float(first.get("distance") or 0),
            duration_s=float(first.get("duration") or 0),
        )
        self._route_cache[cache_key] = route
        return route

    def bicycling_route(self, origin: LatLng, destination: LatLng) -> WalkRoute:
        """骑行路径规划（v4 接口，最大 500km）。"""
        payload = self._get(
            "direction/bicycling",
            {
                "origin": f"{origin.lng:.6f},{origin.lat:.6f}",
                "destination": f"{destination.lng:.6f},{destination.lat:.6f}",
            },
            version=4,
        )
        paths = ((payload.get("data") or {}).get("paths")) or []
        if not paths:
            raise AmapError("高德未返回骑行路径")
        first = paths[0]
        return WalkRoute(
            distance_m=float(first.get("distance") or 0),
            duration_s=float(first.get("duration") or 0),
        )

    def driving_route(self, origin: LatLng, destination: LatLng) -> WalkRoute:
        """驾车路径规划。"""
        payload = self._get(
            "direction/driving",
            {
                "origin": f"{origin.lng:.6f},{origin.lat:.6f}",
                "destination": f"{destination.lng:.6f},{destination.lat:.6f}",
            },
        )
        paths = ((payload.get("route") or {}).get("paths")) or []
        if not paths:
            raise AmapError("高德未返回驾车路径")
        first = paths[0]
        return WalkRoute(
            distance_m=float(first.get("distance") or 0),
            duration_s=float(first.get("duration") or 0),
        )

    def transit_route(self, origin: LatLng, destination: LatLng, city: str) -> WalkRoute:
        """公交路径规划。需要城市名或 citycode；票价不在返回结果中。"""
        payload = self._get(
            "direction/transit/integrated",
            {
                "origin": f"{origin.lng:.6f},{origin.lat:.6f}",
                "destination": f"{destination.lng:.6f},{destination.lat:.6f}",
                "city": city,
            },
        )
        transits = ((payload.get("route") or {}).get("transits")) or []
        if not transits:
            raise AmapError("高德未返回公交方案")
        first = transits[0]
        return WalkRoute(
            distance_m=float(first.get("distance") or 0),
            duration_s=float(first.get("duration") or 0),
        )

    def regeocode(self, point: LatLng) -> dict:
        """Structured address for a coordinate (used to calibrate MCP coordinates)."""
        payload = self._get(
            "geocode/regeo",
            {"location": f"{point.lng:.6f},{point.lat:.6f}", "extensions": "base"},
        )
        return payload.get("regeocode") or {}

    def geocode(self, address: str, city: str | None = None) -> LatLng | None:
        """Coordinates for a textual address, as the map provider itself resolves it."""
        params = {"address": address}
        if city:
            params["city"] = city
        payload = self._get("geocode/geo", params)
        geocodes = payload.get("geocodes") or []
        if not geocodes:
            return None
        location = geocodes[0].get("location") or ""
        try:
            lng_str, lat_str = location.split(",")
            return LatLng(float(lat_str), float(lng_str))
        except ValueError:
            return None


def route_fn():
    """A ``(origin, dest) -> (metres, minutes)`` callable for ``apply_routing``.

    Returns None when no key is configured, which makes every caller fall back
    to straight-line distances and label the output honestly.
    """
    if not enabled():
        return None
    client = AmapClient()

    def _route(origin: LatLng, destination: LatLng) -> tuple[float, float]:
        route = client.walking_route(origin, destination)
        return route.distance_m, route.minutes

    return _route


def status_line() -> str:
    """One-line description of the current enhancement level, for the CLI header."""
    if enabled():
        return "距离口径：高德步行路线（真实路径）"
    return "距离口径：直线距离（未配置 AMAP_KEY，可用高德步行增强）"
