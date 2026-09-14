/* EverList webchat — CSP-safe: no eval, no inline handlers, textContent-only rendering.
   Chat is the main interaction (centered dock, minimises to a corner pill).
   A manual Discover grid renders real hub listings via read-only /api/listings. */
"use strict";

const chat = document.getElementById("chat");
const form = document.getElementById("form");
const input = document.getElementById("text");
const sendBtn = document.getElementById("send");
const dot = document.getElementById("dot");
const statusText = document.getElementById("status-text");
const chips = document.getElementById("chips");
const resetBtn = document.getElementById("reset");

const grid = document.getElementById("grid");
const countEl = document.getElementById("count");
const emptyEl = document.getElementById("empty");
const filters = document.getElementById("filters");
const minBtn = document.getElementById("minbtn");
const pill = document.getElementById("pill");
const postBtn = document.getElementById("postBtn") || document.getElementById("postbtn");
const navPost = document.getElementById("nav-post");

let busy = false;
let ALL = [];           // cached listings
let FILTER = "all";

/* ---------- chat (unchanged brain wiring) ---------- */
function addMsg(cls, text) {
  const wrap = document.createElement("div");
  wrap.className = "msg " + cls;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text; /* XSS-safe by construction */
  wrap.appendChild(bubble);
  chat.appendChild(wrap);
  chat.scrollTop = chat.scrollHeight;
  return bubble;
}

function setBusy(b) { busy = b; sendBtn.disabled = b; input.disabled = b; }

function openChat() { document.body.classList.remove("min"); }

async function send(text) {
  text = (text || "").trim();
  if (!text || busy) return;
  addMsg("user", text);
  input.value = "";
  autosize();
  setBusy(true);
  const think = addMsg("think", "&#8230;");
  think.firstChild.textContent = "\u2026";
  const t0 = Date.now();
  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const dt = ((Date.now() - t0) / 1000).toFixed(1);
    if (res.status === 429) {
      const wait = parseInt(res.headers.get("Retry-After") || "5", 10);
      think.remove();
      addMsg("err", "rate limited \u2014 try again in " + wait + "s");
    } else {
      const data = await res.json().catch(() => ({}));
      think.remove();
      addMsg("agent", data.reply || "(empty response)");
      const meta = document.createElement("div");
      meta.className = "msg think";
      const m = document.createElement("div");
      m.className = "bubble";
      m.textContent = "\u00b7 " + dt + "s";
      meta.appendChild(m);
      chat.appendChild(meta);
      chat.scrollTop = chat.scrollHeight;
    }
    setStatus(true);
  } catch (e) {
    think.remove();
    addMsg("err", "network error \u2014 is the hub up?");
    setStatus(false);
  } finally {
    setBusy(false);
    input.focus();
  }
}

function setStatus(ok) {
  dot.className = "dot " + (ok ? "ok" : "down");
  statusText.textContent = ok ? "online" : "offline";
}

async function pollHealth() {
  try {
    const r = await fetch("/api/health", { cache: "no-store" });
    const d = await r.json();
    setStatus(!!d.ok);
  } catch (e) { setStatus(false); }
}

function autosize() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 180) + "px";
}

form.addEventListener("submit", (e) => { e.preventDefault(); send(input.value); });
input.addEventListener("input", autosize);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input.value); }
});
chips.addEventListener("click", (e) => {
  const b = e.target.closest(".chip");
  if (b && b.dataset.q) { openChat(); send(b.dataset.q); }
});
resetBtn.addEventListener("click", async () => {
  try { await fetch("/api/reset", { method: "POST" }); } catch (e) {}
  location.reload();
});

/* ---------- chat dock minimise / pill ---------- */
if (minBtn) minBtn.addEventListener("click", () => { document.body.classList.add("min"); });
if (pill) pill.addEventListener("click", () => { openChat(); input.focus(); });

/* ---------- post-a-listing affordances ---------- */
function startPost() {
  openChat();
  input.value = "list ";
  input.focus();
  autosize();
}
if (postBtn) postBtn.addEventListener("click", startPost);
if (navPost) navPost.addEventListener("click", (e) => { e.preventDefault(); startPost(); });

/* ---------- Discover grid (real data, CSP-safe DOM) ---------- */
const MONTHS = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];

function fmtDate(d) {
  if (!d) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(d));
  if (!m) return { big: String(d).slice(0, 6), small: "" };
  return { big: m[3], small: MONTHS[(+m[2]) - 1] || "" };
}

function priceOf(l) {
  const p = (l.price != null ? l.price : l.amount);
  const n = parseFloat(p);
  if (isNaN(n)) return { txt: p ? String(p) : "\u2014", esc: false };
  if (n === 0) return { txt: "Free", esc: true };
  return { txt: "\u20ac" + (Number.isInteger(n) ? n : n.toFixed(2)), esc: true };
}

function spotsLeft(l) {
  const cap = parseInt(l.capacity, 10);
  const reg = parseInt(l.registered != null ? l.registered : l.booked, 10);
  if (!isNaN(cap) && cap > 0) {
    const left = (!isNaN(reg) ? Math.max(0, cap - reg) : cap);
    return left + " / " + cap + " spots";
  }
  if (!isNaN(cap) && cap === 0) return "on request";
  return null;
}

function matchFilter(l, f) {
  if (f === "all") return true;
  const hay = [l.vertical, l.category].join(" ").toLowerCase();
  const tags = (l.tags || []).join(" ").toLowerCase();
  if (f === "classes") return /class|workshop|course|lesson/.test(hay + " " + tags);
  return hay.indexOf(f) >= 0 || tags.indexOf(f) >= 0;
}

function makeCard(l, featured) {
  const card = document.createElement("article");
  card.className = "card" + (featured ? " feat" : "");

  const r1 = document.createElement("div"); r1.className = "r1";
  const dt = fmtDate(l.date);
  if (dt) {
    const date = document.createElement("span"); date.className = "date";
    const b = document.createElement("b"); b.textContent = dt.big;
    const s = document.createElement("s"); s.textContent = dt.small;
    date.appendChild(b); date.appendChild(s); r1.appendChild(date);
  } else {
    const date = document.createElement("span"); date.className = "date";
    const b = document.createElement("b"); b.textContent = "\u221e";
    const s = document.createElement("s"); s.textContent = "any";
    date.appendChild(b); date.appendChild(s); r1.appendChild(date);
  }
  const vtag = document.createElement("span"); vtag.className = "vtag";
  vtag.textContent = ((l.category || l.vertical || "listing").toUpperCase());
  r1.appendChild(vtag);
  if (featured) {
    const sp = spotsLeft(l);
    if (sp) { const cap = document.createElement("span"); cap.className = "cap"; cap.textContent = sp; r1.appendChild(cap); }
  }
  card.appendChild(r1);

  const h3 = document.createElement("h3"); h3.textContent = l.title || "Untitled"; card.appendChild(h3);

  if (featured && l.description) {
    const desc = document.createElement("p"); desc.className = "desc";
    desc.textContent = String(l.description).slice(0, 220) + (String(l.description).length > 220 ? "\u2026" : "");
    card.appendChild(desc);
  }

  const metaBits = [];
  if (l.location) metaBits.push(l.location);
  const sp2 = spotsLeft(l);
  if (!featured && sp2) metaBits.push(sp2);
  if (l.owner_public) metaBits.push(l.owner_public);
  if (metaBits.length) { const meta = document.createElement("div"); meta.className = "meta"; meta.textContent = metaBits.join(" \u00b7 "); card.appendChild(meta); }

  const foot = document.createElement("div"); foot.className = "foot";
  const pr = priceOf(l);
  const price = document.createElement("span"); price.className = "price"; price.textContent = pr.txt; foot.appendChild(price);
  const esc = document.createElement("span"); esc.className = "esc"; esc.textContent = pr.esc ? "escrow" : "no payment"; foot.appendChild(esc);
  if (featured) {
    const flex = document.createElement("span"); flex.className = "flex"; foot.appendChild(flex);
    const cta = document.createElement("button"); cta.className = "cta"; cta.type = "button";
    cta.textContent = "Ask AI to book";
    cta.addEventListener("click", () => { openChat(); input.value = "book " + (l.title || ""); input.focus(); autosize(); });
    foot.appendChild(cta);
  }
  card.appendChild(foot);
  return card;
}

function renderGrid() {
  grid.innerHTML = "";
  const list = ALL.filter((l) => matchFilter(l, FILTER));
  list.forEach((l, i) => grid.appendChild(makeCard(l, i === 0)));
  countEl.textContent = ALL.length + (ALL.length === 1 ? " live listing" : " live listings");
  emptyEl.hidden = list.length > 0;
  grid.hidden = list.length === 0;
}

async function loadListings() {
  try {
    const r = await fetch("/api/listings?limit=48", { cache: "no-store" });
    const d = await r.json();
    ALL = d.listings || d.results || (Array.isArray(d) ? d : []);
    renderGrid();
  } catch (e) {
    countEl.textContent = "hub offline";
    emptyEl.hidden = false;
  }
}

if (filters) filters.addEventListener("click", (e) => {
  const b = e.target.closest(".chip");
  if (!b) return;
  FILTER = b.dataset.f || "all";
  filters.querySelectorAll(".chip").forEach((c) => c.classList.toggle("on", c === b));
  renderGrid();
});

/* ---------- boot ---------- */
if (window.innerWidth < 760) document.body.classList.add("min"); /* mobile: pill by default, tap to open */
pollHealth();
setInterval(pollHealth, 15000);
loadListings();
input.focus();
