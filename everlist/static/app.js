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
      // Brain v2 nav: the chat can move you around the site for real
      if (data.nav === "dash") {
        showView(true);
      } else if (data.nav === "home") {
        BOARD = null; SIG = "";
        renderGrid();
        showView(false);
      } else if (data.nav === "results" && openDetailCard) {
        closeDetail(false);
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
/* ---------- W3: post wizard (composes the brain's own rich list text) ---------- */
const WIZ_IDS = ["title", "price", "date", "loc", "cat", "cap", "tags", "url", "desc", "rail", "refund"];

function wizVals() {
  const v = {};
  WIZ_IDS.forEach((k) => { const n = document.getElementById("w-" + k); if (n) v[k] = n.value.trim(); });
  return v;
}

function composeListing(f) {
  const L = ["list", "title: " + f.title, "price: " + f.price];
  if (f.date) L.push("date: " + f.date);
  if (f.loc) L.push("location: " + f.loc);
  if (f.cat) L.push("category: " + f.cat);
  if (f.cap) L.push("capacity: " + f.cap);
  if (f.tags) L.push("tags: " + f.tags);
  if (f.url) L.push("url: " + f.url);
  if (f.desc) L.push("description: " + f.desc);
  L.push("rail: " + (f.rail || "escrow"));
  if ((f.rail || "escrow") === "escrow" && f.refund) L.push("refund_window: " + f.refund);
  return L.join("\n");
}

function updatePreview() {
  const pre = document.getElementById("w-preview");
  if (!pre) return;
  const f = wizVals();
  pre.textContent = (f.title && f.price !== "") ? composeListing(f) : "(fill title and price to see the message)";
}

function startPost(prefill) {
  showView("post");
  const f = prefill || {};
  const map = { title: "w-title", price: "w-price", date: "w-date", loc: "w-loc", cat: "w-cat", cap: "w-cap", tags: "w-tags", url: "w-url", desc: "w-desc", rail: "w-rail", refund: "w-refund" };
  Object.keys(map).forEach((k) => { const n = document.getElementById(map[k]); if (n) n.value = (f[k] != null) ? String(f[k]) : (k === "refund" ? "72" : ""); });
  updatePreview();
}
if (postBtn) postBtn.addEventListener("click", () => startPost());
if (navPost) navPost.addEventListener("click", (e) => { e.preventDefault(); startPost(); });
const wizForm = document.getElementById("wiz");
if (wizForm) {
  wizForm.addEventListener("input", updatePreview);
  wizForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const f = wizVals();
    if (!f.title || f.price === "") return;
    const text = composeListing(f);
    showView(false); /* back to browse; the dock carries the brain's reply */
    openChat();
    send(text);
  });
}

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
  card.dataset.listing = JSON.stringify(l);
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

  /* W4: honest ratings from the hub's S6 aggregates — paid reviews are
     amount-weighted server-side, free-class feedback is a separate channel.
     No reviews -> render nothing; we never fake stars. */
  const rateBits = [];
  if ((l.rating_count | 0) > 0) {
    let avg = 0;
    if ((l.rating_wtot || 0) > 0) avg = (l.rating_wsum || 0) / l.rating_wtot;
    else avg = (l.rating_avg != null) ? l.rating_avg : ((l.rating_sum || 0) / l.rating_count);
    rateBits.push("\u2605 " + (Math.round(avg * 10) / 10) + "/5 (" + l.rating_count + ")");
  }
  if ((l.free_rating_count | 0) > 0) {
    rateBits.push("free-class " + (Math.round((l.free_rating_sum || 0) / l.free_rating_count * 10) / 10) + "/5 (" + l.free_rating_count + ")");
  }
  if (rateBits.length) { const rl = document.createElement("div"); rl.className = "rline"; rl.textContent = rateBits.join(" \u00b7 "); card.appendChild(rl); }

  const foot = document.createElement("div"); foot.className = "foot";
  const pr = priceOf(l);
  const price = document.createElement("span"); price.className = "price"; price.textContent = pr.txt; foot.appendChild(price);
  /* W2: shareable, indexable detail page (server-rendered, no-JS surface) */
  if (l.id) {
    const dl = document.createElement("a"); dl.className = "dlink";
    dl.href = "/l/" + encodeURIComponent(l.id);
    dl.target = "_blank"; dl.rel = "noopener";
    dl.textContent = "details";
    foot.appendChild(dl);
  }
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
  resetBoardState();
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

/* ---------- board motion: everything slides, nothing fades ----------
   No card may ever appear or disappear during a reflow: every moving card
   is one rigid translate. Transform-only (compositor) — no width/height
   animation anywhere, so a reflow never touches layout per frame. */
const EASE = "cubic-bezier(.2,.7,.2,1)";
const REDUCE = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

function slideBack(f, l, c) {
  const dx = f.left - l.left, dy = f.top - l.top;
  if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
  if (REDUCE()) return;
  c.animate(
    [{ transform: "translate(" + dx + "px," + dy + "px)" },
     { transform: "none" }],
    { duration: 340, easing: EASE }
  );
}

function animateBoardChange(mutate) {
  const before = new Map();
  Array.from(grid.children).forEach((c) => before.set(c, c.getBoundingClientRect()));
  mutate();
  Array.from(grid.children).forEach((c) => {
    const f = before.get(c);
    if (!f) return;                 // inserted node: panel animates itself
    slideBack(f, c.getBoundingClientRect(), c);
  });
}

/* ---------- detail panel: one click = the full /l/{id} detail, row ABOVE ----------
   Owner call (2026-09-17, v3): in-grid full-row panel like the previous
   implementation — but it opens in a row ABOVE the clicked card, not below.
   Rows above stay put; the clicked row (and everything below it) slides
   down; the clicked slot becomes a dashed ghost hole. The panel grows
   straight out of the clicked card into its slot above. On close it fades
   FAST (half gone by 40% of the run) BEFORE shrinking back into the card —
   the end-of-shrink flash is structurally impossible. Close paths: x
   button, Escape, click on the hole, click on empty panel background,
   browser back. Switching listings swaps the panel inside ONE FLIP step
   and replaces the history entry so the back stack stays clean. /l/{id}
   stays a real server page for crawlers, no-JS visitors and deep links —
   in-app we never navigate. */
let openDetailCard = null;   // card currently holed
let detailPanel = null;      // panel element (in flow while open)

function cardListing(card) {
  try { return JSON.parse(card.dataset.listing || "null"); } catch (e) { return null; }
}

function killStrayPanels() {
  /* Bulletproof hygiene: no frozen/animating panel may ever survive a state
     change — it would float over the board and swallow clicks. */
  document.querySelectorAll(".detail-panel").forEach((p) => p.remove());
  detailPanel = null;
}

function resetBoardState() {
  /* Board re-rendered (search/filter): any open detail is torn down silently
     and the URL returns home. Called by renderGrid BEFORE innerHTML clears. */
  openDetailCard = null;
  killStrayPanels();
  if (history.state && history.state.detail) history.replaceState(null, "", "/");
}

function rowsOfGrid() {
  const rows = [];
  let cur = null, top = -1e9;
  Array.from(grid.children).forEach((el) => {
    if (el.classList && el.classList.contains("card")) {
      const t = el.offsetTop;
      if (!cur || Math.abs(t - top) > 2) { cur = []; rows.push(cur); top = t; }
      cur.push(el);
    }
  });
  return rows;
}

function boardData() {
  return (typeof BOARD !== "undefined" && BOARD ? BOARD : (typeof ALL !== "undefined" ? ALL : []));
}

function fmtDateLong(d) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(d || ""));
  if (!m) return String(d || "any date");
  return (+m[3]) + " " + (MONTHS[(+m[2]) - 1] || m[2]) + " " + m[1];
}

/* Content parity with the SSR /l/{id} page (pages.py listing_html): same
   data, same classes (.lmeta .chip .esc.note .lcta .lnote), so the shared
   stylesheet renders both identically. CSP-safe: textContent everywhere. */
function buildDetailPanel(l) {
  const d = document.createElement("article");
  d.className = "detail-panel ldetail";

  const close = document.createElement("button");
  close.className = "dclose"; close.type = "button"; close.setAttribute("aria-label", "close");
  close.textContent = "\u00d7";
  close.addEventListener("click", (ev) => { ev.stopPropagation(); closeDetail(false); });
  d.appendChild(close);

  const price = priceOf(l);
  const metaBits = [l.vertical || l.category || "", fmtDateLong(l.date), price.txt, l.location || ""].filter(Boolean);
  const meta = document.createElement("div"); meta.className = "lmeta"; meta.textContent = metaBits.join(" \u00b7 "); d.appendChild(meta);

  const h2 = document.createElement("h2"); h2.className = "dtitle"; h2.textContent = l.title || "Untitled"; d.appendChild(h2);

  const cap = parseInt(l.capacity, 10);
  if (!isNaN(cap) && cap > 0) {
    const reg = parseInt(l.registered != null ? l.registered : l.booked, 10) || 0;
    const left = Math.max(0, cap - reg);
    const spots = document.createElement("div"); spots.className = "lmeta";
    spots.textContent = left + " of " + cap + " spots left";
    d.appendChild(spots);
  }

  if (l.description) { const de = document.createElement("p"); de.className = "ldesc"; de.textContent = String(l.description); d.appendChild(de); }

  const tags = l.tags || [];
  if (tags.length) {
    const tw = document.createElement("div");
    tags.slice(0, 8).forEach((t) => { const c = document.createElement("span"); c.className = "chip stat"; c.textContent = t; tw.appendChild(c); });
    d.appendChild(tw);
  }

  const rateBits = [];
  if ((l.rating_count | 0) > 0) {
    let avg = (l.rating_wtot || 0) > 0 ? (l.rating_wsum || 0) / l.rating_wtot
            : (l.rating_avg != null ? l.rating_avg : (l.rating_sum || 0) / l.rating_count);
    rateBits.push("\u2605 " + (Math.round(avg * 10) / 10) + "/5 paid reviews (" + l.rating_count + ")");
  }
  if ((l.free_rating_count | 0) > 0) {
    rateBits.push("free-class " + (Math.round((l.free_rating_sum || 0) / l.free_rating_count * 10) / 10) + "/5 (" + l.free_rating_count + ")");
  }
  if (rateBits.length) { const rl = document.createElement("div"); rl.className = "lmeta rline"; rl.textContent = rateBits.join(" \u00b7 "); d.appendChild(rl); }

  const pt = l.payment_terms || {};
  if ((pt.rail || "") === "escrow" && price.esc) {
    const note = document.createElement("div"); note.className = "esc note";
    note.textContent = "\ud83d\udd12 Price held in escrow \u2014 released only when you confirm completion. Refund window: " + (pt.refund_window_hours != null ? pt.refund_window_hours : 24) + "h after booking.";
    d.appendChild(note);
  }

  const cta = document.createElement("div"); cta.className = "lcta";
  const book = document.createElement("button"); book.className = "btn"; book.type = "button";
  book.textContent = "\ud83d\udcac Ask EverList to book this";
  book.addEventListener("click", (ev) => {
    ev.stopPropagation();
    closeDetail(false);   /* clear the panel before handing over to the chat */
    openChat();
    input.value = "book " + (l.id || "") + " ";
    input.focus(); autosize();
  });
  cta.appendChild(book);
  if (l.id) {
    const ics = document.createElement("a"); ics.className = "chip";
    ics.href = "/l/" + encodeURIComponent(l.id) + ".ics";
    ics.target = "_blank"; ics.rel = "noopener";
    ics.textContent = "\ud83d\udcc5 Add to calendar";
    ics.addEventListener("click", (ev) => ev.stopPropagation());
    cta.appendChild(ics);
  }
  d.appendChild(cta);

  if (l.url) {
    const un = document.createElement("div"); un.className = "lnote";
    un.textContent = "Organizer page: ";
    const ua = document.createElement("a"); ua.href = l.url; ua.rel = "noopener nofollow"; ua.target = "_blank";
    ua.textContent = l.url;
    ua.addEventListener("click", (ev) => ev.stopPropagation());
    un.appendChild(ua);
    d.appendChild(un);
  }

  /* Same board data the SSR page uses for "More from this organizer":
     switches straight to that listing's panel. */
  const own = String(l.owner || "");
  if (own && l.id) {
    const sibs = boardData()
      .filter((x) => String(x.id) !== String(l.id) && String(x.owner || "") === own).slice(0, 3);
    if (sibs.length) {
      const sec = document.createElement("section"); sec.className = "drel";
      const sh = document.createElement("div"); sh.className = "lmeta"; sh.textContent = "More from this organizer"; sec.appendChild(sh);
      const row = document.createElement("div"); row.className = "drelrow";
      sibs.forEach((x) => {
        const b = document.createElement("button"); b.className = "drelitem"; b.type = "button";
        b.textContent = x.title || "Untitled";
        b.addEventListener("click", (ev) => {
          ev.stopPropagation();
          const target = Array.from(grid.querySelectorAll(".card")).find((c) => {
            const xl = cardListing(c); return xl && String(xl.id) === String(x.id);
          });
          if (target) openDetail(target);
        });
        row.appendChild(b);
      });
      sec.appendChild(row);
      d.appendChild(sec);
    }
  }
  return d;
}

function openDetail(card) {
  if (openDetailCard === card) { closeDetail(false); return; }
  const l = cardListing(card);
  if (!l) return;
  // direct swap: old panel (if any) is replaced inside the same FLIP step,
  // so the board moves once — never collapse-then-reexpand
  const prevCard = openDetailCard;
  const prevPanel = detailPanel;
  // insert the panel in a row ABOVE the clicked card (owner call v3):
  // rows above stay, the clicked row and everything below slide down
  const rows = rowsOfGrid();
  let rowIdx = -1;
  for (let i = 0; i < rows.length; i++) {
    if (rows[i].indexOf(card) !== -1) { rowIdx = i; break; }
  }
  animateBoardChange(() => {
    if (prevCard) prevCard.classList.remove("ghost");
    if (prevPanel) prevPanel.remove();
    card.classList.add("ghost");
    detailPanel = buildDetailPanel(l);
    if (rowIdx <= 0) grid.insertBefore(detailPanel, grid.firstChild);
    else rows[rowIdx - 1][rows[rowIdx - 1].length - 1].after(detailPanel);
  });
  // transform-only morph (GPU, zero reflow): grow out of the clicked card
  // into the slot directly above it
  const hole = card.getBoundingClientRect();
  const pr = detailPanel.getBoundingClientRect();
  if (!REDUCE() && pr.width > 0 && pr.height > 0) {
    detailPanel.style.transformOrigin = "top left";
    detailPanel.animate(
      [{ transform: "translate(" + (hole.left - pr.left) + "px," + (hole.top - pr.top) + "px) scale(" + (hole.width / pr.width) + "," + (hole.height / pr.height) + ")", opacity: 0.55 },
       { transform: "none", opacity: 1 }],
      { duration: 340, easing: EASE }
    );
  }
  openDetailCard = card;
  // push once per detail session; a switch replaces the entry (clean stack)
  const url = "/l/" + encodeURIComponent(l.id);
  if (history.state && history.state.detail) history.replaceState({ detail: l.id }, "", url);
  else if (location.pathname !== url) history.pushState({ detail: l.id }, "", url);
  detailPanel.scrollIntoView({ behavior: REDUCE() ? "auto" : "smooth", block: "nearest" });
}

function closeDetail(viaPop) {
  if (!openDetailCard || !detailPanel) return;
  const card = openDetailCard, panel = detailPanel;
  openDetailCard = null; detailPanel = null;
  // capture BEFORE-positions while the panel still holds the row open
  const before = new Map();
  Array.from(grid.children).forEach((c) => { if (c !== panel) before.set(c, c.getBoundingClientRect()); });
  // freeze the panel OUT of the grid — in DOCUMENT coordinates (absolute,
  // not fixed) so page scroll during the retreat keeps it glued to the hole
  const pr = panel.getBoundingClientRect();
  const sx = window.scrollX || 0, sy = window.scrollY || 0;
  panel.style.position = "absolute";
  panel.style.left = (pr.left + sx) + "px";
  panel.style.top = (pr.top + sy) + "px";
  panel.style.width = pr.width + "px";
  panel.style.height = pr.height + "px";
  panel.style.boxSizing = "border-box";   /* rect.width == style.width exactly */
  panel.style.margin = "0";
  panel.style.zIndex = "40";
  panel.style.pointerEvents = "none";
  document.body.appendChild(panel);
  // refill the hole: the card never left, it just un-dims (CSS transition)
  card.classList.remove("ghost");
  // hole FINAL resting rect — measured BEFORE any slide transform runs.
  // getBoundingClientRect includes transforms: measuring after the slides
  // start would target the animated START position and mis-dock by a row.
  const hole = card.getBoundingClientRect();
  // board closes NOW and every compensating slide starts in THIS task, so
  // the first painted frame is already the sliding state (no 1-frame jump)
  Array.from(grid.children).forEach((c) => {
    const f = before.get(c);
    if (f) slideBack(f, c.getBoundingClientRect(), c);
  });
  if (REDUCE()) {
    panel.remove();
  } else {
    // fade FAST first (half gone by 40% of the run), then shrink into the
    // hole's FINAL position — same 340ms/easing as the board slide, so the
    // panel and the cards land together, pixel-exact
    const frames = [
      { transform: "none", opacity: 1, offset: 0 },
      { transform: "none", opacity: 0.5, offset: 0.4 }
    ];
    if (pr.width > 0 && hole.width > 0) {
      panel.style.transformOrigin = "top left";
      frames.push({ transform: "translate(" + (hole.left - pr.left) + "px," + (hole.top - pr.top) + "px) scale(" + (hole.width / pr.width) + "," + (hole.height / pr.height) + ")", opacity: 0 });
    } else {
      frames.push({ transform: "translateY(8px) scale(.97)", opacity: 0 });
    }
    /* fill BOTH pins the final frame (opacity 0) after the animation ends.
       WITHOUT it, WAAPI reverts the panel to its natural style (opacity 1,
       full size) the instant the animation finishes — a 1-2 frame full-panel
       flash before the removal timer fires. That was the "briefly opens up
       again" glitch at the very end of the close. */
    const retreat = panel.animate(frames, { duration: 340, easing: EASE, fill: "both" });
    retreat.finished.then(() => panel.remove()).catch(() => {});
  }
  // unconditional cleanup: the floating panel can never outlive its exit
  setTimeout(() => panel.remove(), 400);
  if (!viaPop && history.state && history.state.detail) history.back();
  // NOTE: no scrollIntoView here — scrolling during the retreat moves the
  // hole relative to the frozen panel and was a prime glitch source
}

window.addEventListener("popstate", () => {
  if (openDetailCard && !(history.state && history.state.detail)) closeDetail(true);
  else if (!openDetailCard && history.state && history.state.detail) {
    // forward button: re-open the panel for the listing in the URL
    const id = history.state.detail;
    const card = Array.from(grid.querySelectorAll(".card")).find((c) => {
      const l = cardListing(c); return l && String(l.id) === String(id);
    });
    if (card) openDetail(card);
  }
});

/* One handler, ordered: chat CTA first, then detail-close gestures, then
   card click = FULL detail. The details link is folded in here — a second
   listener would open+close in the same click dispatch (net: nothing).
   Modifier-clicked links keep native /l/ navigation. */
grid.addEventListener("click", (e) => {
  if (e.target.closest(".cta")) return;             /* CTA routes to chat itself */
  const dl = e.target.closest(".dlink");
  if (dl && !(e.metaKey || e.ctrlKey || e.shiftKey || e.altKey)) e.preventDefault(); /* panel, not navigation */
  if (e.target.closest(".detail-panel")) {
    if (e.target === detailPanel) closeDetail(false); /* bg/padding click = close; content stays interactive */
    return;
  }
  const card = e.target.closest(".card");
  if (!card) { if (openDetailCard) closeDetail(false); return; }
  if (openDetailCard === card) { closeDetail(false); return; }  /* hole click = close */
  openDetail(card);
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && openDetailCard) closeDetail(false); });

/* ---------- W3: browser signup (PoW + keygen in-page; seed shown ONCE) ---------- */
/* The keypair is generated IN THE BROWSER (vendored tweetnacl): the seed is
   displayed once and never leaves this tab — the hub stores only the pubkey.
   Crypto contract cross-checked against the hub's Python ed25519. */
const _hex = (buf) => Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, "0")).join("");

async function solvePoW(challenge, difficulty, onTick) {
  const enc = new TextEncoder();
  const t0 = Date.now();
  for (let nonce = 0; nonce <= 1e15; nonce++) {
    const digest = await crypto.subtle.digest("SHA-256", enc.encode(challenge + nonce));
    const b = new Uint8Array(digest);
    let bits = 0;
    for (let i = 0; i < 32; i++) {
      if (b[i] === 0) { bits += 8; continue; }
      bits += Math.clz32(b[i]) - 24;
      break;
    }
    if (bits >= difficulty) return nonce;
    if (onTick && (nonce & 16383) === 0 && Date.now() - t0 > 1500) onTick(nonce);
  }
  throw new Error("proof-of-work failed");
}

async function browserSignupFlow(name, statusEl) {
  const ch = await fetch("/api/signup-challenge").then((r) => r.json());
  if (!ch.challenge) throw new Error(ch.error || "challenge unavailable");
  statusEl.textContent = "solving proof-of-work…";
  const nonce = await solvePoW(ch.challenge, ch.difficulty, (n) => {
    statusEl.textContent = "solving proof-of-work… " + n.toLocaleString();
  });
  statusEl.textContent = "generating your key in-browser…";
  const seed = crypto.getRandomValues(new Uint8Array(32));
  const kp = nacl.sign.keyPair.fromSeed(seed);
  const pubHex = _hex(kp.publicKey);
  statusEl.textContent = "creating account…";
  const res = await fetch("/api/signup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ agent: name, pubkey: pubHex, pow: { challenge: ch.challenge, nonce: nonce } }),
  });
  const data = await res.json().catch(() => ({}));
  if (res.status !== 201) throw new Error(data.error || "signup rejected");
  statusEl.textContent = "signing you in…";
  let logged = false;
  try {
    const lch = await fetch("/api/login-challenge?pubkey=" + pubHex).then((r) => r.json());
    if (lch.challenge) {
      const msg = new TextEncoder().encode("everlist-login:" + lch.challenge);
      const sig = nacl.sign.detached(msg, kp.secretKey);
      const lres = await fetch("/api/login-pubkey", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ agent: name, pubkey: pubHex, sig: _hex(sig) }),
      });
      logged = !!((await lres.json().catch(() => ({}))).ok);
    }
  } catch (e2) { /* account created; seed login still works */ }
  return { seedHex: _hex(seed), logged: logged };
}

const signupArea = document.getElementById("signup-area");
const signupForm = document.getElementById("signup");
const signupBtn = document.getElementById("s-go");
const signupStatus = document.getElementById("s-status");

function toggleSignup(show) { if (signupArea) signupArea.hidden = !show; }

if (signupForm) signupForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  if (signupBtn.disabled) return;
  const name = document.getElementById("s-name").value.trim();
  if (!name) { signupStatus.textContent = "a name is required"; return; }
  signupBtn.disabled = true;
  signupStatus.textContent = "starting…";
  try {
    const out = await browserSignupFlow(name, signupStatus);
    signupStatus.textContent = "";
    signupForm.textContent = "";
    const warn = el("p", null, "🔑 Your account SEED — shown ONCE, store it like a crypto seed phrase. It is your only way to log in elsewhere:");
    const seedBox = document.createElement("pre");
    seedBox.className = "seedbox";
    seedBox.textContent = out.seedHex;
    const ok = el("button", "cta", "I've saved it — continue");
    ok.type = "button";
    ok.addEventListener("click", () => { toggleSignup(false); loadDashboard(); });
    signupForm.appendChild(warn);
    signupForm.appendChild(seedBox);
    signupForm.appendChild(ok);
    if (out.logged) loadDashboard();
    else addMsg("err", "account created — auto sign-in failed; log in with the seed above.");
  } catch (err) {
    signupStatus.textContent = "⚠️ " + (err && err.message ? err.message : "signup failed");
  }
  signupBtn.disabled = false;
});

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

function showView(name) {
  // normalize legacy boolean callers (true = dashboard, false = browse)
  // — the Bookings button and brain nav both used booleans.
  if (name === true) name = "dash";
  else if (name === false) name = "browse";
  const dash = name === "dash", post = name === "post";
  browseSec.hidden = dash || post;
  const postSec = document.getElementById("post");
  if (postSec) postSec.hidden = !post;
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
  const idLink = el("a", "esc", "#" + b.id);
  idLink.href = "/booking/" + encodeURIComponent(b.id);
  foot.appendChild(idLink);
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
  const idLink = el("a", "esc", "#" + o.id);
  idLink.href = "/booking/" + encodeURIComponent(o.id);
  foot.appendChild(idLink);
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
  const p = el("p", null, "Log in with your account seed and your bookings + orders appear here. No seed yet? ");
  const su = el("a", null, "Create an account right here");
  su.href = "#";
  su.addEventListener("click", (e) => { e.preventDefault(); toggleSignup(true); });
  p.appendChild(su);
  p.appendChild(document.createTextNode(" — your key is generated in your browser and the seed is shown once."));
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

/* ---------- W3: my listings (dashboard manage) ---------- */
async function loadMyListings() {
  const box = document.getElementById("my-listings");
  if (!box) return;
  box.textContent = "";
  let data;
  try {
    const r = await fetch("/api/my-listings", { cache: "no-store" });
    data = await r.json();
  } catch (e) { return; }
  if (!data.listings || !data.listings.length) {
    box.appendChild(el("p", "empty", "No listings yet — use “Post a listing” or type 'list ...' in the chat."));
    return;
  }
  data.listings.forEach((l) => {
    const row = el("div", "myrow");
    row.appendChild(el("span", "t", l.title || l.id));
    row.appendChild(el("span", "m", (l.id || "") + " · " + priceOf(l).txt + (l.date ? " · " + l.date : "")));
    row.appendChild(el("span", "m", l.available === false ? "archived" : "live"));
    row.appendChild(el("span", "grow"));
    const det = document.createElement("a"); det.className = "linkbtn"; det.href = "/l/" + encodeURIComponent(l.id); det.target = "_blank"; det.rel = "noopener"; det.textContent = "details";
    row.appendChild(det);
    const arc = document.createElement("button"); arc.className = "linkbtn"; arc.type = "button";
    arc.textContent = l.available === false ? "unarchive" : "archive";
    arc.addEventListener("click", () => { openChat(); send((l.available === false ? "unarchive " : "archive ") + l.id); setTimeout(loadMyListings, 1500); });
    row.appendChild(arc);
    const ed = document.createElement("button"); ed.className = "linkbtn"; ed.type = "button"; ed.textContent = "edit";
    ed.addEventListener("click", () => startPost({ title: l.title, price: l.price, date: l.date, loc: l.location, cap: l.capacity, url: l.url }));
    row.appendChild(ed);
    box.appendChild(row);
  });
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
  loadMyListings();
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
/* W2 deep links: /?book=<id> from detail pages -> prefill (never auto-send:
   booking stays a human confirm); /?q=<search> -> run the search in chat. */
try {
  const u = new URL(location.href);
  const bid = u.searchParams.get("book");
  const q = u.searchParams.get("q");
  const view = u.searchParams.get("view");
  if (view === "dash") { showView("dash"); loadDashboard(); }
  if (bid) {
    openChat();
    input.value = "book " + bid + " ";
    input.focus();
    autosize();
  } else if (q) {
    openChat();
    send(q.slice(0, 200));
  }
} catch (e) {}
