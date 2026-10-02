"""Official MCP SDK transport adapter (dependency isolated from validation)."""

from __future__ import annotations

import json
from typing import Any

from .tools import get_plan_format, propose_academic_plan
from .validator import MAX_BYTES, PlanValidationError


def build_server(bridge: Any | None = None) -> Any:
    # Keep the validator/format/CLI usable when the optional SDK is absent.
    from mcp.server import Server
    from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool, ToolAnnotations

    schema = get_plan_format()["schema"]
    # Embedded $refs in tool schemas resolve at the tool-schema root.
    proposal_shape = {key: value for key, value in schema.items() if key not in {"$schema", "$id", "$defs"}}
    annotations = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
    tools = [
        Tool(
            name="get_plan_format",
            description="Get the habits academic-plan JSON format and example. No app data is read or changed.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            output_schema={
                "type": "object", "properties": {
                    "status": {"const": "proposal_only"}, "schema": {"type": "object"}, "example": proposal_shape,
                    "maxBytes": {"const": MAX_BYTES}, "semanticRules": {"type": "array", "items": {"type": "string"}},
                    "message": {"type": "string"},
                }, "required": ["status", "schema", "example", "maxBytes", "semanticRules", "message"],
                "additionalProperties": False, "$defs": schema["$defs"],
            },
            annotations=annotations,
        ),
        Tool(
            name="propose_academic_plan",
            description="Validate a user-sourced academic plan and return a portable proposal. Never applies or syncs it; the user reviews and confirms import in the app. Do not invent missing dates.",
            input_schema={
                "type": "object", "properties": {"proposal": proposal_shape}, "required": ["proposal"],
                "additionalProperties": False, "$defs": schema["$defs"],
            },
            output_schema={
                "type": "object", "properties": {
                    "status": {"const": "proposal_only"}, "proposal": proposal_shape, "message": {"type": "string"},
                }, "required": ["status", "proposal", "message"], "additionalProperties": False, "$defs": schema["$defs"],
            },
            annotations=annotations,
        ),
    ]
    if bridge is not None:
        readonly = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
        mutable = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
        target = {"targetDeviceID": schema["$defs"]["uuid"]}

        def arguments(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
            return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}

        descriptions = {
            "desktop_status": "Inspect this local desktop bridge and paired phone identifiers. No phone application is implied by desktop readiness.",
            "create_pairing_invite": "Create a private single-use 10-minute phone invite with the local certificate fingerprint. Replaces any previous unconsumed invite. Transfer it directly to your phone; this does not pair or apply data by itself.",
            "get_shared_academic_context": "Read only the explicitly shared academic snapshot from a paired phone. Snapshot may be stale. Omitted assessment state is unknown, never pending; avoid preparation for recorded completed/dismissed work unless explicitly requested. Calendar coverage is unknown even for empty/terminal-only snapshots. No task or habit histories are available.",
            "stage_academic_plan": "Validate and stage an immutable academic proposal for one paired phone to fetch and review. This does not apply it. Report pending until get_plan_receipt returns the phone's applied receipt. Supply targetDeviceID when multiple phones are paired.",
            "get_plan_receipt": "Read the paired phone's receipt for a staged proposal. Distinguish pending, applied, undone, rejected, and not_found literally; never infer application from staging.",
            "revoke_device": "Revoke a lost/offline phone's bridge credential by deviceID, preserving all academic snapshots and proposal history. The phone must pair again with a fresh invite to restore access.",
        }
        inputs = {
            "desktop_status": arguments({}), "create_pairing_invite": arguments({}),
            "get_shared_academic_context": arguments(target),
            "stage_academic_plan": {**arguments({"proposal": proposal_shape, **target}, ["proposal"]), "$defs": schema["$defs"]},
            "get_plan_receipt": arguments({"proposalID": schema["$defs"]["uuid"], **target}, ["proposalID"]),
            "revoke_device": arguments({"deviceID": schema["$defs"]["uuid"]}, ["deviceID"]),
        }
        for name, description in descriptions.items():
            hint = readonly if name in {"desktop_status", "get_shared_academic_context", "get_plan_receipt"} else mutable
            if name == "create_pairing_invite":
                hint = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
            tools.append(Tool(name=name, description=description, input_schema=inputs[name], output_schema={"type": "object", "properties": {"status": {"type": "string"}, "message": {"type": "string"}}, "required": ["status", "message"]}, annotations=hint))

    async def list_tools(ctx: Any, params: Any) -> Any:
        return ListToolsResult(tools=tools)

    async def call_tool(ctx: Any, params: Any) -> Any:
        try:
            arguments = {} if params.arguments is None else params.arguments
            if params.name == "get_plan_format":
                if arguments != {}:
                    raise PlanValidationError("arguments: get_plan_format accepts no arguments")
                result = get_plan_format()
            elif params.name == "propose_academic_plan":
                if type(arguments) is not dict or set(arguments) != {"proposal"}:
                    raise PlanValidationError("arguments: exactly one proposal object is required")
                result = propose_academic_plan(arguments["proposal"])
            elif bridge is not None and params.name in descriptions:
                from .validator import _object
                required = set(inputs[params.name]["required"])
                optional = set(inputs[params.name]["properties"]) - required
                _object(arguments, "arguments", required, optional)
                target_id = arguments.get("targetDeviceID")
                if "targetDeviceID" in arguments and target_id is None:
                    raise PlanValidationError("targetDeviceID: null is not permitted")
                if params.name == "desktop_status":
                    result = bridge.desktop_status()
                elif params.name == "create_pairing_invite":
                    result = bridge.create_pairing_invite()
                elif params.name == "get_shared_academic_context":
                    result = bridge.get_shared_academic_context(target_id)
                elif params.name == "stage_academic_plan":
                    result = bridge.stage_academic_plan(arguments["proposal"], target_id)
                elif params.name == "revoke_device":
                    result = bridge.revoke_device(arguments["deviceID"])
                else:
                    result = bridge.get_plan_receipt(arguments["proposalID"], target_id)
            else:
                raise PlanValidationError("tool: unsupported tool; no action was taken")
        except PlanValidationError as error:
            return CallToolResult(content=[TextContent(type="text", text=f"Proposal rejected. {error}. No changes were applied.")], is_error=True)
        except ValueError as error:
            # Bridge errors describe local staging/auth state; never claim app writes.
            return CallToolResult(content=[TextContent(type="text", text=f"Desktop operation rejected. {error}. No app application was performed by this tool.")], is_error=True)
        # Text JSON is intentional compatibility for hosts that ignore structuredContent.
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))], structured_content=result)

    return Server(
        "Habits academic proposals", version="1.0.0", on_list_tools=list_tools, on_call_tool=call_tool,
        instructions="This server validates portable academic-plan proposals and, when explicitly enabled, stages them for a private paired phone. Staging is pending until a phone receipt reports applied. It cannot directly apply phone changes. Shared academic context is a possibly stale user-approved snapshot. Omitted assessment state is unknown, never pending; do not schedule preparation for recorded completed/dismissed assessments unless explicitly requested. Calendar coverage remains unknown for empty/terminal-only snapshots. Never invent dates or claim pending means applied.",
    )


async def run_stdio(bridge: Any | None = None) -> None:
    from mcp.server.stdio import stdio_server

    server = build_server(bridge)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def http_app() -> Any:
    from mcp.server.transport_security import TransportSecuritySettings

    return build_server().streamable_http_app(
        host="127.0.0.1", stateless_http=True, json_response=True,
        max_request_body_size=MAX_BYTES + 64 * 1024,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*"],
        ),
    )


def run_http(port: int) -> None:
    import uvicorn

    # A deliberately fixed loopback bind; no external-host CLI or environment switch.
    uvicorn.run(http_app(), host="127.0.0.1", port=port, log_level="warning")
