from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendRoomInviteCopyTests(unittest.TestCase):
    def test_external_invite_copy_never_substitutes_a_local_preview_url(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const source = await fs.readFile(path.resolve("frontend/src/lib/roomInviteCopy.ts"), "utf8");
            const compiled = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
              },
            }).outputText;
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-room-invite-copy-"));
            const modulePath = path.join(tempDir, "roomInviteCopy.mjs");
            await fs.writeFile(modulePath, compiled, "utf8");
            const copy = await import(pathToFileURL(modulePath).href);

            const external = copy.secureInviteCopyTarget({
              joinUrl: "https://shared-room.example.com/join?token=aai1.secret",
              localPreviewUrl: "http://127.0.0.1:8765/?guest=1&room=friend-room",
            });
            assert.equal(external.copyUrl, "https://shared-room.example.com/join?token=aai1.secret");
            assert.equal(external.secure, true);

            const missingPublicUrl = copy.secureInviteCopyTarget({
              joinUrl: "",
              localPreviewUrl: "http://127.0.0.1:8765/?guest=1&room=friend-room",
            });
            assert.equal(missingPublicUrl.copyUrl, "");
            assert.equal(missingPublicUrl.secure, false);

            const localJoin = copy.secureInviteCopyTarget({
              joinUrl: "http://127.0.0.1:8765/join?token=aai1.local",
              localPreviewUrl: "http://127.0.0.1:8765/?guest=1&room=friend-room",
            });
            assert.equal(localJoin.copyUrl, "");
            assert.equal(localJoin.secure, false);

            assert.equal(copy.isExternalInviteUrl("https://shared-room.example.com/join?token=aai1.secret"), true);
            assert.equal(copy.isExternalInviteUrl("http://127.0.0.1:8765/join?token=aai1.local"), false);
            assert.equal(copy.isExternalInviteUrl("http://localhost:8765/join?token=aai1.local"), false);
            assert.equal(copy.isExternalInviteUrl("https://shared-room.example.com/?guest=1"), false);
            """
        )
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )

    def test_api_create_room_invite_sends_session_host_token(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import { compileTypeScriptModule } from "./tests/frontend_api_runtime_helpers.mjs";

            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-api-host-token-"));
            const modulePath = await compileTypeScriptModule(path.resolve("frontend/src/api.ts"), tempDir);

            const stored = new Map();
            globalThis.sessionStorage = {
              getItem: (key) => stored.get(key) ?? null,
              setItem: (key, value) => stored.set(key, String(value)),
              removeItem: (key) => stored.delete(key),
            };
            const calls = [];
            globalThis.fetch = async (url, options = {}) => {
              calls.push({ url, options });
              return {
                ok: true,
                json: async () => ({ join_url: "https://shared-room.example.com/join?token=aai1.secret" }),
              };
            };

            const api = await import(pathToFileURL(modulePath).href);
            assert.equal(api.loadHostToken(), "");
            api.saveHostToken("host-secret");
            assert.equal(api.loadHostToken(), "host-secret");
            await api.createRoomInvite({
              meetingId: "friend-room",
              agentId: "guest",
              displayName: "Guest",
              inviteScope: "room",
            });
            assert.equal(calls[0].url, "/api/room-invite/create");
            assert.equal(calls[0].options.headers["X-Host-Token"], "host-secret");
            api.clearHostToken();
            assert.equal(api.loadHostToken(), "");
            """
        )
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
