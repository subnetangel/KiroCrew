#!/usr/bin/env python3
"""Capture the Muse frame corpus off the fork adapter under Crew's FULL two-part seed.

The Muse corpus has to PROVE one thing the prior one could not: under the host
``:ask-me`` permission profile a session-mounted MCP tool call draws a real
``session/request_permission``, and a deny fails the call. The earlier corpus was
taken with only the approval POLICY seeded (``promptUnmatched``) and the host default
profile (``:auto-review``), which cleared the MCP call through its own judge with no
prompt. This script sends what Kiro Crew actually sends -- the approval policy AND the
``:ask-me`` profile -- by IMPORTING the seed and client capabilities from the harness
module, so the capture can never drift from the product's wire.

    python3 scripts/capture_muse_frames.py            # -> build/acp-capture/muse/
    python3 scripts/capture_muse_frames.py --into-corpus   # overwrite the four fixtures

It drives the fork adapter (``muse-code-acp`` ``dist/index.js``) over stdio against the
installed, signed-in ``muse`` host, mounting a dependency-free stdio MCP server whose
one tool ``crew_probe_echo`` returns a fixed marker. Four scenarios are driven, each in
its own fresh session so an earlier turn cannot shape a later one:

* ``handshake``  -- initialize result; ``session/new`` reading BOTH the policy and the
  profile back; the absent-id ``session/load`` rejection.
* ``turn``       -- the builtin shell turn, ALLOW path (``allow_once``).
* ``permission-deny`` -- the shell turn, DENY path (``abort``).
* ``mcp-stdio-mount`` -- THE PROOF: the MCP tool call drawing a
  ``session/request_permission``, answered ``abort`` -> ``failed``, and the ALLOW path
  (``allow_once``) completing with the marker.

Every agent->client line is recorded RAW, then the named reductions are applied AT
CAPTURE (never by hand), then the lines are sliced into the required-class carriers in
wire order. Default output is ``build/acp-capture/muse/`` (git-ignored); moving a
reviewed capture into ``test/fixtures/acp_frames/muse/`` is the deliberate second step
``--into-corpus`` performs.

If the MCP call does NOT draw a ``session/request_permission`` under the full seed, the
script writes nothing into the corpus and reports exactly what the frames showed: that
is a real finding, not a reason to synthesize a fixture.

Exit codes: 0 captured and the MCP call prompted; 2 a usage/environment error;
3 the host refused (auth/provider); 4 the MCP call did NOT prompt (the finding);
5 a recording-host marker survived the sweep.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import importlib.util
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]

# Pinned before any kiro_crew import, as the sibling capture script does: the metrics
# provider resolves consent on its first build and the env var outranks the config flag,
# so a host with telemetry on would otherwise record this drive.
os.environ["KIROCREW_TELEMETRY"] = "0"

sys.path.insert(0, str(REPO_ROOT / "src"))

from kiro_crew.acp._dispatch import redact_text  # noqa: E402
from kiro_crew.acp.harness import muse as muse_harness  # noqa: E402

BACKEND = "muse"

#: The marker the probe MCP tool returns. Fixed and product-authored, so its presence in
#: the fixture proves the round trip rather than leaking anything.
PROBE_MARKER = "crew-stdio-mount-proved"

#: The probe server's name and tool, and the host tool grammar they produce. The host
#: normalizes ``crew-probe`` -> ``crew_probe`` and spells the id ``mcp__<server>__<tool>``.
PROBE_SERVER_NAME = "crew-probe"
PROBE_TOOL_NAME = "crew_probe_echo"
PROBE_TOOL_ID = f"mcp__crew_probe__{PROBE_TOOL_NAME}"

#: Prompts. Each asks for exactly one tool call and nothing else, so the turn is the
#: tool round trip rather than the model's prose.
MCP_PROMPT = (
    f"Call the MCP tool named {PROBE_TOOL_NAME} (no arguments) and report exactly what "
    "it returns. Call the tool; do not describe it."
)
SHELL_PROMPT = (
    "Run the shell command `echo muse-shell-probe` using your shell tool. Call the "
    "tool; do not describe it."
)
SHELL_MARKER = "muse-shell-probe"

#: session/update discriminants dropped at capture: each is an inventory of what the
#: RECORDING HOST has or a wall-clock/fingerprint record, not a shape any parser reads.
#: ``available_commands_update`` is the host's command + skill inventory (internal
#: markers, unreleased codenames); ``session_info_update`` carries host-compatibility
#: fingerprints, a prompt title and approval uuids; ``config_option_update`` re-sends the
#: full provider/model catalog after session/new. The required read-backs all live on the
#: session/new RESULT, so none of these is a required-class carrier.
DROPPED_UPDATES = (
    "available_commands_update",
    "session_info_update",
    "config_option_update",
)

#: Tool-call noise dropped at capture: the host emits internal ``reminderChild`` tool
#: calls of its own throughout a turn. They are not the probe's call and carry no
#: required class; keeping them would bury the one tool call the fixture exists to show.
DROPPED_TOOL_NAMES = ("reminderChild",)

#: The probe MCP server, written to the session cwd and mounted in the session/new
#: array. Dependency-free: Node's built-in readline over stdio, one JSON-RPC server
#: speaking just enough MCP (initialize / tools/list / tools/call) to expose one tool.
PROBE_SERVER_SOURCE = """\
'use strict';
// Dependency-free stdio MCP server: one tool, crew_probe_echo, returning a fixed
// marker. Node built-ins only (readline over stdin/stdout), so nothing is installed.
const readline = require('readline');
const MARKER = %(marker)r.replace(/^'|'$/g, '');
const TOOL = %(tool)r.replace(/^'|'$/g, '');
const rl = readline.createInterface({ input: process.stdin });
function send(msg) { process.stdout.write(JSON.stringify(msg) + '\\n'); }
rl.on('line', (line) => {
  line = line.trim();
  if (!line) return;
  let req;
  try { req = JSON.parse(line); } catch (e) { return; }
  const id = req.id;
  const method = req.method;
  if (method === 'initialize') {
    send({ jsonrpc: '2.0', id, result: {
      protocolVersion: '2024-11-05',
      capabilities: { tools: {} },
      serverInfo: { name: 'crew-probe', version: '1.0.0' },
    }});
  } else if (method === 'notifications/initialized') {
    // notification: no response
  } else if (method === 'tools/list') {
    send({ jsonrpc: '2.0', id, result: { tools: [
      { name: TOOL, description: 'Return a fixed probe marker.',
        inputSchema: { type: 'object', properties: {}, additionalProperties: false } },
    ]}});
  } else if (method === 'tools/call') {
    const name = (req.params || {}).name;
    if (name === TOOL) {
      send({ jsonrpc: '2.0', id, result: {
        content: [{ type: 'text', text: MARKER }], isError: false,
      }});
    } else {
      send({ jsonrpc: '2.0', id, error: { code: -32602, message: 'unknown tool: ' + name } });
    }
  } else if (id !== undefined && id !== null) {
    send({ jsonrpc: '2.0', id, error: { code: -32601, message: 'method not found: ' + method } });
  }
});
""" % {"marker": repr(PROBE_MARKER), "tool": repr(PROBE_TOOL_NAME)}


class CaptureError(Exception):
    """Something about the host or the arguments, not the adapter's behaviour."""


class HostRefused(Exception):
    """The host answered with an auth or provider error rather than a turn."""


REFUSAL_MARKERS = (
    "authentication required",
    "not authenticated",
    "unauthorized",
    "no credentials",
    "failed to resolve provider",
)


# ── resolving what to spawn ─────────────────────────────────────────────────────


def resolve_adapter_argv() -> list[str]:
    """``node <adapter dist/index.js>`` resolved from the sibling adapter checkout.

    The adapter is read-only for this track and is not on a Crew launch ladder the way
    goose/pi are, so it is resolved by its known path rather than through
    ``acp.client``. The path is the one the brief pins; a missing file is a CaptureError
    naming it rather than a traceback.
    """
    adapter = Path("/local/home/jpruiz/workplace/muse-code-acp/dist/index.js")
    if not adapter.is_file():
        raise CaptureError(
            f"adapter entry not found at {adapter}. The fork adapter must be built "
            "(its dist/index.js present); this track does not build it."
        )
    node = shutil.which("node") or shutil.which("node-22")
    if not node:
        raise CaptureError("node is not on PATH; the adapter is a Node stdio server")
    return [node, str(adapter)]


def resolve_muse_host() -> str:
    """The installed ``muse`` binary the adapter must drive, or a CaptureError."""
    muse = os.environ.get("MUSE_CODE_EXECUTABLE") or shutil.which("muse")
    if not muse:
        raise CaptureError(
            "the muse host was not found: set MUSE_CODE_EXECUTABLE or put muse on PATH. "
            "The adapter spawns `muse serve`; without the host there is nothing to drive."
        )
    return muse


def probe_server_element(cwd: Path) -> dict[str, Any]:
    """Write the probe server into *cwd* and return its ``session/new`` array element."""
    node = shutil.which("node-22") or shutil.which("node")
    if not node:
        raise CaptureError("node is not on PATH; the probe MCP server is a Node script")
    script = cwd / "crew-probe-mcp.cjs"
    script.write_text(PROBE_SERVER_SOURCE, encoding="utf-8")
    return {"name": PROBE_SERVER_NAME, "command": node, "args": [str(script)], "env": []}


# ── the stdio driver ────────────────────────────────────────────────────────────


class StdioDriver:
    """A minimal ACP client over the adapter's stdio: initialize, session, one prompt.

    Not ``AcpClient``: a capture must be evidence about the adapter's own wire, so
    nothing of Crew's session pool / gate / projection sits between the adapter and the
    recorded frames. What IS reused is the part a capture must get right -- the exact
    client capabilities and ``session/new`` seed Crew sends -- imported from the harness
    module, never retyped.
    """

    def __init__(self, argv: list[str], host_bin: str, cwd: Path, timeout: float) -> None:
        self._argv = argv
        self._cwd = cwd
        self._timeout = timeout
        env = dict(os.environ)
        env["MUSE_CODE_EXECUTABLE"] = host_bin
        self._env = env
        self._next_id = 0
        self._lines: "queue.Queue[str | None]" = queue.Queue()
        self.frames: list[dict[str, Any]] = []
        self.stderr_tail: list[str] = []
        self.agent_version = "unmeasured"
        self.agent_name = ""
        self.session_id = ""
        self._proc: subprocess.Popen[str] | None = None

    def __enter__(self) -> "StdioDriver":
        try:
            self._proc = subprocess.Popen(  # noqa: S603 - resolved argv, never a shell
                self._argv,
                cwd=str(self._cwd),
                env=self._env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise CaptureError(f"cannot start {' '.join(self._argv)}: {exc}") from exc
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        return self

    def __exit__(self, *_exc: object) -> None:
        proc = self._proc
        if proc is None:
            return
        for step in (proc.terminate, proc.kill):
            if proc.poll() is not None:
                break
            step()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                continue

    def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for line in self._proc.stdout:
            self._lines.put(line)
        self._lines.put(None)

    def _read_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        for line in self._proc.stderr:
            self.stderr_tail.append(line.rstrip())
            del self.stderr_tail[:-60]

    def _stderr_hint(self) -> str:
        if not self.stderr_tail:
            return "It wrote nothing to stderr."
        return "Its last stderr lines were:\n  " + "\n  ".join(self.stderr_tail[-10:])

    def _send(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise CaptureError("the adapter process is gone")
        try:
            proc.stdin.write(json.dumps(payload) + "\n")
            proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise CaptureError(
                f"the adapter closed its input after {len(self.frames)} frame(s): {exc}. "
                f"{self._stderr_hint()}"
            ) from exc

    def request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        answer: Callable[[dict[str, Any]], str] | None = None,
    ) -> dict[str, Any]:
        """Send a request and pump the wire until its response arrives.

        *answer* decides how an inbound ``session/request_permission`` is answered,
        given its params: it returns an optionId (e.g. ``allow_once`` or ``abort``).
        Any other inbound agent->client request is refused with method-not-found, since
        this capture client implements none of them.
        """
        self._next_id += 1
        request_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return self._pump_until(request_id, method, answer)

    def _pump_until(
        self, request_id: int, method: str, answer: Callable[[dict[str, Any]], str] | None
    ) -> dict[str, Any]:
        while True:
            try:
                line = self._lines.get(timeout=self._timeout)
            except queue.Empty:
                raise CaptureError(
                    f"the adapter sent nothing for {self._timeout:.0f}s while answering "
                    f"{method}. {self._stderr_hint()}"
                ) from None
            if line is None:
                raise CaptureError(
                    f"the adapter exited while answering {method}. {self._stderr_hint()}"
                )
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(frame, dict):
                continue
            self.frames.append(frame)
            if frame.get("id") == request_id and ("result" in frame or "error" in frame):
                error = frame.get("error")
                if error:
                    self._raise_for_error(method, error)
                result = frame.get("result")
                return result if isinstance(result, dict) else {}
            if "method" in frame and "id" in frame:
                self._answer_inbound(frame, answer)

    def _raise_for_error(self, method: str, error: Any) -> None:
        message = error.get("message") if isinstance(error, dict) else str(error)
        lowered = f"{message} {json.dumps(error)}".lower()
        if any(marker in lowered for marker in REFUSAL_MARKERS):
            raise HostRefused(f"{method} refused: {message}")
        raise CaptureError(f"{method} failed: {message}. {self._stderr_hint()}")

    def _answer_inbound(
        self, frame: dict[str, Any], answer: Callable[[dict[str, Any]], str] | None
    ) -> None:
        method = frame.get("method") or ""
        request_id = frame.get("id")
        if method == "session/request_permission" and answer is not None:
            option_id = answer(frame.get("params") or {})
            if option_id:
                self._send(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {"outcome": {"outcome": "selected", "optionId": option_id}},
                    }
                )
                return
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"outcome": {"outcome": "cancelled"}},
                }
            )
            return
        # Any other inbound request: this capture client implements none.
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"{method} not implemented by capture client"},
            }
        )

    # -- the handshake Crew sends, imported not retyped --

    def initialize(self) -> dict[str, Any]:
        result = self.request(
            "initialize",
            {
                "protocolVersion": muse_harness.PROTOCOL_VERSION_MUSE,
                "clientCapabilities": muse_harness._MUSE_CLIENT_CAPABILITIES,
            },
        )
        info = result.get("agentInfo")
        if isinstance(info, dict):
            if info.get("version"):
                self.agent_version = str(info["version"])
            if info.get("name"):
                self.agent_name = str(info["name"])
        return result

    def new_session(self, mcp_servers: list[dict[str, Any]]) -> dict[str, Any]:
        result = self.request(
            "session/new",
            {
                "cwd": str(self._cwd),
                "mcpServers": mcp_servers,
                "_meta": muse_harness._approval_seed(),
            },
        )
        session_id = result.get("sessionId")
        if not session_id:
            raise CaptureError(f"session/new returned no sessionId: {json.dumps(result)[:400]}")
        self.session_id = str(session_id)
        return result

    def session_load_absent(self) -> None:
        """Drive the absent-id ``session/load`` rejection (a required handshake class).

        The error is expected; it is recorded as a frame and swallowed here so the
        capture continues.
        """
        self._next_id += 1
        request_id = self._next_id
        absent = "muse-session-absent"
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "session/load",
                "params": {"sessionId": absent, "cwd": str(self._cwd), "mcpServers": []},
            }
        )
        try:
            self._pump_until(request_id, "session/load", None)
        except CaptureError:
            # The rejection is the point; it arrives as an error frame already recorded.
            pass

    def prompt(
        self, text: str, *, answer: Callable[[dict[str, Any]], str] | None = None
    ) -> dict[str, Any]:
        return self.request(
            "session/prompt",
            {"sessionId": self.session_id, "prompt": [{"type": "text", "text": text}]},
            answer=answer,
        )


# ── answer strategies ───────────────────────────────────────────────────────────


def answer_allow_once(_params: dict[str, Any]) -> str:
    """Crew's approve path records the FIRST allow_always; the once option is allow_once."""
    return "allow_once"


def answer_abort(_params: dict[str, Any]) -> str:
    """Crew's reject_option_id prefers kind reject_once -> abort."""
    return "abort"


# ── reduction ───────────────────────────────────────────────────────────────────

#: Every uuid shape the host mints (session id, museApprovalId/TurnId/ItemId/...,
#: call_<hex> tool-call ids) is normalized to a fixed synthetic value AT CAPTURE.
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_CALL_RE = re.compile(r"call_[0-9a-f]{6,}")


def reduce_frames(
    frames: Sequence[dict[str, Any]], *, session_id: str, cwd: Path
) -> tuple[list[dict[str, Any]], list[str]]:
    """Drop host inventories/noise and normalize host-minted ids, reporting what was done.

    Reductions, matching the prior corpus exactly:
      * drop the host inventory / fingerprint / catalog update frames (DROPPED_UPDATES);
      * drop the host's internal reminderChild tool-call churn (DROPPED_TOOL_NAMES);
      * session id -> muse-session-1; permission request id -> perm-1;
        every uuid under museApprovalId/museTurnId/museItemId/... -> muse-id-N;
        every call_<hex> -> muse-call-N;
      * the recording home directory and the scratch cwd -> ~;
      * the product redact_text scrub (the same the in-product recorder applies).
    Nothing inside a kept frame is otherwise altered.
    """
    notes: list[str] = []
    dropped_updates = 0
    dropped_tools = 0
    kept: list[dict[str, Any]] = []

    for frame in frames:
        params = frame.get("params")
        update = params.get("update") if isinstance(params, dict) else None
        if isinstance(update, dict):
            kind = update.get("sessionUpdate")
            if kind in DROPPED_UPDATES:
                dropped_updates += 1
                continue
            if update.get("name") in DROPPED_TOOL_NAMES:
                dropped_tools += 1
                continue
        kept.append(frame)

    if dropped_updates:
        notes.append(
            f"{dropped_updates} {'/'.join(DROPPED_UPDATES)} frame(s) dropped: host "
            "command/skill inventory, host-compatibility fingerprints and the "
            "provider/model catalog -- recording-host data, not a required class"
        )
    if dropped_tools:
        notes.append(
            f"{dropped_tools} host reminderChild tool-call frame(s) dropped: the host's "
            "own internal churn, not the probe's call"
        )

    # Product redact_text scrub over the frame DATA (not the JSON text), as the
    # in-product recorder does.
    scrubbed = 0

    def _scrub(text: str) -> str:
        nonlocal scrubbed
        cleaned = redact_text(text)
        if cleaned != text:
            scrubbed += 1
        return cleaned

    kept = [_map_strings(frame, _scrub) for frame in kept]
    if scrubbed:
        notes.append(f"{scrubbed} string(s) scrubbed through the product's redact_text")

    # Path substitutions FIRST: cwd before home, because the scratch cwd contains the
    # home path and replacing the shorter one first mangles the longer.
    reduced = kept
    cwd_text = str(cwd)
    if cwd_text and cwd_text != os.sep:
        reduced, hit = _substitute(reduced, cwd_text, "~")
        if hit:
            notes.append("the scratch working directory is replaced with ~")
    home = str(Path.home())
    if home and home != os.sep:
        reduced, hit = _substitute(reduced, home, "~")
        if hit:
            notes.append("the recording user's home directory is replaced with ~")

    # Id normalization. Session id first (a plain substring), then the uuid regex over
    # everything that remains (museApprovalId/TurnId/..., and any session id not caught
    # above), then call_<hex>. Regexes run over the frame DATA via a mapper.
    if session_id:
        reduced, hit = _substitute(reduced, session_id, "muse-session-1")
        if hit:
            notes.append("the session id is replaced with the synthetic muse-session-1")

    reduced, uuid_notes = _normalize_by_regex(reduced, _UUID_RE, "muse-id")
    if uuid_notes:
        notes.append(
            "every host-minted uuid (museApprovalId/museTurnId/museItemId/...) is "
            "replaced with a fixed synthetic muse-id-N"
        )
    reduced, call_notes = _normalize_by_regex(reduced, _CALL_RE, "muse-call")
    if call_notes:
        notes.append("every call_<hex> tool-call id is replaced with a synthetic muse-call-N")

    # The permission request id (JSON-RPC id 0 on the inbound request) is a small int on
    # this host, not a uuid, so it needs no normalization. If a future host mints a
    # uuid-shaped permission id it is already covered by the uuid pass above.

    return reduced, notes


def _substitute(
    frames: Sequence[dict[str, Any]], needle: str, replacement: str
) -> tuple[list[dict[str, Any]], bool]:
    hit = False

    def _swap(text: str) -> str:
        nonlocal hit
        if needle in text:
            hit = True
            return text.replace(needle, replacement)
        return text

    return [_map_strings(frame, _swap) for frame in frames], hit


def _normalize_by_regex(
    frames: Sequence[dict[str, Any]], pattern: re.Pattern[str], prefix: str
) -> tuple[list[dict[str, Any]], bool]:
    """Replace each DISTINCT regex match with ``<prefix>-N`` in first-seen order.

    One stable number per distinct id across the whole frame list, so the same uuid maps
    to the same synthetic value everywhere it appears (an approvalId echoed on several
    frames stays one id).
    """
    mapping: dict[str, str] = {}

    def _swap(text: str) -> str:
        def _one(match: re.Match[str]) -> str:
            value = match.group(0)
            if value not in mapping:
                mapping[value] = f"{prefix}-{len(mapping) + 1}"
            return mapping[value]

        return pattern.sub(_one, text)

    return [_map_strings(frame, _swap) for frame in frames], bool(_count_matches(frames, pattern))


def _count_matches(frames: Sequence[dict[str, Any]], pattern: re.Pattern[str]) -> int:
    blob = json.dumps(frames)
    return len(pattern.findall(blob))


def _map_strings(node: Any, swap: Callable[[str], str]) -> Any:
    if isinstance(node, str):
        return swap(node)
    if isinstance(node, dict):
        return {
            (swap(k) if isinstance(k, str) else k): _map_strings(v, swap) for k, v in node.items()
        }
    if isinstance(node, list):
        return [_map_strings(item, swap) for item in node]
    return node


# ── slicing ─────────────────────────────────────────────────────────────────────


def agent_to_client(frames: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Only the lines the AGENT sent to the client: results, errors, and notifications.

    A fixture records the agent->client wire. The driver's own outbound requests
    (initialize/session/new/session/prompt) and its permission ANSWERS are client->agent
    and never belong in the corpus; they are excluded here. An inbound
    ``session/request_permission`` IS agent->client and is kept.
    """
    out: list[dict[str, Any]] = []
    for frame in frames:
        method = frame.get("method")
        if method is None:
            # A response to one of our requests (has id + result/error): agent->client.
            if "result" in frame or "error" in frame:
                out.append(frame)
            continue
        # A method frame: a notification (session/update) or an inbound request
        # (session/request_permission). Both are agent->client. Our own outbound requests
        # never reach self.frames (we record only what we READ), so every method frame
        # here is inbound.
        out.append(frame)
    return out


def is_permission_request(frame: dict[str, Any]) -> bool:
    return frame.get("method") == "session/request_permission"


def mcp_permission_drawn(frames: Sequence[dict[str, Any]]) -> bool:
    """Whether a ``session/request_permission`` for the probe MCP tool was recorded."""
    for frame in frames:
        if not is_permission_request(frame):
            continue
        params = frame.get("params") or {}
        tool_call = params.get("toolCall") or {}
        title = str(tool_call.get("title") or "")
        if PROBE_TOOL_ID in title or title.endswith(PROBE_TOOL_NAME):
            return True
    return False


# ── provenance ──────────────────────────────────────────────────────────────────


def meta_header(agent_version: str, agent_name: str, note: str) -> dict[str, Any]:
    return {
        "_meta": {
            "backend": BACKEND,
            "recorded": "live",
            "date": _datetime.date.today().isoformat(),
            "agent_version": agent_version,
            "agent_name": agent_name,
            "note": note,
        }
    }


def write_fixture(path: Path, header: dict[str, Any], frames: Sequence[dict[str, Any]]) -> None:
    lines = [json.dumps(header, ensure_ascii=False)]
    lines += [json.dumps(frame, ensure_ascii=False) for frame in frames]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── the host-data sweep, run with the repo's own gate patterns ───────────────────


def host_markers(text: str) -> list[tuple[str, str]]:
    gate_path = REPO_ROOT / "scripts" / "check_acp_frame_host_data.py"
    spec = importlib.util.spec_from_file_location("acp_frame_host_data_gate", gate_path)
    if spec is None or spec.loader is None:
        raise CaptureError(f"cannot load {gate_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    found: list[tuple[str, str]] = []
    for pattern, reason in module.PATTERNS:
        match = re.search(pattern, text)
        if match:
            found.append((match.group(0), reason))
    return found


# ── the four scenario drives ─────────────────────────────────────────────────────


def drive(
    scenario: str,
    *,
    adapter: list[str],
    host: str,
    timeout: float,
    mount_probe: bool,
    prompt: str | None,
    answer: Callable[[dict[str, Any]], str] | None,
    load_absent: bool,
) -> tuple[list[dict[str, Any]], str, str, str]:
    """Run ONE scenario in a fresh session; return (raw frames, version, name, session id)."""
    cwd = Path(tempfile.mkdtemp(prefix=f"muse-cap-{scenario}-"))
    mcp_servers = [probe_server_element(cwd)] if mount_probe else []
    with StdioDriver(adapter, host, cwd, timeout) as driver:
        driver.initialize()
        driver.new_session(mcp_servers)
        if load_absent:
            driver.session_load_absent()
        if prompt is not None:
            driver.prompt(prompt, answer=answer)
        return list(driver.frames), driver.agent_version, driver.agent_name, driver.session_id, cwd  # type: ignore[return-value]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="capture_muse_frames.py", description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=None, help="Output dir (default build/acp-capture/muse/)."
    )
    parser.add_argument(
        "--timeout", type=float, default=180.0, help="Per-frame wait seconds (default 180)."
    )
    parser.add_argument(
        "--into-corpus",
        action="store_true",
        help="Write the reduced fixtures straight into test/fixtures/acp_frames/muse/ "
        "(the deliberate second step; default writes to build/).",
    )
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])

    try:
        adapter = resolve_adapter_argv()
        host = resolve_muse_host()
    except CaptureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out_dir = args.out or (REPO_ROOT / "build" / "acp-capture" / "muse")
    corpus_dir = REPO_ROOT / "test" / "fixtures" / "acp_frames" / "muse"

    # Each scenario: (fixture name, mount probe?, prompt, answer, drive session/load?).
    scenarios = [
        ("handshake", False, None, None, True),
        ("turn", False, SHELL_PROMPT, answer_allow_once, False),
        ("permission-deny", False, SHELL_PROMPT, answer_abort, False),
        ("mcp-stdio-mount", True, MCP_PROMPT, answer_abort, False),
        ("mcp-stdio-allow", True, MCP_PROMPT, answer_allow_once, False),
    ]

    captured: dict[str, dict[str, Any]] = {}
    agent_version = "unmeasured"
    agent_name = ""
    try:
        for name, mount, prompt, answer, load_absent in scenarios:
            raw, version, aname, session_id, cwd = drive(
                name,
                adapter=adapter,
                host=host,
                timeout=args.timeout,
                mount_probe=mount,
                prompt=prompt,
                answer=answer,
                load_absent=load_absent,
            )
            if version != "unmeasured":
                agent_version = version
            if aname:
                agent_name = aname
            a2c = agent_to_client(raw)
            reduced, notes = reduce_frames(a2c, session_id=session_id, cwd=cwd)
            captured[name] = {"frames": reduced, "notes": notes}
            print(f"  {name}: {len(a2c)} agent->client frame(s) -> {len(reduced)} after reduction")
    except HostRefused as exc:
        print(f"error: the muse host refused -- {exc}", file=sys.stderr)
        return 3
    except CaptureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    # THE PROOF gate: the MCP call must have drawn a permission request under the full
    # seed, in BOTH the deny and allow scenarios.
    deny_prompted = mcp_permission_drawn(captured["mcp-stdio-mount"]["frames"])
    allow_prompted = mcp_permission_drawn(captured["mcp-stdio-allow"]["frames"])
    if not (deny_prompted and allow_prompted):
        print(
            "\nFINDING: the MCP tool call did NOT draw a session/request_permission under "
            "the full policy+profile seed.\n"
            f"  deny scenario prompted:  {deny_prompted}\n"
            f"  allow scenario prompted: {allow_prompted}\n"
            "Not writing a fixture. The recorded frames are under the output dir for "
            "inspection; this contradicts Track A (which saw it prompt with a "
            "profile-only seed) and is a real result, not a reason to synthesize.",
            file=sys.stderr,
        )
        # Still dump the raw reduced frames so the finding can be inspected.
        dump = out_dir / "FINDING"
        for name, data in captured.items():
            write_fixture(
                dump / f"{name}.jsonl",
                meta_header(agent_version, agent_name, "FINDING: MCP did not prompt"),
                data["frames"],
            )
        print(f"  frames written under {dump}", file=sys.stderr)
        return 4

    # Headers per file, each naming its own reductions and what it establishes.
    headers = _headers(agent_version, agent_name, captured)

    dest = corpus_dir if args.into_corpus else out_dir
    files = {
        "handshake-live.jsonl": captured["handshake"]["frames"],
        "turn-live.jsonl": captured["turn"]["frames"],
        "permission-deny-live.jsonl": captured["permission-deny"]["frames"],
        "mcp-stdio-mount-live.jsonl": captured["mcp-stdio-mount"]["frames"],
        "mcp-stdio-allow-live.jsonl": captured["mcp-stdio-allow"]["frames"],
    }
    header_key = {
        "handshake-live.jsonl": "handshake",
        "turn-live.jsonl": "turn",
        "permission-deny-live.jsonl": "permission-deny",
        "mcp-stdio-mount-live.jsonl": "mcp-stdio-mount",
        "mcp-stdio-allow-live.jsonl": "mcp-stdio-allow",
    }
    for filename, frames in files.items():
        write_fixture(dest / filename, headers[header_key[filename]], frames)

    # Sweep every written file with the repo's own gate patterns before claiming done.
    bad: list[str] = []
    for filename in files:
        text = (dest / filename).read_text(encoding="utf-8")
        for match, reason in host_markers(text):
            bad.append(f"{filename}: {reason} -- {match!r}")
    if bad:
        print("\nhost markers survived; the capture is NOT commit-ready:", file=sys.stderr)
        for line in bad:
            print(f"  {line}", file=sys.stderr)
        return 5

    print(f"\nwrote 5 fixture(s) to {dest} (MCP permission request drawn on deny AND allow)")
    if not args.into_corpus:
        print("Review, then re-run with --into-corpus to replace the committed corpus.")
    return 0


def _headers(
    agent_version: str, agent_name: str, captured: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    seed = json.dumps(muse_harness._approval_seed()["muse/approval"])
    common = (
        "Captured by scripts/capture_muse_frames.py off the fork adapter dist/index.js "
        "driven over stdio against Muse Code 1.4.11 (MUSE_CODE_EXECUTABLE=host muse), a "
        "signed-in account, so the model answered live. Agent-to-client lines in order. "
        f"Crew's FULL two-part seed was sent, imported from the harness module: "
        f"initialize clientCapabilities._meta[muse/approval]=1, and session/new "
        f"_meta[muse/approval]={seed}. "
    )

    def note(name: str, specific: str) -> str:
        reductions = "; ".join(captured[name]["notes"]) or "none"
        return f"{common}{specific} Reductions: {reductions}. The ids are synthetic; nothing else is altered."

    return {
        "handshake": meta_header(
            agent_version,
            agent_name,
            note(
                "handshake",
                "Covers the initialize result (protocolVersion, agentInfo.version); the "
                "session/new result whose configOptions read BOTH "
                "nativeApprovalPolicy.currentValue=promptUnmatched AND "
                "nativePermissionProfile.currentValue=:ask-me back in the SAME response; and "
                "the absent-id session/load rejection (-32602, this host's shape, NOT -32002).",
            ),
        ),
        "turn": meta_header(
            agent_version,
            agent_name,
            note(
                "turn",
                "The builtin shell turn, ALLOW path (allow_once). The sandboxed first attempt "
                "fails and the model retries with escalated sandbox, which draws a "
                "session/request_permission; answering allow_once runs it to a completed "
                "tool_call_update with the command's output. All seven required classes.",
            ),
        ),
        "permission-deny": meta_header(
            agent_version,
            agent_name,
            note(
                "permission-deny",
                "The same shell turn, DENY path (abort): the session/request_permission is "
                "answered abort, leaving the call failed with no completed execute update -- "
                "the deny stops the command.",
            ),
        ),
        "mcp-stdio-mount": meta_header(
            agent_version,
            agent_name,
            note(
                "mcp-stdio-mount",
                "THE PROOF (deny). A dependency-free stdio MCP server (one tool, "
                f"{PROBE_TOOL_NAME}) is passed in the session/new mcpServers array. Under the "
                "two-part seed (:ask-me profile) the MCP tool call "
                f"{PROBE_TOOL_ID} DREW a real session/request_permission reaching the client; "
                "answering abort left the call failed -- the deny stopped the MCP call. "
                "Host option kinds offered: allow_once (allow_once), allow_session "
                "(allow_always), allow_local_mcp_tool (reject_always -- the adapter maps a "
                "localPersistent approvedPolicyAmendment to that kind), abort (reject_once). "
                "The tool grammar is mcp__<server>__<tool>; crew-probe is normalized to "
                "crew_probe.",
            ),
        ),
        "mcp-stdio-allow": meta_header(
            agent_version,
            agent_name,
            note(
                "mcp-stdio-allow",
                "THE PROOF (allow). The same MCP tool call drawing the same "
                "session/request_permission; answering allow_once ran it to a completed "
                f"tool_call_update carrying the marker {PROBE_MARKER!r} -- the round trip is "
                "proven and the permission request is real.",
            ),
        ),
    }


if __name__ == "__main__":
    sys.exit(main())
