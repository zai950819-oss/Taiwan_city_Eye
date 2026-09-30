// Time-lapse replay: scrub the last 24h of speeds, camera counts and alerts for the area in view.
import { $, COLORS, fmtTime, getJSON } from "./util.js";
import { alertsAt, unfreeze } from "./alerts.js";
import { radarAt } from "./rain.js";

let map, base, currentBbox, data = null, i = 0, timer = null, fps = 6, bboxKey = "";
export const replay = { active: false };

export function initReplay(m, { getBase, getBboxQ, onExit }) {
  map = m; base = getBase; currentBbox = getBboxQ;
  $("btn-replay").onclick = () => (replay.active ? exit() : enter());
  $("r-live").onclick = exit;
  $("r-play").onclick = () => (timer ? pause() : play());
  $("r-slider").oninput = (e) => { pause(); render(Number(e.target.value)); };
  $("r-speed").onchange = (e) => { fps = Number(e.target.value); if (timer) { pause(); play(); } };
  document.addEventListener("keydown", (e) => {
    if (!replay.active || e.target.tagName === "INPUT" && e.target.type !== "range") return;
    if (e.code === "Space") { e.preventDefault(); timer ? pause() : play(); }
    if (e.key === "ArrowRight") { pause(); render(Math.min(i + 1, maxI())); }
    if (e.key === "ArrowLeft") { pause(); render(Math.max(i - 1, 0)); }
  });
  replay.onExit = onExit;
}

const maxI = () => (data ? data.times.length - 1 : 0);

async function enter() {
  replay.active = true;
  document.body.classList.add("replaying");
  $("replay").classList.remove("hidden");
  $("btn-replay").classList.add("on");
  await reloadReplay(true);
}

function exit() {
  pause();
  replay.active = false;
  data = null;
  document.body.classList.remove("replaying");
  $("replay").classList.add("hidden");
  $("btn-replay").classList.remove("on");
  unfreeze();
  radarAt(null);
  replay.onExit?.();
}

// Called on enter and whenever the viewport changes during replay.
export async function reloadReplay(startAtBeginning = false, bboxQ = null) {
  if (!replay.active) return;
  const q = bboxQ || currentBbox();
  if (!q) return;
  if (q === bboxKey && data && !startAtBeginning) { render(i); return; }
  const keepT = data?.times[i];
  $("r-time").textContent = "loading…";
  data = await getJSON(`/api/replay?${q}&hours=24&step=600`);
  bboxKey = q;
  $("r-slider").max = maxI();
  drawStrip();
  const j = keepT != null ? Math.max(0, data.times.findIndex((t) => t >= keepT)) : 0;
  render(startAtBeginning ? 0 : j);
  if (startAtBeginning) play();
}

function play() {
  if (!data?.times.length) return;
  if (i >= maxI()) i = 0;
  $("r-play").textContent = "❚❚";
  timer = setInterval(() => (i >= maxI() ? pause() : render(i + 1)), 1000 / fps);
}

function pause() { clearInterval(timer); timer = null; $("r-play").textContent = "▶"; }

function render(j) {
  if (!data || !data.times.length) { $("r-time").textContent = "no history yet"; return; }
  i = j;
  const t = data.times[i];
  $("r-slider").value = i;
  $("r-time").textContent = fmtTime(t, true);
  const { cams, secs } = base();
  map.getSource("sections").setData({ ...secs, features: secs.features.map((f) => {
    const v = at(data.sections[f.properties.id], t);
    return { ...f, properties: { ...f.properties, speed: v ? v[0] : null, level: v ? v[1] : 0 } };
  }) });
  map.getSource("cameras").setData({ ...cams, features: cams.features.map((f) => {
    const v = at(data.cameras[f.properties.id], t);
    return { ...f, properties: { ...f.properties, total: v ? v[0] : null, level: v ? v[1] : 0 } };
  }) });
  alertsAt(data.alerts, t, data.step);
  radarAt(t);
}

// Value at t, carrying the last sample forward up to 30 min (cameras are sampled less often than buckets)
function at(series, t) {
  if (!series) return null;
  for (let k = 0; k <= 3; k++) { const v = series[t - k * data.step]; if (v) return v; }
  return null;
}

// Heat strip above the slider: share of jammed sections/cameras per time bucket.
function drawStrip() {
  const c = $("r-strip"), ctx = c.getContext("2d");
  ctx.clearRect(0, 0, c.width, c.height);
  const n = data.times.length;
  if (!n) return;
  const w = c.width / n;
  data.times.forEach((t, k) => {
    let jam = 0, busy = 0, all = 0;
    for (const m of [data.sections, data.cameras]) for (const id in m) {
      const v = at(m[id], t); if (!v) continue;
      all++; if (v[1] === 3) jam++; else if (v[1] === 2) busy++;
    }
    const f = all ? (jam + 0.4 * busy) / all : 0;
    ctx.fillStyle = f > 0.35 ? COLORS[3] : f > 0.15 ? COLORS[2] : all ? COLORS[1] : COLORS[0];
    ctx.globalAlpha = all ? 0.35 + Math.min(0.65, f * 1.5) : 0.3;
    ctx.fillRect(k * w, 0, Math.ceil(w), c.height);
  });
  ctx.globalAlpha = 1;
  const alertTimes = data.alerts.map((a) => a.started);
  ctx.fillStyle = "#ffffff";
  for (const at of alertTimes) {
    const k = data.times.findIndex((t) => t >= at - data.step);
    if (k >= 0) ctx.fillRect(k * w + w / 2 - 1, 0, 2, 4);
  }
}
