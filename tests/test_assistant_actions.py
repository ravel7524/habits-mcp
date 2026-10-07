"""Proposal validation, contract boundaries, and optional real MCP transport tests."""

import asyncio
import copy
import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path
from uuid import UUID

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from habits_mcp import MAX_BYTES, PlanValidationError, get_plan_format, propose_academic_plan, validate_plan

FIXTURE = Path(__import__("habits_mcp").__file__).resolve().parent / "examples/two-course-exams.json"
MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None


def example():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class AssistantActionsValidationTests(unittest.TestCase):
    def assert_rejected(self, plan, message=None):
        with self.assertRaises(PlanValidationError) as caught:
            validate_plan(plan)
        if message:
            self.assertIn(message, str(caught.exception))

    def test_valid_example_preserves_unknown_time_and_undated_preparation(self):
        plan = example()
        validated = validate_plan(FIXTURE.read_bytes())
        self.assertEqual(validated, plan)
        self.assertNotIn("dueTime", validated["assessments"][1])
        self.assertNotIn("dueDate", validated["preparationTasks"][1])
        validated["semester"]["title"] = "Edited result"
        self.assertEqual(plan["semester"]["title"], "Autumn 2026")

    def test_tools_explicitly_return_proposal_only(self):
        for result in (get_plan_format(), propose_academic_plan(example())):
            self.assertEqual(result["status"], "proposal_only")
            self.assertIn("No phone, app database", result["message"])
        result = propose_academic_plan(example())
        self.assertEqual(result["proposal"], example())

    def test_unknown_keys_rejected_at_every_object_level(self):
        for locate in (lambda p:p, lambda p:p["semester"], lambda p:p["courses"][0], lambda p:p["assessments"][0], lambda p:p["preparationTasks"][0]):
            with self.subTest(locate=locate):
                plan = example()
                locate(plan)["applyNow"] = True
                self.assert_rejected(plan, "unsupported fields")

    def test_missing_required_keys_rejected(self):
        cases = [("proposalID",None),("source",None),("dueDate","assessments"),("notes","assessments"),("estimatedWorkMinutes","preparationTasks")]
        for key, collection in cases:
            with self.subTest(key=key):
                plan = example()
                del (plan if collection is None else plan[collection][0])[key]
                self.assert_rejected(plan, "missing required")

    def test_format_and_version_are_not_coerced(self):
        for key, value in [("format","habits.academic-plan.v2"),("version",2),("version",True),("version",1.0),("version","1")]:
            plan = example(); plan[key] = value
            with self.subTest(key=key,value=value): self.assert_rejected(plan)

    def test_title_name_source_are_bounded_nonempty_and_trimmed(self):
        cases = [(lambda p:p,"source",300),(lambda p:p["semester"],"title",200),(lambda p:p["courses"][0],"name",200),(lambda p:p["assessments"][0],"title",200),(lambda p:p["preparationTasks"][0],"title",200)]
        for locate,key,limit in cases:
            for bad in ("", " ", " leading", "trailing\n", "x"*(limit+1), None):
                plan=example();locate(plan)[key]=bad
                with self.subTest(key=key,bad=bad): self.assert_rejected(plan)
            plan=example();locate(plan)[key]="é"*limit
            self.assertEqual(validate_plan(plan),plan)

    def test_notes_are_literal_and_bounded(self):
        for collection in ("assessments", "preparationTasks"):
            plan=example();plan[collection][0]["notes"]=" \nLiteral user text\t "
            self.assertEqual(validate_plan(plan)[collection][0]["notes"], plan[collection][0]["notes"])
            plan[collection][0]["notes"]="x"*4000;validate_plan(plan)
            plan[collection][0]["notes"]="x"*4001;self.assert_rejected(plan)
            plan[collection][0]["notes"]=None;self.assert_rejected(plan)

    def test_utf8_payload_limit_accepts_exact_boundary(self):
        raw=FIXTURE.read_bytes()
        padded=raw+b" "*(MAX_BYTES-len(raw))
        self.assertEqual(validate_plan(padded),example())
        self.assert_rejected(padded+b" ","2 MiB")
        self.assert_rejected("é"*(MAX_BYTES//2+1),"2 MiB")

    def test_invalid_json_unicode_and_duplicate_keys_rejected(self):
        for raw in (b"\xff", "{", "[]", '{"version":1,"version":1}', '{"value":NaN}', '{"value":Infinity}', "\ufeff{}"):
            with self.subTest(raw=raw): self.assert_rejected(raw)
        plan=example();plan["source"]="\ud800";self.assert_rejected(plan)

    def test_python_only_types_are_not_coerced(self):
        plan=example();plan["courses"]=tuple(plan["courses"]);self.assert_rejected(plan)
        plan=example();plan["semester"][1]="bad";self.assert_rejected(plan)

    def test_uuid_identity_is_global_and_case_insensitive(self):
        plan=example();plan["preparationTasks"][0]["id"]=plan["courses"][0]["id"]
        self.assert_rejected(plan,"duplicates")
        plan=example();plan["semester"]["id"]=plan["proposalID"]
        self.assert_rejected(plan,"duplicates")
        plan=example();plan["courses"][0]["id"]="ABCDEFAB-1111-4111-8111-111111111111"
        plan["assessments"][0]["courseID"]=plan["courses"][0]["id"].lower()
        validate_plan(plan)
        plan["courses"][1]["id"]=plan["courses"][0]["id"].lower()
        self.assert_rejected(plan,"duplicates")

    def test_uuid_requires_hyphenated_format(self):
        for bad in ("not-a-uuid", "11111111111141118111111111111111", "{11111111-1111-4111-8111-111111111111}", 1):
            plan=example();plan["proposalID"]=bad
            with self.subTest(bad=bad): self.assert_rejected(plan,"UUID")

    def test_references_must_resolve(self):
        for collection,key in (("assessments","courseID"),("preparationTasks","assessmentID")):
            plan=example();plan[collection][0][key]="99999999-9999-4999-8999-999999999999"
            self.assert_rejected(plan,"must reference")

    def test_course_limit_matches_existing_codec(self):
        plan=example()
        plan["courses"].extend({"id":str(UUID(int=i)),"name":f"Course {i}"} for i in range(1,9))
        validate_plan(plan)
        plan["courses"].append({"id":str(UUID(int=9)),"name":"Course 11"})
        self.assert_rejected(plan,"10 items")

    def test_assessment_and_preparation_limits_handle_scale(self):
        for collection in ("assessments","preparationTasks"):
            plan=example(); template=plan[collection][0]
            plan[collection]=[{**copy.deepcopy(template),"id":str(UUID(int=1000+i)),"title":f"Item {i}"} for i in range(200)]
            if collection=="assessments": plan["preparationTasks"]=[]
            validate_plan(plan)
            plan[collection].append({**copy.deepcopy(template),"id":str(UUID(int=3000))})
            self.assert_rejected(plan,"200 items")

    def test_week_count_and_estimate_integer_boundaries(self):
        for value in (0,53,True,14.0,"14"):
            plan=example();plan["semester"]["weekCount"]=value;self.assert_rejected(plan)
        for value in (1,52):
            plan=example();plan["semester"]["weekCount"]=value;validate_plan(plan)
        for value in (-1,1441,True,45.0,"45"):
            plan=example();plan["preparationTasks"][0]["estimatedWorkMinutes"]=value;self.assert_rejected(plan)
        for value in (0,1440):
            plan=example();plan["preparationTasks"][0]["estimatedWorkMinutes"]=value;validate_plan(plan)

    def test_all_supported_assessment_kinds(self):
        for kind in ("exam","presentation","essay","deadline"):
            plan=example();plan["assessments"][0]["kind"]=kind;validate_plan(plan)
        for kind in ("assignment","Exam",None):
            plan=example();plan["assessments"][0]["kind"]=kind;self.assert_rejected(plan)

    def test_civil_dates_and_times_are_strict(self):
        for day in ("2026-02-29","2026-04-31","0000-01-01","2026-1-01","2026-12-16T09:00:00Z",None):
            plan=example();plan["assessments"][0]["dueDate"]=day;self.assert_rejected(plan)
        for clock in ("24:00","12:60","9:00","09:00:00","09:00Z",None):
            plan=example();plan["assessments"][0]["dueTime"]=clock;self.assert_rejected(plan)
        plan=example();plan["semester"]["startDate"]="9999-12-31";self.assert_rejected(plan,"range")

    def test_iana_timezones_and_skipped_days(self):
        for zone in ("Unknown/Zone","+02:00","localtime","EST","CET","GMT"," Europe/Zurich",None):
            plan=example();plan["semester"]["timeZoneIdentifier"]=zone;self.assert_rejected(plan)
        plan=example();plan["semester"]["timeZoneIdentifier"]="UTC";validate_plan(plan)
        plan=example();plan["semester"].update(timeZoneIdentifier="Pacific/Apia",startDate="2011-12-30")
        self.assert_rejected(plan,"does not exist")

    def test_iana_aliases_are_accepted(self):
        for zone in ("Etc/UTC", "US/Eastern"):
            plan=example();plan["semester"]["timeZoneIdentifier"]=zone;validate_plan(plan)

    def test_controls_are_rejected_and_notes_keep_only_supported_controls(self):
        for character in ("\x00","\x7f","\x85","\u00ad","\u200b","\u200d","\u2060","\ufeff","\U000e0001"):
            plan=example();plan["source"]="Source"+character+"text";self.assert_rejected(plan,"control")
            plan=example();plan["assessments"][0]["notes"]="Notes"+character+"text";self.assert_rejected(plan,"control")
        for character in ("\t","\n","\r"):
            plan=example();plan["source"]="Source"+character+"text";self.assert_rejected(plan,"control")
            plan=example();plan["assessments"][0]["notes"]="Notes"+character+"text";validate_plan(plan)

    def test_duplicate_assessments_rejected_without_collapsing_distinct_dates_or_case(self):
        plan=example();item=copy.deepcopy(plan["assessments"][0]);item["id"]="99999999-9999-4999-8999-999999999999"
        plan["assessments"].append(item);self.assert_rejected(plan,"duplicates another assessment")
        item["title"]=item["title"].lower();validate_plan(plan)
        item["title"]=plan["assessments"][0]["title"];item["dueDate"]="2026-12-17";validate_plan(plan)

    def test_date_only_prep_rejects_nonexistent_storage_midnight(self):
        plan=example();plan["semester"]["timeZoneIdentifier"]="America/Sao_Paulo"
        task=plan["preparationTasks"][0];task["dueDate"]="2018-11-04";del task["dueTime"]
        self.assert_rejected(plan,"does not exist")
        task["dueTime"]="01:00";validate_plan(plan)

    def test_dst_gaps_and_ambiguities_rejected(self):
        for day in ("2026-03-29","2026-10-25"):
            plan=example();plan["assessments"][0].update(dueDate=day,dueTime="02:30")
            plan["preparationTasks"]=[]
            with self.subTest(day=day): self.assert_rejected(plan,"local date/time")
        plan=example();plan["assessments"][0].update(dueDate="2026-03-29",dueTime="03:30")
        plan["preparationTasks"]=[];validate_plan(plan)

    def test_missing_prep_dates_preserved_and_time_without_date_rejected(self):
        plan=example();plan["preparationTasks"][1]["dueTime"]="12:00"
        self.assert_rejected(plan,"dueDate")

    def test_preparation_deadline_order_and_date_only_semantics(self):
        plan=example();task=plan["preparationTasks"][0]
        task.update(dueDate="2026-12-16",dueTime="09:00");validate_plan(plan)
        task["dueTime"]="09:01";self.assert_rejected(plan,"no later")
        del task["dueTime"];self.assert_rejected(plan,"no later")
        del plan["assessments"][0]["dueTime"];validate_plan(plan)
        task["dueDate"]="2026-12-17";self.assert_rejected(plan,"no later")

    @unittest.skipUnless(importlib.util.find_spec("jsonschema") is not None,"optional jsonschema absent")
    def test_canonical_schema_is_valid_and_agrees_on_structural_limits(self):
        from jsonschema import Draft202012Validator, FormatChecker
        schema=get_plan_format()["schema"]
        Draft202012Validator.check_schema(schema)
        validator=Draft202012Validator(schema,format_checker=FormatChecker())
        validator.validate(example())
        self.assertEqual(schema["properties"]["courses"]["maxItems"],10)
        self.assertEqual(schema["properties"]["assessments"]["maxItems"],200)
        self.assertEqual(schema["properties"]["preparationTasks"]["maxItems"],200)
        for mutate in (lambda p:p.update(unknown=1), lambda p:p.update(version=True), lambda p:p["preparationTasks"][1].update(dueTime="09:00")):
            plan=example();mutate(plan)
            self.assertTrue(list(validator.iter_errors(plan)))


@unittest.skipUnless(MCP_AVAILABLE,"optional official MCP SDK not installed")
class AssistantActionsMCPTests(unittest.TestCase):
    async def check_client(self, client):
        # Client's context entry performs a real MCP initialize handshake.
        listed=await client.list_tools()
        self.assertEqual({tool.name for tool in listed.tools},{"get_plan_format","propose_academic_plan","get_semester_board_format","propose_semester_board"})
        for tool in listed.tools:
            self.assertTrue(tool.annotations.read_only_hint)
            self.assertFalse(tool.annotations.destructive_hint)
        fmt=await client.call_tool("get_plan_format",{})
        self.assertFalse(fmt.is_error);self.assertEqual(fmt.structured_content["status"],"proposal_only")
        result=await client.call_tool("propose_academic_plan",{"proposal":example()})
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["proposal"],example())
        self.assertEqual(json.loads(result.content[0].text),result.structured_content)
        invalid=example();invalid["assessments"][0]["dueDate"]="2026-02-30"
        result=await client.call_tool("propose_academic_plan",{"proposal":invalid})
        self.assertTrue(result.is_error)
        result=await client.call_tool("propose_academic_plan",{"proposal":example(),"apply":True})
        self.assertTrue(result.is_error)
        result=await client.call_tool("apply_academic_plan",{})
        self.assertTrue(result.is_error)

    def test_real_stdio_initialize_list_and_call(self):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters
        async def run():
            parameters=StdioServerParameters(command=sys.executable,args=["-m","habits_mcp","stdio"],cwd=REPO,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
            async with Client(parameters,read_timeout_seconds=10,cache=None) as client:
                await self.check_client(client)
        asyncio.run(run())

    def test_real_loopback_http_initialize_list_and_call(self):
        from mcp import Client
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1",0));port=reserved.getsockname()[1]
        process=subprocess.Popen([sys.executable,"-m","habits_mcp","http","--port",str(port)],cwd=REPO,env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"},stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1",port))==0: break
                if process.poll() is not None: self.fail(process.stderr.read().decode())
                time.sleep(0.05)
            else: self.fail("loopback MCP server did not start within 10 seconds")
            async def run():
                async with Client(f"http://127.0.0.1:{port}/mcp",read_timeout_seconds=10,cache=None) as client:
                    await self.check_client(client)
            asyncio.run(run())
            from urllib.error import HTTPError
            from urllib.request import Request,urlopen
            # Host filtering remains active even on the development endpoint.
            with self.assertRaises(HTTPError) as failure:
                urlopen(Request(f"http://127.0.0.1:{port}/mcp",headers={"Host":"evil.example"}),timeout=5)
            self.assertEqual(failure.exception.code,421)
        finally:
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            process.stderr.close()


if __name__ == "__main__":
    unittest.main()
