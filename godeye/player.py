"""Figure out how the browser should play each camera.

TDX gives one URL per camera, but the format differs by agency:
  • 國道 (cctvn.freeway.gov.tw/.../bmjpg)      → MJPEG stream        → <img>
  • 台北市 (hls.bote.gov.taipei/live/index.html) → HTML player page    → <iframe>
  • 新北市 (atis.ntpc.gov.tw/.../ShowFrame4CCTV) → HTML player page    → <iframe>
  • *.m3u8                                     → HLS                 → <video> + hls.js
  • anything else (e.g. 省道 thb.gov.tw)         → probe Content-Type once, cache
"""
from __future__ import annotations

import re

import httpx

MJPEG, IFRAME, HLS, MOCK = "mjpeg", "iframe", "hls", "mock"

_cache: dict[str, str] = {}

_RULES = [
    (re.compile(r"^mock://"), MOCK),
    (re.compile(r"\.m3u8", re.I), HLS),
    (re.compile(r"mjpe?g|snapshot|\.jpe?g($|\?)", re.I), MJPEG),
    (re.compile(r"\.html?($|\?)|ShowFrame|/live/", re.I), IFRAME),
]


def classify_url(url: str) -> str | None:
    for rx, kind in _RULES:
        if rx.search(url):
            return kind
    return None


async def detect(http: httpx.AsyncClient, url: str) -> str:
    if url in _cache:
        return _cache[url]
    kind = classify_url(url)
    if kind is None:
        kind = IFRAME  # safest default: let the agency's own page render it
        try:
            async with http.stream("GET", url, timeout=6) as r:
                ct = r.headers.get("content-type", "").lower()
                if "multipart/x-mixed-replace" in ct or ct.startswith("image/"):
                    kind = MJPEG
                elif "mpegurl" in ct:
                    kind = HLS
        except httpx.HTTPError:
            pass
    _cache[url] = kind
    return kind


def cached_kind(url: str) -> str | None:
    """Non-blocking lookup used by the vision loop."""
    return _cache.get(url) or classify_url(url)
