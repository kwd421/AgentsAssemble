from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


class StartupIdentitySourceTests(unittest.TestCase):
    def test_app_is_not_mounted_before_startup_identity_boundary_completes(self) -> None:
        source = read("frontend/src/main.tsx")
        self.assertIn("<StartupIdentityBoundary>", source)
        self.assertIn("<App />", source)
        self.assertLess(
            source.index("<StartupIdentityBoundary>"),
            source.index("<App />"),
        )

    def test_guest_onboarding_requires_explicit_recovery_code_acknowledgement(self) -> None:
        source = read("frontend/src/views/components/StartupIdentityGate.tsx")
        client = read("frontend/src/lib/centralIdentity.ts")
        self.assertIn('screen === "recovery-code"', source)
        self.assertIn("복구 코드를 안전한 곳에 저장했습니다", source)
        self.assertIn("disabled={!savedRecoveryCode || busy}", source)
        self.assertIn("loadPendingCentralRecoveryCode", source)
        self.assertIn("clearPendingCentralRecoveryCode", source)
        self.assertIn("PENDING_RECOVERY_KEY", client)
        self.assertLess(
            client.index("localStorage.setItem(PENDING_RECOVERY_KEY"),
            client.index("saveSession(result)"),
        )

    def test_mobile_shell_restores_unacknowledged_recovery_code_before_bootstrap(self) -> None:
        source = read("desktop/shell/shell.js")
        client = read("desktop/shell/central-identity.js")
        start = source.index("async function initializeCentralIdentity()")
        end = source.index("async function updateBeforeStartup()", start)
        initialization = source[start:end]
        self.assertIn("loadPendingRecoveryCode", initialization)
        self.assertIn("loadCentralSession", initialization)
        self.assertIn("PENDING_RECOVERY_KEY", client)
        self.assertLess(
            initialization.index("const pendingRecoveryCode = loadPendingRecoveryCode()"),
            initialization.index("const session = loadCentralSession()"),
        )
        self.assertLess(
            client.index("localStorage.setItem(PENDING_RECOVERY_KEY"),
            client.index("saveSession(result)"),
        )

    def test_central_device_private_key_is_not_written_to_local_storage(self) -> None:
        for relative in [
            "frontend/src/lib/centralIdentity.ts",
            "desktop/shell/central-identity.js",
        ]:
            with self.subTest(relative=relative):
                source = read(relative)
                self.assertIn("indexedDB.open", source)
                self.assertIn("localStorage.setItem(SESSION_KEY", source)
                self.assertNotIn("localStorage.setItem(DEVICE_KEY", source)
                self.assertIn("AA-DEVICE-1", source)

    def test_react_central_device_creation_is_serialized(self) -> None:
        source = read("frontend/src/lib/centralIdentity.ts")
        self.assertIn("let devicePromise: Promise<StoredDevice> | undefined", source)
        self.assertIn("async function loadOrCreateDevice()", source)
        self.assertIn("devicePromise = loadOrCreateDevice().catch", source)
        self.assertIn("return devicePromise", source)

    def test_server_registration_proof_is_not_a_public_tunnel_route(self) -> None:
        source = read("agentsassemble/web/security.py")
        self.assertIn('"/api/server-info"', source)
        self.assertNotIn('"/api/central-directory/registration-proof"', source)

    def test_desktop_startup_does_not_render_cached_rooms_before_ready(self) -> None:
        markup = read("desktop/shell/index.html")
        source = read("desktop/shell/shell.js")
        self.assertIn('id="cached-rooms" class="cached-rooms hidden"', markup)
        desktop_start = source.index(
            'clientPlatformLabel.textContent = "AGENTSASSEMBLE DESKTOP"'
        )
        desktop_source = source[desktop_start:]
        self.assertIn('cachedRooms.classList.add("hidden")', desktop_source)
        self.assertNotIn("void loadCachedRooms();", desktop_source)
        self.assertIn("void loadCachedRooms();", source[:desktop_start])

    def test_remote_engine_origin_never_runs_the_central_login_gate(self) -> None:
        boundary = read("frontend/src/views/components/StartupIdentityBoundary.tsx")
        self.assertIn("startupIdentityRunsOnThisOrigin", boundary)
        self.assertIn("loopbackHosts.has(hostname)", boundary)
        self.assertIn("url.origin === centralOrigin", boundary)
        self.assertIn("!startupIdentityRunsOnThisOrigin()", boundary)
        self.assertNotIn("trycloudflare.com", boundary)

    def test_mobile_known_server_is_verified_before_navigation(self) -> None:
        source = read("desktop/shell/shell.js")
        client = read("desktop/shell/central-identity.js")
        verification = source.index("void verifyKnownServer(server)")
        navigation = source.index("await openServer(origin)", verification)
        self.assertLess(verification, navigation)
        self.assertIn("AA-SERVER-CHALLENGE-1", client)
        self.assertIn("crypto.subtle.verify", client)
        self.assertNotIn("authorization:", client[client.index("fetchServerChallenge"):])

    def test_public_worker_url_is_shared_by_tauri_and_production_frontend(self) -> None:
        expected = "https://agentsassemble-identity-directory.seinel.workers.dev"
        rust = read("desktop/src-tauri/src/server_url.rs")
        production_env = read("frontend/.env.production")
        runtime = read("desktop/src-tauri/src/lib.rs")
        self.assertIn(expected, rust)
        self.assertIn(expected, production_env)
        self.assertIn('std::env::set_var("AGENTSASSEMBLE_CENTRAL_URL"', runtime)


if __name__ == "__main__":
    unittest.main()
