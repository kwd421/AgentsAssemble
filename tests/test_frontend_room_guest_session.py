from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendRoomGuestSessionTests(unittest.TestCase):
    def test_simple_guest_query_is_local_dev_read_only_preview(self):
        source = (ROOT / "frontend/src/lib/roomDockModel.ts").read_text(encoding="utf-8")

        self.assertIn('inviteScope: "read_only"', source)
        self.assertIn('url.searchParams.set("scope", "read_only")', source)
        self.assertIn('url.searchParams.set("preview", "local-dev")', source)
        self.assertIn("localPreviewInviteUrlForRoom", source)

    def test_room_invite_modal_separates_secure_invite_from_local_preview(self):
        source = (ROOT / "frontend/src/views/components/RoomInviteModal.tsx").read_text(encoding="utf-8")

        self.assertIn("친구에게 보낼 보안 초대 링크", source)
        self.assertIn("공개 URL을 먼저 설정하면 /join?token=... 링크가 여기에 표시됩니다", source)
        self.assertIn("로컬/dev 미리보기", source)
        self.assertIn("친구에게 보내지 마세요", source)
        self.assertIn("로컬 미리보기 복사", source)
        self.assertNotIn("value={localPreviewUrl}", source.split("친구에게 보낼 보안 초대 링크", 1)[1].split("로컬/dev 미리보기", 1)[0])
        self.assertNotIn("보안 링크 복사", source)

    def test_join_token_url_and_join_response_become_guest_session(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const source = await fs.readFile(path.resolve("frontend/src/lib/roomGuestSession.ts"), "utf8");
            const compiled = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
              },
            }).outputText;
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-room-guest-session-"));
            const modulePath = path.join(tempDir, "roomGuestSession.mjs");
            await fs.writeFile(modulePath, compiled, "utf8");
            const guest = await import(pathToFileURL(modulePath).href);

            assert.equal(
              guest.joinInviteTokenFromUrl("https://room.example.com/join?token=aai1.invite-token&x=1"),
              "aai1.invite-token"
            );
            assert.equal(
              guest.joinInviteTokenFromUrl("https://room.example.com/?token=aai1.not-a-join-route"),
              ""
            );
            assert.equal(guest.joinInviteTokenFromUrl("not a url"), "");

            const session = guest.roomGuestSessionFromJoinPayload("aai1.invite-token", {
              status: "admitted",
              session_token: "aas1.session-token",
              meeting_id: "friend-room",
              agent_id: "friend-human",
              display_name: "Friend Human",
              invite_scope: "read_only",
              expires_at: "2026-06-04T12:00:00+00:00",
            });

            assert.equal(session.inviteToken, "aai1.invite-token");
            assert.equal(session.sessionToken, "aas1.session-token");
            assert.equal(session.meetingId, "friend-room");
            assert.equal(session.agentId, "friend-human");
            assert.equal(session.displayName, "Friend Human");
            assert.equal(session.inviteScope, "read_only");
            assert.equal(session.expiresAt, "2026-06-04T12:00:00+00:00");
            assert.match(session.joinedAt, /^\\d{4}-\\d{2}-\\d{2}T/);

            const restored = guest.normalizeRoomGuestSession({
              inviteToken: "aai1.invite-token",
              sessionToken: "aas1.session-token",
              meetingId: "friend-room",
              agentId: "friend-human",
              displayName: "Friend Human",
              inviteScope: "read_only",
              expiresAt: "2026-06-04T12:00:00+00:00",
              joinedAt: "2026-06-04T12:01:00.000Z",
            });
            assert.equal(restored.inviteScope, "read_only");

            const migratedV1 = guest.normalizeRoomGuestSession({
              inviteToken: "aai1.old-invite-token",
              sessionToken: "aas1.old-session-token",
              meetingId: "old-room",
              agentId: "old-guest",
              displayName: "Old Guest",
              expiresAt: "2026-06-04T12:00:00+00:00",
              joinedAt: "2026-06-04T12:01:00.000Z",
            });
            assert.equal(migratedV1.inviteScope, "room");
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

    def test_api_error_marks_401_as_guest_session_expiry(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const source = await fs.readFile(path.resolve("frontend/src/lib/apiErrors.ts"), "utf8");
            const compiled = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
              },
            }).outputText;
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-api-errors-"));
            const modulePath = path.join(tempDir, "apiErrors.mjs");
            await fs.writeFile(modulePath, compiled, "utf8");
            const apiErrors = await import(pathToFileURL(modulePath).href);

            const unauthorized = new apiErrors.ApiError(401, "invalid or expired session");
            assert.equal(apiErrors.isUnauthorizedApiError(unauthorized), true);
            assert.equal(apiErrors.isUnauthorizedApiError(new apiErrors.ApiError(403, "forbidden")), false);
            assert.equal(apiErrors.GUEST_SESSION_EXPIRED_MESSAGE, "Guest session expired or was revoked. Ask the host for a new invite.");
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

    def test_guest_posting_state_never_falls_back_to_host_lobby(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const source = await fs.readFile(path.resolve("frontend/src/lib/roomGuestPosting.ts"), "utf8");
            const compiled = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
              },
            }).outputText;
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-room-guest-posting-"));
            const modulePath = path.join(tempDir, "roomGuestPosting.mjs");
            await fs.writeFile(modulePath, compiled, "utf8");
            const posting = await import(pathToFileURL(modulePath).href);

            assert.deepEqual(
              posting.roomPostingState({
                guestLocked: false,
                guestReadOnly: false,
                sessionToken: "",
              }),
              {
                mode: "host",
                canPost: true,
                transport: "host-lobby",
                sessionToken: "",
                disabledReason: "",
              }
            );

            assert.deepEqual(
              posting.roomPostingState({
                guestLocked: true,
                guestReadOnly: false,
                sessionToken: "aas1.session",
              }),
              {
                mode: "guest",
                canPost: true,
                transport: "room-say",
                sessionToken: "aas1.session",
                disabledReason: "",
              }
            );

            const noSession = posting.roomPostingState({
              guestLocked: true,
              guestReadOnly: false,
              sessionToken: "",
            });
            assert.equal(noSession.mode, "guest");
            assert.equal(noSession.canPost, false);
            assert.equal(noSession.transport, "blocked");
            assert.match(noSession.disabledReason, /유효한 초대 세션/);

            const readOnly = posting.roomPostingState({
              guestLocked: true,
              guestReadOnly: true,
              sessionToken: "aas1.session",
            });
            assert.equal(readOnly.mode, "guest");
            assert.equal(readOnly.canPost, false);
            assert.equal(readOnly.transport, "blocked");
            assert.match(readOnly.disabledReason, /읽기 전용/);
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
