// Injected into the page while recording: captions, title cards, fake cursor.
(() => {
  const css = `
  #demo-cap { position: fixed; left: 50%; bottom: 104px; transform: translate(-50%, 16px); z-index: 50;
    opacity: 0; transition: opacity .45s ease, transform .45s ease; pointer-events: none; text-align: center;
    background: rgba(5,9,14,.82); border: 1px solid rgba(79,209,197,.45); border-radius: 12px;
    padding: 12px 26px 11px; box-shadow: 0 0 40px rgba(79,209,197,.18); backdrop-filter: blur(8px); max-width: 1100px; }
  #demo-cap.on { opacity: 1; transform: translate(-50%, 0); }
  #demo-cap .zh { font: 700 30px/1.3 "PingFang TC","Noto Sans TC","Microsoft JhengHei",sans-serif; color: #eaf6ff; letter-spacing: .04em; }
  #demo-cap .en { font: 500 17px/1.4 ui-monospace,Menlo,monospace; color: #4fd1c5; margin-top: 4px; letter-spacing: .03em; }
  #demo-card { position: fixed; inset: 0; z-index: 60; display: grid; place-items: center; pointer-events: none;
    background: radial-gradient(ellipse at center, rgba(5,8,12,.55) 0%, rgba(5,8,12,.92) 70%);
    opacity: 0; transition: opacity .8s ease; }
  #demo-card.on { opacity: 1; }
  #demo-card .inner { text-align: center; transform: translateY(10px); transition: transform 1.2s ease; }
  #demo-card.on .inner { transform: none; }
  #demo-card .eye { width: 74px; height: 74px; margin: 0 auto 26px; border-radius: 50%; border: 3px solid #4fd1c5;
    box-shadow: 0 0 40px #4fd1c5, inset 0 0 22px #4fd1c5; animation: pulse 3s infinite; }
  #demo-card h1 { margin: 0; font: 800 76px/1.1 "PingFang TC","Noto Sans TC",sans-serif; color: #fff; letter-spacing: .12em; }
  #demo-card h2 { margin: 14px 0 0; font: 500 26px/1.3 ui-monospace,Menlo,monospace; color: #4fd1c5; letter-spacing: .35em; }
  #demo-card p { margin: 26px 0 0; font: 400 21px/1.6 "PingFang TC","Noto Sans TC",sans-serif; color: #b9c7d6; }
  #demo-card .feat { display: flex; gap: 14px; justify-content: center; margin-top: 30px; flex-wrap: wrap; }
  #demo-card .feat span { border: 1px solid rgba(79,209,197,.5); color: #d6e2ee; border-radius: 999px; padding: 7px 16px;
    font: 500 17px/1 "PingFang TC","Noto Sans TC",sans-serif; }
  #demo-card small { display: block; margin-top: 34px; color: #6b7a8c; font: 14px/1.5 ui-monospace,Menlo,monospace; }
  #demo-cursor { position: fixed; z-index: 70; width: 26px; height: 26px; margin: -13px 0 0 -13px; border-radius: 50%;
    border: 2px solid #fff; background: rgba(255,255,255,.18); box-shadow: 0 0 14px rgba(255,255,255,.6);
    pointer-events: none; left: -60px; top: -60px; transition: left .75s cubic-bezier(.45,.05,.2,1), top .75s cubic-bezier(.45,.05,.2,1); }
  .demo-ripple { position: fixed; z-index: 69; width: 18px; height: 18px; margin: -9px 0 0 -9px; border-radius: 50%;
    border: 2px solid #4fd1c5; pointer-events: none; animation: demo-rip .6s ease-out forwards; }
  @keyframes demo-rip { to { transform: scale(3.4); opacity: 0; } }
  body.demo-nogate #zoom-gate { display: none !important; }
  `;
  const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
  const cap = Object.assign(document.createElement("div"), { id: "demo-cap" });
  const card = Object.assign(document.createElement("div"), { id: "demo-card" });
  const cur = Object.assign(document.createElement("div"), { id: "demo-cursor" });
  document.body.append(cap, card, cur);
  document.body.classList.add("demo-nogate");
  // honest labelling: this recording uses simulated data
  const badge = document.getElementById("mode");
  new MutationObserver(() => { if (badge.textContent === "mock") badge.textContent = "demo data"; })
    .observe(badge, { childList: true, characterData: true, subtree: true });

  window.demo = {
    caption(zh, en) {
      if (!zh) { cap.classList.remove("on"); return; }
      const swap = () => { cap.innerHTML = `<div class="zh">${zh}</div><div class="en">${en || ""}</div>`; cap.classList.add("on"); };
      if (cap.classList.contains("on")) { cap.classList.remove("on"); setTimeout(swap, 380); } else swap();
    },
    card(html) {
      if (!html) { card.classList.remove("on"); return; }
      card.innerHTML = `<div class="inner">${html}</div>`;
      requestAnimationFrame(() => card.classList.add("on"));
    },
    cursor(x, y) { cur.style.left = x + "px"; cur.style.top = y + "px"; },
    ripple(x, y) {
      const r = Object.assign(document.createElement("div"), { className: "demo-ripple" });
      r.style.left = x + "px"; r.style.top = y + "px"; document.body.appendChild(r);
      setTimeout(() => r.remove(), 700);
    },
    showGate(on) { document.body.classList.toggle("demo-nogate", !on); },
  };
})();
