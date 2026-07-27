from __future__ import annotations

import json
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendLobbyComposerRuntimeTests(unittest.TestCase):
    def test_api_uploads_attachment_before_lobby_post_and_parses_json_errors(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import { compileTypeScriptModule } from "./tests/frontend_api_runtime_helpers.mjs";

            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-lobby-api-"));
            const modulePath = await compileTypeScriptModule(path.resolve("frontend/src/api.ts"), tempDir);

            class FakeFileReader {
              constructor() {
                this.listeners = new Map();
                this.result = "";
              }
              addEventListener(type, listener) {
                this.listeners.set(type, listener);
              }
              readAsDataURL(file) {
                this.result = `data:${file.type};base64,${file.payload}`;
                this.listeners.get("load")();
              }
            }

            globalThis.FileReader = FakeFileReader;
            const calls = [];
            globalThis.fetch = async (url, options = {}) => {
              calls.push({
                url: String(url),
                headers: options.headers || {},
                body: options.body ? JSON.parse(options.body) : null,
              });
              if (url === "/api/attachments") {
                return jsonResponse({
                  attachment: {
                    id: "att-12345678",
                    filename: "notes.txt",
                    content_type: "text/plain",
                    size: 9,
                    is_image: false,
                    url: "/api/attachments/att-12345678?view=1",
                    download_url: "/api/attachments/att-12345678?download=1",
                  },
                });
              }
              if (url === "/api/lobby") {
                return jsonResponse({ event: { id: "event-1" }, events: [{ id: "event-1" }] });
              }
              throw new Error(`unexpected url ${url}`);
            };

            const api = await import(pathToFileURL(modulePath).href);
            const attachment = await api.uploadLobbyAttachment({
              name: "notes.txt",
              type: "text/plain",
              payload: "cm9vbSBub3Rl",
            }, {
              roomId: "room-a",
              sessionToken: "room-session-a",
            });
            await api.postLobbyMessage({
              name: "나",
              side: "mine",
              kind: "message",
              message: "파일 확인",
              attachments: [attachment],
            });

            assert.equal(calls[0].url, "/api/attachments");
            assert.equal(calls[0].headers.Authorization, "Bearer room-session-a");
            assert.equal(calls[0].body.room_id, "room-a");
            assert.equal(calls[0].body.purpose, "room_attachment");
            assert.equal(calls[0].body.data_base64, "cm9vbSBub3Rl");
            assert.equal(calls[1].url, "/api/lobby");
            assert.equal(calls[1].body.attachments[0].id, "att-12345678");
            assert.equal("data_base64" in calls[1].body.attachments[0], false);

            globalThis.fetch = async () => ({
              ok: false,
              status: 400,
              statusText: "Bad Request",
              text: async () => JSON.stringify({ error: "없는 파일" }),
            });

            await assert.rejects(
              () => api.postLobbyMessage({ name: "나", message: "실패", attachments: [] }),
              /없는 파일/
            );

            function jsonResponse(payload) {
              return {
                ok: true,
                status: 200,
                statusText: "OK",
                json: async () => payload,
                text: async () => JSON.stringify(payload),
              };
            }
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

    def test_lobby_composer_model_preserves_failure_draft_and_caps_attachments(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const source = await fs.readFile(path.resolve("frontend/src/lib/lobbyComposerModel.ts"), "utf8");
            const compiled = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
              },
            }).outputText;
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-lobby-composer-"));
            const modulePath = path.join(tempDir, "lobbyComposerModel.mjs");
            await fs.writeFile(modulePath, compiled, "utf8");
            const model = await import(pathToFileURL(modulePath).href);

            const selected = [{ id: "a" }, { id: "b" }, { id: "c" }];
            const capped = model.selectLobbyAttachmentFiles(7, selected);
            assert.deepEqual(capped.accepted, [{ id: "a" }]);
            assert.equal(capped.error, model.MAX_ATTACHMENTS_MESSAGE);

            const full = model.selectLobbyAttachmentFiles(8, selected);
            assert.deepEqual(full.accepted, []);
            assert.equal(full.error, model.MAX_ATTACHMENTS_MESSAGE);

            assert.deepEqual(model.lobbySubmitSuccessDraft(), {
              message: "",
              pendingAttachments: [],
            });
            const restored = model.lobbySubmitFailureDraft("초안", [{ id: "att" }], "없는 파일");
            assert.deepEqual(restored, {
              message: "초안",
              pendingAttachments: [{ id: "att" }],
              error: "없는 파일",
            });
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
