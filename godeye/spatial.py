"""Section geometry cache + bbox helpers shared by the API and the analytics engine."""
from __future__ import annotations

import json
import math

BBox = tuple[float, float, float, float]  # west, south, east, north

_section_cache: dict[str, tuple[list, BBox]] = {}


def section_geom(row) -> tuple[list, BBox]:
    hit = _section_cache.get(row["id"])
    if hit is None:
        lines = json.loads(row["lines"])
        xs = [p[0] for ln in lines for p in ln]
        ys = [p[1] for ln in lines for p in ln]
        hit = _section_cache[row["id"]] = (lines, (min(xs), min(ys), max(xs), max(ys)))
    return hit


def intersects(a: BBox, b: BBox) -> bool:
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def sections_in(db, bbox: BBox) -> list[dict]:
    out = []
    for s in db.sections_latest():
        lines, sb = section_geom(s)
        if intersects(sb, bbox):
            out.append({**dict(s), "lines": lines})
    return out


def midpoint(lines: list) -> list[float]:
    ln = lines[0]
    return ln[len(ln) // 2]


def km(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = (a[0] - b[0]) * 111.32 * math.cos(math.radians((a[1] + b[1]) / 2))
    dy = (a[1] - b[1]) * 110.57
    return math.hypot(dx, dy)
