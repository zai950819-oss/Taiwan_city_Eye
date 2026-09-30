#!/usr/bin/env python3
"""Lay a music track under the demo video, with the track's first drop landing on a chosen moment.

    python scripts/add_music.py demo/track.mp3                       # auto: drop → 3D-city reveal (10 s)
    python scripts/add_music.py demo/track.mp3 --hit 10 --offset 6   # manual: start the track at 6 s

Output: demo/taiwan-city-eye-demo-bgm.mp4 (video stream copied, not re-encoded).
Loudness is normalised to −14 LUFS (YouTube / IG target), with fade-in and a fade-out over the end card.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SR, HOP = 11025, 0.1  # analysis sample rate, 100 ms frames


def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return shutil.which("ffmpeg") or sys.exit("ffmpeg not found — pip install imageio-ffmpeg")


def duration(ff: str, path: Path) -> float:
    out = subprocess.run([ff, "-i", str(path)], capture_output=True, text=True).stderr
    h, m, s = out.split("Duration: ")[1].split(",")[0].split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def energy_db(ff: str, path: Path) -> np.ndarray:
    raw = subprocess.run([ff, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    n = int(SR * HOP)
    frames = x[: len(x) // n * n].reshape(-1, n)
    return 20 * np.log10(np.sqrt((frames ** 2).mean(axis=1)) + 1e-9)


def find_drops(db: np.ndarray, min_jump=4.0) -> list[float]:
    """Times where 2 s of music is ≥ min_jump dB louder than the 4 s before it."""
    k_after, k_before = int(2 / HOP), int(4 / HOP)
    drops, last = [], -99.0
    for i in range(k_before, len(db) - k_after):
        jump = db[i:i + k_after].mean() - db[i - k_before:i].mean()
        t = i * HOP
        if jump >= min_jump and t - last > 8:
            # refine to the sharpest single-frame rise within ±0.5 s
            w = slice(max(1, i - 5), i + 5)
            j = w.start + int(np.argmax(np.diff(db[w.start - 1:w.stop])))
            drops.append(round(j * HOP, 2))
            last = t
    return drops


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("music")
    ap.add_argument("--video", default=str(ROOT / "demo" / "taiwan-city-eye-demo.mp4"))
    ap.add_argument("--out", default=str(ROOT / "demo" / "taiwan-city-eye-demo-bgm.mp4"))
    ap.add_argument("--hit", type=float, default=10.0, help="video second where the first drop should land")
    ap.add_argument("--offset", type=float, help="start the track at this second (skips auto-detection)")
    ap.add_argument("--fade-out", type=float, default=5.0)
    args = ap.parse_args()

    ff = ffmpeg_exe()
    music, video = Path(args.music), Path(args.video)
    vdur, mdur = duration(ff, video), duration(ff, music)
    if args.offset is not None:
        offset = args.offset
    else:
        drops = find_drops(energy_db(ff, music))
        print(f"• drops detected at: {drops}")
        usable = [d for d in drops if 0 <= d - args.hit <= max(0.0, mdur - vdur)]
        offset = round(usable[0] - args.hit, 2) if usable else 0.0
        if not usable:
            print("! no drop fits — starting the track from 0 s")
    if mdur - offset < vdur:
        print(f"! track is {vdur - (mdur - offset):.1f}s shorter than the video — it will end early")
    print(f"• video {vdur:.1f}s · track {mdur:.1f}s · track starts at {offset:.2f}s "
          f"(first drop lands at {args.hit:.1f}s)")

    fo_start = max(0.0, vdur - args.fade_out)
    af = (f"atrim=start={offset},asetpts=PTS-STARTPTS,"
          f"afade=t=in:st=0:d=1.5,afade=t=out:st={fo_start:.2f}:d={args.fade_out},"
          f"loudnorm=I=-14:TP=-1.0:LRA=11")
    subprocess.run([ff, "-y", "-v", "error", "-i", str(video), "-i", str(music),
                    "-filter_complex", f"[1:a]{af}[a]", "-map", "0:v", "-map", "[a]",
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                    "-t", f"{vdur:.3f}", "-movflags", "+faststart", args.out], check=True)
    print(f"✓ saved {args.out}")


if __name__ == "__main__":
    main()
