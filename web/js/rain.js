// Rain: RainViewer radar (free, no key, last 2h @10 min) + CWA rain gauges from our API.
import { $, EMPTY, fmtTime, getJSON } from "./util.js";

const META = "https://api.rainviewer.com/public/weather-maps.json";
const OPACITY = 0.55;
let map, frames = [], idx = 0, anim = null, pinnedTime = null;

export async function initRain(m, beforeId) {
  map = m;
  map.addSource("rain", { type: "geojson", data: EMPTY });
  const mm = ["coalesce", ["get", "past1h"], 0];
  map.addLayer({ id: "rain-gauges", type: "circle", source: "rain", minzoom: 9, filter: [">", mm, 0.4],
    paint: {
      "circle-color": ["interpolate", ["linear"], mm, 0.5, "#9ad0ff", 5, "#3d8bff", 20, "#2449d8", 50, "#b04dff"],
      "circle-radius": ["interpolate", ["linear"], mm, 0.5, 5, 50, 14],
      "circle-opacity": 0.85, "circle-stroke-width": 1, "circle-stroke-color": "#05080c" } }, beforeId);
  map.addLayer({ id: "rain-labels", type: "symbol", source: "rain", minzoom: 11.5, filter: [">", mm, 0.4],
    layout: { "text-field": ["concat", ["to-string", mm], " mm"], "text-size": 11, "text-offset": [0, 1.4],
              "text-font": styleFont() },
    paint: { "text-color": "#9ad0ff", "text-halo-color": "#05080c", "text-halo-width": 1.2 } }, beforeId);

  $("l-gauges").onchange = (e) => ["rain-gauges", "rain-labels"].forEach((l) =>
    map.setLayoutProperty(l, "visibility", e.target.checked ? "visible" : "none"));
  $("l-radar").onchange = () => show();
  await loadRadar(beforeId);
  setInterval(() => loadRadar(beforeId), 10 * 60_000);
}

// reuse a font the basemap already ships, so labels never 404 on glyphs
function styleFont() {
  const l = map.getStyle().layers.find((x) => x.layout?.["text-font"]);
  return l ? l.layout["text-font"] : ["Open Sans Regular"];
}

async function loadRadar(beforeId) {
  let meta;
  try { meta = await getJSON(META); } catch (e) { console.warn("radar unavailable", e); return; }
  frames.forEach((_, i) => { map.removeLayer(`radar-${i}`); map.removeSource(`radar-${i}`); });
  frames = (meta.radar?.past || []).map((f) => ({ time: f.time, path: f.path }));
  frames.forEach((f, i) => {
    map.addSource(`radar-${i}`, { type: "raster", tileSize: 256, maxzoom: 7,
      tiles: [`${meta.host}${f.path}/256/{z}/{x}/{y}/2/1_1.png`],
      attribution: '<a href="https://www.rainviewer.com/">RainViewer</a>' });
    map.addLayer({ id: `radar-${i}`, type: "raster", source: `radar-${i}`,
      paint: { "raster-opacity": 0, "raster-fade-duration": 0 } }, beforeId);
  });
  idx = frames.length - 1;
  show();
}

function show() {
  clearInterval(anim);
  const on = $("l-radar").checked && frames.length;
  frames.forEach((_, i) => map.setPaintProperty(`radar-${i}`, "raster-opacity", 0));
  if (!on) { $("radar-time").textContent = ""; return; }
  if (pinnedTime != null) { showFrame(nearest(pinnedTime)); return; }
  // loop the last 2 hours, pausing on "now"
  let hold = 0;
  showFrame(frames.length - 1);
  anim = setInterval(() => {
    if (idx === frames.length - 1 && hold++ < 4) return;
    hold = 0;
    showFrame((idx + 1) % frames.length);
  }, 600);
}

function showFrame(i) {
  if (i == null) {
    frames.forEach((_, k) => map.setPaintProperty(`radar-${k}`, "raster-opacity", 0));
    $("radar-time").textContent = "(no radar for this time)";
    return;
  }
  map.setPaintProperty(`radar-${idx}`, "raster-opacity", 0);
  idx = i;
  map.setPaintProperty(`radar-${idx}`, "raster-opacity", OPACITY);
  $("radar-time").textContent = fmtTime(frames[idx].time);
}

function nearest(t) {
  let best = null, d = Infinity;
  frames.forEach((f, i) => { const dd = Math.abs(f.time - t); if (dd < d) { d = dd; best = i; } });
  return d <= 900 ? best : null;
}

// Replay sync: pin radar to a timestamp (null = back to the live loop)
export function radarAt(t) { pinnedTime = t; show(); }

export async function refreshRain(bboxQ, signal) {
  map.getSource("rain").setData(await getJSON(`/api/rain?${bboxQ}`, signal));
}
