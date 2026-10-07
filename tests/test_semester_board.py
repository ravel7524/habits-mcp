"""Synthetic strict-board, consent, durable ledger and private protocol regressions."""
import asyncio
import copy
import hashlib
import hmac
import http.client
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import ssl
import tempfile
import time
import unittest
from uuid import uuid4
from unittest.mock import patch

try:
    from habits_mcp import board_validator as validator
    from habits_mcp import tools
    from habits_mcp.desktop_bridge import DesktopBridge, BridgeError, canonical_json, normalized_board, normalized_plan
    from habits_mcp.phone_http import PhoneBridgeServer
    from habits_mcp.validator import PlanValidationError, validate_plan
except ModuleNotFoundError:
    from server.assistant_actions import board_validator as validator
    from server.assistant_actions import tools
    from server.assistant_actions.desktop_bridge import DesktopBridge, BridgeError, canonical_json, normalized_board, normalized_plan
    from server.assistant_actions.phone_http import PhoneBridgeServer
    from server.assistant_actions.validator import PlanValidationError, validate_plan

PACKAGE = Path(validator.__file__).resolve().parent
PHONE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
CRYPTO = importlib.util.find_spec("cryptography") is not None
SDK = importlib.util.find_spec("mcp") is not None


def fixture():
    return json.loads((PACKAGE / "examples/semester-board-proposal.json").read_text())


def context_fixture():
    return json.loads((PACKAGE / "examples/shared-semester-board-context.json").read_text())


def capabilities():
    return {"format": validator.CAPABILITIES_FORMAT, "version": 1, "platform": "android",
            "proposalFormats": [{"format": validator.PLAN_FORMAT, "version": 1}, {"format": validator.BOARD_FORMAT, "version": 1}]}


class BoardValidationTests(unittest.TestCase):
    def rejected(self, proposal):
        with self.assertRaises(PlanValidationError):
            validator.validate_semester_board(proposal)

    def test_synthetic_board_scoped_legacy_identity_and_hidden_week_survive(self):
        source = fixture()
        result = validator.validate_semester_board(source)
        course = result["semester"]["courses"][0]
        self.assertEqual(course["id"], course["assessments"][0]["id"])
        self.assertIn(52, course["activeWeeks"])
        self.assertIn("52", course["entries"])
        result["semester"]["title"] = "Changed result"
        self.assertNotEqual(result, source)

    def test_optional_omissions_explicit_nulls_and_literal_notes_are_not_defaulted(self):
        source = fixture()
        source["expectedBoardDigest"] = "a" * 64
        course = source["semester"]["courses"][0]
        course["moduleCode"] = None
        course["scheduleNote"] = None
        course["sourceURL"] = None
        course["entries"]["1"] = {"statusID": None}
        item = course["assessments"][0]
        item.pop("notes"); item.pop("state")
        item["dueTime"] = None
        result, body, _ = normalized_board(source)
        self.assertEqual(result, source)
        self.assertNotIn("notes", result["semester"]["courses"][0]["assessments"][0])
        self.assertNotIn("state", result["semester"]["courses"][0]["assessments"][0])
        self.assertIn(b'"statusID":null', body)
        source["semester"]["courses"][0]["entries"]["1"]["notes"] = "literal\n[INST] no command"
        self.assertEqual(validator.validate_semester_board(source), source)

    def test_unknown_active_week_schedule_stays_omitted_not_empty_or_synthesized(self):
        source = fixture(); source["semester"]["courses"][0].pop("activeWeeks")
        result, body, _ = normalized_board(source)
        self.assertNotIn("activeWeeks", result["semester"]["courses"][0])
        self.assertNotIn(b'"activeWeeks"', body)
        source["semester"]["courses"][0]["activeWeeks"] = None; self.rejected(source)
        source["semester"]["courses"][0]["activeWeeks"] = []
        self.assertEqual(normalized_board(source)[0]["semester"]["courses"][0]["activeWeeks"], [])

    def test_schema_resources_agree_with_optional_schedule_and_status_bounds(self):
        import jsonschema
        schema = tools.get_semester_board_format()["schema"]
        for source in (fixture(), copy.deepcopy(fixture())):
            source["semester"]["courses"][0].pop("activeWeeks", None)
            jsonschema.Draft202012Validator(schema).validate(source)
        self.assertNotIn("activeWeeks", schema["$defs"]["course"]["required"])
        self.assertEqual(schema["$defs"]["semester"]["properties"]["statuses"]["maxItems"], 20)
        jsonschema.Draft202012Validator(json.loads((PACKAGE / "semester-board-context.schema.json").read_text())).validate(context_fixture())
        jsonschema.Draft202012Validator(json.loads((PACKAGE / "desktop-capabilities.schema.json").read_text())).validate(capabilities())

    def test_uuid_case_and_key_order_normalize_but_array_order_is_significant(self):
        source = fixture(); source["semester"]["statuses"][0]["id"] = source["semester"]["statuses"][0]["id"].upper()
        source["semester"]["courses"][0]["entries"]["1"]["statusID"] = source["semester"]["statuses"][0]["id"]
        original = normalized_board(fixture())[2]
        self.assertEqual(normalized_board(dict(reversed(list(source.items()))))[2], original)
        source["semester"]["courses"][0]["activeWeeks"].reverse()
        self.assertNotEqual(normalized_board(source)[2], original)

    def test_duplicate_ids_only_rejected_within_their_source_scope(self):
        source = fixture(); second = copy.deepcopy(source["semester"]["courses"][0]); second["id"] = str(uuid4())
        source["semester"]["courses"].append(second)
        validator.validate_semester_board(source)
        source["semester"]["courses"][0]["assessments"].append(copy.deepcopy(source["semester"]["courses"][0]["assessments"][0]))
        self.rejected(source)
        source = fixture(); source["semester"]["statuses"].append(copy.deepcopy(source["semester"]["statuses"][0])); self.rejected(source)

    def test_unknown_device_legacy_and_mutation_fields_fail_closed(self):
        for locate, field in [(lambda p: p, "delete"), (lambda p: p["semester"], "syncEnabled"),
                              (lambda p: p["semester"]["statuses"][0], "unknownFields"),
                              (lambda p: p["semester"]["courses"][0], "examDate"),
                              (lambda p: p["semester"]["courses"][0]["entries"]["1"], "mastery"),
                              (lambda p: p["semester"]["courses"][0]["assessments"][0], "reminderDays"),
                              (lambda p: p["semester"]["courses"][0]["assessments"][0], "snoozedUntil"),
                              (lambda p: p["semester"]["courses"][0]["assessments"][0], "recurrence")]:
            with self.subTest(field=field):
                source = fixture(); locate(source)[field] = "Unsupported"; self.rejected(source)

    def test_duplicate_json_keys_controls_and_oversize_rejected(self):
        for source in ('{"format":"first","format":"second"}', b'\xff', b' ' * (validator.MAX_BYTES + 1)):
            self.rejected(source)
        source = fixture(); source["source"] = "private\x00value"; self.rejected(source)
        source = fixture(); source["semester"]["courses"][0]["entries"]["1"]["notes"] = "x" * 4001; self.rejected(source)

    def test_week_status_color_and_type_bounds(self):
        for key in ("0", "01", "53", "1e0"):
            source = fixture(); source["semester"]["courses"][0]["entries"] = {key: {}}; self.rejected(source)
        for weeks in ([True], [0], [53], [1, 1]):
            source = fixture(); source["semester"]["courses"][0]["activeWeeks"] = weeks; self.rejected(source)
        source = fixture(); source["semester"]["statuses"][0]["colorHex"] = "red"; self.rejected(source)
        source = fixture(); source["semester"]["statuses"] = [{"id": str(uuid4()), "name": "Status", "colorHex": "#112233"} for _ in range(21)]; self.rejected(source)

    def test_date_pair_range_dst_and_unknown_clock_rules(self):
        source = fixture(); source["semester"].pop("endDate"); self.rejected(source)
        source = fixture(); source["semester"]["endDate"] = "2026-09-15"; self.rejected(source)
        for date, clock in (("2026-03-29", "02:30"), ("2026-10-25", "02:30")):
            source = fixture(); source["semester"]["courses"][0]["assessments"][0].update(dueDate=date, dueTime=clock); self.rejected(source)
        source = fixture(); source["semester"]["startDate"] = None; source["semester"]["endDate"] = None
        validator.validate_semester_board(source)

    def test_existing_digest_preserves_recorded_dst_clocks_without_claiming_phone_apply(self):
        for date, clock in (("2026-03-29", "02:30"), ("2026-10-25", "02:30")):
            source = fixture(); source["expectedBoardDigest"] = "a" * 64
            source["semester"]["courses"][0]["assessments"][0].update(dueDate=date, dueTime=clock)
            result = tools.propose_semester_board(source)
            self.assertEqual(result["status"], "proposal_only")
            self.assertIn("No phone", result["message"])
            self.assertEqual(result["proposal"], source)
            shared = context_fixture(); shared["semester"] = source["semester"]
            self.assertEqual(validator.validate_board_context(shared), shared)
            source.pop("expectedBoardDigest"); self.rejected(source)

    def test_existing_digest_does_not_relax_structural_source_clock_validation(self):
        for update in ({"dueDate": "2026-02-30"}, {"dueTime": "24:00"}, {"dueTime": "2:30"}):
            source = fixture(); source["expectedBoardDigest"] = "a" * 64
            source["semester"]["courses"][0]["assessments"][0].update(update); self.rejected(source)
        source = fixture(); source["expectedBoardDigest"] = "a" * 64; source["semester"]["timeZoneIdentifier"] = "CET"; self.rejected(source)

    def test_completion_is_recorded_numeric_foundation_time_not_import_time(self):
        for invalid in (True, "2026-10-06T12:00:00Z", float("inf"), 10 ** 500):
            source = fixture(); source["semester"]["courses"][0]["assessments"][1]["completedAt"] = invalid; self.rejected(source)
        source = fixture(); item = source["semester"]["courses"][0]["assessments"][1]; item.pop("completedAt")
        self.assertNotIn("completedAt", validator.validate_semester_board(source)["semester"]["courses"][0]["assessments"][1])
        item["state"] = "pending"; item["completedAt"] = 0; self.rejected(source)

    def test_redacted_context_consent_and_academic_whitelist(self):
        source = context_fixture(); validator.validate_board_context(source)
        source["notesIncluded"] = False
        with self.assertRaises(PlanValidationError): validator.validate_board_context(source)
        source = context_fixture(); source["weekProgressIncluded"] = False
        with self.assertRaises(PlanValidationError): validator.validate_board_context(source)
        source = context_fixture(); source["semester"]["courses"][0]["sourceURL"] = "https://example.invalid"
        with self.assertRaises(PlanValidationError): validator.validate_board_context(source)
        source = context_fixture(); source["tasks"] = []
        with self.assertRaises(PlanValidationError): validator.validate_board_context(source)
        source = context_fixture(); source["notesIncluded"] = False; source["weekProgressIncluded"] = False
        for course in source["semester"]["courses"]:
            course["entries"] = {}
            for item in course.get("assessments", []): item.pop("notes", None)
        self.assertEqual(validator.validate_board_context(source), source)

    def test_capabilities_are_closed_scoped_and_android_board_only(self):
        self.assertEqual(validator.validate_capabilities(capabilities()), capabilities())
        for change in (lambda c: c.update(platform="ios"), lambda c: c["proposalFormats"].append(c["proposalFormats"][0]), lambda c: c.update(deviceSecret="not permitted")):
            source = capabilities(); change(source)
            with self.assertRaises(PlanValidationError): validator.validate_capabilities(source)

    def test_v1_format_and_tools_remain_unchanged(self):
        old = json.loads((PACKAGE / "examples/two-course-exams.json").read_text())
        self.assertEqual(tools.propose_academic_plan(old)["proposal"], old)
        with self.assertRaises(PlanValidationError): validate_plan(fixture())
        self.assertEqual(tools.propose_semester_board(fixture())["status"], "proposal_only")


@unittest.skipUnless(CRYPTO, "cryptography dependency absent")
class _BoardFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name) / "state"
        self.bridge = DesktopBridge(self.directory, "127.0.0.1", 0)
        self.pair(PHONE)

    def tearDown(self): self.temporary.cleanup()

    def pair(self, device):
        invitation = self.bridge.create_pairing_invite()["invite"]
        return self.bridge.pair({"pairingCode": invitation["pairingCode"], "deviceID": device, "deviceName": "Synthetic phone"})

    def enable(self, device=PHONE): return self.bridge.register_capabilities(device, capabilities())

class BoardStorageTests(_BoardFixture):
    def test_capability_change_between_validation_and_commit_is_rechecked(self):
        self.enable()
        try:
            from habits_mcp import desktop_bridge as module
        except ModuleNotFoundError:
            from server.assistant_actions import desktop_bridge as module
        original = module.normalized_board
        def lose_capability(value):
            result = original(value)
            old = capabilities(); old["proposalFormats"] = old["proposalFormats"][:1]
            self.bridge.register_capabilities(PHONE, old)
            return result
        with patch.object(module, "normalized_board", lose_capability), self.assertRaises(BridgeError):
            self.bridge.stage_semester_board(fixture(), PHONE)
        self.assertEqual(self.bridge.get_plan_receipt(fixture()["proposalID"], PHONE)["status"], "not_found")

    def test_version_one_migration_preserves_identity_pending_receipt_and_nonce(self):
        legacy = Path(self.temporary.name) / "legacy"; legacy.mkdir(mode=0o700)
        db = legacy / "bridge.sqlite3"
        old = json.loads((PACKAGE / "examples/two-course-exams.json").read_text()); plan, body, digest = normalized_plan(old)
        with sqlite3.connect(db) as database:
            database.executescript("""
                CREATE TABLE devices(device_id TEXT PRIMARY KEY,name TEXT,secret BLOB,paired_at REAL,last_seen REAL,revoked_at REAL);
                CREATE TABLE invites(code_hash TEXT PRIMARY KEY,expires_at REAL,used INTEGER DEFAULT0);
                CREATE TABLE nonces(device_id TEXT,nonce TEXT,timestamp REAL,PRIMARY KEY(device_id,nonce));
                CREATE TABLE proposals(proposal_id TEXT PRIMARY KEY,digest TEXT,body BLOB,created_at REAL);
                CREATE TABLE deliveries(proposal_id TEXT,device_id TEXT,state TEXT,detail TEXT,updated_at REAL,PRIMARY KEY(proposal_id,device_id));
                CREATE TABLE contexts(device_id TEXT PRIMARY KEY,body BLOB,captured_at TEXT,received_at REAL);
                PRAGMA user_version=1;
            """.replace("DEFAULT0", "DEFAULT 0"))
            database.execute("INSERT INTO devices VALUES(?,?,?,?,?,NULL)", (PHONE, "Synthetic legacy", bytes(range(32)), 1, 1))
            database.execute("INSERT INTO proposals VALUES(?,?,?,?)", (plan["proposalID"], digest, body, 1))
            database.execute("INSERT INTO deliveries VALUES(?,?,?,NULL,?)", (plan["proposalID"], PHONE, "applied", 1))
            database.execute("INSERT INTO nonces VALUES(?,?,?)", (PHONE, OTHER, 1))
        migrated = DesktopBridge(legacy, "127.0.0.1", 0)
        self.assertEqual(migrated.get_plan_receipt(plan["proposalID"], PHONE)["status"], "applied")
        with migrated.connection() as database:
            self.assertEqual(database.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(database.execute("SELECT secret FROM devices").fetchone()[0], bytes(range(32)))
            self.assertEqual(database.execute("SELECT nonce FROM nonces").fetchone()[0], OTHER)
        with self.assertRaises(BridgeError): migrated.stage_semester_board(fixture(), PHONE)

    def test_legacy_device_never_receives_board_and_target_is_explicit(self):
        with self.assertRaises(BridgeError): self.bridge.stage_semester_board(fixture(), PHONE)
        self.enable()
        with self.assertRaises(BridgeError): self.bridge.stage_semester_board(fixture())
        self.bridge.stage_semester_board(fixture(), PHONE)
        old = json.loads((PACKAGE / "examples/two-course-exams.json").read_text()); self.bridge.stage_academic_plan(old, PHONE)
        self.assertEqual(self.bridge.pending_proposals(PHONE)["proposals"][0]["proposal"], old)
        self.assertEqual(self.bridge.pending_board_proposals(PHONE)["proposals"][0]["proposal"]["format"], validator.BOARD_FORMAT)

    def test_digest_comes_from_that_phone_context_and_v1_context_is_separate(self):
        self.enable(); source = fixture(); source["expectedBoardDigest"] = "a" * 64
        with self.assertRaises(BridgeError): self.bridge.stage_semester_board(source, PHONE)
        self.bridge.share_board_context(PHONE, context_fixture())
        self.assertEqual(self.bridge.stage_semester_board(source, PHONE)["status"], "pending")
        self.assertEqual(self.bridge.get_shared_academic_context(PHONE)["status"], "no_shared_context")
        self.pair(OTHER); self.enable(OTHER)
        with self.assertRaises(BridgeError): self.bridge.stage_semester_board(source, OTHER)

    def test_consumed_replay_changed_context_restart_and_delayed_ack_remain_idempotent(self):
        self.enable(); source = fixture(); source["expectedBoardDigest"] = "a" * 64
        self.bridge.share_board_context(PHONE, context_fixture())
        staged = self.bridge.stage_semester_board(source, PHONE)
        self.bridge.acknowledge(PHONE, {"proposalID": staged["proposalID"], "proposalDigest": staged["proposalDigest"], "state": "undone"})
        changed = context_fixture(); changed["boardDigest"] = "b" * 64; self.bridge.share_board_context(PHONE, changed)
        restarted = DesktopBridge(self.directory, "127.0.0.1", 0)
        self.assertEqual(restarted.stage_semester_board(source, PHONE)["status"], "undone")
        self.assertFalse(restarted.pending_board_proposals(PHONE)["proposals"])
        self.assertEqual(restarted.acknowledge(PHONE, {"proposalID": staged["proposalID"], "proposalDigest": staged["proposalDigest"], "state": "applied"})["status"], "undone")
        source["source"] = "Changed immutable content"
        with self.assertRaises(BridgeError): restarted.stage_semester_board(source, PHONE)

    def test_rotation_revocation_and_capability_downgrade_block_new_board_access(self):
        self.enable(); self.pair(PHONE)
        with self.assertRaises(BridgeError): self.bridge.pending_board_proposals(PHONE)
        self.enable(); self.bridge.revoke_device(PHONE)
        with self.assertRaises(BridgeError): self.bridge.pending_board_proposals(PHONE)

    def test_combined_pending_limit_and_board_batch_bound(self):
        self.enable()
        for _ in range(200):
            source = fixture(); source["proposalID"] = str(uuid4()); self.bridge.stage_semester_board(source, PHONE)
        self.assertEqual(len(self.bridge.pending_board_proposals(PHONE)["proposals"]), 20)
        source = fixture(); source["proposalID"] = str(uuid4())
        with self.assertRaises(BridgeError): self.bridge.stage_semester_board(source, PHONE)


@unittest.skipUnless(CRYPTO, "cryptography dependency absent")
class BoardTLSTests(_BoardFixture):
    def test_authenticated_new_routes_preserve_pairing_v1_queue_and_nonce_guards(self):
        server = PhoneBridgeServer(self.bridge); server.start()
        try:
            credentials = self.pair(PHONE)
            def request(method, path, value=None, *, signed=True, nonce=None):
                body = b"" if value is None else canonical_json(value)
                stamp = str(int(time.time())); nonce = nonce or str(uuid4())
                headers = {"Content-Type": "application/json"}
                if signed:
                    text = "\n".join((method, path, stamp, nonce, hashlib.sha256(body).hexdigest())).encode()
                    headers.update({"X-Habits-Device": PHONE, "X-Habits-Timestamp": stamp, "X-Habits-Nonce": nonce,
                                    "X-Habits-Signature": hmac.new(bytes.fromhex(credentials["deviceSecret"]), text, hashlib.sha256).hexdigest()})
                connection = http.client.HTTPSConnection(self.bridge.bind, self.bridge.port, context=ssl.create_default_context(cafile=str(self.bridge.certificate_path)), timeout=5)
                try:
                    connection.connect()
                    self.assertEqual(hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest(), self.bridge.certificate_sha256)
                    connection.request(method, path, body, headers); response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally: connection.close()
            self.assertEqual(request("POST", "/v1/assistant/capabilities", capabilities(), signed=False)[0], 401)
            self.assertEqual(request("POST", "/v1/assistant/capabilities", capabilities())[1], {"status": "capabilities_registered", "deviceID": PHONE, "platform": "android", "proposalFormats": capabilities()["proposalFormats"]})
            nonce = str(uuid4())
            self.assertEqual(request("POST", "/v1/assistant/board-context", context_fixture(), nonce=nonce)[0], 200)
            self.assertEqual(request("POST", "/v1/assistant/board-context", context_fixture(), nonce=nonce)[0], 409)
            source = fixture(); source["expectedBoardDigest"] = "a" * 64; self.bridge.stage_semester_board(source, PHONE)
            self.assertEqual(request("GET", "/v1/assistant/proposals")[1], {"proposals": []})
            rows = request("GET", "/v1/assistant/board-proposals")[1]["proposals"]
            self.assertEqual(set(rows[0]), {"proposal", "proposalDigest", "status"})
            receipt = {"proposalID": rows[0]["proposal"]["proposalID"], "proposalDigest": rows[0]["proposalDigest"], "state": "applied"}
            self.assertEqual(request("POST", "/v1/assistant/receipt", receipt)[1]["status"], "applied")
        finally: server.close()


@unittest.skipUnless(SDK, "official MCP SDK dependency absent")
class BoardMCPTests(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(CRYPTO, "cryptography dependency absent")
    async def test_new_bridge_tools_stage_pending_and_read_only_consented_board(self):
        from mcp import Client
        try:
            from habits_mcp.mcp_server import build_server
        except ModuleNotFoundError:
            from server.assistant_actions.mcp_server import build_server
        with tempfile.TemporaryDirectory() as temporary:
            bridge = DesktopBridge(Path(temporary) / "state", "127.0.0.1", 0)
            invite = bridge.create_pairing_invite()["invite"]
            bridge.pair({"pairingCode": invite["pairingCode"], "deviceID": PHONE, "deviceName": "Synthetic phone"})
            bridge.register_capabilities(PHONE, capabilities())
            bridge.share_board_context(PHONE, context_fixture())
            async with Client(build_server(bridge), cache=None) as client:
                names = {tool.name for tool in (await client.list_tools()).tools}
                self.assertEqual(len(names), 12)
                self.assertIn("get_shared_semester_board_context", names)
                source = fixture(); source["expectedBoardDigest"] = "a" * 64
                staged = await client.call_tool("stage_semester_board", {"proposal": source, "targetDeviceID": PHONE})
                self.assertFalse(staged.is_error); self.assertEqual(staged.structured_content["status"], "pending")
                context = await client.call_tool("get_shared_semester_board_context", {"targetDeviceID": PHONE})
                self.assertEqual(context.structured_content["context"], context_fixture())
                denied = await client.call_tool("stage_semester_board", {"proposal": source})
                self.assertTrue(denied.is_error)

    async def test_tool_shapes_and_closed_arguments(self):
        from mcp import ClientSession
        from mcp.shared.memory import create_client_server_memory_streams
        try:
            from habits_mcp.mcp_server import build_server
        except ModuleNotFoundError:
            from server.assistant_actions.mcp_server import build_server
        server = build_server()
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            async with asyncio.TaskGroup() as tasks:
                task = tasks.create_task(server.run(*server_streams, server.create_initialization_options()))
                async with ClientSession(*client_streams) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    self.assertEqual({tool.name for tool in listed.tools}, {"get_plan_format", "propose_academic_plan", "get_semester_board_format", "propose_semester_board"})
                    result = await session.call_tool("propose_semester_board", {"proposal": fixture()})
                    self.assertFalse(result.is_error); self.assertEqual(result.structured_content["status"], "proposal_only")
                    result = await session.call_tool("propose_semester_board", {"proposal": fixture(), "apply": True})
                    self.assertTrue(result.is_error)
                task.cancel()


if __name__ == "__main__": unittest.main()
