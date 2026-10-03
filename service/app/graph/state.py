"""Shared state passed between LangGraph nodes."""

from __future__ import annotations

from typing import Literal, Optional, TypedDict

Persona = Literal["ciso", "it", "hr"]


class LeadInput(TypedDict, total=False):
    id: str
    email: str
    first_name: Optional[str]
    last_name: Optional[str]
    company: Optional[str]
    job_title: Optional[str]
    timezone: str


class DraftedEmail(TypedDict):
    step_number: int
    subject: str
    body: str


class SequenceState(TypedDict, total=False):
    lead: LeadInput
    step_count: int

    persona: Optional[Persona]
    routing_mode: Literal["keyword", "llm"]

    emails: list[DraftedEmail]
    provider: Optional[str]
    model: Optional[str]

    validation_errors: list[str]
    draft_attempts: int
    status: Literal["ok", "manual_review", "failed"]
    error: Optional[str]
