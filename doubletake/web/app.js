"use strict";

// The screens. They hold no data of their own: a couple of times a second they
// ask the engine for its state and redraw from it, and they send it the buttons
// that are pressed. Reloading or closing this page changes nothing in the engine.

const $ = (id) => document.getElementById(id);
const VIEWS = ["home", "ready", "copy", "done", "library", "grid", "viewer", "editor", "exports", "settings"];
// Where Back goes from each screen.
const BACK = { ready: "home", library: "home", grid: "library", viewer: "grid", editor: "viewer", exports: "library", settings: "home" };

let S = null;          // the engine's latest state
let view = "home";     // the screen the user chose: home or ready
let shown = "";        // the screen that is up
let misses = 0;        // polls in a row that got no answer
let stopped = false;   // Exit was pressed
let busy = false;      // a poll is in flight
let stopping = false;  // Stop backup was pressed
let lists = {};        // what each rebuilt list was last built from

// ----- small helpers ----------------------------------------------------------

function setText(id, value) {
  const el = $(id);
  const text = value === null || value === undefined ? "" : String(value);
  if (el.textContent !== text) el.textContent = text;
}
// Icons are SVG elements, which have no "hidden" property, so set the attribute.
function show(id, on) { $(id).toggleAttribute("hidden", !on); }
// "microSD" keeps its small m.
function cap(s) { return !s ? "" : s.indexOf("microSD") === 0 ? s : s.charAt(0).toUpperCase() + s.slice(1); }
const numberFormat = new Intl.NumberFormat("en-US");
function n(v) { return numberFormat.format(Math.round(v || 0)); }
function plural(count, one, many) { return count === 1 ? one : many; }

const UNITS = ["B", "KB", "MB", "GB", "TB"];
function unitFor(v) {
  let i = 0;
  let x = Math.abs(v || 0);
  while (x >= 1000 && i < UNITS.length - 1) { x /= 1000; i += 1; }
  return i;
}
function inUnit(v, i) {
  const x = (v || 0) / Math.pow(1000, i);
  return i === 0 ? String(Math.round(x)) : x.toFixed(1);
}
// Like setText, but Sony's α stays a small α in headings set in capitals.
// A camera's name, kept in two pieces that never break inside: the brand, then the model.
// When the line is too narrow it wraps between them, so Insta360 sits over Ace Pro 2,
// never "Insta360 Ace" over "Pro 2".
const BRANDS = ["Camera card", "OM System", "Insta360", "Nikon", "Canon", "Sony", "Fujifilm", "Panasonic",
  "Olympus", "Leica", "GoPro", "DJI", "Ricoh", "Pentax", "Hasselblad", "Sigma", "Apple", "Samsung", "Google"];
function nameParts(text) {
  text = String(text || "").trim();
  const low = text.toLowerCase();
  const brand = BRANDS.find((b) => low === b.toLowerCase() || low.startsWith(b.toLowerCase() + " "));
  const cut = brand ? brand.length : text.indexOf(" ");
  if (cut <= 0 || cut >= text.length) return [text];
  return [text.slice(0, cut), text.slice(cut + 1).trim()].filter(Boolean);
}
function setName(id, text, split = false) {
  const el = $(id);
  text = String(text);
  if (el.dataset.name === text) return;
  el.dataset.name = text;
  const parts = split ? nameParts(text) : [text];
  el.replaceChildren(...parts.flatMap((piece, i) => {
    const span = document.createElement("span");
    if (split) span.className = "nw";
    span.append(...piece.split(/(α)/).filter(Boolean).map((part) => {
      if (part !== "α") return document.createTextNode(part);
      const a = document.createElement("span"); a.className = "lc"; a.textContent = "α"; return a;
    }));
    return i ? [document.createTextNode(" "), span] : [span];
  }));
}

// A long file name shortened in the middle, so its start and its number and type stay:
// IMG_2026…053354_063.dng
// The version, with a closing "c" in the accent colour: 1.0c.
function renderVersion(el, version) {
  version = String(version || "");
  if (el.dataset.v === version) return;
  el.dataset.v = version;
  const m = version.match(/^(.*\d)(c)(.*)$/);
  if (!m) { el.textContent = version; return; }
  const c = document.createElement("span");
  c.className = "ver-c"; c.textContent = m[2];
  el.replaceChildren(m[1], c, m[3]);
}

function middle(name, max) {
  name = String(name || "");
  if (name.length <= max) return name;
  const tail = Math.ceil((max - 1) * 0.6);
  return name.slice(0, max - 1 - tail) + "…" + name.slice(-tail);
}
// "58.2 GB free of 256 GB": what is left first, then the size of the drive.
function space(free, total) {
  return bytes(free) + " free" + (total ? " of " + bytes(total) : "");
}
function bytes(v) { const i = unitFor(v); return inUnit(v, i) + " " + UNITS[i]; }
function rate(bytesPerSecond) {
  const mb = (bytesPerSecond || 0) / 1e6;
  return (mb >= 100 ? mb.toFixed(0) : mb.toFixed(1)) + " MB/s";
}
function clock(seconds) {
  const s = Math.max(0, Math.round(seconds || 0));
  const two = (x) => String(x).padStart(2, "0");
  return two(Math.floor(s / 3600)) + ":" + two(Math.floor((s % 3600) / 60)) + ":" + two(s % 60);
}
function about(seconds) {
  const s = seconds || 0;
  if (s <= 0) return "";
  if (s < 90) return "about a minute";
  if (s < 3600) return "about " + Math.round(s / 60) + " min";
  return "about " + Math.floor(s / 3600) + " h " + Math.round((s % 3600) / 60) + " min";
}
function leaf(path) { return (path || "").split("/").filter(Boolean).pop() || ""; }
function percent(job) { return job && job.total_bytes ? Math.min(100, (100 * job.done_bytes) / job.total_bytes) : 0; }

async function post(path, body) {
  try {
    const reply = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-DoubleTake": "1" },
      body: JSON.stringify(body || {}),
    });
    const data = await reply.json();
    poll();
    return data;
  } catch (err) {
    return { ok: false, error: "The engine did not answer." };
  }
}

// ----- talking to the engine ----------------------------------------------------

async function poll() {
  if (busy || stopped) return;
  busy = true;
  try {
    const reply = await fetch("/api/state", { cache: "no-store" });
    S = await reply.json();
    misses = 0;
    show("gone", false);
    render();
  } catch (err) {
    misses += 1;
    if (misses >= 4) {
      setText("gone-title", "Not running");
      setText("gone-text", "DoubleTake is not answering. Start it again with: doubletake gui");
      show("gone", true);
    }
  } finally {
    busy = false;
  }
}

// ----- what the tiles say, worked out once and used on several screens -----------

// "40 JPEGs and 3 videos", for the files the What to copy setting leaves on the camera.
const COPY_NAMES = { raw: "RAW only", raw_jpeg: "RAW + JPEG", all: "Everything" };
function skippedList(k) {
  if (!k || !k.count) return "";
  const parts = [];
  if (k.raw) parts.push(n(k.raw) + plural(k.raw, " RAW file", " RAW files"));
  if (k.jpeg) parts.push(n(k.jpeg) + plural(k.jpeg, " JPEG", " JPEGs"));
  if (k.video) parts.push(n(k.video) + plural(k.video, " video", " videos"));
  if (k.other) parts.push(n(k.other) + plural(k.other, " other file", " other files"));
  return parts.length > 1 ? parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1] : parts[0];
}
// RAW only, with a camera that always makes a JPEG beside its RAW (the Insta360 Ace Pro 2).
function twinsText(k) {
  if (!k || !k.twins) return "";
  return "The JPEG beside each RAW is used for its picture and not kept: with RAW only, it is not needed.";
}
function skippedText(k, choice, photos) {
  const list = skippedList(k);
  if (!list) return twinsText(k);
  const why = "What to copy is set to " + (COPY_NAMES[choice] || choice) + " (Settings, Backup rules).";
  if (!photos) return "No files to copy: the cards hold only " + list + ". " + why;
  return "Not copied: " + list + ". " + why + (twinsText(k) ? " " + twinsText(k) : "");
}

function formatInfo() {
  const f = S.format;
  if (!f.known) {
    return { kind: "unknown", title: "Not checked", badge: "",
             text: "Connect the camera to see whether its cards can be formatted." };
  }
  const left = skippedList(f.skipped);
  if (!f.photos && left) {
    return { kind: "no", title: "Nothing copied", badge: "Do not format",
             text: "The cards hold only " + left + ", which What to copy leaves on the camera." };
  }
  if (!f.photos) {
    return { kind: "unknown", title: "Cards are empty", badge: "", text: "There are no photos on the camera's cards." };
  }
  const asRead = f.connected ? "" : " As read at " + f.read_at + ". Photos taken since then are not covered.";
  if (f.safe) {
    return { kind: "safe", title: "Safe to format", badge: "Safe to format",
             text: "Every photo on the camera's cards is verified on Copy 1 and on Copy 2." +
                   (f.skipped && f.skipped.twins ? " The JPEGs beside the RAW files are not needed with RAW only." : "") + asRead };
  }
  if (f.copied_safe) {
    return { kind: "no", title: f.copy_what === "raw" ? "RAW files are safe" : "Copied photos are safe", badge: "Not all copied",
             text: "Everything copied is in two places. " + left.charAt(0).toUpperCase() + left.slice(1) + " " +
                   (f.skipped.count === 1 ? "isn't copied (What to copy), so formatting would lose it."
                                          : "aren't copied (What to copy), so formatting would lose them.") + asRead };
  }
  let why;
  if (f.cleared) {
    why = n(f.cleared) + plural(f.cleared, " photo on the cards was", " photos on the cards were") +
          " copied before and deleted from DoubleTake since, so " + plural(f.cleared, "it is", "they are") +
          " on neither copy now. Format only if you are sure " + plural(f.cleared, "it is", "they are") + " safe elsewhere.";
    if (f.on_tablet + f.cleared < f.photos) {
      const k = f.photos - f.on_tablet - f.cleared;
      why = n(k) + plural(k, " photo is", " photos are") + " not on the tablet yet. " + why;
    }
    return { kind: "no", title: "Do not format yet", badge: "Do not format", text: why + asRead };
  }
  if (f.on_tablet < f.photos) {
    const k = f.photos - f.on_tablet;
    why = n(k) + plural(k, " photo is", " photos are") + " not on the tablet yet.";
  } else {
    const k = f.photos - f.on_second;
    why = n(k) + plural(k, " photo has", " photos have") + " no second copy yet.";
  }
  return { kind: "no", title: "Do not format yet", badge: "Do not format", text: why + asRead };
}

function secondInfo() {
  const k = S.card;
  const m = S.mirror;
  const copying = S.backup && S.backup.running;
  const out = { title: "", text: "", bar: null, file: "", button: "", action: "", ok: false, running: false,
                line: k.label ? (k.label !== "DT-COPY2" ? "'" + k.label + "', " : "") + space(k.free, k.total) : "" };
  const word = k.kind === "usb" ? "drive" : "card";
  switch (k.state) {
    case "off":
      out.title = "Switched off";
      out.text = "The second copy is switched off in the settings.";
      break;
    case "declined":
      out.title = cap(word) + " not in use";
      out.text = "You chose not to use the " + word + " that is " + (word === "drive" ? "plugged in." : "inserted.");
      out.button = "Use this " + word; out.action = "/api/card/use";
      break;
    case "several":
      out.title = "More than one drive";
      out.text = "Unplug all but one. None is used until then.";
      break;
    case "readonly":
      out.title = cap(word) + " is read-only";
      out.text = "It cannot hold the second copy.";
      break;
    case "new":
      out.title = "New " + word;
      out.text = "Waiting for your answer.";
      break;
    case "ready":
      out.ok = true;
      if (m.state === "running") {
        const pct = percent(m);
        out.running = true;
        out.title = "Copying " + pct.toFixed(0) + "%";
        const left = about(m.remaining);
        out.text = n(m.done_files) + " of " + n(m.total_files) + " photos. " + rate(m.speed_avg) +
                   (left ? ", " + left + " left." : ".");
        out.bar = pct; out.file = m.current;
        out.button = "Pause"; out.action = "/api/mirror/pause";
      } else if (m.state === "error") {
        out.title = "Stopped";
        out.text = m.message + " " + n(k.waiting) + plural(k.waiting, " photo", " photos") + " waiting.";
        out.button = "Try again"; out.action = "/api/mirror/resume";
      } else if (k.waiting > 0) {
        const waiting = n(k.waiting) + plural(k.waiting, " photo", " photos") + " (" + bytes(k.waiting_bytes) + ") waiting.";
        if (m.paused_by_user || m.state === "paused") {
          out.title = "Paused"; out.text = waiting;
          out.button = "Resume"; out.action = "/api/mirror/resume";
        } else if (copying) {
          out.title = "Next"; out.text = "Starts by itself once a tenth of the camera copy is done, and follows behind it. " + waiting;
        } else {
          out.title = "Starting"; out.text = waiting;
        }
      } else if (k.held > 0) {
        out.title = "Up to date";
        out.text = n(k.held) + plural(k.held, " photo", " photos") + " on the " + k.noun + ", each read back and checked.";
      } else {
        out.title = "Ready";
        out.text = "Nothing to copy yet.";
      }
      break;
    default: {
      const usbDrive = k.kind === "usb";
      out.title = usbDrive ? "No USB drive" : "No card";
      out.text = k.wanted ? "The folder named in the settings is not there."
        : usbDrive ? "Plug in the USB drive. Photos waiting are copied to it by itself, and the camera cards are not called safe to format until they are."
        : "Put a microSD card in the tablet. Until then, backups exist only on the tablet.";
      if (k.unopened && k.unopened.length) {
        out.text = usbDrive ? "A drive is plugged in, but the system has not opened it. Open Files and select the drive."
          : "A card is in the slot, but the system has not opened it. Open Files and select the card.";
      } else if (k.other_kind > 0) {
        out.text += usbDrive ? " A microSD card is in the slot; to use it instead, choose microSD card in Settings, Second copy."
          : " A USB drive is plugged in; to use it instead, choose USB drive in Settings, Second copy.";
      }
    }
  }
  return out;
}

// ----- drawing --------------------------------------------------------------------

// ----- unattended backup ---------------------------------------------------------------

function renderAuto() {
  const a = S.auto;
  show("auto", !!a);
  if (a) {
    if (a.state === "countdown") {
      $("auto-title").innerHTML = 'Backing up in <span class="lc">' + Number(a.left) + " s</span>";
      setText("auto-text", "Into " + (a.folder || "a new shoot") + ". The second copy follows by itself. " +
              "Tap Cancel to only look at the camera.");
      show("auto-now", true);
      setText("auto-cancel", "Cancel");
    } else {
      setText("auto-title", "Did not start");
      setText("auto-text", a.message);
      show("auto-now", false);
      setText("auto-cancel", "OK");
    }
  }
}

// ----- beeps: one as a backup starts, two when done, three low ones when it needs a look -----

let lastSound = null;
function renderSound() {
  const id = S.sound ? S.sound.id : 0;
  if (lastSound !== null && id !== lastSound && S.sound.kind) beeps(S.sound.kind);
  lastSound = id;
}

function beeps(kind) {
  const plan = { start: [988, 1], done: [988, 2], problem: [330, 3] }[kind];
  if (!plan) return;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    for (let i = 0; i < plan[1]; i++) {
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = kind === "problem" ? "square" : "sine";
      o.frequency.value = plan[0];
      const t = ctx.currentTime + i * 0.26, long = kind === "problem" ? 0.2 : 0.14;
      g.gain.setValueAtTime(0.0001, t);
      g.gain.exponentialRampToValueAtTime(kind === "problem" ? 0.12 : 0.3, t + 0.01);
      g.gain.setValueAtTime(kind === "problem" ? 0.12 : 0.3, t + long - 0.03);
      g.gain.exponentialRampToValueAtTime(0.0001, t + long);
      o.connect(g).connect(ctx.destination);
      o.start(t);
      o.stop(t + long + 0.02);
    }
    setTimeout(() => ctx.close(), 2000);
  } catch (err) { /* no sound on this tablet */ }
}

// ----- the tablet's status (P3, or Settings, Device) ---------------------------------------

let statusAt = 0, statusShown = false;
function renderStatus() {
  const open = !!S.status_open;
  if (open && (!statusShown || Date.now() - statusAt > 5000)) loadStatus();
  statusShown = open;
  show("status", open);
}

function statusRows(parent, big, rows, fill, warn) {
  parent.textContent = "";
  const top = document.createElement("div");
  top.className = "big";
  top.textContent = big;
  parent.append(top);
  if (fill !== undefined && fill !== null) {
    const bar = document.createElement("div"), span = document.createElement("span");
    bar.className = "bar";
    span.style.width = Math.max(0, Math.min(100, fill)) + "%";
    if (warn) span.className = "low";
    bar.append(span);
    parent.append(bar);
  }
  for (const text of rows) {
    if (!text) continue;
    const row = document.createElement("div");
    row.className = "row";
    row.textContent = text;
    parent.append(row);
  }
}

function minutesText(m) {
  if (m === null || m === undefined) return "";
  const h = Math.floor(m / 60), r = m % 60;
  return h ? h + " h " + String(r).padStart(2, "0") + " min" : r + " min";
}

async function loadStatus() {
  statusAt = Date.now();
  let t;
  try { t = await getJSON("/api/status"); } catch (err) { return; }
  if (!t || t.error) return;
  setText("status-sub", t.host ? "on " + t.host : "");
  statusRows($("status-dt"), t.version, [t.system]);
  statusRows($("status-photos"), n(t.photos) + plural(t.photos, " photo", " photos"),
             [n(t.shoots) + plural(t.shoots, " shoot", " shoots") + " in the library"]);
  const c1 = t.copy1;
  statusRows($("status-copy1"), bytes(c1.bytes) + " of photos",
             [bytes(c1.free) + " free of " + bytes(c1.total)],
             c1.total ? 100 - 100 * c1.free / c1.total : null, c1.total && c1.free / c1.total < 0.1);
  const c2 = t.copy2;
  setText("status-copy2-label", "Copy 2 · " + (c2 ? c2.noun : "card"));
  if (c2) {
    statusRows($("status-copy2"), bytes(c2.bytes) + " of photos",
               [n(c2.photos) + plural(c2.photos, " photo", " photos") + (c2.waiting ? ", " + n(c2.waiting) + " waiting" : ", up to date"),
                bytes(c2.free) + " free of " + bytes(c2.total)],
               c2.total ? 100 - 100 * c2.free / c2.total : null, c2.total && c2.free / c2.total < 0.1);
  } else {
    statusRows($("status-copy2"), t.card_state === "none" ? "No card" : "Not in use", ["Photos are only on the tablet until it is back."]);
  }
  const box = $("status-batteries");
  box.textContent = "";
  const p = t.power || {};
  const packs = p.packs || [];
  if (!packs.length) {
    const card = document.createElement("div");
    card.className = "status-card";
    statusRows(card, p.on_power === false ? "On battery" : "On the charger", ["No battery found."]);
    box.append(card);
  }
  const time = minutesText(p.minutes);
  for (const pack of packs) {
    const card = document.createElement("div");
    card.className = "status-card";
    const label = document.createElement("div");
    label.className = "label";
    label.textContent = pack.label + (pack.model ? " · " + pack.model : "");
    card.append(label);
    const inner = document.createElement("div");
    const state = { charging: "Charging", discharging: "In use", full: "Full", "not charging": "Not charging" }[pack.status] || pack.status;
    statusRows(inner, (pack.level === null ? "?" : pack.level + "%") + "  ·  " + state,
               [pack.health ? "Health " + pack.health + "% of new" + (pack.cycles ? ", " + n(pack.cycles) + plural(pack.cycles, " cycle", " cycles") : "")
                            : (pack.cycles ? n(pack.cycles) + plural(pack.cycles, " cycle", " cycles") : "")],
               pack.level, pack.level !== null && pack.level <= 20);
    card.append(inner);
    box.append(card);
  }
  if (time && packs.length) {
    const row = document.createElement("div");
    row.className = "body";
    row.style.gridColumn = "1 / -1";
    row.textContent = (p.on_power ? "Full in about " : "About ") + time + (p.on_power ? "" : " left, at the rate it is going now.");
    box.append(row);
  }
}

$("status").addEventListener("click", () => { show("status", false); S.status_open = false; post("/api/status/close"); });
$("set-status").addEventListener("click", () => { S.status_open = true; statusShown = false; renderStatus(); post("/api/status/open"); });

// ----- on the way out: each real step, with OK as it finishes -------------------------------

const SPIN = ["*     ", "**    ", "***   ", " ***  ", "  *** ", "   ***", "    **", "     *"];
let spinAt = 0, spinTimer = null, leavingShown = false;
function renderSwitching() {
  const l = S.leaving;
  if (!l || !leavingShown) return;
  if (l.error) {
    leavingShown = false;
    show("switching", false);
    ask("Still here", l.error, "OK");
    return;
  }
  drawSwitching(l);
}

function drawSwitching(l) {
  const box = $("switching-lines");
  box.textContent = "";
  const head = document.createElement("div");
  head.className = "head";
  head.textContent = "DoubleTake " + (S.version || "") + "  ·  " +
    ({ desktop: "switching to Maintenance mode", logout: "logging out", restart: "restarting", shutdown: "shutting down" }[l.action] || "");
  box.append(head);
  for (const step of l.steps || []) {
    const row = document.createElement("div");
    const mark = document.createElement("span");
    if (step.state === "ok") { mark.className = "ok"; mark.textContent = "  OK  "; }
    else if (step.state === "fail") { mark.className = "fail"; mark.textContent = "FAILED"; }
    else { mark.className = "run"; mark.textContent = SPIN[spinAt % SPIN.length]; }
    row.append("[", mark, "] " + step.text + (step.state === "run" || step.state === "wait" ? "..." : ""));
    box.append(row);
  }
  if (l.note) {
    const note = document.createElement("div");
    note.className = "note";
    note.textContent = l.note;
    box.append(note);
  }
}

function startSwitching(action) {
  leavingShown = true;
  if (!S.leaving || S.leaving.action !== action) S.leaving = { action: action, steps: [], done: false, error: "" };
  show("switching", true);
  drawSwitching(S.leaving);
  if (!spinTimer) spinTimer = setInterval(() => { spinAt += 1; if (leavingShown && S.leaving) drawSwitching(S.leaving); }, 140);
}

// ----- the volume and brightness keys: the kiosk has no handler of its own for them -----

const MEDIA_KEYS = { AudioVolumeUp: "volume_up", VolumeUp: "volume_up", AudioVolumeDown: "volume_down",
                     VolumeDown: "volume_down", AudioVolumeMute: "mute", VolumeMute: "mute",
                     BrightnessUp: "brightness_up", BrightnessDown: "brightness_down",
                     MonBrightnessUp: "brightness_up", MonBrightnessDown: "brightness_down" };
let osdTimer = null, lastLevel = null;
document.addEventListener("keydown", async (e) => {
  const key = MEDIA_KEYS[e.key];
  if (!key || stopped) return;
  e.preventDefault();
  showLevel(await post("/api/media", { key: key }));
}, true);
// The brightness keys come through the engine (the helper reads them), so the level comes in the state.
function renderLevel() {
  const l = S.level_shown || {};
  if (lastLevel !== null && l.id !== lastLevel) showLevel(l);
  lastLevel = l.id;
}
function showLevel(reply) {
  if (!reply || reply.error || reply.level === undefined) return;
  const muted = reply.kind === "volume" && reply.muted;
  setText("osd-label", reply.kind === "volume" ? (muted ? "Muted" : "Volume") : "Brightness");
  $("osd-fill").style.width = (muted ? 0 : Math.max(0, Math.min(100, reply.level))) + "%";
  setText("osd-value", muted ? "" : reply.level + "%");
  show("osd", true);
  clearTimeout(osdTimer);
  osdTimer = setTimeout(() => show("osd", false), 1500);
}

// A touch or a key tells the engine someone is here: it lights a dark screen, and holds off
// darkening it and sleep.
let touchSent = 0, wokenAt = 0;
function someoneHere(e) {
  if (!S) return;
  const dark = !$("blackout").hidden;
  if (dark) {
    // The first touch or key only lights the screen.
    e.preventDefault();
    e.stopPropagation();
    $("blackout").hidden = true;
    wokenAt = Date.now();
  }
  if (dark || S.screen_dark || Date.now() - touchSent > 15000) {
    touchSent = Date.now();
    post("/api/screen/on");
  }
}
for (const kind of ["pointerdown", "pointerup", "click", "keydown"]) {
  document.addEventListener(kind, (e) => {
    if (kind === "pointerdown" || kind === "keydown") return someoneHere(e);
    // The rest of the waking touch presses nothing either.
    if (Date.now() - wokenAt < 600) { e.preventDefault(); e.stopPropagation(); }
  }, true);
}
// Copy 2's icon on every screen: a microSD card, or a USB stick when the settings say USB.
// The tablet's own card slot: an SD card. A USB drive: a thumb drive or an external drive, as
// the engine guesses from the drive itself; kept while it is unplugged, an external drive before.
let copy2Style = "drive";
function copy2Icons() {
  const k = S.card || {};
  let style = "card";
  if (k.kind === "usb") {
    if (k.style === "stick" || k.style === "drive") copy2Style = k.style;
    style = copy2Style;
  }
  // An svg has no .hidden property of its own: set the attribute.
  for (const el of document.querySelectorAll(".copy2-sd")) el.toggleAttribute("hidden", style !== "card");
  for (const el of document.querySelectorAll(".copy2-usb")) el.toggleAttribute("hidden", style !== "stick");
  for (const el of document.querySelectorAll(".copy2-drive")) el.toggleAttribute("hidden", style !== "drive");
}

function renderBlackout() {
  // Just woken: wait for the engine to say so, rather than going dark again on an old state.
  if (Date.now() - wokenAt < 3000) return;
  $("blackout").hidden = !S.screen_dark;
}

function render() {
  if (!S || stopped) return;
  renderBlackout();
  renderAuto();
  renderSound();
  renderLevel();
  renderStatus();
  renderSwitching();
  copy2Icons();
  if (document.documentElement.dataset.theme !== S.theme) document.documentElement.dataset.theme = S.theme;
  if (document.documentElement.dataset.look !== S.look) document.documentElement.dataset.look = S.look || "classic";
  rememberLook(S.look || "classic", S.theme);
  const b = S.backup;
  let v = view;
  if (b && b.running) v = "copy";
  else if (b && (v === "home" || v === "ready")) v = "done";
  else if (v === "ready" && S.camera.state !== "ready") { view = "home"; v = "home"; }
  if (v !== shown) {
    for (const name of VIEWS) $("view-" + name).classList.toggle("on", name === v);
    const was = shown;
    shown = v;
    stopping = false;
    if (v === "ready") refreshFolder();
    if (v === "library") loadLibrary();
    if (v === "settings") loadSettings();
    if (was === "viewer") leaveFull();
  }
  renderBar(v);
  if (wiz) wizLive();
  if (v === "home") renderHome();
  if (v === "ready") renderReady();
  if (v === "copy") renderCopy();
  if (v === "done") renderDone();
  if (v === "library") pollLibrary();
  if (v === "settings") renderSecondSettings();
  renderExportJob();
  renderUsbJob();
  renderPhone();
  renderModal();
}

function renderBar(v) {
  show("back", !!BACK[v]);
  show("bar-icon", v === "home");
  const titles = { home: "DoubleTake \u2022 " + (S.edition === "tron" ? "Two Replicas of Negatives" : "Field Station"), library: "Shoot library", settings: "Settings", exports: "Exports" };
  const inShoot = (v === "grid" || v === "viewer" || v === "editor") && shoot;
  setText("bar-title", inShoot ? leaf(shoot.folder) : titles[v] || "Camera backup");
  $("bar-title").classList.toggle("mono", !!inShoot);
  $("bar-title").classList.toggle("title", !inShoot);
  show("bar-count", !!inShoot);
  if (inShoot) {
    setText("bar-count", v === "editor" ? "Edit " + (ed ? ed.file.name : "") : v === "viewer" ? n(vw.index + 1) + " of " + n(shoot.files.length)
                                        : n(shoot.files.length) + plural(shoot.files.length, " photo", " photos"));
  }
  renderNetBar();
  const cam = S.camera;
  const camLevel = batteryPercent(cam.battery);
  show("bar-camera", !!cam.connected && camLevel !== null);
  if (camLevel !== null) {
    drawBatts("bar-camera-batts", [{ label: "Camera", level: camLevel, status: "" }],
              camLevel <= 5 ? "Camera" : "", "Camera battery");
  }
  const chip = $("bar-chip");
  const folder = S.backup ? leaf(S.backup.folder) : "";
  if (v === "ready") { setText("bar-chip", "Ready"); chip.className = "chip"; }
  if (v === "copy") {
    setText("bar-chip", S.backup.phase === "reading" ? "Reading the cards" : "Copying");
    chip.className = "chip solid";
  }
  show("bar-chip", v === "ready" || v === "copy");
  show("bar-folder", v === "done" && !!folder);
  setText("bar-folder", folder);
  const level = S.tablet.battery;
  let packs = S.tablet.packs || [];
  if (!packs.length && level !== undefined && level !== null) {
    packs = [{ label: "Battery", level: level, status: S.tablet.charging ? "charging" : "" }];
  }
  show("bar-packs", packs.length > 0);
  if (packs.length) drawBatts("bar-tablet-batts", packs, S.tablet.swap, "Tablet battery");
  setText("bar-clock", S.clock);
}

function renderBatteryCard() {
  let packs = ((S && S.tablet && S.tablet.packs) || []).slice();
  if (!packs.length && S && S.tablet && S.tablet.battery !== undefined && S.tablet.battery !== null) {
    packs.push({ label: "Tablet battery", level: S.tablet.battery, status: S.tablet.charging ? "charging" : "", model: "" });
  }
  const camLevel = S && S.camera && S.camera.connected ? batteryPercent(S.camera.battery) : null;
  if (camLevel !== null) {
    packs.push({ label: "Camera", level: camLevel, status: "", model: S.camera.model || "", camera: true });
  }
  show("dev-batteries", packs.length > 0);
  rebuild("dev-battery-rows", packs, (p) => {
    const row = document.createElement("div");
    row.className = "setrow";
    const what = document.createElement("div");
    what.className = "what";
    const t = document.createElement("div");
    t.className = "t";
    t.textContent = p.label + (p.model ? "  ·  " + p.model : "");
    const b = document.createElement("div");
    b.className = "body";
    const state = p.camera ? "Below 10% a backup does not start; one running stops after the photo it is on"
      : ({ charging: "Charging", discharging: "In use", full: "Full", "not charging": "Not charging" }[p.status] || "Idle");
    b.textContent = state + (p.health ? ". Holds " + p.health + "% of what it held new." : ".");
    what.append(t, b);
    const level = document.createElement("div");
    level.className = "num-m";
    level.textContent = (p.level === null || p.level === undefined ? "–" : p.level + "%");
    row.append(what, level);
    return row;
  });
}

// Batteries in the top bar: one upright icon each, filled to its level, with no numbers.
// Green above 30%, amber from 30% to 20%, red below 20%, flashing at 5% or below. The
// numbers are a tap away, in Settings, Device, Batteries. "Swap" shows when one should go.
function battClass(level) {
  if (level === null || level === undefined) return "b-unknown";
  if (level <= 5) return "b-empty";
  if (level < 20) return "b-low";
  if (level <= 30) return "b-warn";
  return "b-ok";
}
function battIcon(level, charging) {
  const h = level === null || level === undefined ? 0 : Math.max(1.2, 14 * Math.min(100, level) / 100);
  const bolt = charging ? '<path class="bolt" d="M6.8 8.5l-2 3.6h2.4l-2 3.6"/>' : "";
  return '<svg class="batt" viewBox="0 0 12 24"><rect class="cap" x="4" y="1" width="4" height="2" rx=".6"/>' +
         '<rect class="shell" x="1.5" y="3" width="9" height="19" rx="1.6"/>' +
         '<rect class="fill" x="3" y="' + (20.5 - h).toFixed(2) + '" width="6" height="' + h.toFixed(2) + '" rx=".6"/>' +
         bolt + "</svg>";
}
function drawBatts(id, packs, swap, what) {
  const box = $(id);
  const key = JSON.stringify([packs.map((p) => [p.level, p.status]), swap]);
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  const parts = packs.map((p) => {
    const span = document.createElement("span");
    span.className = "batt-wrap " + battClass(p.level);
    span.innerHTML = battIcon(p.level, p.status === "charging");
    return span;
  });
  if (swap) {
    const note = document.createElement("span");
    note.className = "batt-swap";
    note.textContent = "Swap";
    parts.push(note);
  }
  box.replaceChildren(...parts);
  const said = packs.map((p) => (packs.length > 1 ? p.label + " " : "") + (p.level === null || p.level === undefined ? "unknown" : p.level + "%"));
  box.parentElement.setAttribute("aria-label", what + ": " + said.join(", ") + (swap ? ". Swap " + swap.toLowerCase() + "." : ""));
  box.parentElement.title = box.parentElement.getAttribute("aria-label");
}

// Battery colours: amber at 30% or below, red at 20%. The word "low" is added at 20%,
// so the warning does not rest on colour alone.
// A camera that plugs in as a drive (the Ace Pro 2) does not report its battery over USB,
// and a camera's card in a reader has no battery at all.
function batteryShown(c) {
  if (c.battery) return c.battery;
  return String(c.serial || "").startsWith("drive:") ? "Not reported" : "–";
}
// The battery box: a real reading in large figures; "Not reported" small and quiet; and no
// box at all for a card in a reader.
function setBattery(id, c) {
  const el = $(id);
  const card = String(c.serial || "").startsWith("card:");
  el.parentElement.hidden = card;
  const text = batteryShown(c);
  el.classList.toggle("num-m", !!c.battery);
  el.classList.toggle("batt-none", !c.battery);
  setText(id, text);
}
function batteryPercent(value) {
  const m = /(\d{1,3})/.exec(String(value === undefined || value === null ? "" : value));
  return m ? parseInt(m[1], 10) : null;
}
function batteryClass(id, value) {
  const level = batteryPercent(value);
  $(id).classList.toggle("batt-warn", level !== null && level <= 30 && level > 20);
  $(id).classList.toggle("batt-low", level !== null && level <= 20);
}
function batteryWord(value) {
  const level = batteryPercent(value);
  return level !== null && level <= 20 ? " low" : "";
}

// ----- network icons -----

function wifiIcon(level, off) {
  // Three arcs and a dot. Arcs above the signal level are drawn faint.
  const arcs = ["M8.5 15a5 5 0 0 1 7 0", "M5.5 12a9.2 9.2 0 0 1 13 0", "M2.5 9a13.5 13.5 0 0 1 19 0"];
  let html = '<circle cx="12" cy="18.5" r="1.2" fill="currentColor"/>';
  arcs.forEach((d, i) => { html += '<path d="' + d + '"' + (off || i >= level ? ' class="dim"' : "") + "/>"; });
  if (off) html += '<path d="M3 3l18 18"/>';
  return html;
}
const HOTSPOT_ICON = '<circle cx="12" cy="12" r="2"/><path d="M8.5 8.5a5 5 0 0 0 0 7M15.5 8.5a5 5 0 0 1 0 7M5.5 5.5a9.5 9.5 0 0 0 0 13M18.5 5.5a9.5 9.5 0 0 1 0 13"/>';
const LOCK_ICON = '<svg class="lock" viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="10" rx="1"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>';

function signalLevel(signal) { return signal >= 70 ? 3 : signal >= 45 ? 2 : signal > 0 ? 1 : 0; }

// The network-cable icon, and the FSDC (Field Station Direct Connect) icon: a laptop with a spark on its screen.
const WIRED_ICON = '<rect x="9" y="3" width="6" height="5" rx="1"/><rect x="2" y="16" width="6" height="5" rx="1"/>' +
  '<rect x="16" y="16" width="6" height="5" rx="1"/><path d="M12 8v4M5 16v-4h14v4"/>';
const LINK_ICON = '<rect x="3.5" y="4" width="17" height="11.5" rx="1.5"/><path d="M1.5 19.5h21"/>' +
  '<path d="M13 6.5l-3 4.25h4l-3 4.25" fill="none"/>';

function renderNetBar() {
  const net = S.net || {};
  show("bar-wired", !!(net.available && (net.wired || net.direct || net.direct_waiting)));
  const linkMode = net.direct ? "link" : net.wired ? "wired" : "linking";
  setText("bar-wired-text", linkMode === "link" ? "FSDC" : linkMode === "wired" ? "Wired" : "Linking…");
  const wiredSvg = $("bar-wired-icon");
  const wiredIcon = linkMode === "wired" ? WIRED_ICON : LINK_ICON;
  if (wiredSvg.dataset.icon !== wiredIcon) { wiredSvg.innerHTML = wiredIcon; wiredSvg.dataset.icon = wiredIcon; }
  $("bar-wired").classList.toggle("linking", linkMode === "linking");
  $("bar-wired").classList.toggle("linked", linkMode === "link");
  show("bar-wifi", !!net.available);
  if (!net.available) return;
  let icon, text;
  if (S.net_job && S.net_job.busy) { icon = wifiIcon(0, false); text = "Working"; }
  else if (net.hotspot) { icon = HOTSPOT_ICON; text = "Hotspot"; }
  else if (net.ssid) { icon = wifiIcon(signalLevel(net.signal), false); text = net.ssid.length > 16 ? net.ssid.slice(0, 15) + "…" : net.ssid; }
  else { icon = wifiIcon(0, true); text = "No Wi-Fi"; }
  const svg = $("bar-wifi-icon");
  if (svg.dataset.icon !== icon) { svg.innerHTML = icon; svg.dataset.icon = icon; }
  setText("bar-wifi-text", text);
}


function renderHome() {
  const c = S.camera;
  let mode = "absent";
  if (c.state === "scanning") mode = "scanning";
  else if (c.state === "error") mode = "error";
  else if (c.state === "ready") mode = c.new > 0 ? "ready" : "none";
  for (const name of ["absent", "scanning", "ready", "none", "error"]) show("cam-" + name, name === mode);
  $("cam").classList.toggle("is-new", mode === "ready");
  if (mode === "scanning") {
    setName("cam-scanning-text", (c.model ? c.model + ". " : "") +
      (c.listed ? "Reading the cards: " + n(c.listed) + " files so far." : "Opening the camera."));
  }
  if (mode === "ready") {
    setName("cam-ready-sub", (c.model || "Camera") + " connected by USB");
    setText("cam-new", n(c.new));
    setText("cam-bytes", bytes(c.new_bytes));
    show("cam-batt-box", !!c.battery);
    setText("cam-batt", c.battery);
  }
  if (mode === "none") {
    setText("cam-none-text", c.photos
      ? "All " + n(c.photos) + " photos on the camera are already on the tablet."
        + (c.skipped && c.skipped.count ? " " + skippedText(c.skipped, c.copy_what, c.photos) : "")
      : skippedText(c.skipped, c.copy_what, 0) || "There are no photos on the camera's cards.");
  }
  if (mode === "error") setText("cam-error-text", c.message);

  const f = formatInfo();
  $("fmt").classList.toggle("safe", f.kind === "safe");
  $("fmt").classList.toggle("no", f.kind === "no");
  setText("fmt-title", f.title);
  const fmtText = f.text + (f.kind === "safe" ? " Format them in the camera." : "");
  setText("fmt-text", fmtText);
  $("fmt").classList.toggle("long", fmtText.length > 120);
  show("fmt-counts", f.kind === "no");
  if (S.format.known) {
    setText("fmt-photos", n(S.format.photos));
    setText("fmt-tablet", n(S.format.on_tablet));
    setText("fmt-second", n(S.format.on_second));
  }

  const s = secondInfo();
  setText("sec-title", s.title);
  setText("sec-text", s.text);
  show("sec-bar", s.bar !== null);
  if (s.bar !== null) $("sec-fill").style.width = s.bar.toFixed(1) + "%";
  setText("sec-card", s.file ? middle(s.file, 18) : s.line);
  $("sec-card").classList.toggle("mono", !!s.file);
  show("btn-sec", !!s.button);
  setText("btn-sec", s.button);
  $("btn-sec").dataset.action = s.action;

  setText("lib-title", n(S.shoot_count) + plural(S.shoot_count, " shoot", " shoots"));
  rebuild("lib-list", S.shoots.slice(0, 3), (shoot) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "shoot";
    row.addEventListener("click", (event) => { event.stopPropagation(); openShoot(shoot.id); });
    const pic = document.createElement("div");
    pic.className = "pic";
    if (shoot.cover) pic.append(picture(shoot.cover));
    row.append(pic);
    // The full folder name, and the number of photos under it. A longer name from before
    // 1.0.2c is shortened in the middle, so the number that tells imports apart still shows.
    const words = document.createElement("div");
    words.className = "words";
    const name = document.createElement("div");
    name.className = "mono";
    name.textContent = middle(leaf(shoot.folder), 19);
    name.title = leaf(shoot.folder);
    const count = document.createElement("div");
    count.className = "n";
    count.textContent = n(shoot.files) + plural(shoot.files, " photo", " photos");
    words.append(name, count);
    const badge = document.createElement("div");
    if (shoot.files > 0 && shoot.mirrored >= shoot.files) { badge.className = "badge green"; badge.textContent = "2 copies"; }
    else if (shoot.mirrored > 0) { badge.className = "badge"; badge.textContent = n(shoot.mirrored) + " on 2nd"; }
    else { badge.className = "badge orange"; badge.textContent = "1 copy"; }
    row.append(words, badge);
    return row;
  });

  renderNasTile();
  setText("foot-tablet", space(S.tablet.free, S.tablet.total));
  // Copy 2: the microSD card, or the USB drive when the settings say so; greyed while it is out.
  const usb = S.card.kind === "usb";
  const in2 = S.card.state === "ready";
  setText("foot-card", in2 ? space(S.card.free, S.card.total) : usb ? "No drive" : "No card");

  $("foot-copy2").classList.toggle("off", !in2);
  renderVersion($("foot-version"), S.version);
  setText("foot-problem", S.problem);
}

// ----- the NAS tile -----

// Where to point a computer: the share by name and by address, and SFTP when it is on.
function nasAddress() {
  const n = S.nas || {};
  const open = (n.addresses || []).filter((a) => a.open);
  const best = open.find((a) => a.kind === "direct") || open.find((a) => a.kind === "cable") || open[0];
  if (!n.host || !best) return "";
  const lines = [];
  if (n.nas_on) lines.push("smb://" + n.host + ".local   " + best.address);
  if (n.sftp_on && n.user) lines.push("sftp://" + n.user + "@" + best.address + ":" + n.port);
  return lines.join("\n");
}

// The first network the tablet is on where the settings keep the share closed, if the share is on.
function nasHidden() {
  const n = S.nas || {};
  if (!n.installed || !(n.nas_on || n.sftp_on)) return null;
  return (n.hidden_on || [])[0] || null;
}

function hiddenText(h) {
  const where = h.kind === "cable" ? "the cable" : h.kind === "direct" ? "FSDC" : h.kind === "hotspot" ? "the hotspot" : "'" + h.network + "'";
  if (h.kind === "wifi" && !h.secured) return "computers cannot see it on " + where + ": it has no password, so it cannot be trusted. Use the cable or the hotspot.";
  return "computers cannot see it on " + where + ", which is not trusted.";
}

async function trustHidden(h) {
  if (h.kind === "cable" || h.kind === "direct") return trust({ nas_cable: true });
  if (h.kind === "hotspot") return trust({ nas_hotspot: true });
  if (!prefs) await loadSettings();
  if (!prefs) return;
  const list = prefs.nas_trusted.filter((v) => v !== h.network);
  list.push(h.network);
  return trust({ nas_trusted: list });
}

function trustLabel(h) {
  return h.kind === "wifi" ? "Trust " + h.network : (h.kind === "cable" || h.kind === "direct") ? "Allow on the cable" : "Allow on the hotspot";
}

function canTrust(h) {
  return !!h && (h.kind !== "wifi" || h.secured);
}

let nasFix = null;

function renderNasTile() {
  const n = S.nas || {};
  const job = S.nas_job || {};
  let title, text = "", foot = "";
  const on = n.nas_on || n.sftp_on;
  const people = n.sessions || [];
  const hidden = job.busy ? null : nasHidden();
  if (!n.installed) { title = "Not set up"; text = "Run doubletake setup in a terminal to add the NAS. Settings, NAS, explains."; }
  else if (job.busy) { title = "Working"; text = job.message; }
  else if (!on) { title = "Off"; text = "Switch it on in Settings, NAS, to open the photos from a computer."; }
  else if (hidden && !people.length) { title = "Hidden"; text = "On, but " + hiddenText(hidden); }
  else if (!people.length && !n.fresh) { title = "On"; text = "Cannot tell who is connected yet. If this stays, run doubletake setup in a terminal."; foot = nasAddress(); }
  else if (!people.length) { title = "Waiting"; text = "On. Nobody is connected."; foot = nasAddress(); }
  else {
    title = people.length === 1 ? "1 computer" : people.length + " computers";
    const file = (n.open || [])[0];
    text = file ? "Reading " + file + (n.speed > 100000 ? ", " + rate(n.speed) : "") + "." : "Connected.";
    foot = nasAddress();
  }
  setText("nas-title", title);
  setText("nas-text", text);
  setText("nas-foot", foot);
  $("nas-tile").classList.toggle("is-warn", !!hidden && !people.length);
  show("nas-fix", canTrust(hidden) && !people.length);
  if (hidden) setText("nas-fix", trustLabel(hidden));
  nasFix = hidden;
  rebuild("nas-who", people.slice(0, 3), (p) => {
    const row = document.createElement("div");
    row.className = "who";
    const dot = document.createElement("div"); dot.className = "dot";
    const name = document.createElement("div"); name.className = "mono";
    name.textContent = (p.machine || p.address) + (p.kind === "sftp" ? "  (SFTP)" : p.kind === "both" ? "  (share + SFTP)" : "") + (p.user ? "  " + p.user : "");
    row.append(dot, name);
    return row;
  });
}

function rebuild(id, items, make) {
  const key = JSON.stringify(items);
  if (lists[id] === key) return;
  lists[id] = key;
  const box = $(id);
  box.replaceChildren(...items.map(make));
}

function cardRow(prefix, afterText) {
  const k = S.card;
  const s = secondInfo();
  const row = $(prefix + "-card-name").closest(".dest");
  row.classList.toggle("warn", !s.ok);
  if (s.ok) {
    setText(prefix + "-card-name", "Copy 2");
    // A drive prepared by DoubleTake is always DT-COPY2, so its name is only shown when it is something else.
    const named = k.label && k.label !== "DT-COPY2" ? "'" + k.label + "', " : "";
    setText(prefix + "-card-text", named + space(k.free, k.total) + (afterText ? ". " + afterText : ""));
  } else {
    setText(prefix + "-card-name", "No second copy");
    setText(prefix + "-card-text", "This backup will exist only on the tablet. " + s.text);
  }
  return s;
}

function renderReady() {
  const c = S.camera;
  setName("rdy-model", c.model || "Camera", true);
  setText("rdy-serial", c.serial && c.serial.indexOf(":") < 0 ? "Serial " + c.serial : "");
  setBattery("rdy-batt", c);
  setText("rdy-photos", n(c.photos));
  setText("rdy-already", n(c.already));
  setText("rdy-new", n(c.new));
  setText("rdy-bytes", bytes(c.new_bytes));
  setText("rdy-dupes", c.duplicates
    ? n(c.found) + " files on the cards. The same photos are on both cards, so each one is copied once."
    : "");
  setText("rdy-skipped", skippedText(c.skipped, c.copy_what, c.photos));
  show("rdy-limit", !!S.limit);
  setText("rdy-limit", S.limit ? "Testing: this backup copies at most " + n(S.limit) + " photos." : "");
  setText("rdy-storage", (c.storage || []).join("\n"));
  $("rdy-storage").style.whiteSpace = "pre-line";

  const room = c.new_bytes + 2e9 <= S.tablet.free;
  setText("rdy-tablet", space(S.tablet.free, S.tablet.total) + (room ? "" : ". Not enough room for this backup."));
  const s = cardRow("rdy", "");
  setText("rdy-card-badge", s.ok ? "Second" : "");
  $("rdy-card-badge").className = "badge ink";

  const start = $("btn-start");
  start.disabled = !(c.new > 0 && room) || !!S.power_problem;
  show("rdy-power", !!S.power_problem);
  setText("rdy-power-text", S.power_problem || "");
  setText("btn-start-text", c.new > 0 ? "Start backup" : "Nothing new to copy");
}

// The folder the backup goes in. Every import is named the same way: 2026_1015_import_01.
async function refreshFolder() {
  try {
    const reply = await fetch("/api/folder?name=", { cache: "no-store" });
    setText("rdy-folder", (await reply.json()).folder);
  } catch (err) { /* the next poll reports a lost engine */ }
}

function renderCopy() {
  const b = S.backup;
  const c = S.camera;
  const reading = b.phase === "reading";
  setName("cp-model", c.model || "Camera", true);
  setText("cp-sub", reading
    ? (b.listed ? "Reading the cards: " + n(b.listed) + " files so far." : "Opening the camera.")
    : "USB connected. " + n(b.total_files) + " new photos.");
  setBattery("cp-batt", c);
  const unit = unitFor(b.total_bytes);
  setText("cp-files", n(b.done_files));
  setText("cp-files-of", " / " + (reading ? "–" : n(b.total_files)));
  setText("cp-data", inUnit(b.done_bytes, unit));
  setText("cp-data-of", " / " + (reading ? "–" : inUnit(b.total_bytes, unit) + " " + UNITS[unit]));
  setText("cp-elapsed", clock(b.elapsed));
  setText("cp-left", b.remaining > 0 ? clock(b.remaining) : "--:--:--");
  setText("cp-file", b.current);
  const pct = percent(b);
  $("cp-fill").style.width = pct.toFixed(1) + "%";
  setText("cp-pct", pct.toFixed(1) + "% complete");
  $("cp-ring").style.strokeDashoffset = String(276.46 * (1 - Math.max(0, Math.min(100, pct)) / 100));
  setText("cp-ring-pct", Math.floor(pct) + "%");
  setText("cp-speed", reading ? "" : rate(b.speed_now) + " now, " + rate(b.speed_avg) + " average");
  setText("cp-verified", n(b.verified));
  setText("cp-failed", n(b.failed));
  setText("cp-folder", b.folder ? b.folder.replace(S.tablet.library + "/", "") : "…");
  setText("cp-tablet", space(S.tablet.free, S.tablet.total));
  setText("cp-tablet-badge", reading ? "Next" : "Writing");

  const running = S.mirror.state === "running";
  const s = cardRow("cp", running ? "" : "Starts by itself once a tenth of Copy 1 is done.");
  setText("cp-card-badge", s.ok ? (running ? "Copying" : "Next") : "");
  $("cp-card-badge").className = "badge" + (running ? " orange" : "");
  $("btn-stop").disabled = stopping;
}

function renderDone() {
  const b = S.backup;
  const banner = $("dn-banner");
  let kind = "ok";
  let title = "Backup complete";
  let sub = n(b.verified) + plural(b.verified, " photo", " photos") + " verified on the tablet.";
  let side = "You can unplug the camera. The second copy carries on by itself.";
  if (b.error) {
    kind = "stop"; title = "Backup did not run"; sub = b.error; side = "";
  } else if (b.nothing) {
    title = "Nothing new to copy"; sub = "Everything on the camera is already on the tablet."; side = "";
  } else if (b.interrupted) {
    kind = "warn"; title = "Backup interrupted";
    sub = b.reason + " " + n(b.verified) + " of " + n(b.total_files) + " photos are safely on the tablet.";
    side = "Continue to pick up where it stopped. Nothing is copied twice.";
  } else if (b.failed) {
    kind = "warn"; title = "Finished with problems";
    sub = n(b.failed) + plural(b.failed, " file", " files") + " failed the checks and " +
          plural(b.failed, "was", "were") + " not kept.";
    side = "Failed files are tried again on the next backup.";
  }
  banner.className = "banner" + (kind === "ok" ? "" : " " + kind);
  show("dn-icon-ok", kind === "ok");
  show("dn-icon-warn", kind !== "ok");
  setText("dn-title", title);
  setText("dn-sub", sub);
  setText("dn-side", side);

  setText("dn-files", n(b.verified));
  setText("dn-files-of", " / " + n(b.total_files));
  setText("dn-data", bytes(b.done_bytes));
  setText("dn-time", clock(b.elapsed));
  setText("dn-failed", n(b.failed));

  setText("dn-tablet", space(S.tablet.free, S.tablet.total));
  setText("dn-tablet-state", b.verified ? n(b.verified) + " verified" : "");

  const k = S.card;
  const m = S.mirror;
  const s = secondInfo();
  const state = $("dn-card-state");
  setText("dn-card-name", s.ok ? "Copy 2" : "No second copy");
  show("dn-card-bar", s.running);
  if (s.running) {
    $("dn-card-fill").style.width = s.bar.toFixed(1) + "%";
    const left = about(m.remaining);
    setText("dn-card-text", "Copying in the background: " + n(m.done_files) + " of " + n(m.total_files) +
            (left ? ", " + left + " left" : ""));
    setText("dn-card-state", s.bar.toFixed(0) + "%");
    state.className = "state";
  } else if (s.ok && k.waiting === 0 && m.state !== "error") {
    setText("dn-card-text", s.line);
    setText("dn-card-state", k.held ? "All " + n(k.held) + " verified" : "");
    state.className = "state green";
  } else {
    setText("dn-card-text", s.text);
    setText("dn-card-state", s.ok ? s.title : "1 copy only");
    state.className = "state orange";
  }

  const f = formatInfo();
  setText("dn-format-text", f.text);
  setText("dn-format-state", f.badge);
  $("dn-format-state").className = "badge " + (f.kind === "safe" ? "green" : "orange");

  const failures = (b.failures || []).slice(0, 2).map((x) => x.name + ": " + x.note).join("   ");
  setText("dn-fail-list", failures);
  show("btn-again", !!b.interrupted && S.camera.connected);
}

// A new card or drive: DoubleTake erases it and keeps it for itself, after three checks.
let preparing = false;   // the checks or the erase are under way: the pop-up steps aside
let driveInfo = null;    // the whole drive, as the engine describes it
let driveFor = "";

function renderModal() {
  const k = S.card;
  const open = k.state === "new" && !preparing;
  if (open && $("modal").hasAttribute("hidden")) show("modal-error", false);
  show("modal", open);
  if (!open) return;
  const usb = k.kind === "usb";
  setText("modal-title", usb ? "New USB drive" : "New card in the tablet");
  for (const el of document.querySelectorAll(".kw")) el.textContent = usb ? "drive" : "card";
  const key = k.mount + "|" + k.total;
  if (driveFor !== key) {
    driveFor = key;
    driveInfo = null;
    post("/api/card/details").then((d) => { if (!d.error) { driveInfo = d; renderModal(); } });
  }
  const d = driveInfo;
  setText("modal-card", d ? d.name + "   " + bytes(d.size) + (d.used ? ", " + bytes(d.used) + " used" : ", empty")
                          : "'" + k.label + "'   " + bytes(k.total));
}

function partitionsText(d) {
  if (!d || !d.partitions || !d.partitions.length) return "";
  return d.partitions.map((x) => (x.label ? "'" + x.label + "'" : "unnamed") + " (" + (x.fstype || "unknown") + ", " +
    bytes(x.size) + (x.used ? ", " + bytes(x.used) + " used" : "") + ")").join(", ");
}

async function prepareDrive() {
  const k = S.card;
  if (k.noun === "folder") {  // a folder set by hand is never erased: taken as it is
    const reply = await post("/api/card/use");
    if (reply.error) { setText("modal-error", reply.error); show("modal-error", true); }
    return;
  }
  const d = driveInfo || {};
  const what = d.name || (k.kind === "usb" ? "the USB drive" : "the card");
  const size = bytes(d.size || k.total);
  preparing = true;
  render();
  try {
    // Check 2: everything on it goes.
    const parts = partitionsText(d);
    if (!await ask("Erase " + what + "?",
        "Everything on " + what + " (" + size + ") will be erased" + (parts ? ": " + parts : "") +
        ". It cannot be brought back. Afterwards the drive holds DoubleTake's second copy, and nothing else.",
        "Erase it")) return;
    // Check 3: for DoubleTake alone, then type AGREE.
    if (!await ask("Keep it for DoubleTake",
        "From now on DoubleTake treats " + what + " as its own: every backup's second copy goes on it, and " +
        "it is not for other files. On the next screen, type AGREE to erase it.", "Type AGREE")) return;
    const agree = await new Promise((resolve) => openKeyboard({
      label: "Type AGREE to erase " + what, hint: "Capital letters or not. Anything else, and nothing is erased.",
      value: "", max: 10, done: (v) => resolve(v), cancel: () => resolve(null) }));
    if (agree === null) return;
    if (agree.trim().toUpperCase() !== "AGREE") {
      await ask("Not erased", "AGREE was not typed, so " + what + " was left as it is.", "OK");
      return;
    }
    setText("confirm-title", "Preparing " + what);
    const reply = await guardedPost("/api/card/prepare", { agree: agree }, "Erase " + what);
    if (reply.cancelled) return;
    if (reply.error) await ask("Not prepared", reply.error, "OK");
    else await ask("Ready", what + " is now DoubleTake's Copy 2. The second copy starts by itself.", "OK");
  } finally {
    preparing = false;
    driveFor = "";
    render();
  }
}

// ----- buttons ----------------------------------------------------------------------

$("cam-ready").addEventListener("click", () => { view = "ready"; render(); });
$("back").addEventListener("click", () => go(BACK[shown] || "home"));
$("btn-scan").addEventListener("click", () => post("/api/camera/scan"));
$("btn-rescan").addEventListener("click", () => post("/api/camera/scan"));
$("btn-retry").addEventListener("click", () => post("/api/camera/scan"));
$("btn-sec").addEventListener("click", (event) => {
  const action = event.currentTarget.dataset.action;
  if (action) post(action);
});
$("btn-start").addEventListener("click", async () => {
  const reply = await post("/api/backup/start", { name: "" });
  if (reply.error) { show("rdy-power", true); setText("rdy-power-text", reply.error); return; }
  view = "home";
});
$("btn-stop").addEventListener("click", () => { stopping = true; $("btn-stop").disabled = true; post("/api/backup/stop"); });
$("btn-again").addEventListener("click", async () => {
  const reply = await post("/api/backup/start", { name: S.backup ? S.backup.name : "" });
  if (reply.error) { setText("done-power", reply.error); show("done-power", true); }
});
$("btn-done").addEventListener("click", () => { view = "home"; post("/api/backup/dismiss"); });
$("btn-card-yes").addEventListener("click", () => prepareDrive());
$("btn-card-no").addEventListener("click", () => post("/api/card/ignore"));
$("lib").addEventListener("click", () => go("library"));
$("btn-settings").addEventListener("click", () => go("settings"));
$("auto-cancel").addEventListener("click", () => post("/api/auto/cancel"));
$("auto-now").addEventListener("click", async () => {
  const a = S.auto;
  await post("/api/auto/cancel");
  if (a && a.state === "countdown") {
    const reply = await post("/api/backup/start", { name: a.name });
    if (reply.error) ask("Not started", reply.error, "OK");
  }
});
for (const id of ["bar-packs", "bar-camera"]) {
  $(id).addEventListener("click", () => {
    section = "device";
    go("settings");
    setTimeout(() => { const card = $("dev-batteries"); if (card) card.scrollIntoView({ block: "start" }); }, 300);
  });
}
$("dev-exit").addEventListener("click", async () => {
  const running = [];
  if (S.backup && S.backup.running) running.push("A backup from the camera is running.");
  if (S.mirror && S.mirror.state === "running") running.push("The second copy is running.");
  const n = S.nas || {};
  if (n.nas_on || n.sftp_on) running.push((n.sessions || []).length ? "A computer is connected to the NAS. The NAS keeps running." : "The NAS keeps running after this.");
  const text = "Close the screens and go to the desktop? " + (running.length ? running.join(" ") + " " : "") +
               "Anything part-way is picked up again next time.";
  if (!await ask("Exit to the desktop", text, "Exit")) return;
  const check = await guardedPost("/api/quit", {}, "Exit");
  if (check.cancelled || check.error) { if (check.error) await ask("Exit", check.error, "OK"); return; }
  stopped = true;
  setText("gone-title", "Stopped");
  setText("gone-text", "DoubleTake has stopped. Anything part-way is picked up again next time. " +
          "You can close this window. Start it again with: doubletake gui");
  show("gone", true);
});

// ===== the shoot library, photos and settings ===========================================

let lib = null;        // the library as last loaded
let libAt = 0;         // when it was loaded
let shoot = null;      // the shoot being browsed, with its photos
let vw = { index: 0, info: null, full: false, crop: null, fx: 0.5, fy: 0.5, x: 0, y: 0, token: 0, scale: 1, tapTimer: 0 };
let prefs = null;      // the settings as last loaded
let section = "display";

function go(next) { view = next; render(); }

async function getJSON(path) {
  const reply = await fetch(path, { cache: "no-store" });
  if (!reply.ok) throw new Error(String(reply.status));
  return reply.json();
}

function ask(title, text, yes) {
  return new Promise((resolve) => {
    setText("confirm-title", title);
    setText("confirm-text", text);
    setText("confirm-yes", yes);
    show("confirm", true);
    const done = (answer) => {
      show("confirm", false);
      $("confirm-yes").onclick = null;
      $("confirm-no").onclick = null;
      resolve(answer);
    };
    $("confirm-yes").onclick = () => done(true);
    $("confirm-no").onclick = () => done(false);
  });
}

// ----- library -----

async function loadLibrary() {
  try {
    lib = await getJSON("/api/library");
    libAt = Date.now();
    renderLibrary();
  } catch (err) { /* the next poll reports a lost engine */ }
}
function pollLibrary() { if (Date.now() - libAt > 3000) loadLibrary(); }

const TICK = '<svg viewBox="0 0 24 24"><path d="M5 12l5 5 9-10"/></svg>';
const RING = '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/></svg>';

function stateCell(kind, label) {
  const cell = document.createElement("div");
  cell.className = "lib-state " + kind;
  cell.innerHTML = kind === "ok" ? TICK : RING;
  const span = document.createElement("span");
  span.textContent = label;
  cell.append(span);
  return cell;
}

function renderLibrary() {
  if (!lib) return;
  const t = lib.totals;
  setText("lib-summary", n(t.shoots) + plural(t.shoots, " shoot, ", " shoots, ") + n(t.photos) + " photos, " +
          bytes(t.bytes) + " on this tablet. " + bytes(lib.free) + " free.");
  for (const id of [...chosen]) if (!lib.shoots.some((s) => s.id === id && s.deletable)) chosen.delete(id);
  renderDeleteButton();
  rebuild("lib-rows", lib.shoots.map((s) => Object.assign({}, s, { picked: chosen.has(s.id) })), (s) => {
    const row = document.createElement("div");
    row.className = "card lib-row lib-cols" + (s.cleared ? " cleared" : "");
    const name = document.createElement("button");
    name.type = "button";
    name.className = "lib-name";
    const pic = document.createElement("div");
    pic.className = "lib-pic";
    if (s.cover) pic.append(picture(s.cover));
    const text = document.createElement("div");
    text.className = "lib-text";
    const title = document.createElement("div");
    title.className = "mono";
    title.textContent = leaf(s.folder);
    const sub = document.createElement("div");
    sub.className = "body";
    sub.textContent = n(s.files) + " photos, " + bytes(s.bytes) + (s.failed ? ", " + n(s.failed) + " failed" : "");
    text.append(title, sub);
    if (!s.cleared) {
      const link = document.createElement("div");
      link.className = "view-link";
      link.innerHTML = 'View photos <svg viewBox="0 0 24 24"><path d="M9 5l7 7-7 7"/></svg>';
      text.append(link);
      name.addEventListener("click", () => openShoot(s.id));
    }
    name.append(pic, text);

    const tablet = s.cleared ? stateCell("none", "Deleted") : stateCell("ok", "Verified");
    let card;
    if (s.cleared) card = stateCell("none", "Deleted");
    else if (s.files && s.mirrored >= s.files) card = stateCell("ok", "Verified");
    else if (s.mirrored) card = stateCell("part", n(s.mirrored) + " of " + n(s.files));
    else card = stateCell("none", "No copy yet");

    const lr = document.createElement("div");
    lr.className = "lib-lr";
    if (s.cleared) {
      lr.append(document.createElement("div"));
    } else if (s.deletable) {
      const pick = document.createElement("button");
      pick.type = "button";
      pick.className = "lib-pick" + (s.picked ? " on" : "");
      pick.setAttribute("aria-pressed", s.picked ? "true" : "false");
      pick.innerHTML = (s.picked ? TICK : "") + "<span>" + (s.picked ? "Chosen" : "Choose") + "</span>";
      pick.addEventListener("click", () => {
        if (chosen.has(s.id)) chosen.delete(s.id); else chosen.add(s.id);
        renderLibrary();
      });
      lr.append(pick);
    } else {
      const when = document.createElement("div");
      when.className = "small";
      when.textContent = "Kept until " + s.deletable_from + ", a day after copying.";
      lr.append(when);
    }
    row.append(name, tablet, card, lr);
    return row;
  });
  if (!lib.shoots.length) {
    const empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "No shoots yet. Back up the camera to start.";
    $("lib-rows").replaceChildren(empty);
    lists["lib-rows"] = "";
  }
}

// Shoots chosen for deleting, by id. Kept while the list is redrawn.
const chosen = new Set();

function renderDeleteButton() {
  const picked = lib ? lib.shoots.filter((s) => chosen.has(s.id)) : [];
  const size = picked.reduce((t, s) => t + s.bytes, 0);
  $("btn-delete").disabled = !picked.length;
  setText("btn-delete", picked.length ? "Delete " + n(picked.length) + plural(picked.length, " shoot", " shoots") + " (" + bytes(size) + ")" : "Delete");
}

$("btn-delete").addEventListener("click", async () => {
  const picked = lib ? lib.shoots.filter((s) => chosen.has(s.id)) : [];
  if (!picked.length) return;
  const photos = picked.reduce((t, s) => t + s.files, 0), size = picked.reduce((t, s) => t + s.bytes, 0);
  const what = picked.length === 1 ? leaf(picked[0].folder) : n(picked.length) + " shoots";
  const yes = await ask("Delete " + what,
    "Delete " + what + " (" + n(photos) + " photos, " + bytes(size) + ") from Copy 1 and Copy 2? Make sure " +
    (picked.length === 1 ? "it is" : "they are") + " on your editing workstation first: this removes both of " +
    "DoubleTake's copies and cannot be undone.", "Delete");
  if (!yes) return;
  const reply = await guardedPost("/api/shoot/delete", { ids: picked.map((s) => s.id) }, "Delete");
  if (reply.cancelled) return;
  chosen.clear();
  if (reply.error) await ask("Not everything was deleted", reply.error, "OK");
  loadLibrary();
});

// ----- photos of one shoot -----

function picture(fileId) {
  const img = document.createElement("img");
  img.loading = "lazy";
  img.decoding = "async";
  img.alt = "";
  img.draggable = false;
  img.src = "/photo/" + fileId + "/thumb.jpg";
  img.addEventListener("error", () => img.remove());
  return img;
}

function frameNumber(name) {
  const digits = (name.match(/(\d+)(?!.*\d)/) || [""])[0];
  return digits || name;
}

function thumbFor(file, index, onClick) {
  const box = document.createElement("button");
  box.type = "button";
  box.className = "thumb";
  const img = document.createElement("img");
  img.loading = "lazy";
  img.decoding = "async";
  img.alt = "";
  img.src = "/photo/" + file.id + "/thumb.jpg";
  img.addEventListener("error", () => { img.remove(); });
  const label = document.createElement("div");
  label.className = "num";
  label.textContent = frameNumber(file.name);
  box.append(img, label);
  if (file.edited || file.exported) {
    const badge = document.createElement("div");
    badge.className = "badge-e" + (file.exported ? " done" : "");
    badge.textContent = file.exported ? "Exported" : "Edited";
    box.append(badge);
  }
  box.addEventListener("click", () => onClick(index));
  return box;
}

// How a shoot's photos are ordered. Capture time, oldest first, unless changed.
function sortKey() { return (prefs && prefs.photo_sort) || "taken_asc"; }

function sortFiles(files) {
  const [by, dir] = sortKey().split("_");
  const sign = dir === "desc" ? -1 : 1;
  const byName = (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true });
  return files.slice().sort((a, b) => sign * (by === "name" ? byName(a, b) : (a.taken - b.taken) || byName(a, b)));
}

function renderSortBar() {
  const [by, dir] = sortKey().split("_");
  for (const b of $("sort-by").children) b.classList.toggle("on", b.dataset.by === by);
  setText("sort-dir", by === "name" ? (dir === "asc" ? "A to Z" : "Z to A") : (dir === "asc" ? "Oldest first" : "Newest first"));
}

async function setSort(value) {
  if (!prefs) await loadSettings();
  if (prefs) prefs.photo_sort = value;
  post("/api/settings", { changes: { photo_sort: value } });
  if (shoot) {
    const current = shoot.files[vw.index];
    shoot.files = sortFiles(shoot.files);
    if (current) vw.index = shoot.files.indexOf(current);
    $("grid").replaceChildren(...shoot.files.map((f, i) => thumbFor(f, i, openPhoto)));
  }
  renderSortBar();
}

for (const b of $("sort-by").children) {
  b.addEventListener("click", () => setSort(b.dataset.by + "_" + sortKey().split("_")[1]));
}
$("sort-dir").addEventListener("click", () => {
  const [by, dir] = sortKey().split("_");
  setSort(by + "_" + (dir === "asc" ? "desc" : "asc"));
});

async function openShoot(id) {
  try {
    shoot = await getJSON("/api/shoot?id=" + id);
  } catch (err) { return; }
  if (!prefs) await loadSettings();
  shoot.files = sortFiles(shoot.files);
  renderSortBar();
  show("grid-exports", shoot.files.some((f) => f.exported));
  const grid = $("grid");
  grid.replaceChildren(...shoot.files.map((f, i) => thumbFor(f, i, openPhoto)));
  show("grid-note", !shoot.files.length || (lib && !lib.viewer));
  setText("grid-note", !shoot.files.length ? "This shoot has no photos on the tablet."
    : "Run doubletake setup in a terminal with the tablet online to add the image library. Until then there are no pictures.");
  $("grid-wrap").scrollTop = 0;
  go("grid");
}

function openPhoto(index) {
  vw.index = index;
  go("viewer");
  showPhoto();
}

function showPhoto() {
  if (!shoot || !shoot.files.length) return;
  vw.index = Math.max(0, Math.min(vw.index, shoot.files.length - 1));
  const file = shoot.files[vw.index];
  const token = ++vw.token;
  leaveFull();
  setText("vw-name", file.name);
  setText("vw-time", new Date(file.taken * 1000).toTimeString().slice(0, 8));
  $("vw-exif").replaceChildren();
  show("vw-info", false);
  show("vw-note", false);
  const fit = $("vw-fit");
  fit.hidden = false;
  fit.onerror = () => {
    fit.hidden = true;
    show("vw-info", false);
    $("stage").classList.remove("matted");
    setText("vw-note", lib && !lib.viewer ? "Run doubletake setup in a terminal with the tablet online to add the image library."
                                            : "No preview stored in this file.");
    show("vw-note", true);
  };
  fit.src = "/photo/" + file.id + "/screen.jpg";
  const taken = new Date(file.taken * 1000);
  setText("vw-taken", taken.toTimeString().slice(0, 8));
  vw.info = null;
  getJSON("/api/photo?id=" + file.id).then((info) => {
    if (token !== vw.token) return;
    vw.info = info;
    if (info.taken && info.taken.length >= 19) {
      setText("vw-taken", info.taken.slice(11, 19));
      setText("vw-time", info.taken.slice(11, 19));
    }
    const parts = [info.shutter, info.aperture, info.iso, info.focal].filter(Boolean);
    $("vw-exif").replaceChildren(...parts.map((text) => { const el = document.createElement("span"); el.textContent = text; return el; }));
    placeInfo();
  }).catch(() => {});

  // The strip of nearby frames, ten at a time.
  const start = Math.max(0, Math.min(vw.index - 4, shoot.files.length - 10));
  const near = shoot.files.slice(start, start + 10);
  setText("vw-frames", "Frames " + n(start + 1) + " to " + n(start + near.length));
  $("vw-strip").replaceChildren(...near.map((f, i) => {
    const box = thumbFor(f, start + i, (k) => { vw.index = k; showPhoto(); });
    box.classList.toggle("on", start + i === vw.index);
    return box;
  }));
  // Fetch the next picture early, so moving on is instant.
  const next = shoot.files[vw.index + 1];
  if (next) new Image().src = "/photo/" + next.id + "/screen.jpg";
  render();
}

function step(by) {
  if (!shoot) return;
  const target = vw.index + by;
  if (target < 0 || target >= shoot.files.length) return;
  vw.index = target;
  showPhoto();
}

// ----- 100% focus check -----

function stageBox() { return $("stage").getBoundingClientRect(); }

function leaveFull() {
  vw.full = false;
  vw.crop = null;
  show("vw-full", false);
  show("vw-mode", false);
  if ($("vw-fit").src) { $("vw-fit").hidden = false; placeFit(); }
  markZoom(0);
  placeInfo();
}

function markZoom(level) {
  $("vw-fitbtn").classList.toggle("on", !level);
  for (const b of document.querySelectorAll("[data-zoom]")) b.classList.toggle("on", Number(b.dataset.zoom) === level);
}

function placeFull() {
  const img = $("vw-full");
  const c = vw.crop;
  if (!c) return;
  const box = stageBox();
  const dpr = window.devicePixelRatio || 1;
  // Keep the picture covering the stage while it is dragged.
  const w = c.w * vw.scale / dpr, h = c.h * vw.scale / dpr;
  vw.x = w <= box.width ? (box.width - w) / 2 : Math.min(0, Math.max(box.width - w, vw.x));
  vw.y = h <= box.height ? (box.height - h) / 2 : Math.min(0, Math.max(box.height - h, vw.y));
  img.style.width = w + "px";
  img.style.height = h + "px";
  img.style.transform = "translate(" + vw.x + "px," + vw.y + "px)";
  placeInfo();
}

async function enterFull(fx, fy) {
  const info = vw.info;
  if (!info || !info.width) return;
  const file = shoot.files[vw.index];
  const token = vw.token;
  const box = stageBox();
  const dpr = window.devicePixelRatio || 1;
  const s = vw.scale;
  // Twice the stage in each direction, so there is room to drag before fetching more.
  // w and h are in the photo's own pixels; at 50% the same stage shows twice as many.
  const w = Math.min(info.width, Math.round(box.width * dpr * 2 / s), Math.round(4000 / s));
  const h = Math.min(info.height, Math.round(box.height * dpr * 2 / s), Math.round(3000 / s));
  const left = Math.floor(Math.min(Math.max(0, fx * info.width - w / 2), info.width - w));
  const top = Math.floor(Math.min(Math.max(0, fy * info.height - h / 2), info.height - h));
  vw.full = true;
  const label = Math.round(s * 100) + "%";
  markZoom(Math.round(s * 100));
  setText("vw-mode", label + "  loading");
  show("vw-mode", true);
  const img = new Image();
  img.src = "/photo/" + file.id + "/crop.jpg?x=" + fx.toFixed(4) + "&y=" + fy.toFixed(4) + "&w=" + w + "&h=" + h + "&s=" + s;
  try { await img.decode(); } catch (err) { setText("vw-mode", label + " not available"); return; }
  if (token !== vw.token || !vw.full) return;
  const shownImg = $("vw-full");
  shownImg.src = img.src;
  vw.crop = { left: left, top: top, w: w, h: h };
  // Put the chosen point in the middle of the stage.
  vw.x = box.width / 2 - (fx * info.width - left) * s / dpr;
  vw.y = box.height / 2 - (fy * info.height - top) * s / dpr;
  shownImg.hidden = false;
  $("vw-fit").hidden = true;
  $("stage").classList.remove("matted");
  // Say how big the stored preview is, since 100% is 100% of that.
  setText("vw-mode", label + " of " + n(info.width) + " x " + n(info.height));
  placeFull();
}

function centreFraction() {
  // Which point of the photo is in the middle of the stage right now.
  const info = vw.info, c = vw.crop, box = stageBox();
  const dpr = window.devicePixelRatio || 1;
  return {
    fx: (c.left + (box.width / 2 - vw.x) * dpr / vw.scale) / info.width,
    fy: (c.top + (box.height / 2 - vw.y) * dpr / vw.scale) / info.height,
  };
}

function fitFraction(clientX, clientY) {
  // Where a tap on the fitted picture lands on the photo, as fractions (the frame left out).
  const img = $("vw-fit"), box = img.getBoundingClientRect();
  if (!img.naturalWidth) return { fx: 0.5, fy: 0.5 };
  const pad = parseFloat(getComputedStyle(img).paddingLeft) || 0;
  const w = box.width - 2 * pad, h = box.height - 2 * pad;
  const x = clientX - box.left - pad, y = clientY - box.top - pad;
  return { fx: Math.min(1, Math.max(0, x / w)), fy: Math.min(1, Math.max(0, y / h)) };
}

// The fitted photo in a mat: the same width of frame on all four sides, hugging the photo,
// and the whole centred in the viewer with the screen's own colour around it.
function placeFit() {
  const img = $("vw-fit");
  if (!img.naturalWidth || img.hidden) return;
  const box = stageBox();
  if (!box.width || !box.height) return;
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  const frame = 1.25 * rem, room = 1.25 * rem;  // the frame, and the space around it
  const scale = Math.min((box.width - 2 * (frame + room)) / img.naturalWidth,
                         (box.height - 2 * (frame + room)) / img.naturalHeight);
  if (!(scale > 0)) return;
  const w = Math.round(img.naturalWidth * scale), h = Math.round(img.naturalHeight * scale);
  img.style.padding = frame + "px";
  img.style.width = w + "px";
  img.style.height = h + "px";
  img.style.left = Math.round((box.width - w) / 2 - frame) + "px";
  img.style.top = Math.round((box.height - h) / 2 - frame) + "px";
  $("stage").classList.add("matted");
  placeInfo();
}

// The photo's name, time and settings: in the photo's bottom-left corner when it is fitted,
// in the screen's bottom-left when zoomed in. One line, or two (broken at the dot) when one
// does not fit. A tap on the photo hides or shows it, for every photo, until tapped again.
function infoOn() { return !prefs || prefs.viewer_info !== false; }

function placeInfo() {
  const el = $("vw-info");
  const fit = $("vw-fit"), full = $("vw-full");
  const showing = vw.full ? !full.hidden : (!fit.hidden && fit.naturalWidth > 0 && !!fit.style.width);
  show("vw-info", showing);
  if (!showing) return;
  el.classList.toggle("off", !infoOn());
  el.querySelector(".vi-sep").hidden = !$("vw-exif").children.length;
  const box = stageBox();
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  const inset = rem;
  let left = inset, bottom = inset, room = box.width - 2 * inset;
  if (!vw.full) {
    const frame = parseFloat(fit.style.padding) || 0;
    const x = parseFloat(fit.style.left) + frame, y = parseFloat(fit.style.top) + frame;
    const w = parseFloat(fit.style.width), h = parseFloat(fit.style.height);
    left = x + inset;
    bottom = box.height - (y + h) + inset;
    room = w - 2 * inset;
  }
  el.style.left = Math.round(left) + "px";
  el.style.bottom = Math.round(bottom) + "px";
  el.classList.remove("two");
  if (el.scrollWidth > room) el.classList.add("two");
}

function toggleInfo() {
  const on = !infoOn();
  if (prefs) prefs.viewer_info = on;
  post("/api/settings", { changes: { viewer_info: on } });
  placeInfo();
}
$("vw-fit").addEventListener("load", placeFit);
if (window.ResizeObserver) new ResizeObserver(placeFit).observe($("stage"));

// One finger: swipe to change photo, double-tap to look at 100%, drag to look around at 100%.
let touch = null;
let lastTap = 0;
// Pictures must not be picked up and dragged like a file.
$("stage").addEventListener("dragstart", (event) => event.preventDefault());
$("stage").addEventListener("pointercancel", () => { touch = null; });
$("stage").addEventListener("pointerdown", (event) => {
  event.preventDefault();
  touch = { x: event.clientX, y: event.clientY, sx: vw.x, sy: vw.y, moved: false };
  $("stage").setPointerCapture(event.pointerId);
});
$("stage").addEventListener("pointermove", (event) => {
  if (!touch) return;
  const dx = event.clientX - touch.x, dy = event.clientY - touch.y;
  if (Math.abs(dx) + Math.abs(dy) > 8) touch.moved = true;
  if (vw.full && vw.crop) { vw.x = touch.sx + dx; vw.y = touch.sy + dy; placeFull(); }
});
$("stage").addEventListener("pointerup", (event) => {
  if (!touch) return;
  const dx = event.clientX - touch.x;
  const was = touch;
  touch = null;
  if (vw.full) {
    if (was.moved && vw.crop) {
      // Near the edge of what was fetched: fetch more around the new centre.
      const c = centreFraction();
      const info = vw.info, crop = vw.crop;
      const px = c.fx * info.width - crop.left, py = c.fy * info.height - crop.top;
      if (px < crop.w * 0.3 || px > crop.w * 0.7 || py < crop.h * 0.3 || py > crop.h * 0.7) enterFull(c.fx, c.fy);
    } else if (!was.moved && Date.now() - lastTap < 350) {
      clearTimeout(vw.tapTimer);
      lastTap = 0;
      leaveFull();
      return;
    } else if (!was.moved) {
      clearTimeout(vw.tapTimer);
      vw.tapTimer = setTimeout(toggleInfo, 350);
    }
    lastTap = was.moved ? 0 : Date.now();
    return;
  }
  if (was.moved && Math.abs(dx) > 60) { step(dx < 0 ? 1 : -1); return; }
  if (!was.moved) {
    if (Date.now() - lastTap < 350) {
      clearTimeout(vw.tapTimer);
      const f = fitFraction(event.clientX, event.clientY);
      vw.fx = f.fx; vw.fy = f.fy;
      lastTap = 0;
      vw.scale = ((prefs && prefs.viewer_zoom) || 100) / 100;
      enterFull(f.fx, f.fy);
    } else {
      lastTap = Date.now();
      clearTimeout(vw.tapTimer);
      vw.tapTimer = setTimeout(toggleInfo, 350);
    }
  }
});

$("vw-prev").addEventListener("click", () => step(-1));
$("vw-next").addEventListener("click", () => step(1));
$("vw-fitbtn").addEventListener("click", () => leaveFull());
for (const b of document.querySelectorAll("[data-zoom]")) {
  b.addEventListener("click", () => {
    const level = Number(b.dataset.zoom);
    // Keep looking at the same spot when changing level.
    if (vw.full && vw.crop) { const c = centreFraction(); vw.fx = c.fx; vw.fy = c.fy; }
    vw.scale = level / 100;
    if (prefs) prefs.viewer_zoom = level;
    post("/api/settings", { changes: { viewer_zoom: level } });
    enterFull(vw.fx, vw.fy);
  });
}
$("vw-all").addEventListener("click", () => {
  go("grid");
  const tiles = $("grid").children;
  if (tiles[vw.index]) tiles[vw.index].scrollIntoView({ block: "center" });
});
document.addEventListener("keydown", (event) => {
  if (shown !== "viewer" || event.target.tagName === "INPUT") return;
  if (event.key === "ArrowRight") step(1);
  if (event.key === "ArrowLeft") step(-1);
  if (event.key === "Escape") { if (vw.full) leaveFull(); else go("grid"); }
});
window.addEventListener("resize", () => { if (vw.full) placeFull(); });


// ===== quick edits and exports ===========================================================

const ED_KEYS = ["angle", "exposure", "contrast", "highlights", "shadows", "temp", "tint", "saturation", "sharpen"];
const ASPECT = { "3:2": 3 / 2, "4:5": 4 / 5, "1:1": 1, "16:9": 16 / 9 };
let ed = null;   // { file, info, params, cropping, token, renderAt, saveTimer }

function edValue(key, v) {
  if (key === "exposure") return (v > 0 ? "+" : "") + Number(v).toFixed(2);
  if (key === "angle") return (v > 0 ? "+" : "") + Number(v).toFixed(1) + "°";
  return (v > 0 && key !== "sharpen" ? "+" : "") + Math.round(v);
}

async function openEditor() {
  if (!shoot || !shoot.files.length) return;
  const file = shoot.files[vw.index];
  let info;
  try { info = await getJSON("/api/edit?id=" + file.id); } catch (err) { return; }
  ed = { file: file, info: info, params: Object.assign({}, info.params), cropping: false, token: 0, saveTimer: 0, natural: null };
  go("editor");
  for (const key of ED_KEYS) $("ed-" + key).value = ed.params[key];
  setText("ed-saved", "");
  edRefreshControls();
  edRender();
}

function edRefreshControls() {
  for (const key of ED_KEYS) setText("ed-v-" + key, edValue(key, ed.params[key]));
  toggle("ed-bw", ed.params.bw);
  for (const b of $("ed-aspect").children) b.classList.toggle("on", b.dataset.aspect === ed.params.aspect);
  $("ed-cropbtn").classList.toggle("on", ed.cropping);
  setText("ed-cropbtn", ed.cropping ? "Done cropping" : "Crop");
  $("ed-saturation").disabled = ed.params.bw;
}

function edChanged() {
  edRefreshControls();
  edRender();
  clearTimeout(ed.saveTimer);
  setText("ed-saved", "");
  const params = Object.assign({}, ed.params), id = ed.file.id;
  ed.saveTimer = setTimeout(async () => {
    const reply = await post("/api/edit", { id: id, params: params });
    if (ed && ed.file.id === id) setText("ed-saved", reply.error ? reply.error : "Saved");
    ed.file.edited = true;
  }, 600);
}

// Only the latest picture is shown: a slider dragged quickly asks for many.
function edRender() {
  if (!ed) return;
  const token = ++ed.token;
  const p = encodeURIComponent(JSON.stringify(ed.params));
  const url = "/photo/" + ed.file.id + "/edit.jpg?crop=" + (ed.cropping ? "0" : "1") + "&p=" + p;
  clearTimeout(ed.renderTimer);
  ed.renderTimer = setTimeout(async () => {
    show("ed-tag", true);
    setText("ed-tag", "Working");
    const img = new Image();
    img.src = url;
    try { await img.decode(); } catch (err) { if (token === ed.token) setText("ed-tag", "No preview in this file"); return; }
    if (!ed || token !== ed.token) return;
    $("ed-img").src = img.src;
    ed.natural = { w: img.naturalWidth, h: img.naturalHeight };
    edPlace();
    show("ed-tag", false);
  }, 90);
}

function edPlace() {
  // Fit the picture in the stage, and the crop box on top of it.
  if (!ed || !ed.natural) return;
  const box = $("ed-stage").getBoundingClientRect();
  const scale = Math.min((box.width - 32) / ed.natural.w, (box.height - 32) / ed.natural.h);
  const w = Math.round(ed.natural.w * scale), h = Math.round(ed.natural.h * scale);
  const img = $("ed-img");
  img.style.width = w + "px";
  img.style.height = h + "px";
  show("ed-crop", ed.cropping);
  if (ed.cropping) edDrawCrop();
}

function edCropBox() {
  return ed.params.crop || [0, 0, 1, 1];
}

function edDrawCrop() {
  const [x0, y0, x1, y1] = edCropBox();
  const el = $("ed-crop");
  el.style.left = (x0 * 100) + "%";
  el.style.top = (y0 * 100) + "%";
  el.style.width = ((x1 - x0) * 100) + "%";
  el.style.height = ((y1 - y0) * 100) + "%";
}

// The largest box of the chosen shape, centred on the current crop.
function edFitAspect(name) {
  if (!ed.natural) return;
  const imgRatio = ed.natural.w / ed.natural.h;
  let ratio = name === "original" ? imgRatio : ASPECT[name];
  if (!ratio) return;
  if (ed.natural.h > ed.natural.w && name !== "original" && ratio > 1) ratio = 1 / ratio; // upright photo
  let w = 1, h = imgRatio / ratio;      // as fractions of the picture
  if (h > 1) { w = ratio / imgRatio; h = 1; }
  const [x0, y0, x1, y1] = edCropBox();
  const cx = Math.min(1 - w / 2, Math.max(w / 2, (x0 + x1) / 2));
  const cy = Math.min(1 - h / 2, Math.max(h / 2, (y0 + y1) / 2));
  ed.params.crop = [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2];
}

function edRatio() {
  // The chosen shape as a ratio of fractions (width fraction / height fraction), or 0 for free.
  const name = ed.params.aspect;
  if (!ed.natural || name === "free") return 0;
  const imgRatio = ed.natural.w / ed.natural.h;
  let ratio = name === "original" ? imgRatio : ASPECT[name];
  if (ed.natural.h > ed.natural.w && name !== "original" && ratio > 1) ratio = 1 / ratio;
  return ratio / imgRatio;
}

let edDrag = null;
$("ed-frame").addEventListener("pointerdown", (event) => {
  if (!ed || !ed.cropping) return;
  event.preventDefault();
  const rect = $("ed-img").getBoundingClientRect();
  edDrag = { handle: event.target.dataset.h || "move", x: event.clientX, y: event.clientY, box: edCropBox().slice(), rect: rect };
  $("ed-frame").setPointerCapture(event.pointerId);
});
$("ed-frame").addEventListener("pointermove", (event) => {
  if (!edDrag) return;
  const d = edDrag, r = d.rect;
  const dx = (event.clientX - d.x) / r.width, dy = (event.clientY - d.y) / r.height;
  let [x0, y0, x1, y1] = d.box;
  const min = 0.05;
  if (d.handle === "move") {
    const w = x1 - x0, h = y1 - y0;
    x0 = Math.min(1 - w, Math.max(0, x0 + dx)); y0 = Math.min(1 - h, Math.max(0, y0 + dy));
    x1 = x0 + w; y1 = y0 + h;
  } else {
    if (d.handle.includes("l")) x0 = Math.min(x1 - min, Math.max(0, x0 + dx));
    if (d.handle.includes("r")) x1 = Math.max(x0 + min, Math.min(1, x1 + dx));
    if (d.handle.includes("t")) y0 = Math.min(y1 - min, Math.max(0, y0 + dy));
    if (d.handle.includes("b")) y1 = Math.max(y0 + min, Math.min(1, y1 + dy));
    const ratio = edRatio();
    if (ratio) {
      // Keep the shape: the height follows the width, from the fixed corner.
      let w = x1 - x0, h = w / ratio;
      const top = d.handle.includes("t");
      const room = top ? y1 : 1 - y0;
      if (h > room) { h = room; w = h * ratio; if (d.handle.includes("l")) x0 = x1 - w; else x1 = x0 + w; }
      if (top) y0 = y1 - h; else y1 = y0 + h;
    }
  }
  ed.params.crop = [x0, y0, x1, y1];
  edDrawCrop();
});
function edDragEnd() {
  if (!edDrag) return;
  edDrag = null;
  edChanged();
}
$("ed-frame").addEventListener("pointerup", edDragEnd);
$("ed-frame").addEventListener("pointercancel", edDragEnd);
$("ed-frame").addEventListener("dragstart", (event) => event.preventDefault());

for (const key of ED_KEYS) {
  const input = $("ed-" + key);
  input.addEventListener("input", () => { ed.params[key] = Number(input.value); edChanged(); });
  // Double-tap a slider's name to put it back.
  input.closest(".ed-row").querySelector(".ed-top").addEventListener("dblclick", () => {
    ed.params[key] = ed.info.neutral[key];
    input.value = ed.params[key];
    edChanged();
  });
}
for (const b of $("ed-aspect").children) {
  b.addEventListener("click", () => {
    ed.params.aspect = b.dataset.aspect;
    if (b.dataset.aspect !== "free") edFitAspect(b.dataset.aspect);
    ed.cropping = true;
    edChanged();
  });
}
$("ed-cropbtn").addEventListener("click", () => { ed.cropping = !ed.cropping; edChanged(); });
$("ed-cropclear").addEventListener("click", () => { ed.params.crop = null; ed.params.aspect = "free"; edChanged(); });
$("ed-bw").addEventListener("click", () => { ed.params.bw = !ed.params.bw; edChanged(); });
$("ed-reset").addEventListener("click", async () => {
  if (!await ask("Reset", "Put every adjustment and the crop back to how the photo came?", "Reset")) return;
  ed.params = Object.assign({}, ed.info.neutral);
  ed.cropping = false;
  for (const key of ED_KEYS) $("ed-" + key).value = ed.params[key];
  edChanged();
});
// Hold to compare with the photo as it came.
function edBefore(on) {
  if (!ed) return;
  if (on) { $("ed-img").src = "/photo/" + ed.file.id + "/screen.jpg"; show("ed-crop", false); setText("ed-tag", "Original"); show("ed-tag", true); }
  else { show("ed-tag", false); edRender(); }
}
$("ed-before").addEventListener("pointerdown", () => edBefore(true));
$("ed-before").addEventListener("pointerup", () => edBefore(false));
$("ed-before").addEventListener("pointerleave", (event) => { if (event.buttons) edBefore(false); });
window.addEventListener("resize", () => { if (shown === "editor") edPlace(); });
$("vw-edit").addEventListener("click", () => openEditor());

// ----- the export dialog -----

let xm = { size: "2048", quality: "high", ids: [], one: null, watching: false };

function xmMark() {
  for (const b of $("xm-size").children) b.classList.toggle("on", b.dataset.size === xm.size);
  for (const b of $("xm-quality").children) b.classList.toggle("on", b.dataset.quality === xm.quality);
}

async function openExport() {
  if (!ed) return;
  clearTimeout(ed.saveTimer);
  await post("/api/edit", { id: ed.file.id, params: ed.params });  // export what is on screen
  ed.file.edited = true;
  xm.one = ed.file.id;
  xm.ids = shoot.files.filter((f) => f.edited || f.id === ed.file.id).map((f) => f.id);
  setText("xm-text", "A new JPEG of " + ed.file.name + " goes to Exports, " + ed.info.shoot +
    ". The RAW file is not changed. 2048 px suits social media; full size suits prints.");
  setText("xm-all", "All edited (" + n(xm.ids.length) + ")");
  show("xm-all", xm.ids.length > 1);
  show("xm-progress", false);
  xmMark();
  show("export-modal", true);
}

async function xmStart(ids) {
  const reply = await post("/api/export", { ids: ids, size: xm.size, quality: xm.quality });
  if (reply.error) { await ask("Not exported", reply.error, "OK"); return; }
  xm.watching = true;
  show("xm-progress", true);
}

function renderExportJob() {
  const j = S.export_job || {};
  if (!xm.watching) return;
  $("xm-bar").style.width = (j.total ? 100 * j.done / j.total : 0) + "%";
  if (j.busy) setText("xm-status", "Exporting " + n(j.done + 1) + " of " + n(j.total) + ": " + j.current);
  else {
    setText("xm-status", j.error ? j.error : (j.message + " Find them in Exports."));
    $("xm-status").classList.toggle("warn", !!j.error);
    xm.watching = false;
    if (shoot) for (const f of shoot.files) if (xm.ids.includes(f.id)) f.exported = true;
    setText("xm-one", "Open Exports");
    $("xm-one").dataset.open = "1";
  }
}

for (const b of $("xm-size").children) b.addEventListener("click", () => { xm.size = b.dataset.size; xmMark(); });
for (const b of $("xm-quality").children) b.addEventListener("click", () => { xm.quality = b.dataset.quality; xmMark(); });
$("xm-cancel").addEventListener("click", () => { show("export-modal", false); resetXm(); });
$("xm-one").addEventListener("click", () => {
  if ($("xm-one").dataset.open) { show("export-modal", false); resetXm(); openExports("editor"); return; }
  xmStart([xm.one]);
});
$("xm-all").addEventListener("click", () => xmStart(xm.ids));
function resetXm() { delete $("xm-one").dataset.open; setText("xm-one", "This photo"); }
$("ed-export").addEventListener("click", () => openExport());

// ----- the Exports screen -----

let exportsData = null;

async function openExports(from) {
  BACK.exports = from || "library";
  go("exports");
  await loadExports();
}

async function loadExports() {
  try { exportsData = await getJSON("/api/exports"); } catch (err) { return; }
  const d = exportsData;
  setText("ex-summary", d.count ? n(d.count) + plural(d.count, " photo, ", " photos, ") + bytes(d.bytes) +
    ". Also in the NAS share, in the Exports folder." : "");
  show("ex-empty", !d.count);
  $("ex-usb").disabled = !d.count;
  $("ex-phone").disabled = !d.count;
  $("ex-list").replaceChildren(...d.shoots.map((s) => {
    const sec = document.createElement("div");
    sec.className = "ex-shoot";
    const head = document.createElement("div");
    head.className = "ex-head";
    const t = document.createElement("div"); t.className = "mono ex-title"; t.textContent = s.shoot;
    const c = document.createElement("div"); c.className = "body muted"; c.textContent = n(s.files.length) + plural(s.files.length, " photo", " photos");
    const grow = document.createElement("div"); grow.className = "grow";
    const del = document.createElement("button"); del.type = "button"; del.className = "btn"; del.textContent = "Delete";
    del.addEventListener("click", async () => {
      if (!await ask("Delete exports", "Delete the " + n(s.files.length) + " exported JPEGs of " + s.shoot +
        "? The photos and your edits stay; they can be exported again.", "Delete")) return;
      await post("/api/exports/delete", { shoot: s.shoot });
      loadExports();
    });
    head.append(t, c, grow, del);
    const grid = document.createElement("div");
    grid.className = "thumbs";
    for (const f of s.files) {
      const box = document.createElement("div");
      box.className = "thumb";
      const img = document.createElement("img");
      img.loading = "lazy"; img.alt = ""; img.draggable = false;
      img.src = "/export/" + encodeURIComponent(s.shoot) + "/" + encodeURIComponent(f.name) + "?t=thumb&v=" + Math.round(f.time);
      const label = document.createElement("div"); label.className = "num"; label.textContent = frameNumber(f.name);
      box.append(img, label);
      grid.append(box);
    }
    sec.append(head, grid);
    return sec;
  }));
}

$("btn-exports").addEventListener("click", () => openExports("library"));
$("grid-exports").addEventListener("click", () => openExports("grid"));

// ----- copying exports to a USB stick -----

let um = { open: false, drives: [], mount: "", timer: 0, watching: false };

async function umLoad() {
  if (!um.open) return;
  try { um.drives = (await post("/api/usb")).drives || []; } catch (err) { um.drives = []; }
  if (!um.drives.some((d) => d.mount === um.mount)) um.mount = um.drives.length ? um.drives[0].mount : "";
  umRender();
  um.timer = setTimeout(umLoad, 2000);
}

function umRender() {
  const count = exportsData ? exportsData.count : 0;
  setText("um-text", um.drives.length
    ? "Copies the " + n(count) + plural(count, " exported photo", " exported photos") + " to a folder called DoubleTake Exports, " +
      "each read back and checked. Nothing on the stick is removed."
    : "Plug in a USB stick. It shows here when the tablet has opened it.");
  rebuild("um-drives", um.drives.map((d) => Object.assign({ on: d.mount === um.mount }, d)), (d) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "um-drive" + (d.on ? " on" : "");
    const t = document.createElement("div"); t.className = "t"; t.textContent = d.label;
    const b = document.createElement("div"); b.className = "body"; b.textContent = bytes(d.free) + " free of " + bytes(d.total);
    row.append(t, b);
    row.addEventListener("click", () => { um.mount = d.mount; umRender(); });
    return row;
  });
  const job = S.usb_job || {};
  $("um-copy").disabled = !um.mount || !!job.busy;
  show("um-eject", !!um.mount && !job.busy && job.mount === um.mount && !!job.message);
}

function renderUsbJob() {
  if (!um.open) return;
  const job = S.usb_job || {};
  if (job.busy || um.watching) {
    show("um-progress", true);
    $("um-bar").style.width = (job.total ? 100 * job.done / job.total : 0) + "%";
    setText("um-status", job.busy ? "Copying " + n(job.done + 1) + " of " + n(job.total) + ": " + job.current
                                  : (job.error || job.message));
    $("um-status").classList.toggle("warn", !!job.error);
    if (!job.busy) um.watching = false;
  }
  umRender();
}

function openUsb() {
  um.open = true;
  um.watching = false;
  show("um-progress", false);
  show("usb-modal", true);
  umLoad();
}

$("ex-usb").addEventListener("click", () => openUsb());
$("um-close").addEventListener("click", () => { um.open = false; clearTimeout(um.timer); show("usb-modal", false); });
$("um-copy").addEventListener("click", async () => {
  const reply = await post("/api/usb/copy", { mount: um.mount });
  if (reply.error) { await ask("Not copied", reply.error, "OK"); return; }
  um.watching = true;
});
$("um-eject").addEventListener("click", async () => {
  const reply = await post("/api/usb/eject", { mount: um.mount });
  show("um-progress", true);
  setText("um-status", reply.error || "Ejected. The USB stick can be pulled out.");
  $("um-status").classList.toggle("warn", !!reply.error);
  show("um-eject", false);
});

// ----- sending exports to a phone, over the hotspot -----

let pm = { open: false, waiting: false, codes: null };

async function pmStart() {
  const reply = await post("/api/phone/start", {});
  if (!pm.open) { if (reply.ok) post("/api/phone/stop"); return; }
  if (reply.need_hotspot) {
    pm.codes = null;
    show("pm-codes", false);
    show("pm-hotspot", !pm.waiting);
    setText("pm-text", pm.waiting ? "Starting the hotspot..." :
      "The phone joins the tablet's own Wi-Fi hotspot to fetch the photos. While the hotspot is on, the tablet " +
      "leaves any other Wi-Fi network.");
    if (pm.waiting) setTimeout(pmStart, 2000);
    return;
  }
  if (reply.error) { setText("pm-text", reply.error); show("pm-codes", false); return; }
  pm.waiting = false;
  pm.codes = reply;
  show("pm-hotspot", false);
  setText("pm-text", "Scan the first code with the phone's camera to join the tablet's Wi-Fi, then the second to open the " +
    n(reply.count) + plural(reply.count, " photo.", " photos."));
  // As pictures, so the screens' own drawing styles cannot touch the codes.
  const qr = (id, svg) => {
    const img = document.createElement("img");
    img.alt = "";
    if (svg) img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
    $(id).replaceChildren(...(svg ? [img] : []));
    show(id, !!svg);
  };
  qr("pm-qr-wifi", reply.qr_wifi);
  qr("pm-qr-url", reply.qr_url);
  setText("pm-wifi", reply.wifi + "  /  " + reply.password);
  setText("pm-url", reply.url);
  show("pm-codes", true);
}

function renderPhone() {
  if (!pm.open || !pm.codes) return;
  const p = S.phone || {};
  if (!p.on) { setText("pm-status", "Sharing has stopped."); return; }
  setText("pm-status", (p.phones ? n(p.phones) + plural(p.phones, " phone", " phones") + " connected. " : "Waiting for the phone. ") +
    (p.served ? n(p.served) + " of " + n(p.count) + " photos opened. " : "") + "Stops by itself in " + n(p.minutes_left) + " min.");
}

$("ex-phone").addEventListener("click", () => {
  pm.open = true; pm.waiting = false; pm.codes = null;
  setText("pm-text", "Starting..."); setText("pm-status", "");
  show("pm-codes", false); show("pm-hotspot", false);
  show("phone-modal", true);
  pmStart();
});
$("pm-hotspot").addEventListener("click", async () => {
  pm.waiting = true;
  show("pm-hotspot", false);
  await post("/api/network/hotspot", { on: true });
  setTimeout(pmStart, 3000);
});
$("pm-close").addEventListener("click", () => {
  pm.open = false; pm.codes = null;
  post("/api/phone/stop");
  show("phone-modal", false);
});

// ----- photo names -----

const RENAME_PRESETS = ["{date}_{shoot}_{seq}", "{shoot}_{seq}", "{date}_{time}_{num}", "{shoot}_{orig}", "{camera}_{date}_{seq}"];

async function namingExample(pattern) {
  try { return await getJSON("/api/naming?pattern=" + encodeURIComponent(pattern)); } catch (err) { return { error: "" }; }
}

async function renderNaming() {
  if (!prefs) return;
  toggle("set-rename", prefs.rename_on_import);
  setText("set-rename-pattern", prefs.rename_pattern);
  const ex = await namingExample(prefs.rename_pattern);
  setText("set-rename-example", ex.error || "For example: " + ex.example);
  rebuild("set-rename-presets", RENAME_PRESETS.map((p) => ({ p: p, on: p === prefs.rename_pattern })), (x) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "btn" + (x.on ? " on" : "");
    chip.textContent = x.p;
    chip.addEventListener("click", () => setPattern(x.p));
    return chip;
  });
  if (!$("set-rename-tokens").childElementCount) {
    $("set-rename-tokens").replaceChildren(...Object.entries(prefs.rename_tokens || {}).map(([t, d]) => {
      const line = document.createElement("div");
      const b = document.createElement("b"); b.className = "mono"; b.textContent = t;
      line.append(b, document.createTextNode("  " + d));
      return line;
    }));
  }
}

async function setPattern(pattern) {
  const reply = await post("/api/settings", { changes: { rename_pattern: pattern } });
  if (reply.error) { await ask("Not saved", reply.error, "OK"); return false; }
  await loadSettings();
  renderNaming();
  return true;
}

function editPattern(after) {
  openKeyboard({
    label: "Name pattern", value: prefs.rename_pattern, max: 80, any: true, chips: RENAME_PRESETS,
    hint: "Use {date} {time} {shoot} {seq} {orig} {num} {camera}. The #+= key has the curly brackets.",
    done: async (value) => { if (value && await setPattern(value) && after) after(); },
  });
}

$("net-direct").addEventListener("click", async () => { await change({ direct_cable: !prefs.direct_cable }); await loadSettings(); });
$("set-rename").addEventListener("click", async () => {
  await change({ rename_on_import: !prefs.rename_on_import });
  await loadSettings();
  renderNaming();
});
$("set-rename-edit").addEventListener("click", () => editPattern());

// Renaming a shoot that is already on the tablet.
let rn = { id: 0 };

async function openRename() {
  if (!shoot) return;
  if (!prefs) await loadSettings();
  rn.id = shoot.id;
  show("rename-modal", true);
  await rnRefresh();
}

async function rnRefresh() {
  setText("rn-pattern", prefs.rename_pattern);
  let plan;
  try { plan = await getJSON("/api/rename?id=" + rn.id); } catch (err) { plan = { error: "Could not read the shoot." }; }
  if (plan.error) {
    setText("rn-text", plan.error);
    $("rn-sample").replaceChildren();
    $("rn-go").disabled = true;
    return;
  }
  setText("rn-text", plan.changes
    ? "Renames " + n(plan.changes) + " of the " + n(plan.total) + " photos on Copy 1, and their copies on Copy 2 " +
      "(now, or the next time it is connected). The camera's own names are kept in the records."
    : "Every photo already has a name from this pattern.");
  $("rn-sample").replaceChildren(...plan.sample.map((s) => {
    const row = document.createElement("div");
    row.className = "rn-row mono";
    row.textContent = s.old + "  →  " + s.new;
    return row;
  }));
  $("rn-go").disabled = !plan.changes;
  setText("rn-go", plan.changes ? "Rename " + n(plan.changes) : "Rename");
}

$("grid-rename").addEventListener("click", () => openRename());
$("rn-edit").addEventListener("click", () => editPattern(rnRefresh));
$("rn-cancel").addEventListener("click", () => show("rename-modal", false));
$("rn-go").addEventListener("click", async () => {
  $("rn-go").disabled = true;
  setText("rn-go", "Renaming...");
  const reply = await post("/api/rename", { id: rn.id });
  show("rename-modal", false);
  if (reply.error) { await ask("Not renamed", reply.error, "OK"); return; }
  openShoot(rn.id);
});

// ----- settings -----

async function loadSettings() {
  try {
    prefs = await getJSON("/api/settings");
    renderSettings();
  } catch (err) { /* the next poll reports a lost engine */ }
}

async function change(changes) {
  const reply = await guardedPost("/api/settings", { changes: changes }, "Settings");
  if (reply.cancelled) { await loadSettings(); return; }
  if (reply.error) { await ask("Not saved", reply.error, "OK"); }
  else { setText("set-saved", "Saved"); setTimeout(() => setText("set-saved", ""), 1500); }
  await loadSettings();
}

function segment(id, value) {
  for (const b of $(id).children) b.classList.toggle("on", b.dataset.v === String(value));
}
function toggle(id, on) {
  const el = $(id);
  el.classList.toggle("on", !!on);
  el.firstElementChild.textContent = on ? "On" : "Off";
}

function renderSettings() {
  if (!prefs) return;
  if (wiz) wizRender();
  for (const b of document.querySelectorAll(".rail [data-sec]")) b.classList.toggle("on", b.dataset.sec === section);
  for (const panel of document.querySelectorAll("[data-panel]")) panel.toggleAttribute("hidden", panel.dataset.panel !== section);
  for (const t of document.querySelectorAll("[data-theme-pick]")) t.classList.toggle("on", t.dataset.themePick === prefs.theme);
  for (const t of document.querySelectorAll("[data-cockpit-pick]")) t.classList.toggle("on", t.dataset.cockpitPick === prefs.cockpit_theme);
  segment("set-look", prefs.look);
  segment("set-dark", prefs.screen_dark_after);
  segment("set-sleep", prefs.sleep_on_battery);
  setText("set-sleep-text", prefs.sleep_on_battery
    ? "With no touch for this long, and only on battery. Never while photos are being copied. The power button wakes it." +
      (prefs.auto_backup ? " With unattended backup on, Never is better: plugging in the camera may not wake a sleeping tablet." : "")
    : "Never: the tablet stays awake with the screen dark, so plugging in the camera always works. Uses a little battery.");
  const sj = prefs.splash_job || {};
  toggle("set-splash", prefs.splash_on);
  $("set-splash").disabled = !prefs.splash_available || !!sj.busy;
  setText("set-splash-text", !prefs.splash_available ? "Not set up yet. Run doubletake setup in a terminal; it installs the DoubleTake boot screen."
    : sj.busy ? "Changing the boot screen. This takes a minute or two; the tablet keeps working meanwhile."
    : sj.error ? "Not changed: " + sj.error
    : prefs.splash_on ? "The DoubleTake logo shows while the tablet starts."
    : "Show the DoubleTake logo while the tablet starts, instead of the system's own screen.");
  if (sj.busy) setTimeout(loadSettings, 3000);
  show("themes-classic", prefs.look !== "cockpit");
  show("themes-cockpit", prefs.look === "cockpit");

  toggle("set-auto", prefs.auto_backup);
  toggle("set-auto-dark", prefs.auto_dark);
  toggle("set-auto-chime", prefs.auto_chime);
  segment("set-updates", prefs.update_days);
  loadFlicker();
  show("set-auto-dark-row", prefs.auto_backup);
  setText("set-auto-text", prefs.auto_backup
    ? "On. Plug in the camera: after a 10-second countdown you can cancel, the new photos are copied, into a shoot folder named by the date (2026_1015_import_01, then _02). What to copy and the battery rules apply."
    : "Start a backup by itself when the camera is plugged in, after a 10-second countdown you can cancel.");
  setText("set-library", prefs.library);

  toggle("set-second", prefs.second_copy === "on");
  segment("set-second-device", prefs.second_copy_device);
  const onUsb = prefs.second_copy_device === "usb";
  setText("set-device-text", onUsb
    ? "The drive need not stay plugged in: photos backed up while it is away are copied when it is back."
    : "The card in the tablet's own slot.");
  setText("set-folder-title", onUsb ? "Folder on the drive" : "Folder on the card");
  setText("set-card-folder", prefs.second_copy_folder + (prefs.second_copy_path ? "   in " + prefs.second_copy_path : ""));
  renderSecondSettings();

  segment("set-copy", prefs.copy_what);
  setText("set-copy-text", {
    raw: "Only RAW files are copied. JPEGs and videos stay on the camera, and the cards are never called safe to format while they are there.",
    raw_jpeg: "RAW and JPEG photos are copied. Videos stay on the camera, and the cards are never called safe to format while they are there.",
    all: "Every file in the camera's DCIM folder is copied, videos too.",
  }[prefs.copy_what] || "");

  toggle("set-login", prefs.start_at_power_on);
  setText("set-login-text", !prefs.power_on_ready
    ? "Needs the system setup first: run doubletake setup in a terminal."
    : prefs.start_at_power_on
      ? "On. The tablet logs in by itself, with no password, and opens DoubleTake full screen. The screen does not lock or go dark."
      : "Log in by itself at power-on, with no password, and open DoubleTake full screen. The screen will not lock or go dark.");
  $("set-login").disabled = !prefs.power_on_ready;
  const pw = prefs.fs_password || { mode: "unset" };
  setText("set-pw-text", !pw.available ? "Needs the system setup first: run doubletake setup in a terminal."
    : pw.mode === "password" ? "Set. It is asked for Maintenance mode, Exit, switching DoubleTake only off, and Format, Reset and Erase. Everything else works without it."
    : pw.mode === "none" ? "No password, by your choice. Anyone holding the tablet can open Maintenance mode, Exit, and Format or Erase."
    : pw.mode === "unknown" ? "Could not be checked: " + (pw.error || "")
    : "Not set. Recommended with DoubleTake only: it keeps the ways out of DoubleTake, and the jobs that cannot be undone, behind a password of its own.");
  setText("set-pw", pw.mode === "password" ? "Change" : "Set");
  $("set-pw").disabled = !pw.available;
  show("set-pw-none", pw.available && pw.mode !== "none");
  toggle("set-kiosk", prefs.kiosk_on);
  $("set-kiosk").disabled = !prefs.kiosk_available;
  setText("set-kiosk-text", !prefs.kiosk_available
    ? "Not set up yet. Run doubletake setup in a terminal; it adds the kiosk session that this needs."
    : prefs.kiosk_on && prefs.in_kiosk
      ? "On. The tablet runs DoubleTake and nothing else: no desktop behind it. If the screens ever close, they open again by themselves."
      : prefs.kiosk_on
        ? "On from the next login: DoubleTake and nothing else, with no desktop behind it."
        : "Start into DoubleTake alone, with no desktop behind it, like an appliance. The desktop stays a tap away here.");
  renderBatteryCard();
  show("dev-desktop-row", !!prefs.in_kiosk);
  show("dev-exit-row", !prefs.in_kiosk);
  show("set-apps-row", !prefs.in_kiosk);
  const about = prefs.about;
  const pairs = (rows) => rows.flatMap(([k, v]) => {
    const a = document.createElement("b"); a.textContent = k;
    const b = document.createElement("span"); b.textContent = v || "unknown";
    return [a, b];
  });
  $("set-about").replaceChildren(...pairs([["DoubleTake Field Station", about.version], ["Tablet", about.tablet], ["Name on the network", about.host],
    ["System", about.system], ["Python", about.python], ["Camera library", about.camera_lib], ["Image library", about.image_lib]]));
  $("set-folders-about").replaceChildren(...pairs([["Program", about.program], ["Photos", about.photos], ["Records", about.records]]));
}

let netList = [];

function renderNetworkSettings() {
  if (!prefs || section !== "network") return;
  const net = S.net || {};
  const job = S.net_job || {};
  if (!net.available) {
    setText("net-wifi-text", "NetworkManager was not found on this tablet.");
  } else if (net.hotspot) {
    setText("net-wifi-text", "Running the hotspot " + prefs.hotspot_name + ".");
  } else if (net.ssid) {
    setText("net-wifi-text", "Connected to " + net.ssid + ", signal " + net.signal + "%.");
  } else {
    setText("net-wifi-text", net.wifi_state === "unavailable" ? "Wi-Fi is switched off." : "Not connected.");
  }
  show("net-disconnect", !!(net.ssid && !net.hotspot));
  setText("net-wired-text", net.direct ? "Linked straight to a laptop." : net.wired ? "Connected." :
    net.direct_waiting ? "Plugged in. No network answered yet; linking to the laptop shortly." :
    net.cable_waiting ? "Plugged in, waiting for an address." : "Not connected.");
  toggle("net-direct", prefs.direct_cable);
  if (S.nas && S.nas.host) setText("net-direct-host", S.nas.host);
  show("net-job", !!(job.busy || job.message || job.error));
  setText("net-job-text", job.error || job.message);
  $("net-job-text").classList.toggle("warn", !!job.error);
  toggle("net-hotspot", !!net.hotspot);
  setText("net-hs-name", prefs.hotspot_name);
  setText("net-hs-pass", prefs.hotspot_password || "Made the first time the hotspot is switched on.");
  setText("net-hs-help", "On the laptop, join " + prefs.hotspot_name + ". The tablet is then at " + prefs.hotspot_address + ".");
  rebuild("net-list", netList.map((x) => Object.assign({}, x, { here: x.ssid === net.ssid })), (item) => {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "net" + (item.here ? " on" : "");
    const icon = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    icon.setAttribute("viewBox", "0 0 24 24");
    icon.innerHTML = wifiIcon(signalLevel(item.signal), false);
    const name = document.createElement("div");
    name.className = "ssid";
    name.textContent = item.ssid;
    const st = document.createElement("div");
    st.className = "st";
    st.textContent = item.here ? "Connected" : item.saved ? "Saved" : "";
    row.append(icon, name);
    if (item.secure) row.insertAdjacentHTML("beforeend", LOCK_ICON);
    row.append(st);
    row.addEventListener("click", () => joinNetwork(item));
    return row;
  });
}

async function scanNetworks() {
  setText("net-scan", "Looking...");
  $("net-scan").disabled = true;
  const reply = await post("/api/network/scan");
  $("net-scan").disabled = false;
  setText("net-scan", "Look again");
  if (reply.error) { await ask("Wi-Fi", reply.error, "OK"); return; }
  netList = reply.networks || [];
  renderNetworkSettings();
}

function joinNetwork(item) {
  if (item.here) return;
  if (item.secure && !item.saved) {
    openKeyboard({
      label: "Password for " + item.ssid, value: "", max: 63, any: true, secret: true,
      done: (password) => { if (password) post("/api/network/connect", { ssid: item.ssid, password: password }); },
    });
  } else {
    post("/api/network/connect", { ssid: item.ssid });
  }
}

let nasNets = [];

async function loadNasNetworks() {
  const reply = await post("/api/nas/networks");
  nasNets = reply.networks || [];
  renderNasSettings();
}

function renderNasSettings() {
  if (!prefs || section !== "nas") return;
  const n = S.nas || {};
  const job = S.nas_job || {};
  show("nas-missing", !n.installed);
  const hidden = job.busy ? null : nasHidden();
  show("nas-hidden", !!hidden);
  if (hidden) {
    const where = (hidden.kind === "cable" || hidden.kind === "direct") ? "The cable" : hidden.kind === "hotspot" ? "The hotspot" : "'" + hidden.network + "'";
    setText("nas-hidden-text", canTrust(hidden)
      ? where + " is not trusted, so the firewall keeps the share closed. Trust it only if it is your own network."
      : where + " has no password, so it cannot be trusted. Use the cable or the hotspot.");
    show("nas-hidden-fix", canTrust(hidden));
    setText("nas-hidden-fix", trustLabel(hidden));
  }
  nasFix = hidden;
  toggle("nas-share", !!n.nas_on);
  toggle("nas-sftp", !!n.sftp_on);
  setText("nas-share-text", n.nas_on ? "On. The photos folder is shared, read-only." : "Off.");
  setText("nas-sftp-text", n.sftp_on ? "On, on port " + n.port + ", read-only." : "Off. For FTP-style apps such as Cyberduck or Transmit.");
  show("nas-job", !!(job.busy || job.message || job.error));
  setText("nas-job-text", job.error || job.message);
  $("nas-job-text").classList.toggle("warn", !!job.error);
  setText("nas-login-text", n.user ? "Username " + n.user + ". The password is kept by the system, not shown." : "No login yet. Set one before switching the share on.");
  setText("nas-login", n.user ? "Change" : "Set up");
  toggle("nas-cable", prefs.nas_cable);
  toggle("nas-hotspot", prefs.nas_hotspot);
  const wifi = nasNets.filter((x) => x.kind === "wifi");
  rebuild("nas-wifi", wifi.map((x) => Object.assign({}, x, { on: prefs.nas_trusted.indexOf(x.name) >= 0 })), (x) => {
    const row = document.createElement("div");
    row.className = "setrow";
    const what = document.createElement("div"); what.className = "what";
    const t = document.createElement("div"); t.className = "t"; t.textContent = x.name;
    const b = document.createElement("div"); b.className = "body";
    b.textContent = x.secured ? "Saved Wi-Fi network." : "No password, so it cannot be trusted.";
    what.append(t, b);
    row.append(what);
    if (x.secured) {
      const sw = document.createElement("button");
      sw.type = "button"; sw.className = "toggle" + (x.on ? " on" : "");
      sw.innerHTML = "<span>" + (x.on ? "On" : "Off") + "</span>";
      sw.addEventListener("click", () => {
        const list = prefs.nas_trusted.filter((v) => v !== x.name);
        if (!x.on) list.push(x.name);
        trust({ nas_trusted: list });
      });
      row.append(sw);
    }
    return row;
  });
  const how = [];
  const open = (n.addresses || []).filter((a) => a.open);
  if (n.host) {
    how.push("Mac (Finder, Go, Connect to Server):  smb://" + n.host + ".local");
    how.push("Windows (File Explorer address bar):  \\\\" + n.host + "\\DoubleTake");
    how.push("Linux (Files, Other Locations):  smb://" + n.host + ".local/DoubleTake");
  }
  for (const a of n.addresses || []) how.push("or by address:  smb://" + a.address + "   (" + (a.kind === "cable" ? "cable" : a.kind === "direct" ? "FSDC" : a.kind === "hotspot" ? "hotspot" : a.network || a.kind) + (a.open ? "" : ", not trusted") + ")");
  if (n.sftp_on && n.user && n.host) {
    how.push("SFTP (Cyberduck, Transmit, FileZilla):  sftp://" + n.user + "@" + n.host + ".local:" + n.port);
    for (const a of n.addresses || []) how.push("or by address:  sftp://" + n.user + "@" + a.address + ":" + n.port + (a.open ? "" : "   (not trusted)"));
  } else if (n.sftp_on && !n.user) how.push("SFTP: set the share's login above first.");
  if (!how.length) how.push("Switch the shared folder on to see the addresses.");
  $("nas-how").replaceChildren(...how.map((line) => { const d = document.createElement("div"); d.className = "how"; d.textContent = line; return d; }));
}

async function trust(changes) {
  const reply = await post("/api/nas/trust", { changes: changes });
  if (reply.error) await ask("Not saved", reply.error, "OK");
  await loadSettings();
}

let btInfo = null, btAt = 0, btWasBusy = false;

async function loadBluetooth() {
  btAt = Date.now();
  try { btInfo = await post("/api/bluetooth"); } catch (err) { return; }
  renderBluetooth();
}

function btRow(d, buttons) {
  const row = document.createElement("div");
  row.className = "setrow";
  const what = document.createElement("div"); what.className = "what";
  const t = document.createElement("div"); t.className = "t"; t.textContent = d.name || d.address;
  const b = document.createElement("div"); b.className = "body";
  b.textContent = [d.kind ? d.kind[0].toUpperCase() + d.kind.slice(1) : "", d.paired ? (d.connected ? "Connected" : "Not connected") : ""]
    .filter(Boolean).join(". ") || d.address;
  what.append(t, b);
  row.append(what);
  for (const [label, act] of buttons) {
    const btn = document.createElement("button");
    btn.type = "button"; btn.className = "btn"; btn.textContent = label;
    btn.addEventListener("click", act);
    row.append(btn);
  }
  return row;
}

async function btDo(what, body) {
  const reply = await post("/api/bluetooth/" + what, body || {});
  if (reply.error) await ask("Bluetooth", reply.error, "OK");
}

function renderBluetooth() {
  if (section !== "bluetooth") return;
  const job = S.bt_job || {};
  if (btWasBusy && !job.busy) loadBluetooth();
  btWasBusy = !!job.busy;
  if (!btInfo) { if (Date.now() - btAt > 1000) loadBluetooth(); return; }
  if (!job.busy && Date.now() - btAt > 5000) loadBluetooth();
  const b = btInfo;
  let missing = "";
  if (!b.hardware) missing = "This tablet has no Bluetooth adapter, or it is switched off in the BIOS.";
  else if (!b.tool || !b.service) missing = "The Bluetooth service is not installed or not running. Run doubletake setup in a terminal.";
  show("bt-missing", !!missing);
  show("bt-main", !missing);
  setText("bt-missing-text", missing);
  if (missing) return;
  toggle("bt-power", b.powered);
  $("bt-power").disabled = !!job.busy;
  setText("bt-text", b.powered ? "On" + (b.name ? ", as " + b.name : "") + "." : "Off.");
  show("bt-job", !!(job.busy || job.message || job.error));
  setText("bt-job-text", job.error || job.message);
  $("bt-job-text").classList.toggle("warn", !!job.error);
  $("bt-scan").disabled = !b.powered || !!job.busy;
  rebuild("bt-paired", b.paired.length ? b.paired : [{ none: true }], (d) => {
    if (d.none) { const e = document.createElement("div"); e.className = "body muted"; e.textContent = "Nothing paired yet."; return e; }
    return btRow(d, [
      [d.connected ? "Disconnect" : "Connect", () => btDo("connect", { address: d.address, on: !d.connected })],
      ["Remove", async () => { if (await ask("Remove " + (d.name || d.address), "Forget this device? It can be paired again later.", "Remove")) btDo("remove", { address: d.address }); }],
    ]);
  });
  rebuild("bt-nearby", b.powered ? b.nearby : [], (d) => btRow(d, [["Pair", () => btDo("pair", { address: d.address, name: d.name })]]));
}

function renderSecondSettings() {
  if (prefs && section === "device") renderBatteryCard();
  renderNetworkSettings();
  renderBluetooth();
  renderNasSettings();
  if (!prefs || section !== "second") return;
  const k = S.card;
  const s = secondInfo();
  setText("set-card-title", k.label ? cap(k.noun || "card") + " '" + k.label + "'" : s.title);
  setText("set-card-text", k.label ? bytes(k.free) + " free of " + bytes(k.total) + ". " + s.title + ". " + s.text : s.text);
  show("set-card-use", k.state === "declined" || k.state === "new");
  for (const el of document.querySelectorAll("#set-card-use .kw")) el.textContent = k.kind === "usb" ? "drive" : "card";
  show("set-card-ignore", k.state === "ready" || k.state === "new");
}

for (const b of document.querySelectorAll(".rail [data-sec]")) {
  b.addEventListener("click", () => {
    section = b.dataset.sec;
    renderSettings();
    if (section === "network" && !netList.length) scanNetworks();
    if (section === "nas") loadNasNetworks();
    if (section === "bluetooth") loadBluetooth();
    if (section === "folders") renderNaming();
  });
}
function rememberLook(look, theme) {
  try {
    if (localStorage.getItem("fs-look") !== look) localStorage.setItem("fs-look", look);
    if (localStorage.getItem("fs-theme") !== theme) localStorage.setItem("fs-theme", theme);
  } catch (err) { /* only saves a flash of the old look at start */ }
}

// The screens change at once; the engine saves the choice and draws in it from then on.
function drawIn(look, theme) {
  document.documentElement.dataset.look = look;
  document.documentElement.dataset.theme = theme;
  if (S) { S.look = look; S.theme = theme; }
}
for (const t of document.querySelectorAll("[data-theme-pick]")) {
  t.addEventListener("click", () => {
    drawIn("classic", t.dataset.themePick);
    change({ theme: t.dataset.themePick });
  });
}
for (const t of document.querySelectorAll("[data-cockpit-pick]")) {
  t.addEventListener("click", () => {
    drawIn("cockpit", t.dataset.cockpitPick);
    change({ cockpit_theme: t.dataset.cockpitPick });
  });
}
$("set-splash").addEventListener("click", () => change({ boot_splash: !prefs.splash_on }));
for (const b of $("set-look").children) {
  b.addEventListener("click", () => {
    const look = b.dataset.v;
    drawIn(look, look === "cockpit" ? prefs.cockpit_theme : prefs.theme);
    change({ look: look });
  });
}
for (const b of $("set-copy").children) b.addEventListener("click", () => change({ copy_what: b.dataset.v }));
$("set-auto").addEventListener("click", () => {
  const on = !prefs.auto_backup;
  // A sleeping tablet may not wake for the camera, so unattended backup keeps it awake, screen dark.
  change(on && prefs.sleep_on_battery ? { auto_backup: true, sleep_on_battery: 0 } : { auto_backup: on });
});
for (const b of $("set-dark").children) b.addEventListener("click", () => change({ screen_dark_after: Number(b.dataset.v) }));
for (const b of $("set-sleep").children) b.addEventListener("click", () => change({ sleep_on_battery: Number(b.dataset.v) }));
$("set-auto-dark").addEventListener("click", () => change({ auto_dark: !prefs.auto_dark }));
$("set-auto-chime").addEventListener("click", () => change({ auto_chime: !prefs.auto_chime }));

// The screen flicker fix: Intel graphics' Panel Self Refresh off, for every kernel.
let flicker = {};
async function loadFlicker() {
  try { flicker = await getJSON("/api/flicker"); } catch (err) { flicker = {}; }
  show("set-flicker-row", !!flicker.intel);
  if (!flicker.intel) return;
  toggle("set-flicker", !!flicker.set);
  setText("set-flicker-text", (flicker.set ? "On: two of Intel graphics' power savings are off, which stops the flicker some Intel screens have on Linux. Uses a little more battery."
    : "Turns off two of Intel graphics' power savings, the usual causes of a flickering screen on Linux. Uses a little more battery.")
    + (flicker.set !== flicker.active ? " Restart the tablet to apply." : ""));
}
$("set-flicker").addEventListener("click", async () => {
  const on = !flicker.set;
  if (!await ask("Screen flicker fix",
      on ? "This changes how the tablet starts: Panel Self Refresh and the display's deep idle states are switched off for every kernel, the Maintenance entry included. It takes effect after a restart. Switch it off here at any time."
         : "Both go back to the system's own choice after a restart.", on ? "Switch on" : "Switch off")) return;
  const reply = await guardedPost("/api/flicker", { on: on }, "Screen flicker fix");
  if (reply.cancelled) return;
  if (reply.error) { await ask("Screen flicker fix", reply.error, "OK"); return; }
  await loadFlicker();
  if (reply.restart) leave("restart", "Restart now?", "The flicker fix applies from the next start.", "Restart");
});
for (const b of $("set-updates").children) b.addEventListener("click", () => change({ update_days: Number(b.dataset.v) }));
$("set-second").addEventListener("click", () => change({ second_copy: prefs.second_copy === "on" ? "off" : "on" }));
for (const b of $("set-second-device").children) b.addEventListener("click", () => change({ second_copy_device: b.dataset.v }));
$("set-login").addEventListener("click", async () => {
  const on = !prefs.start_at_power_on;
  if (on && !await ask("Start at power-on",
      "Anyone who picks up the tablet can then use it without a password. The NAS keeps its own login. Switch it on?", "Switch on")) return;
  await change({ start_at_power_on: on });
  await loadSettings();
});
$("net-scan").addEventListener("click", scanNetworks);
$("net-disconnect").addEventListener("click", () => post("/api/network/disconnect"));
$("net-hotspot").addEventListener("click", async () => {
  const on = !(S.net && S.net.hotspot);
  if (on && S.net && S.net.ssid) {
    const yes = await ask("Hotspot", "Starting the hotspot takes the tablet off " + S.net.ssid +
                          " until it is switched off again.", "Start the hotspot");
    if (!yes) return;
  }
  await post("/api/network/hotspot", { on: on });
  setTimeout(loadSettings, 1500);
});
$("net-hs-rename").addEventListener("click", () => openKeyboard({
  label: "Hotspot network name", value: prefs.hotspot_name, max: 32, any: true,
  done: (name) => { if (name) change({ hotspot_name: name }); },
}));
$("net-hs-repass").addEventListener("click", () => openKeyboard({
  label: "Hotspot password, at least 8 characters", value: "", max: 63, any: true,
  done: (password) => { if (password) change({ hotspot_password: password }); },
}));
$("nas-fix").addEventListener("click", (e) => { e.stopPropagation(); if (nasFix) trustHidden(nasFix); });
$("nas-hidden-fix").addEventListener("click", () => { if (nasFix) trustHidden(nasFix); });
$("bt-power").addEventListener("click", () => btDo("power", { on: !(btInfo && btInfo.powered) }));
$("bt-scan").addEventListener("click", () => btDo("scan"));
$("nas-share").addEventListener("click", () => post("/api/nas/switch", { what: "nas", on: !(S.nas && S.nas.nas_on) }));
$("nas-sftp").addEventListener("click", () => post("/api/nas/switch", { what: "sftp", on: !(S.nas && S.nas.sftp_on) }));
$("nas-cable").addEventListener("click", () => trust({ nas_cable: !prefs.nas_cable }));
$("nas-hotspot").addEventListener("click", () => trust({ nas_hotspot: !prefs.nas_hotspot }));
$("nas-login").addEventListener("click", () => openKeyboard({
  label: "Username for the share (small letters and digits)", value: (S.nas && S.nas.user) || "photos", max: 30,
  done: (name) => {
    name = name.toLowerCase().replace(/[^a-z0-9_-]/g, "");
    if (!name) return;
    setTimeout(() => openKeyboard({
      label: "Password for " + name + ", at least 8 characters", value: "", max: 128, any: true, secret: true,
      done: (password) => { if (password) post("/api/nas/login", { name: name, password: password }); },
    }), 50);
  },
}));
$("dev-format").addEventListener("click", async () => {
  const r = await post("/api/card/report");
  if (r.error) { await ask("Format the card", r.error, "OK"); return; }
  let text = "Everything on the " + r.noun + " '" + r.label + "' (" + bytes(r.total) + ") is erased";
  text += r.other_files ? ", including " + n(r.other_files) + " files that are not the app's." : ".";
  if (r.unprotected) text += " Until Copy 2 runs again, which it does by itself, the tablet holds the only copy of the " + n(r.unprotected) + " photos on it.";
  if (!await ask("Format the card", text, "Format the card")) return;
  setText("set-saved", "Formatting...");
  const done = await guardedPost("/api/card/format", {}, "Format the card");
  if (done.cancelled) { setText("set-saved", ""); return; }
  setText("set-saved", "");
  await ask("Format the card", done.error || "Done. The card is empty and is offered as a new second copy.", "OK");
});
$("dev-reset").addEventListener("click", async () => {
  if (!await ask("Reset settings", "Put every setting back to how it came, and switch the NAS off? Photos and records stay.", "Reset")) return;
  const done = await guardedPost("/api/reset/settings", {}, "Reset settings");
  if (done.cancelled) return;
  await ask("Reset settings", done.error || "Done.", "OK");
  loadSettings();
});
$("dev-erase").addEventListener("click", async () => {
  if (!await ask("Erase everything", "Erase every photo and record on the tablet, and the settings? Copy 2's card or drive and the camera's cards are not touched. This cannot be undone.", "Erase")) return;
  let done = await guardedPost("/api/reset/everything", { confirmed: false }, "Erase everything");
  if (done.cancelled) return;
  if (done.needs_confirm) {
    openKeyboard({
      label: n(done.unimported) + plural(done.unimported, " shoot is", " shoots are") + " still on the tablet. Type ERASE to erase " + plural(done.unimported, "it", "them") + " anyway",
      value: "", max: 10,
      done: async (typed) => {
        if (typed !== "ERASE") { ask("Erase everything", "Nothing was erased.", "OK"); return; }
        const again = await guardedPost("/api/reset/everything", { confirmed: true }, "Erase everything");
        if (again.cancelled) return;
        ask("Erase everything", again.error || "Erased.", "OK");
        loadSettings();
      },
    });
    return;
  }
  await ask("Erase everything", done.error || "Erased.", "OK");
  loadSettings();
});
$("set-card-use").addEventListener("click", async () => {
  if (S.card.state === "declined") await post("/api/card/use");  // offered again, with the pop-up
  else prepareDrive();
});
$("set-card-ignore").addEventListener("click", () => post("/api/card/ignore"));
// ----- the DoubleTake password ------------------------------------------------------

let pwHeld = { value: "", at: 0 };  // kept for a minute, so a two-step job asks once

function askSecret(label, hint) {
  return new Promise((resolve) => {
    openKeyboard({ label: label, hint: hint || "", value: "", max: 128, secret: true, any: true,
                   done: (v) => resolve(v), cancel: () => resolve(null) });
  });
}

// Post to a guarded button. When the engine asks for the password, ask for it and try again.
async function guardedPost(path, body, title) {
  const send = (pw) => post(path, Object.assign({}, body || {}, pw ? { password: pw } : {}));
  const held = Date.now() - pwHeld.at < 60000 ? pwHeld.value : "";
  let reply = await send(held);
  let hint = "";
  while (reply.need_password) {
    const pw = await askSecret((title || "DoubleTake") + ": DoubleTake password", hint || reply.error);
    if (pw === null) return { cancelled: true, ok: false };
    reply = await send(pw);
    if (!reply.need_password) pwHeld = { value: pw, at: Date.now() };
    hint = reply.error;
  }
  return reply;
}

async function setPasswordFlow() {
  const st = (prefs && prefs.fs_password) || {};
  let current = "";
  if (st.mode === "password") {
    current = await askSecret("Current DoubleTake password");
    if (current === null) return;
  }
  let hint = "At least 10 characters. A few words with spaces is easy to type here and hard to guess.";
  for (;;) {
    const first = await askSecret("New DoubleTake password", hint);
    if (first === null) return;
    const again = await askSecret("Type the new password again");
    if (again === null) return;
    if (first !== again) { hint = "The two did not match. Try again."; continue; }
    const reply = await post("/api/fs-password/set", { current: current, new: first });
    if (reply.error) {
      if (/current/i.test(reply.error)) { await ask("DoubleTake password", reply.error, "OK"); return; }
      hint = reply.error + " Try another.";
      continue;
    }
    await ask("DoubleTake password", "Set. Maintenance mode, Exit, switching DoubleTake only off, Format, Reset and Erase now ask for it.", "OK");
    loadSettings();
    return;
  }
}

// ----- DoubleTake only, restart, shut down ---------------------------------------

function busyWarning() {
  if (S.backup && S.backup.running) return "A backup from the camera is running. Stop it, or wait for it to finish, first.";
  return "";
}
async function leave(action, title, text, yes, confirm = true) {
  const busy = busyWarning();
  if (busy) { await ask(title, busy, "OK"); return; }
  if (confirm && !await ask(title, text, yes)) return;
  const reply = await guardedPost("/api/leave", { action: action }, title);
  if (reply.cancelled) return;
  if (reply.error) { await ask(title, reply.error, "OK"); return; }
  // The steps run in the engine; the switching screen lists each one as it finishes.
  startSwitching(action);
}
$("set-pw").addEventListener("click", setPasswordFlow);
$("set-pw-none").addEventListener("click", async () => {
  if (!await ask("No DoubleTake password",
      "Without one, anyone holding the tablet can open Maintenance mode and reach the desktop, Exit DoubleTake, " +
      "and Format the card or Erase everything. Only choose this if the tablet never leaves your hands, or the " +
      "tablet's own login is protected in another way.", "No password")) return;
  let current = "";
  if (prefs.fs_password && prefs.fs_password.mode === "password") {
    current = await askSecret("Current DoubleTake password");
    if (current === null) return;
  }
  const reply = await post("/api/fs-password/none", { current: current });
  await ask("DoubleTake password", reply.error || "No password. You can set one here at any time.", "OK");
  loadSettings();
});
$("set-kiosk").addEventListener("click", async () => {
  const on = !prefs.kiosk_on;
  if (on && !await ask("DoubleTake only",
      "From the next login the tablet starts straight into DoubleTake, with no desktop, top bar or app list behind it. " +
      "To get the desktop back, use Settings, Device, Switch to the desktop.", "Switch on")) return;
  await change({ kiosk: on });
  if (on && prefs.kiosk_on && prefs.fs_password && prefs.fs_password.mode === "unset" &&
      await ask("Set a DoubleTake password?",
        "With no password, anyone holding the tablet can open Maintenance mode and reach the desktop. " +
        "A DoubleTake password, separate from the tablet's own, keeps those ways out shut.", "Set a password")) {
    await setPasswordFlow();
  }
  if (prefs.kiosk_on === on && on !== !!prefs.in_kiosk) {
    await leave("logout", on ? "Start it now?" : "Back to the desktop?",
      on ? "Log out now, so the tablet starts DoubleTake only? Anything part-way is picked up again."
         : "Log out now, so the tablet starts the desktop? Anything part-way is picked up again.",
      "Log out now");
  }
});
$("dev-desktop").addEventListener("click", () => leave("desktop", "Maintenance mode",
  "Log out to the plain desktop for one visit? DoubleTake stays closed there. Restart the tablet, or log out, to return to DoubleTake.", "Maintenance"));
$("dev-restart").addEventListener("click", () => leave("restart", "Restart the tablet",
  "Restart the tablet now? The second copy stops at a safe point and carries on after the restart.", "Restart"));
$("dev-shutdown").addEventListener("click", () => leave("shutdown", "Shut down the tablet",
  "Shut the tablet down now? The second copy stops at a safe point and carries on next time.", "Shut down"));
$("set-files").addEventListener("click", async () => { const r = await post("/api/open", { app: "files" }); if (r.error) ask("Files", r.error, "OK"); });
$("set-terminal").addEventListener("click", async () => { const r = await post("/api/open", { app: "terminal" }); if (r.error) ask("Terminal", r.error, "OK"); });

// ===== Reconfigure tablet ================================================================
// Settings, Device, Reconfigure: the main choices again, one page each, the same questions
// as the terminal setup (doubletake setup). Every step uses the same switches as Settings,
// so nothing here is new behaviour. Installing and the system part are done in the terminal.

let wiz = null;  // { step } while the wizard is open

function wizRow(title, text, ...controls) {
  const row = document.createElement("div");
  row.className = "setrow";
  const what = document.createElement("div");
  what.className = "what";
  const t = document.createElement("div"); t.className = "t"; t.textContent = title;
  const b = document.createElement("div"); b.className = "body"; b.textContent = text || "";
  what.append(t, b);
  row.append(what, ...controls);
  return row;
}
function wizButton(label, onClick, cls) {
  const b = document.createElement("button");
  b.type = "button"; b.className = "btn" + (cls ? " " + cls : ""); b.textContent = label;
  b.addEventListener("click", onClick);
  return b;
}
function wizToggle(on, onClick, disabled) {
  const b = document.createElement("button");
  b.type = "button"; b.className = "toggle" + (on ? " on" : "");
  const span = document.createElement("span"); span.textContent = on ? "On" : "Off";
  b.append(span); b.disabled = !!disabled;
  b.addEventListener("click", onClick);
  return b;
}
function wizSeg(options, current, onPick) {
  const seg = document.createElement("div");
  seg.className = "seg";
  for (const [value, label] of options) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = label; b.classList.toggle("on", value === current);
    b.addEventListener("click", () => onPick(value));
    seg.append(b);
  }
  return seg;
}
function wizStatus(text, ok) {
  const d = document.createElement("div");
  d.className = "wiz-status body" + (ok === true ? " ok" : ok === "bad" ? " bad" : "");
  d.textContent = text;
  return d;
}

const WIZ_STEPS = [
  {
    title: "Reconfigure tablet",
    lead: "The main choices again, one page each: the DoubleTake password, what to copy, Copy 2, the built-in NAS, " +
          "where it opens, the hotspot and how the tablet starts. Every one of them is also in Settings.",
    next: "Start",
    body: () => ((prefs.system_setup || {}).needed
      ? [wizStatus("Not set up yet: the NAS, the boot screen and the start-up switches need the system part. " +
                   "In a terminal: doubletake setup", "bad")]
      : []),
  },
  {
    title: "The DoubleTake password",
    lead: "Separate from the tablet's own login. It guards the ways out of DoubleTake (Maintenance mode, Exit) and the " +
          "jobs that cannot be undone (preparing a drive, Format, Reset, Erase). Everyday backups never ask for it.",
    body: () => {
      const mode = (prefs.fs_password || {}).mode;
      const said = mode === "password" ? "Set." : mode === "none" ? "No password: those jobs ask you to confirm, but anyone can confirm."
        : mode === "unknown" ? "It could not be checked just now." : "Not set yet.";
      const out = [wizRow("Password", said,
        wizButton(mode === "password" ? "Change" : "Set a password", async () => { await setPasswordFlow(); wizRender(); }, "solid"))];
      if (mode !== "password" && mode !== "none") {
        out[0].append(wizButton("No password", async () => {
          const r = await post("/api/fs-password/none", { current: "" });
          if (r.error) await ask("DoubleTake password", r.error, "OK");
          await loadSettings();
        }));
      }
      if (!(prefs.fs_password || {}).available) out.push(wizStatus("Not set up yet. In a terminal: doubletake setup"));
      return out;
    },
  },
  {
    title: "What to copy",
    lead: "Which files come off the camera. Whatever is left on the camera keeps its cards from being called safe to format, " +
          "so nothing is lost by accident.",
    body: () => [wizRow("Copy", ({ raw: "RAW files only.", raw_jpeg: "RAW files and JPEGs. No videos.", all: "Every photo and video." })[prefs.copy_what],
      wizSeg([["raw", "RAW only"], ["raw_jpeg", "RAW + JPEG"], ["all", "Everything"]], prefs.copy_what, (v) => change({ copy_what: v })))],
  },
  {
    title: "Copy 2",
    lead: "The second copy lives on a card or drive of its own: the microSD card in the tablet's slot, or a USB drive. " +
          "A new one is erased and kept for DoubleTake, after you confirm. Until there is a Copy 2, backups exist only on the tablet.",
    body: () => {
      const out = [wizRow("Where", prefs.second_copy_device === "usb" ? "A USB drive." : "The microSD card in the tablet's slot.",
        wizSeg([["microsd", "microSD card"], ["usb", "USB drive"]], prefs.second_copy_device, (v) => change({ second_copy_device: v })))];
      const k = S.card || {};
      const info = secondInfo();
      out.push(k.state === "ready" ? wizStatus("Ready: " + info.line + ".", true)
        : k.state === "new" ? wizStatus("A new " + (prefs.second_copy_device === "usb" ? "drive" : "card") + " is in. DoubleTake is asking about it.")
        : wizStatus((info.title ? info.title + ". " : "") + (info.text || "Put it in now, or later.")));
      return out;
    },
  },
  {
    title: "The built-in NAS",
    lead: "At home, use DoubleTake's built-in NAS to bring the photos onto your editing workstation, over Wi-Fi or a " +
          "network cable. Any photo app that imports from a network folder can read it. Read-only: nothing on the " +
          "workstation can change or delete the photos.",
    body: () => {
      const nasNow = S.nas || {};
      if (!nasNow.installed) return [wizStatus("Not set up yet. In a terminal: doubletake setup")];
      return [
        wizRow("1. Login for the NAS", nasNow.user
          ? "Your editing workstation logs in as " + nasNow.user + "."
          : "First, a name and password for the NAS alone: your workstation uses them to connect. " +
            "They are not the tablet's own login and cannot unlock the tablet.",
          wizButton(nasNow.user ? "Change" : "Set the login", () => $("nas-login").click(), nasNow.user ? "" : "solid")),
        wizRow("2. Share the photos", !nasNow.user ? "Set the login first: the NAS needs it before it can be switched on."
          : nasNow.nas_on ? "On. The photos are shared on the networks you trust (next screen)." : "Off.",
          wizToggle(nasNow.nas_on, () => post("/api/nas/switch", { what: "nas", on: !nasNow.nas_on }), !nasNow.user)),
      ];
    },
  },
  {
    title: "Where the share opens",
    lead: "Only on networks you trust: never on hotel, airport or café Wi-Fi.",
    body: () => {
      const out = [
        wizRow("A network cable", "Plug a network cable from the tablet straight into your editing workstation, with no router " +
          "needed: the two find each other in about 20 seconds. This is FSDC, DoubleTake's direct connection, and the " +
          "fastest way to bring a big shoot home. It also works through a router at home.", wizToggle(prefs.nas_cable, () => trust({ nas_cable: !prefs.nas_cable }))),
        wizRow("The tablet's hotspot", "Its own Wi-Fi network, made by the tablet.", wizToggle(prefs.nas_hotspot, () => trust({ nas_hotspot: !prefs.nas_hotspot }))),
      ];
      const ssid = S.net && S.net.ssid;
      if (ssid) {
        const on = prefs.nas_trusted.includes(ssid);
        out.push(wizRow("This Wi-Fi: " + ssid, "Trust it only if it is your own, at home.",
          wizToggle(on, () => trust({ nas_trusted: on ? prefs.nas_trusted.filter((v) => v !== ssid) : prefs.nas_trusted.concat([ssid]) }))));
      } else {
        out.push(wizStatus("To trust your home Wi-Fi, join it in Settings, Network, then switch it on in Settings, NAS."));
      }
      return out;
    },
  },
  {
    title: "The hotspot",
    lead: "The tablet can make a Wi-Fi network of its own, for a phone or a laptop in the field.",
    body: () => [
      wizRow("Name", prefs.hotspot_name, wizButton("Change", () => openKeyboard({
        label: "Hotspot name", value: prefs.hotspot_name, max: 32, any: true,
        done: (name) => { if (name && name.trim()) change({ hotspot_name: name.trim() }); },
      }))),
      wizRow("Password", prefs.hotspot_password ? "Set." : "Not set yet: at least 8 characters.", wizButton(prefs.hotspot_password ? "Change" : "Set", () => openKeyboard({
        label: "Hotspot password, at least 8 characters", value: "", max: 63, any: true, secret: true,
        done: (pw) => { if (pw && pw.length >= 8) change({ hotspot_password: pw }); else if (pw) ask("Hotspot", "At least 8 characters.", "OK"); },
      }))),
    ],
  },
  {
    title: "At power-on",
    lead: "How the tablet starts. For a tablet that lives in a camera bag, all on: power on, and DoubleTake is ready.",
    body: () => [
      wizRow("Start at power-on", !prefs.power_on_ready ? "Not set up yet. In a terminal: doubletake setup"
        : "Logs in by itself, with no password, and opens DoubleTake.",
        wizToggle(prefs.start_at_power_on, async () => {
          const on = !prefs.start_at_power_on;
          if (on && !await ask("Start at power-on",
            "Anyone who picks up the tablet can then use it without a password. The share keeps its own login. Switch it on?", "Switch on")) return;
          change({ start_at_power_on: on });
        }, !prefs.power_on_ready)),
      wizRow("Boot screen", !prefs.splash_available ? "Not set up yet. In a terminal: doubletake setup"
        : (prefs.splash_job || {}).busy ? "Being changed: a few minutes."
        : "The DoubleTake icon while the tablet starts, inside a turning orange and green ring.",
        wizToggle(prefs.splash_on, () => change({ boot_splash: !prefs.splash_on }),
          !prefs.splash_available || (prefs.splash_job || {}).busy)),
      wizRow("DoubleTake only", !prefs.kiosk_available ? "Not set up yet. In a terminal: doubletake setup"
        : "No desktop behind it: the tablet starts straight into DoubleTake. Maintenance mode gets the desktop back for one visit.",
        wizToggle(prefs.kiosk_on, () => change({ kiosk: !prefs.kiosk_on }), !prefs.kiosk_available)),
    ],
  },
  {
    title: "All set",
    lead: "Everything chosen here is saved, and is in Settings too.",
    next: "Done",
    body: () => [],
  },
];

// Live steps (Copy 2, the share) are drawn again only when what they show has changed,
// so a button is never swapped under a finger.
function wizLive() {
  const k = S.card || {}, nasNow = S.nas || {};
  const sig = JSON.stringify([k.state, k.label, k.free, nasNow.installed, nasNow.nas_on, nasNow.user, S.net && S.net.ssid]);
  if (sig === wiz.sig) return;
  wiz.sig = sig;
  wizRender();
}

async function openWizard() {
  if (!prefs) await loadSettings();
  if (!prefs) return;
  wiz = { step: 0 };
  show("wizard", true);
  wizRender();
}
async function closeWizard() {
  wiz = null;
  show("wizard", false);
  if (prefs && prefs.kiosk_on && !prefs.in_kiosk) {
    await leave("logout", "Start it now?",
      "Log out now, so the tablet starts DoubleTake only? Anything part-way is picked up again.", "Log out now");
  }
}
function wizRender() {
  if (!wiz || !prefs || !S) return;
  const step = WIZ_STEPS[wiz.step];
  setText("wiz-step", "Step " + (wiz.step + 1) + " of " + WIZ_STEPS.length);
  $("wiz-dots").replaceChildren(...WIZ_STEPS.map((_, i) => {
    const d = document.createElement("span");
    d.className = i === wiz.step ? "w-now" : i < wiz.step ? "w-past" : "";
    return d;
  }));
  setText("wiz-title", step.title);
  setText("wiz-lead", step.lead);
  $("wiz-body").replaceChildren(...step.body());
  $("wiz-back").hidden = wiz.step === 0;
  setText("wiz-next", step.next || "Next");
  $("wiz-skip").hidden = wiz.step === WIZ_STEPS.length - 1;
}
$("wiz-next").addEventListener("click", async () => {
  if (!wiz) return;
  if (wiz.step >= WIZ_STEPS.length - 1) { closeWizard(); return; }
  wiz.step += 1;
  wizRender();
});
$("wiz-back").addEventListener("click", () => { if (wiz && wiz.step > 0) { wiz.step -= 1; wizRender(); } });
$("wiz-skip").addEventListener("click", () => closeWizard());
$("set-wizard").addEventListener("click", openWizard);


// ===== the pop-up keyboard ============================================================
// Typing fields open this instead of the desktop's keyboard. A keyboard plugged into
// the tablet types into it too.

const KEY_ROWS = ["1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm"];
const SYMBOL_ROWS = ["1234567890", "!@#$%^&*()", "-_=+[]{};:", "'\",.<>/?|"];
const BACKSPACE = '<svg viewBox="0 0 24 24"><path d="M9 6h11v12H9l-6-6z"/><path d="M13 10l4 4M17 10l-4 4"/></svg>';
let kb = null;

function openKeyboard(options) {
  kb = { value: options.value || "", max: options.max || 60, done: options.done, cancel: options.cancel, upper: false,
         any: !!options.any, secret: !!options.secret, reveal: false, symbols: false };
  setText("kbd-label", options.label || "");
  setText("kbd-hint", options.hint || "");
  rebuildChips(options.chips || []);
  drawKeys();
  show("kbd", true);
}

function closeKeyboard(save) {
  if (!kb) return;
  const done = kb.done, cancel = kb.cancel;
  const value = kb.any ? kb.value : kb.value.trim();
  kb = null;
  show("kbd", false);
  if (save && done) done(value);
  else if (!save && cancel) cancel();
}

function shownValue() {
  return kb.secret && !kb.reveal ? "\u2022".repeat(kb.value.length) : kb.value;
}
function refreshText() { setText("kbd-text", shownValue()); }

function typeKey(ch) {
  if (!kb || kb.value.length >= kb.max) return;
  kb.value += ch;
  refreshText();
}

function rebuildChips(names) {
  $("kbd-chips").replaceChildren(...names.map((name) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "btn";
    chip.textContent = name;
    chip.addEventListener("click", () => { kb.value = name; refreshText(); });
    return chip;
  }));
  show("kbd-chips", names.length > 0);
}

function keyButton(label, onPress, extra, html) {
  const key = document.createElement("button");
  key.type = "button";
  key.className = "key" + (extra ? " " + extra : "");
  if (html) key.innerHTML = html; else key.textContent = label;
  // Act on the press, not the release, so fast typing is not lost.
  key.addEventListener("pointerdown", (event) => { event.preventDefault(); onPress(); });
  return key;
}

function drawKeys() {
  refreshText();
  const source = kb.symbols ? SYMBOL_ROWS : KEY_ROWS;
  const rows = source.map((letters, i) => {
    const row = document.createElement("div");
    row.className = "kbd-row";
    if (i === 3 && !kb.symbols) {
      row.append(keyButton(kb.upper ? "ABC" : "abc", () => { kb.upper = !kb.upper; drawKeys(); }, "wide" + (kb.upper ? " on" : "")));
    }
    for (const ch of letters) {
      const shown = kb.upper && !kb.symbols ? ch.toUpperCase() : ch;
      row.append(keyButton(shown, () => typeKey(shown)));
    }
    if (i === 3) {
      row.append(keyButton("", () => { kb.value = kb.value.slice(0, -1); refreshText(); }, "wide", BACKSPACE));
    }
    return row;
  });
  const last = document.createElement("div");
  last.className = "kbd-row";
  last.append(keyButton("Cancel", () => closeKeyboard(false), "wide"));
  if (kb.any) last.append(keyButton(kb.symbols ? "abc" : "#+=", () => { kb.symbols = !kb.symbols; drawKeys(); }, "wide"));
  if (kb.secret) last.append(keyButton(kb.reveal ? "Hide" : "Show", () => { kb.reveal = !kb.reveal; drawKeys(); }, "wide"));
  last.append(keyButton("Clear", () => { kb.value = ""; refreshText(); }, "wide"));
  if (!kb.any) last.append(keyButton("-", () => typeKey("-")), keyButton("_", () => typeKey("_")));
  last.append(
    keyButton("Space", () => typeKey(" "), "wide space"),
    keyButton("Done", () => closeKeyboard(true), "wide done"),
  );
  $("kbd-keys").replaceChildren(...rows, last);
}

document.addEventListener("keydown", (event) => {
  if (!kb) return;
  if (event.key === "Enter") closeKeyboard(true);
  else if (event.key === "Escape") closeKeyboard(false);
  else if (event.key === "Backspace") { kb.value = kb.value.slice(0, -1); refreshText(); }
  else if (event.key.length === 1 && (kb.any || /[A-Za-z0-9 ._-]/.test(event.key))) typeKey(event.key);
  else return;
  event.preventDefault();
  event.stopPropagation();
}, true);

setInterval(poll, 700);
poll();
