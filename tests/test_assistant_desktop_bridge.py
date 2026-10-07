"""Private TLS pairing, HMAC replay protection, staging, receipts, and packaging."""

import asyncio
import copy
import hashlib
import hmac
import http.client
import importlib.util
import json
import os
import ssl
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
try:
    from habits_mcp import desktop_bridge as bridge_module
    from habits_mcp.desktop_bridge import BridgeError, DesktopBridge, canonical_json, normalized_plan, private_bind, validate_context
    from habits_mcp.phone_http import PhoneBridgeServer
    from habits_mcp.validator import PlanValidationError, validate_plan
    PREFIX = "habits_mcp"
except ModuleNotFoundError:
    from server.assistant_actions import desktop_bridge as bridge_module
    from server.assistant_actions.desktop_bridge import BridgeError, DesktopBridge, canonical_json, normalized_plan, private_bind, validate_context
    from server.assistant_actions.phone_http import PhoneBridgeServer
    from server.assistant_actions.validator import PlanValidationError, validate_plan
    PREFIX = "server.assistant_actions"

PACKAGE = Path(bridge_module.__file__).resolve().parent
CRYPTO_AVAILABLE = importlib.util.find_spec("cryptography") is not None
SDK_AVAILABLE = importlib.util.find_spec("mcp") is not None
PHONE_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
PHONE_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
PAIR = "/v1/assistant/pair"
PROPOSALS = "/v1/assistant/proposals"
RECEIPT = "/v1/assistant/receipt"
CONTEXT = "/v1/assistant/context"
DISCONNECT = "/v1/assistant/disconnect"


def example():
    return json.loads((PACKAGE / "examples/two-course-exams.json").read_text())


def context_example():
    return json.loads((PACKAGE / "examples/shared-academic-context.json").read_text())


def signed_headers(device, secret, method, path, body=b"", *, nonce=None, timestamp=None):
    timestamp = str(int(time.time())) if timestamp is None else str(timestamp)
    nonce = str(uuid4()) if nonce is None else nonce
    canonical = "\n".join((method, path, timestamp, nonce, hashlib.sha256(body).hexdigest())).encode()
    return {"X-Habits-Device": device, "X-Habits-Timestamp": timestamp, "X-Habits-Nonce": nonce, "X-Habits-Signature": hmac.new(bytes.fromhex(secret), canonical, hashlib.sha256).hexdigest()}


def tls_request(bridge, method, path, value=None, credentials=None, headers=None, expected_pin=None):
    body = b"" if value is None else canonical_json(value)
    context = ssl.create_default_context(cafile=str(bridge.certificate_path))
    connection = http.client.HTTPSConnection(bridge.bind, bridge.port, context=context, timeout=10)
    try:
        connection.connect()
        actual = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
        if not hmac.compare_digest(actual, expected_pin or bridge.certificate_sha256):
            raise ssl.SSLError("certificate pin mismatch before sending request credentials")
        outgoing = {"Content-Type": "application/json"}
        if credentials:
            outgoing.update(signed_headers(credentials["deviceID"], credentials["deviceSecret"], method, path, body))
        if headers:
            outgoing.update(headers)
        connection.request(method, path, body=body, headers=outgoing)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


class DesktopContractTests(unittest.TestCase):
    def test_public_wildcard_and_nonprivate_addresses_are_rejected(self):
        for address in ("0.0.0.0", "::", "8.8.8.8", "203.0.113.1", "169.254.1.2", "example.com", "224.0.0.1"):
            with self.subTest(address=address), self.assertRaises(BridgeError): private_bind(address)
        for address in ("127.0.0.1", "::1", "192.168.1.10", "10.1.2.3", "172.16.0.10", "fd00::1"):
            self.assertEqual(private_bind(address), address)

    def test_canonical_digest_uuid_case_key_order_and_literal_unicode(self):
        plan = example()
        plan["courses"][0]["id"] = "abcdefab-1111-4111-8111-111111111111"
        plan["assessments"][0]["courseID"] = plan["courses"][0]["id"]
        plan["assessments"][0]["notes"] = "é / literal\nnotes"
        normalized, encoded, digest = normalized_plan(plan)
        reordered = dict(reversed(list(plan.items())))
        reordered["courses"][0]["id"] = reordered["courses"][0]["id"].upper()
        self.assertEqual(normalized_plan(reordered)[2], digest)
        self.assertIn("é / literal".encode(), encoded)
        self.assertNotIn(b"\\/", encoded)
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), digest)
        self.assertEqual(normalized["courses"][0]["id"], "abcdefab-1111-4111-8111-111111111111")

    def test_nfc_duplicate_title_matches_swift_canonical_equality(self):
        plan = example()
        plan["assessments"][0]["title"] = "Café exam"
        duplicate = {**plan["assessments"][0], "id": str(uuid4()), "title": "Cafe\u0301 exam"}
        plan["assessments"].append(duplicate)
        with self.assertRaisesRegex(PlanValidationError, "duplicates another assessment"):
            validate_plan(plan)

    def test_context_preserves_missing_metadata_and_fractional_utc(self):
        value = context_example()
        del value["semesters"][0]["startDate"]
        del value["semesters"][0]["timeZoneIdentifier"]
        for timestamp in ("2026-10-02T12:00:00Z", "2026-10-02T12:00:00.1Z", "2026-10-02T12:00:00.123456789Z"):
            value["capturedAt"] = timestamp
            self.assertEqual(validate_context(value), value)
        self.assertNotIn("startDate", validate_context(value)["semesters"][0])

    def test_context_rejects_unknown_fields_nulls_invalid_values_and_history(self):
        for mutate in (
            lambda c:c.update(habits=[]), lambda c:c["semesters"][0].update(tasks=[]),
            lambda c:c["semesters"][0].update(startDate=None), lambda c:c["semesters"][0].update(timeZoneIdentifier=None),
            lambda c:c["semesters"][0].update(weekCount=True), lambda c:c.update(capturedAt="2026-10-02T12:00:00+01:00"),
            lambda c:c["semesters"][0]["existingAssessments"][0].update(kind=[]),
            lambda c:c["semesters"][0]["existingAssessments"][0].update(courseID=str(uuid4())),
        ):
            value=context_example();mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(PlanValidationError): validate_context(value)
        value=context_example();value["semesters"].append(copy.deepcopy(value["semesters"][0]))
        with self.assertRaises(PlanValidationError):validate_context(value)
        with self.assertRaisesRegex(PlanValidationError,"2 MiB"):validate_context(b" "*(2*1024*1024+1))

    def test_context_assessment_state_is_optional_literal_and_never_inferred(self):
        for state in ("pending", "completed", "dismissed"):
            value=context_example();value["semesters"][0]["existingAssessments"][0]["state"]=state
            self.assertEqual(validate_context(value)["semesters"][0]["existingAssessments"][0]["state"],state)
        value=context_example()
        for item in value["semesters"][0]["existingAssessments"]:item.pop("state",None)
        self.assertEqual(validate_context(value),value)
        for item in validate_context(value)["semesters"][0]["existingAssessments"]:self.assertNotIn("state",item)
        tools=__import__(PREFIX+".tools",fromlist=["get_plan_format"])
        rules=" ".join(tools.get_plan_format()["semanticRules"])
        self.assertIn("omitted assessment state is unknown, never pending",rules)

    def test_context_invalid_assessment_state_is_rejected_without_coercion(self):
        for state in (None,True,1,"Pending","finished",[],{}):
            value=context_example();value["semesters"][0]["existingAssessments"][0]["state"]=state
            with self.subTest(state=state),self.assertRaisesRegex(PlanValidationError,"state"):
                validate_context(value)

    def test_fixed_phone_hmac_vector(self):
        headers=signed_headers(PHONE_A,bytes(range(32)).hex(),"POST",DISCONNECT,b"{}",nonce=PHONE_A,timestamp=1790942400)
        self.assertEqual(headers["X-Habits-Signature"],"9b6e4f1225d31dcc86399727fa99fc3fe9d5140b6e8fc1bf94c1609c2dee4381")


@unittest.skipUnless(CRYPTO_AVAILABLE, "optional cryptography dependency is absent")
class DesktopStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="habits-bridge-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory=Path(self.temporary.name)/"state"
        self.bridge=DesktopBridge(self.directory,port=0)

    def pair(self, device=PHONE_A):
        invite=self.bridge.create_pairing_invite()["invite"]
        return self.bridge.pair({"pairingCode":invite["pairingCode"],"deviceID":device,"deviceName":"Test phone"})

    def test_private_storage_certificate_and_invite_permissions(self):
        from cryptography import x509
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        invite=self.bridge.create_pairing_invite()
        self.assertEqual(len(invite["invite"]["pairingCode"]),32)
        certificate=x509.load_pem_x509_certificate(self.bridge.certificate_path.read_bytes())
        names=certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        self.assertIn("localhost",names.get_values_for_type(x509.DNSName))
        import ipaddress
        self.assertIn(ipaddress.ip_address("10.0.2.2"),names.get_values_for_type(x509.IPAddress))
        key=serialization.load_pem_private_key(self.bridge.key_path.read_bytes(),password=None)
        self.assertIsInstance(key,ec.EllipticCurvePrivateKey)
        self.assertEqual(key.curve.name,"secp256r1")
        if os.name!="nt":
            self.assertEqual(self.directory.stat().st_mode&0o777,0o700)
            for path in (self.bridge.db_path,self.bridge.key_path,self.bridge.certificate_path,Path(invite["invitePath"])):
                self.assertEqual(path.stat().st_mode&0o777,0o600)
        status=self.bridge.desktop_status()
        self.assertNotIn("secret",canonical_json(status).decode())

    def test_expired_used_rotated_invites_and_pair_secret_rotation(self):
        first=self.bridge.create_pairing_invite()["invite"]
        current=self.bridge.create_pairing_invite()["invite"]
        request={"pairingCode":first["pairingCode"],"deviceID":PHONE_A,"deviceName":"Test phone"}
        with self.assertRaises(BridgeError):self.bridge.pair(request)
        request["pairingCode"]=current["pairingCode"]
        credentials=self.bridge.pair(request)
        self.assertEqual(len(credentials["deviceSecret"]),64)
        with self.assertRaises(BridgeError):self.bridge.pair(request)
        later=self.bridge.create_pairing_invite()["invite"]
        with self.bridge.connection() as database:database.execute("UPDATE invites SET expires_at=0")
        request["pairingCode"]=later["pairingCode"]
        with self.assertRaises(BridgeError):self.bridge.pair(request)
        second=self.pair()
        self.assertNotEqual(second["deviceSecret"],credentials["deviceSecret"])

    def test_requires_one_device_or_explicit_paired_target(self):
        with self.assertRaises(BridgeError):self.bridge.stage_academic_plan(example())
        self.pair();self.pair(PHONE_B)
        with self.assertRaises(BridgeError):self.bridge.stage_academic_plan(example())
        self.assertEqual(self.bridge.stage_academic_plan(example(),PHONE_A)["targetDeviceID"],PHONE_A)
        with self.assertRaises(BridgeError):self.bridge.stage_academic_plan(example(),str(uuid4()))

    def test_immutable_idempotent_staging_ack_states_and_offline_undo(self):
        self.pair()
        first=self.bridge.stage_academic_plan(example())
        self.assertEqual(first["status"],"pending")
        self.assertEqual(self.bridge.stage_academic_plan(example()),first)
        changed=example();changed["semester"]["title"]="Changed"
        with self.assertRaisesRegex(BridgeError,"immutable"):self.bridge.stage_academic_plan(changed)
        ack={"proposalID":first["proposalID"],"proposalDigest":first["proposalDigest"],"state":"applied","detail":"Confirmed on phone"}
        applied=self.bridge.acknowledge(PHONE_A,ack)
        self.assertEqual(applied["status"],"applied")
        self.assertEqual(self.bridge.acknowledge(PHONE_A,{**ack,"detail":"retry"}),applied)
        self.assertEqual(self.bridge.pending_proposals(PHONE_A),{"proposals":[]})
        undone=self.bridge.acknowledge(PHONE_A,{**ack,"state":"undone"})
        self.assertEqual(undone["status"],"undone")
        self.assertEqual(self.bridge.acknowledge(PHONE_A,ack),undone)
        self.assertEqual(self.bridge.stage_academic_plan(example())["status"],"undone")
        another=example();another["proposalID"]=str(uuid4())
        pending=self.bridge.stage_academic_plan(another)
        self.assertEqual(self.bridge.acknowledge(PHONE_A,{"proposalID":pending["proposalID"],"proposalDigest":pending["proposalDigest"],"state":"undone"})["status"],"undone")

    def test_stage_rejects_nfc_duplicate_titles_before_persisting_any_proposal(self):
        self.pair()
        plan=example();plan["assessments"][0]["title"]="Café exam"
        duplicate={**plan["assessments"][0],"id":str(uuid4()),"title":"Cafe\u0301 exam"}
        plan["assessments"].append(duplicate)
        with self.assertRaisesRegex(PlanValidationError,"duplicates another assessment"):
            self.bridge.stage_academic_plan(plan)
        self.assertEqual(self.bridge.pending_proposals(PHONE_A),{"proposals":[]})
        self.assertEqual(self.bridge.desktop_status()["pendingProposalCount"],0)

    def test_rejected_is_terminal_and_foreign_or_wrong_digest_ack_is_refused(self):
        self.pair();self.pair(PHONE_B)
        staged=self.bridge.stage_academic_plan(example(),PHONE_A)
        ack={"proposalID":staged["proposalID"],"proposalDigest":staged["proposalDigest"],"state":"rejected"}
        with self.assertRaises(BridgeError):self.bridge.acknowledge(PHONE_B,ack)
        with self.assertRaises(BridgeError):self.bridge.acknowledge(PHONE_A,{**ack,"proposalDigest":"0"*64})
        self.assertEqual(self.bridge.acknowledge(PHONE_A,ack)["status"],"rejected")
        with self.assertRaises(BridgeError):self.bridge.acknowledge(PHONE_A,{**ack,"state":"applied"})
        self.assertEqual(self.bridge.stage_academic_plan(example(),PHONE_A)["status"],"rejected")

    def test_pending_batches_are_bounded_and_advance_after_receipts(self):
        self.pair()
        for index in range(21):
            plan=example();plan["proposalID"]=str(UUID(int=1000+index));self.bridge.stage_academic_plan(plan)
        batch=self.bridge.pending_proposals(PHONE_A)
        self.assertEqual(len(batch["proposals"]),20)
        self.assertLessEqual(len(canonical_json(batch)),2*1024*1024)
        item=batch["proposals"][0]
        self.bridge.acknowledge(PHONE_A,{"proposalID":item["proposal"]["proposalID"],"proposalDigest":item["proposalDigest"],"state":"rejected"})
        next_batch=self.bridge.pending_proposals(PHONE_A)
        self.assertEqual(len(next_batch["proposals"]),20)
        self.assertNotEqual(next_batch["proposals"][0]["proposal"]["proposalID"],item["proposal"]["proposalID"])

    def test_snapshot_is_explicit_private_and_context_omissions_are_disclosed(self):
        self.pair()
        self.assertEqual(self.bridge.get_shared_academic_context()["status"],"no_shared_context")
        value=context_example();del value["semesters"][0]["timeZoneIdentifier"]
        self.bridge.share_context(PHONE_A,value)
        result=self.bridge.get_shared_academic_context()
        self.assertEqual(result["context"],value)
        self.assertIn("snapshot",result["message"])
        self.assertIn("Missing semester",result["message"])

    def test_empty_and_terminal_only_snapshots_have_unknown_calendar_coverage(self):
        self.pair()
        empty={"format":"habits.academic-context","version":1,"capturedAt":"2026-10-02T12:00:00Z","semesters":[]}
        terminal=context_example()
        for assessment in terminal["semesters"][0]["existingAssessments"]:assessment["state"]="completed"
        for context in (empty,terminal):
            self.bridge.share_context(PHONE_A,context)
            result=self.bridge.get_shared_academic_context()
            self.assertEqual(result["calendarCoverage"],"unknown")
            self.assertIn("including when the snapshot is empty",result["message"])
            self.assertIn("missing records do not establish free time",result["message"])

    def test_revocation_preserves_history_and_repair_restores_access(self):
        self.pair();staged=self.bridge.stage_academic_plan(example());self.bridge.share_context(PHONE_A,context_example())
        self.assertEqual(self.bridge.revoke_device(PHONE_A)["status"],"device_revoked")
        self.assertEqual(self.bridge.revoke_device(PHONE_A)["status"],"device_revoked")
        self.assertEqual(self.bridge.desktop_status()["pairedDevices"],[])
        with self.assertRaises(BridgeError):self.bridge.target_device(PHONE_A)
        self.pair()
        self.assertEqual(self.bridge.get_plan_receipt(staged["proposalID"])["status"],"pending")
        self.assertEqual(self.bridge.get_shared_academic_context()["context"],context_example())

    def test_restart_preserves_certificates_proposals_and_nonce_replay_guard(self):
        credentials=self.pair();staged=self.bridge.stage_academic_plan(example())
        headers=signed_headers(PHONE_A,credentials["deviceSecret"],"GET",PROPOSALS,nonce=PHONE_B)
        self.bridge.authenticate("GET",PROPOSALS,b"",headers)
        restarted=DesktopBridge(self.directory,port=0)
        self.assertEqual(restarted.certificate_sha256,self.bridge.certificate_sha256)
        self.assertEqual(restarted.get_plan_receipt(staged["proposalID"])["status"],"pending")
        with self.assertRaisesRegex(BridgeError,"already used"):restarted.authenticate("GET",PROPOSALS,b"",headers)

    def test_nonbridge_directory_and_symlink_paths_are_refused_without_deletion(self):
        unrelated=Path(self.temporary.name)/"unrelated";unrelated.mkdir();file=unrelated/"user.txt";file.write_text("keep")
        with self.assertRaises(BridgeError):DesktopBridge(unrelated)
        self.assertEqual(file.read_text(),"keep")
        if hasattr(os,"symlink"):
            symlink=Path(self.temporary.name)/"link";symlink.symlink_to(self.directory,target_is_directory=True)
            with self.assertRaises(BridgeError):DesktopBridge(symlink)


@unittest.skipUnless(CRYPTO_AVAILABLE,"optional cryptography dependency is absent")
class DesktopTLSTransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix="habits-tls-test-")
        self.addCleanup(self.temporary.cleanup)
        self.bridge=DesktopBridge(Path(self.temporary.name)/"state",port=0)
        self.server=PhoneBridgeServer(self.bridge);self.server.start();self.addCleanup(self.server.close)

    def pair(self, device=PHONE_A):
        invite=self.bridge.create_pairing_invite()["invite"]
        status,result=tls_request(self.bridge,"POST",PAIR,{"pairingCode":invite["pairingCode"],"deviceID":device,"deviceName":"Fictional test phone"})
        self.assertEqual(status,200)
        return result

    def test_real_tls_pair_stage_ack_and_context(self):
        credentials=self.pair();staged=self.bridge.stage_academic_plan(example())
        status,result=tls_request(self.bridge,"GET",PROPOSALS,credentials=credentials)
        self.assertEqual(status,200);self.assertEqual(result["proposals"][0]["status"],"pending")
        self.assertEqual(result["proposals"][0]["proposalDigest"],staged["proposalDigest"])
        status,result=tls_request(self.bridge,"POST",CONTEXT,context_example(),credentials)
        self.assertEqual(status,200);self.assertEqual(result["status"],"context_shared")
        self.assertEqual(self.bridge.get_shared_academic_context()["context"],context_example())
        receipt={"proposalID":staged["proposalID"],"proposalDigest":staged["proposalDigest"],"state":"applied"}
        status,result=tls_request(self.bridge,"POST",RECEIPT,receipt,credentials)
        self.assertEqual(status,200);self.assertEqual(result["status"],"applied")

    def test_unsigned_stale_forged_tampered_and_replayed_requests(self):
        credentials=self.pair()
        self.assertEqual(tls_request(self.bridge,"GET",PROPOSALS)[0],401)
        stale=signed_headers(PHONE_A,credentials["deviceSecret"],"GET",PROPOSALS,timestamp=int(time.time())-121)
        self.assertEqual(tls_request(self.bridge,"GET",PROPOSALS,headers=stale)[0],401)
        headers=signed_headers(PHONE_A,credentials["deviceSecret"],"GET",PROPOSALS,nonce=PHONE_B)
        forged={**headers,"X-Habits-Signature":"0"*64}
        self.assertEqual(tls_request(self.bridge,"GET",PROPOSALS,headers=forged)[0],401)
        self.assertEqual(tls_request(self.bridge,"GET",PROPOSALS,headers=headers)[0],200)
        self.assertEqual(tls_request(self.bridge,"GET",PROPOSALS,headers=headers)[0],409)
        value=context_example();body=canonical_json(value)
        headers=signed_headers(PHONE_A,credentials["deviceSecret"],"POST",CONTEXT,body)
        value["capturedAt"]="2026-10-02T12:00:01Z"
        self.assertEqual(tls_request(self.bridge,"POST",CONTEXT,value,headers=headers)[0],401)

    def test_tls_trust_and_pin_mismatch_never_send_pairing_code(self):
        invite=self.bridge.create_pairing_invite()["invite"]
        request={"pairingCode":invite["pairingCode"],"deviceID":PHONE_A,"deviceName":"Test phone"}
        with self.assertRaises(ssl.SSLError):tls_request(self.bridge,"POST",PAIR,request,expected_pin="0"*64)
        connection=http.client.HTTPSConnection(self.bridge.bind,self.bridge.port,context=ssl.create_default_context(),timeout=5)
        try:
            with self.assertRaises(ssl.SSLCertVerificationError):connection.connect()
        finally:connection.close()
        self.assertEqual(tls_request(self.bridge,"POST",PAIR,request)[0],200)
        self.assertEqual(tls_request(self.bridge,"POST",PAIR,request)[0],401)

    def test_foreign_device_sees_no_proposals_and_cannot_ack_other_target(self):
        first=self.pair();second=self.pair(PHONE_B)
        staged=self.bridge.stage_academic_plan(example(),PHONE_A)
        self.assertEqual(tls_request(self.bridge,"GET",PROPOSALS,credentials=second),(200,{"proposals":[]}))
        receipt={"proposalID":staged["proposalID"],"proposalDigest":staged["proposalDigest"],"state":"applied"}
        self.assertEqual(tls_request(self.bridge,"POST",RECEIPT,receipt,second)[0],404)
        self.assertEqual(tls_request(self.bridge,"POST",RECEIPT,receipt,first)[0],200)

    def test_authenticated_disconnect_invalidates_future_signatures(self):
        credentials=self.pair()
        self.assertEqual(tls_request(self.bridge,"POST",DISCONNECT,{},credentials)[0],200)
        self.assertEqual(tls_request(self.bridge,"GET",PROPOSALS,credentials=credentials)[0],401)
        self.assertEqual(self.bridge.desktop_status()["pairedDevices"],[])

    def test_path_method_and_body_limits_do_not_bypass_authentication(self):
        credentials=self.pair()
        self.assertEqual(tls_request(self.bridge,"GET",PROPOSALS+"?deviceID="+PHONE_A,credentials=credentials)[0],404)
        self.assertEqual(tls_request(self.bridge,"POST",PAIR,{"pairingCode":"x"*9000,"deviceID":PHONE_A,"deviceName":"Test"})[0],413)
        self.assertEqual(tls_request(self.bridge,"POST",CONTEXT,{**context_example(),"habits":[]},credentials)[0],400)


@unittest.skipUnless(CRYPTO_AVAILABLE and SDK_AVAILABLE,"official SDK/cryptography dependencies are absent")
class DesktopMCPTransportTests(unittest.TestCase):
    def test_real_stdio_desktop_tools_keep_staging_pending(self):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters
        with tempfile.TemporaryDirectory(prefix="habits-mcp-stdio-test-") as temporary:
            state=Path(temporary)/"state"
            async def run():
                parameters=StdioServerParameters(command=sys.executable,args=["-m",PREFIX+".desktop_cli","--phone-bridge","--phone-port",str(self.free_port()),"--data-dir",str(state)],env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"},cwd=REPO)
                async with Client(parameters,read_timeout_seconds=15,cache=None) as client:
                    names={tool.name for tool in (await client.list_tools()).tools}
                    self.assertEqual(names,{"get_plan_format","propose_academic_plan","get_semester_board_format","propose_semester_board","desktop_status","create_pairing_invite","get_shared_academic_context","stage_academic_plan","get_shared_semester_board_context","stage_semester_board","get_plan_receipt","revoke_device"})
                    status=(await client.call_tool("desktop_status",{})).structured_content
                    self.assertEqual(status["status"],"desktop_ready")
                    invite=(await client.call_tool("create_pairing_invite",{})).structured_content["invite"]
                    # Separate local test client pairs through the real TLS endpoint.
                    local=DesktopBridge(state,port=int(invite["baseURL"].rsplit(":",1)[1]))
                    response=tls_request(local,"POST",PAIR,{"pairingCode":invite["pairingCode"],"deviceID":PHONE_A,"deviceName":"Fictional test phone"})
                    self.assertEqual(response[0],200)
                    staged=(await client.call_tool("stage_academic_plan",{"proposal":example()})).structured_content
                    self.assertEqual(staged["status"],"pending")
                    receipt=(await client.call_tool("get_plan_receipt",{"proposalID":staged["proposalID"]})).structured_content
                    self.assertEqual(receipt["status"],"pending")
                    self.assertTrue((await client.call_tool("stage_academic_plan",{"proposal":example(),"apply":True})).is_error)
                    self.assertTrue((await client.call_tool("apply_academic_plan",{})).is_error)
                    self.assertEqual((await client.call_tool("revoke_device",{"deviceID":PHONE_A})).structured_content["status"],"device_revoked")
            asyncio.run(run())

    @staticmethod
    def free_port():
        import socket
        with socket.socket() as listener:
            listener.bind(("127.0.0.1",0));return listener.getsockname()[1]


class DesktopReleaseTests(unittest.TestCase):
    def test_source_archive_whitelist_and_manifest_exclude_runtime_material(self):
        module=__import__(PREFIX+".release",fromlist=["build_source_release"])
        with tempfile.TemporaryDirectory(prefix="habits-release-test-") as temporary:
            wheel=Path(temporary)/"habits_desktop_mcp-0.1.0-py3-none-any.whl"
            with zipfile.ZipFile(wheel,"w") as archive:archive.writestr("habits_mcp/__init__.py","# test fixture")
            result=module.build_source_release(wheel,Path(temporary)/"output")
            self.assertEqual(len(result["files"]),2)
            for entry in result["files"]:
                path=Path(temporary)/"output"/entry["name"]
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),entry["sha256"])
            source=next((Path(temporary)/"output").glob("*-source.zip"))
            with zipfile.ZipFile(source) as archive:
                names=archive.namelist()
                self.assertTrue(any(name.endswith("pyproject.toml") for name in names))
                self.assertTrue(any(name.endswith("examples/two-course-exams.json") for name in names))
                self.assertFalse(any(name.endswith((".pem",".sqlite3","pairing-invite.json")) or "/release/" in name or "__pycache__" in name for name in names))

    @unittest.skipUnless(os.environ.get("HABITS_MCP_WHEEL"),"set HABITS_MCP_WHEEL to validate a built wheel")
    def test_universal_wheel_installs_in_clean_environment_and_has_resources(self):
        import venv
        wheel=Path(os.environ["HABITS_MCP_WHEEL"]).resolve()
        with tempfile.TemporaryDirectory(prefix="habits-wheel-clean-test-") as temporary:
            environment=Path(temporary)/"venv"
            venv.EnvBuilder(with_pip=True).create(environment)
            executable=environment/("Scripts/python.exe" if os.name=="nt" else "bin/python")
            subprocess.run([str(executable),"-m","pip","install","--no-deps","--no-index",str(wheel)],check=True,capture_output=True,text=True)
            code="from habits_mcp import get_plan_format,propose_academic_plan,get_semester_board_format,propose_semester_board; data=get_plan_format(); assert data['example']['format']=='habits.academic-plan'; assert propose_academic_plan(data['example'])['status']=='proposal_only'; board=get_semester_board_format(); assert board['example']['format']=='habits.semester-board-proposal'; assert propose_semester_board(board['example'])['status']=='proposal_only'"
            subprocess.run([str(executable),"-c",code],check=True,cwd=temporary,capture_output=True,text=True)
            command=environment/("Scripts/habits-mcp.exe" if os.name=="nt" else "bin/habits-mcp")
            result=subprocess.run([str(command),"--version"],check=True,cwd=temporary,capture_output=True,text=True)
            self.assertIn("0.2.0",result.stdout)
            with zipfile.ZipFile(wheel) as archive:
                self.assertIn("habits_mcp/academic-plan.schema.json",archive.namelist())
                self.assertIn("habits_mcp/examples/two-course-exams.json",archive.namelist())
                self.assertFalse(any(name.endswith((".pem",".sqlite3")) for name in archive.namelist()))


if __name__=="__main__":
    unittest.main()
