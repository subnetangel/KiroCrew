"""Projects the agent spec onto ``muse-code-acp`` (the Muse Code ACP adapter).

The third single-binary spec harness to carry a session ``mcpServers`` array, and
the array's MECHANICS are the ones :mod:`kiro_crew.providers.mirrors.opencode`
already owns and :mod:`kiro_crew.providers.mirrors.goose` already reuses: the same
shared translation (:func:`kiro_crew.acp.session_mcp.session_mcp_servers`, no new
translator), the same ``tools`` allowlist, the same registry filter, the same
control-plane re-derivation, and the same one-owner rule for the pooled broker
stubs. So this module DELEGATES to that placement function
(:func:`~kiro_crew.providers.mirrors.opencode.place_single_binary_array`) rather
than restating it, and carries what is actually Muse's: the rulings prose, and the
wire facts that differ.

Measured, not inferred. The channel is a ROUND TRIP against this host's Muse
1.4.11 through the fork adapter ``@bex-co/muse-code-acp`` (branch
``feat/kirocrew-seams``), recorded in Track A (``muse-code-ref/reports/trackA-REPORT.md``
§2b):

* ``initialize`` advertises ``agentCapabilities.mcpCapabilities: {"http": true}``
  and no ``stdio`` flag. That is not a refusal of stdio: ACP v1 treats ``http``
  and ``sse`` as the OPTIONAL transports its ``McpCapabilities`` schema can name,
  and stdio is the baseline every v1 agent MUST serve, so an absent stdio flag is
  exactly what full support looks like -- the same non-evidence recorded for
  opencode, goose and deepseek.
* A dependency-free stdio MCP server passed in the ACP ``session/new``
  ``mcpServers`` array was merged by the adapter into a private XDG overlay
  (``muse-code-acp/src/mcp-overlay.ts`` ``createMuseMcpOverlay``), listed by the
  model, and CALLED: ``mcp__probeping__ping`` returned ``pong`` and the turn ended
  ``end_turn``. So ``sessionMcp`` IS granted on 1.4.11 and the ``session/new``
  array is Muse's whole MCP channel -- Crew writes no Muse config file (the overlay
  is the adapter's own private temp copy, built per session from what Crew sends).

Two wire facts differ from the neighbouring mirrors, and neither changes the
projection shape:

* **An unstartable element FAILS THE WHOLE TURN** rather than being dropped. The
  adapter stamps every Crew-supplied session server with ``mode: "required"``
  (``mcp-overlay.ts:133``), so an element whose command cannot start aborts the
  turn -- fail-closed, the same hazard that puts deepseek (``session/new`` failure)
  in ``ACP_BACKENDS_PRUNE_UNSTARTABLE_MCP``. Muse does NOT join that set, for the
  reason codex (the same runtime-served, mode-enforced shape) is also absent: the
  prune (``AcpClient._prune_unstartable_mcp``) runs only on ``AcpClient`` call
  sites, and Muse is served by ``AcpRuntime`` (``ACP_BACKENDS_ACP_RUNTIME``, not
  ``ACP_BACKENDS_ACP_CLIENT_SPAWNABLE``), so those sites are never reached for it.
  A runtime-path prune is the fix when one is wanted; it does not exist yet, so
  neither backend claims an enforcement that would not run.
* **The MCP child inherits the adapter's ambient env** and ALSO the element's own
  ``env`` map, so the identity carriage is the same as opencode/goose. The adapter
  builds the stdio entry with ``env: Object.fromEntries(server.env.map(...))``
  (``mcp-overlay.ts:37``) -- the element's ``env`` reaches the child verbatim --
  and launches it with the overlay env ``{...baseEnv, XDG_CONFIG_HOME}``
  (``mcp-overlay.ts:175-177``), i.e. it does NOT ``env_clear`` the child the way
  codex-rs does. Either way the element's ``env`` is honoured, so Crew's control
  plane carries ``KIROCREW_SESSION_KEY`` on the element exactly as codex does, and
  for the same reason: the element ``env`` is what makes the value session-SCOPED
  rather than adapter-scoped.

The per-tool restriction is ``whole-server``, like goose, and for a cause proven in
the adapter source rather than assumed. Muse's ``session/request_permission`` (the
one channel a client could refuse a call on) carries only the flat MSP ``toolName``
string and the model-authored ``rawInput``
(``muse-code-acp/src/muse-permissions.ts`` ``approvalToPermissionRequest``, lines
~198-221): there is NO structured ``(server, tool)`` identity on the request, and no
``_meta`` field naming the MCP server. The ``mcp__<server>__<tool>`` string Track A
saw is the host-formed display NAME on the ``tool_call`` frame, not an identity Crew
consumes, and no Crew-side refusal path reads it. Scraping a server out of that
display string would be trusting a host/model-authored name, which is exactly the
``goose`` follow-up's "trust only a structured ``_meta`` pair" bar -- and Muse does
not even offer the pair. So a narrowed server is withheld WHOLE, Crew's own control
plane included, and ``denied_tools`` stays empty. That is the only faithful action
for a transport whose per-call channel carries no server identity.

The duplication that remains between the three single-binary harnesses is the
mirror CLASS, not the mechanics: a shared base for "single binary, spec dialect,
session array" is a refactor of all three, which is not this change's to make.
"""

from __future__ import annotations

from typing import Any, Collection, Mapping

from kiro_crew.acp.session_mcp import session_mcp_projection
from kiro_crew.agent_sdk.backends import ACP_BACKEND_MUSE
from kiro_crew.providers.mirrors.base import AgentConfigMirror, Concern
from kiro_crew.providers.mirrors.base import Disposition as _D
from kiro_crew.providers.mirrors.base import Ruling, SessionProjection
from kiro_crew.providers.mirrors.opencode import place_single_binary_array

__all__ = ["MuseMirror", "muse_projection"]


def muse_projection(
    agent: str | None,
    *,
    stub_server_names: Collection[str] = (),
    stub_elements: Collection[Mapping[str, Any]] = (),
    work_dir: object = None,
    session_key: str = "",
    channel_id: str = "",
    session_token: str = "",
) -> SessionProjection:
    """The whole Muse array -- spec translation AND pooled stubs.

    Placed by the single-binary harness's own function
    (:func:`~kiro_crew.providers.mirrors.opencode.place_single_binary_array`),
    because every placement rule is one this transport needs for the SAME measured
    reason as goose: one owner for both halves of the array (a stub appended after
    the projection withheld that name would un-withhold it, and the stub is the
    unrestricted server), Crew's identity-bound servers withheld from the
    spec-described half, and the control-plane element carrying this session's
    identity.

    The restriction is ``whole-server``: every server the spec narrows per tool is
    withheld WHOLE, Crew's own control plane included, and ``denied_tools`` stays
    empty. Muse's permission request carries no structured ``(server, tool)``
    identity -- only the flat MSP ``toolName`` and the model's ``rawInput`` -- so a
    per-call refusal has nothing to key on, and withholding the server is the only
    faithful action. See the module docstring for the adapter source behind that.

    Blocking (parses the agent spec once), so callers run it off the event loop.
    """
    projection = session_mcp_projection(
        agent,
        stub_server_names=stub_server_names,
        work_dir=work_dir,  # type: ignore[arg-type]
    )
    narrowed = frozenset(server for server, _tool in projection.disabled_tools)
    out = place_single_binary_array(
        projection,
        label="muse",
        unhonoured=narrowed,
        stub_elements=stub_elements,
        session_key=session_key,
        channel_id=channel_id,
        session_token=session_token,
    )
    return SessionProjection(
        params={"mcpServers": out},
        disabled_servers=projection.disabled_servers,
        restricted_servers=narrowed,
        unhonoured_servers=narrowed,
        zero_tools=projection.zero_tools,
        derived_spec_snapshot=projection.derived_spec_snapshot,
    )


class MuseMirror(AgentConfigMirror):
    """Projects the agent spec onto ``muse-code-acp``."""

    backend = ACP_BACKEND_MUSE

    def rulings(self) -> Mapping[Concern, Ruling]:
        return {
            Concern.MCP_SERVERS: Ruling(
                _D.DELIVERED,
                "the session/new + session/load mcpServers array, translated by "
                "acp.session_mcp.session_mcp_servers with NO new translator and then "
                "narrowed the way the sibling single-binary harnesses narrow it. The "
                "channel is measured as a ROUND TRIP rather than as an accepted "
                "element (Track A, muse-code-ref/reports/trackA-REPORT.md §2b): driven "
                "against this host's Muse 1.4.11 through the fork adapter "
                "@bex-co/muse-code-acp, the element Crew already emits "
                "(acp.session_mcp.acp_server_element) is merged into the adapter's "
                "private XDG overlay (muse-code-acp/src/mcp-overlay.ts "
                "createMuseMcpOverlay), listed by the model, and CALLED -- "
                "mcp__probeping__ping returned pong and the turn ended end_turn. Its "
                "initialize advertises mcpCapabilities of http with no stdio flag, "
                "which is not a refusal: ACP's McpCapabilities schema names only the "
                "OPTIONAL transports, and stdio is the baseline every v1 agent MUST "
                "serve. Crew writes no Muse config file -- the overlay is the adapter's "
                "own per-session temp copy built from the array Crew sends. The "
                "control-plane element carries KIROCREW_SESSION_KEY because the "
                "adapter honours the element's own env map (mcp-overlay.ts:37) and the "
                "child inherits the adapter's ambient env (mcp-overlay.ts:175), so the "
                "element env is what makes the credential session-scoped; that "
                "credential rides ONLY kirocrew-core and kirocrew-cron, the entries "
                "the translation replaces from the managed source. Every OTHER managed "
                "Crew server is withheld rather than mounted credential-less "
                "(identity_bound_crew_servers), and the array and withhold set come "
                "from ONE parse of the spec",
            ),
            Concern.TOOL_ALLOWLIST: Ruling(
                _D.TRANSLATED,
                "into the allowlist that decides which servers enter the array -- the "
                "shared translation's own rule, not a second one here. kiro-cli "
                "parity: a server the spec declares but never references is not "
                "mounted. It carries the same residual claude and codex do: an "
                "@server/tool grant narrows to one tool on kiro-cli but mounts the "
                "whole server here, because the tool set is not knowable without "
                "connecting",
            ),
            Concern.DENIED_TOOLS: Ruling(
                _D.TRANSLATED,
                "by withholding the server it narrows, Crew's own control plane "
                "included, and declared as per_tool_deny=whole-server on the "
                "projection record. A per-call refusal is NOT reachable on Muse: its "
                "session/request_permission carries only the flat MSP toolName string "
                "and the model-authored rawInput "
                "(muse-code-acp/src/muse-permissions.ts approvalToPermissionRequest), "
                "with no structured (server, tool) identity and no _meta field naming "
                "the MCP server. The mcp__<server>__<tool> string Track A observed is "
                "the host-formed display NAME on the tool_call frame, not an identity "
                "Crew consumes, and scraping a server out of it would be trusting a "
                "host/model-authored string -- below goose's own 'trust only the "
                "structured _meta pair' bar, which Muse does not even offer. So a "
                "narrowed server is withheld whole: an availability cost is the honest "
                "price of a deny channel this transport does not have, and "
                "reachability of a tool the user switched off is not. `disabled` needs "
                "nothing here -- build_agent_config strips a disabled server's @alias "
                "from `tools`, and the allowlist mounts nothing `tools` does not name",
            ),
            Concern.AUTO_APPROVE: Ruling(
                _D.WITHHELD,
                "Muse's approval granularity is a session-level nativeApprovalPolicy "
                "(allowAll | promptUnmatched | onRequest | denyUnmatched), not a "
                "per-tool value, and the only policy Crew seeds is promptUnmatched -- "
                "the one that asks. An auto-approve projection would have to name "
                "allowAll, which suppresses the session/request_permission frames the "
                "host gate is reached through, skipping Crew's permission gate, its "
                "governance ceiling and its SEL audit. Every MCP call must reach the "
                "host gate",
            ),
            Concern.MODEL: Ruling(
                _D.DELIVERED,
                "not through this mirror: Muse advertises its own model catalog on "
                "session/new configOptions and the resolved model is set as a session "
                "config option, from that advertised set. Named here rather than left "
                "out so a reader does not read this mirror's silence as the model "
                "being dropped",
            ),
            Concern.MODEL_ALLOWLIST: Ruling(
                _D.WITHHELD,
                "the direction is reversed, as it is on codex and goose: Muse "
                "advertises its own provider and model vocabulary on session/new "
                "(muse-wonder-1.0, muse-spark-1.3, ... per Track A), and that list is "
                "the only source of ids the session accepts. A Crew-side list would "
                "be a second answer to a question the session already answers, and "
                "would offer ids Muse refuses",
            ),
            Concern.PERMISSION_MODE: Ruling(
                _D.WITHHELD,
                "not projected as spec data. The mode Crew requires travels as the "
                "nativeApprovalPolicy seed at session/new (_meta muse/approval), read "
                "back off the session response before the first prompt; putting the "
                "spec's defaultMode here as well would give one guarantee two owners, "
                "and honouring a spec-requested mode would let an agent file widen a "
                "Muse session past the boundary that makes the harness offerable once "
                "its routing is verified",
            ),
            Concern.PROMPT: Ruling(
                _D.WITHHELD,
                "not a mirror concern on any backend: the prompt reaches every harness "
                "as ordinary prompt text in the [AGENT SYSTEM PROMPT] context block, "
                "which is backend-agnostic and already works",
            ),
            Concern.RESOURCES: Ruling(
                _D.WITHHELD,
                "same as PROMPT -- steering files are injected as context text, not "
                "projected into a backend's config, and the array carries servers "
                "rather than resources",
            ),
            Concern.HOOKS: Ruling(
                _D.TRANSLATED,
                "the ACP session/new element set has no hooks field, so the adapter "
                "never receives the spec's hooks block, and Crew's turn loop runs it "
                "instead (agent_sdk/spec_hooks.py, ACP_BACKENDS_CREW_FIRES_SPEC_HOOKS), "
                "as it does for KAS and goose. That is sound here because every tool "
                "call arrives as a session/request_permission, which is where a "
                "PreToolUse hook runs and can block. A tool matcher is written in "
                "kiro-cli's names; the tool name Muse states is mapped back to them "
                "(acp/harness_tool_names.py). This covers the chat, subagent and "
                "task-runner turn loops; a channel-agent turn runs no script hooks on "
                "any backend. Crew's OWN hooks are unaffected and work on this backend "
                "already",
            ),
        }

    def session_params(
        self,
        agent: str | None,
        *,
        stub_server_names: Collection[str] = (),
        work_dir: object = None,
        session_key: str = "",
        channel_id: str = "",
        session_token: str = "",
        **kwargs: object,
    ) -> dict[str, object]:
        """The wire face: the ``mcpServers`` array for this Muse session.

        ``permission_surface_owned`` is accepted and IGNORED (it arrives in
        ``kwargs``), which is the documented behaviour for a mirror outside claude's
        class. That flag exists because claude's permission surface is a file Crew
        may not own, so a pre-approved tool there never sends
        ``session/request_permission``. Muse has no such file in play: its approval
        policy is seeded at ``session/new`` and read back off the session response
        before the first prompt, so a session that reaches this point is one whose
        every tool call asks.

        ``stub_elements`` are the shared gateway's broker stubs for this session,
        which the caller holds and this mirror places, so the withhold rule covers
        both halves of the array -- see :func:`muse_projection`.

        Blocking -- it reads the agent spec. The caller warms this on the Muse spawn
        path and serves the shared ``session/new`` call site from that cache
        (harness-parity H13).
        """
        stubs = kwargs.get("stub_elements") or ()
        return self.session_projection(
            agent,
            stub_server_names=stub_server_names,
            stub_elements=stubs if isinstance(stubs, (list, tuple)) else (),
            work_dir=work_dir,
            session_key=session_key,
            channel_id=channel_id,
            session_token=session_token,
        ).params

    def session_projection(
        self,
        agent: str | None,
        *,
        stub_server_names: Collection[str] = (),
        stub_elements: Collection[Mapping[str, Any]] = (),
        work_dir: object = None,
        session_key: str = "",
        channel_id: str = "",
        session_token: str = "",
        **kwargs: object,
    ) -> SessionProjection:
        """The structured face: :func:`muse_projection`, with ``kwargs`` ignored as
        :meth:`session_params` documents. ``denied_tools`` is empty on this backend
        by decision, not by default -- see the ``disabledTools`` ruling."""
        del kwargs
        return muse_projection(
            agent,
            stub_server_names=stub_server_names,
            stub_elements=stub_elements,
            work_dir=work_dir,
            session_key=session_key,
            channel_id=channel_id,
            session_token=session_token,
        )
