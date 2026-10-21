"""The Muse Code ACP adapter: one Node process, N sessions, over MSP.

``@bex-co/muse-code-acp`` is a Node stdio server. It boots a single ``muse serve``
child and translates ACP onto Muse's own protocol (MSP). The ``muse`` CLI does not
serve ACP itself -- ``muse serve`` speaks MSP -- so the adapter is the transport, not
an optimization, and it is modelled on :mod:`kiro_crew.acp.harness.codex`: one adapter
process multiplexes N sessions over one ``muse serve``, which is why this harness is a
:class:`~kiro_crew.acp.harness.codex.CodexHarness`-shaped ``HarnessAdapter`` driven
through ``AcpRuntime`` rather than a per-session ``ProcessAdapter``.

TWO components, like pi rather than codex: the ``muse-code-acp`` adapter is the ACP
server, and ``muse`` is the host it drives. Either can be absent on its own, so the
resolver and the install probe each name both, and the adapter is told which ``muse``
to run through its own ``MUSE_CODE_EXECUTABLE`` -- which Crew forwards so the operator's
choice of binary is honoured rather than overridden.

Muse lands DORMANT
------------------
This harness is complete enough to spawn, but its ROUTING is
:data:`~kiro_crew.agent_sdk.backends.Routing.UNVERIFIED` (see
``agent_sdk/backends_routing.py``). MSP's ``ApprovalMode`` is a closed enum
(``allowAll | promptUnmatched | onRequest | denyUnmatched``) and the ``Session`` object
reads the effective mode back (``approvalMode: EffectiveApprovalModeState``), which is
the shape goose's ``VERIFIED_SEEDED_SETTINGS`` uses -- but whether Crew can seed
``promptUnmatched`` at ``session/start`` and read it back before the first prompt on
this host's Muse 1.4.11 is unproven (Track A holds the live captures). So:

* ``UNVERIFIED`` resolves INDETERMINATE and refuses, and
  ``register_selectable_backend`` refuses an ``UNVERIFIED`` id, so Muse is NOT in
  ``BASELINE_SELECTABLE_BACKENDS`` -- it is named in ``NOT_SHIPPED_SELECTABLE`` with
  the reason;
* it is NOT in ``tool_gate.ENFORCED_ROUTINGS``, so no credential mask is applied to its
  child. :meth:`resolve_spawn` therefore applies NO ``extra_hidden_dirs`` -- the exact
  opposite of ``CodexHarness.resolve_spawn``, and the correct answer for an unenforced
  host: a mask is the compensating control for an ENFORCED host's passive reads, and
  claiming one here would assert a control that does not run.

Flipping to selectable is one stage: prove the seed + read-back (Track A), change the
routing row to ``VERIFIED_SEEDED_SETTINGS`` with its ``("approvalMode",
"promptUnmatched")`` setting, decide the MCP projection, and remove the
``NOT_SHIPPED_SELECTABLE`` entry. Nothing in THIS file changes except that the enforced
path would then resolve a mask here the way codex does.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from kiro_crew.acp.harness._common import MembershipHarness
from kiro_crew.acp.harness.base import (
    NotificationAliases,
    SessionExtras,
    SpawnContext,
    SpawnPlan,
    TeardownPolicy,
)
from kiro_crew.acp.types import (
    ACP_BACKEND_MUSE,
    ACP_CLIENT_CAPABILITIES,
    METHOD_SESSION_CLOSE,
    METHOD_SESSION_UPDATE,
)
from kiro_crew.providers.mirrors.codex import drop_unadvertised_transports

__all__ = ["PROTOCOL_VERSION_MUSE", "MuseHarness"]

logger = logging.getLogger(__name__)

#: muse-code-acp numbers ACP revisions like codex-acp and the claude adapter, not
#: kiro-cli's date string. Kept as this harness's OWN literal even though the integer
#: matches codex's and claude's today: harness-parity H10, so a future divergence is a
#: one-line edit here rather than a silent downgrade of whichever harness moved first.
#:
#: UNVERIFIED-PENDING on the exact value. ACP v1 is ``1`` and the adapter pins the
#: ACP SDK, so ``1`` is the correct literal for ACP v1; Track A's handshake capture
#: against Muse 1.4.11 is what CONFIRMS the adapter answers with it. Should that
#: capture show a different revision, this is the one line that changes.
PROTOCOL_VERSION_MUSE = 1

#: The adapter's env override for the ``muse`` host it spawns, forwarded to the child
#: so the operator's choice of binary is carried rather than overridden.
_ENV_MUSE_CODE_EXECUTABLE = "MUSE_CODE_EXECUTABLE"


class MuseHarness(MembershipHarness):
    """The muse-code-acp host, codex-shaped and currently dormant."""

    backend = ACP_BACKEND_MUSE

    # ── Seam 1: spawn ──

    async def resolve_spawn(self, ctx: SpawnContext) -> SpawnPlan:
        """Resolve BOTH components, and forward the host binary to the adapter.

        TWO independently-absent components, so the resolution is pi-shaped: the
        ``muse-code-acp`` adapter on the Node-entry ladder, and the ``muse`` host on
        the plain-binary ladder. Both are resolved because the adapter must be told
        which ``muse`` to run, and the not-found message names whichever half is
        absent.

        NO credential mask is resolved here, and that is deliberate rather than an
        omission: this harness's routing is ``UNVERIFIED``, so it is outside
        ``tool_gate.ENFORCED_ROUTINGS`` and its privileged tools are not made to ask
        -- there is no enforced boundary for a mask to compensate for yet. When the
        routing is verified and the harness becomes enforced, this method grows the
        same ``resolve_spawn_masks`` call ``CodexHarness.resolve_spawn`` makes; until
        then an empty mask is the honest answer, not a simplification.

        The adapter takes no argv of its own: any invocation enters stdio-server mode
        and blocks on stdin, so there is no ``--agent`` (it reads no Kiro agent file)
        and no ``--model`` (the model is a per-session choice). ``host_auth`` stays
        False: Muse holds its own credential and raises nothing at Crew.
        """
        # Circular import: ``kiro_crew.acp.client`` imports the harness registry this
        # module is part of, so both resolve at call time rather than module scope.
        from kiro_crew.acp import client as client_mod
        from kiro_crew.acp.session_handle import AcpRuntimeError

        adapter_argv, adapter_search = await asyncio.to_thread(
            client_mod._resolve_muse_code_acp_bin
        )
        if not adapter_argv:
            # The wording comes from the client too, for the reason codex's does: it is
            # the operator's only instruction for fixing the install, and two
            # authorings of it drift. The search path is threaded through so a
            # "searched ..." line can never name a directory the search skipped.
            raise AcpRuntimeError(client_mod.muse_code_acp_not_found_message(adapter_search))
        host_bin, host_search = await asyncio.to_thread(client_mod._resolve_muse_host_bin)
        if not host_bin:
            # The adapter resolves but the ``muse`` it must drive does not. A separate
            # message names the HOST half, since either component can be absent alone.
            raise AcpRuntimeError(client_mod.muse_host_not_found_message(host_search))
        self._muse_host_bin = host_bin
        return SpawnPlan(argv=list(adapter_argv))

    def apply_spawn_env(
        self,
        env: dict[str, str],
        *,
        spawned_binary: str | None = None,
        cli_owned_auth: bool = False,
    ) -> None:
        """Point the adapter at the resolved ``muse`` host.

        The adapter spawns ``muse serve`` and reads ``MUSE_CODE_EXECUTABLE`` to decide
        WHICH ``muse``. Crew resolves that on the plain-binary ladder (honouring the
        operator's own ``MUSE_CODE_EXECUTABLE`` if set) and writes the resolved path
        back here, so the adapter runs exactly the binary Crew found rather than
        re-searching a PATH that may differ inside the sandbox. Set unconditionally:
        an operator's own value was already honoured by ``_resolve_muse_host_bin`` and
        is what this path points at.

        ``cli_owned_auth`` is accepted and unused: Muse authenticates itself from its
        own ``~/.config/muse/auth.json``, so there is no Crew callback to toggle.
        """
        host_bin = getattr(self, "_muse_host_bin", "")
        if host_bin:
            env[_ENV_MUSE_CODE_EXECUTABLE] = host_bin

    @property
    def verifies_agent_activation(self) -> bool:
        """No -- Muse takes no ``--agent`` at spawn, so nothing was selected to confirm."""
        return False

    # ── Seam 2: initialize ──

    @property
    def protocol_version(self) -> Any:
        return PROTOCOL_VERSION_MUSE

    @property
    def client_capabilities(self) -> dict[str, Any]:
        return ACP_CLIENT_CAPABILITIES

    # ── Seam 3: session/new and session/load extras ──

    async def session_extras(
        self,
        agent: str,
        *,
        work_dir: str | Path | None,
        mcp_gateway_overlay: Any = None,
        member_dispatch: bool = False,
        crew_panel: bool = False,
        session_key: str = "",
    ) -> SessionExtras:
        """Empty. Muse has no ``--agent`` channel to register anything on.

        Like codex, what Muse needs per session is the ``mcpServers`` array -- and
        while the harness is dormant its MCP projection is ``no-channel``, so even
        that carries nothing yet. There is no custom-agent definition to hand over.
        """
        return SessionExtras()

    def session_mcp_servers(
        self,
        requested: list[dict[str, Any]],
        *,
        agent_capabilities: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Drop the elements whose transport THIS session's adapter did not advertise.

        Muse is IN ``ACP_BACKENDS_SESSION_MCP_ARRAY`` (its channel is proven on 1.4.11
        by Track A §2b -- a stdio element passed in the ``session/new`` array was
        merged into the adapter's overlay and called), so Crew composes the array
        through ``providers/mirrors/muse.py`` and this method narrows it exactly as
        ``CodexHarness.session_mcp_servers`` does, with the SAME shared filter
        (``drop_unadvertised_transports``).

        Narrowed at session start rather than when the array was built, for a timing
        reason: the array is assembled on the spawn path, before the adapter process
        exists, while the transports it accepts are unknown until ``initialize``
        answers. Reading them here uses what this session was told, and stays a pure
        in-memory pass.

        An UNKNOWN handshake passes the array through untouched -- an absent
        ``agentCapabilities``, an absent ``mcpCapabilities``, or one that is not a
        mapping. Narrowing to nothing there would strip every tool from every session
        on a host that simply did not say. Stdio is exempt (ACP's baseline), so the
        stdio elements Crew actually emits are kept; Muse 1.4.11 advertises
        ``mcpCapabilities: {"http": true}`` with no stdio flag, which this filter
        reads correctly as full stdio support (Track A §2a).
        """
        advertised = agent_capabilities.get("mcpCapabilities")
        if not isinstance(advertised, dict) or not advertised:
            return requested
        return drop_unadvertised_transports(list(requested), dict(advertised))

    @property
    def wants_session_file_on_load(self) -> bool:
        """No -- Muse owns its session records and resolves a load from the id alone."""
        return False

    # ── Seam 4: inbound requests the host answers ──

    @property
    def host_answered_methods(self) -> tuple[str, ...]:
        """None. Muse asks Crew for nothing at the connection level.

        Empty rather than absent, so a reader can tell "this host needs no callback"
        from "nobody has checked". Muse authenticates from its own credential file and
        defines none of the ``_kiro/*`` callback vocabulary.
        """
        return ()

    async def answer_request(self, method: str) -> dict[str, Any]:
        """Never called: :attr:`host_answered_methods` is empty."""
        raise NotImplementedError(f"muse harness answers no inbound request, including {method!r}")

    @property
    def opens_external_urls(self) -> bool:
        """No -- Muse defines no ``_kiro/openExternalUrl`` sign-in channel.

        Inherited from ``MembershipHarness`` through ``ACP_BACKENDS_OPEN_EXTERNAL_URL``
        (Muse is not a member); stated here so the dormant answer is explicit.
        """
        return super().opens_external_urls

    # ── Seam 5: notification aliases ──

    @property
    def notification_aliases(self) -> NotificationAliases:
        """Plain ACP ``session/update``, with no forked spellings.

        The adapter speaks standard ACP to Crew; the ``_kiro.dev/*`` vocabulary is
        the kiro family's, reached through kiro-cli's relay, which Muse is not.
        """
        return NotificationAliases(session_update=(METHOD_SESSION_UPDATE,))

    # ── Seam 6: teardown ──

    @property
    def teardown(self) -> TeardownPolicy:
        """The standard ACP ``session/close``, as a request.

        The adapter speaks standard ACP, so the standard verb is what ends a session;
        a kiro-family extension would draw method-not-found. Sent as a REQUEST because
        the adapter answers it -- a notification would be ignored. Whether that close
        also EVICTS the session from the adapter's map is a measured claim Muse has
        not earned (it is out of ``ACP_BACKENDS_SESSION_EVICTION`` until Track A drives
        it), but the verb itself is still the correct teardown for a standard-ACP host.
        """
        return TeardownPolicy(method=METHOD_SESSION_CLOSE, notification=False)
