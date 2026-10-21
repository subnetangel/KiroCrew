"""MuseHarness: the two-component spawn, and the dormant-state invariants.

Muse Code is codex-shaped (one Node adapter, N sessions over one ``muse serve``,
driven through ``AcpRuntime``) but with pi's TWO independently-absent components: the
``muse-code-acp`` adapter and the ``muse`` host it drives. This file proves the spawn
half -- that both components resolve, that the not-found message names whichever is
absent, and that the resolved host is forwarded to the adapter -- and pins the
DORMANT invariants, which are the whole point of how Muse landed: its routing is
``UNVERIFIED``, so it is not selectable, not enforced, and carries no credential mask,
and each of those is asserted here so a later change that flips one has to flip the
test that says why it was off.

The seam-level contract every harness answers is in ``test_acp_harness_contract.py``;
what is here is true of Muse and of nothing else.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kiro_crew.acp.harness import harness_for
from kiro_crew.acp.harness import muse as harness_mod
from kiro_crew.acp.harness.base import SpawnContext, TeardownPolicy
from kiro_crew.acp.harness.muse import MuseHarness
from kiro_crew.acp.types import (
    ACP_BACKEND_MUSE,
    ACP_BACKENDS_ACP_RUNTIME,
    ACP_BACKENDS_HARNESS_OWNED_SESSIONS,
    ACP_BACKENDS_SESSION_EVICTION,
    ACP_BACKENDS_SESSION_MCP_ARRAY,
    ACP_BACKENDS_SESSION_SHARING,
    METHOD_SESSION_CLOSE,
    METHOD_SESSION_UPDATE,
)
from kiro_crew.agent_sdk import backend_install
from kiro_crew.agent_sdk.backends import Routing, routing_for
from kiro_crew.agent_sdk.host_auth import declaration_for

_ADAPTER_ARGV = ["/opt/bin/node", "/opt/lib/muse-code-acp/index.js"]
_HOST_BIN = "/opt/bin/muse"
_SEARCH = "/opt/bin"


def _ctx(**over):
    base = {
        "agent": "kirocrew",
        "work_dir": "/w",
        "model": None,
        "environ": {},
        "home": Path("/h"),
        "sandbox_mode": "standard",
    }
    base.update(over)
    return SpawnContext(**base)


@pytest.fixture()
def adapter() -> MuseHarness:
    return MuseHarness()


def _patch_both(adapter_result, host_result):
    """Patch BOTH component resolvers on the client the harness reads at call time."""
    from kiro_crew.acp import client as client_mod

    return (
        patch.object(client_mod, "_resolve_muse_code_acp_bin", return_value=adapter_result),
        patch.object(client_mod, "_resolve_muse_host_bin", return_value=host_result),
    )


class TestRegistry:
    def test_harness_for_muse_returns_this_harness(self):
        resolved = harness_for(ACP_BACKEND_MUSE)
        assert isinstance(resolved, MuseHarness)
        assert resolved.backend == ACP_BACKEND_MUSE

    def test_registration_does_not_wait_on_the_routing_set(self):
        """Registered and testable while dormant, exactly like codex.

        A harness is written and tested before ``ACP_BACKENDS_ACP_RUNTIME`` routes a
        session onto the runtime; gating registration on the set would make it
        unreachable to its own tests. Muse IS in the set (it is AcpRuntime-shaped),
        and the registration is what the test above reaches regardless.
        """
        assert isinstance(harness_for(ACP_BACKEND_MUSE), MuseHarness)
        assert ACP_BACKEND_MUSE in ACP_BACKENDS_ACP_RUNTIME


class TestTwoComponentSpawn:
    @pytest.mark.asyncio
    async def test_the_adapter_entry_is_the_whole_argv(self, adapter):
        a, h = _patch_both((_ADAPTER_ARGV, _SEARCH), (_HOST_BIN, _SEARCH))
        with a, h:
            plan = await adapter.resolve_spawn(_ctx())
        assert plan.argv == _ADAPTER_ARGV

    @pytest.mark.asyncio
    async def test_no_agent_or_model_flag_is_appended(self, adapter):
        """The adapter takes no argv of its own: any invocation blocks on stdin."""
        a, h = _patch_both((_ADAPTER_ARGV, _SEARCH), (_HOST_BIN, _SEARCH))
        with a, h:
            plan = await adapter.resolve_spawn(_ctx())
        assert "--agent" not in plan.argv
        assert "--model" not in plan.argv
        assert "kirocrew" not in plan.argv

    @pytest.mark.asyncio
    async def test_crew_never_owns_this_host_credential(self, adapter):
        """Muse authenticates itself, so a process expecting a Crew callback would wait."""
        a, h = _patch_both((_ADAPTER_ARGV, _SEARCH), (_HOST_BIN, _SEARCH))
        with a, h:
            plan = await adapter.resolve_spawn(_ctx())
        assert plan.host_auth is False

    @pytest.mark.asyncio
    async def test_no_credential_mask_is_applied_while_unenforced(self, adapter):
        """The dormant invariant that is a security claim, not a simplification.

        Muse's routing is ``UNVERIFIED``, so it is outside ``ENFORCED_ROUTINGS`` and
        its tools are not made to ask -- there is no enforced boundary for a mask to
        compensate for. An empty mask here is correct; a non-empty one would assert a
        control that never runs. (Codex, which IS enforced, resolves one here.)
        """
        a, h = _patch_both((_ADAPTER_ARGV, _SEARCH), (_HOST_BIN, _SEARCH))
        with a, h:
            plan = await adapter.resolve_spawn(_ctx())
        assert plan.extra_hidden_dirs == ()
        assert plan.extra_expose_files == ()

    @pytest.mark.asyncio
    async def test_a_missing_adapter_aborts_with_its_own_searched_path(self, adapter):
        """The adapter half absent: the message names the adapter and the npm command."""
        from kiro_crew.acp.session_handle import AcpRuntimeError

        a, h = _patch_both((None, "/one:/two"), (_HOST_BIN, _SEARCH))
        with a, h:
            with pytest.raises(AcpRuntimeError) as exc:
                await adapter.resolve_spawn(_ctx())
        message = str(exc.value)
        assert "muse-code-acp" in message
        assert "@bex-co/muse-code-acp" in message
        assert "MUSE_CODE_ACP_BIN" in message
        assert "/one" in message and "/two" in message

    @pytest.mark.asyncio
    async def test_a_missing_host_aborts_with_the_host_message(self, adapter):
        """The host half absent: a SEPARATE message names ``muse``, not the adapter.

        Either component can be absent on its own, so the two halves get two
        diagnoses -- the adapter's an npm install, the host's Meta's own installer.
        """
        from kiro_crew.acp.session_handle import AcpRuntimeError

        a, h = _patch_both((_ADAPTER_ARGV, _SEARCH), (None, "/three"))
        with a, h:
            with pytest.raises(AcpRuntimeError) as exc:
                await adapter.resolve_spawn(_ctx())
        message = str(exc.value)
        assert "muse" in message
        assert "MUSE_CODE_EXECUTABLE" in message
        assert "/three" in message
        # It does not misreport the adapter half as the absent one.
        assert "@bex-co/muse-code-acp" not in message

    @pytest.mark.asyncio
    async def test_both_resolvers_run_once(self, adapter):
        a, h = _patch_both((_ADAPTER_ARGV, _SEARCH), (_HOST_BIN, _SEARCH))
        with a as adapter_r, h as host_r:
            await adapter.resolve_spawn(_ctx())
        assert adapter_r.call_count == 1
        assert host_r.call_count == 1

    @pytest.mark.asyncio
    async def test_the_resolved_host_is_forwarded_to_the_adapter(self, adapter):
        """The adapter reads ``MUSE_CODE_EXECUTABLE`` to pick which ``muse`` to run.

        Crew resolves it (honouring the operator's own value) and writes the resolved
        path back, so the adapter runs exactly the binary Crew found rather than
        re-searching a PATH that may differ inside the sandbox.
        """
        a, h = _patch_both((_ADAPTER_ARGV, _SEARCH), (_HOST_BIN, _SEARCH))
        with a, h:
            await adapter.resolve_spawn(_ctx())
        env: dict[str, str] = {"PATH": "/usr/bin"}
        adapter.apply_spawn_env(env)
        assert env["MUSE_CODE_EXECUTABLE"] == _HOST_BIN
        assert env["PATH"] == "/usr/bin"


class TestHandshakeAndSeams:
    def test_the_protocol_version_is_this_harness_own_integer_literal(self, adapter):
        assert adapter.protocol_version == 1
        assert harness_mod.PROTOCOL_VERSION_MUSE == 1
        assert isinstance(adapter.protocol_version, int)
        assert not isinstance(adapter.protocol_version, str)

    def test_only_the_standard_session_update_spelling_is_accepted(self, adapter):
        aliases = adapter.notification_aliases
        assert aliases.session_update == (METHOD_SESSION_UPDATE,)
        assert aliases.subagent_list_update == ""
        assert aliases.mcp_init == ()

    def test_the_teardown_is_the_standard_close_as_a_request(self, adapter):
        assert adapter.teardown == TeardownPolicy(method=METHOD_SESSION_CLOSE, notification=False)

    def test_no_inbound_request_is_claimed(self, adapter):
        assert adapter.host_answered_methods == ()

    @pytest.mark.asyncio
    async def test_answering_one_raises_rather_than_returning_empty(self, adapter):
        with pytest.raises(NotImplementedError, match="muse"):
            await adapter.answer_request("_kiro/auth/getAccessToken")

    @pytest.mark.asyncio
    async def test_session_extras_carry_no_custom_agents(self, adapter):
        extras = await adapter.session_extras("kirocrew", work_dir="/w")
        assert extras.custom_agents is None

    def test_crew_sandbox_is_the_only_confinement(self, adapter):
        """A Node adapter carries no OS sandbox for Crew's to nest inside or defer to."""
        assert adapter.internal_sandbox is False

    def test_no_pod_home_remap(self, adapter):
        assert adapter.pod_home_remap is False

    def test_nothing_was_selected_at_spawn_to_confirm(self, adapter):
        assert adapter.verifies_agent_activation is False


class TestDormantState:
    """The invariants that make 'dormant' a named state rather than an incomplete one."""

    def test_routing_is_unverified(self):
        """UNVERIFIED resolves INDETERMINATE and refuses, and is what
        ``register_selectable_backend`` rejects -- the gate that keeps Muse off the
        switch until Track A proves the approvalMode seed and read-back."""
        assert routing_for(ACP_BACKEND_MUSE) is Routing.UNVERIFIED

    def test_it_is_in_the_transport_and_owned_sessions_sets(self):
        """What dormancy does NOT withhold: Muse is codex-shaped (AcpRuntime) and owns
        its own sessions, both decided facts rather than gated on verification."""
        assert ACP_BACKEND_MUSE in ACP_BACKENDS_ACP_RUNTIME
        assert ACP_BACKEND_MUSE in ACP_BACKENDS_HARNESS_OWNED_SESSIONS

    def test_the_measured_sets_stay_out_until_driven(self):
        """Session sharing and eviction are MEASURED claims Track A has not driven, so
        a dormant harness is not a member of either. The MCP array is the one measured
        set it HAS joined: Track A §2b drove a stdio element passed in the session/new
        array and the model called it (mcp__probeping__ping -> pong), so Muse's MCP
        projection is a mirror and it is in ACP_BACKENDS_SESSION_MCP_ARRAY."""
        assert ACP_BACKEND_MUSE not in ACP_BACKENDS_SESSION_SHARING
        assert ACP_BACKEND_MUSE not in ACP_BACKENDS_SESSION_EVICTION
        assert ACP_BACKEND_MUSE in ACP_BACKENDS_SESSION_MCP_ARRAY

    def test_the_auth_declaration_fences_its_leaf_but_carves_nothing(self):
        """own_credential_file, the leaf relocated under XDG_CONFIG_HOME, and -- because
        it is unenforced -- adapter_own_leaves empty, exactly like claude."""
        d = declaration_for(ACP_BACKEND_MUSE)
        assert d.credential_leaves == (".config/muse/auth.json",)
        assert d.home_override_env_vars == ("XDG_CONFIG_HOME",)
        assert d.override_relative_leaves == ("muse/auth.json",)
        assert d.adapter_own_leaves == ()
        assert d.host_logout_retires_children is False

    def test_the_install_probe_names_the_adapter_and_the_command(self):
        """On a host without the adapter the probe names it and the npm command; the
        host half's remedy is the Stage 5 auth declaration, not an npm line."""
        from kiro_crew.acp import client as client_mod

        a, h = _patch_both((None, _SEARCH), (None, _SEARCH))
        with a, h:
            state = backend_install._probe_muse()
        assert state.installed == backend_install.MISSING
        assert backend_install.COMPONENT_MUSE_ACP_ADAPTER in state.missing_components
        assert backend_install.COMPONENT_MUSE_HOST in state.missing_components
        assert state.install_command == client_mod.MUSE_CODE_INSTALL_COMMAND

    def test_the_probe_omits_the_npm_command_when_only_the_host_is_missing(self):
        """The npm install does not install the host, so it is not suggested for it."""
        a, h = _patch_both((_ADAPTER_ARGV, _SEARCH), (None, _SEARCH))
        with a, h:
            state = backend_install._probe_muse()
        assert state.missing_components == (backend_install.COMPONENT_MUSE_HOST,)
        assert state.install_command == ""

    def test_the_probe_reports_installed_when_both_resolve(self):
        a, h = _patch_both((_ADAPTER_ARGV, _SEARCH), (_HOST_BIN, _SEARCH))
        with a, h:
            state = backend_install._probe_muse()
        assert state.installed == backend_install.INSTALLED
        assert state.missing_components == ()
