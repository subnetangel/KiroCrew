"""Muse's spec projection as unit behaviour: the agent spec -> a ``session/new`` array.

``providers/mirrors/muse.py`` reuses claude's translation (no new translator) and the
single-binary placement ``opencode.place_single_binary_array`` that goose also reuses,
and narrows the result against THIS session's ``initialize`` advertisement through the
shared ``drop_unadvertised_transports`` filter the way ``CodexHarness`` does. The two
facts that are Muse's own -- the ``whole-server`` per-tool deny and the control-plane
identity carriage -- are asserted here.

This is the UNIT half. The REAL-ADAPTER measurement (a stdio element passed in the
``session/new`` array is mounted and called on Muse 1.4.11) was driven in Track A
(``muse-code-ref/reports/trackA-REPORT.md`` §2b) and is not re-run here: the fork
adapter ``@bex-co/muse-code-acp`` is unpublished, so there is no entry in
``test/real_adapters/`` for a guarded lane to collect, and the parent session owns the
live drive. When the adapter is published, a ``@pytest.mark.real_adapter`` case plus a
manifest entry is the follow-up, exactly as codex has.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kiro_crew import agent as agent_mod
from kiro_crew.acp import session_mcp
from kiro_crew.acp.harness.muse import MuseHarness
from kiro_crew.acp_backends import ACP_BACKEND_MUSE, ACP_BACKENDS_SESSION_MCP_ARRAY
from kiro_crew.providers.mirrors import Concern, Disposition, mirror_for
from kiro_crew.providers.mirrors.muse import MuseMirror, muse_projection
from kiro_crew.providers.mirrors.registry import (
    PerToolDeny,
    ProjectionKind,
    projection_for,
)

#: What Track A §2b recorded Muse 1.4.11's ``initialize`` advertising. Used as the
#: live advertisement so these tests feed the filter the same shape the client does.
_MUSE_1_4_11_CAPS = {"http": True}

_CORE = {"command": "/opt/kirocrew", "args": ["mcp-core"]}
_CRON = {"command": "/opt/kirocrew", "args": ["mcp-cron"]}


@pytest.fixture
def agents_dir(tmp_path, monkeypatch):
    """Point the agent-spec resolver at a temp agents directory.

    The same seam as ``test_codex_session_mcp_projection.py`` and
    ``test_per_tool_mcp_deny_single_binary.py``: materialization would rebuild the
    managed default from bundled defaults, and these tests supply the spec.
    """
    d = tmp_path / "agents"
    d.mkdir()
    monkeypatch.setattr(agent_mod, "KIRO_AGENTS_DIR", d)
    monkeypatch.setattr(agent_mod, "_KIRO_MCP_JSON", tmp_path / "settings-mcp.json")
    monkeypatch.setattr(session_mcp, "ensure_agent_materialized", lambda _a: True)
    managed = {"kirocrew-core": dict(_CORE), "kirocrew-cron": dict(_CRON)}
    monkeypatch.setattr(
        session_mcp,
        "managed_mcp_spec_entry",
        lambda name, **kwargs: dict(managed[name]) if name in managed else None,
    )
    monkeypatch.setattr(session_mcp, "_mcp_registry_mode", lambda: False)
    return d


def _write_spec(agents_dir: Path, *, servers: dict, tools: list | None = None) -> None:
    spec: dict = {"name": "kirocrew", "mcpServers": servers}
    if tools is not None:
        spec["tools"] = tools
    (agents_dir / "kirocrew.json").write_text(json.dumps(spec), encoding="utf-8")


def _by_name(elements: list[dict]) -> dict[str, dict]:
    return {e["name"]: e for e in elements}


def _env(element: dict) -> dict[str, str]:
    return {p["name"]: p["value"] for p in element.get("env") or []}


def _muse_session_array(
    *,
    session_key: str = "",
    channel_id: str = "",
    caps: dict | None = None,
    stubs: list[dict] | None = None,
):
    """The array a Muse ``session/new`` carries, assembled the way a session is.

    Two owners in the order the runtime asks them, exactly as the codex helper does:
    the MIRROR turns the spec into elements, places the stubs and withholds; the
    HARNESS then narrows against the ``mcpCapabilities`` THIS session advertised. A
    test that means "what reaches the adapter" must run both, because the transport
    rule cannot read a live advertisement from the spawn path.
    """
    projection = muse_projection(
        "kirocrew",
        session_key=session_key,
        channel_id=channel_id,
        stub_elements=list(stubs or []),
    )
    agent_capabilities = {} if caps is None else {"mcpCapabilities": dict(caps)}
    kept = MuseHarness().session_mcp_servers(
        list(projection.params["mcpServers"]), agent_capabilities=agent_capabilities
    )
    return projection, kept


# -- membership and declaration ------------------------------------------------


class TestDeclaration:
    def test_muse_is_a_mirror_with_whole_server_deny(self):
        record = projection_for(ACP_BACKEND_MUSE)
        assert record.kind is ProjectionKind.MIRROR
        assert record.per_tool_deny is PerToolDeny.WHOLE_SERVER

    def test_the_mirror_registry_returns_the_muse_mirror(self):
        mirror = mirror_for(ACP_BACKEND_MUSE)
        assert isinstance(mirror, MuseMirror)
        assert mirror.backend == ACP_BACKEND_MUSE

    def test_muse_is_in_the_session_mcp_array_set(self):
        assert ACP_BACKEND_MUSE in ACP_BACKENDS_SESSION_MCP_ARRAY


# -- the transport filter via the harness seam ---------------------------------


class TestTransportFilter:
    """Muse 1.4.11 advertises ``{"http": true}`` with no stdio flag. Stdio is the ACP
    baseline, so Crew's stdio elements survive; a transport neither advertised nor
    baseline is dropped."""

    def test_the_crew_stdio_elements_survive_the_1_4_11_advertisement(self, agents_dir):
        _write_spec(agents_dir, servers={}, tools=["@kirocrew-core", "@kirocrew-cron"])
        _projection, kept = _muse_session_array(
            session_key="sk", channel_id="C1", caps=_MUSE_1_4_11_CAPS
        )
        names = set(_by_name(kept))
        assert "kirocrew-core" in names
        assert "kirocrew-cron" in names

    def test_an_unadvertised_sse_element_is_dropped(self, agents_dir):
        _write_spec(agents_dir, servers={}, tools=["@kirocrew-core"])
        projection, _ = _muse_session_array(
            session_key="sk", channel_id="C1", caps=_MUSE_1_4_11_CAPS
        )
        array = list(projection.params["mcpServers"])
        array.append({"name": "remote", "type": "sse", "url": "https://x", "headers": []})
        kept = MuseHarness().session_mcp_servers(
            array, agent_capabilities={"mcpCapabilities": dict(_MUSE_1_4_11_CAPS)}
        )
        assert "remote" not in _by_name(kept)
        assert "kirocrew-core" in _by_name(kept)

    def test_an_unknown_advertisement_passes_the_array_through(self, agents_dir):
        _write_spec(agents_dir, servers={}, tools=["@kirocrew-core"])
        projection, kept = _muse_session_array(session_key="sk", channel_id="C1", caps=None)
        # caps=None -> empty agent_capabilities -> pass-through (no emptying).
        assert _by_name(kept).keys() == _by_name(projection.params["mcpServers"]).keys()


# -- identity carriage on the control plane only -------------------------------


class TestControlPlaneIdentity:
    def test_the_session_key_rides_the_control_plane_element(self, agents_dir):
        _write_spec(agents_dir, servers={}, tools=["@kirocrew-core", "@kirocrew-cron"])
        _projection, kept = _muse_session_array(
            session_key="SK123", channel_id="CH1", caps=_MUSE_1_4_11_CAPS
        )
        core = _by_name(kept)["kirocrew-core"]
        assert _env(core).get("KIROCREW_SESSION_KEY") == "SK123"
        assert _env(core).get("KIROCREW_CHANNEL_ID") == "CH1"

    def test_a_third_party_server_gets_no_crew_identity(self, agents_dir):
        _write_spec(
            agents_dir,
            servers={"vendor": {"command": "/usr/bin/vendor", "args": []}},
            tools=["@kirocrew-core", "@vendor"],
        )
        _projection, kept = _muse_session_array(
            session_key="SK123", channel_id="CH1", caps=_MUSE_1_4_11_CAPS
        )
        vendor = _by_name(kept).get("vendor")
        assert vendor is not None
        assert "KIROCREW_SESSION_KEY" not in _env(vendor)


# -- whole-server deny: a narrowed server is withheld, not refused per call -----


class TestWholeServerDeny:
    def test_a_narrowed_third_party_server_is_withheld_whole(self, agents_dir):
        _write_spec(
            agents_dir,
            servers={
                "vendor": {
                    "command": "/usr/bin/vendor",
                    "args": [],
                    "disabledTools": ["dangerous"],
                }
            },
            tools=["@kirocrew-core", "@vendor"],
        )
        projection, kept = _muse_session_array(
            session_key="sk", channel_id="C1", caps=_MUSE_1_4_11_CAPS
        )
        assert "vendor" not in _by_name(kept)
        assert "vendor" in projection.restricted_servers
        assert "vendor" in projection.unhonoured_servers

    def test_the_projection_hands_the_client_no_deny_set(self, agents_dir):
        """``denied_tools`` is empty by DECISION: Muse's permission request carries no
        structured (server, tool) identity, so a per-call refusal has nothing to match
        and withholding the server whole is the only faithful action."""
        _write_spec(
            agents_dir,
            servers={
                "vendor": {
                    "command": "/usr/bin/vendor",
                    "args": [],
                    "disabledTools": ["dangerous"],
                }
            },
            tools=["@kirocrew-core", "@vendor"],
        )
        projection, _ = _muse_session_array(
            session_key="sk", channel_id="C1", caps=_MUSE_1_4_11_CAPS
        )
        assert projection.denied_tools == frozenset()


# -- the declared rulings ------------------------------------------------------


class TestRulings:
    def test_every_concern_is_ruled(self):
        rulings = MuseMirror().rulings()
        assert set(rulings) == set(Concern)
        for concern, ruling in rulings.items():
            assert ruling.reason.strip(), concern

    def test_mcp_servers_is_delivered(self):
        assert MuseMirror().rulings()[Concern.MCP_SERVERS].disposition is Disposition.DELIVERED

    def test_denied_tools_is_translated_by_withholding(self):
        ruling = MuseMirror().rulings()[Concern.DENIED_TOOLS]
        assert ruling.disposition is Disposition.TRANSLATED
        assert "whole-server" in ruling.reason

    def test_hooks_is_translated_not_no_channel(self):
        # Crew's turn loop runs the spec's hooks (ACP_BACKENDS_CREW_FIRES_SPEC_HOOKS),
        # as for KAS and goose, so this is TRANSLATED rather than a gap.
        assert MuseMirror().rulings()[Concern.HOOKS].disposition is Disposition.TRANSLATED
