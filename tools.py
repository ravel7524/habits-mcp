"""Transport-independent proposal tools; neither tool applies changes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .validator import MAX_BYTES, validate_plan
from .board_validator import validate_semester_board

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


def get_semester_board_format() -> dict[str, Any]:
    return {
        "status": "proposal_only",
        "schema": json.loads((_ROOT / "semester-board-proposal.schema.json").read_text(encoding="utf-8")),
        "example": json.loads((_ROOT / "examples/semester-board-proposal.json").read_text(encoding="utf-8")),
        "maxBytes": MAX_BYTES,
        "semanticRules": [
            "This separate schema-3 board merge only adds or explicitly updates academic records; omitted records and optional fields survive. No deletion, reminders or sync command.",
            "For an existing semester, echo expectedBoardDigest from this explicitly targeted phone's shared board context. The phone rechecks freshness and shows every field change before confirmation.",
            "UUIDs are scoped by entity type/course. Keep external source IDs, including a legacy exam whose UUID equals its course UUID. Week keys and active weeks are 1 through 52, including retained hidden weeks.",
            "Omitted activeWeeks means no source schedule recorded. Do not fabricate all teaching weeks or an empty schedule. Raw iOS files still require genuine activeWeeks arrays.",
            "New boards require valid, unambiguous source-zone instants. With expectedBoardDigest, structural acceptance preserves recorded DST gap/overlap clocks; the phone validates new or changed deadlines and timezone changes against the merged source zone. Proposal-only/pending never means applied.",
            "Unknown fields, legacy exam fields, reminderDays, snoozedUntil and recurrence edits are outside this assistant DTO. Raw file import compatibility is separate.",
            "Never fill omitted private notes, state or timestamps with defaults. Explicit null clears only nullable fields; empty notes are an intentional clear and must be reviewed.",
            "Completion is recorded pending/completed/dismissed state. completedAt is finite Foundation seconds since 2001-01-01 UTC; omitted historical completion time stays unknown, never import time.",
            "Share only one explicitly selected board. Week progress and course/week/assessment notes need separate disclosed consent. No planner, preparation history, habits, profile, Calendar or credentials.",
            "Stage only to an explicitly named Android device advertising this proposal format/version. Staged is pending, never applied.",
        ],
        "message": PROPOSAL_MESSAGE,
    }


def propose_semester_board(proposal: bytes | str | dict[str, Any]) -> dict[str, Any]:
    return {"status": "proposal_only", "proposal": validate_semester_board(proposal), "message": PROPOSAL_MESSAGE}
