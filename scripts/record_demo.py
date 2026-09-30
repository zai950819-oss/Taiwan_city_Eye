#!/usr/bin/env python3
"""Record a 1080p demo video of Taiwan City Eye — one command, fully automatic.

    .venv/bin/python -m pip install -r requirements-demo.txt
    .venv/bin/python -m playwright install chromium
    .venv/bin/python scripts/record_demo.py      # → demo/taiwan-city-eye-demo.mp4

What it does
  1. Starts the app on a private port with a *scripted demo scenario* (simulated speeds,
     counts and incidents). If data/godeye.db holds live TDX data, real camera positions,
     real live freeway feeds and real freeway shapes are used for Taipei.
  2. Warms up until alerts, rain and replay history are ready.
  3. Drives a ~70 s tour (bilingual captions, visible cursor) and screen-captures it via
     the Chrome DevTools screencast, then encodes an H.264 MP4 with ffmpeg.

Options: --out PATH  --port N  --headed/--headless  --author "Your name"  --keep-frames
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Fail early with the exact fix if this Python is missing something (common with conda + venv).
_missing = []
for _mod in ("fastapi", "uvicorn", "httpx", "playwright"):
    try:
        __import__(_mod)
    except ImportError:
        _missing.append(_mod)
if _missing:
    sys.exit(f"Missing in {sys.executable}: {', '.join(_missing)}\n"
             f"Fix:  {sys.executable} -m pip install -r requirements-demo.txt\n"
             f"      {sys.executable} -m playwright install chromium")

import httpx  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
W, H = 1920, 1080
REGION = (121.40, 24.95, 121.66, 25.13)          # Taipei basin — where real geometry is used
WARM_BBOX = "121.47,25.00,121.64,25.10"
INCIDENTS = "121.5515,25.0730,crash;121.5440,25.0410,crash;121.5200,25.0620,flood"
RAIN = "121.605,25.058"
HERO_CAMERA_NEAR = (121.5515, 25.0730)            # 國道1號 21K — sits inside the scripted incident

INTRO = """<div class="eye"></div><h1>台灣城市之眼</h1><h2>TAIWAN CITY EYE</h2>
<p>即時交通態勢系統 · Real-time traffic intelligence for Taiwan</p>"""
OUTRO = """<div class="eye"></div><h1>台灣城市之眼</h1><h2>TAIWAN CITY EYE</h2>
<div class="feat"><span>3D 城市</span><span>直播鏡頭</span><span>AI 車流分析</span><span>異常警報</span>
<span>雨量雷達</span><span>24h 回放</span><span>監控牆</span></div>
<p>只看車流，不看人臉 · Flows, not faces</p>{author}
<small>資料來源 Data: 交通部 TDX · 中央氣象署 · RainViewer · OpenFreeMap / © OpenStreetMap<br>
Speeds, counts and incidents in this video are simulated for demonstration.</small>"""


# ----------------------------------------------------------------------------- scenario
def build_real_geometry(out: Path) -> bool:
    """Export real cameras + freeway shapes around Taipei from the live DB, if present."""
    src = ROOT / "data" / "godeye.db"
    if not src.exists():
        return False
    c = sqlite3.connect(src)
    c.row_factory = sqlite3.Row
    w, s, e, n = REGION
    cams = [dict(r) for r in c.execute(
        "SELECT id, source, name, road, direction, lon, lat, stream_url FROM cameras "
        "WHERE id NOT LIKE 'MOCK:%' AND lon BETWEEN ? AND ? AND lat BETWEEN ? AND ?", (w, e, s, n))]
    fw = [x for x in cams if x["source"] == "Freeway"]
    secs = []
    for r in c.execute("SELECT id, name, lines FROM sections WHERE id NOT LIKE 'MOCK:%'"):
        lines = json.loads(r["lines"])
        pts = [p for ln in lines for p in ln]
        if not any(w <= x <= e and s <= y <= n for x, y in pts):
            continue
        name = r["name"]
        if not name and fw:  # TDX section names can be empty → borrow the nearest freeway camera's
            mx, my = lines[0][len(lines[0]) // 2]
            near = min(fw, key=lambda k: (k["lon"] - mx) ** 2 + (k["lat"] - my) ** 2)
            name = f"{near['name'].split('+')[0]} 路段"
        secs.append({"id": r["id"], "name": name, "lines": lines})
    if len(cams) < 20:
        return False
    out.write_text(json.dumps({"bbox": REGION, "cameras": cams, "sections": secs}, ensure_ascii=False))
    print(f"• real geometry: {len(cams)} cameras ({len(fw)} freeway live feeds), {len(secs)} freeway sections")
    return True


def start_server(port: int, extra: Path | None) -> subprocess.Popen:
    db = ROOT / "data" / "demo.db"
    for f in db.parent.glob("demo.db*"):
        f.unlink()
    env = {**os.environ, "GODEYE_MOCK": "1", "GODEYE_DB": "data/demo.db",
           "GODEYE_MOCK_INCIDENTS": INCIDENTS, "GODEYE_MOCK_RAIN": RAIN}
    if extra:
        env["GODEYE_MOCK_EXTRA"] = str(extra)
    return subprocess.Popen([sys.executable, "-m", "uvicorn", "godeye.main:app", "--port", str(port),
                             "--log-level", "warning"], cwd=ROOT, env=env)


async def warm_up(base: str, timeout: float = 240) -> None:
    async with httpx.AsyncClient(timeout=60) as c:
        for _ in range(120):
            try:
                if (await c.get(f"{base}/api/status")).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.5)
        t0 = time.time()
        while time.time() - t0 < timeout:
            q = f"bbox={WARM_BBOX}"
            for path in ("cameras", "sections", "rain"):
                await c.get(f"{base}/api/{path}?{q}")          # keeps the area "watched"
            alerts = (await c.get(f"{base}/api/alerts?{q}")).json()
            kinds = {a["kind"] for a in alerts if not a["resolved"]}
            print(f"\r• warming up {time.time() - t0:5.0f}s  alerts={len(alerts)} kinds={sorted(kinds)}   ",
                  end="", flush=True)
            if len(alerts) >= 4 and {"queue", "flood"} <= kinds:
                print()
                return
            await asyncio.sleep(5)
        print("\n! warm-up timed out — recording anyway")


# ----------------------------------------------------------------------------- capture
class Screencast:
    def __init__(self, page, folder: Path):
        self.page, self.folder, self.frames, self.cdp = page, folder, [], None

    async def start(self):
        self.cdp = await self.page.context.new_cdp_session(self.page)
        self.cdp.on("Page.screencastFrame", lambda p: asyncio.ensure_future(self._frame(p)))
        await self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92,
                                                     "maxWidth": W, "maxHeight": H, "everyNthFrame": 1})

    async def _frame(self, p):
        path = self.folder / f"{len(self.frames):06d}.jpg"
        path.write_bytes(base64.b64decode(p["data"]))
        self.frames.append((p["metadata"].get("timestamp") or time.time(), path))
        try:
            await self.cdp.send("Page.screencastFrameAck", {"sessionId": p["sessionId"]})
        except Exception:  # noqa: BLE001 — session may be closing
            pass

    async def stop(self):
        await asyncio.sleep(0.3)
        await self.cdp.send("Page.stopScreencast")
        self.frames.append((time.time(), self.frames[-1][1]))  # hold the last frame

    def encode(self, out: Path, ffmpeg: str):
        lst = self.folder / "frames.txt"
        rows = []
        for (t, path), (t2, _) in zip(self.frames, self.frames[1:]):
            rows.append(f"file '{path}'\nduration {max(0.001, min(t2 - t, 3.0)):.4f}")
        rows.append(f"file '{self.frames[-1][1]}'")
        lst.write_text("\n".join(rows))
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                        "-vf", f"fps=30,scale={W}:{H}:flags=lanczos,format=yuv420p",
                        "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-movflags", "+faststart",
                        str(out)], check=True)


def find_ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        exe = shutil.which("ffmpeg")
        if not exe:
            sys.exit("ffmpeg not found — pip install imageio-ffmpeg (or brew install ffmpeg)")
        return exe


# ----------------------------------------------------------------------------- tour helpers
async def wait(ms: float):
    await asyncio.sleep(ms / 1000)


async def within(coro, seconds: float) -> bool:
    """Run coro but stop waiting after `seconds` (without blocking on cancellation). True = finished."""
    task = asyncio.ensure_future(coro)
    done, _ = await asyncio.wait({task}, timeout=seconds)
    if not done:
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        return False
    task.result()
    return True


async def caption(page, zh=None, en=None):
    await page.evaluate("([a,b]) => demo.caption(a,b)", [zh, en])


async def card(page, html=None):
    await page.evaluate("h => demo.card(h)", html)


async def idle(page, timeout=7000):
    await page.evaluate("""t => new Promise(res => { const m = app.map;
        if (m.loaded() && m.areTilesLoaded()) return res(); m.once('idle', () => res()); setTimeout(res, t); })""", timeout)


async def fly(page, center, zoom, pitch, bearing, ms):
    await page.evaluate("""([c,z,p,b,d]) => new Promise(res => { app.map.once('moveend', () => res());
        setTimeout(res, d + 3000);
        app.map.flyTo({center:c, zoom:z, pitch:p, bearing:b, duration:d, essential:true}); })""",
                        [center, zoom, pitch, bearing, ms])


async def ease(page, ms, **opts):
    await page.evaluate("""([o,d]) => new Promise(res => { app.map.once('moveend', () => res());
        setTimeout(res, d + 3000);
        app.map.easeTo({...o, duration:d, easing:t=>t}); })""", [opts, ms])


async def point_at(page, x, y, click=True, pause=850):
    await page.evaluate("([x,y]) => demo.cursor(x,y)", [x, y])
    await wait(pause)
    if click:
        await page.evaluate("([x,y]) => demo.ripple(x,y)", [x, y])
        await page.mouse.click(x, y)


async def point_el(page, selector, click=True, pause=850):
    box = await page.locator(selector).first.bounding_box()
    if not box:
        print(f"! not visible: {selector}")
        return
    await point_at(page, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, click, pause)


async def camera_xy(page, cam_id):
    return await page.evaluate("""id => { const c = app.camIndex.get(id); if (!c) return null;
        const p = app.map.project(c.center); return [p.x, p.y]; }""", cam_id)


async def pick_cameras(page, near, n=4, freeway_first=True):
    """Camera ids closest to `near`, preferring real freeway MJPEG feeds."""
    return await page.evaluate("""([near, n, fw]) => [...app.camIndex.values()]
        .map(c => ({c, d: (c.center[0]-near[0])**2 + (c.center[1]-near[1])**2
                         - (fw && c.source === 'Freeway' && !String(c.stream_url).startsWith('mock') ? 1 : 0)}))
        .sort((a, b) => a.d - b.d).slice(0, n).map(x => x.c.id)""", [list(near), n, freeway_first])


# ----------------------------------------------------------------------------- the tour
async def tour(page, author: str):
    m = "app.map"
    # 0 ─ intro over the whole island
    # (the intro card is already up when the screencast starts — see record())
    await ease(page, 4200, zoom=7.25)
    await card(page)
    await wait(700)

    # 1 ─ dive into Taipei, 3D
    await caption(page, "放大即掃描 · 只抓取畫面內的資料", "Zoom in to scan — data loads only for what's on screen")
    await fly(page, [121.5654, 25.0335], 15.3, 62, -28, 6500)
    await idle(page, 6000)
    await caption(page, "3D 城市 · 即時車速與路口鏡頭", "3D city · live road speeds & traffic cameras")
    await ease(page, 5200, bearing=12)

    # 2 ─ a real freeway camera inside the incident
    await caption(page, "點擊任一鏡頭 · 直播畫面 + AI 車流分析", "Click any camera — live feed + AI vehicle analysis")
    await fly(page, [HERO_CAMERA_NEAR[0], HERO_CAMERA_NEAR[1] - 0.004], 15.6, 58, -8, 3800)
    await idle(page, 4000)
    await wait(2500)                                   # fresh camera data for this area
    hero = (await pick_cameras(page, HERO_CAMERA_NEAR, 1))[0]
    xy = await camera_xy(page, hero)
    await point_at(page, *xy)
    await wait(2600)
    await caption(page, "兩張影格比對 · 分辨「塞住」還是「車多但在動」",
                  "Two frames, 2 s apart — queue vs. heavy-but-moving traffic")
    await point_el(page, "#p-counts div:last-child", click=False, pause=900)
    await wait(1700)
    await point_el(page, "#p-cond", click=False, pause=700)
    await wait(1300)

    # 3 ─ camera wall
    await caption(page, "一鍵釘選 · 多鏡頭監控牆", "Pin cameras to a live multi-camera wall")
    await point_el(page, "#p-pin")
    for cid in (await pick_cameras(page, HERO_CAMERA_NEAR, 5))[1:4]:
        await page.evaluate("id => { app.openCamera(id); }", cid)
        await wait(450)
        await point_el(page, "#p-pin", pause=350)
        await wait(250)
    await point_el(page, "#btn-wall")
    await wait(5200)
    await point_el(page, "#wall-close")
    await wait(400)
    await point_el(page, "#close", pause=500)

    # 4 ─ anomaly alerts
    await caption(page, "AI 異常偵測 · 每條路都和「自己的平常」比較", "Anomaly alerts — every road compared to its own normal")
    await fly(page, [121.548, 25.055], 13.9, 50, -10, 2600)
    await idle(page, 3000)
    await point_el(page, "[data-tab=alerts]")
    await wait(1800)
    await point_el(page, "#alert-list li:first-child")
    await wait(3600)
    flood = await page.evaluate("""() => [...document.querySelectorAll('#alert-list li')]
        .findIndex(li => li.textContent.includes('Flooding')) + 1""")
    if flood:
        await caption(page, "影像辨識路況 · 積水、濃煙、起霧", "Vision tags road conditions — flooding, smoke, fog")
        await point_el(page, f"#alert-list li:nth-child({flood})")
        await wait(3600)
    await point_el(page, "#close", pause=500)

    # 5 ─ rain
    await caption(page, "雨量雷達 + 雨量站 · 大雨路段自動標註", "Rain radar + gauges — heavy rain flagged on slowdowns")
    await point_el(page, "[data-tab=overview]", pause=600)
    await fly(page, [121.600, 25.052], 13.6, 45, 5, 2800)
    await wait(3200)

    # 6 ─ replay
    await caption(page, "24 小時時光回放 · 看見尖峰如何形成", "24-hour time-lapse — watch rush hour build up")
    await fly(page, [121.545, 25.052], 12.7, 48, -12, 2600)
    await point_el(page, "#btn-replay")
    await wait(1200)
    await page.select_option("#r-speed", "15")
    await ease(page, 9500, bearing=-40)
    await wait(600)
    await point_el(page, "#r-live", pause=600)
    await caption(page)

    # 7 ─ outro
    who = f"<p style='margin-top:10px;font-size:17px;color:#8fa3b8'>{author}</p>" if author else ""
    await card(page, OUTRO.format(author=who))
    await ease(page, 6000, bearing=-10, zoom=12.4)
    await wait(600)


# ----------------------------------------------------------------------------- main
async def record(args, page_hook=None):
    from playwright.async_api import async_playwright

    extra = ROOT / "data" / "demo_extra.json"
    use_real = build_real_geometry(extra)
    if not use_real:
        print("• no live DB found — using fully synthetic geometry")
    server = start_server(args.port, extra if use_real else None)
    base = f"http://127.0.0.1:{args.port}"
    frames_dir = Path(tempfile.mkdtemp(prefix="godeye-demo-"))
    try:
        await warm_up(base)
        async with async_playwright() as p:
            headless = args.headless or (not args.headed and sys.platform != "darwin")
            print(f"• browser: {'headless' if headless else 'window (GPU)'}")
            launch = {"headless": headless,
                      "args": ["--enable-gpu", "--ignore-gpu-blocklist", "--hide-scrollbars",
                               # macOS pauses rendering + throttles timers for windows that are covered
                               # (e.g. by the Terminal) — that is what froze the rehearsal
                               "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding",
                               "--disable-background-timer-throttling",
                               "--window-position=0,0", f"--window-size={W},{H + 120}"]}
            try:
                browser = await p.chromium.launch(channel="chrome", **launch)
            except Exception:  # noqa: BLE001 — Chrome not installed → bundled Chromium
                browser = await p.chromium.launch(**launch)
            ctx = await browser.new_context(viewport={"width": W, "height": H}, device_scale_factor=1,
                                            locale="zh-TW", timezone_id="Asia/Taipei")
            await ctx.add_init_script("try { localStorage.removeItem('godeye.wall') } catch (e) {}")
            page = await ctx.new_page()
            if page_hook:
                await page_hook(page)
            await page.goto(base + "/")
            await page.wait_for_function("window.app && app.map && app.map.getSource('cameras') && app.map.getLayer('cameras')",
                                         timeout=60_000)
            await page.evaluate((ROOT / "scripts" / "demo_overlay.js").read_text(encoding="utf-8"))
            await page.bring_to_front()
            vis = await page.evaluate("document.visibilityState")
            if vis != "visible":
                print(f"! page visibility is '{vis}' — keep the Chrome window uncovered")
            gpu = await page.evaluate("""() => { const g = document.createElement('canvas').getContext('webgl');
                const e = g && g.getExtension('WEBGL_debug_renderer_info');
                return e ? g.getParameter(e.UNMASKED_RENDERER_WEBGL) : 'unknown'; }""")
            print(f"• WebGL renderer: {gpu}")
            if any(k in gpu.lower() for k in ("swiftshader", "llvmpipe", "software")):
                print("! software rendering — this will be slow and choppy. On a Mac, run without --headless.")
            # rehearsal: load every area once so the take has warm tiles and fresh data
            views = ([[121.5654, 25.0335], 15.3, 62, -28], [[121.5515, 25.069], 15.6, 58, -8],
                     [[121.548, 25.055], 13.9, 50, -10], [[121.600, 25.052], 13.6, 45, 5],
                     [[121.545, 25.052], 12.7, 48, -12], [[120.95, 23.72], 6.9, 0, 0])
            for n, (c, z, pt, b) in enumerate(views, 1):
                t = time.time()
                ok = await within(page.evaluate(
                    "([c,z,p,b]) => { app.map.jumpTo({center:c, zoom:z, pitch:p, bearing:b}); }", [c, z, pt, b]), 15)
                ok = ok and await within(idle(page, 8000), 15)
                await wait(1500)
                print(f"• rehearsal {n}/{len(views)} {'ready' if ok else 'slow — continuing'} "
                      f"({time.time() - t:.1f}s)")
            await card(page, INTRO)
            await wait(1200)
            print("• recording…")
            cast = Screencast(page, frames_dir)
            await cast.start()
            t0 = time.time()
            await tour(page, args.author)
            await cast.stop()
            print(f"• captured {len(cast.frames)} frames in {time.time() - t0:.0f}s — encoding…")
            await browser.close()
        out = Path(args.out).resolve()
        cast.encode(out, find_ffmpeg())
        print(f"✓ saved {out}")
    finally:
        server.terminate()
        if not args.keep_frames:
            shutil.rmtree(frames_dir, ignore_errors=True)
        else:
            print(f"• frames kept in {frames_dir}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "demo" / "taiwan-city-eye-demo.mp4"))
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--headed", action="store_true", help="show the browser window (default on macOS)")
    ap.add_argument("--headless", action="store_true", help="hide the browser (no GPU on macOS → slow)")
    ap.add_argument("--author", default="", help="optional credit line on the end card")
    ap.add_argument("--keep-frames", action="store_true")
    asyncio.run(record(ap.parse_args()))


if __name__ == "__main__":
    main()
