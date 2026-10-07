# Semester Board Python and package checks — 6 October 2026

The 0.2.0 source adds a separate Semester Board merge proposal, explicit Android capabilities, a separate consented board context and inbox. Existing academic-plan version-1 field sets, pairing, HMAC/pinning/nonce checks and receipt shapes remain in place. These checks preceded publication and changed no installed personal Codex/Claude configuration.

The combined canonical Python suite passed **82 tests, zero failures/errors/skips, in 18.900 seconds** using isolated Python 3.12 with `mcp==2.2.0`, `cryptography==50.0.2`, `jsonschema==4.26.0`, `build==1.6.1`, `setuptools==82.0.1` and `wheel==0.48.0`. The test-built universal `habits_desktop_mcp-0.2.0-py3-none-any.whl` installed in a fresh temporary environment without fetching dependencies; plan and board schemas/examples loaded there, and the console reported 0.2.0.

Coverage includes unchanged v1 proposal validation/tools, real stdio and loopback MCP initialization, strict board fields/types/bounds, absent versus null fields, unknown course schedules, twenty-status bounds, hidden week52, source-scoped legacy assessment IDs, IANA/DST/date-range validation, numeric Foundation timestamps, separately disclosed notes/week progress, context privacy, Android capability targeting, isolated old/new queues, schema-1 desktop storage migration, persisted nonces/credentials, immutable proposal IDs, offline/delayed Undo receipts and replay after changed context/restart. A commit-time capability check refuses an advertisement removed during staging.

Temporary TLS/HTTP listeners used loopback, synthetic invitations/credentials and disposable directories; each listener/server process was closed by test cleanup. A dependency-install DNS denial and an initial TLS bind denial were sandbox restrictions, followed by explicitly authorized isolated-dependency/test execution. One earlier package test was skipped until a wheel was supplied; the final canonical suite had no skips. An upstream SDK resource warning named an already closed socket during an older loopback fixture, without a test failure or retained service.

The synthetic board example normalizes to SHA-256 `4b534684d01d4d67b99b78f33ccdda45eeeaf453156e61a1eaea9321a80e890b` over 1,246 UTF-8 bytes. Canonicalization lowercases known UUIDs and sorts object keys only; source array order, nullable clears and optional omissions remain significant. This is a fixture vector, not a real user's board digest.

The standalone-checkout discovery run separately executed 82 tests in 15.029 seconds: 81 passed and one clean-wheel test skipped because its wheel environment variable was absent. It had no dependency skips; that same clean-wheel check passed in the canonical run above.

Run the standalone source regressions from the installed source checkout:

```sh
python -m unittest discover -s tests -v
```

Provide `HABITS_MCP_WHEEL` with the locally built universal wheel to execute the clean-install check rather than skip it. The source-release whitelist includes the Python tests and this public summary while excluding keys, TLS certificates, SQLite/runtime files, virtual environments and build output.

These checks establish Python validation, server transport, resources and package behavior. Android source migration/merge, preview/confirmation, guarded local Undo, real phone/private-LAN delivery, native app startup and visual acceptance remain separate checks. No long-lived listener or phone runtime was started for this verification. No project license was selected.

## Recorded DST clock alignment follow-up

A focused semantic review changed existing-digest proposals to defer DST instant validation, including when their explicit timezone is recognized. Imported raw gap/overlap clocks can remain unchanged while a title/state is edited. Dates, clock syntax, IANA identifiers and numeric state/timestamp rules remain strict; a new board still receives full DST validation. Structural acceptance or pending delivery never establishes phone application: the phone checks every new or date/time-changed row and timezone change against the effective merged source zone before confirmation.

The rebuilt package and canonical protocol suite passed **84 tests, zero failures/errors/skips, in 29.117 seconds**. Two new tests cover unchanged gap/overlap clock acceptance versus new-board rejection, consented context preservation, unchanged malformed date/clock/zone rejection and proposal-only wording. The prior 82-test result above remains a separate checkpoint. Temporary synthetic TLS/HTTP listeners were closed by cleanup. No phone/app runtime, global client configuration or publication occurred.
