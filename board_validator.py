"""Strict, presence-preserving Semester Board proposals and shared snapshots.

These values are proposals, not app commands. The phone owns merge/confirmation.
"""
from __future__ import annotations

import copy
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from .validator import (
    MAX_BYTES, MAX_COURSES, MAX_ASSESSMENTS, NOTES_LIMIT, SOURCE_LIMIT, TITLE_LIMIT,
    _array, _civil_instant, _date, _fail, _integer, _object, _read, _string, _time, _uuid, _zone,
)

BOARD_FORMAT = "habits.semester-board-proposal"
BOARD_CONTEXT_FORMAT = "habits.semester-board-context"
CAPABILITIES_FORMAT = "habits.desktop-capabilities"
PLAN_FORMAT = "habits.academic-plan"
MAX_STATUSES = 20
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_CAPTURED_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$")


def _bool(value: Any, path: str) -> None:
    if type(value) is not bool:
        _fail(path, "must be a boolean")


def _digest(value: Any, path: str) -> None:
    if type(value) is not str or not _DIGEST.fullmatch(value):
        _fail(path, "must be a lowercase SHA-256 digest")


def _timestamp(value: Any, path: str) -> None:
    if type(value) not in (int, float):
        _fail(path, "must be finite Foundation seconds since 2001-01-01 UTC")
    try:
        if not math.isfinite(value):
            _fail(path, "must be finite Foundation seconds since 2001-01-01 UTC")
        datetime(2001, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=value)
    except (OverflowError, ValueError):
        _fail(path, "timestamp is outside the supported calendar range")


def validate_board(value: Any, *, allow_existing_references: bool = False) -> dict[str, Any]:
    """Validate known schema-3 board fields without inserting missing defaults."""
    board = _object(value, "semester", {"schemaVersion", "id", "title", "weekCount", "statuses", "courses"},
                    {"startDate", "endDate", "showExamWeeks", "timeZoneIdentifier"})
    _integer(board["schemaVersion"], "semester.schemaVersion", 3, 3)
    _uuid(board["id"], "semester.id")
    _string(board["title"], "semester.title", TITLE_LIMIT, trimmed=True)
    weeks = _integer(board["weekCount"], "semester.weekCount", 1, 52)
    if "showExamWeeks" in board:
        _bool(board["showExamWeeks"], "semester.showExamWeeks")
    if ("startDate" in board) != ("endDate" in board):
        _fail("semester", "startDate and endDate must be paired or both omitted")
    if "startDate" in board:
        if (board["startDate"] is None) != (board["endDate"] is None):
            _fail("semester", "date range can only be cleared as a pair")
        if board["startDate"] is not None:
            first = _date(board["startDate"], "semester.startDate")
            last = _date(board["endDate"], "semester.endDate")
            days = (last - first).days
            if not 0 <= days < 364 or (days + 7) // 7 != weeks:
                _fail("semester", "date range must agree with weekCount")
    zone = _zone(board["timeZoneIdentifier"]) if board.get("timeZoneIdentifier") is not None else None
    status_ids: set[str] = set()
    for index, status in enumerate(_array(board["statuses"], "semester.statuses", MAX_STATUSES)):
        path = f"semester.statuses[{index}]"
        _object(status, path, {"id", "name", "colorHex"})
        identity = _uuid(status["id"], path + ".id")
        if identity in status_ids:
            _fail(path + ".id", "duplicates a status identity")
        status_ids.add(identity)
        _string(status["name"], path + ".name", TITLE_LIMIT, trimmed=True)
        if type(status["colorHex"]) is not str or not re.fullmatch(r"#[0-9a-fA-F]{6}", status["colorHex"]):
            _fail(path + ".colorHex", "must be #RRGGBB")
    course_ids: set[str] = set()
    assessment_total = 0
    for index, course in enumerate(_array(board["courses"], "semester.courses", MAX_COURSES)):
        path = f"semester.courses[{index}]"
        _object(course, path, {"id", "name", "entries"}, {"activeWeeks", "moduleCode", "sourceURL", "scheduleNote", "assessments"})
        identity = _uuid(course["id"], path + ".id")
        if identity in course_ids:
            _fail(path + ".id", "duplicates a course identity")
        course_ids.add(identity)
        _string(course["name"], path + ".name", TITLE_LIMIT, trimmed=True)
        if "activeWeeks" in course:
            active = _array(course["activeWeeks"], path + ".activeWeeks", 52)
            for week in active:
                _integer(week, path + ".activeWeeks", 1, 52)
            if len(set(active)) != len(active):
                _fail(path + ".activeWeeks", "contains duplicate weeks")
        for field, limit, notes in (("moduleCode", TITLE_LIMIT, False), ("sourceURL", 2048, False), ("scheduleNote", NOTES_LIMIT, True)):
            if field in course and course[field] is not None:
                _string(course[field], path + "." + field, limit, note_controls=notes)
                if field == "sourceURL" and course[field]:
                    try:
                        parsed = urlsplit(course[field])
                    except ValueError:
                        _fail(path + ".sourceURL", "must be a valid HTTP(S) URL without credentials")
                    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                        _fail(path + ".sourceURL", "must be an HTTP(S) URL without credentials")
        entries = course["entries"]
        if type(entries) is not dict or len(entries) > 52:
            _fail(path + ".entries", "must be a map of at most 52 course weeks")
        for key, entry in entries.items():
            entry_path = path + ".entries"
            if type(key) is not str or not re.fullmatch(r"[1-9]|[1-4][0-9]|5[0-2]", key):
                _fail(entry_path, "keys must be canonical week numbers 1 through 52")
            _object(entry, entry_path, set(), {"statusID", "notes"})
            if "statusID" in entry and entry["statusID"] is not None:
                status = _uuid(entry["statusID"], entry_path + ".statusID")
                if status not in status_ids and not allow_existing_references:
                    _fail(entry_path + ".statusID", "must reference a supplied status for a new semester")
            if "notes" in entry:
                _string(entry["notes"], entry_path + ".notes", NOTES_LIMIT, note_controls=True)
        assessment_ids: set[str] = set()
        for assessment_index, item in enumerate(_array(course.get("assessments", []), path + ".assessments", MAX_ASSESSMENTS)):
            assessment_total += 1
            if assessment_total > MAX_ASSESSMENTS:
                _fail("semester.courses", "proposal contains more than 200 assessments")
            item_path = path + f".assessments[{assessment_index}]"
            _object(item, item_path, {"id", "title", "kind", "dueDate"}, {"dueTime", "notes", "state", "completedAt"})
            identifier = _uuid(item["id"], item_path + ".id")
            if identifier in assessment_ids:
                _fail(item_path + ".id", "duplicates an assessment identity within this course")
            assessment_ids.add(identifier)
            _string(item["title"], item_path + ".title", TITLE_LIMIT, trimmed=True)
            if type(item["kind"]) is not str or item["kind"] not in {"exam", "presentation", "essay", "deadline"}:
                _fail(item_path + ".kind", "unsupported assessment kind")
            due = _date(item["dueDate"], item_path + ".dueDate")
            if item.get("dueTime") is not None:
                clock = _time(item["dueTime"], item_path + ".dueTime")
                if zone is not None and not allow_existing_references:
                    _civil_instant(due, clock, zone, item_path + ".dueTime")
                elif zone is None and not allow_existing_references:
                    _fail(item_path + ".dueTime", "a source timezone is required for a timed new assessment")
            if "notes" in item:
                _string(item["notes"], item_path + ".notes", NOTES_LIMIT, note_controls=True)
            if "state" in item and (type(item["state"]) is not str or item["state"] not in {"pending", "completed", "dismissed"}):
                _fail(item_path + ".state", "must be pending, completed or dismissed")
            if item.get("completedAt") is not None:
                _timestamp(item["completedAt"], item_path + ".completedAt")
                if item.get("state") in {"pending", "dismissed"} or ("state" not in item and not allow_existing_references):
                    _fail(item_path + ".completedAt", "must describe recorded completed state")
    return copy.deepcopy(board)


def validate_semester_board(value: bytes | str | dict[str, Any]) -> dict[str, Any]:
    proposal = _object(_read(value), "proposal", {"format", "version", "proposalID", "source", "semester"}, {"expectedBoardDigest"})
    if proposal["format"] != BOARD_FORMAT:
        _fail("proposal.format", "must be habits.semester-board-proposal")
    _integer(proposal["version"], "proposal.version", 1, 1)
    _uuid(proposal["proposalID"], "proposal.proposalID")
    _string(proposal["source"], "proposal.source", SOURCE_LIMIT, trimmed=True)
    if "expectedBoardDigest" in proposal:
        _digest(proposal["expectedBoardDigest"], "proposal.expectedBoardDigest")
    validate_board(proposal["semester"], allow_existing_references="expectedBoardDigest" in proposal)
    return copy.deepcopy(proposal)


def validate_board_context(value: Any) -> dict[str, Any]:
    context = _object(_read(value), "context", {"format", "version", "capturedAt", "boardDigest", "notesIncluded", "weekProgressIncluded", "semester"})
    if context["format"] != BOARD_CONTEXT_FORMAT:
        _fail("context.format", "must be habits.semester-board-context")
    _integer(context["version"], "context.version", 1, 1)
    _digest(context["boardDigest"], "context.boardDigest")
    stamp = context["capturedAt"]
    if type(stamp) is not str or not _CAPTURED_AT.fullmatch(stamp):
        _fail("context.capturedAt", "must be an ISO UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        _fail("context.capturedAt", "invalid UTC timestamp")
    _bool(context["notesIncluded"], "context.notesIncluded")
    _bool(context["weekProgressIncluded"], "context.weekProgressIncluded")
    validate_board(context["semester"], allow_existing_references=True)
    for course in context["semester"]["courses"]:
        if "sourceURL" in course:
            _fail("context", "source URLs are excluded from shared board context")
        if not context["notesIncluded"] and "scheduleNote" in course:
            _fail("context", "course notes require explicit notesIncluded consent")
        if not context["weekProgressIncluded"] and course["entries"]:
            _fail("context", "week entries require explicit weekProgressIncluded consent")
        if not context["notesIncluded"] and (any("notes" in item for item in course.get("assessments", [])) or any("notes" in item for item in course["entries"].values())):
            _fail("context", "assessment and week notes require explicit notesIncluded consent")
    return copy.deepcopy(context)


def validate_capabilities(value: Any) -> dict[str, Any]:
    caps = _object(_read(value), "capabilities", {"format", "version", "platform", "proposalFormats"})
    if caps["format"] != CAPABILITIES_FORMAT:
        _fail("capabilities.format", "must be habits.desktop-capabilities")
    _integer(caps["version"], "capabilities.version", 1, 1)
    if type(caps["platform"]) is not str or caps["platform"] not in {"android", "ios"}:
        _fail("capabilities.platform", "must identify android or ios")
    seen: set[tuple[str, int]] = set()
    for item in _array(caps["proposalFormats"], "capabilities.proposalFormats", 8):
        _object(item, "capability", {"format", "version"})
        if type(item["format"]) is not str or item["format"] not in {PLAN_FORMAT, BOARD_FORMAT}:
            _fail("capability.format", "unsupported proposal format")
        _integer(item["version"], "capability.version", 1, 1)
        key = (item["format"], item["version"])
        if key in seen:
            _fail("capabilities", "duplicate proposal capability")
        seen.add(key)
    if (BOARD_FORMAT, 1) in seen and caps["platform"] != "android":
        _fail("capabilities", "Semester Board capability currently requires Android")
    return copy.deepcopy(caps)


def normalize_board_ids(proposal: dict[str, Any]) -> dict[str, Any]:
    """Only UUID spellings normalize. Optional presence/nulls and literal notes survive."""
    result = validate_semester_board(proposal)
    result["proposalID"] = _uuid(result["proposalID"], "proposalID")
    board = result["semester"]
    board["id"] = _uuid(board["id"], "semester.id")
    for status in board["statuses"]:
        status["id"] = _uuid(status["id"], "status.id")
    for course in board["courses"]:
        course["id"] = _uuid(course["id"], "course.id")
        for entry in course["entries"].values():
            if entry.get("statusID") is not None:
                entry["statusID"] = _uuid(entry["statusID"], "entry.statusID")
        for item in course.get("assessments", []):
            item["id"] = _uuid(item["id"], "assessment.id")
    return result
