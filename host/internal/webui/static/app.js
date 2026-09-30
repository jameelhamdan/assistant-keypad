"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

async function api(method, path, body) {
  const r = await fetch("/api" + path, {
    method,
    headers: { "X-Keypad": "1", "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || r.statusText);
  return data;
}

function flash(el, text, kind = "ok") {
  el.textContent = text;
  el.className = "msg " + kind;
  if (kind === "ok") setTimeout(() => { if (el.textContent === text) el.textContent = ""; }, 2500);
}

async function run(btn, msgEl, fn, okText = "Saved") {
  btn && (btn.disabled = true);
  try { await fn(); flash(msgEl, okText); }
  catch (e) { flash(msgEl, e.message, "err"); }
  finally { btn && (btn.disabled = false); }
}

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v; else if (k === "text") e.textContent = v; else e.setAttribute(k, v);
  }
  e.append(...kids);
  return e;
}

const linkName = l => ({ usb: "USB", wifi: "Wi-Fi", fake: "Simulated" })[l] || l;

function ago(iso) {
  const s = Math.max(0, (Date.now() - new Date(iso)) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + " min ago";
  return Math.floor(s / 3600) + " h ago";
}

// ---- tabs ------------------------------------------------------------------
$$("#tabs button").forEach(b => b.addEventListener("click", () => {
  $$("#tabs button").forEach(x => x.classList.toggle("on", x === b));
  $$(".tab").forEach(t => t.classList.toggle("on", t.id === b.dataset.tab));
  location.hash = b.dataset.tab;
  if (b.dataset.tab === "claude") loadClaude();
}));
if (location.hash) $(`#tabs button[data-tab="${location.hash.slice(1)}"]`)?.click();

// ---- live status ------------------------------------------------------------
let status = null;
let editing = new Set(); // keypad ids with unsaved edits: don't overwrite their forms

async function refresh() {
  try { status = await api("GET", "/status"); } catch { return; }
  $("#paused").checked = status.paused;
  renderOverview();
  renderKeypads();
}

function renderOverview() {
  const live = new Map(status.keypads.map(k => [k.id, k]));
  const box = $("#ov-keypads");
  box.replaceChildren();
  if (!status.devices.length) {
    box.append(el("div", { class: "card muted", text: "No keypad yet. Plug one in with USB." }));
  }
  for (const d of status.devices) {
    const k = live.get(d.id);
    const how = k ? linkName(k.link) : (d.key === "true" ? "Offline" : "Not connected");
    const card = el("div", { class: "card" },
      el("div", { class: "kp-head" }, el("b", { text: d.name }), el("span", { class: "pill" + (k ? " live" : ""), text: how })),
      el("div", { class: "muted", text: k ? [k.wifi?.ip, k.battery ? k.battery + "%" : "", "fw " + k.fw].filter(Boolean).join(" · ") : d.id }));
    box.append(card);
  }

  const sess = $("#ov-sessions");
  sess.replaceChildren();
  if (!status.sessions.length) sess.append(el("div", { class: "empty", text: "No active sessions. Start Claude Code in any project." }));
  for (const s of status.sessions) {
    sess.append(el("div", { class: "item" },
      el("span", { class: "dot " + s.state }),
      el("div", { class: "grow" },
        el("div", {}, el("b", { text: s.project || s.id.slice(0, 8) }), document.createTextNode("  " + s.title), s.worker ? el("span", { class: "pill", text: "worker" }) : ""),
        el("div", { class: "detail", text: s.detail || "" })),
      el("span", { class: "muted", text: ago(s.last) })));
  }

  const ws = status.workers || [];
  $("#ov-workers-h").hidden = !ws.length;
  const wbox = $("#ov-workers");
  wbox.replaceChildren();
  for (const w of ws) {
    const stop = el("button", { class: "ghost", text: "Stop" });
    stop.onclick = () => api("POST", `/workers/${w.pid}/stop`).then(refresh);
    wbox.append(el("div", { class: "item" },
      el("span", { class: "dot " + (w.done ? (w.exit ? "failed" : "done") : "working") }),
      el("div", { class: "grow" }, el("b", { text: `${w.project}: ${w.label}` }), el("div", { class: "detail", text: w.result || (w.done ? "exit " + w.exit : "running…") })),
      w.done ? "" : stop));
  }
}

$("#paused").addEventListener("change", e => api("POST", "/pause", { paused: e.target.checked }));

// ---- keypads ------------------------------------------------------------------
function renderKeypads() {
  const live = new Map(status.keypads.map(k => [k.id, k]));
  const list = $("#kp-list");
  const seen = new Set();
  for (const d of status.devices) {
    seen.add(d.id);
    let card = list.querySelector(`[data-id="${d.id}"]`);
    if (!card) {
      card = $("#t-keypad").content.firstElementChild.cloneNode(true);
      card.dataset.id = d.id;
      bindKeypad(card, d.id);
      list.append(card);
    }
    const k = live.get(d.id);
    $(".kp-name", card).textContent = d.name;
    $(".kp-id", card).textContent = d.id;
    const link = $(".kp-link", card);
    link.textContent = k ? linkName(k.link) : "offline";
    link.classList.toggle("live", !!k);
    const meta = [];
    if (d.key === "true") meta.push("paired · Wi-Fi " + (d.ssid || "?"));
    else meta.push("USB only");
    if (k?.wifi?.state === "up") meta.push(k.wifi.ip + " (" + k.wifi.rssi + " dBm)");
    if (k?.fw) meta.push("firmware " + k.fw);
    if (k?.battery) meta.push("battery " + k.battery + "%");
    $(".kp-meta", card).textContent = meta.join(" · ");
    $(".kp-pair", card).disabled = !(k && k.link === "usb");
    $(".kp-pair", card).title = k && k.link === "usb" ? "" : "Plug the keypad in with USB to set up Wi-Fi";
    $(".kp-ident", card).disabled = !k;
    $(".kp-update", card).disabled = !k;
    if (!editing.has(d.id)) {
      $(".kp-f-name", card).value = d.name;
      $(".kp-f-theme", card).value = d.theme || "system";
      $(".kp-f-bright", card).value = d.brightness || 80;
      $(".kp-f-proj", card).value = (d.projects || []).join(", ");
    }
  }
  $$("[data-id]", list).forEach(c => { if (!seen.has(c.dataset.id)) c.remove(); });
  if (!status.devices.length) {
    list.replaceChildren(el("div", { class: "card muted", text: "No keypads yet. Plug one in with a USB cable and it will appear here." }));
  } else {
    list.querySelector(".card.muted:not(.keypad)")?.remove();
  }

  const strangers = (status.discovered || []).filter(s => !status.devices.some(d => d.id === s.id));
  $("#kp-discovered").replaceChildren(...(strangers.length ? [
    el("h2", { text: "Other keypads on this network" }),
    el("div", { class: "list" }, ...strangers.map(s => el("div", { class: "item" },
      el("div", { class: "grow" }, el("b", { text: s.id }), el("div", { class: "detail", text: s.paired ? "Paired with another computer" : "Not paired. Plug it in with USB to pair." })),
      el("span", { class: "pill", text: s.addr }))))] : []));
}

function bindKeypad(card, id) {
  const msg = $(".msg", card);
  for (const f of $$("input, select", card)) f.addEventListener("input", () => editing.add(id));
  $(".kp-f-bright", card).addEventListener("change", e => api("PATCH", `/devices/${id}`, { brightness: +e.target.value }));
  $(".kp-f-theme", card).addEventListener("change", e => api("PATCH", `/devices/${id}`, { theme: e.target.value }));
  $(".kp-save", card).onclick = e => run(e.target, msg, async () => {
    await api("PATCH", `/devices/${id}`, {
      name: $(".kp-f-name", card).value.trim(),
      theme: $(".kp-f-theme", card).value,
      brightness: +$(".kp-f-bright", card).value,
      projects: $(".kp-f-proj", card).value.split(",").map(s => s.trim()).filter(Boolean),
    });
    editing.delete(id);
    await refresh();
  });
  $(".kp-ident", card).onclick = e => run(e.target, msg, () => api("POST", `/devices/${id}/identify`), "Look at the keypad");
  $(".kp-unpair", card).onclick = e => {
    if (e.target.dataset.armed !== "1") {
      e.target.dataset.armed = "1"; e.target.textContent = "Really forget?";
      setTimeout(() => { e.target.dataset.armed = ""; e.target.textContent = "Forget"; }, 4000);
      return;
    }
    run(e.target, msg, async () => { await api("POST", `/devices/${id}/unpair`); await refresh(); }, "Forgotten");
  };
  $(".kp-update", card).onclick = e => run(e.target, msg, async () => {
    const fw = await api("GET", "/firmware");
    if (!fw.bundled) throw new Error("This build has no bundled firmware");
    await api("POST", `/devices/${id}/update`);
    for (;;) {
      await new Promise(r => setTimeout(r, 700));
      const p = await api("GET", `/devices/${id}/update`);
      if (p.progress < 0) throw new Error("Update failed (see logs)");
      flash(msg, `Updating… ${p.progress}%`, "");
      if (p.progress >= 100) break;
    }
  }, "Updated. The keypad restarts");
  $(".kp-pair", card).onclick = () => openPair(id, $(".kp-f-name", card).value);
}

async function openPair(id, name) {
  const dlg = $("#pair"), f = $("#f-pair");
  $("#pair-id").textContent = id;
  f.name.value = name && name !== id ? name : "";
  f.pass.value = "";
  $(".msg", f).textContent = "";
  if (!f.ssid.value) api("GET", "/ssid").then(r => { if (!f.ssid.value) f.ssid.value = r.ssid; }).catch(() => {});
  dlg.showModal();
  f.onsubmit = ev => {
    if (ev.submitter?.value !== "ok") return;
    ev.preventDefault();
    run(ev.submitter, $(".msg", f), async () => {
      await api("POST", `/devices/${id}/provision`, { ssid: f.ssid.value.trim(), pass: f.pass.value, name: f.name.value.trim() });
      f.pass.value = "";
      setTimeout(() => dlg.close(), 900);
      refresh();
    }, "Paired. The keypad is joining Wi-Fi");
  };
}

// ---- config forms ----------------------------------------------------------------------
let cfg = null;

const get = (o, path) => path.split(".").reduce((x, k) => x?.[k], o);
const set = (o, path, v) => { const ks = path.split("."); const last = ks.pop(); ks.reduce((x, k) => x[k] ??= {}, o)[last] = v; };

function fillForm(form) {
  for (const f of $$("[name]", form)) {
    const v = get(cfg, f.name);
    if (f.type === "checkbox") f.checked = !!v; else f.value = v ?? "";
  }
}

function readForm(form) {
  for (const f of $$("[name]", form)) {
    set(cfg, f.name, f.type === "checkbox" ? f.checked : f.type === "number" ? +f.value : f.value.trim());
  }
}

async function saveConfig(form, btn) {
  await run(btn, $(".msg", form), async () => { cfg = await api("PUT", "/config", cfg); });
}

$("#f-behavior").addEventListener("submit", e => { e.preventDefault(); readForm(e.target); saveConfig(e.target, e.submitter); });

// key map ------------------------------------------------------------------------------
const SCREENS = [
  { name: "Permission", hint: "Claude wants to run a tool", acts: [["allow", "Allow", "ok"], ["deny", "Deny", "danger"], ["pc", "PC", ""]] },
  { name: "Question", hint: "Yes / no questions", acts: [["yes", "Yes", "ok"], ["no", "No", "danger"], ["pc", "PC", ""]] },
  { name: "Claude finished", hint: "The Stop prompt", acts: [["continue", "Continue", "ok"], ["shortcuts", "Shortcut", "accent"], ["done", "Done", ""], ["pc", "PC", ""]] },
  { name: "Status", hint: "Idle screen", acts: [["menu", "Shortcuts", "accent"]] },
];

function renderKeymap() {
  const box = $("#keymaps");
  box.replaceChildren();
  for (const sc of SCREENS) {
    const pad = el("div", { class: "pad" });
    for (let n = 1; n <= 8; n++) {
      const hit = sc.acts.find(([a]) => cfg.keys[a] === n);
      pad.append(el("div", { class: "k " + (hit ? hit[2] : "") }, el("span", { class: "n", text: n }), el("span", { class: "a", text: hit ? hit[1] : "" })));
    }
    const sel = el("div", { class: "km-sel" });
    for (const [a, label] of sc.acts) {
      const s = el("select", { "data-act": a });
      for (let n = 1; n <= 8; n++) s.append(el("option", { value: n, text: "Key " + n }));
      s.value = cfg.keys[a];
      s.onchange = () => { cfg.keys[a] = +s.value; renderKeymap(); };
      sel.append(el("label", {}, label, s));
    }
    box.append(el("div", { class: "km" }, el("div", {}, el("b", { text: sc.name }), el("span", { class: "muted", text: sc.hint })), el("div", {}, pad, sel)));
  }
}
$("#f-keys").addEventListener("submit", e => { e.preventDefault(); saveConfig(e.target, e.submitter); });

// shortcuts ------------------------------------------------------------------------------
function renderShortcuts() {
  const box = $("#sc-list");
  box.replaceChildren();
  cfg.shortcuts.forEach((s, i) => {
    const label = el("input", { maxlength: "28", placeholder: "Label on the keypad" });
    label.value = s.label;
    label.oninput = () => (s.label = label.value);
    const prompt = el("textarea", { placeholder: "Instruction sent to Claude" });
    prompt.value = s.prompt;
    prompt.oninput = () => (s.prompt = prompt.value);
    const up = el("button", { type: "button", class: "ghost", text: "↑", title: "Move up" });
    up.onclick = () => { if (i) { [cfg.shortcuts[i - 1], cfg.shortcuts[i]] = [cfg.shortcuts[i], cfg.shortcuts[i - 1]]; renderShortcuts(); } };
    const del = el("button", { type: "button", class: "danger", text: "✕", title: "Remove" });
    del.onclick = () => { cfg.shortcuts.splice(i, 1); renderShortcuts(); };
    box.append(el("div", { class: "sc" }, el("span", { class: "num", text: i + 1 }), el("div", {}, label, prompt), el("div", { class: "tools" }, up, del)));
  });
}
$("#sc-add").onclick = () => { cfg.shortcuts.push({ label: "", prompt: "" }); renderShortcuts(); $("#sc-list .sc:last-child input").focus(); };
$("#f-shortcuts").addEventListener("submit", e => {
  e.preventDefault();
  cfg.shortcuts = cfg.shortcuts.filter(s => s.label.trim() && s.prompt.trim());
  saveConfig(e.target, e.submitter).then(renderShortcuts);
});

// ---- Claude Code --------------------------------------------------------------------------
async function loadClaude() {
  const r = await api("GET", "/claude");
  const st = r.status;
  const box = $("#cc-status");
  const state = r.installed ? ["Installed", "ok"] : st.hooks ? ["Needs repair", "warn"] : ["Not installed", ""];
  box.replaceChildren(
    el("div", { class: "kp-head" }, el("b", { text: "Claude Code integration" }), el("span", { class: "pill " + (r.installed ? "live" : ""), text: state[0] })),
    el("div", { class: "muted", text: `${st.hooks}/${st.expected} hooks · MCP server ${st.mcp ? "registered" : "missing"}${st.stale ? " · hooks point at an older install" : ""}` }),
    el("div", { class: "muted", text: "claude CLI: " + (r.claude || "not found") }));
  return r;
}
$("#cc-install").onclick = e => run(e.target, $("#claude .msg"), async () => { await api("POST", "/claude/install"); await loadClaude(); banner(); }, "Installed. Restart Claude Code");
$("#cc-uninstall").onclick = e => run(e.target, $("#claude .msg"), async () => { await api("POST", "/claude/uninstall"); await loadClaude(); }, "Removed");
$("#open-logs").onclick = () => api("POST", "/logs/open");

async function banner() {
  const r = await api("GET", "/claude").catch(() => null);
  const b = $("#setup-banner");
  if (!r || r.installed) { b.hidden = true; return; }
  const go = el("button", { class: "primary", text: "Install" });
  go.onclick = e => run(e.target, b.querySelector(".msg") || b.appendChild(el("span", { class: "msg" })), async () => { await api("POST", "/claude/install"); b.hidden = true; });
  b.replaceChildren(el("span", { text: "Claude Code isn't connected to the keypad yet." }), go);
  b.hidden = false;
}

// ---- boot ----------------------------------------------------------------------------------
(async () => {
  cfg = await api("GET", "/config");
  fillForm($("#f-behavior"));
  renderKeymap();
  renderShortcuts();
  await refresh();
  banner();
  setInterval(refresh, 1500);
})();
