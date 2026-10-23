"""Geometry helpers shared by the store-coverage and pickup-route features.

All functions are pure and operate on (lat, lng) pairs in decimal degrees.
Distance uses the haversine formula on a spherical Earth (mean radius
6371008.8 m). At city scale (a few km) the error versus an ellipsoid is well
under 0.5%, which is far below the granularity we report (10 m).
"""
from __future__ import annotations

import math
from typing import Iterable, NamedTuple

EARTH_RADIUS_M = 6371008.8


class LatLng(NamedTuple):
    lat: float
    lng: float


def haversine_m(a: LatLng, b: LatLng) -> float:
    """Great-circle distance in metres between two points."""
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlng = math.radians(b.lng - a.lng)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, h)))


def bounding_box(center: LatLng, radius_m: float) -> tuple[float, float, float, float]:
    """Axis-aligned lat/lng box that fully contains a circle.

    Returns (lat_min, lat_max, lng_min, lng_max). Used to pre-filter stores
    before the (more expensive) haversine call.
    """
    dlat = radius_m / 111_320.0
    # A degree of longitude shrinks with latitude; guard against the poles.
    cos_lat = max(math.cos(math.radians(center.lat)), 1e-6)
    dlng = radius_m / (111_320.0 * cos_lat)
    return (center.lat - dlat, center.lat + dlat, center.lng - dlng, center.lng + dlng)


def nearest(points: Iterable[LatLng], origin: LatLng, limit: int = 5) -> list[tuple[int, float]]:
    """Return (index, distance_m) for the `limit` closest points, ascending."""
    scored = [(i, haversine_m(origin, p)) for i, p in enumerate(points)]
    scored.sort(key=lambda pair: pair[1])
    return scored[:limit]


def circle_overlap_ratio(radius_a_m: float, radius_b_m: float, center_distance_m: float) -> float:
    """Fraction of circle A's area that lies inside circle B.

    0.0 means no overlap, 1.0 means A is entirely covered by B. Used by the
    coverage chapter to decide whether a store's service circle is redundant.
    """
    if center_distance_m >= radius_a_m + radius_b_m:
        return 0.0
    if center_distance_m <= abs(radius_a_m - radius_b_m) and radius_a_m <= radius_b_m:
        return 1.0
    a, b, d = radius_a_m, radius_b_m, center_distance_m
    # Lens area between two circles, via the standard circular segment formula.
    cos_a = (d * d + a * a - b * b) / (2 * d * a)
    cos_b = (d * d + b * b - a * a) / (2 * d * b)
    cos_a = max(-1.0, min(1.0, cos_a))
    cos_b = max(-1.0, min(1.0, cos_b))
    angle_a = 2 * math.acos(cos_a)
    angle_b = 2 * math.acos(cos_b)
    lens = 0.5 * a * a * (angle_a - math.sin(angle_a)) + 0.5 * b * b * (angle_b - math.sin(angle_b))
    return min(1.0, lens / (math.pi * a * a))


def sample_grid(bbox: tuple[float, float, float, float], step_m: float) -> list[LatLng]:
    """Sample a lat/lng box on a regular grid with approximately `step_m` spacing."""
    lat_min, lat_max, lng_min, lng_max = bbox
    mid_lat = (lat_min + lat_max) / 2
    dlat = step_m / 111_320.0
    dlng = step_m / (111_320.0 * max(math.cos(math.radians(mid_lat)), 1e-6))
    out: list[LatLng] = []
    lat = lat_min
    while lat <= lat_max:
        lng = lng_min
        while lng <= lng_max:
            out.append(LatLng(lat, lng))
            lng += dlng
        lat += dlat
    return out


def coverage_fraction(points: Iterable[LatLng], stores: list[LatLng], radius_m: float) -> float:
    """Share of sample points that fall within `radius_m` of at least one store."""
    points = list(points)
    if not points:
        return 0.0
    covered = 0
    for point in points:
        for store in stores:
            if haversine_m(point, store) <= radius_m:
                covered += 1
                break
    return covered / len(points)
