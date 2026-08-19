from __future__ import annotations

from pathlib import Path

import pytest

from agentsassemble import entrypoint
from agentsassemble.legacy_runtime import (
    LEGACY_RUNTIME_ENV,
    install_legacy_runtime_quarantine,
    legacy_runtime_enabled,
    requested_legacy_command,
)


def test_legacy_runtime_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LEGACY_RUNTIME_ENV, raising=False)

    assert legacy_runtime_enabled() is False


def test_legacy_runtime_requires_explicit_true_value(monkeypatch: pytest.MonkeyPatch) -> None:
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv(LEGACY_RUNTIME_ENV, value)
        assert legacy_runtime_enabled() is True

    for value in ("", "0", "false", "no", "off", "unexpected"):
        monkeypatch.setenv(LEGACY_RUNTIME_ENV, value)
        assert legacy_runtime_enabled() is False


def test_requested_legacy_command_only_checks_top_level_command() -> None:
    assert requested_legacy_command(["demo"]) == "demo"
    assert requested_legacy_command(["--help"]) is None
    assert requested_legacy_command(["room", "demo"]) is None
    assert requested_legacy_command(["gui"]) is None


def test_default_entrypoint_rejects_legacy_before_loading_cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(LEGACY_RUNTIME_ENV, raising=False)
    loaded = False

    def fail_if_loaded():
        nonlocal loaded
        loaded = True
        raise AssertionError("legacy-aware CLI must not load")

    monkeypatch.setattr(entrypoint, "_load_cli_main", fail_if_loaded)

    assert entrypoint.main(["live-agent"]) == 2
    assert loaded is False
    assert "Legacy runtime is disabled" in capsys.readouterr().err


def test_native_command_delegates_after_quarantine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LEGACY_RUNTIME_ENV, raising=False)
    observed: list[list[str]] = []

    def fake_cli_main(argv: list[str]) -> int:
        observed.append(argv)
        return 7

    monkeypatch.setattr(entrypoint, "_load_cli_main", lambda: fake_cli_main)

    assert entrypoint.main(["frontend-info"]) == 7
    assert observed == [["frontend-info"]]


def test_quarantine_replaces_legacy_registration_functions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(LEGACY_RUNTIME_ENV, raising=False)

    patched = install_legacy_runtime_quarantine()

    assert "agentsassemble.legacy.gui_hooks.register_legacy_gui_routes" in patched
    from agentsassemble.legacy import gui_hooks

    assert getattr(
        gui_hooks.register_legacy_gui_routes,
        "_agentsassemble_legacy_quarantined",
        False,
    )


def test_console_script_uses_quarantine_entrypoint() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"

    assert 'assemble = "agentsassemble.entrypoint:main"' in pyproject.read_text(
        encoding="utf-8"
    )
