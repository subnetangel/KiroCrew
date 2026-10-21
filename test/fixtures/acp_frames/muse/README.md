# muse frame corpus

Four files, all live. Read `../README.md` first for what a fixture is and what the
corpus does and does not prove.

Captured off the Muse Code fork adapter (`@bex-co/muse-code-acp` 0.10.0-kirocrew.1,
branch `feat/kirocrew-seams`) driven over stdio against **Muse Code 1.4.11
(1.4.11-R6667.1)**, the signed-in host, with `MUSE_CODE_EXECUTABLE` pointed at it.
The model answered from a live account, so the agent prose and tool output are real.

`muse` is a DORMANT backend on this branch: it is in `ACP_BACKENDS_KNOWN` but not in
`BASELINE_SELECTABLE_BACKENDS`, so `test_every_known_backend_has_fixtures` treats this
directory as the one a verification run produces rather than a stray. The corpus still
has to reach all seven required frame classes, and it does.

| File | Frame classes it carries |
|---|---|
| `handshake-live.jsonl` | `initialize` result with `protocolVersion` and `agentInfo.version`, the `session/new` result with a `sessionId` whose `configOptions` read `nativeApprovalPolicy.currentValue` back as `promptUnmatched`, and the `session/load` answer for an absent id (`-32602 Invalid params`, this host's shape — NOT `-32002`) |
| `turn-live.jsonl` | `session/new` result, `session_info_update` (the adapter's `muse/hostCompatibility` `_meta`), `usage_update`, `agent_message_chunk`, `tool_call`, `session/request_permission`, `tool_call_update` (`in_progress` then `completed`), and the `session/prompt` result carrying `stopReason` — the ALLOW path (`allow_once`), so the command runs and completes |
| `permission-deny-live.jsonl` | the same shell turn where the client answers `abort` (`reject_once`): `session/request_permission`, then the `failed` `tool_call` / `tool_call_update` for that call and the `stopReason` result — no `completed` execute update follows, so the deny stops the command |
| `mcp-stdio-mount-live.jsonl` | a stdio MCP mount completing a ROUND TRIP: `tool_call` / `tool_call_update` (`completed`) for `mcp__crew_probe__crew_probe_echo`, carrying the server's fixed marker |

**Nothing here is synthesized.** The adapter produced all seven required classes on its
own wire, including the permission request, so there is no class written from a declared
shape.

## What the permission capture establishes

The approval route is settled in the `session/new` response and read back there in the
SAME response. The client sends `initialize` with
`clientCapabilities._meta["muse/approval"] = 1` (which is what makes the adapter
advertise `muse/approval.seedAtSessionNew: true` in its `initialize` result), then
`session/new` with `_meta {"muse/approval": {"policy": "promptUnmatched"}}`. The
`session/new` result carries `configOptions[id=nativeApprovalPolicy].currentValue ==
"promptUnmatched"` — the seed and its read-back in one response, with no separate
`set_config_option` round trip. So the route is in force before the first prompt is
sent, and confirming it needs no second session.

`session/request_permission` then fires for a builtin shell command (`turn-live.jsonl`
and `permission-deny-live.jsonl`). This host offers TWO option kinds, not goose's four:

- `allow_once` (kind `allow_once`, `_meta.museDecision: approved`)
- `abort` (kind `reject_once`, `_meta.museDecision: abort`)

Answering `allow_once` runs the command to a `completed` `tool_call_update` with its
real stdout. Answering `abort` leaves the call `failed` with `tool denied: approval
aborted` and no `completed` execute update — the deny stops the command.

## What the MCP capture establishes, and the fact the parent needs

`mcp-stdio-mount-live.jsonl` passes a dependency-free stdio MCP server (one tool,
`crew_probe_echo`, returning a fixed marker) in the `session/new` `mcpServers` array.
`session/new` accepts the element, the model calls the tool, and the result carries
`crew-stdio-mount-proved` — so the transport is mounted and the tool is REACHABLE, not
merely that an element was accepted. The tool grammar on this host is
`mcp__<server>__<tool>` (an `mcp__` prefix and DOUBLE underscores), and the server name
`crew-probe` is normalized to `crew_probe` in the tool id.

**The adapter did NOT draw a `session/request_permission` for the MCP tool call.** Under
the seeded `promptUnmatched` policy it auto-approved it, even though it drew a real
permission for the builtin shell command in the same policy (`turn-live.jsonl`). So on
1.4.11 the host gate covers the builtin shell tool but not an mcpServers-mounted MCP
tool.

## What the corpus does NOT establish

- `session/list`: omitted. This host's `session/list` result enumerates prior sessions
  with their real cwds and prompt titles — recording-host data a corpus must not carry —
  and no required class needs it. `initialize` advertises `sessionCapabilities.list`, so
  the method exists; its result shape is not pinned here.
- `session/load` / `session/resume` for a LIVE id, and mode persistence across
  processes: not captured. Only the absent-id `session/load` rejection is here.
- compaction: not captured; no `_kiro.dev/*`-style compaction frame family exists on
  this host and none was driven.
- Whether a NON-`promptUnmatched` native policy (`denyUnmatched`, `allowAll`) is
  enforced by the host: not probed. The handshake notes 1.2.1+ enforcement is required
  but the fixtures only exercise `promptUnmatched`.

## Reductions

A live frame is host data until proved otherwise, so each reduction is named here and in
the file's own `_meta.note`:

- Session ids → `muse-session-1`; the permission request id → `perm-1`; every
  turn/item/approval uuid (`museApprovalId`, `museTurnId`, `museItemId`, …) → a fixed
  synthetic `muse-id-N`; every `call_<hex>` tool-call id → a fixed synthetic
  `muse-call-N`. These are the two marker classes minted by the agent rather than read
  from the host, so they are normalized AT CAPTURE in the capture script's reduction
  step, not hand-edited.
- The recording home directory and the scratch cwd → `~`.
- The model catalog on `session/new` is one option (`muse-spark-1.2`) exactly as the
  host returned it — the execution host had not enumerated the fuller catalog at
  `session/new` time, so no catalog pruning was needed.
- Longer turns (30–100 frames) are sliced to the required-class carriers; nothing within
  a kept frame is otherwise altered, and the kept frames stay in wire order.

Every file passes `scripts/check_acp_frame_host_data.py` with no new baseline entry.
