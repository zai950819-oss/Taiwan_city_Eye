"""Anomaly engine: compares every road section / camera in view to *its own* normal.

Baseline = same entity, same hour of day (±1h), history older than 30 min.
Rules (tunable below):
  slowdown   section speed ≤ 60% of its usual speed
  surge      camera count ≥ 3σ above its usual count
  queue      ≥ 70% of visible vehicles didn't move between two frames
  stopped    a stationary vehicle on a freeway/highway camera with light traffic
  condition  CLIP tags flood / smoke / fog / wet
Each alert is keyed by kind+entity: it opens once, updates while active, resolves when normal.
"""
from __future__ import annotations

import time

from .spatial import BBox, km, midpoint, sections_in

SLOW_RATIO = 0.6
SLOW_MIN_BASE = 45       # km/h — ignore sections that are always slow
SLOW_MIN_DROP = 20       # km/h
SURGE_Z = 3.0
SURGE_MIN_TOTAL = 15
QUEUE_MOVING = 0.3
QUEUE_MIN_TOTAL = 8
MIN_SAMPLES_SECTION = 3
MIN_SAMPLES_CAMERA = 6
FRESH_S = 900
RAIN_NEAR_KM = 6
RAIN_MM = 8

CONDITION_ALERTS = {  # label → (severity, min confidence, message)
    "flood": (3, 0.45, "Possible flooding on the road"),
    "smoke": (3, 0.45, "Smoke or fire visible"),
    "fog": (2, 0.5, "Low visibility — fog"),
    "wet": (1, 0.55, "Wet road / heavy rain"),
}


def _rain_note(rain: list, lon: float, lat: float) -> str:
    near = [r for r in rain if r["past1h"] and r["past1h"] >= RAIN_MM and km((lon, lat), (r["lon"], r["lat"])) < RAIN_NEAR_KM]
    if not near:
        return ""
    mm = max(r["past1h"] for r in near)
    return f" · raining {mm:.0f} mm/h nearby"


def candidates(db, bbox: BBox, now: float | None = None) -> list[dict]:
    now = now or time.time()
    hour = time.localtime(now).tm_hour
    before = now - 1800
    rain = [dict(r) for r in db.rain_in_bbox(bbox)]
    out: list[dict] = []

    # ---- road sections ----
    secs = [s for s in sections_in(db, bbox) if s["speed"] is not None]
    base = db.section_baselines([s["id"] for s in secs], hour, before)
    for s in secs:
        mean, n = base.get(s["id"], (None, 0))
        if n < MIN_SAMPLES_SECTION or mean is None or mean < SLOW_MIN_BASE:
            continue
        cur = s["speed"]
        if cur <= SLOW_RATIO * mean and mean - cur >= SLOW_MIN_DROP:
            ratio = cur / mean
            lon, lat = midpoint(s["lines"])
            out.append({
                "key": f"slowdown:{s['id']}", "kind": "slowdown", "entity_type": "section",
                "entity_id": s["id"], "name": s["name"] or s["id"], "lon": lon, "lat": lat,
                "severity": 3 if ratio < 0.35 else 2 if ratio < 0.5 else 1,
                "message": f"{cur:.0f} km/h — {100 - ratio * 100:.0f}% slower than usual ({mean:.0f})"
                           + _rain_note(rain, lon, lat),
            })

    # ---- cameras ----
    cams = [c for c in db.cameras_in_bbox(bbox) if c["v_ts"] and now - c["v_ts"] < FRESH_S]
    cbase = db.camera_baselines([c["id"] for c in cams], hour, before)
    for c in cams:
        common = {"entity_type": "camera", "entity_id": c["id"], "name": c["name"],
                  "lon": c["lon"], "lat": c["lat"]}
        total, moving = c["total"] or 0, c["moving"]

        mean, std, n = cbase.get(c["id"], (None, 0.0, 0))
        if n >= MIN_SAMPLES_CAMERA and mean is not None and total >= SURGE_MIN_TOTAL:
            z = (total - mean) / max(std, 2.0)
            if z >= SURGE_Z:
                out.append({**common, "key": f"surge:{c['id']}", "kind": "surge",
                            "severity": 2 if z < 5 else 3,
                            "message": f"Traffic surge: {total} vehicles (usual ~{mean:.0f})"})

        if moving is not None and total >= QUEUE_MIN_TOTAL and moving < QUEUE_MOVING:
            out.append({**common, "key": f"queue:{c['id']}", "kind": "queue",
                        "severity": 3 if moving < 0.1 else 2,
                        "message": f"Queue detected — {100 - moving * 100:.0f}% of {total} vehicles stationary"
                                   + _rain_note(rain, c["lon"], c["lat"])})
        elif (moving is not None and total and total <= 4 and moving < 0.5
              and c["source"] in ("Freeway", "Highway")):
            out.append({**common, "key": f"stopped:{c['id']}", "kind": "stopped", "severity": 2,
                        "message": "Possible stopped vehicle in light traffic"})

        cond = c["condition"]
        if cond in CONDITION_ALERTS:
            sev, min_conf, msg = CONDITION_ALERTS[cond]
            if (c["cond_conf"] or 0) >= min_conf:
                out.append({**common, "key": f"cond:{c['id']}", "kind": cond, "severity": sev,
                            "message": f"{msg} ({(c['cond_conf'] or 0) * 100:.0f}% confidence)"})
    return out


def sync_alerts(db, bbox: BBox, now: float | None = None) -> dict:
    """Open new alerts, refresh active ones, resolve the ones that returned to normal."""
    now = now or time.time()
    found = {a["key"]: a for a in candidates(db, bbox, now)}
    opened = 0
    for key, a in found.items():
        cur = db.active_alert(key)
        if cur:
            db.touch_alert(cur["id"], a["severity"], a["message"], now)
        else:
            db.open_alert(a, now)
            opened += 1
    stale = [r["id"] for r in db.alerts_in_bbox(bbox, now, active_only=True) if r["key"] not in found]
    db.resolve_alerts(stale, now)
    return {"active": len(found), "opened": opened, "resolved": len(stale)}
