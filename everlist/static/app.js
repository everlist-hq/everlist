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

const TRANSCRIPT_CAP = 50000; /* chars of live transcript (~12k tokens; the
  brain itself is stateless per message, so this bounds the DOM session) */
let capNoticeShown = false;

function transcriptChars() {
  let n = 0;
  chat.querySelectorAll(".bubble").forEach((b) => { n += (b.textContent || "").length; });
  return n;
}

function trimTranscript() {
  let total = transcriptChars();
  if (total <= TRANSCRIPT_CAP) return;
  for (const m of Array.from(chat.children)) {
    if (total <= TRANSCRIPT_CAP) break;
    const b = m.querySelector(".bubble");
    total -= (b && b.textContent ? b.textContent.length : 0) + 16;
    m.remove();
  }
  if (!capNoticeShown) {
    capNoticeShown = true;
    const wrap = document.createElement("div");
    wrap.className = "msg think";
    const b = document.createElement("div");
    b.className = "bubble";
    b.textContent = "\u00b7 older messages trimmed to keep the session fast";
    wrap.appendChild(b);
    chat.insertBefore(wrap, chat.firstChild);
  }
}

/* ---------- chat (unchanged brain wiring) ---------- */
function addMsg(cls, text) {
  const wrap = document.createElement("div");
  wrap.className = "msg " + cls;
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text; /* XSS-safe by construction */
  wrap.appendChild(bubble);
  chat.appendChild(wrap);
  trimTranscript();
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
      if (n != null) {
        send("book " + n);
      } else {
        /* featured card: prefill with the listing id — the brain resolves ids,
           not titles; the user just adds their name and sends */
        input.value = "book " + (l.id || "") + " ";
        input.focus();
        autosize();
      }
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

/* ---------- smart expansion: grow/shrink with a no-overlap invariant ----------
   Math: every animated box stays inside its own final footprint, so nothing
   can ever cover a neighbor.
   - The hero card (opening/closing) animates its REAL width/height (layout
     box, no transform scale — text never smears) from old rect to new. Its
     animated box is always within the union of old+new footprint.
   - Every other card moves rigidly: translate-only, same easing. Pushed
     neighbors share the hero's delta per grid lane, so the clearance between
     the moving edge and the hero stays constant (= grid gap) at every frame.
   - If the hero's top-left jumps (row crossing), it fades in place instead
     of flying — no motion, no overlap. */
const EASE = { duration: 340, easing: "cubic-bezier(.2,.7,.2,1)" };

function animateBoardChange(mutate, hero) {
  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const before = new Map();
  Array.from(grid.children).forEach((c) => before.set(c, c.getBoundingClientRect()));
  mutate();
  Array.from(grid.children).forEach((c) => {
    const f = before.get(c);
    if (!f) return;
    const l = c.getBoundingClientRect();
    if (hero && c === hero) {
      if (reduce) return;
      const jumped = Math.abs(f.left - l.left) > 2 || Math.abs(f.top - l.top) > 2;
      if (jumped) { c.animate([{ opacity: 0.35 }, { opacity: 1 }], { duration: 180 }); return; }
      // grow/shrink the real box; content reflows inside, never overlaps
      c.animate(
        [{ width: f.width + "px", height: f.height + "px" },
         { width: l.width + "px", height: l.height + "px" }],
        EASE
      );
      return;
    }
    if (reduce) return;
    const dx = f.left - l.left, dy = f.top - l.top;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
    c.animate(
      [{ transform: "translate(" + dx + "px," + dy + "px)" },
       { transform: "none" }],
      EASE
    );
  });
}

function expandCard(card) {
  animateBoardChange(() => {
    if (openCard && openCard !== card) openCard.classList.remove("open");
    card.classList.add("open");
    openCard = card;
  }, card);
}

function collapseCard() {
  if (!openCard) return;
  const c = openCard;
  openCard = null;
  animateBoardChange(() => c.classList.remove("open"), c);
}

grid.addEventListener("click", (e) => {
  if (e.target.closest(".cta")) return; /* CTA routes to chat itself */
  const card = e.target.closest(".card");
  if (!card) { collapseCard(); return; }
  if (openCard === card) collapseCard();
  else expandCard(card);
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") collapseCard(); });

/* ---------- W1 dashboard: bookings + orders (tokens stay server-side) ---------- */
const browseSec = document.getElementById("browse");
const dashSec = document.getElementById("dash");
const navBrowse = document.getElementById("nav-browse");
const navDash = document.getElementById("nav-dash");
const dashAcct = document.getElementById("dash-acct");
const dashLogin = document.getElementById("dash-login");
const myBookings = document.getElementById("mybookings");
const myOrders = document.getElementById("myorders");
const dashRefresh = document.getElementById("dash-refresh");
const dashLogout = document.getElementById("dash-logout");

const ESCROW_LABEL = { HELD: "escrow held", WAIVED: "free (no payment)", RELEASED: "released to owner", REFUNDED: "refunded", DIRECT: "instant — settled" };

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function showView(dash) {
  browseSec.hidden = dash;
  dashSec.hidden = !dash;
  navBrowse.classList.toggle("on", !dash);
  navDash.classList.toggle("on", dash);
  if (dash) loadDashboard();
}
if (navBrowse) navBrowse.addEventListener("click", (e) => { e.preventDefault(); showView(false); });
if (navDash) navDash.addEventListener("click", (e) => { e.preventDefault(); showView(true); });

function escBadge(state) {
  return el("span", "escbadge e-" + String(state || "x").toLowerCase(), ESCROW_LABEL[state] || String(state || "?"));
}

function when(ts) {
  const d = new Date((ts || 0) * 1000);
  return isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function bookingCard(b) {
  const card = el("div", "card dashcard");
  const r1 = el("div", "r1");
  r1.appendChild(el("span", "vtag", String(b.vertical || "booking").toUpperCase()));
  r1.appendChild(escBadge(b.escrow));
  r1.appendChild(el("span", "flex"));
  r1.appendChild(el("span", "meta", when(b.created)));
  card.appendChild(r1);
  card.appendChild(el("h3", null, b.title || b.listing_id));
  const foot = el("div", "foot");
  foot.appendChild(el("span", "price", b.amount ? "\u20ac" + b.amount : "Free"));
  foot.appendChild(el("span", "esc", "#" + b.id));
  if (b.can_cancel) {
    foot.appendChild(el("span", "flex"));
    const btn = el("button", "cta danger", "Cancel & refund");
    btn.type = "button";
    btn.addEventListener("click", () => act("/api/cancel", b.id));
    foot.appendChild(btn);
  }
  card.appendChild(foot);
  return card;
}

function orderCard(o) {
  const card = el("div", "card dashcard");
  const r1 = el("div", "r1");
  r1.appendChild(el("span", "vtag", "ORDER"));
  r1.appendChild(escBadge(o.escrow));
  r1.appendChild(el("span", "flex"));
  r1.appendChild(el("span", "meta", when(o.created)));
  card.appendChild(r1);
  card.appendChild(el("h3", null, o.title || o.listing_id));
  const foot = el("div", "foot");
  foot.appendChild(el("span", "price", o.amount ? "\u20ac" + o.amount : "Free"));
  foot.appendChild(el("span", "esc", "#" + o.id));
  if (o.can_confirm) {
    foot.appendChild(el("span", "flex"));
    const btn = el("button", "cta", "Confirm & release");
    btn.type = "button";
    btn.addEventListener("click", () => act("/api/confirm", o.id));
    foot.appendChild(btn);
  }
  card.appendChild(foot);
  return card;
}

async function act(url, id) {
  openChat();
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ booking_id: id }),
    });
    const data = await res.json().catch(() => ({}));
    if (data.ok) {
      addMsg("agent", "\u2705 Booking " + id + " \u2014 escrow is now " + (data.escrow || "updated")
        + (data.owner_received != null ? (". Owner receives \u20ac" + data.owner_received) : "."));
    } else {
      addMsg("err", "\u26a0\ufe0f " + (data.error || "action failed"));
    }
  } catch (e) {
    addMsg("err", "network error \u2014 is the hub up?");
  }
  loadDashboard();
}

function renderLogin() {
  dashLogin.innerHTML = "";
  const p = el("p", null, "Log in with your account seed and your bookings + orders appear here. No seed? Say ");
  p.appendChild(el("b", null, "signup"));
  p.appendChild(document.createTextNode(" in the chat to create an account in one message."));
  const form = el("form", "loginrow");
  form.setAttribute("autocomplete", "off");
  const inp = el("input", "loginseed");
  inp.type = "password"; /* masked; the seed is never displayed or echoed */
  inp.placeholder = "paste your account seed (shown once at signup)";
  inp.maxLength = 80;
  const btn = el("button", "cta", "Log in");
  btn.type = "submit";
  form.appendChild(inp);
  form.appendChild(btn);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const seed = inp.value.trim();
    if (!seed || btn.disabled) return;
    btn.disabled = true;
    try {
      const res = await fetch("/api/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ seed }),
      });
      const data = await res.json().catch(() => ({}));
      if (data.ok) {
        inp.value = "";
        loadDashboard();
      } else {
        addMsg("err", "\u26a0\ufe0f " + (data.error || "login failed"));
      }
    } catch (e2) {
      addMsg("err", "network error \u2014 is the hub up?");
    }
    btn.disabled = false;
  });
  dashLogin.appendChild(p);
  dashLogin.appendChild(form);
}

async function loadDashboard() {
  let data;
  try {
    const r = await fetch("/api/dashboard", { cache: "no-store" });
    data = await r.json();
  } catch (e) {
    dashAcct.textContent = "offline";
    return;
  }
  myBookings.innerHTML = "";
  myOrders.innerHTML = "";
  const acct = data.account;
  dashLogout.hidden = !acct;
  if (!acct || data.expired) {
    dashAcct.textContent = "";
    if (!dashLogin.childNodes.length) renderLogin();
    dashLogin.hidden = false;
    myBookings.appendChild(el("p", "empty", "Log in to see your bookings."));
    myOrders.appendChild(el("p", "empty", "Orders for your listings appear here."));
    return;
  }
  dashLogin.hidden = true;
  dashAcct.textContent = acct.account_id + (acct.verified ? " \u00b7 human-verified" : "");
  if (!data.bookings.length) myBookings.appendChild(el("p", "empty", "No bookings yet \u2014 search in the chat, then say \u201cbook 1\u201d."));
  data.bookings.forEach((b) => myBookings.appendChild(bookingCard(b)));
  if (!data.orders.length) myOrders.appendChild(el("p", "empty", "No orders yet \u2014 orders for your listings appear here."));
  data.orders.forEach((o) => myOrders.appendChild(orderCard(o)));
}

if (dashRefresh) dashRefresh.addEventListener("click", loadDashboard);
if (dashLogout) dashLogout.addEventListener("click", async () => {
  try { await fetch("/api/logout", { method: "POST" }); } catch (e) {}
  loadDashboard();
});

/* ---------- boot ---------- */
if (window.innerWidth < 760) document.body.classList.add("min"); /* mobile: pill by default, tap to open */
pollHealth();
setInterval(pollHealth, 15000);
loadListings();
input.focus();
