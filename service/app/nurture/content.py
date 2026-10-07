"""Nurture content: step briefs, fallback emails and the resource library.

No tables: the starting text lives here, and whatever a person edits in the
console is stored in cold_email.settings (keys nurture_briefs,
nurture_fallbacks, nurture_resources). An edit replaces only that one brief
or email and bumps its version, which is logged with every email written.

Placeholders the code turns into tracked links (the AI never writes URLs):
  {{CTA_DEMO}} "book a short demo", {{CTA_PRICING}} "see pricing",
  {{RESOURCE_LINK}} the chosen resource's title.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from app import settings_store

PERSONAS = ("ciso", "it", "hr")
TEMPERATURES = ("warm", "cold")

# --- step briefs ---------------------------------------------------------------
# (goal, resource stages, call-to-action emphasis, max words) per track and step.
_STEPS = {
    "cold": [
        ("Open with one useful insight about a problem they likely have. Earn attention; no product pitch.", ["awareness"], "soft", 140),
        ("Give one practical, self-serve tip they can use this week. Still no product pitch.", ["awareness"], "soft", 140),
        ("Share how peers approach the problem (a simple framework). Educational; no product pitch.", ["awareness", "consideration"], "soft", 150),
        ("First mention of how our platform helps with the problem discussed so far, in one or two sentences.", ["consideration"], "demo", 150),
        ("Show what results look like for teams like theirs, using only the provided resource for any claim.", ["consideration", "decision"], "demo", 150),
        ("A short, polite last note: summarise the value and leave the door open.", ["decision"], "demo", 110),
    ],
    "warm": [
        ("Acknowledge their interest and point to the single most relevant resource. Offer a demo.", ["consideration"], "demo", 130),
        ("Proof: a case study or customer story relevant to their role.", ["consideration", "decision"], "demo", 140),
        ("Value and ROI framing: time saved and risk reduced, with claims only from the provided resource.", ["decision"], "pricing", 140),
        ("Handle the most common hesitation for their role and show it is manageable.", ["consideration"], "demo", 140),
        ("Explain what a demo covers and that pricing is easy to see. Make the next step concrete.", ["decision"], "pricing", 120),
        ("Direct, friendly final call to action.", ["decision"], "demo", 100),
    ],
}
_ANGLES = {
    "ciso": {
        "cold": ["Most incidents still start with a person clicking or replying; human risk is measurable.",
                 "How to baseline phishing susceptibility quickly and track it over time.",
                 "How security leaders report human-risk trends to the board in plain numbers.",
                 "Continuous, automated awareness training with reporting a CISO can take to the board.",
                 "Fewer risky clicks and faster reporting of suspicious email over time.",
                 "One place to measure and reduce human risk, when the timing suits them."],
        "warm": ["Measuring human risk the way they measure technical risk.",
                 "A security team that turned training into measurable behaviour change.",
                 "Risk reduction and audit evidence without extra headcount.",
                 "Concern that training is a box-ticking exercise: show behaviour metrics instead.",
                 "A demo focused on their reporting needs and risk dashboard.",
                 "Short call to see their own human-risk baseline."],
    },
    "it": {
        "cold": ["Phishing-related helpdesk tickets and clean-up eat IT time.",
                 "A simple way to cut the admin work of running phishing simulations.",
                 "How IT teams automate awareness campaigns instead of running them by hand.",
                 "Automated campaigns, user sync and reporting with little admin effort.",
                 "Less manual work and fewer phishing-related tickets for IT teams like theirs.",
                 "Set-and-forget awareness training, whenever they want to look."],
        "warm": ["Getting training running without adding to the IT workload.",
                 "An IT team that automated simulations and reporting.",
                 "Hours saved each month versus running campaigns manually.",
                 "Worry about deployment effort and integrations: show it is straightforward.",
                 "A demo of setup, user sync and automated reports.",
                 "Short call to see how little admin it needs."],
    },
    "hr": {
        "cold": ["Security habits are part of onboarding and culture, not only an IT topic.",
                 "Making mandatory training engaging instead of a chore for employees.",
                 "How people teams track training completion and policy acknowledgement.",
                 "Short, engaging training with completion tracking HR can rely on.",
                 "Better completion rates and less chasing for people teams.",
                 "Engaging awareness training for every employee, when the time is right."],
        "warm": ["Training employees actually complete, with tracking built in.",
                 "A people team that improved completion and engagement.",
                 "Time saved on chasing completion and preparing training records.",
                 "Concern about employee pushback: short, relevant, non-punitive training.",
                 "A demo of the employee experience and completion reports.",
                 "Short call to see the employee experience first-hand."],
    },
}


def default_brief(persona: str, temperature: str, step: int) -> dict[str, Any]:
    goal, stages, cta, words = _STEPS[temperature][step - 1]
    return {"goal": goal, "angle": _ANGLES[persona][temperature][step - 1], "allowed_stages": stages,
            "cta_emphasis": cta, "max_words": words, "version": 1}


# --- fallback emails (persona x step; the greeting and signature are added by code) ---
_FALLBACKS = {
    "ciso": [
        ("Measuring the human side of security risk", "A quick thought on a risk that is hard to see",
         "Most security programmes measure patching, endpoints and alerts closely, yet the risk that starts with a single click is often only measured once a year.\n\nTeams that track it continuously usually find it is one of the easiest risks to reduce.\n\nIf it would help to see how others measure it, you can {{CTA_DEMO}} or {{CTA_PRICING}} at any time."),
        ("A simple way to baseline phishing risk", "One practical step for this week",
         "A useful first step is a baseline: one realistic simulation, then a look at who clicked and who reported it. Reporting rates often say more than click rates.\n\nRepeating it every month turns a guess into a trend you can show.\n\nWhen you want to see this done automatically, you can {{CTA_DEMO}} or {{CTA_PRICING}}."),
        ("How security leaders report human risk", "Keeping the board update simple",
         "Security leaders we speak with keep board reporting on human risk to three numbers: how often people click, how often they report, and how both change over time.\n\nSimple trends like these make the case for the programme without technical detail.\n\nIf you would like to see this kind of reporting, you can {{CTA_DEMO}} or {{CTA_PRICING}}."),
        ("Measuring and reducing human risk in one place", "How our platform fits in",
         "Our platform runs awareness training and phishing simulations continuously and turns the results into reporting you can share with leadership.\n\nIt is built for security teams that want behaviour change they can measure, not just completed courses.\n\nYou can {{CTA_DEMO}} to see it with your own use case in mind, or {{CTA_PRICING}}."),
        ("What progress looks like for security teams", "From a yearly course to continuous improvement",
         "Teams that move from a yearly course to regular, short training usually see people report suspicious email sooner and click less often as the months go by.\n\nThat trend is what makes human risk manageable.\n\nIf you want to see how it would look for your organisation, you can {{CTA_DEMO}} or {{CTA_PRICING}}."),
        ("Leaving the door open", "A last note from me",
         "I will not keep filling your inbox. If measuring and reducing human risk becomes a priority, we would be glad to help.\n\nYou can {{CTA_DEMO}} or {{CTA_PRICING}} whenever the timing suits you."),
    ],
    "it": [
        ("The hidden IT cost of phishing", "Tickets, clean-up and manual campaigns add up",
         "Phishing rarely shows up as one big event for IT. It shows up as tickets, password resets, mailbox clean-ups and the time spent running training by hand.\n\nReducing that work starts with people recognising suspicious email earlier.\n\nIf you would like to see how other IT teams handle it, you can {{CTA_DEMO}} or {{CTA_PRICING}}."),
        ("Less admin for phishing simulations", "A practical tip for IT teams",
         "One quick win: schedule simulations in advance and let them run on their own, instead of building each campaign by hand. Pair them with short training for anyone who clicks.\n\nIt keeps the programme running without taking your time each month.\n\nWhen you want to see it automated end to end, you can {{CTA_DEMO}} or {{CTA_PRICING}}."),
        ("How IT teams automate awareness training", "Set it up once, let it run",
         "IT teams that run awareness training well tend to automate three things: keeping the user list in sync, sending simulations on a schedule, and producing reports without spreadsheets.\n\nOnce those run on their own, the programme stops being a monthly chore.\n\nIf that sounds useful, you can {{CTA_DEMO}} or {{CTA_PRICING}}."),
        ("Awareness training with very little admin", "How our platform helps IT teams",
         "Our platform syncs your users, runs simulations and training on a schedule, and sends reports automatically, so the programme runs without manual campaign work.\n\nIt is designed for IT teams that already have plenty on their plate.\n\nYou can {{CTA_DEMO}} to see the setup, or {{CTA_PRICING}}."),
        ("What changes for IT teams", "Less manual work, fewer phishing tickets",
         "IT teams that automate awareness training usually spend less time on campaign admin and see people report suspicious email instead of clicking it.\n\nBoth mean fewer interruptions for your team.\n\nIf you want to see how it would fit your setup, you can {{CTA_DEMO}} or {{CTA_PRICING}}."),
        ("A last note from me", "Here whenever you need it",
         "This is my last email for now. If automating awareness training ever moves up your list, we would be happy to show you around.\n\nYou can {{CTA_DEMO}} or {{CTA_PRICING}} at any time."),
    ],
    "hr": [
        ("Security habits start at onboarding", "Why people teams play a part",
         "Good security habits are part of how people work, which makes them part of onboarding and culture as much as an IT topic.\n\nShort, relevant training from day one makes a real difference to how people respond to suspicious email.\n\nIf you would like to see how other people teams approach it, you can {{CTA_DEMO}} or {{CTA_PRICING}}."),
        ("Making mandatory training less of a chore", "A practical idea for people teams",
         "Mandatory training gets finished faster when it is short, relevant to the role and spread through the year instead of one long annual course.\n\nPeople also respond better when mistakes in simulations lead to a quick lesson rather than blame.\n\nWhen you want to see training like this, you can {{CTA_DEMO}} or {{CTA_PRICING}}."),
        ("Tracking training without the chasing", "How people teams keep records simple",
         "People teams we speak with want two things from training records: knowing who has finished, and not having to chase everyone else by hand.\n\nAutomatic reminders and a simple completion report usually solve both.\n\nIf that would help your team, you can {{CTA_DEMO}} or {{CTA_PRICING}}."),
        ("Training employees actually finish", "How our platform helps people teams",
         "Our platform delivers short, engaging security awareness training with automatic reminders and completion reports for HR.\n\nIt is designed to feel helpful to employees rather than like another box to tick.\n\nYou can {{CTA_DEMO}} to see the employee experience, or {{CTA_PRICING}}."),
        ("What changes for people teams", "Better completion with less chasing",
         "People teams using short, regular training usually see completion rise and spend far less time sending reminders.\n\nEmployees also tend to find it more useful than a long annual course.\n\nIf you would like to see how it would work for your organisation, you can {{CTA_DEMO}} or {{CTA_PRICING}}."),
        ("A last note from me", "Here when the time is right",
         "I will leave it here for now. If engaging security training for your employees becomes a priority, we would be glad to help.\n\nYou can {{CTA_DEMO}} or {{CTA_PRICING}} whenever it suits you."),
    ],
}


def default_fallback(persona: str, step: int) -> dict[str, Any]:
    subject, preheader, body = _FALLBACKS[persona][step - 1]
    return {"subject": subject, "preheader": preheader, "body": body, "version": 1}


# --- reading and saving -----------------------------------------------------------

async def _stored(key: str) -> dict[str, Any]:
    value = await settings_store.get(key)
    return dict(value) if isinstance(value, dict) else {}   # a copy: callers edit it


async def brief(persona: str, temperature: str, step: int) -> dict[str, Any]:
    edited = (await _stored("nurture_briefs")).get(f"{persona}.{temperature}.{step}")
    return {**default_brief(persona, temperature, step), **(edited or {})}


async def all_briefs() -> list[dict[str, Any]]:
    edited = await _stored("nurture_briefs")
    return [
        {"persona": p, "temperature": t, "step": s,
         **default_brief(p, t, s), **(edited.get(f"{p}.{t}.{s}") or {})}
        for p in PERSONAS for t in TEMPERATURES for s in range(1, 7)
    ]


async def save_brief(persona: str, temperature: str, step: int, fields: dict[str, Any], by: str) -> int:
    edited = await _stored("nurture_briefs")
    current = await brief(persona, temperature, step)
    version = int(current.get("version") or 1) + 1
    edited[f"{persona}.{temperature}.{step}"] = {**fields, "version": version, "updated_by": by}
    await settings_store.set_value("nurture_briefs", edited, "Email Nurture step briefs (edited ones only).")
    return version


async def fallback(persona: str, step: int) -> dict[str, Any]:
    edited = (await _stored("nurture_fallbacks")).get(f"{persona}.{step}")
    return {**default_fallback(persona, step), **(edited or {})}


async def all_fallbacks() -> list[dict[str, Any]]:
    edited = await _stored("nurture_fallbacks")
    return [{"persona": p, "step": s, **default_fallback(p, s), **(edited.get(f"{p}.{s}") or {})}
            for p in PERSONAS for s in range(1, 7)]


async def save_fallback(persona: str, step: int, fields: dict[str, Any], by: str) -> int:
    edited = await _stored("nurture_fallbacks")
    version = int((await fallback(persona, step)).get("version") or 1) + 1
    edited[f"{persona}.{step}"] = {**fields, "version": version, "updated_by": by}
    await settings_store.set_value("nurture_fallbacks", edited, "Email Nurture fallback emails (edited ones only).")
    return version


async def resources() -> list[dict[str, Any]]:
    value = await settings_store.get("nurture_resources")
    return [dict(r) for r in value if isinstance(r, dict)] if isinstance(value, list) else []


async def resource(resource_id: Optional[str]) -> Optional[dict[str, Any]]:
    if not resource_id:
        return None
    return next((r for r in await resources() if r.get("id") == resource_id), None)


async def _save_resources(rows: list[dict[str, Any]]) -> None:
    await settings_store.set_value("nurture_resources", rows, "Email Nurture resource library.")


async def save_resource(resource_id: Optional[str], fields: dict[str, Any]) -> Optional[str]:
    """Add (resource_id None) or update a resource. Saving clears any AI
    tag suggestion, since a person has now decided."""
    rows = await resources()
    if resource_id is None:
        resource_id = uuid.uuid4().hex
        rows.append({"id": resource_id, **fields, "suggestion": None})
    else:
        for r in rows:
            if r.get("id") == resource_id:
                r.update(fields, suggestion=None)
                break
        else:
            return None
    await _save_resources(rows)
    return resource_id


async def delete_resource(resource_id: str) -> bool:
    rows = await resources()
    kept = [r for r in rows if r.get("id") != resource_id]
    if len(kept) == len(rows):
        return False
    await _save_resources(kept)
    return True


async def suggest_tags(resource_id: str, suggestion: dict[str, Any]) -> bool:
    rows = await resources()
    for r in rows:
        if r.get("id") == resource_id:
            r["suggestion"] = suggestion
            await _save_resources(rows)
            return True
    return False
