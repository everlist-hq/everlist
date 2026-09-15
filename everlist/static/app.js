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
let ALL = [];           // default board: all live listings
let BOARD = null;       // chat results mode: listing dicts in 'book <n>' order; null = default board
let SIG = "";           // signature of rendered chat results (skips no-op re-renders)
let openCard = null;    // currently expanded card element

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
      // chat-first board: search replies open their results on screen
      if (Array.isArray(data.results)) {
        const sig = data.results.map((l) => l.id).join(",");
        if (sig !== SIG || BOARD == null) {
          SIG = sig;
          BOARD = data.results.length ? data.results : null;
          renderGrid();
          if (BOARD) {
            grid.classList.remove("boardin");
            void grid.offsetWidth; // restart entrance animation
            grid.classList.add("boardin");
          }
        }
      }
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

function makeCard(l, featured, n) {
  const card = document.createElement("article");
  card.className = "card" + (featured ? " feat" : "");
  if (n != null) card.dataset.idx = String(n);

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
  if (n != null) {
    const bN = document.createElement("span"); bN.className = "bkn";
    bN.textContent = "book " + n;
    r1.appendChild(bN);
  }
  if (featured) {
    const sp = spotsLeft(l);
    if (sp) { const cap = document.createElement("span"); cap.className = "cap"; cap.textContent = sp; r1.appendChild(cap); }
  }
  card.appendChild(r1);

  const h3 = document.createElement("h3"); h3.textContent = l.title || "Untitled"; card.appendChild(h3);

  if (l.description) {
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
  if (featured || n != null) {
    const flex = document.createElement("span"); flex.className = "flex"; foot.appendChild(flex);
    const cta = document.createElement("button"); cta.className = "cta"; cta.type = "button";
    cta.textContent = n != null ? ("Book \u00b7 " + n) : "Ask AI to book";
    cta.addEventListener("click", (ev) => {
      ev.stopPropagation();
      openChat();
      send(n != null ? ("book " + n) : ("book " + (l.title || "")));
    });
    foot.appendChild(cta);
  }
  card.appendChild(foot);
  return card;
}

function renderGrid() {
  openCard = null;
  grid.innerHTML = "";
  if (BOARD) {
    BOARD.forEach((l, i) => grid.appendChild(makeCard(l, false, i + 1)));
    countEl.textContent = BOARD.length + " result" + (BOARD.length === 1 ? "" : "s") + " \u00b7 from your chat";
  } else {
    ALL.forEach((l, i) => grid.appendChild(makeCard(l, i === 0)));
    countEl.textContent = ALL.length + (ALL.length === 1 ? " live listing" : " live listings");
  }
  const list = BOARD || ALL;
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
  if (b.dataset.reset) {
    BOARD = null;
    SIG = "";
    grid.classList.remove("boardin");
    renderGrid();
    return;
  }
  if (b.dataset.q) { openChat(); send(b.dataset.q); }
});

/* ---------- smart expansion: clicked card grows, others glide aside ----------
   FLIP over every card: measure before, mutate, invert, play. The expanded
   card spans two columns (real growth); neighbors keep their exact size and
   only translate — they never shrink, and motion stays proportional. */
function flipAnimate(mutate) {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const before = new Map();
  Array.from(grid.children).forEach((c) => before.set(c, c.getBoundingClientRect()));
  mutate();
  if (reduce) return;
  Array.from(grid.children).forEach((c) => {
    const f = before.get(c);
    if (!f) return;
    const l = c.getBoundingClientRect();
    const dx = f.left - l.left, dy = f.top - l.top;
    const sx = f.width / l.width, sy = f.height / l.height;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1 && Math.abs(1 - sx) < 0.01 && Math.abs(1 - sy) < 0.01) return;
    c.animate(
      [{ transformOrigin: "top left", transform: "translate(" + dx + "px," + dy + "px) scale(" + sx + "," + sy + ")" },
       { transformOrigin: "top left", transform: "none" }],
      { duration: 340, easing: "cubic-bezier(.2,.7,.2,1)" }
    );
  });
}

function expandCard(card) {
  flipAnimate(() => {
    if (openCard && openCard !== card) openCard.classList.remove("open");
    card.classList.add("open");
    openCard = card;
  });
}

function collapseCard() {
  if (!openCard) return;
  const c = openCard;
  openCard = null;
  flipAnimate(() => c.classList.remove("open"));
}

grid.addEventListener("click", (e) => {
  if (e.target.closest(".cta")) return; /* CTA routes to chat itself */
  const card = e.target.closest(".card");
  if (!card) { collapseCard(); return; }
  if (openCard === card) collapseCard();
  else expandCard(card);
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") collapseCard(); });

/* ---------- boot ---------- */
if (window.innerWidth < 760) document.body.classList.add("min"); /* mobile: pill by default, tap to open */
pollHealth();
setInterval(pollHealth, 15000);
loadListings();
input.focus();
