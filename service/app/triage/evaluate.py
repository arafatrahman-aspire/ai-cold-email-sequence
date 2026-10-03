"""Accuracy check for the reply classifier (OUT-05 "done when": 95% on 50).

Runs every labelled reply in testset.jsonl through the real classifier and
reports overall and per-category accuracy, plus the extraction details the
actions depend on (referral email, return date, chosen slot).

    python -m app.triage.evaluate          # from service/, prints a report

Add real replies to testset.jsonl as they arrive: a score on your own mail
is the one to trust.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.triage.classify import CATEGORIES, classify_reply

TESTSET = Path(__file__).with_name("testset.jsonl")
TARGET = 0.95
# Relative dates in the test replies ("Thursday 8 October") assume this day.
TODAY = date(2026, 10, 5)


def load_testset(path: Path = TESTSET) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _extraction_checks(row: dict, extracted: dict) -> list[tuple[str, bool]]:
    checks = []
    if "referral_email" in row:
        got = ((extracted.get("referral") or {}).get("email") or "").lower()
        checks.append(("referral email", got == row["referral_email"].lower()))
    if "return_date" in row:
        checks.append(("return date", extracted.get("return_date") == row["return_date"]))
    if "chosen_slot" in row:
        checks.append(("chosen slot", extracted.get("chosen_slot") == row["chosen_slot"]))
    if row.get("proposed"):
        checks.append(("proposed time", bool(extracted.get("proposed_time"))))
    return checks


async def evaluate(rows: Optional[list[dict]] = None, concurrency: int = 4) -> dict[str, Any]:
    rows = rows if rows is not None else load_testset()
    sem = asyncio.Semaphore(concurrency)
    started = time.monotonic()
    model: Optional[str] = None

    async def one(row: dict) -> dict:
        nonlocal model
        async with sem:
            try:
                r = await classify_reply(
                    row.get("subject", ""), row["body"],
                    lead_timezone="UTC", today=TODAY, offered=row.get("offered"),
                )
                model = model or r.model
                return {"row": row, "category": r.category, "confidence": r.confidence,
                        "extracted": r.extracted, "error": None}
            except Exception as exc:
                return {"row": row, "category": None, "confidence": 0.0, "extracted": {}, "error": str(exc)}

    results = await asyncio.gather(*(one(r) for r in rows))

    by_category = {c: {"total": 0, "correct": 0} for c in CATEGORIES}
    mistakes, extraction_fails = [], []
    correct = checked = extraction_ok = 0
    for res in results:
        row = res["row"]
        expected = row["category"]
        ok = res["category"] == expected
        by_category.setdefault(expected, {"total": 0, "correct": 0})
        by_category[expected]["total"] += 1
        if ok:
            correct += 1
            by_category[expected]["correct"] += 1
        else:
            mistakes.append({
                "id": row["id"], "expected": expected, "got": res["category"],
                "confidence": res["confidence"], "body": row["body"], "error": res["error"],
            })
        for name, passed in _extraction_checks(row, res["extracted"]):
            checked += 1
            extraction_ok += passed
            if not passed:
                extraction_fails.append({"id": row["id"], "check": name, "extracted": res["extracted"]})

    total = len(rows)
    accuracy = correct / total if total else 0.0
    return {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(time.monotonic() - started, 1),
        "model": model,
        "total": total,
        "correct": correct,
        "accuracy": round(accuracy, 4),
        "target": TARGET,
        "passed": accuracy >= TARGET,
        "by_category": by_category,
        "mistakes": mistakes,
        "extraction": {"checked": checked, "correct": extraction_ok, "failures": extraction_fails},
    }


def _print(report: dict) -> None:
    print(f"Accuracy {report['correct']}/{report['total']} = {report['accuracy']:.0%} "
          f"(target {report['target']:.0%}) -> {'PASS' if report['passed'] else 'FAIL'}  [{report['model']}]")
    for c, v in report["by_category"].items():
        print(f"  {c:<14} {v['correct']}/{v['total']}")
    ex = report["extraction"]
    print(f"Extraction {ex['correct']}/{ex['checked']}")
    for m in report["mistakes"]:
        print(f"  MISS {m['id']}: expected {m['expected']}, got {m['got']} ({m['confidence']:.2f}) {m['error'] or ''}")
    for f in ex["failures"]:
        print(f"  EXTRACT {f['id']}: {f['check']} -> {f['extracted']}")


if __name__ == "__main__":
    _print(asyncio.run(evaluate()))
