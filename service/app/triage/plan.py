"""What to do about a classified reply. Pure: no I/O, so every rule is testable.

The worker carries the plan out. Actions:
  suppress / mark_dnc / cancel_drafts  unsubscribe everywhere
  pause_ooo                            push the remaining emails past the return date
  snooze                               stop now, start a fresh sequence in N days
  add_referral                         enroll the person they pointed to
  book_offered / book_proposed         book a slot (falls back to offering new ones)
  offer_slots                          find free slots to put in the reply

Below the confidence threshold nothing irreversible happens: no booking, no
suppression, no DNC, no snooze. A draft may still be written, but it never
auto-sends and the reply is marked for a person.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Plan:
    category: str
    actions: list[str] = field(default_factory=list)
    draft_kind: Optional[str] = None
    needs_human: bool = False
    note: str = ""


def decide(
    category: str,
    confidence: float,
    extracted: dict[str, Any],
    *,
    min_confidence: float,
    event_type: str = "reply",
    offered_count: int = 0,
    has_meeting: bool = False,
) -> Plan:
    confident = confidence >= min_confidence

    # A reply already flagged by the opt-out keywords, or an auto-reply flagged
    # by its headers, is certain whatever the model's confidence.
    if event_type == "unsubscribe":
        category, confident = "unsubscribe", True
    elif event_type == "auto_reply" and category != "unsubscribe":
        category, confident = "out_of_office", True

    if category == "unsubscribe":
        if not confident:
            return Plan(category, needs_human=True, note="Possible unsubscribe; please confirm.")
        return Plan(category, ["suppress", "cancel_drafts", "mark_dnc"],
                    note="Suppressed, drafts cancelled, lead marked DNC.")

    if category == "out_of_office":
        return Plan(category, ["pause_ooo"], note="Remaining emails moved past their return.")

    if category == "other":
        return Plan(category, needs_human=True, note="Could not tell what this reply means.")

    if category == "not_now":
        if not confident:
            return Plan(category, draft_kind="not_now_ack", needs_human=True,
                        note="Looks like 'not now'; confirm before snoozing.")
        return Plan(category, ["snooze"], "not_now_ack", note="Snoozed; a fresh sequence starts later.")

    if category == "wrong_person":
        referral = extracted.get("referral") or {}
        if referral.get("email") and confident:
            return Plan(category, ["add_referral"], "referral_ack", note="Referred contact enrolled.")
        return Plan(category, draft_kind="referral_ask", needs_human=not confident,
                    note="Asking who the right person is." if confident else "Confirm before replying.")

    if category == "objection":
        return Plan(category, draft_kind="objection_reply", needs_human=not confident)

    # interested
    if has_meeting and "proposed_time" not in extracted:
        return Plan(category, note="A meeting is already booked; nothing to send.")
    if has_meeting:
        return Plan(category, needs_human=True, note="Wants to move an existing meeting; please reschedule.")
    if not confident:
        return Plan(category, ["offer_slots"], "meeting_offer", needs_human=True,
                    note="Looks interested; check the draft before it goes.")
    chosen = extracted.get("chosen_slot")
    if isinstance(chosen, int) and 1 <= chosen <= offered_count:
        return Plan(category, ["book_offered"], "booking_confirmation")
    if extracted.get("proposed_time"):
        return Plan(category, ["book_proposed"], "booking_confirmation")
    return Plan(category, ["offer_slots"], "meeting_offer")
