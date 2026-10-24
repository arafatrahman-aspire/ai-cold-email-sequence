"""How our emails look. Every email goes out as plain text plus HTML.

Three looks, each fitted to who reads it:

* ``personal`` — cold emails and answers to replies. A well-set personal email
  with a proper signature: no banner, no images, no buttons. Designed cold
  email lands in Gmail's Promotions tab and gets fewer replies, so the polish
  is in the typography and signature, not in decoration.
* ``branded`` — Email Nurture. A designed newsletter-style layout: logo
  header, card, a call-to-action button, a footer with the address and a
  one-click unsubscribe.
* ``internal`` — notifications for our own team (new reply, hand-off, alerts):
  a heading, the facts in a table, the lead's words quoted, a button to the
  console.

Built for email clients, not browsers: tables, inline styles, web-safe fonts,
a hidden preheader, and a small <style> block only for phones and dark mode
(clients that drop it still get a correct email). Pure functions.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Optional

FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
INK, MUTED, LINE, PAGE = "#1f2937", "#6b7280", "#e5e7eb", "#f3f4f6"
DEFAULT_ACCENT = "#2563eb"
_URL = re.compile(r"(https?://[^\s<>\"')]+[^\s<>\"').,;:!?])")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def accent_color(value: Optional[str]) -> str:
    return value if value and _COLOR.match(value.strip()) else DEFAULT_ACCENT


def layout_name(value: object) -> str:
    return value if value in ("plain", "card") else "plain"


def e(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


# --- signature ------------------------------------------------------------------

@dataclass
class Signature:
    name: str = ""
    title: str = ""
    company: str = ""
    website: str = ""
    phone: str = ""
    address: str = ""

    @staticmethod
    def from_settings(value: Optional[dict], name: str = "") -> "Signature":
        v = value if isinstance(value, dict) else {}

        def get(k: str, limit: int = 120) -> str:
            return str(v.get(k) or "").strip()[:limit]

        return Signature(name=get("name") or (name or "").strip()[:120], title=get("title"),
                         company=get("company"), website=get("website", 200), phone=get("phone", 40),
                         address=get("address", 200))

    def text(self) -> str:
        role = " | ".join(p for p in (self.title, self.company) if p)
        contact = " | ".join(p for p in (self.phone, self.website) if p)
        return "\n".join(p for p in (self.name, role, contact) if p)

    def html(self, accent: str = DEFAULT_ACCENT) -> str:
        rows = []
        if self.name:
            rows.append(f'<div class="ink" style="font-weight:600;color:{INK}">{e(self.name)}</div>')
        role = " · ".join(e(p) for p in (self.title, self.company) if p)
        if role:
            rows.append(f'<div class="soft" style="color:{MUTED}">{role}</div>')
        contact = []
        if self.phone:
            contact.append(f'<a href="tel:{e(re.sub(r"[^0-9+]", "", self.phone))}" '
                           f'style="color:{MUTED};text-decoration:none">{e(self.phone)}</a>')
        if self.website:
            url = self.website if self.website.startswith("http") else f"https://{self.website}"
            shown = re.sub(r"^https?://(www\.)?", "", self.website).rstrip("/")
            contact.append(f'<a href="{e(url)}" style="color:{accent};text-decoration:none">{e(shown)}</a>')
        if contact:
            rows.append(f'<div class="soft" style="color:{MUTED}">{" · ".join(contact)}</div>')
        if not rows:
            return ""
        return (f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:20px">'
                f'<tr><td style="border-left:3px solid {accent};padding:2px 0 2px 12px;'
                f'font-family:{FONT};font-size:13px;line-height:1.5">{"".join(rows)}</td></tr></table>')


# --- shared pieces ---------------------------------------------------------------

def paragraphs(text: str, accent: str = DEFAULT_ACCENT, links: Optional[dict[str, str]] = None) -> str:
    """Plain text -> <p> blocks: escaped first, then bare URLs become links and
    ``links`` (placeholder -> ready-made <a> HTML) are put back in."""
    out = []
    for para in re.split(r"\n\s*\n", (text or "").strip()):
        if not para.strip():
            continue
        chunk = e(para.strip())
        chunk = _URL.sub(lambda m: f'<a href="{m.group(1)}" style="color:{accent}">{m.group(1)}</a>', chunk)
        for placeholder, anchor in (links or {}).items():
            chunk = chunk.replace(e(placeholder), anchor)
        out.append(f'<p style="margin:0 0 16px">{chunk.replace(chr(10), "<br>")}</p>')
    return "".join(out)


def _head(title: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{e(title)}</title>
<style>
  @media (max-width:620px) {{ .wrap {{ width:100% !important; }} .pad {{ padding:24px 20px !important; }} }}
  @media (prefers-color-scheme:dark) {{
    .page {{ background:#111827 !important; }}
    .card {{ background:#1f2937 !important; }}
    .ink, .ink p {{ color:#f3f4f6 !important; }}
    .soft {{ color:#9ca3af !important; }}
  }}
</style>
</head>"""


def _preheader(text: str) -> str:
    if not text:
        return ""
    # The trailing spacers stop clients from filling the preview with body text.
    return (f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;mso-hide:all">'
            f'{e(text)}{"&#8199;&#65279;&#847; " * 30}</div>')


def button(label: str, url: str, accent: str = DEFAULT_ACCENT) -> str:
    """A button that renders in every client, Outlook included."""
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 20px">'
            f'<tr><td style="border-radius:6px;background:{accent}">'
            f'<a href="{e(url)}" style="display:inline-block;padding:12px 22px;font-family:{FONT};'
            f'font-size:15px;font-weight:600;color:#ffffff;text-decoration:none;border-radius:6px">'
            f'{e(label)}</a></td></tr></table>')


# --- personal: cold emails and answers to replies ----------------------------------

@dataclass
class Email:
    text: str
    html: str


def personal(body: str, signature: Signature, *, sign_off: str = "", opt_out: str = "",
             accent: str = DEFAULT_ACCENT, subject: str = "", layout: str = "plain") -> Email:
    """``sign_off`` ("Best,") goes before the signature when the body has none;
    ``opt_out`` is one quiet line at the very end (cold emails only).

    ``layout``: "plain" is a bare personal email; "card" sets the same text in a
    white card on a light page with the company name above it (still no images
    or buttons, so it stays close to a personal email)."""
    body = (body or "").strip()
    closing = f"{sign_off}\n" if sign_off else ""
    sig_text = signature.text()
    text = body + (f"\n\n{closing}{sig_text}" if sig_text or closing else "")
    if opt_out:
        text += f"\n\n{opt_out}"

    sign_html = f'<p style="margin:0">{e(sign_off)}</p>' if sign_off else ""
    opt_html = (f'<p class="soft" style="margin:28px 0 0;font-size:12px;line-height:1.5;color:{MUTED}">'
                f'{e(opt_out)}</p>') if opt_out else ""
    inner = f"{paragraphs(body, accent)}{sign_html}{signature.html(accent)}{opt_html}"
    if layout == "card":
        name = (f'<tr><td style="padding:0 4px 14px"><span class="ink" style="font-family:{FONT};font-size:17px;'
                f'font-weight:700;color:{INK};letter-spacing:-0.01em">{e(signature.company)}</span></td></tr>'
                if signature.company else "")
        doc = f"""{_head(subject)}
<body class="page" style="margin:0;padding:0;background:{PAGE}">
<table role="presentation" class="page" width="100%" cellpadding="0" cellspacing="0" style="background:{PAGE}">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" class="wrap" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%">
{name}<tr><td class="card" style="background:#ffffff;border-radius:10px;border-top:4px solid {accent}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
<tr><td class="pad ink" style="padding:30px 34px;font-family:{FONT};font-size:15px;line-height:1.6;color:{INK};text-align:left">
{inner}
</td></tr></table>
</td></tr></table>
</td></tr></table>
</body></html>"""
        return Email(text=text, html=doc)

    # The page and text colours both flip in dark mode (class "page" / "ink"):
    # a fixed white background under light dark-mode text is unreadable.
    doc = f"""{_head(subject)}
<body class="page" style="margin:0;padding:0;background:#ffffff">
<table role="presentation" class="page" width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff">
<tr><td style="padding:8px 16px">
<table role="presentation" class="wrap" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%">
<tr><td class="ink" style="font-family:{FONT};font-size:15px;line-height:1.6;color:{INK};text-align:left">
{inner}
</td></tr></table>
</td></tr></table>
</body></html>"""
    return Email(text=text, html=doc)


# --- branded: Email Nurture ----------------------------------------------------------

@dataclass
class Branding:
    company: str = ""
    address: str = ""
    logo_url: str = ""
    accent: str = DEFAULT_ACCENT
    signature: Signature = field(default_factory=Signature)


def branded(*, greeting: str, body_html: str, preheader: str, subject: str, brand: Branding,
            cta: Optional[tuple[str, str]], unsubscribe_url: str, reason: str = "") -> str:
    """The nurture layout around already-built body paragraphs."""
    accent = accent_color(brand.accent)
    if brand.logo_url.startswith("https://"):
        header = (f'<img src="{e(brand.logo_url)}" alt="{e(brand.company)}" height="32" '
                  f'style="display:block;height:32px;width:auto;border:0">')
    else:
        header = (f'<span style="font-family:{FONT};font-size:18px;font-weight:700;color:{INK};'
                  f'letter-spacing:-0.01em">{e(brand.company)}</span>')
    cta_html = button(cta[0], cta[1], accent) if cta else ""
    footer_bits = [p for p in (brand.company, brand.address) if p]
    reason_html = f"{e(reason)}<br>" if reason else ""
    return f"""{_head(subject)}
<body class="page" style="margin:0;padding:0;background:{PAGE}">
{_preheader(preheader)}
<table role="presentation" class="page" width="100%" cellpadding="0" cellspacing="0" style="background:{PAGE}">
<tr><td align="center" style="padding:28px 12px">
<table role="presentation" class="wrap" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%">
<tr><td style="padding:0 4px 16px">{header}</td></tr>
<tr><td class="card" style="background:#ffffff;border-radius:10px;border-top:4px solid {accent}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">
<tr><td class="pad ink" style="padding:32px 36px;font-family:{FONT};font-size:16px;line-height:1.65;color:{INK}">
<p style="margin:0 0 16px">{e(greeting)}</p>
{body_html}{cta_html}{brand.signature.html(accent)}
</td></tr></table>
</td></tr>
<tr><td class="soft" style="padding:20px 8px 0;font-family:{FONT};font-size:12px;line-height:1.6;color:{MUTED};text-align:center">
{reason_html}{" · ".join(e(p) for p in footer_bits)}{"<br>" if footer_bits else ""}<a href="{e(unsubscribe_url)}" style="color:{MUTED};text-decoration:underline">Unsubscribe</a>
</td></tr>
</table>
</td></tr></table>
</body></html>"""


# --- internal: notifications to our own team -------------------------------------------

def internal(*, title: str, intro: str = "", facts: list[tuple[str, str]] = (), quote: str = "",
             quote_label: str = "", note: str = "", action: Optional[tuple[str, str]] = None,
             tone: str = "info") -> str:
    accent = {"info": DEFAULT_ACCENT, "good": "#059669", "warning": "#d97706", "critical": "#dc2626"}.get(tone, DEFAULT_ACCENT)
    rows = "".join(
        f'<tr><td width="130" style="width:130px;padding:6px 12px 6px 0;color:{MUTED};white-space:nowrap;vertical-align:top">{e(k)}</td>'
        f'<td style="padding:6px 0;color:{INK}">{e(v)}</td></tr>' for k, v in facts if v)
    facts_html = (f'<table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;margin:4px 0 16px;'
                  f'border-top:1px solid {LINE};border-bottom:1px solid {LINE};font-size:14px">{rows}</table>') if rows else ""
    quote_html = ""
    if quote:
        label = (f'<div style="font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:{MUTED};'
                 f'margin-bottom:6px">{e(quote_label)}</div>') if quote_label else ""
        quote_html = (f'{label}<div style="background:{PAGE};border-left:3px solid {accent};padding:12px 14px;'
                      f'border-radius:4px;font-size:14px;line-height:1.55;color:{INK};margin-bottom:16px">'
                      f'{e(quote).replace(chr(10), "<br>")}</div>')
    intro_html = paragraphs(intro, accent) if intro else ""
    note_html = (f'<p class="soft" style="margin:0 0 16px;font-size:13px;color:{MUTED}">'
                 f'{e(note).replace(chr(10), "<br>")}</p>') if note else ""
    action_html = button(action[0], action[1], accent) if action else ""
    return f"""{_head(title)}
<body class="page" style="margin:0;padding:0;background:{PAGE}">
<table role="presentation" class="page" width="100%" cellpadding="0" cellspacing="0" style="background:{PAGE}">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" class="wrap card" width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;background:#ffffff;border-radius:10px;border-top:4px solid {accent}">
<tr><td class="pad ink" style="padding:28px 32px;font-family:{FONT};font-size:15px;line-height:1.6;color:{INK}">
<h1 style="margin:0 0 12px;font-size:19px;line-height:1.35;color:{INK}">{e(title)}</h1>
{intro_html}{facts_html}{quote_html}{note_html}{action_html}
</td></tr></table>
<p class="soft" style="margin:14px 0 0;font-family:{FONT};font-size:12px;color:{MUTED}">Sent by the Outreach Console</p>
</td></tr></table>
</body></html>"""
