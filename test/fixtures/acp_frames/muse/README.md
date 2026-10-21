# muse frame corpus

Five files, all live. Read `../README.md` first for what a fixture is and what the
corpus does and does not prove.

Captured by `scripts/capture_muse_frames.py` off the Muse Code fork adapter
(`@bex-co/muse-code-acp` **0.10.0-kirocrew.2**, branch `feat/kirocrew-seams`) driven
over stdio against **Muse Code 1.4.11 (1.4.11-R6667.1)**, the signed-in host, with
`MUSE_CODE_EXECUTABLE` pointed at it. The model answered from a live account, so the
agent prose and tool output are real. The capture script sends Kiro Crew's **exact**
two-part approval seed, imported from `src/kiro_crew/acp/harness/muse.py` rather than
retyped, so the corpus cannot drift from what Crew puts on the wire.

`muse` is a backend in `ACP_BACKENDS_KNOWN` but not yet in
`BASELINE_SELECTABLE_BACKENDS`, so `test_every_known_backend_has_fixtures` treats this
directory as the one a verification run produces rather than a stray. **This corpus is
what the selectability flip rests on**: it proves on the wire that under the host
`:ask-me` profile a session-mounted MCP tool call draws a real
`session/request_permission` and that a deny fails the call. Flipping the pin is the
parent's integration step; the corpus does not flip it.

| File | Frame classes it carries |
|---|---|
| `handshake-live.jsonl` | `initialize` result with `protocolVersion` and `agentInfo.version`; the `session/new` result with a `sessionId` whose `configOptions` read BOTH `nativeApprovalPolicy.currentValue == "promptUnmatched"` AND `nativePermissionProfile.currentValue == ":ask-me"` back in the SAME response; and the `session/load` answer for an absent id (`-32602 Invalid params`, this host's shape — NOT `-32002`) |
| `turn-live.jsonl` | the builtin shell turn, ALLOW path. `session/new` result, `usage_update`, `agent_message_chunk`, `tool_call`, `session/request_permission`, `tool_call_update` (through `completed`), and the `session/prompt` result carrying `stopReason`. The sandboxed first `bash` attempt fails (`bwrap: Failed to make / slave`), the model retries with `require_escalated`, and THAT draws the permission request; `allow_once` runs it to a `completed` update with the command's real output — all seven required classes |
| `permission-deny-live.jsonl` | the same shell turn where the client answers `abort` (`reject_once`): `session/request_permission`, then the `failed` `tool_call` / `tool_call_update` for that call and the `stopReason` result — no `completed` execute update follows, so the deny stops the command |
| `mcp-stdio-mount-live.jsonl` | **THE PROOF (deny).** The MCP tool call `mcp__crew_probe__crew_probe_echo` drawing a `session/request_permission`, answered `abort` → `failed` `tool_call_update`, then `stopReason` — the deny stopped the MCP call |
| `mcp-stdio-allow-live.jsonl` | **THE PROOF (allow).** The same MCP tool call drawing the same `session/request_permission`, answered `allow_once` → `in_progress` then `completed` `tool_call_update` carrying the server's fixed marker `crew-stdio-mount-proved`, then `stopReason` — the round trip is proven and the permission request is real |

**Nothing here is synthesized.** The adapter produced every frame on its own wire,
including the MCP permission request, so there is no class written from a declared
shape. The MCP proof is split across two files (deny + allow) because the parent needs
BOTH facts — that the MCP call prompts, and that the deny stops it — and a single turn
answers the prompt one way.

## The two-part seed and the double read-back

The approval route is settled in the `session/new` response and read back there in the
SAME response. The client sends `initialize` with
`clientCapabilities._meta["muse/approval"] = 1` (which is what makes the adapter
advertise `muse/approval.seedAtSessionNew: true` and `.seedProfileAtSessionNew: true`
in its `initialize` result), then `session/new` with
`_meta {"muse/approval": {"policy": "promptUnmatched", "profile": ":ask-me"}}`. Both
values come from `permission_settings_for(ACP_BACKEND_MUSE)` and are carried by the
harness's `_approval_seed()`; the capture script imports that function, so the seed in
the fixtures is byte-for-byte the seed Crew sends.

The `session/new` result carries TWO `configOptions` read-backs in one response, with
no separate `set_config_option` round trip:

- `configOptions[id=nativeApprovalPolicy].currentValue == "promptUnmatched"`
- `configOptions[id=nativePermissionProfile].currentValue == ":ask-me"`

So the full route — policy AND profile — is in force before the first prompt is sent,
and confirming it needs no second session. `MuseHarness.seeded_setting_readback` reads
both keys and refuses the session if either is absent or not a string.

## What the permission capture establishes

`session/request_permission` fires both for a builtin shell command (`turn-live.jsonl`,
`permission-deny-live.jsonl`) and — the fact the prior corpus could not show — for a
session-mounted MCP tool call (`mcp-stdio-*-live.jsonl`).

For the MCP call the host offers FOUR option kinds (`mcp-stdio-mount-live.jsonl`):

- `allow_once` (kind `allow_once`, `_meta.museDecision: approved`)
- `allow_session` (kind `allow_always`, `_meta.museDecision: approvedForSession`)
- `allow_local_mcp_tool` (kind **`reject_always`**, `_meta.museDecision:
  approvedPolicyAmendment`) — a host fact, not a bug: the adapter maps a
  `localPersistent` "always allow this MCP tool" amendment to the `reject_always` ACP
  kind. Crew's approve path records the first `allow_always` it sees (→ `allow_session`)
  and its `reject_option_id` prefers kind `reject_once` (→ `abort`), so this
  kind-mapping does not misroute either of Crew's decisions. Do not "fix" it here.
- `abort` (kind `reject_once`, `_meta.museDecision: abort`)

The builtin shell command offers the two-kind set (`allow_once` / `abort`).

Answering `allow_once` runs the call to a `completed` `tool_call_update` — the shell
command's stdout in `turn-live.jsonl`, the marker `crew-stdio-mount-proved` in
`mcp-stdio-allow-live.jsonl`. Answering `abort` leaves the call `failed` with no
`completed` execute update — the deny stops it, for both the shell command
(`permission-deny-live.jsonl`) and the MCP call (`mcp-stdio-mount-live.jsonl`).

The MCP capture establishes, on the wire, that the permission request WAS drawn for the
MCP tool call under the two-part seed, and that the deny stopped it. Under the prior
corpus's profile-less seed (host default `:auto-review`) the same MCP call was
auto-cleared by the host's own judge with no prompt; seeding `:ask-me` is what routes
it to the client. That difference is the whole reason this corpus exists.

The tool grammar on this host is `mcp__<server>__<tool>` (an `mcp__` prefix and DOUBLE
underscores), and the server name `crew-probe` is normalized to `crew_probe` in the
tool id.

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
  enforced by the host: not probed. The fixtures only exercise `promptUnmatched`.
- The host-committed profile id at `session/new` time: `currentValue` on the read-back
  is the seeded REQUEST, because the host starts lazily at the first turn (same as
  `nativeApprovalPolicy`). The host commits the profile at the first turn — which these
  fixtures then exercise by drawing the permission request the committed `:ask-me`
  profile produces.
- `session/permissionProfileChanged` re-emit: not driven; it fires only after a
  mid-session set gesture, out of scope here.

## Reductions

A live frame is host data until proved otherwise, so each reduction is named here and in
each file's own `_meta.note`. All reductions are applied AT CAPTURE, in
`scripts/capture_muse_frames.py`'s reduction step — never by hand:

- Session ids → `muse-session-1`; every host-minted uuid
  (`museApprovalId`, `museTurnId`, `museItemId`, `museTaskId`, `museRequirementId`, …) →
  a fixed synthetic `muse-id-N`; every `call_<hex>` tool-call id → a fixed synthetic
  `muse-call-N`. These are the marker classes minted by the host rather than read from
  product shape, so they are normalized at capture, the way the goose/deepseek corpora
  are. (The absent-id `session/load` uses the literal `muse-session-absent`, which the
  host echoes in its rejection message.)
- The recording home directory and the scratch cwd → `~`.
- Dropped at capture as recording-host inventory / churn, none of them a required-class
  carrier: `available_commands_update` (the host's command + skill inventory, carrying
  internal markers and unreleased codenames), `session_info_update` (host-compatibility
  fingerprints, a prompt title, approval uuids), `config_option_update` (the post-
  `session/new` re-send of the full provider/model catalog), and the host's internal
  `reminderChild` tool-call frames.
- Every string is passed through the product's own `redact_text` (the same scrub the
  in-product frame recorder applies).
- Longer turns are sliced to the required-class carriers in wire order; nothing within a
  kept frame is otherwise altered.

Every file passes `scripts/check_acp_frame_host_data.py` with **no new baseline entry**.

## Re-capturing

`python3 scripts/capture_muse_frames.py` writes to `build/acp-capture/muse/`
(git-ignored) by default and refuses to write a fixture if the MCP call does not draw a
permission request — that would be a real finding, not a reason to synthesize. Review
the capture, then `python3 scripts/capture_muse_frames.py --into-corpus` overwrites the
five fixtures, and `python3 scripts/update_acp_frame_snapshots.py` regenerates the
`*.expected.json` snapshots. Commit the `.jsonl` and `.expected.json` together.
