"""From a checked draft to the email that is sent.

The placeholders become tracked links, /n/c/<email id>/<demo|pricing|resource>
(our own redirect, which logs the click before forwarding; no link table is
needed), and the body is wrapped in a fixed layout: greeting, signature,
company footer and the unsubscribe link. The model only ever wrote the body
paragraphs, so the layout cannot be changed by it. Pure function.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any, Optional

from app.nurture import checks
from app.nurture.safety import clean

LABELS = {"demo": "book a short demo", "pricing": "see pricing"}
PLACEHOLDER_TYPE = {checks.DEMO: "demo", checks.PRICING: "pricing", checks.RESOURCE: "resource"}


@dataclass
class Rendered:
    text: str
    html: str
    problems: list[str] = field(default_factory=list)


def destinations(branding: dict[str, Any], resource: Optional[dict[str, Any]]) -> dict[str, str]:
    out = {"demo": (branding.get("demo_url") or "").strip(),
           "pricing": (branding.get("pricing_url") or "").strip()}
    if resource:
        out["resource"] = (resource.get("url") or "").strip()
    return out


def render(
    draft: checks.Draft,
    *,
    message_id: str,
    first_name: Optional[str],
    resource: Optional[dict[str, Any]],
    branding: dict[str, Any],
    public_url: str,
    unsubscribe_url: str,
    demo_fallback: str = "",
) -> Rendered:
    brand = dict(branding)
    if not brand.get("demo_url") and demo_fallback:
        brand["demo_url"] = demo_fallback
    dest = destinations(brand, resource)
    base = public_url.rstrip("/")
    problems: list[str] = []
    tracked: dict[str, str] = {}

    for placeholder, kind in PLACEHOLDER_TYPE.items():
        if placeholder not in draft.body:
            continue
        if not dest.get(kind):
            problems.append(f"no destination URL for the {kind} link")
            continue
        tracked[kind] = f"{base}/n/c/{message_id}/{kind}"

    def label(kind: str) -> str:
        if kind == "resource":
            return f"“{clean((resource or {}).get('title'), 150)}”"
        return LABELS[kind]

    name = clean(first_name, 40).split(" ")[0] if clean(first_name, 40) else ""
    greeting = f"Hi {name}," if name else "Hi there,"
    sender = clean(brand.get("sender_name"), 80)
    title = clean(brand.get("sender_title"), 80)
    company = clean(brand.get("company_name"), 80)
    address = clean(brand.get("company_address"), 200)

    # Plain text.
    body_text = draft.body
    for placeholder, kind in PLACEHOLDER_TYPE.items():
        if kind in tracked:
            body_text = body_text.replace(placeholder, f"{label(kind)} ({tracked[kind]})")
    signature = "\n".join(p for p in (sender, title, company) if p)
    footer = " · ".join(p for p in (company, address) if p)
    text = (f"{greeting}\n\n{body_text.strip()}\n\n{signature}\n\n--\n"
            + (f"{footer}\n" if footer else "")
            + f"Unsubscribe: {unsubscribe_url}\n")

    # HTML: the body is escaped first, then the placeholders become anchors.
    paragraphs = []
    for para in draft.body.strip().split("\n\n"):
        chunk = html.escape(para.strip()).replace("\n", "<br>")
        for placeholder, kind in PLACEHOLDER_TYPE.items():
            if kind in tracked:
                chunk = chunk.replace(placeholder,
                                      f'<a href="{html.escape(tracked[kind])}">{html.escape(label(kind))}</a>')
        paragraphs.append(f'<p style="margin:0 0 14px">{chunk}</p>')
    logo = clean(brand.get("logo_url"), 400)
    logo_html = (f'<img src="{html.escape(logo)}" alt="{html.escape(company)}" height="32" '
                 f'style="display:block;margin-bottom:20px">') if logo.startswith("https://") else ""
    sig_html = "<br>".join(html.escape(p) for p in (sender, title, company) if p)
    pre = html.escape(clean(draft.preheader, 140))
    html_doc = f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#f5f6f8">
<span style="display:none;max-height:0;overflow:hidden;opacity:0">{pre}</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f6f8">
<tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:8px">
<tr><td style="padding:28px 32px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.55;color:#1f2329">
{logo_html}<p style="margin:0 0 14px">{html.escape(greeting)}</p>
{''.join(paragraphs)}
<p style="margin:22px 0 0">{sig_html}</p>
</td></tr></table>
<p style="font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:1.5;color:#6b7280;margin:16px 0 0">
{html.escape(footer)}{'<br>' if footer else ''}<a href="{html.escape(unsubscribe_url)}" style="color:#6b7280">Unsubscribe</a>
</p>
</td></tr></table>
</body></html>"""

    problems += checks.unresolved(text) + checks.unresolved(html_doc)
    return Rendered(text=text, html=html_doc, problems=problems)
