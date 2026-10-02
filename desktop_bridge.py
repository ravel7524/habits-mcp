"""Persistent private phone bridge. Staging never applies an academic plan."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, time as civil_time, timezone
from pathlib import Path
from typing import Any

from .validator import (
    MAX_BYTES, NOTES_LIMIT, TITLE_LIMIT, PlanValidationError, _array, _civil_instant,
    _date, _deadline, _integer, _object, _read, _string, _uuid, _zone, validate_plan,
)

PAIRING_SECONDS = 600
CLOCK_SKEW_SECONDS = 120
MAX_PENDING = 200
MAX_RESPONSE_BYTES = MAX_BYTES
MAX_BATCH = 20
_HEX = re.compile(r"^[0-9a-fA-F]{64}$")
_CAPTURED_AT = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z$")


class BridgeError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def normalized_plan(value: Any) -> tuple[dict[str, Any], bytes, str]:
    plan = validate_plan(value)
    plan["proposalID"] = _uuid(plan["proposalID"], "proposalID")
    plan["semester"]["id"] = _uuid(plan["semester"]["id"], "semester.id")
    for collection, references in (("courses", ()), ("assessments", ("courseID",)), ("preparationTasks", ("assessmentID",))):
        for item in plan[collection]:
            for field in ("id", *references):
                item[field] = _uuid(item[field], field)
    encoded = canonical_json(plan)
    return plan, encoded, hashlib.sha256(encoded).hexdigest()


def private_bind(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise BridgeError("phone-bind must be a literal private or loopback IP address") from error
    private_v4 = (ipaddress.ip_network("10.0.0.0/8"), ipaddress.ip_network("172.16.0.0/12"), ipaddress.ip_network("192.168.0.0/16"))
    allowed = address.is_loopback or (address.version == 4 and any(address in network for network in private_v4)) or (address.version == 6 and address in ipaddress.ip_network("fc00::/7"))
    if not allowed or address.is_unspecified or address.is_multicast:
        raise BridgeError("phone-bind must be RFC1918, IPv6 ULA, or loopback; public/wildcard binds are refused")
    return str(address)


def utc_string(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if epoch is None else epoch, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise BridgeError("the bridge data directory must not be a symlink")
    if path.exists() and path.is_dir():
        allowed = {"bridge.sqlite3", "bridge.sqlite3-journal", "desktop-cert.pem", "desktop-key.pem", "pairing-invite.json", ".DS_Store"}
        if any(entry.name not in allowed for entry in path.iterdir()):
            raise BridgeError("use a dedicated bridge data directory; unrelated existing files were preserved")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise BridgeError("the bridge data path must be a directory")
    os.chmod(path, 0o700)


def private_file(path: Path, content: bytes, *, replace: bool = False) -> None:
    if path.is_symlink():
        raise BridgeError("bridge files must not be symlinks")
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if replace else os.O_EXCL)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def validate_context(value: Any) -> dict[str, Any]:
    context = _object(_read(value), "context", {"format", "version", "capturedAt", "semesters"})
    if context["format"] != "habits.academic-context":
        raise PlanValidationError("context.format: must be habits.academic-context")
    _integer(context["version"], "context.version", 1, 1)
    captured_at = context["capturedAt"]
    if type(captured_at) is not str or not _CAPTURED_AT.fullmatch(captured_at):
        raise PlanValidationError("context.capturedAt: must be an ISO UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise PlanValidationError("context.capturedAt: invalid UTC timestamp") from error
    identities: set[str] = set()

    def identity(value: Any, path: str) -> str:
        identifier = _uuid(value, path)
        if identifier in identities:
            raise PlanValidationError(path + ": duplicate academic context UUID")
        identities.add(identifier)
        return identifier

    for index, semester in enumerate(_array(context["semesters"], "context.semesters", 1)):
        prefix = f"context.semesters[{index}]"
        _object(semester, prefix, {"id", "title", "weekCount", "courses", "existingAssessments"}, {"startDate", "timeZoneIdentifier"})
        identity(semester["id"], prefix + ".id")
        _string(semester["title"], prefix + ".title", TITLE_LIMIT, trimmed=True)
        _integer(semester["weekCount"], prefix + ".weekCount", 1, 52)
        zone = _zone(semester["timeZoneIdentifier"]) if "timeZoneIdentifier" in semester else None
        if "startDate" in semester:
            start = _date(semester["startDate"], prefix + ".startDate")
            if zone is not None:
                _civil_instant(start, civil_time(12), zone, prefix + ".startDate")
        courses: set[str] = set()
        for course in _array(semester["courses"], prefix + ".courses", 10):
            _object(course, prefix + ".course", {"id", "name"})
            courses.add(identity(course["id"], prefix + ".course.id"))
            _string(course["name"], prefix + ".course.name", TITLE_LIMIT, trimmed=True)
        for assessment in _array(semester["existingAssessments"], prefix + ".existingAssessments", 200):
            path = prefix + ".existingAssessment"
            _object(assessment, path, {"id", "courseID", "title", "kind", "dueDate", "notes"}, {"dueTime", "state"})
            identity(assessment["id"], path + ".id")
            if _uuid(assessment["courseID"], path + ".courseID") not in courses:
                raise PlanValidationError(path + ".courseID: must reference a shared course")
            _string(assessment["title"], path + ".title", TITLE_LIMIT, trimmed=True)
            _string(assessment["notes"], path + ".notes", NOTES_LIMIT, note_controls=True)
            if type(assessment["kind"]) is not str or assessment["kind"] not in {"exam", "presentation", "essay", "deadline"}:
                raise PlanValidationError(path + ".kind: unsupported assessment kind")
            if "state" in assessment and (type(assessment["state"]) is not str or assessment["state"] not in {"pending", "completed", "dismissed"}):
                raise PlanValidationError(path + ".state: must be pending, completed, or dismissed when supplied")
            if zone is not None:
                _deadline(assessment, path, zone, required=True)
            else:
                _date(assessment["dueDate"], path + ".dueDate")
                if "dueTime" in assessment:
                    from .validator import _time
                    _time(assessment["dueTime"], path + ".dueTime")
    return json.loads(canonical_json(context))


class DesktopBridge:
    """Owns private desktop records; the phone owns application writes and receipts."""

    def __init__(self, data_dir: Path, bind: str = "127.0.0.1", port: int = 8766):
        self.bind = private_bind(bind)
        self.port = port
        if not 0 <= port <= 65535:
            raise BridgeError("phone port must be between 1 and 65535")
        self.data_dir = Path(data_dir).expanduser().absolute()
        private_directory(self.data_dir)
        self.db_path = self.data_dir / "bridge.sqlite3"
        if self.db_path.is_symlink():
            raise BridgeError("the bridge database must not be a symlink")
        if not self.db_path.exists():
            private_file(self.db_path, b"")
        os.chmod(self.db_path, 0o600)
        with self.connection() as database:
            version = database.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise BridgeError("unsupported bridge storage version; files were preserved")
            database.executescript("""
                CREATE TABLE IF NOT EXISTS devices(device_id TEXT PRIMARY KEY, name TEXT NOT NULL, secret BLOB NOT NULL, paired_at REAL NOT NULL, last_seen REAL, revoked_at REAL);
                CREATE TABLE IF NOT EXISTS invites(code_hash TEXT PRIMARY KEY, expires_at REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS nonces(device_id TEXT NOT NULL REFERENCES devices(device_id), nonce TEXT NOT NULL, timestamp REAL NOT NULL, PRIMARY KEY(device_id,nonce));
                CREATE TABLE IF NOT EXISTS proposals(proposal_id TEXT PRIMARY KEY, digest TEXT NOT NULL, body BLOB NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS deliveries(proposal_id TEXT NOT NULL REFERENCES proposals(proposal_id), device_id TEXT NOT NULL REFERENCES devices(device_id), state TEXT NOT NULL, detail TEXT, updated_at REAL NOT NULL, PRIMARY KEY(proposal_id,device_id));
                CREATE TABLE IF NOT EXISTS contexts(device_id TEXT PRIMARY KEY REFERENCES devices(device_id), body BLOB NOT NULL, captured_at TEXT NOT NULL, received_at REAL NOT NULL);
                PRAGMA user_version=1;
            """)
            if "revoked_at" not in {row[1] for row in database.execute("PRAGMA table_info(devices)")}:
                database.execute("ALTER TABLE devices ADD COLUMN revoked_at REAL")
        from .phone_tls import ensure_certificate
        self.certificate_path, self.key_path, self.certificate_sha256 = ensure_certificate(self.data_dir, self.bind)

    @property
    def base_url(self) -> str:
        host = f"[{self.bind}]" if ":" in self.bind else self.bind
        return f"https://{host}:{self.port}"

    @contextmanager
    def connection(self):
        database = sqlite3.connect(self.db_path, timeout=10)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA foreign_keys=ON")
        database.execute("PRAGMA journal_mode=DELETE")
        try:
            yield database
            database.commit()
        except BaseException:
            database.rollback()
            raise
        finally:
            database.close()

    def desktop_status(self) -> dict[str, Any]:
        with self.connection() as database:
            devices = [{"deviceID": row["device_id"], "deviceName": row["name"], "lastSeen": utc_string(row["last_seen"]) if row["last_seen"] else None} for row in database.execute("SELECT device_id,name,last_seen FROM devices WHERE revoked_at IS NULL ORDER BY device_id")]
            pending = database.execute("SELECT COUNT(*) FROM deliveries JOIN devices USING(device_id) WHERE state='pending' AND revoked_at IS NULL").fetchone()[0]
        return {"status": "desktop_ready", "baseURL": self.base_url, "certificateSHA256": self.certificate_sha256, "pairedDevices": devices, "pendingProposalCount": pending, "message": "Local desktop bridge. Plans remain pending until a paired phone reports user-confirmed application; phone connectivity is not guaranteed."}

    def create_pairing_invite(self) -> dict[str, Any]:
        code = base64.urlsafe_b64encode(secrets.token_bytes(24)).decode("ascii")
        expires = time.time() + PAIRING_SECONDS
        invite = {"format": "habits.desktop-pairing", "version": 1, "baseURL": self.base_url, "certificateSHA256": self.certificate_sha256, "pairingCode": code}
        with self.connection() as database:
            database.execute("DELETE FROM invites")
            database.execute("INSERT INTO invites(code_hash,expires_at) VALUES(?,?)", (hashlib.sha256(code.encode()).hexdigest(), expires))
        invite_path = self.data_dir / "pairing-invite.json"
        private_file(invite_path, canonical_json(invite), replace=True)
        return {"status": "pairing_invite_created", "invite": invite, "invitePath": str(invite_path), "expiresAt": utc_string(expires), "message": "Single-use invite valid for 10 minutes. Transfer the complete invite directly to your phone; its certificate fingerprint is the trust anchor. No phone is paired yet."}

    def pair(self, value: Any) -> dict[str, str]:
        _object(value, "pair", {"pairingCode", "deviceID", "deviceName"})
        code = value["pairingCode"]
        if type(code) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{32}", code):
            raise BridgeError("invalid or expired pairing invite", 401)
        device = _uuid(value["deviceID"], "pair.deviceID")
        name = _string(value["deviceName"], "pair.deviceName", 200, trimmed=True)
        now = time.time()
        secret = secrets.token_bytes(32)
        with self.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            consumed = database.execute("UPDATE invites SET used=1 WHERE code_hash=? AND used=0 AND expires_at>?", (hashlib.sha256(code.encode()).hexdigest(), now)).rowcount
            if consumed != 1:
                raise BridgeError("invalid or expired pairing invite", 401)
            database.execute("INSERT INTO devices(device_id,name,secret,paired_at,last_seen) VALUES(?,?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET name=excluded.name,secret=excluded.secret,paired_at=excluded.paired_at,last_seen=excluded.last_seen,revoked_at=NULL", (device, name, secret, now, now))
        return {"deviceID": device, "deviceSecret": secret.hex()}

    def authenticate(self, method: str, path: str, body: bytes, headers: Any) -> str:
        try:
            device = _uuid(headers.get("X-Habits-Device"), "auth.device")
            timestamp = headers.get("X-Habits-Timestamp", "")
            nonce_text = headers.get("X-Habits-Nonce", "")
            nonce = _uuid(nonce_text, "auth.nonce")
            signature = headers.get("X-Habits-Signature", "")
            if not re.fullmatch(r"[0-9]{1,12}", timestamp) or not _HEX.fullmatch(signature):
                raise ValueError("invalid headers")
            now = time.time()
            if abs(now - int(timestamp)) > CLOCK_SKEW_SECONDS:
                raise ValueError("timestamp outside clock window")
        except (PlanValidationError, TypeError, ValueError) as error:
            raise BridgeError("request authentication failed", 401) from error
        signed = "\n".join((method.upper(), path, timestamp, nonce_text, hashlib.sha256(body).hexdigest())).encode("utf-8")
        with self.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute("SELECT secret FROM devices WHERE device_id=? AND revoked_at IS NULL", (device,)).fetchone()
            if row is None or not hmac.compare_digest(hmac.new(row["secret"], signed, hashlib.sha256).hexdigest(), signature.lower()):
                raise BridgeError("request authentication failed", 401)
            # Retain all accepted nonces for this credential across restarts.
            # Expired timestamps cannot be replayed, but retention also prevents
            # a reused nonce with a newly signed timestamp.
            try:
                database.execute("INSERT INTO nonces VALUES(?,?,?)", (device, nonce, int(timestamp)))
            except sqlite3.IntegrityError as error:
                raise BridgeError("request nonce was already used", 409) from error
            database.execute("UPDATE devices SET last_seen=? WHERE device_id=?", (now, device))
        return device

    def target_device(self, requested: str | None = None) -> str:
        with self.connection() as database:
            devices = [row[0] for row in database.execute("SELECT device_id FROM devices WHERE revoked_at IS NULL ORDER BY device_id")]
        if requested is not None:
            target = _uuid(requested, "targetDeviceID")
            if target not in devices:
                raise BridgeError("targetDeviceID must identify a paired device", 404)
            return target
        if len(devices) != 1:
            raise BridgeError("Pair one phone first; when multiple phones are paired, supply targetDeviceID explicitly")
        return devices[0]

    def get_shared_academic_context(self, target_device_id: str | None = None) -> dict[str, Any]:
        target = self.target_device(target_device_id)
        with self.connection() as database:
            row = database.execute("SELECT body,captured_at,received_at FROM contexts WHERE device_id=?", (target,)).fetchone()
        context = json.loads(row["body"]) if row else None
        return {"status": "shared_context" if row else "no_shared_context", "targetDeviceID": target, "context": context, "receivedAt": utc_string(row["received_at"]) if row else None, "calendarCoverage": "unknown", "message": "This is the phone's explicitly shared academic snapshot, not live app state. Missing semester start dates/time zones require user clarification before constructing a proposal. Missing assessment state means unknown, never pending; avoid preparation for recorded completed/dismissed assessments unless the user explicitly requests it. Calendar coverage is unknown, including when the snapshot is empty or all recorded assessments are terminal. No task or habit history is shared, and missing records do not establish free time."}

    def stage_academic_plan(self, proposal: Any, target_device_id: str | None = None) -> dict[str, Any]:
        target = self.target_device(target_device_id)
        plan, body, digest = normalized_plan(proposal)
        if len(canonical_json({"proposals": [{"proposal": plan, "proposalDigest": digest, "status": "pending"}]})) > MAX_RESPONSE_BYTES:
            raise BridgeError("proposal plus phone delivery envelope exceeds 2 MiB; reduce proposal text before staging", 413)
        identifier = plan["proposalID"]
        with self.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            old = database.execute("SELECT digest FROM proposals WHERE proposal_id=?", (identifier,)).fetchone()
            if old and old["digest"] != digest:
                raise BridgeError("proposalID already identifies different immutable content; use a fresh proposal UUID", 409)
            delivery = database.execute("SELECT state FROM deliveries WHERE proposal_id=? AND device_id=?", (identifier, target)).fetchone()
            if delivery is None:
                pending = database.execute("SELECT COUNT(*) FROM deliveries WHERE device_id=? AND state='pending'", (target,)).fetchone()[0]
                if pending >= MAX_PENDING:
                    raise BridgeError("this phone has 200 pending proposals; review those before staging another", 409)
                database.execute("INSERT OR IGNORE INTO proposals VALUES(?,?,?,?)", (identifier, digest, body, time.time()))
                database.execute("INSERT INTO deliveries VALUES(?,?,?,NULL,?)", (identifier, target, "pending", time.time()))
        return self.get_plan_receipt(identifier, target)

    def pending_proposals(self, device: str) -> dict[str, Any]:
        # Bounded response batches; acknowledged rows disappear from this queue.
        result: dict[str, Any] = {"proposals": []}
        with self.connection() as database:
            rows = database.execute("SELECT p.body,p.digest FROM proposals p JOIN deliveries d ON p.proposal_id=d.proposal_id WHERE d.device_id=? AND d.state='pending' ORDER BY p.created_at,p.proposal_id", (device,))
            for row in rows:
                candidate = {"proposal": json.loads(row["body"]), "proposalDigest": row["digest"], "status": "pending"}
                result["proposals"].append(candidate)
                if len(canonical_json(result)) > MAX_RESPONSE_BYTES:
                    result["proposals"].pop()
                    break
                if len(result["proposals"]) == MAX_BATCH:
                    break
        return result

    def acknowledge(self, device: str, value: Any) -> dict[str, Any]:
        _object(value, "receipt", {"proposalID", "proposalDigest", "state"}, {"detail"})
        identifier = _uuid(value["proposalID"], "receipt.proposalID")
        digest = value["proposalDigest"]
        if type(digest) is not str or not _HEX.fullmatch(digest):
            raise BridgeError("receipt.proposalDigest must be a SHA-256 digest")
        state = value["state"]
        if type(state) is not str or state not in {"applied", "undone", "rejected"}:
            raise BridgeError("receipt.state must be applied, undone, or rejected")
        detail = _string(value["detail"], "receipt.detail", 1000, note_controls=True) if "detail" in value else None
        with self.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute("SELECT p.digest,d.state FROM proposals p JOIN deliveries d ON p.proposal_id=d.proposal_id WHERE p.proposal_id=? AND d.device_id=?", (identifier, device)).fetchone()
            if row is None:
                raise BridgeError("proposal not staged for this device", 404)
            if not hmac.compare_digest(row["digest"], digest.lower()):
                raise BridgeError("receipt digest does not match the immutable proposal", 409)
            old = row["state"]
            if old == "undone" and state == "applied":
                # A delayed applied ACK must never undo a newer rollback receipt.
                state = old
            if old != state:
                if not ((old == "pending" and state in {"applied", "rejected", "undone"}) or (old == "applied" and state == "undone")):
                    raise BridgeError("receipt state transition is not valid", 409)
                database.execute("UPDATE deliveries SET state=?,detail=?,updated_at=? WHERE proposal_id=? AND device_id=?", (state, detail, time.time(), identifier, device))
        return self.get_plan_receipt(identifier, device)

    def get_plan_receipt(self, proposal_id: str, target_device_id: str | None = None) -> dict[str, Any]:
        target = self.target_device(target_device_id)
        identifier = _uuid(proposal_id, "proposalID")
        with self.connection() as database:
            row = database.execute("SELECT p.digest,d.state,d.detail,d.updated_at FROM proposals p JOIN deliveries d ON p.proposal_id=d.proposal_id WHERE p.proposal_id=? AND d.device_id=?", (identifier, target)).fetchone()
        state = row["state"] if row else "not_found"
        messages = {"pending": "Staged on the desktop; waiting for the phone to fetch, review and confirm. No app application has been reported.", "applied": "The paired phone reports that the user applied this proposal.", "undone": "The paired phone reports that the user undid this proposal.", "rejected": "The paired phone reports that the user rejected this proposal.", "not_found": "No proposal is staged for this phone under that UUID."}
        return {"status": state, "proposalID": identifier, "proposalDigest": row["digest"] if row else None, "targetDeviceID": target, "detail": row["detail"] if row else None, "updatedAt": utc_string(row["updated_at"]) if row else None, "message": messages[state]}

    def share_context(self, device: str, value: Any) -> dict[str, Any]:
        context = validate_context(value)
        with self.connection() as database:
            database.execute("INSERT INTO contexts VALUES(?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET body=excluded.body,captured_at=excluded.captured_at,received_at=excluded.received_at", (device, canonical_json(context), context["capturedAt"], time.time()))
        return {"status": "context_shared", "deviceID": device, "capturedAt": context["capturedAt"]}

    def disconnect(self, device: str, value: Any) -> dict[str, Any]:
        _object(value, "disconnect", set())
        with self.connection() as database:
            database.execute("UPDATE devices SET revoked_at=? WHERE device_id=?", (time.time(), device))
        return {"status": "disconnected", "deviceID": device}

    def revoke_device(self, device_id: str) -> dict[str, Any]:
        device = _uuid(device_id, "deviceID")
        with self.connection() as database:
            row = database.execute("SELECT revoked_at FROM devices WHERE device_id=?", (device,)).fetchone()
            if row is None:
                raise BridgeError("deviceID does not identify a known device", 404)
            if row["revoked_at"] is None:
                database.execute("UPDATE devices SET revoked_at=? WHERE device_id=?", (time.time(), device))
        return {"status": "device_revoked", "deviceID": device, "message": "This phone credential is revoked, including while the phone is offline. Stored academic snapshots and proposal/receipt history are preserved. Pair again with a new invite to restore access."}
