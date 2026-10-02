"""Transport-independent proposal tools; neither tool applies changes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .validator import MAX_BYTES, validate_plan

_ROOT = Path(__file__).resolve().parent
PROPOSAL_MESSAGE = (
    "Validated proposal only. No phone, app database, calendar, reminders, or sync data was changed. "
    "Review the proposal in the app and explicitly confirm its import before any app changes."
)


def get_plan_format() -> dict[str, Any]:
    """Return the portable format, example, and semantic constraints."""
    return {
        "status": "proposal_only",
        "schema": json.loads((_ROOT / "academic-plan.schema.json").read_text(encoding="utf-8")),
        "example": json.loads((_ROOT / "examples" / "two-course-exams.json").read_text(encoding="utf-8")),
        "maxBytes": MAX_BYTES,
        "semanticRules": [
            "Supply only dates/times supported by the user's source; ask for missing required assessment dates.",
            "Leave unknown preparation dates and exam times absent. Never infer availability from missing data.",
            "All proposal/entity IDs must be globally unique hyphenated UUIDs; references must resolve inside this proposal.",
            "Duplicate assessments with the same course, exact title, kind, date and optional time are rejected.",
            "Use an available IANA semester timezone; nonexistent or ambiguous local times are rejected.",
            "Date-only deadlines compare as local end of day, without inserting a dueTime into the JSON.",
            "Preparation deadlines must be no later than their linked assessment's deadline.",
            "Source, titles and course names must be nonempty and already trimmed; notes retain literal text.",
            "Control/format characters are rejected; notes permit only tab, newline and carriage-return controls.",
            "When reading shared context, omitted assessment state is unknown, never pending. Do not plan preparation for recorded completed/dismissed assessments unless the user explicitly requests it.",
            "Shared academic context provides no calendar coverage; empty or terminal-only snapshots do not establish free time.",
        ],
        "message": PROPOSAL_MESSAGE,
    }


def propose_academic_plan(proposal: bytes | str | dict[str, Any]) -> dict[str, Any]:
    """Validate and return a proposal for explicit app review, without persistence."""
    return {"status": "proposal_only", "proposal": validate_plan(proposal), "message": PROPOSAL_MESSAGE}
