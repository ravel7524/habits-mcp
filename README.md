# Habits Desktop MCP

Habits Desktop MCP **0.2.0 prerelease** prepares typed academic proposals for phone review and confirmation. It retains `habits.academic-plan` version 1 and adds the separate `habits.semester-board-proposal` version 1 contract. The default stateless server exposes four tools:

- `get_plan_format` accepts `{}` and returns the canonical schema, a fictional two-course example, semantic rules, and `status: "proposal_only"`.
- `propose_academic_plan` accepts `{"proposal": <academic-plan object>}` and returns `{"status":"proposal_only","proposal":<validated object>,"message":...}`. Its MCP response includes both structured data and text JSON for hosts that consume only text content.
- `get_semester_board_format` returns the strict board schema, fictional example and merge/privacy rules.
- `propose_semester_board` validates a board merge proposal and returns `status: "proposal_only"`; existing-board edits need the phone-shared digest.

These proposal tools do not apply a plan, reads or writes the app database, touches CloudKit, schedules notifications, or calls an inference API. No inference API key or hosted planner account is needed. The stateless mode keeps no proposal state. Its tools carry read-only, non-destructive, idempotent, closed-world hints; the implementation has no apply tool.

The optional private phone bridge stores only paired-device credentials, staged academic proposals, phone receipts, and the phone's explicitly shared academic snapshot. Codex or Claude Desktop talks to the MCP over local stdio. The phone talks to the desktop over private TLS and reviews proposals before applying them. Staging means **pending**, never applied.

## Download and run locally

The source repository is [ravel7524/habits-mcp](https://github.com/ravel7524/habits-mcp). Download the [v0.2.0 prerelease](https://github.com/ravel7524/habits-mcp/releases/tag/v0.2.0):

- [Python wheel](https://github.com/ravel7524/habits-mcp/releases/download/v0.2.0/habits_desktop_mcp-0.2.0-py3-none-any.whl)
- [Standalone source ZIP](https://github.com/ravel7524/habits-mcp/releases/download/v0.2.0/habits-desktop-mcp-0.2.0-source.zip)
- [SHA256SUMS](https://github.com/ravel7524/habits-mcp/releases/download/v0.2.0/SHA256SUMS)
- [Release manifest](https://github.com/ravel7524/habits-mcp/releases/download/v0.2.0/release-manifest.json)

A **compatible Habits phone build is required** for pairing and review. It must implement `habits.desktop-pairing` version 1, the private TLS/HMAC routes documented below, and `habits.academic-plan` version 1 with explicit user-confirmed import and receipts. This repository distributes the desktop MCP only; it contains no iOS/Android application code or phone installers. The MCP cannot add phone features to an older app build. Semester Board delivery requires a capability-advertising Android build such as [Habits Android 0.5.0](https://github.com/ravel7524/habits-android/releases/tag/v0.5.0). Existing iPhone and older compatible Android clients keep the unchanged academic-plan v1 route.

Install the prepared wheel into your own virtual environment, or unpack the source ZIP and install that folder:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install https://github.com/ravel7524/habits-mcp/releases/download/v0.2.0/habits_desktop_mcp-0.2.0-py3-none-any.whl
```

The package pins `mcp==2.2.0` and `cryptography==50.0.2`. Installing dependencies may download Python packages; the running bridge requires no hosted application account, OAuth, or model API key.

To upgrade an existing installation, stop its running MCP process, install the 0.2.0 wheel in the same dedicated environment, then restart your client. Keep the existing private data directory and TLS identity. The store migration preserves devices, credentials, nonces and old proposal/receipt history; a 0.1 server cannot open the upgraded store. No client configuration is changed automatically.

Run `habits-mcp` alone for the four stateless proposal tools (two original plan tools plus two separate board tools). Enable phone access explicitly:

```sh
.venv/bin/habits-mcp --phone-bind 192.168.1.50 --phone-port 8766
```

Replace the private IP with an address actually assigned to your desktop. The bridge refuses wildcard/public addresses and listens only on the chosen private IP. The desktop and phone must be able to reach one another on that private network; no tunnel, discovery, public endpoint, or firewall change is installed. For local testing, `--phone-bridge` enables the default `127.0.0.1` bind. A real phone cannot reach the desktop through its own loopback address.

[client-configs/codex.example.toml](client-configs/codex.example.toml) and [client-configs/claude-desktop.example.json](client-configs/claude-desktop.example.json) contain example stdio configurations. Replace their executable/private-IP placeholders and merge the entry into your existing personal configuration; preserve other servers. This implementation never edits global client settings.

For Codex CLI, the documented registration syntax is:

```sh
codex mcp add habits -- /ABSOLUTE/PATH/TO/.venv/bin/habits-mcp --phone-bind 192.168.1.50 --phone-port 8766
```

Alternatively use the TOML example in `~/.codex/config.toml`, then restart the client and inspect `/mcp`. See [official Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

For Claude Desktop, open its Developer settings and Edit Config, merge the example's `habits` entry under `mcpServers`, then restart Claude Desktop. See the [official local-server configuration guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers) and [Claude local MCP guidance](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop). This package is a Python wheel/source release, not a one-click `.mcpb` extension.

Hosted ChatGPT/Claude web chats do not launch this local stdio server. Use a local MCP-capable client. Only one process can own a listening address/port; do not configure two simultaneously running clients to start separate phone listeners on the same port. The examples use macOS/Linux executable paths; Windows virtual environments use `Scripts/habits-mcp.exe`.

Use a dedicated state directory with `--data-dir` if needed. Defaults are `~/Library/Application Support/Habits Desktop MCP` on macOS, `$XDG_DATA_HOME/habits-desktop-mcp` (or `~/.local/share/...`) on Linux, and `%LOCALAPPDATA%/Habits Desktop MCP` on Windows. The directory contains `bridge.sqlite3`, `desktop-key.pem`, `desktop-cert.pem`, and a temporary `pairing-invite.json`. POSIX directory/file modes are 0700/0600. The bridge refuses symlinks and directories containing unrelated files; it does not modify or delete app storage. Windows inherits the user's filesystem ACLs; POSIX permission enforcement and actual runtime verification here are macOS-specific.

## Pair, share context, and stage plans

With the private bridge enabled, the MCP adds:

| Tool | Purpose |
|---|---|
| `desktop_status` | Show the private endpoint, certificate fingerprint, paired device IDs, and pending count. Readiness does not imply phone connectivity. |
| `create_pairing_invite` | Create one single-use invite, valid for 10 minutes, replacing any previous unused invite. Returns the invite, private file path, and expiry. |
| `get_shared_academic_context` | Read the paired phone's explicitly shared academic snapshot and received time. No live database access or task/habit histories. |
| `stage_academic_plan` | Validate and persist immutable proposal content for one paired phone. Returns pending until a phone receipt arrives. |
| `get_plan_receipt` | Read pending/applied/undone/rejected/not_found literally. Applied is a paired-phone report. |
| `revoke_device` | Revoke a lost/offline phone's credential by device ID, preserving all stored academic context and proposal/receipt history. |
| `stage_semester_board` | Stage a merge-only board proposal for an explicitly named capable Android phone; confirmation remains on the phone. |
| `get_shared_semester_board_context` | Read that phone's explicitly selected board snapshot with disclosed notes/week-progress scope. |

Transfer the complete pairing invite directly to your own phone, for example as a file. The invite contains the private HTTPS URL, a 32-character base64url pairing code (192 bits of randomness), and the SHA-256 fingerprint of the desktop certificate's DER bytes. The phone verifies the fingerprint before sending the code. The server consumes the code atomically and returns a separate random 32-byte per-device secret, encoded as 64 hex characters. Codes expire after 10 minutes and cannot be reused. Re-pairing the same device ID rotates its credential without deleting history.

Share one selected semester from the phone explicitly. The desktop receives at most one semester, ten courses, and 200 existing assessments including their notes and recorded state when available. Unknown start dates/time zones and assessment states remain absent. Omitted assessment state means unknown, never pending; the assistant must avoid preparation for recorded completed/dismissed work unless explicitly requested. The MCP labels this as a snapshot, discloses missing metadata, and must ask for missing facts before proposing dates. Snapshots may be stale; calendar coverage is explicitly unknown, including empty or terminal-only snapshots. Nothing is inferred about attendance, free time, tasks, or habits.

The assistant then calls `stage_academic_plan` with `{proposal: <plan>, targetDeviceID?: <UUID>}`. Exactly one active paired device is required when the target is omitted; multiple devices require an explicit target. The phone fetches pending plans and shows its own import preview. The user confirms application or rejects the proposal. No server tool bypasses that phone review.

Proposal UUIDs identify immutable content. Before storing/hashing, all proposal/entity/reference UUID strings are normalized to lowercase; other values remain literal. The digest is SHA-256 of sorted-key, compact UTF-8 JSON with Unicode and slashes unescaped. Repeating the same UUID/content/target is idempotent. A changed payload under the same proposal UUID is rejected. The phone treats the envelope digest as an opaque version token and echoes it in receipts.

Pending receipts can become applied, rejected, or undone. Applied can become undone. An authenticated undone receipt is accepted even when the phone applied and rolled back while the desktop was offline before an applied ACK arrived. A delayed applied ACK never replaces undone. Repeated receipt states are idempotent; rejected/undone content is not requeued under the same UUID. Use a fresh proposal UUID for new work. If the phone disconnects locally while the desktop is offline, remote revocation cannot be guaranteed; use `revoke_device` on the desktop.

## Private phone REST contract

The private listener uses an EC P-256 certificate with a one-year lifetime. SANs cover its bound private IP, localhost, loopback, and `10.0.2.2` for Android emulator testing. The invite advertises only the actual configured bind URL. A saved identity is preserved across restarts. A changed bind IP outside its SANs, mismatched key/certificate, or expired certificate fails startup rather than silently changing the phone's trust anchor; existing state is preserved.

All bodies are UTF-8 JSON. Unknown fields, malformed civil values, duplicate JSON keys, and unexpected null metadata are rejected. Context uploads and proposal-response batches are bounded to 2 MiB. Pair/receipt/disconnect requests are limited to 8 KiB. GET batches include at most 20 pending proposals; acknowledged rows leave the queue so later fetches expose the remaining plans. An individual proposal whose delivery envelope cannot fit 2 MiB is rejected during staging. Each phone can have at most 200 pending proposals.

| Method/path | Request | Success response |
|---|---|---|
| `POST /v1/assistant/pair` | `{pairingCode,deviceID,deviceName}` | `{deviceID,deviceSecret}` |
| `GET /v1/assistant/proposals` | Empty body, authenticated | `{proposals:[{proposal,proposalDigest,status:"pending"}]}` |
| `POST /v1/assistant/receipt` | `{proposalID,proposalDigest,state:"applied"\|"undone"\|"rejected",detail?}` | Current receipt: `{status,proposalID,proposalDigest,targetDeviceID,detail,updatedAt,message}` |
| `POST /v1/assistant/context` | The context object below | `{status:"context_shared",deviceID,capturedAt}` |
| `POST /v1/assistant/disconnect` | Authenticated `{}` | `{status:"disconnected",deviceID}` |

```json
{
  "format": "habits.academic-context",
  "version": 1,
  "capturedAt": "2026-10-02T12:00:00.123456789Z",
  "semesters": [{
    "id": "22222222-2222-4222-8222-222222222222",
    "title": "Autumn 2026",
    "weekCount": 14,
    "courses": [],
    "existingAssessments": []
  }]
}
```

Semester `startDate` and `timeZoneIdentifier` are optional, omitted when unknown. Courses have `{id,name}`; existing assessments have `{id,courseID,title,kind,dueDate,dueTime?,notes,state?}`. Optional state must be the actual recorded `pending`, `completed`, or `dismissed` value; omit unknown state without assuming pending. This optional field preserves version-1 compatibility. Timestamp fractions from zero through nine digits are accepted. The whole snapshot is rejected when it exceeds its limits; it is never silently truncated.

Every route except pairing requires these four headers:

```text
X-Habits-Device: <paired device UUID>
X-Habits-Timestamp: <Unix seconds, within +/-120 seconds>
X-Habits-Nonce: <fresh UUID>
X-Habits-Signature: <64 hex characters>
```

Decode the device secret from hex to its 32 binary bytes. Sign HMAC-SHA256 over exactly:

```text
METHOD\nPATH\nTIMESTAMP\nNONCE\nSHA256(raw_request_body_bytes)
```

The method is uppercase, path has no query, and timestamp/nonce are the literal header strings. GET uses the SHA-256 of an empty body. Successful authentication consumes the nonce persistently; retries must use a fresh nonce, even after validation errors. Signatures, timestamp windows, active credentials, per-device queues, and immutable receipt digests are checked independently. No global TLS bypass or trust-store modification is used.

Fixed vector: secret bytes `00` through `1f`, POST path `/v1/assistant/disconnect`, timestamp `1790942400`, nonce `aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa`, raw body `{}` produces `9b6e4f1225d31dcc86399727fa99fc3fe9d5140b6e8fc1bf94c1609c2dee4381`.


## Semester Board proposals in 0.2

Version 0.2 adds `get_semester_board_format` and `propose_semester_board` in stateless mode, plus `stage_semester_board` and `get_shared_semester_board_context` with the bridge enabled: four default tools or twelve with the private bridge. All original eight tools and academic-plan version-1 schemas/routes remain available. Install the updated desktop package explicitly; phone features and personal client settings are not upgraded automatically.

The separate [proposal schema](semester-board-proposal.schema.json) accepts:

```json
{"format":"habits.semester-board-proposal","version":1,"proposalID":"<UUID>","source":"<source label>","expectedBoardDigest":"<optional phone-shared digest>","semester":{"schemaVersion":3,"id":"<UUID>","title":"<title>","weekCount":14,"statuses":[],"courses":[]}}
```

The phone merges additions and explicitly supplied fields only. Omitted courses, statuses, weeks and assessments survive; absent optional fields never clear existing notes, state, codes or schedules. Existing semester edits require `expectedBoardDigest` from this target phone's explicitly selected shared board context. New semesters require absence at phone preview/apply. The phone shows changed fields, rejects stale/conflicted/archived identities, commits its receipt with local data and owns guarded Undo; desktop staging remains pending.

Bounds are 2 MiB UTF-8, ten courses, twenty named status definitions with `#RRGGBB` colors, week keys/active weeks 1 through 52, and 200 assessments per proposal. Course `activeWeeks` is optional in the assistant/context DTO: omission means no recorded schedule, while `[]` explicitly means no active weeks. This differs from genuine raw iOS files, where `activeWeeks` is required. Notes are literal and bounded to 4,000 code points. Start/end dates are paired or both absent, and a supplied range must agree with teaching week count; hidden weeks remain valid. New-board timed assessments need a recognized source IANA timezone and exact, unambiguous DST resolution. Existing-digest proposals retain strict lexical dates/clocks and IANA identifiers but defer instant validation, preserving unchanged raw DST gap/overlap source clocks. Structural validation/staging is not phone application: the phone checks every new or date/time-changed row and source-zone change against the effective merged timezone before confirmation.

UUIDs are scoped by type/course. A legacy assessment may retain its course UUID; duplicate assessment UUIDs in different courses are distinct source coordinates. Only known UUID spellings normalize to lowercase; array order, omitted keys, explicit nulls and literal notes survive canonical hashing. The full selected-board digest is opaque change protection, not write authorization.

Assessment state is recorded `pending`, `completed` or `dismissed`. `completedAt` is a finite numeric Foundation date: seconds since 2001-01-01 UTC, not Unix seconds or an ISO string. Unknown historical completion timestamps stay omitted. Nullable field clears are explicit: paired semester dates, timezone, module code, source URL, schedule note, week status ID, assessment clock and completion timestamp. An empty notes string is an explicit note clear. Unknown fields, legacy exam fields, recurrence edits, `reminderDays` and `snoozedUntil` are forbidden in this assistant DTO; raw iOS file compatibility is a separate phone codec. Neither this proposal nor connection enables sync, reminders or automatic application.

Pairing invitation/request/response remain version 1. New Android clients register support after explicit pairing/manual refresh using authenticated `POST /v1/assistant/capabilities`:

```json
{"format":"habits.desktop-capabilities","version":1,"platform":"android","proposalFormats":[{"format":"habits.academic-plan","version":1},{"format":"habits.semester-board-proposal","version":1}]}
```

Success is `{status:"capabilities_registered",deviceID,platform,proposalFormats}`. A legacy desktop's HTTP 404 is a nonfatal version-1 fallback; upgrade the installed package explicitly to use boards. Unadvertised devices default to academic-plan v1 only, and credential rotation clears capability advertisements. Board staging requires explicit `targetDeviceID` and this advertised Android capability. Old iPhones never receive board payloads through their strict v1 inbox.

| New authenticated route | Body/result |
| --- | --- |
| `POST /v1/assistant/capabilities` | The capabilities object above; exact response fields above. |
| `GET /v1/assistant/board-proposals` | `{proposals:[{proposal,proposalDigest,status:"pending"}]}`; separate from unchanged `/proposals`. |
| `POST /v1/assistant/board-context` | The [board context schema](semester-board-context.schema.json); returns `{status:"context_shared",deviceID,capturedAt}`. |

Board context has `{format:"habits.semester-board-context",version:1,capturedAt,boardDigest,notesIncluded,weekProgressIncluded,semester}` and contains exactly one explicitly selected board. Week progress and notes require separately disclosed consent. When notes are excluded, omit assessment/entry notes and course schedule notes; when week progress is excluded, entry maps are empty. Source URLs, opaque compatibility fields, recurrence/device settings, planner/preparation history, habits, profile, Calendar and credentials are excluded. Existing academic-context v1 bytes remain unchanged and are stored independently. Context may be stale and never establishes attendance, mastery, free time or time worked.

Both formats reuse the unchanged receipt endpoint and applied/undone/rejected transitions. Durable SQLite storage upgrades version 1 to 2, preserving devices, secrets, TLS identity, accepted nonces, staged v1 content and receipts. Version-0.1 servers fail closed on the upgraded store instead of attempting to deliver unfamiliar board rows; keep a preserved backup if a server downgrade is needed. The server only verifies that an expected digest came from that target's shared snapshot; the phone rechecks actual source freshness before confirmation. Consumed replay remains idempotent even after a newer shared snapshot or empty inbox.

Validate the synthetic [board example](examples/semester-board-proposal.json) without an MCP connection:

```sh
.venv/bin/python -m habits_mcp validate-board examples/semester-board-proposal.json
```

## Contract and validation

[academic-plan.schema.json](academic-plan.schema.json) is the structural contract. [examples/two-course-exams.json](examples/two-course-exams.json) is fictional test data, not a real university schedule. UUIDs are user-proposal identities; the Swift importer validates existing identity collisions and shows a preview before changing app data.

The standard-library validator enforces the following semantic constraints beyond JSON Schema:

- Maximum 2 MiB UTF-8 JSON, 10 courses (the existing `AcademicCodec` bound), 200 assessments, and 200 preparation tasks. Teaching weeks must be 1 through 52; estimated work is 0 through 1,440 minutes.
- Only known fields at every object level; all required fields supplied. Raw JSON duplicate keys, invalid Unicode, non-finite numbers, boolean-as-integer values, and unsupported versions are rejected.
- Source is nonempty and at most 300 Unicode code points. Titles and course names are nonempty and at most 200. These strings must already be trimmed. Notes are preserved literally with a 4,000-code-point bound. Unicode control/format characters (Cc/Cf) are rejected; notes permit tab, newline, and carriage return.
- Hyphenated UUIDs are globally unique across proposal, semester, course, assessment, and preparation-task identities. References resolve within the same proposal; UUID comparisons are case insensitive.
- Duplicate assessments with the same course, case-sensitive title, kind, date, and optional time are rejected even when their UUIDs differ. Unicode canonically equivalent titles compare equal, matching Swift string equality. Distinct dates/times/kinds remain distinct assessments.
- Gregorian `YYYY-MM-DD` dates, optional 24-hour `HH:mm` times, and a portable IANA semester time zone. Use `UTC` or recognized slash identifiers/aliases such as `Europe/Zurich`, `Etc/UTC`, or `US/Eastern`; abbreviations such as `CET` are rejected. Impossible or ambiguous local times, including DST gaps/overlaps, are rejected. Date-only preparation tasks also validate local midnight because the app uses it as the scheduled date. The host needs an IANA time-zone database; unavailable zones fail validation.
- Every assessment needs a source-supported date. Unknown exam times and preparation dates stay absent. A task cannot supply a time without a date. No date, time, estimate, or source provenance is invented by the validator.
- Preparation deadlines must precede or equal the linked assessment deadline. For comparison only, a date without a time means local 23:59:59; no time is inserted into the proposal. Consequently, an untimed preparation task on the day of a 09:00 exam is rejected. Use an earlier date, a supported explicit time, or an undated task.

The adapter validates the supplied structure and chronology. It cannot establish that an exam date, preparation estimate, or source claim is factually correct. Review these in the app before import. This academic contract contains no appointments, general tasks, or preference customization; those would need separately defined contracts.

## Validation and developer commands

The validator itself uses only Python's standard library and the host's IANA time-zone database. After installing this package, validate the fictional example:

```sh
.venv/bin/python -m habits_mcp validate examples/two-course-exams.json
```

For the original stateless proposal tools over stdio:

```sh
.venv/bin/python -m habits_mcp stdio
```

For development Streamable HTTP:

```sh
.venv/bin/python -m habits_mcp http --port 8765
```

That endpoint is `http://127.0.0.1:8765/mcp`: unauthenticated, stateless, loopback-only development. The separately enabled phone bridge always uses private TLS and per-device HMAC. No hosted application account or OAuth service is involved. `requirements.txt` pins the official `mcp==2.2.0` SDK and `cryptography==50.0.2` certificate dependency.

## Build release artifacts

From a source checkout with an isolated build environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install build==1.6.1 setuptools==82.0.1 wheel==0.48.0
.venv/bin/python -m build --no-isolation --wheel --outdir release .
.venv/bin/python release.py release/habits_desktop_mcp-0.2.0-py3-none-any.whl
```

`release.py` uses a source-file whitelist. It excludes private keys, certificates, SQLite databases, pairing invites, virtual environments, build output, and cached bytecode, then writes the source ZIP, `SHA256SUMS`, and `release-manifest.json`. The helper does not upload anything.

## Prerelease verification and provenance

The 0.2 backend/package suite passed **84 automated checks**, zero failures/errors/skips, in 29.117 seconds. Tests used pinned `mcp==2.2.0` and `cryptography==50.0.2`, fictional data, disposable stores and temporary loopback/TLS listeners. They cover existing v1 tools/phone shapes; scoped board identities, omission/null and unknown-schedule preservation; strict schemas, consent and capabilities; isolated queues; v1-to-v2 store migration/history; HMAC, pinning, forgery/replay/revocation; immutable staging and delayed Undo receipts; real MCP stdio/HTTP tools; and a clean-wheel install/resource check. [Dated verification](docs/verification/semester-board-python-2026-10-06.md) retains the earlier 82-test checkpoint separately.

Publication documentation changes are kept separate from the tested runtime. The release wheel/source hashes are recorded in `SHA256SUMS` and `release-manifest.json`, and packaging excludes app/iPhone code, model weights, keys, certificates, runtime databases and personal configuration. A clean install/resource smoke verifies the publication wheel after its README metadata is rebuilt.

These Python checks do not establish physical-phone/private-LAN acceptance, Windows runtime, production readiness or a public-service security audit. The companion phone's preview, persistence and guarded Undo require their own app verification. No hosting, OAuth/account service or cross-platform sync is introduced.

The earlier published 0.1 checkpoint had 58 checks and reviewed source-extraction SHA-256 `d956ca40a5c7d2dc5b2b9b4b7eaccf8ae171590311ef994662ac1c9acacf1846`; it is historical provenance, not the 0.2 artifact hash. Retain existing private state when upgrading and consult the current manifest for exact release assets.

No license has been selected for this prerelease; no LICENSE file is included.

Official references: [Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli), [Claude local MCP guidance](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop), [local MCP configuration guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers), [official Python SDK](https://github.com/modelcontextprotocol/python-sdk).
