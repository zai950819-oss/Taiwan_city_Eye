"""Pull JPEG frames out of an MJPEG stream or snapshot URL — in memory only."""
from __future__ import annotations

import asyncio
import time

import httpx

SOI, EOI = b"\xff\xd8", b"\xff\xd9"
MAX_BYTES = 6_000_000
UA = {"user-agent": "TaiwanCityEye/0.1"}


async def grab_frames(http: httpx.AsyncClient, url: str, n: int = 1, gap: float = 2.0,
                      timeout: float = 10.0) -> list[bytes]:
    """Return up to `n` frames taken at least `gap` seconds apart.

    MJPEG: read consecutive parts from one connection.
    Single-JPEG endpoints: the stream ends after one frame → re-request after `gap`.
    """
    frames: list[bytes] = []
    last = 0.0
    started = time.monotonic()
    buf = bytearray()
    try:
        async with http.stream("GET", url, timeout=timeout, headers=UA) as r:
            if r.status_code != 200:
                return frames
            async for chunk in r.aiter_bytes():
                buf.extend(chunk)
                while True:
                    s = buf.find(SOI)
                    if s == -1:
                        buf.clear()
                        break
                    e = buf.find(EOI, s + 2)
                    if e == -1:
                        del buf[:s]
                        break
                    frame = bytes(buf[s:e + 2])
                    del buf[:e + 2]
                    now = time.monotonic()
                    if not frames or now - last >= gap:
                        frames.append(frame)
                        last = now
                        if len(frames) >= n:
                            return frames
                if len(buf) > MAX_BYTES or time.monotonic() - started > timeout + gap * n:
                    return frames
    except (httpx.HTTPError, OSError):
        return frames
    if frames and len(frames) < n:  # snapshot-style endpoint
        await asyncio.sleep(gap)
        frames += (await grab_frames(http, url, 1, gap, timeout))[:1]
    return frames


async def grab_jpeg(http: httpx.AsyncClient, url: str, timeout: float = 8.0) -> bytes | None:
    got = await grab_frames(http, url, 1, timeout=timeout)
    return got[0] if got else None
