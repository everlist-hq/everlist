import html, json, urllib.parse, urllib.request
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
    a("<link rel=\"icon\" href=\"/favicon.svg\" type=\"image/svg+xml\">")
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
    a("<meta name=\"twitter:card\" content=\"summary\">")
    a("<link rel=\"icon\" href=\"/favicon.svg\" type=\"image/svg+xml\">")
    a("<link rel=\"alternate\" type=\"text/calendar\" href=\"/l/" + esc(lid) + ".ics\">")
    a("<link rel=\"stylesheet\" href=\"/style.css\">")
    a("<script type=\"application/ld+json\">" + json.dumps(_jsonld(l), ensure_ascii=False) + "</script>")
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
    rel = [x for x in all_listings(hub) if str(x.get("id")) != lid][:3]
    if rel:
        a("<section class=\"lrel\"><h2>More on EverList</h2><div class=\"lgrid\">")
        for x in rel:
            xm = [x.get("date") or "any date", _fmt_price(x.get("price")), x.get("location") or ""]
            a("<a class=\"lcard\" href=\"/l/" + esc(x.get("id")) + "\"><div class=\"lmeta\">"
              + " &middot; ".join(esc(m) for m in xm if m) + "</div><h3>" + esc(x.get("title") or "Untitled") + "</h3></a>")
        a("</div></section>")
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
             "SUMMARY:" + (l.get("title") or "EverList listing"),
             "LOCATION:" + (l.get("location") or ""),
             "DESCRIPTION:" + (l.get("description") or "")[:400].replace("\n", " "),
             "URL:" + BASE + "/l/" + str(l.get("id")),
             "END:VEVENT", "END:VCALENDAR"]
    return ("\r\n".join(lines) + "\r\n").encode("utf-8")


def sitemap_xml(hub=None):
    urls = ([BASE + "/", BASE + "/transparency", BASE + "/network"]
            + [BASE + "/l/" + str(l.get("id")) for l in all_listings(hub)])
    body = "".join("<url><loc>" + esc(u) + "</loc></url>" for u in urls)
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">" + body + "</urlset>").encode("utf-8")


ROBOTS = ("User-agent: *\nAllow: /\n\nSitemap: " + BASE + "/sitemap.xml\n").encode("utf-8")
