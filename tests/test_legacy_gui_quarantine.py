from __future__ import annotations

import importlib

import pytest

from agentsassemble.legacy_runtime import (
    LEGACY_RUNTIME_ENV,
    install_legacy_runtime_quarantine,
)


def test_standard_gui_import_keeps_legacy_route_registrars_quarantined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LEGACY_RUNTIME_ENV, raising=False)

    patched = set(install_legacy_runtime_quarantine())
    expected = {
        "agentsassemble.legacy.gui_hooks.register_legacy_gui_routes",
        "agentsassemble.legacy.live_agent.http.flow.register_live_agent_flow_routes",
        "agentsassemble.legacy.meeting.http.room_composition.register_room_routes",
    }
    assert expected <= patched

    gui = importlib.import_module("agentsassemble.gui")
    for name in (
        "register_legacy_gui_routes",
        "register_live_agent_flow_routes",
        "register_room_routes",
    ):
        assert getattr(
            getattr(gui, name),
            "_agentsassemble_legacy_quarantined",
            False,
        )
