"""Per-persona drafting prompts.

Each persona gets its own angle, as specified in OUT-01:
  ciso -> risk & compliance
  it   -> phishing simulation
  hr   -> training completion
"""

from __future__ import annotations

PERSONA_ANGLES: dict[str, dict[str, str]] = {
    "ciso": {
        "label": "Security / risk leadership (CISO, Head of Security, Risk, Compliance)",
        "angle": "risk and compliance",
        "guidance": (
            "Lead with organisational risk exposure and audit/compliance pressure "
            "(SOC 2, ISO 27001, NIS2, cyber-insurance questionnaires). This reader "
            "owns board-level reporting on security posture and cares about "
            "defensible evidence, not tooling features. Be concrete about "
            "measurable risk reduction."
        ),
    },
    "it": {
        "label": "IT / infrastructure (IT Manager, Sysadmin, Head of Infrastructure)",
        "angle": "phishing simulation",
        "guidance": (
            "Lead with phishing simulation: how often campaigns run, how much "
            "manual setup they replace, and how results surface without adding "
            "ticket load. This reader is practical, time-poor and allergic to "
            "marketing language. Emphasise low administrative overhead."
        ),
    },
    "hr": {
        "label": "HR / People (HR Director, People Ops, L&D, Talent)",
        "angle": "training completion",
        "guidance": (
            "Lead with training completion rates and the chase-up burden of "
            "mandatory security awareness training. This reader cares about "
            "employee experience, completion reporting for audits, and not "
            "nagging staff. Emphasise engagement over enforcement."
        ),
    },
}

SYSTEM_PROMPT = """\
You write cold outreach email sequences for a B2B security awareness training company.

Hard rules:
- Write as a human sales rep, not a marketer. No hype, no buzzwords, no exclamation marks.
- Never invent facts about the prospect's company: no fabricated metrics, breaches,
  headcounts, customers, funding, news events or named colleagues. You only know
  what is given to you below.
- Each email is plain text, 60-130 words, and asks for exactly one thing.
- Subject lines are 3-7 words, lowercase or sentence case, and never contain the
  company name of the sender or words like "quick", "free", "guarantee", "urgent".
- Do not include a signature, greeting sign-off block, unsubscribe line or footer.
  Those are appended by the sending system. End on your last sentence.
- Vary sentence structure between emails. Do not reuse the same opening pattern.

Return ONLY a JSON object of this exact shape:
{"emails": [{"step_number": 1, "subject": "...", "body": "..."}, ...]}
"""

SEQUENCE_SHAPE = """\
The sequence has {step_count} emails with these distinct jobs:
1. Opener — name the problem in their world and ask one low-friction question.
2. Follow-up — a different angle on the same problem; add one piece of substance
   (a pattern you see in similar orgs), then re-ask.
3. Value nudge — offer something concrete and small (a short walkthrough, a
   benchmark, a sample report). No pressure.
4. Break-up — brief, gracious, easy to say no to. Make replying "not now" simple.
"""


def build_user_prompt(
    persona: str,
    lead: dict,
    step_count: int,
) -> str:
    spec = PERSONA_ANGLES[persona]
    name = (lead.get("first_name") or "").strip() or "there"
    company = (lead.get("company") or "").strip() or "their company"
    title = (lead.get("job_title") or "").strip() or spec["label"]
    referred_by = (lead.get("referred_by") or "").strip()
    referral = (
        f"- Referred by: {referred_by}, who said this person is the right contact. "
        "Mention that briefly in email 1.\n" if referred_by else ""
    )

    return f"""\
Prospect:
- First name: {name}
- Job title: {title}
- Company: {company}
{referral}
Persona: {spec["label"]}
Primary angle: {spec["angle"]}

How to approach this reader:
{spec["guidance"]}

{SEQUENCE_SHAPE.format(step_count=step_count)}

Address the reader by first name in email 1 only. Use "{company}" naturally at
most once per email. Everything you know about this prospect is listed above —
do not assert anything beyond it.
"""


CLASSIFIER_SYSTEM = """\
You classify a job title into exactly one of three personas for a security
awareness training vendor.

- "ciso": security, risk, compliance, audit, privacy, governance, or executive
  ownership of security posture.
- "it": IT operations, infrastructure, systems, networks, helpdesk, endpoint,
  or general technology administration.
- "hr": people, human resources, talent, learning and development, training,
  culture, or employee operations.

If the title is a general executive role (CEO, COO, Founder, Managing Director)
with no clearer signal, answer "ciso".

Return ONLY: {"persona": "ciso"|"it"|"hr", "confidence": 0.0-1.0}
"""
