// One live-camera player. Used by the side panel and by every tile on the wall.
import { el, enc } from "./util.js";

export class Player {
  constructor(box, { onInfo } = {}) {
    this.box = box;
    this.onInfo = onInfo;
    this.badge = el("span", "live-badge hidden", "● LIVE");
    box.appendChild(this.badge);
    this.timer = null;
    this.hls = null;
    this.current = null;
  }

  stop() {
    clearInterval(this.timer);
    if (this.hls) { this.hls.destroy(); this.hls = null; }
    this.box.querySelectorAll("img, video, iframe, .msg").forEach((n) => {
      if (n.tagName === "IMG") n.src = "";              // closes the MJPEG connection
      if (n.tagName === "IFRAME") n.src = "about:blank";
      n.remove();
    });
    this.badge.classList.add("hidden");
    this.current = null;
  }

  msg(html) {
    this.box.querySelector(".msg")?.remove();
    if (html) this.box.appendChild(el("div", "msg", html));
  }

  live() { this.msg(null); this.badge.classList.remove("hidden"); }

  async play(id) {
    this.stop();
    this.current = id;
    this.msg("Connecting…");
    let info;
    try { info = await fetch(`/api/cameras/${enc(id)}/player`).then((r) => r.json()); }
    catch { this.msg("Camera unavailable"); return; }
    if (this.current !== id) return;
    this.onInfo?.(info);

    if (info.kind === "mjpeg" || info.kind === "mock") {
      const img = el("img");
      img.alt = "live camera";
      this.box.appendChild(img);
      if (info.kind === "mock") {
        const load = () => (img.src = `${info.src}?t=${Date.now()}`);
        img.onload = () => this.msg(null);
        load(); this.timer = setInterval(load, 10_000);
        return;
      }
      img.src = info.src;
      let tries = 0;  // MJPEG never fires a normal "load": watch for the first decoded frame
      this.timer = setInterval(() => {
        if (img.naturalWidth > 0) { this.live(); clearInterval(this.timer); }
        else if (++tries > 20) { clearInterval(this.timer); this.fallback(id, img); }
      }, 400);
      img.onerror = () => { clearInterval(this.timer); this.fallback(id, img); };
    } else if (info.kind === "iframe") {
      const f = el("iframe");
      f.src = info.src;
      f.allow = "autoplay; fullscreen";
      f.referrerPolicy = "no-referrer";
      f.onload = () => this.live();
      this.box.appendChild(f);
    } else if (info.kind === "hls") {
      const v = el("video");
      Object.assign(v, { muted: true, autoplay: true, playsInline: true, controls: true });
      this.box.appendChild(v);
      if (v.canPlayType("application/vnd.apple.mpegurl")) v.src = info.src;
      else if (window.Hls?.isSupported()) { this.hls = new Hls(); this.hls.loadSource(info.src); this.hls.attachMedia(v); }
      else { this.msg("HLS not supported — use “Open in new tab”"); return; }
      v.onplaying = () => this.live();
      v.onerror = () => this.msg("Stream blocked — use “Open in new tab”");
    }
  }

  // Direct MJPEG failed → poll single frames through our relay instead.
  fallback(id, img) {
    if (this.current !== id) return;
    const url = `/api/cameras/${enc(id)}/snapshot`;
    img.onerror = () => { clearInterval(this.timer); img.remove(); this.msg("Camera offline<br><small>try “Open in new tab”</small>"); };
    img.onload = () => { this.msg(null); this.badge.classList.add("hidden"); };
    const load = () => (img.src = `${url}?t=${Date.now()}`);
    load(); clearInterval(this.timer); this.timer = setInterval(load, 5_000);
  }
}
