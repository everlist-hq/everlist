import email.utils
import html as _html
import os
import smtplib
from email.message import EmailMessage

# Phase B+, owner request 2026-09-17: make hub emails look proper and legit.
# Standards used by every serious sender:
#   * multipart/alternative: rich HTML + plain-text fallback (deliverability)
#   * RFC 2369 List-Unsubscribe + RFC 8058 One-Click (Gmail unsubscribe button)
#   * proper From display name, Date, Message-ID, List-Id headers
#   * table-based, inline-styled HTML (email clients strip <style> tags)
# Brand: EverList dark navy + emerald green + cyan accent (site palette).
# Everything is honest: sender identified, escrow explained, easy opt-out.

PUBLIC_URL = os.environ.get("HUB_PUBLIC_URL", "https://everlist.network").rstrip("/")
BRAND = "EverList"
TAGLINE = "a listing is something with a date where a human is needed"

_C = {"bg": "#f2f5f9", "navy": "#0d1b2a", "ink": "#22374d", "mut": "#62788f",
      "green": "#2ecc71", "green_d": "#1f9d55", "cyan": "#38bdf8", "line": "#dde5ee",
      "chipbg": "#e8f8f0", "card": "#ffffff", "foot": "#eef2f7"}


def _esc(s):
    return _html.escape(str(s), quote=True)


def _detail_rows(rows):
    trs = []
    for k, v in rows:
        trs.append(
            '<tr>'
            '<td style="padding:8px 0;color:%s;font-size:13px;width:38%%;">%s</td>'
            '<td style="padding:8px 0;color:%s;font-size:13px;font-weight:600;text-align:right;">%s</td>'
            '</tr>' % (_C["mut"], _esc(k), _C["ink"], _esc(v)))
    return ('<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
            'style="background:%s;border-radius:8px;padding:6px 16px;margin:0 0 20px;">'
            % _C["bg"]) + "".join(trs) + '</table>'


def _btn(label, url):
    return ('<table role="presentation" cellpadding="0" cellspacing="0"><tr>'
            '<td style="background:%s;border-radius:8px;">'
            '<a href="%s" style="display:inline-block;padding:11px 26px;color:#ffffff;'
            'font-size:14px;font-weight:700;text-decoration:none;">%s</a>'
            '</td></tr></table>' % (_C["green_d"], _esc(url), _esc(label)))


def _shell(heading, body_html, footer_html):
    return (
        '<!DOCTYPE html><html><body style="margin:0;padding:0;background:%s;">'
        '<table role="presentation" width="100%%" cellpadding="0" cellspacing="0" '
        'style="background:%s;padding:28px 12px;"><tr><td align="center">'
        '<table role="presentation" width="600" cellpadding="0" cellspacing="0" '
        'style="max-width:600px;width:100%%;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">'
        '<tr><td style="background:%s;padding:22px 32px;border-radius:10px 10px 0 0;">'
        '<a href="%s" style="text-decoration:none;">'
        '<span style="color:%s;font-size:21px;font-weight:800;letter-spacing:.4px;">Ever</span>'
        '<span style="color:%s;font-size:21px;font-weight:800;letter-spacing:.4px;">List</span></a>'
        '<div style="color:#9fb3c8;font-size:11px;margin-top:5px;">%s</div>'
        '</td></tr>'
        '<tr><td style="background:%s;padding:30px 32px 26px;">'
        '<h1 style="margin:0 0 14px;color:%s;font-size:20px;line-height:1.3;">%s</h1>'
        '%s'
        '</td></tr>'
        '<tr><td style="background:%s;padding:16px 32px;border-radius:0 0 10px 10px;">'
        '<div style="color:#7a8ca0;font-size:11px;line-height:1.7;">%s</div>'
        '</td></tr>'
        '</table></td></tr></table></body></html>'
        % (_C["bg"], _C["bg"], _C["navy"], PUBLIC_URL, _C["green"], _C["cyan"],
           _esc(TAGLINE), _C["card"], _C["navy"], _esc(heading), body_html,
           _C["foot"], footer_html))


def _p(text):
    return ('<p style="margin:0 0 18px;color:%s;font-size:14px;line-height:1.6;">%s</p>'
            % (_C["ink"], text))


def _footer_lines(unsub_url, note=None):
    lines = ['Sent by the %s hub (%s) - you have booking notifications enabled.'
             % (_esc(BRAND), _esc(PUBLIC_URL.replace("https://", "")))]
    if note:
        lines.append(_esc(note))
    if unsub_url:
        lines.append('<a href="%s" style="color:%s;">Unsubscribe from booking emails</a>'
                     ' &nbsp;or say <b>notify off</b> in the EverList chat.'
                     % (_esc(unsub_url), _C["cyan"]))
    else:
        lines.append('Say <b>notify off</b> in the EverList chat to turn these off.')
    return "<br>".join(lines)


# ---------------------------------------------------------------- booking ---

def booking_email(event, *, title, booking_id, amount, escrow, role, note=None,
                   unsub_url=None):
    """event: created|released|refunded|cancelled; role: owner|buyer.
    Returns dict(subject, text, html)."""
    amt = ('%.2f' % amount) if isinstance(amount, (int, float)) else str(amount)
    view = PUBLIC_URL + "/booking/" + _esc(booking_id)

    if event == "created" and role == "owner":
        heading = "New booking - escrow is holding the payment"
        intro = (_p('Someone just booked <b>%s</b>. The money is already sitting in escrow - '
                    'it moves to you when you confirm the booking.' % _esc(title))
                 + _detail_rows([("Listing", title), ("Booking", booking_id),
                                 ("Amount held", amt), ("Escrow", escrow)])
                 + _p('Confirm it in your EverList dashboard under <b>my-listings</b> '
                      '(or reply to your agent). Unconfirmed bookings auto-refund when '
                      'the refund window lapses.')
                 + _btn("Open EverList", PUBLIC_URL))
        text = ("New booking for: %s (booking %s)\n"
                "Amount: %s (escrow: %s)\n\n"
                "Confirm it in the EverList chat (my-listings) or via POST /book/%s/confirm.\n"
                "Unconfirmed bookings auto-refund when the refund window lapses."
                % (title, booking_id, amt, escrow, booking_id))
        return {"subject": "New booking: " + title, "text": _with_unsub(text) + (("\n\n" + note) if note else ""), "unsub_url": unsub_url, "html": _shell(heading, intro,
                _footer_lines(unsub_url, note=note))}

    if event == "released" and role == "buyer":
        heading = "Your booking is confirmed"
        intro = (_p('The organizer confirmed <b>%s</b>. Escrow released the payment - '
                    'you are all set.' % _esc(title))
                 + _detail_rows([("Listing", title), ("Booking", booking_id),
                                 ("Amount", amt), ("Confirmation", booking_id + "-ticket")])
                 + _btn("View on EverList", view))
        text = ("Your booking for: %s (booking %s) is confirmed.\n"
                "Escrow released to the organizer. Amount: %s.\n"
                "Confirmation: %s-ticket" % (title, booking_id, amt, booking_id))
        return {"subject": "Confirmed: " + title, "text": _with_unsub(text) + (("\n\n" + note) if note else ""), "unsub_url": unsub_url, "html": _shell(heading, intro,
                _footer_lines(unsub_url, note=note))}

    if event == "refunded" and role == "buyer":
        heading = "Booking cancelled - full refund on its way"
        intro = (_p('Your booking for <b>%s</b> was cancelled. Escrow refunded you in full - '
                    'no money was lost.' % _esc(title))
                 + _detail_rows([("Listing", title), ("Booking", booking_id),
                                 ("Refunded", amt)])
                 + _btn("Find something else", PUBLIC_URL))
        text = ("Your booking for: %s (booking %s) was cancelled.\n"
                "Full refund. Amount: %s." % (title, booking_id, amt))
        return {"subject": "Refunded: " + title, "text": _with_unsub(text) + (("\n\n" + note) if note else ""), "unsub_url": unsub_url, "html": _shell(heading, intro,
                _footer_lines(unsub_url, note=note))}

    if event == "cancelled" and role == "owner":
        heading = "A booking was cancelled"
        intro = (_p('Booking <b>%s</b> for <b>%s</b> was cancelled by the buyer. '
                    'Escrow refunded the buyer automatically.' % (_esc(booking_id), _esc(title)))
                 + _detail_rows([("Listing", title), ("Booking", booking_id),
                                 ("Refunded to buyer", amt)])
                 + _btn("Open EverList", PUBLIC_URL))
        text = ("Booking %s for: %s was cancelled by the buyer.\n"
                "Escrow refunded to the buyer. Amount: %s." % (booking_id, title, amt))
        return {"subject": "Cancelled: " + title, "text": _with_unsub(text) + (("\n\n" + note) if note else ""), "unsub_url": unsub_url, "html": _shell(heading, intro,
                _footer_lines(unsub_url, note=note))}

    raise ValueError("unknown event/role: %s/%s" % (event, role))


def _with_unsub(text):
    return text + "\n\nTurn notifications off: say notify off in the EverList chat."


# ------------------------------------------------------------------ digest ---

def organizer_digest(*, org_name, week_start, week_end, created=0, confirmed=0,
                     cancelled=0, gross=0.0, listings_active=0, unsub_url=None):
    """Weekly summary for an organizer. Amounts in hub currency. Returns
    dict(subject, text, html)."""
    span = '%s to %s' % (week_start, week_end)
    heading = 'Your week on EverList'
    rows = [("Week", span), ("Active listings", listings_active)]
    if created:
        rows.append(("New bookings", created))
    if confirmed:
        rows.append(("Confirmed (escrow released)", confirmed))
    if cancelled:
        rows.append(("Cancelled (auto-refunded)", cancelled))
    rows.append(("Gross booked", '%.2f' % gross))
    intro = _p('Here is your week at a glance. Nothing to do unless something '
               'needs confirming - unconfirmed bookings auto-refund when the '
               'refund window lapses.')
    if created == 0 and confirmed == 0 and cancelled == 0:
        intro = _p('A quiet week - no new bookings. Your listings stay up and '
                   'bookable; agents can find them around the clock.')
        body = intro + _detail_rows(rows) + _btn('Open EverList', PUBLIC_URL)
    else:
        body = intro + _detail_rows(rows) + _btn('Review bookings', PUBLIC_URL)
    text = ('Your week on EverList (%s)\n'
            'Active listings: %d\nNew bookings: %d\nConfirmed: %d\n'
            'Cancelled: %d\nGross booked: %.2f\n\n'
            'Turn notifications off: say notify off in the EverList chat.'
            % (span, listings_active, created, confirmed, cancelled, gross))
    return {"subject": "Your week on EverList: %d booking%s" % (
                created + confirmed + cancelled, '' if (created + confirmed + cancelled) == 1 else 's'),
            "text": text, "html": _shell(heading, body, _footer_lines(unsub_url))}


# ------------------------------------------------------------------- codes ---

def code_email(kind, code, minutes=15):
    """kind: verify|recover. Returns dict(subject, text, html)."""
    if kind == "verify":
        subject = "EverList email verification"
        heading = "Verify your email"
        intro = _p('Enter this code in EverList to link and verify your email address. '
                   'It works for <b>%d minutes</b>.' % minutes)
        text = ("Your EverList verification code: %s\nValid %d minutes. "
                "If you did not request this, ignore it." % (code, minutes))
    else:
        subject = "EverList account recovery"
        heading = "Account recovery"
        intro = _p('Use this code together with your email and a new account code to '
                   'recover access. It works for <b>%d minutes</b>.' % minutes)
        text = ("Your EverList recovery code: %s\nValid %d minutes.\n"
                "Confirm with email + code + a new code at /accounts/email/recover/confirm."
                % (code, minutes))
    code_html = ('<div style="background:%s;border:1px solid %s;border-radius:8px;'
                 'padding:16px 20px;margin:0 0 20px;text-align:center;">'
                 '<span style="font-family:Menlo,Consolas,monospace;font-size:26px;'
                 'letter-spacing:6px;color:%s;font-weight:700;">%s</span></div>'
                 % (_C["bg"], _C["line"], _C["navy"], _esc(code)))
    warn = _p('<span style="color:%s;font-size:12px;">If you did not request this, '
              'ignore this email - nothing changes.</span>' % _C["mut"])
    html = _shell(heading, intro + code_html + warn, _footer_lines(None))
    return {"subject": subject, "text": text, "html": html}


# ------------------------------------------------------------------- MIME ---

def mime_message(from_addr, to_addr, subject, text, html=None,
                 unsub_url=None, list_id=None):
    msg = EmailMessage()
    disp = "%s <%s>" % (BRAND, from_addr)
    msg["From"] = disp
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg["Date"] = email.utils.formatdate(localtime=False)
    msg["Message-ID"] = email.utils.make_msgid(domain=from_addr.split("@", 1)[-1])
    if list_id:
        msg["List-Id"] = "%s notifications <%s.%s>" % (
            BRAND, list_id, from_addr.split("@", 1)[-1])
    if unsub_url:
        msg["List-Unsubscribe"] = "<%s>" % unsub_url
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    return msg


def send_smtp(host, port, user, password, from_addr, to, msg, timeout=15):
    with smtplib.SMTP(host, int(port), timeout=timeout) as srv:
        srv.starttls()
        srv.login(user, password)
        srv.send_message(msg)


# ------------------------------------------------------- unsubscribe page ---

def unsub_page(ok=True, already=False):
    if ok:
        head = "You're unsubscribed"
        body = _p('You will no longer receive booking notification emails. '
                  'Your bookings and escrow are unaffected - say <b>notify on</b> '
                  'in the EverList chat any time to turn them back on.')
    elif already is not None and not ok:
        head = "Link expired"
        body = _p('That unsubscribe link is not valid. Say <b>notify off</b> '
                  'in the EverList chat instead.')
    else:
        head = "You're unsubscribed"
        body = _p('No changes were needed - notifications were already off.')
    return _shell(head, body + _btn("Go to EverList", PUBLIC_URL),
                  _footer_lines(None))
