"""Muse's permission routing on the runtime driver: seed, read-back, tripwire.

The goose mechanism (``Routing.VERIFIED_SEEDED_SETTINGS``) on ``AcpRuntime``: the
required approval policy is SUPPLIED as the session is created, in ``session/new``'s
``_meta`` (``SessionExtras.session_meta``), and READ BACK out of the adapter's own
``configOptions`` on the same response before the first prompt. These tests drive
``create_session`` against a recorded control plane and assert, from the wire:

* the seed goes out, under the adapter's key, with the routing table's value;
* a response that reads the value back opens the session with no extra write;
* a response that reads anything else, or nothing, REFUSES and tears the session
  down -- the fail-closed direction the mechanism exists for;
* the kiro path's ``session/new`` is byte-identical (no ``_meta`` appears);
* a mid-session ``config_option_update`` that no longer carries the value marks the
  handle, schedules its eviction, and makes the next prompt refuse.

What is live-proven and NOT re-asserted here: that Muse 1.4.11 honours the policy per
shell command (``test/fixtures/acp_frames/muse/``). These are the client's half.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kiro_crew.acp._dispatch import attach_kas_custom_agents, build_session_new_params
from kiro_crew.acp.client import AcpToolGateUnroutable
from kiro_crew.acp.runtime import AcpRuntime
from kiro_crew.acp.session_handle import AcpSessionHandle
from kiro_crew.acp.types import (
    ACP_BACKEND_KIRO,
    ACP_BACKEND_MUSE,
    METHOD_SESSION_CLOSE,
    METHOD_SESSION_NEW,
    METHOD_SET_CONFIG_OPTION,
    JsonRpcMessage,
)

_SEED = {"muse/approval": {"policy": "promptUnmatched", "profile": ":ask-me"}}


def _policy_option(current: str | None) -> dict[str, Any]:
    option: dict[str, Any] = {
        "id": "nativeApprovalPolicy",
        "name": "Native approval policy",
        "type": "select",
        "options": [
            {"value": "onRequest", "name": "onRequest"},
            {"value": "promptUnmatched", "name": "promptUnmatched"},
            {"value": "denyUnmatched", "name": "denyUnmatched"},
            {"value": "allowAll", "name": "allowAll"},
        ],
    }
    if current is not None:
        option["currentValue"] = current
    return option


def _profile_option(current: str | None) -> dict[str, Any]:
    option: dict[str, Any] = {
        "id": "nativePermissionProfile",
        "name": "Native permission profile",
        "type": "select",
        "options": [
            {"value": ":auto-review", "name": "Auto-review"},
            {"value": ":ask-me", "name": "Ask me"},
            {"value": ":unrestricted", "name": "Unrestricted"},
            {"value": ":read-only", "name": "Read-only"},
        ],
    }
    if current is not None:
        option["currentValue"] = current
    return option


def _session_new_response(
    policy: str | None,
    *,
    with_option: bool = True,
    profile: str | None = ":ask-me",
    with_profile: bool = True,
) -> dict[str, Any]:
    """What the fork adapter answers ``session/new`` with on 1.4.11 (shape per the corpus).

    Both seeded options are reported: the approval policy and the permission profile.
    ``profile`` defaults to the required ``:ask-me`` so a test that only varies the
    policy keeps the profile valid; ``with_profile=False`` drops the profile option
    entirely (an adapter that does not implement the second half of the seam)."""
    options: list[dict[str, Any]] = [
        {"id": "mode", "type": "select", "currentValue": "default", "options": []},
        {"id": "model", "type": "select", "currentValue": "muse-spark-1.2", "options": []},
    ]
    if with_option:
        options.append(_policy_option(policy))
    if with_profile:
        options.append(_profile_option(profile))
    return {
        "sessionId": "muse-session-1",
        "modes": {
            "currentModeId": "default",
            "availableModes": [{"id": "default", "name": "Default"}],
        },
        "configOptions": options,
    }


class _ControlPlane:
    """Record the control-plane writes a session start makes, answer ``session/new``."""

    def __init__(self, new_response: dict[str, Any]) -> None:
        self._new_response = new_response
        self.methods: list[str] = []
        self.params: list[dict[str, Any]] = []

    def install(self, rt: AcpRuntime, stack: ExitStack) -> None:
        async def _send_and_await(method, params, timeout=None):
            self.methods.append(method)
            self.params.append(params)
            if method == METHOD_SESSION_NEW:
                return self._new_response
            return {}

        async def _send_request(method, params, **_kw):
            self.methods.append(method)
            self.params.append(params)
            return 999

        async def _send_notification(method, params):
            self.methods.append(method)
            self.params.append(params)

        async def _wait_for_response(_self, req_id, timeout=None):
            return {}

        stack.enter_context(patch.object(rt, "_send_and_await", _send_and_await))
        stack.enter_context(patch.object(rt, "send_request", _send_request))
        stack.enter_context(patch.object(rt, "send_notification", _send_notification))
        stack.enter_context(
            patch.object(AcpSessionHandle, "_wait_for_response", _wait_for_response)
        )

    def params_for(self, method: str) -> list[dict[str, Any]]:
        return [p for m, p in zip(self.methods, self.params) if m == method]


def _runtime_for(backend: str) -> AcpRuntime:
    rt = AcpRuntime(work_dir="/tmp", acp_backend=backend, expect_mcp_reports=False)
    proc = MagicMock()
    proc.stdout = None
    proc.stdin = MagicMock()
    proc.returncode = None
    proc.pid = 4343
    rt._process = proc
    rt._pid = 4343
    rt._initialized = True
    return rt


class TestTheSeedTravelsOnSessionNew:
    @pytest.mark.asyncio
    async def test_the_seed_is_the_routing_tables_value_under_the_adapters_key(self):
        rt = _runtime_for(ACP_BACKEND_MUSE)
        plane = _ControlPlane(_session_new_response("promptUnmatched"))
        with ExitStack() as stack:
            plane.install(rt, stack)
            await rt.create_session(cwd="/w", agent="kirocrew")
        (params,) = plane.params_for(METHOD_SESSION_NEW)
        assert params["_meta"] == _SEED
        assert "cwd" in params and "mcpServers" in params

    @pytest.mark.asyncio
    async def test_the_kiro_path_sends_no_meta(self):
        """Byte-identical for the host that needs nothing at creation (H13)."""
        rt = _runtime_for(ACP_BACKEND_KIRO)
        plane = _ControlPlane({"sessionId": "sid-kiro"})
        with ExitStack() as stack:
            plane.install(rt, stack)
            await rt.create_session(cwd="/w", agent="kirocrew")
        (params,) = plane.params_for(METHOD_SESSION_NEW)
        assert "_meta" not in params


class TestTheReadBackDecides:
    @pytest.mark.asyncio
    async def test_the_required_value_read_back_opens_the_session_with_no_extra_write(self):
        rt = _runtime_for(ACP_BACKEND_MUSE)
        plane = _ControlPlane(_session_new_response("promptUnmatched"))
        with ExitStack() as stack:
            plane.install(rt, stack)
            handle = await rt.create_session(cwd="/w", agent="kirocrew")
        assert handle.session_id == "muse-session-1"
        # Nothing is WRITTEN on this mechanism: the seed went out with the request and
        # the response already carried the resolved value.
        assert METHOD_SET_CONFIG_OPTION not in plane.methods
        assert METHOD_SESSION_CLOSE not in plane.methods
        assert handle.seeded_setting_lost == ""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("read_back", ["onRequest", "allowAll", "denyUnmatched"])
    async def test_any_other_value_refuses_and_tears_the_session_down(self, read_back):
        """The adapter resolved something else -- an override above the seed, or an
        adapter that ignored it. Observed, not corrected: refuse and evict."""
        rt = _runtime_for(ACP_BACKEND_MUSE)
        plane = _ControlPlane(_session_new_response(read_back))
        with ExitStack() as stack:
            plane.install(rt, stack)
            with pytest.raises(AcpToolGateUnroutable) as excinfo:
                await rt.create_session(cwd="/w", agent="kirocrew")
        assert "nativeApprovalPolicy" in str(excinfo.value)
        assert read_back in str(excinfo.value)
        assert METHOD_SET_CONFIG_OPTION not in plane.methods
        # session/new succeeded in the adapter, so the refusal evicts it rather than
        # leaking a live session in the shared process.
        assert plane.methods[-1] == METHOD_SESSION_CLOSE
        assert plane.params[-1] == {"sessionId": "muse-session-1"}
        assert "muse-session-1" not in rt._session_queues

    @pytest.mark.asyncio
    async def test_a_missing_option_refuses_rather_than_reading_as_routed(self):
        """ "Cannot tell" is not "armed": an adapter that advertises no policy option
        (one that does not implement the seam) is refused, not trusted."""
        rt = _runtime_for(ACP_BACKEND_MUSE)
        plane = _ControlPlane(_session_new_response(None, with_option=False))
        with ExitStack() as stack:
            plane.install(rt, stack)
            with pytest.raises(AcpToolGateUnroutable) as excinfo:
                await rt.create_session(cwd="/w", agent="kirocrew")
        assert "does not carry 'nativeApprovalPolicy'" in str(excinfo.value)
        assert plane.methods[-1] == METHOD_SESSION_CLOSE

    @pytest.mark.asyncio
    async def test_an_option_without_a_current_value_refuses(self):
        rt = _runtime_for(ACP_BACKEND_MUSE)
        plane = _ControlPlane(_session_new_response(None))
        with ExitStack() as stack:
            plane.install(rt, stack)
            with pytest.raises(AcpToolGateUnroutable):
                await rt.create_session(cwd="/w", agent="kirocrew")
        assert plane.methods[-1] == METHOD_SESSION_CLOSE

    @pytest.mark.asyncio
    async def test_policy_right_but_profile_missing_refuses(self):
        """Contract item 5: BOTH settings are required read back. The policy prompts
        the shell tool, but without the Ask-me profile the built-in reviewer clears
        MCP tool calls -- so an adapter that reports the policy and NOT the profile is
        refused, naming the missing profile option."""
        rt = _runtime_for(ACP_BACKEND_MUSE)
        plane = _ControlPlane(_session_new_response("promptUnmatched", with_profile=False))
        with ExitStack() as stack:
            plane.install(rt, stack)
            with pytest.raises(AcpToolGateUnroutable) as excinfo:
                await rt.create_session(cwd="/w", agent="kirocrew")
        assert "does not carry 'nativePermissionProfile'" in str(excinfo.value)
        assert plane.methods[-1] == METHOD_SESSION_CLOSE
        assert "muse-session-1" not in rt._session_queues

    @pytest.mark.asyncio
    @pytest.mark.parametrize("read_back", [":auto-review", ":unrestricted", ":read-only"])
    async def test_policy_right_but_profile_wrong_refuses(self, read_back):
        """The profile resolved to something other than Ask-me -- the default
        Auto-review, or any non-human-reviewer profile. Observed, not corrected:
        refuse and evict, naming the profile option and the value seen."""
        rt = _runtime_for(ACP_BACKEND_MUSE)
        plane = _ControlPlane(_session_new_response("promptUnmatched", profile=read_back))
        with ExitStack() as stack:
            plane.install(rt, stack)
            with pytest.raises(AcpToolGateUnroutable) as excinfo:
                await rt.create_session(cwd="/w", agent="kirocrew")
        assert "nativePermissionProfile" in str(excinfo.value)
        assert read_back in str(excinfo.value)
        assert METHOD_SET_CONFIG_OPTION not in plane.methods
        assert plane.methods[-1] == METHOD_SESSION_CLOSE
        assert "muse-session-1" not in rt._session_queues

    @pytest.mark.asyncio
    async def test_both_right_opens_the_session_with_no_extra_write(self):
        """policy right + profile right -> accept, exactly as the single-key seam did
        with the policy alone. Nothing is written; the response carried both values."""
        rt = _runtime_for(ACP_BACKEND_MUSE)
        plane = _ControlPlane(_session_new_response("promptUnmatched", profile=":ask-me"))
        with ExitStack() as stack:
            plane.install(rt, stack)
            handle = await rt.create_session(cwd="/w", agent="kirocrew")
        assert handle.session_id == "muse-session-1"
        assert METHOD_SET_CONFIG_OPTION not in plane.methods
        assert METHOD_SESSION_CLOSE not in plane.methods
        assert handle.seeded_setting_lost == ""


def _config_update(policy: str = "promptUnmatched", profile: str = ":ask-me") -> JsonRpcMessage:
    """A ``config_option_update`` carrying BOTH seeded options. Vary either to drive
    a tripwire on that key; the other stays at its required value by default."""
    return JsonRpcMessage(
        method="session/update",
        params={
            "sessionId": "muse-session-1",
            "update": {
                "sessionUpdate": "config_option_update",
                "configOptions": [_policy_option(policy), _profile_option(profile)],
            },
        },
    )


class TestTheTripwireHoldsTheSessionThere:
    @pytest.mark.asyncio
    async def test_a_mid_session_change_marks_evicts_and_refuses_the_next_prompt(self):
        rt = _runtime_for(ACP_BACKEND_MUSE)
        plane = _ControlPlane(_session_new_response("promptUnmatched"))
        with ExitStack() as stack:
            plane.install(rt, stack)
            handle = await rt.create_session(cwd="/w", agent="kirocrew")
            evict = AsyncMock()
            stack.enter_context(patch.object(rt, "terminate_session", evict))
            stack.enter_context(patch("kiro_crew.sel.sel", return_value=MagicMock()))
            events = handle._handle_update(_config_update("allowAll"))
            # Let the scheduled eviction and audit run.
            await asyncio.sleep(0)
            await asyncio.gather(*list(handle._audit_tasks), return_exceptions=True)
        assert events == []
        assert "allowAll" in handle.seeded_setting_lost
        evict.assert_awaited_once_with("muse-session-1")
        with pytest.raises(AcpToolGateUnroutable):
            async for _ in handle.prompt("hello"):
                pass

    @pytest.mark.asyncio
    async def test_a_mid_session_profile_change_also_trips_the_wire(self):
        """The tripwire fires on EITHER seeded setting changing. Here the POLICY stays
        promptUnmatched but the PROFILE is flipped off Ask-me -- the built-in reviewer
        would start clearing MCP tool calls again -- so the session is marked, evicted
        and the next prompt refuses, naming the profile value seen."""
        rt = _runtime_for(ACP_BACKEND_MUSE)
        plane = _ControlPlane(_session_new_response("promptUnmatched"))
        with ExitStack() as stack:
            plane.install(rt, stack)
            handle = await rt.create_session(cwd="/w", agent="kirocrew")
            evict = AsyncMock()
            stack.enter_context(patch.object(rt, "terminate_session", evict))
            stack.enter_context(patch("kiro_crew.sel.sel", return_value=MagicMock()))
            events = handle._handle_update(_config_update(profile=":auto-review"))
            await asyncio.sleep(0)
            await asyncio.gather(*list(handle._audit_tasks), return_exceptions=True)
        assert events == []
        assert ":auto-review" in handle.seeded_setting_lost
        assert "nativePermissionProfile" in handle.seeded_setting_lost
        evict.assert_awaited_once_with("muse-session-1")
        with pytest.raises(AcpToolGateUnroutable):
            async for _ in handle.prompt("hello"):
                pass

    @pytest.mark.asyncio
    async def test_an_update_that_still_reads_the_value_is_a_no_op(self):
        rt = _runtime_for(ACP_BACKEND_MUSE)
        plane = _ControlPlane(_session_new_response("promptUnmatched"))
        with ExitStack() as stack:
            plane.install(rt, stack)
            handle = await rt.create_session(cwd="/w", agent="kirocrew")
            evict = AsyncMock()
            stack.enter_context(patch.object(rt, "terminate_session", evict))
            handle._handle_update(_config_update("promptUnmatched"))
            await asyncio.sleep(0)
        assert handle.seeded_setting_lost == ""
        evict.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_second_losing_update_does_not_evict_twice(self):
        rt = _runtime_for(ACP_BACKEND_MUSE)
        plane = _ControlPlane(_session_new_response("promptUnmatched"))
        with ExitStack() as stack:
            plane.install(rt, stack)
            handle = await rt.create_session(cwd="/w", agent="kirocrew")
            evict = AsyncMock()
            stack.enter_context(patch.object(rt, "terminate_session", evict))
            stack.enter_context(patch("kiro_crew.sel.sel", return_value=MagicMock()))
            handle._handle_update(_config_update("allowAll"))
            handle._handle_update(_config_update("onRequest"))
            await asyncio.sleep(0)
            await asyncio.gather(*list(handle._audit_tasks), return_exceptions=True)
        assert evict.await_count == 1

    @pytest.mark.asyncio
    async def test_the_kiro_path_never_reads_a_config_update_as_a_tripwire(self):
        rt = _runtime_for(ACP_BACKEND_KIRO)
        plane = _ControlPlane({"sessionId": "sid-kiro"})
        with ExitStack() as stack:
            plane.install(rt, stack)
            handle = await rt.create_session(cwd="/w", agent="kirocrew")
            evict = AsyncMock()
            stack.enter_context(patch.object(rt, "terminate_session", evict))
            msg = _config_update("allowAll")
            msg.params["sessionId"] = "sid-kiro"
            handle._handle_update(msg)
            await asyncio.sleep(0)
        assert handle.seeded_setting_lost == ""
        evict.assert_not_awaited()


class TestTheSharedBuilderMergesTheSeed:
    def test_none_adds_no_key(self):
        assert "_meta" not in build_session_new_params("/w", session_meta=None)
        assert "_meta" not in build_session_new_params("/w", session_meta={})

    def test_the_seed_is_merged_beside_another_envelope_not_over_it(self):
        params = build_session_new_params(
            "/w",
            kas_custom_agents=[{"id": "kirocrew", "name": "kirocrew"}],
            session_meta=_SEED,
        )
        assert params["_meta"]["muse/approval"] == _SEED["muse/approval"]
        # The KAS envelope survives alongside, whichever key it uses.
        other = {k: v for k, v in params["_meta"].items() if k != "muse/approval"}
        assert other, "the KAS envelope was dropped by the merge"

    def test_a_later_envelope_does_not_shadow_the_seed_either(self):
        params = build_session_new_params("/w", session_meta=_SEED)
        attach_kas_custom_agents(params, [{"id": "kirocrew", "name": "kirocrew"}])
        assert params["_meta"]["muse/approval"] == _SEED["muse/approval"]
