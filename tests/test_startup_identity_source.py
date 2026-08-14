from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_app_is_not_mounted_before_startup_identity_boundary_completes() -> None:
    source = read("frontend/src/main.tsx")
    assert "<StartupIdentityBoundary>" in source
    assert "<App />" in source
    assert source.index("<StartupIdentityBoundary>") < source.index("<App />")


def test_guest_onboarding_requires_explicit_recovery_code_acknowledgement() -> None:
    source = read("frontend/src/views/components/StartupIdentityGate.tsx")
    assert 'screen === "recovery-code"' in source
    assert "복구 코드를 안전한 곳에 저장했습니다" in source
    assert "disabled={!savedRecoveryCode}" in source
    assert "recoverCentralGuest" in source


def test_central_device_private_key_is_not_written_to_local_storage() -> None:
    source = read("frontend/src/lib/centralIdentity.ts")
    assert "indexedDB.open" in source
    assert 'localStorage.setItem(SESSION_KEY' in source
    assert "localStorage.setItem(DEVICE_KEY" not in source
    assert "AA-DEVICE-1" in source
    assert "host_registration_proof" in source


def test_server_registration_proof_is_not_a_public_tunnel_route() -> None:
    source = read("agentsassemble/web/security.py")
    assert '"/api/server-info"' in source
    assert '"/api/central-directory/registration-proof"' not in source
