"""TDX (運輸資料流通服務) client: OAuth token cache + road-traffic endpoints.

Responses are normalized into plain dicts so the rest of the app never
depends on TDX field spellings. Parsing is defensive because field names
differ slightly between Freeway / Highway / City datasets.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from .geo import parse_wkt_lines

log = logging.getLogger("godeye.tdx")

TOKEN_URL = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
API_BASE = "https://tdx.transportdata.tw/api/basic/"


class TDXClient:
    def __init__(self, client_id: str, client_secret: str, http: httpx.AsyncClient):
        self._id, self._secret, self._http = client_id, client_secret, http
        self._token: str | None = None
        self._token_exp = 0.0
        self._lock = asyncio.Lock()

    async def _auth_header(self) -> dict[str, str]:
        async with self._lock:
            if not self._token or time.time() > self._token_exp - 60:
                r = await self._http.post(
                    TOKEN_URL,
                    data={"grant_type": "client_credentials",
                          "client_id": self._id, "client_secret": self._secret},
                    headers={"content-type": "application/x-www-form-urlencoded"},
                )
                r.raise_for_status()
                body = r.json()
                self._token = body["access_token"]
                self._token_exp = time.time() + int(body.get("expires_in", 3600))
                log.info("TDX token refreshed")
        return {"authorization": f"Bearer {self._token}", "accept-encoding": "gzip"}

    async def get(self, path: str, **params: Any) -> Any:
        params.setdefault("$format", "JSON")
        for attempt in range(4):
            r = await self._http.get(API_BASE + path, params=params, headers=await self._auth_header())
            if r.status_code == 429:  # rate limited → back off
                await asyncio.sleep(2 ** attempt * 2)
                continue
            if r.status_code == 401:  # token expired early
                self._token = None
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"TDX request failed after retries: {path}")

    # ---------- endpoints ----------

    async def cctvs(self, source: str) -> list[dict]:
        """source: 'Freeway' | 'Highway' | 'City/Taipei' ..."""
        data = await self.get(f"v2/Road/Traffic/CCTV/{source}")
        return [c for c in (normalize_cctv(x, source) for x in _items(data, "CCTVs")) if c]

    async def live_freeway(self) -> list[dict]:
        data = await self.get("v2/Road/Traffic/Live/Freeway")
        out = []
        for x in _items(data, "LiveTraffics"):
            sid = x.get("SectionID")
            if not sid:
                continue
            out.append({
                "section_id": f"FW:{sid}",
                "speed": _num(x.get("TravelSpeed")),
                "level": _congestion(x.get("CongestionLevel")),
            })
        return out

    async def freeway_section_shapes(self) -> list[dict]:
        data = await self.get("v2/Road/Traffic/SectionShape/Freeway")
        out = []
        for x in _items(data, "SectionShapes"):
            lines = parse_wkt_lines(x.get("Geometry", ""))
            if x.get("SectionID") and lines:
                out.append({"id": f"FW:{x['SectionID']}", "lines": lines,
                            "name": x.get("SectionName", "")})
        return out

    async def freeway_sections(self) -> dict[str, dict]:
        """Section metadata (road name, start/end) keyed by our section id."""
        data = await self.get("v2/Road/Traffic/Section/Freeway")
        out = {}
        for x in _items(data, "Sections"):
            sid = x.get("SectionID")
            if not sid:
                continue
            start = (x.get("SectionStart") or {}).get("LocationMile", "")
            end = (x.get("SectionEnd") or {}).get("LocationMile", "")
            out[f"FW:{sid}"] = {
                "road": x.get("RoadName", ""),
                "direction": x.get("RoadDirection", ""),
                "name": f"{x.get('RoadName', '')} {start}–{end}".strip(),
            }
        return out


# ---------- normalization helpers ----------

def _items(data: Any, key: str) -> list[dict]:
    if isinstance(data, dict):
        return data.get(key) or []
    return data if isinstance(data, list) else []


def _num(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f >= 0 else None
    except (TypeError, ValueError):
        return None


def _congestion(v: Any) -> int:
    """TDX CongestionLevel: 1 順暢, 2 車多, 3 壅塞 (others/-99 unknown) → 0 unknown."""
    try:
        i = int(str(v).split(",")[0])
        return i if i in (1, 2, 3) else 0
    except ValueError:
        return 0


def normalize_cctv(x: dict, source: str) -> dict | None:
    lon, lat = _num(x.get("PositionLon")), _num(x.get("PositionLat"))
    cid = x.get("CCTVID")
    stream = x.get("VideoStreamURL") or x.get("VideoImageURL") or ""
    if not cid or not lon or not lat or not stream:
        return None
    road = x.get("RoadName") or ""
    mile = x.get("LocationMile") or ""
    name = x.get("SurveillanceDescription") or f"{road} {mile}".strip() or cid
    return {
        "id": f"{source.replace('/', ':')}:{cid}",
        "source": source,
        "name": name,
        "road": road,
        "direction": x.get("RoadDirection") or "",
        "lon": lon,
        "lat": lat,
        "stream_url": stream,
    }
