// Multi-camera wall: pin up to 9 cameras and watch them side by side.
import { $, el, esc, store } from "./util.js";
import { Player } from "./player.js";

const KEY = "godeye.wall";
const MAX = 9;
let pinned = store.get(KEY, []);
let cols = store.get(KEY + ".cols", 2);
let players = [];
let onSelect = () => {};

export function initWall({ select }) {
  onSelect = select;
  $("btn-wall").onclick = () => ($("wall").classList.contains("hidden") ? open() : close());
  $("wall-close").onclick = close;
  document.querySelectorAll("[data-cols]").forEach((b) => (b.onclick = () => {
    cols = Number(b.dataset.cols); store.set(KEY + ".cols", cols); render();
  }));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });
  updateCount();
}

export const isPinned = (id) => pinned.some((p) => p.id === id);

export function togglePin(cam) {
  if (isPinned(cam.id)) pinned = pinned.filter((p) => p.id !== cam.id);
  else {
    if (pinned.length >= MAX) pinned.shift();
    pinned.push({ id: cam.id, name: cam.name, center: cam.center });
  }
  store.set(KEY, pinned);
  updateCount();
  if (!$("wall").classList.contains("hidden")) render();
  return isPinned(cam.id);
}

function updateCount() {
  $("wall-count").textContent = pinned.length;
  $("btn-wall").classList.toggle("on", pinned.length > 0);
}

function open() { $("wall").classList.remove("hidden"); render(); }

function close() {
  players.forEach((p) => p.stop());
  players = [];
  $("wall").classList.add("hidden");
}

function render() {
  players.forEach((p) => p.stop());
  players = [];
  const grid = $("wall-grid");
  grid.innerHTML = "";
  const c = pinned.length <= 1 ? 1 : pinned.length <= 4 ? Math.min(cols, 2) : cols;
  grid.className = `wall-grid cols-${Math.max(2, c)}`;
  $("wall-empty").classList.toggle("hidden", pinned.length > 0);
  document.querySelectorAll("[data-cols]").forEach((b) => b.classList.toggle("on", Number(b.dataset.cols) === cols));
  for (const cam of pinned) {
    const tile = el("div", "tile");
    const head = el("div", "tile-head", `<b title="Show on map">${esc(cam.name)}</b>`);
    const x = el("button", null, "✕");
    x.title = "Unpin";
    x.onclick = () => togglePin(cam);
    head.appendChild(x);
    head.querySelector("b").onclick = () => { close(); onSelect(cam); };
    const box = el("div", "player");
    tile.append(head, box);
    grid.appendChild(tile);
    const p = new Player(box);
    players.push(p);
    p.play(cam.id);
  }
}
