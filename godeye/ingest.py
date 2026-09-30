"""Demand-driven ingestion.

Nothing heavy runs until a map client looks at an area:
  • camera registry  – metadata only, fetched lazily on first view, cached 6h
  • freeway speeds   – polled every 60s *only while someone is viewing*
  • vision sampling  – only cameras inside the areas currently on screen
  • anomaly alerts   – re-evaluated every 60s for the areas on screen
  • rain gauges      – CWA, every 10 min while watched (if CWA_API_KEY is set)
"""
from __future__ import annotations

import asyncio
import logging
import time

import httpx

from . import mock
from .config import settings
from .db import DB
from . import cwa, player
from .analytics import sync_alerts
from .conditions import ConditionTagger
from .frames import grab_frames
from .tdx import TDXClient
from .vision import VehicleCounter, level_from

log = logging.getLogger("godeye.ingest")

REGISTRY_TTL_S = 6 * 3600
SHAPES_TTL_S = 24 * 3600
LIVE_POLL_S = 60
ALERT_EVAL_S = 60
RAIN_POLL_S = 600
MOTION_GAP_S = 2.0
TICK_S = 5
PRUNE_S = 3600

BBox = tuple[float, float, float, float]  # west, south, east, north


class Demand:
    """Areas that map clients are currently looking at (expire after `ttl`)."""

    def __init__(self, ttl: float = 90, max_boxes: int = 8):
        self.ttl, self.max_boxes = ttl, max_boxes
        self._boxes: dict[BBox, float] = {}

    def touch(self, bbox: BBox) -> None:
        self._boxes[tuple(round(v, 3) for v in bbox)] = time.time()
        if len(self._boxes) > self.max_boxes:  # drop the oldest
            del self._boxes[min(self._boxes, key=self._boxes.get)]

    def active(self) -> list[BBox]:
        cutoff = time.time() - self.ttl
        self._boxes = {b: t for b, t in self._boxes.items() if t > cutoff}
        return list(self._boxes)


class Engine:
    def __init__(self, db: DB):
        self.db = db
        self.http = httpx.AsyncClient(timeout=30, follow_redirects=True)
        self.tdx = None if settings.mock else TDXClient(
            settings.tdx_client_id, settings.tdx_client_secret, self.http)
        self.counter = VehicleCounter() if (settings.vision_enabled and not settings.mock) else None
        self.tagger = (ConditionTagger() if (self.counter and self.counter.available
                                            and settings.conditions_enabled) else None)
        self._cond_at: dict[str, float] = {}  # camera → last condition tag time
        self._alerts_at = 0.0
        self._rain_at = 0.0
        self.demand = Demand()
        self.errors: dict[str, str] = {}
        self._tasks: list[asyncio.Task] = []
        self._registry_at = 0.0
        self._shapes_at = 0.0
        self._live_at = 0.0
        self._registry_lock = asyncio.Lock()
        self._shapes_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        if not settings.mock:
            db.delete_mock()  # don't mix synthetic rows into a live map

    @property
    def mode(self) -> str:
        return "mock" if settings.mock else "live"

    @property
    def vision_on(self) -> bool:
        return settings.mock or bool(self.counter and self.counter.available)

    @property
    def conditions_on(self) -> bool:
        return settings.mock or bool(self.tagger and self.tagger.available)

    @property
    def rain_on(self) -> bool:
        return settings.mock or bool(settings.cwa_api_key)

    def start(self) -> None:
        for fn in (self._live_loop, self._vision_loop, self._alert_loop, self._rain_loop, self._prune_loop):
            self._tasks.append(asyncio.create_task(self._guard(fn), name=fn.__name__))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.http.aclose()

    def viewed(self, bbox: BBox) -> None:
        """Called by the API whenever a client requests an area."""
        first = not self.demand.active()
        self.demand.touch(bbox)
        if first:
            self._wake.set()  # start polling immediately instead of waiting a tick

    async def _guard(self, fn):
        while True:
            try:
                await fn()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.errors[fn.__name__] = f"{type(e).__name__}: {e}"
                log.exception("%s crashed; restarting in 30s", fn.__name__)
                await asyncio.sleep(30)

    async def _tick(self) -> None:
        try:
            await asyncio.wait_for(self._wake.wait(), TICK_S)
        except asyncio.TimeoutError:
            pass
        self._wake.clear()

    # ---------- lazy loaders (called from API on first view) ----------

    async def ensure_registry(self) -> None:
        if time.time() - self._registry_at < REGISTRY_TTL_S:
            return
        async with self._registry_lock:
            if time.time() - self._registry_at < REGISTRY_TTL_S:
                return
            if settings.mock:
                cams = mock.all_cameras()
            else:
                cams = []
                for src in settings.cctv_sources:
                    try:
                        got = await self.tdx.cctvs(src)
                        cams += got
                        self.errors.pop(f"cctv:{src}", None)
                        log.info("CCTV %s: %d cameras", src, len(got))
                    except (httpx.HTTPError, RuntimeError) as e:
                        self.errors[f"cctv:{src}"] = str(e)
                        log.warning("CCTV %s failed: %s", src, e)
            if cams:
                self.db.upsert_cameras(cams)
                self.db.set_meta("cameras_updated", time.time())
                self._registry_at = time.time()
            if settings.mock and not self.db.has_live_history():
                await self.ensure_shapes()
                # synthetic history in the background so the first view isn't blocked
                self._tasks.append(asyncio.create_task(asyncio.to_thread(self._mock_backfill)))

    async def ensure_shapes(self) -> None:
        if time.time() - self._shapes_at < SHAPES_TTL_S:
            return
        async with self._shapes_lock:
            if time.time() - self._shapes_at < SHAPES_TTL_S:
                return
            if settings.mock:
                self.db.upsert_sections(mock.all_sections())
            else:
                shapes = await self.tdx.freeway_section_shapes()
                try:
                    meta = await self.tdx.freeway_sections()
                except (httpx.HTTPError, RuntimeError):
                    meta = {}
                self.db.upsert_sections(shapes, meta)
                log.info("Freeway sections: %d shapes", len(shapes))
            self.db.set_meta("sections_updated", time.time())
            self._shapes_at = time.time()

    # ---------- loops ----------

    async def _live_loop(self):
        while True:
            await self._tick()
            if not self.demand.active() or time.time() - self._live_at < LIVE_POLL_S:
                continue
            await self.ensure_shapes()
            rows = mock.live() if settings.mock else await self.tdx.live_freeway()
            self._live_at = ts = time.time()
            self.db.add_live(rows, ts)
            self.db.set_meta("live_updated", ts)

    async def _vision_loop(self):
        if not self.vision_on:
            return
        sem = asyncio.Semaphore(settings.vision_concurrency)

        async def sample(cam):
            async with sem:
                ts = time.time()
                if settings.mock:
                    counts = mock.vision_counts(dict(cam), ts)
                else:
                    frames = await grab_frames(self.http, cam["stream_url"], n=2, gap=MOTION_GAP_S)
                    if not frames:
                        return
                    counts = await asyncio.to_thread(self.counter.analyze, frames)
                    if counts and self.tagger and self.tagger.available and \
                            ts - self._cond_at.get(cam["id"], 0) >= settings.condition_interval_s:
                        tag = await asyncio.to_thread(self.tagger.tag, frames[0])
                        if tag:
                            counts["condition"], counts["cond_conf"] = tag
                            self._cond_at[cam["id"]] = ts
                    del frames  # frames discarded
                    if not counts:
                        return
                self.db.add_vision(cam["id"], ts, counts, level_from(counts))

        while True:
            await self._tick()
            boxes = self.demand.active()
            if not boxes:
                continue
            now = time.time()
            due: dict[str, dict] = {}
            for b in boxes:
                for c in self.db.cameras_in_bbox(b):
                    if not settings.mock and player.cached_kind(c["stream_url"]) not in (player.MJPEG, None):
                        continue  # web-page / HLS players have no frame we can grab cheaply
                    if not c["v_ts"] or now - c["v_ts"] >= settings.vision_interval_s:
                        due[c["id"]] = c
            batch = list(due.values())[: settings.vision_max_cameras]
            if batch:
                await asyncio.gather(*(sample(c) for c in batch))
                self.db.set_meta("vision_updated", time.time())

    async def _alert_loop(self):
        while True:
            await self._tick()
            boxes = self.demand.active()
            if not boxes or time.time() - self._alerts_at < ALERT_EVAL_S:
                continue
            await asyncio.sleep(2)  # let the live/vision writes of this tick land first
            for b in boxes:
                stats = await asyncio.to_thread(sync_alerts, self.db, b)
                if stats["opened"]:
                    log.info("alerts in %s: %s", b, stats)
            self._alerts_at = time.time()

    async def _rain_loop(self):
        while True:
            await self._tick()
            if not self.rain_on or not self.demand.active() or time.time() - self._rain_at < RAIN_POLL_S:
                continue
            self._rain_at = time.time()
            try:
                st = mock.rain_gauges() if settings.mock else await cwa.rain_gauges(self.http, settings.cwa_api_key)
                self.db.replace_rain(st)
                self.db.set_meta("rain_updated", time.time())
                self.errors.pop("rain", None)
            except (httpx.HTTPError, ValueError) as e:
                self.errors["rain"] = str(e)
                log.warning("CWA rain failed: %s", e)

    async def _prune_loop(self):
        while True:
            self.db.prune(settings.retention_hours)
            await asyncio.sleep(PRUNE_S)

    def _mock_backfill(self, hours: int = 24):
        """Mock mode only: synthesize the last 24h so history charts aren't empty."""
        now = time.time()
        cams = mock.all_cameras()
        for step in range(hours * 6, 0, -1):  # every 10 min
            ts = now - step * 600
            self.db.add_live(mock.live(ts), ts)
            if step % 2 == 0:
                for c in cams:
                    counts = mock.vision_counts(c, ts)
                    self.db.add_vision(c["id"], ts, counts, level_from(counts))
        log.info("mock: backfilled %dh of history", hours)
