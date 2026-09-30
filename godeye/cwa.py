"""中央氣象署 (CWA) automatic rain gauges — O-A0002-001. Free key: https://opendata.cwa.gov.tw"""
from __future__ import annotations

import time

import httpx

URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0002-001"


def _f(v) -> float | None:
    try:
        x = float(v)
        return x if x >= 0 else None  # CWA uses negative sentinels (-99, -998) for missing
    except (TypeError, ValueError):
        return None


def _precip(el: dict, key: str) -> float | None:
    v = (el or {}).get(key)
    return _f(v.get("Precipitation") if isinstance(v, dict) else v)


def parse_stations(data: dict) -> list[dict]:
    stations = ((data or {}).get("records") or {}).get("Station") or []
    out, now = [], time.time()
    for s in stations:
        geo = s.get("GeoInfo") or {}
        coords = geo.get("Coordinates") or []
        if isinstance(coords, dict):
            coords = [coords]
        wgs = next((c for c in coords if c.get("CoordinateName") == "WGS84"), coords[0] if coords else {})
        lon, lat = _f(wgs.get("StationLongitude")), _f(wgs.get("StationLatitude"))
        rain = s.get("RainfallElement") or {}
        if not s.get("StationId") or not lon or not lat:
            continue
        out.append({
            "id": s["StationId"], "name": s.get("StationName", ""), "lon": lon, "lat": lat,
            "now": _precip(rain, "Now"), "past1h": _precip(rain, "Past1hr"), "ts": now,
        })
    return out


async def rain_gauges(http: httpx.AsyncClient, key: str) -> list[dict]:
    r = await http.get(URL, params={"Authorization": key, "format": "JSON"}, timeout=30)
    r.raise_for_status()
    return parse_stations(r.json())
