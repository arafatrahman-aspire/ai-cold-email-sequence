"""Writing one nurture email.

  1. The model writes {subject, preheader, body, resource_id, cta_type} from
     the step brief, the lead (as cleaned data), the allowed resources and
     what this lead has already received and clicked.
  2. Deterministic checks (app.nurture.checks).
  3. A second call, the judge, scores grounding, persona fit, tone and
     non-repetition.
  4. Any failure: one more attempt, told why. A second failure (or the AI
     being down) uses the pre-approved fallback email for this persona and
     step, so the email still goes out on time.
Everything is logged on the message: model, prompt and brief versions,
check results, judge scores, attempts and tokens.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from app.llm.factory import get_gateway
from app.nurture import checks
from app.nurture.options import PERSONAS
from app.nurture.safety import DATA_RULE, clean, data_block

log = logging.getLogger(__name__)

PROMPT_VERSION = "nurture-writer-v1"
JUDGE_VERSION = "nurture-judge-v1"
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)

SYSTEM = """\
You write one email in a B2B nurture sequence for {company}, a security
awareness training company. The reader showed some interest but is not ready
to buy; each email must be genuinely useful to them.

Rules:
- Write only the body paragraphs. No greeting, no sign-off and no signature:
  those are added automatically.
- Plain text, short paragraphs separated by a blank line, at most {max_words}
  words. Warm, specific, no hype, no exclamation marks.
- Never write a URL, web address, email address or HTML. Links are
  placeholders that become link text: {{{{CTA_DEMO}}}} reads "book a short
  demo", {{{{CTA_PRICING}}}} reads "see pricing", {{{{RESOURCE_LINK}}}} reads as
  the resource's title. Write sentences where that text fits, for example
  "If useful, you can {{{{CTA_DEMO}}}} or {{{{CTA_PRICING}}}}."
- Include {{{{CTA_DEMO}}}} and {{{{CTA_PRICING}}}} once each.
- If <resources> lists any resource, choose the most relevant one: put its id
  in resource_id and {{{{RESOURCE_LINK}}}} once in the body. If it lists none,
  set resource_id to null and do not use {{{{RESOURCE_LINK}}}}.
- State only what the brief and the resource descriptions support. Never
  invent statistics, customers, results, prices, discounts or guarantees,
  and never claim the product makes anyone compliant or certified.
- Do not repeat a subject, resource or angle from the earlier emails.
- {pitch_rule}
- {data_rule}

Return ONLY this JSON:
{{"subject": "...", "preheader": "...", "body": "...",
 "resource_id": "<an id from <resources>, or null>",
 "cta_type": "demo" | "pricing" | "resource" | "reply"}}
"""

JUDGE = """\
You review one email from a B2B nurture sequence before it is sent. Score
each from 0 to 1:
- grounding: every factual claim is supported by the brief or the resource
  descriptions given (general, uncontroversial statements are fine;
  invented numbers, customers or results are not)
- persona_fit: relevant to the reader's role
- tone: helpful and not pushy, right for a {temperature} lead
- non_repetition: does not repeat earlier subjects, resources or angles
overall is your overall judgement, never above the lowest score by more
than 0.2. {data_rule}

Return ONLY: {{"grounding": 0.0, "persona_fit": 0.0, "tone": 0.0,
"non_repetition": 0.0, "overall": 0.0, "issues": ["..."]}}
"""

TEMPERATURE_NOTE = {
    "cold": "A Cold lead: educational first, no product pitch before email 4.",
    "warm": "A Warm lead: shows proof earlier (case studies, ROI) and makes stronger calls to action.",
}


@dataclass
class Result:
    draft: checks.Draft
    source: str  # ai | fallback
    attempts: int
    log: list[dict[str, Any]] = field(default_factory=list)
    judge: Optional[dict[str, Any]] = None
    judge_score: Optional[float] = None
    tokens_in: int = 0
    tokens_out: int = 0
    model: Optional[str] = None


def allowed_resources(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """Approved resources for this persona and the brief's stages, minus the
    ones this lead already received."""
    persona = ctx["enrollment"]["persona"]
    stages = set((ctx.get("brief") or {}).get("allowed_stages") or [])
    used = {h.get("resource_id") for h in ctx.get("history") or [] if h.get("resource_id")}
    return [
        r for r in ctx.get("resources") or []
        if stages & set(r.get("stages") or [])
        and (not r.get("personas") or persona in r["personas"])
        and r["id"] not in used
    ]


def _pitch_rule(temperature: str, step: int) -> str:
    if temperature == "cold" and step <= 3:
        return ("This is an educational email: do not pitch or describe our product. Keep the two "
                "calls to action to one low-key closing sentence.")
    return "You may describe how our platform helps, briefly and without hype."


def build_prompt(ctx: dict[str, Any], resources: list[dict[str, Any]], feedback: list[str]) -> str:
    e, brief, step = ctx["enrollment"], ctx.get("brief") or {}, ctx["message"]["step"]
    who = ctx.get("lead") or e
    lead = {
        "job_title": clean(who.get("job_title"), 120),
        "company": clean(who.get("company"), 120),
        "persona": PERSONAS.get(e["persona"], e["persona"]),
    }
    history = [
        {"step": h["step"], "subject": clean(h.get("subject"), 120),
         "resource_id": h.get("resource_id"), "opening": clean(h.get("body"), 240)}
        for h in ctx.get("history") or []
    ]
    clicks = [{"step": c.get("step"), "link": c.get("link") or c.get("link_type"), "resource_id": c.get("resource_id")}
              for c in ctx.get("clicks") or []]
    res = [{"id": r["id"], "title": clean(r.get("title"), 150), "kind": r.get("kind"),
            "description": clean(r.get("description"), 400)} for r in resources]
    parts = [
        f"Email {step} of 6. {TEMPERATURE_NOTE[e['temperature']]}",
        f"Goal: {brief.get('goal', 'Be useful and keep the conversation open.')}",
        f"Angle: {brief.get('angle', '')}",
        f"Call-to-action emphasis: {brief.get('cta_emphasis', 'soft')}",
        data_block("lead_data", lead),
        data_block("history", {"earlier_emails": history}),
        data_block("clicks", {"clicked": clicks}),
        data_block("resources", {"allowed": res}),
    ]
    if not res:
        parts.append("There is no allowed resource for this email: set resource_id to null.")
    if feedback:
        parts.append("Your previous draft was rejected. Fix all of these:\n- " + "\n- ".join(feedback))
    return "\n\n".join(parts)


def _parse(text: str) -> checks.Draft:
    data = json.loads(_FENCE.sub("", text).strip())
    if not isinstance(data, dict):
        raise ValueError("not a JSON object")
    rid = data.get("resource_id")
    rid = str(rid).strip() if rid not in (None, "", "null", "none") else None
    return checks.Draft(
        subject=str(data.get("subject") or "").strip(),
        preheader=str(data.get("preheader") or "").strip(),
        body=str(data.get("body") or "").strip(),
        resource_id=rid,
        cta_type=str(data.get("cta_type") or "").strip().lower(),
    )


async def _judge(ctx: dict[str, Any], draft: checks.Draft, resources: list[dict[str, Any]],
                 result: Result) -> tuple[dict[str, Any], float]:
    e, brief = ctx["enrollment"], ctx.get("brief") or {}
    review = "\n\n".join([
        f"Reader: {PERSONAS.get(e['persona'], e['persona'])}, {e['temperature']} lead, email "
        f"{ctx['message']['step']} of 6.",
        f"Brief goal: {brief.get('goal', '')}\nBrief angle: {brief.get('angle', '')}",
        data_block("resources", {"given": [{"id": r["id"], "title": clean(r.get("title"), 150),
                                            "description": clean(r.get("description"), 400)}
                                           for r in resources]}),
        data_block("history", {"earlier_subjects": [clean(h.get("subject"), 120)
                                                    for h in ctx.get("history") or []]}),
        "The email to review:\n" + data_block("email", {
            "subject": draft.subject, "preheader": draft.preheader, "body": draft.body,
            "resource_id": draft.resource_id}),
    ])
    completion = await get_gateway().complete_json(
        JUDGE.format(temperature=e["temperature"], data_rule=DATA_RULE), review, temperature=0.0)
    result.tokens_in += completion.input_tokens
    result.tokens_out += completion.output_tokens
    data = json.loads(_FENCE.sub("", completion.text).strip())
    scores = {k: max(0.0, min(1.0, float(data.get(k, 0)))) for k in
              ("grounding", "persona_fit", "tone", "non_repetition", "overall")}
    scores["issues"] = [clean(i, 200) for i in (data.get("issues") or [])][:5]
    scores["version"] = JUDGE_VERSION
    return scores, min(scores["overall"], scores["grounding"])


def fallback_draft(ctx: dict[str, Any]) -> checks.Draft:
    f = ctx.get("fallback") or {}
    body = f.get("body") or (
        "I wanted to share a quick thought on keeping people alert to phishing without adding "
        "work for your team. Short, regular training tends to stick far better than a long annual "
        "course.\n\nIf it would help, you can {{CTA_DEMO}} or {{CTA_PRICING}} at any time."
    )
    return checks.Draft(
        subject=f.get("subject") or "A quick thought on security awareness",
        preheader=f.get("preheader") or "",
        body=body,
        resource_id=None,
        cta_type="demo",
    )


async def write(ctx: dict[str, Any], cfg: dict[str, Any]) -> Result:
    e, brief = ctx["enrollment"], ctx.get("brief") or {}
    step = ctx["message"]["step"]
    resources = allowed_resources(ctx)
    allowed_ids = {r["id"] for r in resources}
    max_words = int(brief.get("max_words") or 150)
    banned = list(cfg["banned_phrases"])
    result = Result(draft=fallback_draft(ctx), source="fallback", attempts=0)

    if int(ctx.get("rejected") or 0) >= 2:
        result.log.append({"attempt": 0, "outcome": "rejected twice by a person: fallback"})
        return result

    system = SYSTEM.format(company=cfg["branding"].get("company_name") or "our company",
                           max_words=max_words, pitch_rule=_pitch_rule(e["temperature"], step),
                           data_rule=DATA_RULE)
    feedback: list[str] = []
    for attempt in range(1, int(cfg["max_ai_attempts"]) + 1):
        result.attempts = attempt
        entry: dict[str, Any] = {"attempt": attempt}
        try:
            completion = await get_gateway().complete_json(
                system, build_prompt(ctx, resources, feedback), temperature=0.6)
            result.tokens_in += completion.input_tokens
            result.tokens_out += completion.output_tokens
            result.model = f"{completion.provider}:{completion.model}"
            draft = _parse(completion.text)
        except Exception as exc:
            entry.update(outcome="ai_error", error=str(exc)[:300])
            result.log.append(entry)
            feedback = ["your answer was not valid JSON in the required shape"]
            continue

        problems = checks.check(draft, allowed_resources=allowed_ids, max_words=max_words,
                                banned_phrases=banned)
        if problems:
            entry.update(outcome="checks_failed", problems=problems)
            result.log.append(entry)
            feedback = problems
            continue

        try:
            scores, score = await _judge(ctx, draft, resources, result)
        except Exception as exc:
            entry.update(outcome="judge_error", error=str(exc)[:300])
            result.log.append(entry)
            feedback = ["the reviewer could not assess the draft; keep it simple and grounded"]
            continue
        entry.update(judge=scores)
        result.judge, result.judge_score = scores, score
        if score < float(cfg["judge_min_score"]):
            entry["outcome"] = "judge_failed"
            result.log.append(entry)
            feedback = [f"reviewer: {i}" for i in scores["issues"]] or [
                f"reviewer scored it {score:.2f}; stick strictly to the brief and resources"]
            continue

        entry["outcome"] = "passed"
        result.log.append(entry)
        result.draft, result.source = draft, "ai"
        return result

    log.warning("nurture: AI draft for enrollment %s step %s failed twice; using the fallback",
                e.get("id"), step)
    result.log.append({"attempt": result.attempts, "outcome": "fallback"})
    return result
