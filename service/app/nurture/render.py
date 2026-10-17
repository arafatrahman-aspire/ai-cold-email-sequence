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

from app.mail import templates
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
    style: Optional[dict[str, Any]] = None,
) -> Rendered:
    """``style`` is the shared email design (settings ``email_style``): it fills
    the signature and footer fields nurture's own branding leaves empty, and
    gives the accent colour."""
    style = style or {}
    brand = dict(branding)
    for mine, shared in (("sender_name", "name"), ("sender_title", "title"), ("company_name", "company"),
                         ("company_address", "address")):
        if not str(brand.get(mine) or "").strip() and style.get(shared):
            brand[mine] = style[shared]
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
    accent = templates.accent_color(style.get("accent"))
    anchors = {placeholder: (f'<a href="{html.escape(tracked[kind])}" style="color:{accent};font-weight:600">'
                             f'{html.escape(label(kind))}</a>')
               for placeholder, kind in PLACEHOLDER_TYPE.items() if kind in tracked}
    # One clear button for the email's main ask, when its link is in the email.
    primary = {"demo": "demo", "pricing": "pricing"}.get(draft.cta_type, "resource")
    if primary not in tracked:
        primary = next((k for k in ("resource", "demo", "pricing") if k in tracked), None)
    buttons = {"demo": "Book a short demo", "pricing": "See pricing",
               "resource": f"Read {clean((resource or {}).get('title'), 60)}".strip() if resource else "Read more"}
    cta = (buttons[primary], tracked[primary]) if primary else None
    logo = clean(brand.get("logo_url"), 400)
    html_doc = templates.branded(
        greeting=greeting, body_html=templates.paragraphs(draft.body, accent, anchors),
        preheader=clean(draft.preheader, 140), subject=clean(draft.subject, 200),
        brand=templates.Branding(
            company=company, address=address, logo_url=logo if logo.startswith("https://") else "", accent=accent,
            signature=templates.Signature(name=sender, title=title, company=company,
                                          website=clean(style.get("website"), 200), phone=clean(style.get("phone"), 40))),
        cta=cta, unsubscribe_url=unsubscribe_url,
        reason=f"You are receiving this because you were in touch with {company}." if company else "")

    problems += checks.unresolved(text) + checks.unresolved(html_doc)
    return Rendered(text=text, html=html_doc, problems=problems)
