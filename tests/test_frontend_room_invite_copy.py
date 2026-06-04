from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendRoomInviteCopyTests(unittest.TestCase):
    def test_invite_copy_names_read_only_scope_in_buttons_and_local_dm(self):
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

            assert.equal(
              copy.inviteFriendButtonLabel({ status: "", isAiFriend: false, readOnlyInvite: true }),
              "읽기 전용 초대"
            );
            assert.equal(
              copy.inviteFriendButtonLabel({ status: "", isAiFriend: true, readOnlyInvite: true }),
              "읽기 전용 호출"
            );
            assert.equal(
              copy.inviteFriendButtonLabel({ status: "초대됨", isAiFriend: false, readOnlyInvite: true }),
              "초대됨"
            );
            assert.equal(
              copy.inviteFriendButtonLabel({ status: "", isAiFriend: true, readOnlyInvite: false }),
              "호출하기"
            );

            assert.equal(
              copy.inviteFriendDmMessage({
                roomLabel: "Read Room",
                link: "http://127.0.0.1:8765/?guest=1&scope=read_only",
                isAiFriend: false,
                isLiveSession: false,
                readOnlyInvite: true,
              }),
              "Read Room 읽기 전용 초대: http://127.0.0.1:8765/?guest=1&scope=read_only"
            );
            assert.equal(
              copy.inviteFriendDmMessage({
                roomLabel: "Read Room",
                link: "http://127.0.0.1:8765/?guest=1&scope=read_only",
                isAiFriend: true,
                isLiveSession: true,
                readOnlyInvite: true,
              }),
              "Read Room 읽기 전용 호출: http://127.0.0.1:8765/?guest=1&scope=read_only"
            );
            assert.match(
              copy.inviteFriendDmMessage({
                roomLabel: "Read Room",
                link: "http://127.0.0.1:8765/?guest=1&scope=read_only",
                isAiFriend: true,
                isLiveSession: false,
                readOnlyInvite: true,
              }),
              /읽기 전용 초대 링크가 생성됐지만/
            );

            assert.equal(copy.remoteClientPacketPreview(null), "");
            assert.equal(
              copy.remoteClientPacketPreview({
                packet_kind: "native_remote_room_client_entry_packet",
                env: { AGENTSASSEMBLE_AGENT_ID: "friend-ai" },
              }),
              '{\\n  "packet_kind": "native_remote_room_client_entry_packet",\\n  "env": {\\n    "AGENTSASSEMBLE_AGENT_ID": "friend-ai"\\n  }\\n}'
            );

            assert.deepEqual(
              copy.secureInviteCopyTarget({
                joinUrl: "https://shared-room.example.com/join?token=aai1.secret",
                localPreviewUrl: "http://127.0.0.1:8765/?guest=1&room=friend-room",
              }),
              {
                copyUrl: "https://shared-room.example.com/join?token=aai1.secret",
                status: "보안 초대 링크 복사됨",
                previewLabel: "로컬/dev 미리보기 링크",
                secure: true,
              }
            );

            const missingPublicUrl = copy.secureInviteCopyTarget({
              joinUrl: "",
              localPreviewUrl: "http://127.0.0.1:8765/?guest=1&room=friend-room",
            });
            assert.equal(missingPublicUrl.copyUrl, "");
            assert.equal(missingPublicUrl.secure, false);
            assert.equal(missingPublicUrl.previewLabel, "로컬/dev 미리보기 링크");
            assert.match(missingPublicUrl.status, /공개 URL/);
            assert.match(missingPublicUrl.status, /보안 초대 링크/);

            const localJoin = copy.secureInviteCopyTarget({
              joinUrl: "http://127.0.0.1:8765/join?token=aai1.local",
              localPreviewUrl: "http://127.0.0.1:8765/?guest=1&room=friend-room",
            });
            assert.equal(localJoin.copyUrl, "");
            assert.equal(localJoin.secure, false);
            assert.match(localJoin.status, /외부 초대 링크가 아직 준비되지 않았습니다/);

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
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const source = await fs.readFile(path.resolve("frontend/src/api.ts"), "utf8");
            const compiled = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
              },
            }).outputText.replace(/^import[^\\n]+apiErrors[^\\n]+\\n/m, "class ApiError extends Error { constructor(status, message) { super(message); this.status = status; } }\\n");
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-api-host-token-"));
            const modulePath = path.join(tempDir, "api.mjs");
            await fs.writeFile(modulePath, compiled, "utf8");

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
