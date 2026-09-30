// Anomaly alerts: feed in the left tab, pulsing rings on the map, toasts for new ones.
import { $, EMPTY, SEV_COLORS, ago, el, esc, getJSON } from "./util.js";

const KIND_LABEL = {
  slowdown: "Slowdown", surge: "Traffic surge", queue: "Queue", stopped: "Stopped vehicle",
  flood: "Flooding", smoke: "Smoke / fire", fog: "Fog", wet: "Wet road",
};
let map, onSelect;
let seen = null;           // alert ids already shown (null = first load → no toasts)
let latest = [];
let frozen = false;        // replay mode drives the layer instead of live data

export function initAlerts(m, { select }) {
  map = m; onSelect = select;
  map.addSource("alerts", { type: "geojson", data: EMPTY });
  const sev = ["match", ["get", "severity"], 3, SEV_COLORS[3], 2, SEV_COLORS[2], SEV_COLORS[1]];
  map.addLayer({ id: "alerts-pulse", type: "circle", source: "alerts",
    paint: { "circle-color": sev, "circle-opacity": 0.25, "circle-radius": 20, "circle-blur": 0.4,
             "circle-stroke-color": sev, "circle-stroke-width": 2, "circle-stroke-opacity": 0.8 } });
  map.on("click", "alerts-pulse", (e) => {
    const p = e.features[0].properties;
    const a = latest.find((x) => String(x.id) === String(p.id));
    if (a) onSelect(a);
  });
  $("l-alerts").onchange = (e) => map.setLayoutProperty("alerts-pulse", "visibility", e.target.checked ? "visible" : "none");
  document.querySelectorAll(".tab").forEach((t) => (t.onclick = () => showTab(t.dataset.tab)));
  animate();
}

export function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $("tab-overview").classList.toggle("hidden", name !== "overview");
  $("tab-alerts").classList.toggle("hidden", name !== "alerts");
}

export async function refreshAlerts(bboxQ, signal) {
  const list = await getJSON(`/api/alerts?${bboxQ}&hours=2`, signal);
  latest = list;
  const active = list.filter((a) => !a.resolved);
  $("alert-badge").textContent = active.length;
  $("alert-badge").classList.toggle("hidden", !active.length);
  $("s-alerts").textContent = active.length;
  renderList(list);
  if (!frozen) setLayer(active);
  if (seen) active.filter((a) => !seen.has(a.id)).slice(0, 3).forEach(toast);
  seen = new Set([...(seen || []), ...active.map((a) => a.id)]);
}

export function clearAlerts() {
  latest = [];
  $("alert-list").innerHTML = "";
  $("alert-badge").classList.add("hidden");
  $("s-alerts").textContent = "–";
  setLayer([]);
}

// Replay: show the alerts that were active at time t
export function alertsAt(list, t, step) {
  frozen = true;
  setLayer(list.filter((a) => a.started <= t + step && (!a.resolved || a.resolved >= t)));
}
export function unfreeze() { frozen = false; setLayer(latest.filter((a) => !a.resolved)); }

function setLayer(list) {
  map.getSource("alerts")?.setData({ type: "FeatureCollection", features: list.map((a) => ({
    type: "Feature", geometry: { type: "Point", coordinates: [a.lon, a.lat] },
    properties: { id: a.id, severity: a.severity },
  })) });
}

function renderList(list) {
  const ul = $("alert-list");
  ul.innerHTML = "";
  if (!list.length) { ul.appendChild(el("li", "resolved", "<div class='a-msg'>All normal in this area.</div>")); return; }
  for (const a of list) {
    const li = el("li", `sev${a.severity}${a.resolved ? " resolved" : ""}`,
      `<div class="a-head"><span class="a-kind">${KIND_LABEL[a.kind] || a.kind}</span>
       <span class="muted">${a.resolved ? "resolved " + ago(a.resolved) : ago(a.started)}</span></div>
       <div><b>${esc(a.name)}</b></div><div class="a-msg">${esc(a.message)}</div>`);
    li.onclick = () => onSelect(a);
    ul.appendChild(li);
  }
}

function toast(a) {
  const t = el("div", "toast", `<b>⚠ ${KIND_LABEL[a.kind] || a.kind}</b>${esc(a.name)} — ${esc(a.message)}`);
  t.onclick = () => { onSelect(a); t.remove(); };
  $("toasts").appendChild(t);
  setTimeout(() => t.remove(), 7000);
}

function animate() {
  let last = 0;
  const frame = (now) => {
    if (now - last > 50 && map.getLayer("alerts-pulse")) {
      last = now;
      const k = (now % 1600) / 1600;                    // 0 → 1 every 1.6 s
      map.setPaintProperty("alerts-pulse", "circle-radius", 10 + 26 * k);
      map.setPaintProperty("alerts-pulse", "circle-opacity", 0.35 * (1 - k));
      map.setPaintProperty("alerts-pulse", "circle-stroke-opacity", 0.9 * (1 - k));
    }
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}
