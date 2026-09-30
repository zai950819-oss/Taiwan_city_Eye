"""Synthetic Taiwan data so the whole pipeline runs without TDX keys.

Geometry is a rough approximation of 國道1/3/5 — good enough for a demo map,
not for navigation.
"""
from __future__ import annotations

import math
import os
import random
import time

ROUTES = {
    "國道1號": [(121.74, 25.13), (121.60, 25.07), (121.52, 25.06), (121.30, 25.00), (121.00, 24.80),
               (120.82, 24.56), (120.65, 24.18), (120.55, 24.00), (120.45, 23.70), (120.40, 23.45),
               (120.25, 23.05), (120.33, 22.70), (120.35, 22.60)],
    "國道3號": [(121.70, 25.10), (121.60, 24.98), (121.35, 24.90), (121.05, 24.70), (120.85, 24.40),
               (120.75, 24.10), (120.62, 23.85), (120.55, 23.50), (120.40, 23.15), (120.45, 22.75),
               (120.55, 22.45)],
    "國道5號": [(121.60, 24.99), (121.68, 24.88), (121.75, 24.80), (121.78, 24.65)],
}
SEGMENTS_PER_LEG = 6
# Fixed "incident" hotspots (lon, lat) that pulse during the day
HOTSPOTS = [(121.50, 25.05), (121.25, 24.98), (120.66, 24.16), (120.30, 22.95), (121.62, 24.97)]


def _interp(route: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = []
    for (x1, y1), (x2, y2) in zip(route, route[1:]):
        for i in range(SEGMENTS_PER_LEG):
            t = i / SEGMENTS_PER_LEG
            pts.append((round(x1 + (x2 - x1) * t, 5), round(y1 + (y2 - y1) * t, 5)))
    pts.append(route[-1])
    return pts


def cameras() -> list[dict]:
    rng = random.Random(42)
    out = []
    for road, route in ROUTES.items():
        for i, (lon, lat) in enumerate(_interp(route)):
            for d, off in (("N", 0.004), ("S", -0.004)):
                out.append({
                    "id": f"MOCK:{road}:{i}{d}",
                    "source": "Freeway",
                    "name": f"{road} {i * 5 + rng.randint(0, 4)}K {'北上' if d == 'N' else '南下'}",
                    "road": road,
                    "direction": d,
                    "lon": lon + off,
                    "lat": lat + off / 2,
                    "stream_url": "mock://",
                })
    return out


def section_shapes() -> list[dict]:
    out = []
    for road, route in ROUTES.items():
        pts = _interp(route)
        for i, (a, b) in enumerate(zip(pts, pts[1:])):
            for d, off in (("N", 0.0015), ("S", -0.0015)):
                out.append({
                    "id": f"MOCK:{road}:{i}{d}",
                    "name": f"{road} 段{i + 1} {'北上' if d == 'N' else '南下'}",
                    "lines": [[[a[0] + off, a[1]], [b[0] + off, b[1]]]],
                })
    return out


START = time.time()
INCIDENT_PERIOD = 900  # a new synthetic incident every 15 min (only after the server starts)


# Demo scripts can pin the scenario, e.g.
#   GODEYE_MOCK_INCIDENTS="121.5440,25.0410,crash;121.5200,25.0620,flood"
#   GODEYE_MOCK_RAIN="121.585,25.050"
def _parse_incidents() -> list[tuple[float, float, str]]:
    out = []
    for part in os.getenv("GODEYE_MOCK_INCIDENTS", "").split(";"):
        bits = [b.strip() for b in part.split(",")]
        if len(bits) == 3:
            out.append((float(bits[0]), float(bits[1]), bits[2]))
    return out


PINNED_INCIDENTS = _parse_incidents()
_rain_env = os.getenv("GODEYE_MOCK_RAIN", "").split(",")
PINNED_RAIN = (float(_rain_env[0]), float(_rain_env[1])) if len(_rain_env) == 2 else None


def incidents(t: float) -> list[tuple[float, float, str]]:
    """Fake incidents so anomaly alerts have something to find in mock mode."""
    if t < START:
        return []
    if PINNED_INCIDENTS:
        return PINNED_INCIDENTS
    k = int(t // INCIDENT_PERIOD)
    hx, hy = HOTSPOTS[k % len(HOTSPOTS)]
    return [(hx, hy, ("crash", "flood", "crash", "smoke")[k % 4])]


def incident(t: float) -> tuple[float, float, str] | None:
    inc = incidents(t)
    return inc[0] if inc else None


def _incident_near(lon: float, lat: float, t: float) -> tuple[float, str | None]:
    best, kind = 0.0, None
    for ix, iy, k in incidents(t):
        w = math.exp(-((lon - ix) ** 2 + (lat - iy) ** 2) / 0.00005)
        if w > best:
            best, kind = w, (k if w > 0.6 else None)
    return best, kind


def rain_mm(lon: float, lat: float, t: float) -> float:
    """A rain cell drifting NE across the island every ~3h (or pinned for demos)."""
    if PINNED_RAIN and t >= START:
        cx, cy = PINNED_RAIN
        d2 = (lon - cx) ** 2 + (lat - cy) ** 2
        return round(max(0.0, 42 * math.exp(-d2 / 0.00025) - 1), 1)
    phase = (t / 10800) % 1
    cx, cy = 120.1 + 1.8 * phase, 22.4 + 3.0 * phase
    d2 = (lon - cx) ** 2 + (lat - cy) ** 2
    return round(max(0.0, 35 * math.exp(-d2 / 0.08) - 1), 1)


def _pressure(lon: float, lat: float, t: float) -> float:
    """0 (free flow) → 1 (jam), driven by time of day + hotspot proximity + incidents."""
    hour = time.localtime(t).tm_hour + time.localtime(t).tm_min / 60
    rush = max(math.exp(-((hour - 8) ** 2) / 1.5), math.exp(-((hour - 18) ** 2) / 2.0))
    near = max(math.exp(-(((lon - hx) ** 2 + (lat - hy) ** 2) / 0.004)) for hx, hy in HOTSPOTS)
    wobble = 0.15 * math.sin(t / 300 + lon * 40)
    inc, _ = _incident_near(lon, lat, t)
    rain = min(1.0, rain_mm(lon, lat, t) / 30)
    return max(0.0, min(1.0, 0.15 + 0.35 * rush + 0.6 * near * (0.5 + rush) + wobble + 0.9 * inc + 0.2 * rain))


def _mid(lines: list) -> tuple[float, float]:
    a, b = lines[0][0], lines[0][-1]
    return (a[0] + b[0]) / 2, (a[1] + b[1]) / 2


_SHAPES = None


def live(t: float | None = None) -> list[dict]:
    global _SHAPES
    t = t or time.time()
    _SHAPES = _SHAPES or all_sections()
    out = []
    for s in _SHAPES:
        pts = [pt for ln in s["lines"] for pt in ln]
        sample = pts[:: max(1, len(pts) // 6)] + [pts[-1]]   # a few points along the section
        p = max(_pressure(x, y, t) for x, y in sample)
        speed = round(100 - 85 * p + random.uniform(-4, 4), 1)
        level = 1 if speed >= 60 else 2 if speed >= 35 else 3
        out.append({"section_id": s["id"], "speed": max(5.0, speed), "level": level})
    return out


def vision_counts(cam: dict, t: float | None = None) -> dict:
    p = _pressure(cam["lon"], cam["lat"], t or time.time())
    total = int(4 + 38 * p + random.uniform(-3, 3))
    cars = int(total * 0.72)
    trucks = int(total * 0.12)
    buses = int(total * 0.04)
    t = t or time.time()
    inc_w, kind = _incident_near(cam["lon"], cam["lat"], t)
    rain = rain_mm(cam["lon"], cam["lat"], t)
    hour = time.localtime(t).tm_hour
    if kind in ("flood", "smoke"):
        cond, conf = kind, 0.71
    elif rain > 4:
        cond, conf = "wet", round(min(0.9, 0.1 + rain / 60), 2)
    elif hour < 6 or hour >= 19:
        cond, conf = "night", 0.8
    else:
        cond, conf = "dry", 0.83
    return {"cars": cars, "motorcycles": max(0, total - cars - trucks - buses), "buses": buses,
            "trucks": trucks, "total": max(0, total),
            # vehicles only stop moving near an incident; ordinary rush hour stays "heavy but flowing"
            "moving": round(max(0.02, min(1.0, 1.0 - 0.95 * inc_w - 0.35 * max(0.0, p - 0.55)
                                          + random.uniform(-0.05, 0.05))), 2),
            "condition": cond, "cond_conf": conf}


def rain_gauges(t: float | None = None) -> list[dict]:
    t = t or time.time()
    out = []
    rng = random.Random(7)
    for i in range(160):
        lon, lat = rng.uniform(120.15, 121.85), rng.uniform(22.1, 25.25)
        if lon > 121.0 + (lat - 22.0) * 0.28 and lat < 24.5 and lon > 121.5:  # skip the sea off the east
            continue
        mm = rain_mm(lon, lat, t)
        out.append({"id": f"MOCK:RAIN:{i}", "name": f"測站{i:03d}", "lon": round(lon, 4), "lat": round(lat, 4),
                    "now": round(mm / 6, 1), "past1h": mm, "ts": t})
    # a few gauges inside Taipei so the city view has data
    for j, (lon, lat) in enumerate([(121.515, 25.04), (121.545, 25.03), (121.565, 25.05), (121.53, 25.06)]):
        mm = rain_mm(lon, lat, t)
        out.append({"id": f"MOCK:RAIN:TPE{j}", "name": f"台北{j}", "lon": lon, "lat": lat,
                    "now": round(mm / 6, 1), "past1h": mm, "ts": t})
    return out


# ---- Taipei city cameras (approximate arterial intersections) ----
TAIPEI_ROADS = {
    "忠孝東路": [(121.513, 25.0460), (121.5650, 25.0415)],
    "信義路": [(121.518, 25.0335), (121.5680, 25.0330)],
    "仁愛路": [(121.520, 25.0385), (121.5600, 25.0375)],
    "敦化南北路": [(121.5490, 25.0600), (121.5500, 25.0250)],
    "復興南北路": [(121.5440, 25.0620), (121.5430, 25.0200)],
    "基隆路": [(121.5580, 25.0500), (121.5390, 25.0120)],
    "中山北路": [(121.5220, 25.0480), (121.5250, 25.0950)],
    "市民大道": [(121.5050, 25.0480), (121.5700, 25.0450)],
}


def taipei_cameras(per_road: int = 8) -> list[dict]:
    out = []
    for road, ((x1, y1), (x2, y2)) in TAIPEI_ROADS.items():
        for i in range(per_road):
            t = i / (per_road - 1)
            out.append({
                "id": f"MOCK:TPE:{road}:{i}",
                "source": "City/Taipei",
                "name": f"{road} #{i + 1}",
                "road": road,
                "direction": "",
                "lon": round(x1 + (x2 - x1) * t, 5),
                "lat": round(y1 + (y2 - y1) * t, 5),
                "stream_url": "mock://",
            })
    return out


HOTSPOTS.append((121.5440, 25.0410))  # 忠孝復興 area


# ---- optional real geometry for demos -------------------------------------------------
# GODEYE_MOCK_EXTRA=path/to.json  {"bbox":[w,s,e,n], "cameras":[...], "sections":[...]}
# Real camera positions/streams and real freeway shapes replace the synthetic ones inside
# bbox; speeds, counts and incidents stay simulated.
def _load_extra() -> dict | None:
    path = os.getenv("GODEYE_MOCK_EXTRA")
    if not path or not os.path.exists(path):
        return None
    import json
    with open(path, encoding="utf-8") as f:
        return json.load(f)


EXTRA = _load_extra()


def _inside(b, lon, lat) -> bool:
    return b[0] <= lon <= b[2] and b[1] <= lat <= b[3]


def all_cameras() -> list[dict]:
    synth = cameras() + taipei_cameras()
    if not EXTRA:
        return synth
    b = EXTRA["bbox"]
    return [c for c in synth if not _inside(b, c["lon"], c["lat"])] + EXTRA["cameras"]


def all_sections() -> list[dict]:
    synth = section_shapes()
    if not EXTRA:
        return synth
    b = EXTRA["bbox"]
    return [s for s in synth if not _inside(b, *_mid(s["lines"]))] + EXTRA["sections"]
