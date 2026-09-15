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
    urls = [BASE + "/"] + [BASE + "/l/" + str(l.get("id")) for l in all_listings(hub)]
    body = "".join("<url><loc>" + esc(u) + "</loc></url>" for u in urls)
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">" + body + "</urlset>").encode("utf-8")


ROBOTS = ("User-agent: *\nAllow: /\n\nSitemap: " + BASE + "/sitemap.xml\n").encode("utf-8")
