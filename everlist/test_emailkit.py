"""Email format regression suite (owner request 2026-09-17: professional mail).
Locks the branded-email contract: multipart MIME with HTML+text, brand shell,
honest escrow copy, notify-off line in every booking text, RFC 8058 headers,
and the refunded->cancelled owner mapping. Run: python test_emailkit.py
"""
import sys
import emailkit

PASS, FAIL = [], []

def check(name, cond, detail=""):
    cond = bool(cond)
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" -- " + str(detail)[:200] if detail and not cond else ""))

TITLE = "Rooftop Jazz Night"
CASES = [("created", "owner"), ("released", "buyer"), ("refunded", "buyer"), ("cancelled", "owner")]
for ev, role in CASES:
    e = emailkit.booking_email(ev, title=TITLE, booking_id="bk-42",
                               amount=25.0, escrow="HELD", role=role,
                               note="Reason: buyer changed plans",
                               unsub_url="https://everlist.network/accounts/notify/unsubscribe?u=acct-x&t=tok")
    check(f"{ev}/{role}: subject carries title", TITLE in e["subject"], e["subject"])
    check(f"{ev}/{role}: text has notify-off line", "notify off" in e["text"])
    check(f"{ev}/{role}: text carries amount + booking id", "25.00" in e["text"] and "bk-42" in e["text"])
    check(f"{ev}/{role}: text carries note", "Reason: buyer changed plans" in e["text"])
    check(f"{ev}/{role}: html is branded shell", "EverList" in e["html"] and "<html" in e["html"])
    check(f"{ev}/{role}: html carries escrow honesty", "escrow" in e["html"].lower())
    check(f"{ev}/{role}: html has one-click unsubscribe", e["html"].count("accounts/notify/unsubscribe") >= 1)
    check(f"{ev}/{role}: html escapes the listing title", "<b>Rooftop Jazz Night</b>" in e["html"])

# XSS guard: hostile listing title must never inject HTML
e = emailkit.booking_email("created", title="<script>alert(1)</script>", booking_id="bk-x",
                           amount=1, escrow="HELD", role="owner")
check("xss: hostile title escaped in html", "<script>" not in e["html"] and "&lt;script&gt;" in e["html"])

c = emailkit.code_email("verify", "A1B2C3")
check("verify code in text", "A1B2C3" in c["text"])
check("verify code in html", "A1B2C3" in c["html"])
r = emailkit.code_email("recover", "D4E5F6")
check("recover subject", r["subject"] == "EverList account recovery")

m = emailkit.mime_message("notify@everlist.network", "to@example.dev", "S", "plain",
                          html="<html>x</html>", unsub_url="https://everlist.network/u?t=1",
                          list_id="booking")
check("mime multipart/alternative", m.get_content_type() == "multipart/alternative")
check("mime from display name", m["From"] == "EverList <notify@everlist.network>")
check("mime list-unsubscribe", m["List-Unsubscribe"] == "<https://everlist.network/u?t=1>")
check("mime one-click post header", m["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click")
check("mime has message-id + date", bool(m["Message-ID"]) and bool(m["Date"]))

print(f"\nemailkit suite: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", ", ".join(FAIL))
    sys.exit(1)
sys.exit(0)
