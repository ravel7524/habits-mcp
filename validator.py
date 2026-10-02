"""Strict, dependency-free validation of habits.academic-plan version 1.

Validation never persists a proposal or touches application/CloudKit storage.
"""

from __future__ import annotations

import copy
import json
import re
import unicodedata
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones

MAX_BYTES = 2 * 1024 * 1024
MAX_COURSES = 10  # AcademicCodec's existing semester bound.
MAX_ASSESSMENTS = 200
MAX_PREPARATION_TASKS = 200
TITLE_LIMIT = 200
SOURCE_LIMIT = 300
NOTES_LIMIT = 4000

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_TIME = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


class PlanValidationError(ValueError):
    """A validation failure whose message identifies a field, not its private contents."""


def _fail(path: str, reason: str) -> None:
    raise PlanValidationError(f"{path}: {reason}")


def _object(value: Any, path: str, required: set[str], optional: set[str] | None = None) -> dict[str, Any]:
    if type(value) is not dict:
        _fail(path, "must be an object")
    if any(type(key) is not str for key in value):
        _fail(path, "object keys must be strings")
    if set(value) - required - (optional or set()):
        _fail(path, "contains unsupported fields")
    missing = required - set(value)
    if missing:
        _fail(path, "missing required fields: " + ", ".join(sorted(missing)))
    return value


def _string(value: Any, path: str, limit: int, *, trimmed: bool = False, note_controls: bool = False) -> str:
    if type(value) is not str:
        _fail(path, "must be a string")
    if len(value) > limit:
        _fail(path, f"must contain at most {limit} Unicode code points")
    if trimmed and (not value or value != value.strip()):
        _fail(path, "must be nonempty without surrounding whitespace")
    if any(unicodedata.category(character) in {"Cc", "Cf"} and not (note_controls and character in "\t\n\r") for character in value):
        _fail(path, "contains unsupported control characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        _fail(path, "must contain valid Unicode")
    return value


def _integer(value: Any, path: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        _fail(path, f"must be an integer from {low} through {high}")
    return value


def _uuid(value: Any, path: str) -> str:
    if type(value) is not str or not _UUID.fullmatch(value):
        _fail(path, "must be a hyphenated UUID")
    return str(UUID(value))


def _date(value: Any, path: str) -> date:
    if type(value) is not str or not _DATE.fullmatch(value):
        _fail(path, "must be a Gregorian date in YYYY-MM-DD format")
    try:
        return date.fromisoformat(value)
    except ValueError:
        _fail(path, "must be a valid Gregorian date")


def _time(value: Any, path: str) -> time:
    if type(value) is not str or not _TIME.fullmatch(value):
        _fail(path, "must be a 24-hour time in HH:mm format")
    return time.fromisoformat(value)


def _zone(value: Any) -> ZoneInfo:
    _string(value, "semester.timeZoneIdentifier", 200, trimmed=True)
    # ZoneInfo can also load the host-specific 'localtime' file; it is not a portable zone.
    if (value != "UTC" and "/" not in value) or value not in available_timezones():
        _fail("semester.timeZoneIdentifier", "must identify an available IANA time zone")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        _fail("semester.timeZoneIdentifier", "must identify an available IANA time zone")


def _civil_instant(day: date, clock: time, zone: ZoneInfo, path: str) -> datetime:
    """Resolve exactly one local instant, refusing DST gaps and overlaps."""
    naive = datetime.combine(day, clock)
    instants: set[datetime] = set()
    try:
        for fold in (0, 1):
            instant = naive.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
            if instant.astimezone(zone).replace(tzinfo=None) == naive:
                instants.add(instant)
    except (OverflowError, ValueError):
        _fail(path, "local date/time is outside the supported calendar range")
    if not instants:
        _fail(path, "local date/time does not exist in the semester time zone")
    if len(instants) != 1:
        _fail(path, "local date/time is ambiguous in the semester time zone")
    return instants.pop()


def _deadline(item: dict[str, Any], path: str, zone: ZoneInfo, *, required: bool, storage_midnight: bool = False) -> datetime | None:
    if "dueDate" not in item:
        if required or "dueTime" in item:
            _fail(path + ".dueDate", "is required when a due time is supplied")
        return None
    day = _date(item["dueDate"], path + ".dueDate")
    if storage_midnight and "dueTime" not in item:
        _civil_instant(day, time(0), zone, path + ".dueDate")
    # Comparison semantics only; no inferred time is inserted into the proposal.
    clock = _time(item["dueTime"], path + ".dueTime") if "dueTime" in item else time(23, 59, 59)
    return _civil_instant(day, clock, zone, path + ".dueDate/dueTime")


def _array(value: Any, path: str, limit: int) -> list[Any]:
    if type(value) is not list or len(value) > limit:
        _fail(path, f"must be an array with at most {limit} items")
    return value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _fail("proposal", "JSON contains duplicate object keys")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    _fail("proposal", "JSON must not contain non-finite numbers")


def _read(value: bytes | str | dict[str, Any]) -> dict[str, Any]:
    try:
        if isinstance(value, bytes):
            if len(value) > MAX_BYTES:
                _fail("proposal", "exceeds the 2 MiB UTF-8 payload limit")
            raw = value.decode("utf-8")
        elif type(value) is str:
            raw = value
        elif type(value) is dict:
            raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        else:
            _fail("proposal", "must be a JSON object or UTF-8 JSON document")
        if len(raw.encode("utf-8")) > MAX_BYTES:
            _fail("proposal", "exceeds the 2 MiB UTF-8 payload limit")
        parsed = json.loads(raw, object_pairs_hook=_unique_json_object, parse_constant=_reject_constant)
    except PlanValidationError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError):
        _fail("proposal", "must be a valid UTF-8 JSON document")
    # Validate dictionary inputs themselves so JSON serialization cannot coerce
    # Python-only values (e.g. tuples or integer keys) into admissible JSON values.
    return value if type(value) is dict else parsed


def validate_plan(value: bytes | str | dict[str, Any]) -> dict[str, Any]:
    """Return an independent validated proposal; never infer missing source dates."""
    plan = _object(_read(value), "proposal", {
        "format", "version", "proposalID", "source", "semester", "courses", "assessments", "preparationTasks"
    })
    if plan["format"] != "habits.academic-plan":
        _fail("format", "must be habits.academic-plan")
    _integer(plan["version"], "version", 1, 1)
    _string(plan["source"], "source", SOURCE_LIMIT, trimmed=True)

    identities: set[str] = set()

    def identity(value: Any, path: str) -> str:
        canonical = _uuid(value, path)
        if canonical in identities:
            _fail(path, "duplicates another proposal/entity UUID")
        identities.add(canonical)
        return canonical

    identity(plan["proposalID"], "proposalID")
    semester = _object(plan["semester"], "semester", {"id", "title", "startDate", "weekCount", "timeZoneIdentifier"})
    identity(semester["id"], "semester.id")
    _string(semester["title"], "semester.title", TITLE_LIMIT, trimmed=True)
    start = _date(semester["startDate"], "semester.startDate")
    weeks = _integer(semester["weekCount"], "semester.weekCount", 1, 52)
    zone = _zone(semester["timeZoneIdentifier"])
    _civil_instant(start, time(12), zone, "semester.startDate")
    try:
        start + timedelta(days=weeks * 7 - 1)
    except OverflowError:
        _fail("semester.startDate", "semester range exceeds the supported calendar")

    course_ids: set[str] = set()
    for index, item in enumerate(_array(plan["courses"], "courses", MAX_COURSES)):
        path = f"courses[{index}]"
        item = _object(item, path, {"id", "name"})
        course_ids.add(identity(item["id"], path + ".id"))
        _string(item["name"], path + ".name", TITLE_LIMIT, trimmed=True)

    assessment_deadlines: dict[str, datetime] = {}
    assessment_keys: set[tuple[str, str, str, str, str | None]] = set()
    for index, item in enumerate(_array(plan["assessments"], "assessments", MAX_ASSESSMENTS)):
        path = f"assessments[{index}]"
        item = _object(item, path, {"id", "courseID", "title", "kind", "dueDate", "notes"}, {"dueTime"})
        assessment_id = identity(item["id"], path + ".id")
        course_id = _uuid(item["courseID"], path + ".courseID")
        if course_id not in course_ids:
            _fail(path + ".courseID", "must reference a course in this proposal")
        _string(item["title"], path + ".title", TITLE_LIMIT, trimmed=True)
        if type(item["kind"]) is not str or item["kind"] not in {"exam", "presentation", "essay", "deadline"}:
            _fail(path + ".kind", "must be exam, presentation, essay, or deadline")
        _string(item["notes"], path + ".notes", NOTES_LIMIT, note_controls=True)
        deadline = _deadline(item, path, zone, required=True)
        assert deadline is not None
        # Match the importer: title equality is case sensitive and respects
        # Unicode canonical equivalence, as Swift String equality does.
        duplicate_key = (course_id, unicodedata.normalize("NFC", item["title"]), item["kind"], item["dueDate"], item.get("dueTime"))
        if duplicate_key in assessment_keys:
            _fail(path, "duplicates another assessment's course/title/kind/date/time")
        assessment_keys.add(duplicate_key)
        assessment_deadlines[assessment_id] = deadline

    for index, item in enumerate(_array(plan["preparationTasks"], "preparationTasks", MAX_PREPARATION_TASKS)):
        path = f"preparationTasks[{index}]"
        item = _object(item, path, {"id", "assessmentID", "title", "estimatedWorkMinutes", "notes"}, {"dueDate", "dueTime"})
        identity(item["id"], path + ".id")
        assessment_id = _uuid(item["assessmentID"], path + ".assessmentID")
        if assessment_id not in assessment_deadlines:
            _fail(path + ".assessmentID", "must reference an assessment in this proposal")
        _string(item["title"], path + ".title", TITLE_LIMIT, trimmed=True)
        _integer(item["estimatedWorkMinutes"], path + ".estimatedWorkMinutes", 0, 1440)
        _string(item["notes"], path + ".notes", NOTES_LIMIT, note_controls=True)
        deadline = _deadline(item, path, zone, required=False, storage_midnight=True)
        if deadline is not None and deadline > assessment_deadlines[assessment_id]:
            _fail(path + ".dueDate/dueTime", "must be no later than the linked assessment")
    return copy.deepcopy(plan)
