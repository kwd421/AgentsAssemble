from __future__ import annotations

import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendRoomGuestSessionTests(unittest.TestCase):
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
              expires_at: "2026-06-04T12:00:00+00:00",
            });

            assert.equal(session.inviteToken, "aai1.invite-token");
            assert.equal(session.sessionToken, "aas1.session-token");
            assert.equal(session.meetingId, "friend-room");
            assert.equal(session.agentId, "friend-human");
            assert.equal(session.displayName, "Friend Human");
            assert.equal(session.expiresAt, "2026-06-04T12:00:00+00:00");
            assert.match(session.joinedAt, /^\\d{4}-\\d{2}-\\d{2}T/);
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
