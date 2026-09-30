"""Taiwan City Eye — FastAPI app.  Run:  uvicorn godeye.main:app --reload

Every map endpoint takes ?bbox=west,south,east,north. Requesting an area is what
tells the engine to start fetching / analysing it — no bbox, no work.
"""
from __future__ import annotations

import json
import logging

import httpx
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import ROOT, settings
from .db import DB
from .frames import grab_jpeg
from .ingest import BBox, Engine
from .spatial import sections_in
from . import player

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
db = DB(settings.db_path)
engine = Engine(db)

MAX_BBOX_DEG2 = 2.0  # refuse island-wide requests; the UI only asks once zoomed in
FRESH_S = 900


@asynccontextmanager
async def lifespan(_: FastAPI):
    engine.start()
    yield
    await engine.stop()


app = FastAPI(title="Taiwan City Eye", lifespan=lifespan)


def parse_bbox(bbox: str) -> BBox:
    try:
        w, s, e, n = (float(x) for x in bbox.split(","))
    except ValueError:
        raise HTTPException(400, "bbox must be west,south,east,north")
    if (e - w) * (n - s) > MAX_BBOX_DEG2:
        raise HTTPException(413, "area too large — zoom in")
    return w, s, e, n


def _feature(geom: dict, props: dict) -> dict:
    return {"type": "Feature", "geometry": geom, "properties": props}


def _sections_in(bbox: BBox) -> list[dict]:
    return sections_in(db, bbox)


def _fresh(c) -> bool:
    return bool(c["v_ts"]) and time.time() - c["v_ts"] < FRESH_S


# ---------------- endpoints ----------------

@app.get("/api/cameras")
async def cameras(bbox: str):
    b = parse_bbox(bbox)
    engine.viewed(b)
    await engine.ensure_registry()
    feats = []
    for c in db.cameras_in_bbox(b):
        fresh = _fresh(c)
        feats.append(_feature({"type": "Point", "coordinates": [c["lon"], c["lat"]]}, {
            "id": c["id"], "name": c["name"], "road": c["road"], "source": c["source"],
            "total": c["total"] if fresh else None, "level": c["level"] if fresh else 0,
            "cars": c["cars"], "motorcycles": c["motorcycles"], "buses": c["buses"],
            "trucks": c["trucks"], "sampled_at": c["v_ts"], "stream_url": c["stream_url"],
            "moving": c["moving"] if fresh else None,
            "condition": c["condition"] if fresh else None, "cond_conf": c["cond_conf"] if fresh else None,
        }))
    return {"type": "FeatureCollection", "features": feats}


@app.get("/api/sections")
async def sections(bbox: str):
    b = parse_bbox(bbox)
    engine.viewed(b)
    await engine.ensure_shapes()
    feats = []
    for s in _sections_in(b):
        lines = s["lines"]
        geom = ({"type": "LineString", "coordinates": lines[0]} if len(lines) == 1
                else {"type": "MultiLineString", "coordinates": lines})
        feats.append(_feature(geom, {"id": s["id"], "name": s["name"], "speed": s["speed"],
                                     "level": s["level"] or 0}))
    return {"type": "FeatureCollection", "features": feats}


@app.get("/api/status")
def status(bbox: str | None = None):
    out = {"mode": engine.mode, "vision": engine.vision_on, "conditions": engine.conditions_on,
           "rain": engine.rain_on, "meta": db.meta(),
           "errors": engine.errors, "watching": len(engine.demand.active()),
           "server_time": time.time()}
    if bbox:
        b = parse_bbox(bbox)
        cams = db.cameras_in_bbox(b)
        secs = _sections_in(b)
        speeds = [s["speed"] for s in secs if s["speed"] is not None]
        out.update({
            "cameras": len(cams),
            "cameras_sampled": sum(1 for c in cams if _fresh(c)),
            "sections": len(secs),
            "avg_speed": round(sum(speeds) / len(speeds), 1) if speeds else None,
            "jammed_sections": sum(1 for s in secs if s["level"] == 3),
            "active_alerts": len(db.alerts_in_bbox(b, time.time(), active_only=True)),
        })
    return out


@app.get("/api/hotspots")
def hotspots(bbox: str, limit: int = 8):
    b = parse_bbox(bbox)
    secs = sorted((s for s in _sections_in(b) if s["speed"] is not None), key=lambda s: s["speed"])
    cams = sorted((c for c in db.cameras_in_bbox(b) if _fresh(c) and c["total"] is not None),
                  key=lambda c: -c["total"])
    return {
        "slowest_sections": [{"id": s["id"], "name": s["name"], "speed": s["speed"], "level": s["level"],
                              "center": s["lines"][0][len(s["lines"][0]) // 2]} for s in secs[:limit]],
        "busiest_cameras": [{"id": c["id"], "name": c["name"], "total": c["total"], "level": c["level"],
                             "center": [c["lon"], c["lat"]]} for c in cams[:limit]],
    }


@app.get("/api/alerts")
def alerts(bbox: str, hours: float = Query(2, le=72)):
    """Active alerts plus ones resolved within `hours`, most severe first."""
    b = parse_bbox(bbox)
    return [dict(r) for r in db.alerts_in_bbox(b, time.time() - hours * 3600)]


@app.get("/api/rain")
def rain(bbox: str):
    b = parse_bbox(bbox)
    return {"type": "FeatureCollection", "features": [
        _feature({"type": "Point", "coordinates": [r["lon"], r["lat"]]},
                 {"id": r["station_id"], "name": r["name"], "now": r["now"], "past1h": r["past1h"], "ts": r["ts"]})
        for r in db.rain_in_bbox(b) if r["past1h"] is not None]}


@app.get("/api/replay")
def replay(bbox: str, hours: float = Query(24, le=72), step: int = Query(600, ge=60, le=3600)):
    """Time-bucketed history for everything in view: {times, sections{id:{t:[speed,level]}},
    cameras{id:{t:[total,level]}}, alerts[...]} — the client scrubs through it locally."""
    b = parse_bbox(bbox)
    since = time.time() - hours * 3600
    sec_ids = [s["id"] for s in _sections_in(b)]
    cam_ids = [c["id"] for c in db.cameras_in_bbox(b)]
    sections: dict[str, dict] = {}
    times: set[int] = set()
    for r in db.replay_sections(sec_ids, since, step):
        sections.setdefault(r["id"], {})[r["t"]] = [r["speed"], r["level"]]
        times.add(r["t"])
    cameras: dict[str, dict] = {}
    for r in db.replay_cameras(cam_ids, since, step):
        cameras.setdefault(r["id"], {})[r["t"]] = [r["total"], r["level"]]
        times.add(r["t"])
    hist = [{k: a[k] for k in ("id", "kind", "entity_type", "entity_id", "name", "lon", "lat", "severity",
                               "message", "started", "resolved")}
            for a in db.alerts_in_bbox(b, since)]
    return {"step": step, "times": sorted(times), "sections": sections, "cameras": cameras, "alerts": hist}


@app.get("/api/cameras/{cam_id:path}/history")
def camera_history(cam_id: str, hours: float = Query(24, le=72)):
    return [dict(r) for r in db.camera_history(cam_id, hours)]


@app.get("/api/network/history")
def network_history(hours: float = Query(24, le=72)):
    return [dict(r) for r in db.network_history(hours)]


RELAY_MAX_BYTES = 200_000_000  # ~10+ min of MJPEG, then the viewer must reopen


@app.get("/api/cameras/{cam_id:path}/player")
async def camera_player(cam_id: str):
    """Tell the UI how to play this camera: mjpeg <img>, iframe, hls <video>, or mock."""
    cam = db.camera(cam_id)
    if not cam:
        raise HTTPException(404, "unknown camera")
    url = cam["stream_url"]
    kind = await player.detect(engine.http, url)
    enc = cam_id.replace("/", "%2F")
    if kind == player.MOCK:
        src = f"/api/cameras/{enc}/snapshot"
    elif kind == player.MJPEG and url.startswith("http://"):
        src = f"/api/cameras/{enc}/stream"  # relay: avoid mixed-content blocking
    else:
        src = url
    return {"kind": kind, "src": src, "original": None if kind == player.MOCK else url,
            "name": cam["name"], "source": cam["source"]}


@app.get("/api/cameras/{cam_id:path}/stream")
async def camera_stream(cam_id: str):
    """Live MJPEG relay. Upstream is closed as soon as the viewer closes the panel."""
    cam = db.camera(cam_id)
    if not cam or player.cached_kind(cam["stream_url"]) != player.MJPEG:
        raise HTTPException(404, "no MJPEG stream for this camera")
    req = engine.http.build_request("GET", cam["stream_url"], timeout=httpx.Timeout(10, read=30),
                                    headers={"user-agent": "TaiwanCityEye/0.1"})
    upstream = await engine.http.send(req, stream=True)
    if upstream.status_code != 200:
        await upstream.aclose()
        raise HTTPException(502, "camera offline")

    async def body():
        sent = 0
        try:
            async for chunk in upstream.aiter_raw():
                sent += len(chunk)
                yield chunk
                if sent > RELAY_MAX_BYTES:
                    break
        finally:
            await upstream.aclose()

    ctype = upstream.headers.get("content-type", "multipart/x-mixed-replace")
    return StreamingResponse(body(), media_type=ctype, headers={"cache-control": "no-store"})


@app.get("/api/cameras/{cam_id:path}/snapshot")
async def snapshot(cam_id: str):
    """Relay one live frame (streams are often HTTP-only / no CORS). Never cached or stored."""
    cam = db.camera(cam_id)
    if not cam:
        raise HTTPException(404, "unknown camera")
    headers = {"cache-control": "no-store"}
    url = cam["stream_url"]
    if url.startswith("mock://"):
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="480" height="270">'
               f'<rect width="100%" height="100%" fill="#0b1118"/>'
               f'<text x="50%" y="45%" fill="#4fd1c5" font-family="monospace" font-size="18" '
               f'text-anchor="middle">MOCK FEED</text><text x="50%" y="58%" fill="#6b7a8c" '
               f'font-family="monospace" font-size="13" text-anchor="middle">{cam["name"]} · '
               f'{time.strftime("%H:%M:%S")}</text></svg>')
        return Response(svg, media_type="image/svg+xml", headers=headers)
    if ".m3u8" in url.lower():
        raise HTTPException(415, "HLS stream — open the original link")
    jpeg = await grab_jpeg(engine.http, url)
    if not jpeg:
        raise HTTPException(502, "camera offline or unsupported stream format")
    return Response(jpeg, media_type="image/jpeg", headers=headers)


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
