import html, json, re, urllib.parse, urllib.request
from datetime import datetime as _dt, timedelta as _td

# W2 discovery + SEO: server-rendered, indexable pages. The ONLY non-JS
# surface: /l/{id} detail (OG + schema.org), /l/{id}.ics, /sitemap.xml,
# /robots.txt. Every dynamic value passes esc() (text AND attribute
# contexts), so hub data renders verbatim but can never inject markup.
# CSP forbids inline styles (style-src 'self'), so pages reuse /style.css
# plus a small .ldetail block appended there.

BASE = "https://everlist.network"
HUB = "http://127.0.0.1:8802"
_TIMEOUT = 6


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def _get(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
        return json.loads(r.read().decode())


def listing(lid, hub=None):
    try:
        u = (hub or HUB) + "/listings/" + urllib.parse.quote(str(lid), safe="")
        return _get(u)
    except Exception:
        return None


def all_listings(hub=None):
    try:
        d = _get((hub or HUB) + "/listings")
        return [l for l in (d.get("listings") or [])
                if l.get("visibility", "public") == "public"]
    except Exception:
        return []


def ratings_bits(l):
    """W4/S6: honest rating display. Paid reviews are amount-weighted by the
    hub; free-class feedback lives in its own channel. Empty list = no reviews
    yet — we never render fake stars."""
    out = []
    try:
        pc = int(l.get("rating_count") or 0)
    except (TypeError, ValueError):
        pc = 0
    if pc:
        try:
            wt = float(l.get("rating_wtot") or 0)
        except (TypeError, ValueError):
            wt = 0.0
        if wt > 0:
            avg = float(l.get("rating_wsum") or 0) / wt
            out.append("\u2605 %.1f/5 paid reviews (%d, amount-weighted)" % (avg, pc))
        else:
            try:
                out.append("\u2605 %.1f/5 (%d)" % (float(l.get("rating_avg") or (float(l.get("rating_sum") or 0) / pc)), pc))
            except (TypeError, ValueError, ZeroDivisionError):
                pass
    try:
        fc = int(l.get("free_rating_count") or 0)
    except (TypeError, ValueError):
        fc = 0
    if fc:
        try:
            out.append("free-class feedback %.1f/5 (%d)" % (float(l.get("free_rating_sum") or 0) / fc, fc))
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return out


def _fmt_ts(ts):
    try:
        return _dt.utcfromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M UTC")
    except (TypeError, ValueError):
        return ""


def _page_head(title, desc, canon):
    h = []
    a = h.append
    a("<!doctype html>")
    a("<html lang=\"en\"><head>")
    a("<meta charset=\"utf-8\">")
    a("<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">")
    a("<title>" + esc(title) + " \u2014 EverList</title>")
    a("<meta name=\"description\" content=\"" + esc(desc) + "\">")
    a("<link rel=\"canonical\" href=\"" + BASE + canon + "\">")
    a("<meta property=\"og:type\" content=\"website\">")
    a("<meta property=\"og:title\" content=\"" + esc(title) + "\">")
    a("<meta property=\"og:description\" content=\"" + esc(desc) + "\">")
    a("<meta property=\"og:url\" content=\"" + BASE + canon + "\">")
    a("<meta property=\"og:image\" content=\"" + BASE + "/og/default.jpg\">")
    a("<meta property=\"og:image:width\" content=\"1200\">")
    a("<meta property=\"og:image:height\" content=\"630\">")
    a("<meta name=\"theme-color\" content=\"#0d1117\">")
    a("<link rel=\"icon\" href=\"/favicon.svg\" type=\"image/svg+xml\">")
    a("<script src=\"/theme.js\"></script>")
    a("<link rel=\"stylesheet\" href=\"/style.css\">")
    return h


def _page_open(h, nav_extra=""):
    a = h.append
    a("<body><div class=\"page ldetail\">")
    a("<header class=\"top\"><div class=\"brand\"><img src=\"/favicon.svg\" alt=\"\" width=\"30\" height=\"30\"><span>EverList</span></div>"
      "<nav class=\"nav\"><a href=\"/\">Browse</a>" + nav_extra + "</nav></header>")
    a("<article class=\"lmain\">")


def ledger_html(hub=None):
    """W4 /transparency: the hub's append-only settlement ledger, public.
    Honest zeros stay zeros — this page never invents activity."""
    h = _page_head("Transparency ledger",
                   "Every settlement on EverList — escrow releases, refunds, fees — in a public append-only ledger.",
                   "/transparency")
    a = h.append
    _page_open(h, "<a href=\"/network\">Network</a>")
    a("<h1>Transparency</h1>")
    a("<p class=\"ldesc\">Every settlement on this hub lands in an <strong>append-only ledger</strong>: releases, refunds, fees. "
      "Nothing is edited out \u2014 what you see is what the hub has.</p>")
    try:
        d = _get((hub or HUB) + "/ledger")
    except Exception:
        d = None
    if not d:
        a("<div class=\"note\">The ledger is unreachable right now \u2014 try again shortly.</div>")
    else:
        t = d.get("totals") or {}
        a("<div class=\"ltot\">")
        for label, val in (("total settled volume", "\u20ac %.2f" % float(t.get("total_volume") or 0)),
                           ("hub fees collected", "\u20ac %.2f" % float(t.get("total_hub_fees") or 0)),
                           ("x402 settlements", str(t.get("x402_settlements") or 0))):
            a("<div class=\"lcard\"><div class=\"lmeta\">" + esc(label) + "</div><h3>" + esc(val) + "</h3></div>")
        a("</div>")
        entries = d.get("ledger") or []
        a("<h2>Entries (" + str(len(entries)) + ")</h2>")
        if entries:
            a("<table class=\"ltable\"><thead><tr><th>when</th><th>booking</th><th>amount</th><th>state</th><th>flow</th></tr></thead><tbody>")
            for e in entries:
                if e.get("refunded_to"):
                    flow = "refunded to " + str(e.get("refunded_to"))
                else:
                    try:
                        po = float(e.get("owner_payout") or 0)
                    except (TypeError, ValueError):
                        po = 0.0
                    flow = "owner payout \u20ac%.2f" % po if po else ""
                amt = "\u20ac " + ("%.2f" % float(e.get("amount") or 0))
                a("<tr><td>" + esc(_fmt_ts(e.get("ts"))) + "</td><td>" + esc(str(e.get("booking") or ""))
                  + "</td><td>" + esc(amt) + "</td>"
                  + "<td><span class=\"chip stat\">" + esc(str(e.get("escrow") or "")) + "</span></td>"
                  + "<td>" + esc(flow) + "</td></tr>")
            a("</tbody></table>")
        else:
            a("<div class=\"note\">No settled bookings yet. When money moves \u2014 release, refund, fee \u2014 it shows up here. "
              "Honest zeros, no invented activity.</div>")
    a("<div class=\"lnote\">Agents read the same ledger at <code>GET /ledger</code> \u2014 this page is just a window over the public API.</div>")
    a("</article></div></body></html>")
    return ("\n".join(h) + "\n").encode("utf-8")


def network_html(hub=None):
    """W4 /network: the hub registry page \u2014 curated partner positioning,
    tiers, responsibility line, hubs."""
    h = _page_head("Network \u2014 the EverList hub registry",
                   "EverList is a curated network of partner commerce hubs \u2014 open protocol, gated membership.",
                   "/network")
    a = h.append
    _page_open(h, "<a href=\"/transparency\">Transparency</a>")
    a("<h1>Network</h1>")
    a("<p class=\"ldesc\">EverList is an open <strong>protocol</strong> \u2014 but a <strong>curated network</strong>. "
      "The software is inspectable by anyone; running a hub <em>in the EverList network</em> is a partnership: "
      "verified status is granted by EverList after review, never self-service. Agents filter by tier.</p>")
    a("<p class=\"lnote\">Today the network runs one hub \u2014 this one. Partner operation is by application; "
      "the door is open to the right partners, not to everyone.</p>")
    try:
        d = _get((hub or HUB) + "/registry")
    except Exception:
        d = None
    if not d:
        a("<div class=\"note\">The registry is unreachable right now \u2014 try again shortly.</div>")
    else:
        tiers = d.get("tiers") or {}
        a("<h2>Tiers</h2>")
        for name, desc in tiers.items():
            a("<div class=\"lcard\"><div class=\"lmeta tier tier-" + esc(name) + "\">" + esc(name) + "</div>"
              "<p class=\"ldesc\">" + esc(desc) + "</p></div>")
        hubs = d.get("hubs") or []
        a("<h2>Registered hubs (" + str(len(hubs)) + ")</h2>")
        if hubs:
            a("<table class=\"ltable\"><thead><tr><th>hub</th><th>type</th><th>tier</th><th>policy</th></tr></thead><tbody>")
            for x in hubs:
                pol = x.get("content_policy") or ""
                pol_cell = ("<a href=\"" + esc(pol) + "\" rel=\"noopener\">policy</a>") if pol else ""
                a("<tr><td>" + esc(str(x.get("url") or "")) + "</td><td>" + esc(str(x.get("type") or ""))
                  + "</td><td><span class=\"chip stat\">" + esc(str(x.get("tier") or "")) + "</span></td><td>" + pol_cell + "</td></tr>")
            a("</tbody></table>")
        resp = d.get("responsibility")
        if resp:
            a("<div class=\"note\">" + esc(resp) + "</div>")
    a("<div class=\"lnote\">Agents browse the registry at <code>GET /registry</code>.</div>")
    a("</article></div></body></html>")
    return ("\n".join(h) + "\n").encode("utf-8")


def _fmt_price(p):
    try:
        v = float(p or 0)
    except (TypeError, ValueError):
        return ""
    if v == 0:
        return "Free"
    return "\u20ac " + ("%.2f" % v).rstrip("0").rstrip(".")


def _jsonld(l):
    offers = {"@type": "Offer",
              "price": float(l.get("price") or 0),
              "priceCurrency": "EUR",
              "availability": "https://schema.org/" + ("InStock" if l.get("available", True) else "SoldOut"),
              "url": BASE + "/l/" + str(l.get("id"))}
    pt = l.get("payment_terms") or {}
    if pt.get("rail") == "escrow":
        offers["description"] = ("Payment held in escrow until completion "
                                 "(refund window %s h)." % pt.get("refund_window_hours", 24))
    d = {"@context": "https://schema.org",
         "name": l.get("title") or "Untitled",
         "url": BASE + "/l/" + str(l.get("id")),
         "description": l.get("description") or "",
         "offers": offers}
    if l.get("date"):
        d["@type"] = "Event"
        d["startDate"] = str(l.get("date"))
        d["eventAttendanceMode"] = "https://schema.org/OfflineEventAttendanceMode"
        d["eventStatus"] = "https://schema.org/EventScheduled"
        d["location"] = {"@type": "Place", "name": l.get("location") or ""}
        if l.get("capacity"):
            d["maximumAttendeeCapacity"] = l.get("capacity")
            d["remainingAttendeeCapacity"] = max(0, int(l.get("capacity")) - int(l.get("registered") or 0))
    else:
        d["@type"] = "Service"
        d["areaServed"] = l.get("location") or ""
    return d


def detail_html(l, hub=None):
    lid = str(l.get("id"))
    title = l.get("title") or "Untitled"
    price = _fmt_price(l.get("price"))
    h = []
    a = h.append
    a("<!doctype html>")
    a("<html lang=\"en\"><head>")
    a("<meta charset=\"utf-8\">")
    a("<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">")
    a("<title>" + esc(title) + " \u2014 EverList</title>")
    a("<meta name=\"description\" content=\"" + esc((l.get("description") or "")[:160]) + "\">")
    a("<link rel=\"canonical\" href=\"" + BASE + "/l/" + esc(lid) + "\">")
    a("<meta property=\"og:type\" content=\"website\">")
    a("<meta property=\"og:title\" content=\"" + esc(title) + "\">")
    a("<meta property=\"og:description\" content=\"" + esc((l.get("description") or "")[:200]) + "\">")
    a("<meta property=\"og:url\" content=\"" + BASE + "/l/" + esc(lid) + "\">")
    # one honest brand card on every share (owner call 2026-09-16): per-vertical
    # AI scenes retired - a generic scene could misrepresent a real listing
    a("<meta property=\"og:image\" content=\"" + BASE + "/og/default.jpg\">")
    a("<meta property=\"og:image:width\" content=\"1200\">")
    a("<meta property=\"og:image:height\" content=\"630\">")
    a("<meta name=\"twitter:card\" content=\"summary_large_image\">")
    a("<link rel=\"icon\" href=\"/favicon.svg\" type=\"image/svg+xml\">")
    a("<link rel=\"alternate\" type=\"text/calendar\" href=\"/l/" + esc(lid) + ".ics\">")
    a("<link rel=\"stylesheet\" href=\"/style.css\">")
    a("<script type=\"application/ld+json\">" + json.dumps(_jsonld(l), ensure_ascii=False).replace("</", "<\\/") + "</script>")
    a("</head><body><div class=\"page ldetail\">")
    a("<header class=\"top\"><div class=\"brand\"><img src=\"/favicon.svg\" alt=\"\" width=\"30\" height=\"30\"><span>EverList</span></div>"
      "<nav class=\"nav\"><a href=\"/\">Browse</a></nav></header>")
    a("<article class=\"lmain\">")
    meta = [l.get("vertical") or "", l.get("date") or "any date", price, l.get("location") or ""]
    a("<div class=\"lmeta\">" + " &middot; ".join(esc(m) for m in meta if m) + "</div>")
    a("<h1>" + esc(title) + "</h1>")
    if l.get("capacity"):
        left = max(0, int(l.get("capacity")) - int(l.get("registered") or 0))
        a("<div class=\"lmeta\">" + str(left) + " of " + esc(l.get("capacity")) + " spots left</div>")
    a("<p class=\"ldesc\">" + esc(l.get("description") or "") + "</p>")
    tags = l.get("tags") or []
    if tags:
        a("<div>" + "".join("<span class=\"chip stat\">" + esc(t) + "</span>" for t in tags[:8]) + "</div>")
    rb = ratings_bits(l)
    if rb:
        a("<div class=\"lmeta rline\">" + " &middot; ".join(esc(x) for x in rb) + "</div>")
    pt = l.get("payment_terms") or {}
    if pt.get("rail") == "escrow":
        a("<div class=\"esc note\">&#128274; Price held in escrow &mdash; released only when you confirm completion. "
          "Refund window: " + esc(pt.get("refund_window_hours", 24)) + "h after booking.</div>")
    a("<div class=\"lcta\"><a class=\"btn\" href=\"/?book=" + esc(lid) + "\">&#128172; Ask EverList to book this</a>"
      " <a class=\"chip\" href=\"/l/" + esc(lid) + ".ics\">&#128197; Add to calendar</a></div>")
    if l.get("url"):
        a("<div class=\"lnote\">Organizer page: <a href=\"" + esc(l.get("url")) + "\" rel=\"noopener nofollow\">"
          + esc(l.get("url")) + "</a></div>")
    a("<div class=\"lnote\">No account needed to look. Booking happens in the chat &mdash; the same brain our API agents use.</div>")
    a("</article>")
    own = str(l.get("owner") or "")
    sibs = [x for x in all_listings(hub) if str(x.get("id")) != lid and str(x.get("owner") or "") == own]
    rel = [x for x in all_listings(hub) if str(x.get("id")) != lid and str(x.get("owner") or "") != own][:3]
    if sibs:
        a("<section class=\"lrel\"><h2>More from this organizer</h2>"
          "<div class=\"lmeta\"><a href=\"/org/" + urllib.parse.quote(own, safe="") + "\">All listings by " + esc(own) + " &rarr;</a></div>"
          "<div class=\"lgrid\">")
        for x in sibs[:3]:
            xm = [x.get("date") or "any date", _fmt_price(x.get("price")), x.get("location") or ""]
            a("<a class=\"lcard\" href=\"/l/" + esc(x.get("id")) + "\"><div class=\"lmeta\">"
              + " &middot; ".join(esc(m) for m in xm if m) + "</div><h3>" + esc(x.get("title") or "Untitled") + "</h3></a>")
        a("</div></section>")
    if rel:
        a("<section class=\"lrel\"><h2>More on EverList</h2><div class=\"lgrid\">")
        for x in rel:
            xm = [x.get("date") or "any date", _fmt_price(x.get("price")), x.get("location") or ""]
            a("<a class=\"lcard\" href=\"/l/" + esc(x.get("id")) + "\"><div class=\"lmeta\">"
              + " &middot; ".join(esc(m) for m in xm if m) + "</div><h3>" + esc(x.get("title") or "Untitled") + "</h3></a>")
        a("</div></section>")
    a("</div></body></html>")
    return ("\n".join(h) + "\n").encode("utf-8")


def org_page_owners(hub=None):
    """A-phase: distinct owners with >=1 active public listing (sitemap input)."""
    seen = []
    for l in all_listings(hub):
        o = str(l.get("owner") or "")
        if o and o not in seen:
            seen.append(o)
    return seen


def org_html(owner, hub=None):
    """A-phase /org/{owner}: a pure projection over ALREADY-public catalog data.
    No account join: the owner principal is shown as-is; anything not in the
    public catalog (email, account ids, bookings) does not exist on this page.
    Returns None when the owner has no active public listing (honest 404,
    no empty shells)."""
    owner = str(owner)
    mine = [l for l in all_listings(hub) if str(l.get("owner") or "") == owner]
    if not mine:
        return None
    h = _page_head(owner + " \u2014 organizer on EverList",
                   "Listings by " + owner + " on EverList \u2014 bookable in one chat message.",
                   "/org/" + owner)
    a = h.append
    a("<!doctype html>")
    a("<html lang=\"en\"><head>")
    a("<meta charset=\"utf-8\">")
    a("<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">")
    a("<title>" + esc(owner) + " \u2014 EverList organizer</title>")
    a("<meta name=\"description\" content=\"Listings by " + esc(owner) + " on EverList.\">")
    a("<link rel=\"canonical\" href=\"" + BASE + "/org/" + esc(owner) + "\">")
    a("<meta property=\"og:type\" content=\"profile\">")
    a("<meta property=\"og:title\" content=\"" + esc(owner) + " \u2014 EverList organizer\">")
    a("<meta property=\"og:url\" content=\"" + BASE + "/org/" + esc(owner) + "\">")
    a("<meta property=\"og:image\" content=\"" + BASE + "/og/default.jpg\">")
    a("<meta name=\"twitter:card\" content=\"summary_large_image\">")
    a("<link rel=\"icon\" href=\"/favicon.svg\" type=\"image/svg+xml\">")
    a("<link rel=\"stylesheet\" href=\"/style.css\">")
    a("</head><body><div class=\"page ldetail\">")
    a("<header class=\"top\"><div class=\"brand\"><img src=\"/favicon.svg\" alt=\"\" width=\"30\" height=\"30\"><span>EverList</span></div>"
      "<nav class=\"nav\"><a href=\"/\">Browse</a></nav></header>")
    a("<article class=\"lmain\">")
    a("<div class=\"lmeta\">organizer</div>")
    a("<h1>" + esc(owner) + "</h1>")
    a("<div class=\"lmeta\">" + str(len(mine)) + " active listing" + ("" if len(mine) == 1 else "s") + "</div>")
    a("<div class=\"lnote\">This page is a window over the public catalog: only active public listings appear here \u2014 nothing else is known or shown about the organizer.</div>")
    a("</article>")
    a("<section class=\"lrel\"><h2>Listings</h2><div class=\"lgrid\">")
    for x in mine:
        xm = [x.get("vertical") or "", x.get("date") or "any date", _fmt_price(x.get("price")), x.get("location") or ""]
        rb = ratings_bits(x)
        rline = ("<div class=\"lmeta rline\">" + " &middot; ".join(esc(r) for r in rb) + "</div>") if rb else ""
        a("<a class=\"lcard\" href=\"/l/" + esc(x.get("id")) + "\"><div class=\"lmeta\">"
          + " &middot; ".join(esc(m) for m in xm if m) + "</div><h3>" + esc(x.get("title") or "Untitled") + "</h3>"
          + rline + "</a>")
    a("</div></section>")
    a("<div class=\"page ldetail\"><div class=\"lnote\">Want your own page like this? List in one chat message at <a href=\"/\">everlist.network</a> \u2014 free during the pilot.</div></div>")
    a("</div></body></html>")
    return ("\n".join(h) + "\n").encode("utf-8")


ESCROW_LABELS = {
    "HELD": "escrow held",
    "WAIVED": "free (no payment)",
    "RELEASED": "released to owner",
    "REFUNDED": "refunded",
    "DIRECT": "instant — settled",
}


def booking_html(b, l=None):
    """W3: participant-gated booking detail page. `b` is the hub's
    /bookings/{id} projection (buyer or owner view); `l` the listing context
    when still visible. CSP-safe: every dynamic value passes esc(); no inline
    styles or scripts. Private page: noindex, served no-store by webchat."""
    bid = str(b.get("id"))
    esc_state = str(b.get("escrow") or "HELD")
    created = b.get("created")
    when = ""
    if isinstance(created, (int, float)):
        when = _dt.utcfromtimestamp(created).strftime("%Y-%m-%d %H:%M UTC")
    title = (l or {}).get("title") or str(b.get("listing_id"))
    amount = b.get("amount")
    rows = [("State", esc_state)]
    if when:
        rows.append(("Booked at", when))
    if amount:
        rows.append(("Amount", "\u20ac" + str(amount)))
    rail = str((b.get("payment_terms") or {}).get("rail") or "escrow")
    rows.append(("Rail", rail))
    view = b.get("view")
    if view:
        rows.append(("You are the", str(view)))
    h = []
    a = h.append
    a("<!doctype html>")
    a("<html lang=\"en\"><head>")
    a("<meta charset=\"utf-8\">")
    a("<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">")
    a("<meta name=\"robots\" content=\"noindex, nofollow\">")
    a("<title>Booking #" + esc(bid) + " \u2014 EverList</title>")
    a("<link rel=\"icon\" href=\"/favicon.svg\" type=\"image/svg+xml\">")
    a("<link rel=\"stylesheet\" href=\"/style.css\">")
    a("</head><body><div class=\"page ldetail\">")
    a("<header class=\"top\"><div class=\"brand\"><img src=\"/favicon.svg\" alt=\"\" width=\"30\" height=\"30\"><span>EverList</span></div>"
      "<nav class=\"nav\"><a href=\"/\">Browse</a> <a href=\"/?view=dash\">Bookings</a></nav></header>")
    a("<article class=\"lmain\">")
    a("<div class=\"lmeta\">" + esc(title) + "</div>")
    a("<h1>Booking #" + esc(bid) + "</h1>")
    # escrow timeline — the moat, made visible (labels mirror the W1 dashboard)
    if esc_state in ("WAIVED", "DIRECT"):
        timeline = [esc_state]
    elif esc_state == "REFUNDED":
        timeline = ["HELD", "REFUNDED"]
    else:
        timeline = ["HELD", "RELEASED"]
    a("<div class=\"btl\" aria-label=\"escrow timeline\">")
    for i, stp in enumerate(timeline):
        cls = "btl-step"
        if stp == esc_state:
            cls += " now"
        elif i < len(timeline) - 1:
            cls += " done"
        a("<div class=\"" + cls + "\"><span class=\"btl-dot\"></span>" + esc(ESCROW_LABELS.get(stp, stp)) + "</div>")
    a("</div>")
    a("<div class=\"bcard\">")
    for k, v in rows:
        a("<div class=\"brow\"><span class=\"bk\">" + esc(k) + "</span><span class=\"bv\">" + esc(v) + "</span></div>")
    a("</div>")
    if l:
        lid = str(b.get("listing_id") or "")
        a("<div class=\"lcta\"><a class=\"btn\" href=\"/l/" + esc(lid) + "\">View listing</a>"
          " <a class=\"chip\" href=\"/?view=dash\">All bookings</a></div>")
    a("<div class=\"lnote\">Only the buyer and the listing owner can see this page. "
      "Actions (cancel / confirm) live in the Bookings view &mdash; tokens never touch the browser.</div>")
    a("</article></div></body></html>")
    return ("\n".join(h) + "\n").encode("utf-8")


def _ics_escape(v):
    """RFC 5545 3.3.11 TEXT: escape backslash, semicolon, comma, newlines."""
    v = str(v)
    v = v.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    v = v.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    # control chars other than escaped newline have no place in ICS TEXT
    v = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", v)
    return v

def _ics_fold(line):
    """RFC 5545 3.1: fold content lines longer than 75 octets (CRLF + space)."""
    if len(line.encode("utf-8")) <= 75:
        return line
    parts, cur, curlen = [], "", 0
    for ch in line:
        chlen = len(ch.encode("utf-8"))
        if curlen + chlen > 74:  # continuation keeps 74 chars + 1 space = 75
            parts.append(cur)
            cur, curlen = "", 0
        cur += ch
        curlen += chlen
    return "\r\n ".join(parts + [cur])

def ics_body(l):
    try:
        ymd = _dt.strptime(str(l.get("date")), "%Y-%m-%d").strftime("%Y%m%d")
        end = (_dt.strptime(str(l.get("date")), "%Y-%m-%d") + _td(days=1)).strftime("%Y%m%d")
    except (TypeError, ValueError):
        return None
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//EverList//EN", "CALSCALE:GREGORIAN",
             "BEGIN:VEVENT",
             "UID:" + str(l.get("id")) + "@everlist.network",
             "DTSTAMP:" + _dt.utcnow().strftime("%Y%m%dT%H%M%SZ"),
             "DTSTART;VALUE=DATE:" + ymd,
             "DTEND;VALUE=DATE:" + end,
             "SUMMARY:" + _ics_escape(l.get("title") or "EverList listing"),
             "LOCATION:" + _ics_escape(l.get("location") or ""),
             "DESCRIPTION:" + _ics_escape((l.get("description") or "")[:400]),
             "URL:" + BASE + "/l/" + str(l.get("id")),
             "END:VEVENT", "END:VCALENDAR"]
    return ("\r\n".join(_ics_fold(ln) for ln in lines) + "\r\n").encode("utf-8")


def sitemap_xml(hub=None):
    urls = ([BASE + "/", BASE + "/transparency", BASE + "/network", BASE + "/how", BASE + "/agents"]
            + [BASE + "/l/" + str(l.get("id")) for l in all_listings(hub)]
            + [BASE + "/org/" + urllib.parse.quote(o, safe="") for o in org_page_owners(hub)])
    body = "".join("<url><loc>" + esc(u) + "</loc></url>" for u in urls)
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">" + body + "</urlset>").encode("utf-8")


ROBOTS = ("User-agent: *\nAllow: /\n\nSitemap: " + BASE + "/sitemap.xml\n").encode("utf-8")


def _manifest(hub=None):
    """W5: the hub's self-declared manifest (or None when unreachable)."""
    try:
        return _get((hub or HUB) + "/.well-known/agent-hub.json")
    except Exception:
        return None


def _find_mode(d):
    """Honest pay-mode label: recurse for the hub's declared verification mode."""
    if isinstance(d, dict):
        for k, v in d.items():
            if k == "mode" and isinstance(v, str):
                return v
            f = _find_mode(v)
            if f:
                return f
    elif isinstance(d, list):
        for v in d:
            f = _find_mode(v)
            if f:
                return f
    return None


def _pay_banner(man):
    """I4: never imply real money moved when it did not."""
    mode = _find_mode(man or {})
    if mode == "simulated":
        return ("<div class=\"note\">Payments on this hub currently run in <strong>SIMULATED</strong> mode: "
                "the escrow and settlement machinery is real code under test, but <strong>no real money moves yet</strong>. "
                "This label updates automatically when the rails go live.</div>")
    if mode:
        return ("<div class=\"note\">Payment verification mode on this hub: <strong>" + esc(mode) + "</strong>. "
                "Check the manifest for what that means before moving real value.</div>")
    return ""


def _fee_pct(man, default="1"):
    try:
        return str((man or {})["fairness"]["fee_policy"]["actual_fee_pct"]).rstrip("0").rstrip(".")
    except Exception:
        return default


def how_html(hub=None):
    """W5 /how: the escrow explainer — what escrow covers, and honestly what it does not."""
    man = _manifest(hub)
    fee = _fee_pct(man)
    h = _page_head("How it works", "Search, book, and list in one chat message — escrow holds the payment until it is done. What escrow covers, and what it does not.", "/how")
    a = h.append
    _page_open(h, "<a href=\"/agents\">For agents</a>")
    a("<h1>How EverList works</h1>")
    a("<p class=\"ldesc\">EverList is listing-first: <strong>a listing is something with a date where a human is needed or wanted</strong> \u2014 a concert, a dinner, a repair slot, a class. Ask the chat below (or send an agent); escrow keeps the money honest in between.</p>")
    a("<h2>The loop</h2>")
    a("<div class=\"lcard\"><p class=\"ldesc\"><strong>1 \u00b7 Ask.</strong> Type what you want: \u201cfree yoga this weekend\u201d. Matches open on the board.</p></div>")
    a("<div class=\"lcard\"><p class=\"ldesc\"><strong>2 \u00b7 Book.</strong> Say \u201cbook 1\u201d. Your payment goes into <strong>escrow</strong> \u2014 held by the hub, not handed to the organizer.</p></div>")
    a("<div class=\"lcard\"><p class=\"ldesc\"><strong>3 \u00b7 It happens.</strong> You attend, eat, get repaired, learn.</p></div>")
    a("<div class=\"lcard\"><p class=\"ldesc\"><strong>4 \u00b7 Money moves.</strong> Survive the refund window and the organizer is paid (you can watch every settlement on the <a href=\"/transparency\">public ledger</a>). Cancelled inside the window? It refunds to you.</p></div>")
    a("<div class=\"lcard\"><p class=\"ldesc\"><strong>5 \u00b7 Rate.</strong> Buyers rate paid bookings; ratings are weighted so fake volume buys nothing.</p></div>")
    a("<h2>Escrow, plainly</h2>")
    a("<p class=\"ldesc\"><strong>What it covers:</strong></p>")
    a("<p class=\"ldesc\">\u00b7 The organizer cannot take the money and disappear \u2014 it is held until the window closes.<br>"
      "\u00b7 Cancel inside the listing\u2019s refund window and it comes back to you.<br>"
      "\u00b7 Default windows: <strong>events</strong> \u2014 event end + 72h \u00b7 <strong>services</strong> \u2014 fulfillment + 72h \u00b7 <strong>goods</strong> \u2014 delivery + 7 days (organizers can set their own, shown before you book).<br>"
      "\u00b7 Every release and refund is published on the public ledger \u2014 nothing moves in the dark.</p>")
    a("<p class=\"ldesc\"><strong>What it does not:</strong></p>")
    a("<p class=\"ldesc\">\u00b7 It is <strong>not insurance or a quality guarantee</strong> \u2014 it holds money honestly; it cannot judge whether the jazz was good. Ratings are the quality signal.<br>"
      "\u00b7 Each hub operator is responsible for the legality of their own listings.<br>"
      "\u00b7 Disputes: a mutual refund exists, but the hub does not adjudicate one-sided complaints.</p>")
    banner = _pay_banner(man)
    if banner:
        a(banner)
    a("<h2>What it costs</h2>")
    a("<p class=\"ldesc\">Browsing and chatting: <strong>free</strong>, no account needed. Listing: <strong>free</strong>. "
      "A flat <strong>" + esc(fee) + "% booking fee</strong> is taken from the payment \u2014 the organizer receives price minus fee, "
      "and the hub\u2019s declared fee policy is public in its <a href=\"/.well-known/agent-hub.json\">manifest</a>.</p>")
    a("<h2>For organizers</h2>")
    a("<p class=\"ldesc\">List in one message \u2014 \u201clist | Rooftop Jazz Night | 15 | 2026-10-01 | Berlin\u201d \u2014 or use the <a href=\"/\">Post a listing</a> form. "
      "Incoming bookings, confirmations and payouts appear in your Bookings view. No monthly anything.</p>")
    a("</article></div></body></html>")
    return ("\n".join(h) + "\n").encode("utf-8")


def agents_html(hub=None):
    """W5 /agents: the agent face \u2014 real endpoints, real SDK flow, honest rules."""
    man = _manifest(hub)
    fee = _fee_pct(man)
    h = _page_head("For agents", "EverList is agent-native: open manifest, OpenAPI contract, public ledger, stdlib SDK. Everything the website does, agents do too.", "/agents")
    a = h.append
    _page_open(h, "<a href=\"/how\">How it works</a>")
    a("<h1>For agents</h1>")
    a("<p class=\"ldesc\">EverList is <strong>agent-native</strong>: this website is just a client of the same open API agents use. No wall, no key to read listings.</p>")
    a("<h2>Machine-readable entry points</h2>")
    a("<div class=\"lcard\"><p class=\"ldesc\"><code>GET /.well-known/agent-hub.json</code> \u2014 manifest: protocol version, fairness declarations, identity, payments</p></div>")
    a("<div class=\"lcard\"><p class=\"ldesc\"><code>GET /openapi.json</code> \u2014 OpenAPI 3.1 contract &nbsp;\u00b7&nbsp; <code>GET /verticals</code> \u2014 field schemas + category vocab</p></div>")
    a("<div class=\"lcard\"><p class=\"ldesc\"><code>GET /search?q=\u2026</code> \u00b7 <code>GET /listings</code> \u00b7 <code>GET /ledger</code> \u2014 public reads, no auth</p></div>")
    a("<h2>Public reads with curl</h2>")
    a("<pre class=\"seedbox\">curl -s https://everlist.network/search?q=jazz</pre>")
    a("<h2>Do the whole flow with the SDK</h2>")
    a("<p class=\"ldesc\">Copy the <code>sdk/agenthub</code> directory from the public repo (github.com/everlist-hq/everlist) next to your code, then:</p>")
    a("<pre class=\"seedbox\">from agenthub import AgentHub\n"
      "\n"
      "hub = AgentHub(&quot;https://everlist.network&quot;)\n"
      "acct = hub.signup_keypair(&quot;my-agent&quot;)  # Ed25519; seed shown once, stored nowhere\n"
      "\n"
      "for l in hub.search(&quot;jazz&quot;):             # public read\n"
      "    print(l.id, l.title, l.price)\n"
      "\n"
      "b = hub.book(l.id, quantity=1)            # escrow HELD, idempotent\n"
      "print(b.id, b.escrow, b.amount, b.hub_fee)\n"
      "\n"
      "for b in hub.bookings():                  # principal-scoped\n"
      "    print(b.id, b.escrow)</pre>")
    a("<h2>Or just talk to it</h2>")
    a("<p class=\"ldesc\"><code>POST /api/chat</code> with <code>{&quot;text&quot;: &quot;search jazz&quot;}</code> \u2014 the same brain the website uses. "
      "Commands: <code>search</code>, <code>book &lt;n&gt;</code>, <code>list</code>, <code>show &lt;id&gt;</code>, <code>my-bookings</code>, <code>rate</code>, <code>archive</code>/<code>unarchive</code>, <code>help</code>.</p>")
    a("<h2>Rules of the road</h2>")
    a("<p class=\"ldesc\">\u00b7 Credentials travel only in the <code>X-Hub-Token</code> header \u2014 never in URLs. Writes carry an <code>Idempotency-Key</code>.<br>"
      "\u00b7 Rate limits (defaults): 600 reads/min, 10 bookings/min per principal; auth endpoints have their own budgets.<br>"
      "\u00b7 Fairness is <strong>declared, not promised</strong>: this hub\u2019s fee is " + esc(fee) + "% (see <code>fairness.fee_policy</code> in the manifest), and the <a href=\"/transparency\">ledger is public</a> \u2014 verify declared vs actual.<br>"
      "\u00b7 Assert <code>human_verified</code> truthfully \u2014 the interim flag becomes ZK personhood (Midnight) when Tier-2 goes live. Never fake it.<br>"
      "\u00b7 Prices are denominated in merchant currency, never volatile assets; check the manifest\u2019s payments section for rails and their status.</p>")
    a("<p class=\"lnote\">Run your own hub for your community? The protocol is open \u2014 but network membership is curated. See <a href=\"/network\">/network</a>.</p>")
    a("</article></div></body></html>")
    return ("\n".join(h) + "\n").encode("utf-8")
