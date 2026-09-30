// Taiwan City Eye — map client (ES modules, no build step).
// Data is fetched only for the area on screen, and only once zoomed in past MIN_ZOOM.
import { $, COLORS, EMPTY, drawSeries, enc, getJSON, levelColor } from "./util.js";
import { Player } from "./player.js";
import { initWall, isPinned, togglePin } from "./wall.js";
import { clearAlerts, initAlerts, refreshAlerts, showTab } from "./alerts.js";
import { initRain, refreshRain } from "./rain.js";
import { initReplay, reloadReplay, replay } from "./replay.js";

const MIN_ZOOM = 11;
const REFRESH_MS = 30_000;
const BUILDING_TILES = "https://tiles.openfreemap.org/planet"; // free OpenMapTiles w/ render_height
const CONDITION = {
  dry: "Dry road", wet: "Wet road / rain", flood: "Possible flooding", fog: "Fog — low visibility",
  smoke: "Smoke / fire", night: "Night",
};

const map = new maplibregl.Map({
  container: "map",
  style: "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json",
  center: [121.5654, 25.0360], zoom: 15, pitch: 60, bearing: -20, maxPitch: 80,
  attributionControl: { compact: true },
});
map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "bottom-right");
$("min-zoom").textContent = MIN_ZOOM;

let base = { cams: EMPTY, secs: EMPTY };
let camIndex = new Map();
const panelPlayer = new Player($("p-player"), { onInfo: (info) => {
  $("p-link").style.display = info.original ? "inline" : "none";
  if (info.original) $("p-link").href = info.original;
} });
window.app = { map, openCamera: (id) => openCamera(camIndex.get(id) || { id }), get camIndex() { return camIndex; } };

// ---------------- layers ----------------
map.on("load", async () => {
  addBuildings();
  addCameraIcons();
  map.addSource("sections", { type: "geojson", data: EMPTY });
  map.addSource("cameras", { type: "geojson", data: EMPTY });

  map.addLayer({ id: "sections-glow", type: "line", source: "sections",
    paint: { "line-color": levelColor, "line-width": ["interpolate", ["linear"], ["zoom"], 10, 6, 16, 18],
             "line-opacity": 0.2, "line-blur": 4 } });
  map.addLayer({ id: "sections", type: "line", source: "sections", layout: { "line-cap": "round" },
    paint: { "line-color": levelColor, "line-width": ["interpolate", ["linear"], ["zoom"], 10, 2, 16, 6] } });

  initAlerts(map, { select: focus });

  map.addLayer({ id: "cameras-halo", type: "circle", source: "cameras",
    paint: { "circle-color": levelColor, "circle-blur": 1, "circle-opacity": 0.45,
             "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 12, 16, 24] } });
  map.addLayer({ id: "camera-selected", type: "circle", source: "cameras", filter: ["==", ["get", "id"], ""],
    paint: { "circle-radius": ["interpolate", ["linear"], ["zoom"], 11, 16, 16, 26], "circle-color": "rgba(0,0,0,0)",
             "circle-stroke-width": 2.5, "circle-stroke-color": "#ffffff" } });
  map.addLayer({ id: "cameras", type: "symbol", source: "cameras",
    layout: {
      "icon-image": ["concat", "cam-", ["to-string", ["get", "level"]]],
      "icon-size": ["interpolate", ["linear"], ["zoom"], 11, 0.75, 16, 1.2],
      "icon-allow-overlap": true, "icon-ignore-placement": true,
      "symbol-sort-key": ["-", 0, ["coalesce", ["get", "level"], 0]],
    } });

  map.on("click", "cameras", (e) => openCamera(camIndex.get(e.features[0].properties.id) || e.features[0].properties));
  map.on("click", "sections", (e) => {
    if (map.queryRenderedFeatures(e.point, { layers: ["cameras"] }).length) return;  // camera wins
    const p = e.features[0].properties;
    const spd = p.speed == null || p.speed === "null" ? "no data" : `${p.speed} km/h`;
    new maplibregl.Popup().setLngLat(e.lngLat).setHTML(`<b>${p.name || p.id}</b><br>${spd}`).addTo(map);
  });
  const tip = new maplibregl.Popup({ closeButton: false, closeOnClick: false, offset: 18, className: "cam-tip" });
  map.on("mousemove", "cameras", (e) => {
    const p = e.features[0].properties;
    tip.setLngLat(e.features[0].geometry.coordinates)
      .setHTML(`<b>${p.name}</b><br><span style="color:#4fd1c5">▶ Click to watch live</span>`).addTo(map);
  });
  map.on("mouseleave", "cameras", () => tip.remove());
  for (const l of ["cameras", "sections", "alerts-pulse"]) {
    map.on("mouseenter", l, () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", l, () => (map.getCanvas().style.cursor = ""));
  }

  wireControls();
  initWall({ select: focus });
  initReplay(map, { getBase: () => base, getBboxQ: bboxQ, onExit: () => { applyLive(); loadView(); } });
  initRain(map, "sections-glow");   // radar + gauges sit under the roads
  map.on("moveend", scheduleLoad);
  loadView();
  setInterval(loadView, REFRESH_MS);
});

function addBuildings() {
  flatBuildingLayers().forEach((id) => map.setLayoutProperty(id, "visibility", "none"));
  map.addSource("ofm", { type: "vector", url: BUILDING_TILES });
  const h = ["coalesce", ["get", "render_height"], ["get", "height"], 8];
  map.addLayer({
    id: "buildings-3d", type: "fill-extrusion", source: "ofm", "source-layer": "building", minzoom: 13,
    filter: ["!=", ["get", "hide_3d"], true],
    paint: {
      "fill-extrusion-color": ["interpolate", ["linear"], h, 0, "#151e2a", 40, "#1c2a3b", 120, "#24405c", 300, "#2f6f8f"],
      "fill-extrusion-height": ["interpolate", ["linear"], ["zoom"], 13, 0, 14.5, h],
      "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
      "fill-extrusion-opacity": 0.9,
      "fill-extrusion-vertical-gradient": true,
    },
  });
  try { map.setSky({ "sky-color": "#05080c", "horizon-color": "#0f2233", "fog-color": "#05080c",
                     "sky-horizon-blend": 0.6, "horizon-fog-blend": 0.7 }); } catch (_) {}
}

function flatBuildingLayers() {
  return map.getStyle().layers.filter((l) => l["source-layer"] === "building" && l.type !== "fill-extrusion"
    && l.id !== "buildings-3d").map((l) => l.id);
}

function addCameraIcons() {
  for (const [lvl, color] of Object.entries(COLORS)) {
    const S = 64, c = document.createElement("canvas");
    c.width = c.height = S;
    const x = c.getContext("2d");
    x.beginPath(); x.arc(32, 32, 28, 0, Math.PI * 2);
    x.fillStyle = "#070b10"; x.fill();
    x.lineWidth = 5; x.strokeStyle = lvl === "0" ? "#8fa3b8" : color; x.stroke();
    x.fillStyle = lvl === "0" ? "#c9d6e3" : color;
    x.beginPath(); x.roundRect(14, 23, 25, 18, 4); x.fill();
    x.beginPath(); x.moveTo(40, 29); x.lineTo(51, 23); x.lineTo(51, 41); x.lineTo(40, 35); x.closePath(); x.fill();
    x.fillStyle = "#070b10"; x.beginPath(); x.arc(22, 32, 3, 0, Math.PI * 2); x.fill();
    map.addImage(`cam-${lvl}`, x.getImageData(0, 0, S, S), { pixelRatio: 2 });
  }
}

function wireControls() {
  const vis = (ids, on) => ids.forEach((l) => map.setLayoutProperty(l, "visibility", on ? "visible" : "none"));
  $("l-buildings").onchange = (e) => {
    vis(["buildings-3d"], e.target.checked);
    vis(flatBuildingLayers(), !e.target.checked);
    map.easeTo({ pitch: e.target.checked ? 60 : 0 });
  };
  $("l-sections").onchange = (e) => vis(["sections", "sections-glow"], e.target.checked);
  $("l-freeway-cams").onchange = $("l-city-cams").onchange = applyCameraFilter;
  document.querySelectorAll("[data-fly]").forEach((b) => (b.onclick = () => {
    const [lng, lat, z] = b.dataset.fly.split(",").map(Number);
    map.flyTo({ center: [lng, lat], zoom: z, pitch: z >= 13 ? 60 : 0, speed: 1.4 });
  }));
}

function applyCameraFilter() {
  const fw = $("l-freeway-cams").checked, city = $("l-city-cams").checked;
  const isCity = ["==", ["slice", ["get", "source"], 0, 5], "City/"];
  const f = fw && city ? null : fw ? ["!", isCity] : city ? isCity : ["==", 1, 0];
  ["cameras", "cameras-halo"].forEach((l) => map.setFilter(l, f));
}

// ---------------- viewport-driven loading ----------------
let loadTimer = null, inflight = null;
function scheduleLoad() { clearTimeout(loadTimer); loadTimer = setTimeout(loadView, 350); }

function bboxQ() {
  const b = map.getBounds();
  return "bbox=" + [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()].map((v) => v.toFixed(4)).join(",");
}

function applyLive() {
  map.getSource("cameras")?.setData(base.cams);
  map.getSource("sections")?.setData(base.secs);
}

async function loadView() {
  if (!map.getSource("cameras")) return;
  const tooFar = map.getZoom() < MIN_ZOOM;
  $("zoom-gate").classList.toggle("hidden", !tooFar);
  if (tooFar) { clearView(); return; }

  inflight?.abort();
  inflight = new AbortController();
  const sig = inflight.signal, q = bboxQ();
  try {
    const [cams, secs] = await Promise.all([getJSON(`/api/cameras?${q}`, sig), getJSON(`/api/sections?${q}`, sig)]);
    base = { cams, secs };
    camIndex = new Map(cams.features.map((f) => [f.properties.id, { ...f.properties, center: f.geometry.coordinates }]));
    if (replay.active) await reloadReplay(false, q); else applyLive();
    const [status, hot, net] = await Promise.all([getJSON(`/api/status?${q}`, sig), getJSON(`/api/hotspots?${q}`, sig),
                                                  getJSON("/api/network/history?hours=24", sig)]);
    renderStatus(status);
    renderList("h-sections", hot.slowest_sections, (s) => `${s.speed} km/h`);
    renderList("h-cams", hot.busiest_cameras, (c) => `${c.total} veh`);
    drawSeries($("net-chart"), net.map((r) => r.avg_speed), "#4fd1c5");
    await Promise.all([refreshAlerts(q, sig), status.rain ? refreshRain(q, sig) : null]);
    if (status.vision && status.cameras_sampled < status.cameras) setTimeout(scheduleLoad, 8000);
  } catch (err) {
    if (err.name !== "AbortError") console.error("load failed", err);
  }
}

function clearView() {
  inflight?.abort();
  base = { cams: EMPTY, secs: EMPTY };
  applyLive();
  map.getSource("rain")?.setData(EMPTY);
  clearAlerts();
  ["s-cams", "s-sampled", "s-speed", "s-jams"].forEach((id) => ($(id).textContent = "–"));
  $("h-sections").innerHTML = $("h-cams").innerHTML = "";
}

function renderStatus(s) {
  $("mode").textContent = s.mode;
  $("mode").className = `badge ${s.mode}`;
  $("s-cams").textContent = s.cameras.toLocaleString();
  $("s-sampled").textContent = s.vision ? s.cameras_sampled : "off";
  $("s-speed").textContent = s.avg_speed ?? "–";
  $("s-jams").textContent = s.jammed_sections;
  if (Object.keys(s.errors || {}).length) console.warn("server errors", s.errors);
}

function renderList(id, rows, fmt) {
  const ol = $(id);
  ol.innerHTML = "";
  for (const r of rows) {
    const li = document.createElement("li");
    li.innerHTML = `<span style="color:${COLORS[r.level] || COLORS[0]}">${fmt(r)}</span>`;
    li.prepend(document.createTextNode(r.name || r.id));
    li.onclick = () => focus({ ...r, entity_type: camIndex.has(r.id) ? "camera" : "section", entity_id: r.id });
    ol.appendChild(li);
  }
}

// Fly to an alert / list row / wall tile, and open its camera if it has one.
function focus(item) {
  const center = item.center || [item.lon, item.lat];
  if (center[0] != null) map.flyTo({ center, zoom: Math.max(map.getZoom(), 15.5), pitch: 60 });
  const camId = item.entity_type ? (item.entity_type === "camera" ? item.entity_id : null) : item.id;
  if (camId) openCamera(camIndex.get(camId) || { id: camId, name: item.name, source: "", center });
}

// ---------------- camera panel ----------------
let current = null;
async function openCamera(p) {
  current = p;
  map.setFilter("camera-selected", ["==", ["get", "id"], p.id]);
  $("panel").classList.remove("hidden");
  $("p-name").textContent = p.name || p.id;
  $("p-meta").textContent = [p.road, p.source].filter(Boolean).join(" · ");
  const cond = p.condition && p.condition !== "null" ? p.condition : null;
  $("p-cond").innerHTML = cond
    ? `<span class="cond c-${cond}">${CONDITION[cond] || cond} · ${Math.round((p.cond_conf || 0) * 100)}%</span>` : "";
  const n = (v) => (v === "null" || v == null ? "–" : v);
  const moving = p.moving == null || p.moving === "null" ? "–" : `${Math.round(p.moving * 100)}%`;
  $("p-counts").innerHTML = [["cars", "car"], ["motorcycles", "moto"], ["buses", "bus"], ["trucks", "truck"]]
    .map(([k, label]) => `<div><b>${n(p[k])}</b><small>${label}</small></div>`).join("")
    + `<div><b>${moving}</b><small>moving</small></div>`;
  updatePin();
  panelPlayer.play(p.id);
  const hist = await getJSON(`/api/cameras/${enc(p.id)}/history?hours=24`);
  if (current?.id === p.id) drawSeries($("p-chart"), hist.map((r) => r.total), "#f5b83d");
}

function updatePin() {
  const on = current && isPinned(current.id);
  $("p-pin").textContent = on ? "✓ Pinned" : "📌 Pin to wall";
  $("p-pin").classList.toggle("on", !!on);
}

$("p-pin").onclick = () => {
  if (!current) return;
  togglePin({ id: current.id, name: current.name || current.id, center: current.center });
  updatePin();
};
$("p-reload").onclick = () => current && panelPlayer.play(current.id);
$("close").onclick = () => {
  $("panel").classList.add("hidden");
  panelPlayer.stop(); current = null;
  map.setFilter("camera-selected", ["==", ["get", "id"], ""]);
};

showTab("overview");
setInterval(() => ($("clock").textContent = new Date().toLocaleString("zh-TW", { hour12: false })), 1000);
