export const $ = (id) => document.getElementById(id);
export const COLORS = { 0: "#3a4656", 1: "#38d27a", 2: "#f5b83d", 3: "#ff4d5e" };
export const SEV_COLORS = { 1: "#6fb7ff", 2: "#f5b83d", 3: "#ff4d5e" };
export const levelColor = ["match", ["get", "level"], 1, COLORS[1], 2, COLORS[2], 3, COLORS[3], COLORS[0]];
export const EMPTY = { type: "FeatureCollection", features: [] };
export const enc = (id) => encodeURIComponent(id);

export async function getJSON(url, signal) {
  const r = await fetch(url, { signal });
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}

export const fmtTime = (t, withDate = false) => new Date(t * 1000).toLocaleString("zh-TW", {
  hour12: false, hour: "2-digit", minute: "2-digit", ...(withDate ? { month: "2-digit", day: "2-digit" } : {}),
});

export function ago(t) {
  const m = Math.round((Date.now() / 1000 - t) / 60);
  return m < 1 ? "just now" : m < 60 ? `${m} min ago` : `${Math.round(m / 60)} h ago`;
}

// localStorage can be unavailable (private mode) — never let that break the app
export const store = {
  get(k, fallback) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : fallback; } catch { return fallback; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* ignore */ } },
};

export function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html != null) e.innerHTML = html;
  return e;
}

export function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

export function drawSeries(canvas, values, color) {
  const ctx = canvas.getContext("2d"), w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  const v = values.filter((x) => x != null);
  ctx.font = "10px monospace";
  if (v.length < 2) { ctx.fillStyle = "#6b7a8c"; ctx.fillText("collecting data…", 6, h / 2); return; }
  const min = Math.min(...v), max = Math.max(...v), span = max - min || 1;
  ctx.beginPath();
  v.forEach((y, i) => {
    const px = (i / (v.length - 1)) * (w - 4) + 2, py = h - 4 - ((y - min) / span) * (h - 12);
    i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
  });
  ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke();
  ctx.fillStyle = "#6b7a8c";
  ctx.fillText(Math.round(max), 2, 10); ctx.fillText(Math.round(min), 2, h - 2);
}
