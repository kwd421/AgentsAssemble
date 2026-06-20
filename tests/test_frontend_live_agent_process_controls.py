import subprocess
import textwrap
import unittest
from pathlib import Path


class FrontendLiveAgentProcessControlTests(unittest.TestCase):
    def test_individual_controls_only_allow_single_agent_process_groups(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const sourcePath = path.resolve("frontend/src/lib/liveAgentProcessControls.ts");
            const source = await fs.readFile(sourcePath, "utf8");
            const output = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
                importsNotUsedAsValues: ts.ImportsNotUsedAsValues.Remove,
              },
              fileName: sourcePath,
            }).outputText;
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-process-controls-"));
            const modulePath = path.join(tempDir, "liveAgentProcessControls.mjs");
            await fs.writeFile(modulePath, output);
            const controls = await import(pathToFileURL(modulePath).href);

            const single = {
              group_id: "codex-one",
              status: "running",
              meeting_id: "resident-m1",
              config_path: "/tmp/codex.json",
              agents: [{ agent_id: "codex-one", display_name: "Codex One" }],
            };
            const multi = {
              group_id: "crew",
              status: "running",
              meeting_id: "resident-m1",
              config_path: "/tmp/crew.json",
              agents: [
                { agent_id: "codex-one", display_name: "Codex One" },
                { agent_id: "claude-one", display_name: "Claude One" },
              ],
            };
            const stoppedSingle = {
              group_id: "codex-one-stale",
              status: "stopped",
              meeting_id: "resident-m1",
              config_path: "/tmp/codex-stale.json",
              agents: [{ agent_id: "codex-one", display_name: "Codex One" }],
            };
            const legacyWithoutManifest = {
              group_id: "legacy",
              status: "running",
              meeting_id: "resident-m1",
              config_path: "/tmp/legacy.json",
              agents: [],
            };
            const codex = { agent_id: "codex-one", display_name: "Codex One" };

            assert.equal(controls.processGroupAgentCount(single), 1);
            assert.equal(controls.processGroupOwnsAgent(single, codex), true);
            assert.equal(controls.processGroupCanControlSingleAgent(single, codex), true);
            assert.equal(controls.processGroupIndividualControlReason(single, codex, "Codex One"), "");
            assert.equal(controls.processGroupOwnsAgent(single, { display_name: "Codex One" }), false);
            assert.equal(
              controls.processGroupOwnsAgent(
                { ...single, agents: [{ agent_id: "other-agent", display_name: "Codex One" }] },
                codex
              ),
              false
            );

            assert.equal(controls.processGroupOwnsAgent(multi, codex), true);
            assert.equal(controls.processGroupCanControlSingleAgent(multi, codex), false);
            assert.match(
              controls.processGroupIndividualControlReason(multi, codex, "Codex One"),
              /2개 AI가 하나의 프로세스/
            );

            assert.equal(controls.processGroupOwnsAgent(legacyWithoutManifest, codex), false);
            assert.equal(controls.processGroupCanControlSingleAgent(legacyWithoutManifest, codex), false);
            assert.equal(controls.findProcessGroupForAgent([multi, single], codex), single);
            assert.equal(controls.findProcessGroupForAgent([stoppedSingle, multi], codex), multi);

            const registered = controls.registeredAgentProcessGroupForAgent({
              agent_id: "codex-pending",
              display_name: "Codex Pending",
              provider_kind: "codex_live_session",
              connection_kind: "live_session",
              meeting_id: "resident-m1",
              status: "offline",
              process_group_id: "agent-codex-pending",
              live_agent_config_path: "/tmp/codex-pending.json",
            });
            assert.equal(registered.group_id, "agent-codex-pending");
            assert.equal(registered.status, "stopped");
            assert.equal(controls.processGroupCanControlSingleAgent(registered, { agent_id: "codex-pending" }), true);
            assert.equal(
              controls.registeredAgentProcessGroupForAgent({
                agent_id: "codex-live",
                meeting_id: "resident-m1",
                status: "online",
                process_group_id: "agent-codex-live",
                live_agent_config_path: "/tmp/codex-live.json",
              }),
              undefined
            );
            """
        )
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            check=True,
            cwd=".",
        )

    def test_member_and_friend_cards_use_single_agent_process_guard(self):
        member_source = Path("frontend/src/views/components/MemberList.tsx").read_text(encoding="utf-8")
        profile_source = Path("frontend/src/views/components/FriendProfileCard.tsx").read_text(encoding="utf-8")
        panel_source = Path("frontend/src/views/components/RoomConnectionPanel.tsx").read_text(encoding="utf-8")
        app_source = Path("frontend/src/App.tsx").read_text(encoding="utf-8")
        api_source = Path("frontend/src/api.ts").read_text(encoding="utf-8")

        self.assertIn("processGroupCanControlSingleAgent", member_source)
        self.assertIn("processGroupIndividualControlReason", member_source)
        self.assertIn("findProcessGroupForAgent(processGroups", member_source)
        self.assertIn("registeredAgentProcessGroupForAgent(agent)", member_source)
        self.assertIn("START", member_source)
        self.assertIn("추방", member_source)
        self.assertIn("세션 삭제", member_source)
        self.assertIn("저장된 세션 설정도 삭제됩니다", member_source)
        self.assertIn("processGroupCanControlSingleAgent", profile_source)
        self.assertIn("processGroupIndividualControlReason", profile_source)
        self.assertIn("findProcessGroupForAgent(processGroups", profile_source)
        self.assertIn("const hasSourceAgentId = Boolean(sourceAgentId)", profile_source)
        self.assertIn("hasSourceAgentId &&", profile_source)
        self.assertIn("processGroups?: LiveAgentProcessGroup[]", panel_source)
        self.assertNotIn("sessionGroup={sessionGroup}", panel_source)
        self.assertIn("processGroups={activeProcessGroups}", app_source)
        self.assertNotIn("sessionGroup={activeProcessGroup}", app_source)
        self.assertIn("resumeAgentSession", profile_source)
        self.assertIn("stopLiveAgentSessionAgent", profile_source)
        self.assertIn("resumeAgentSession", member_source)
        self.assertIn("stopLiveAgentSessionAgent", member_source)
        self.assertIn("updateLiveAgentSessionAgentTiming", member_source)
        self.assertIn("expelLiveAgentFromRoom", member_source)
        self.assertIn("deleteLiveAgentSession", member_source)
        self.assertIn("호출 간격", member_source)
        self.assertIn("초 단위", member_source)
        self.assertNotIn("즉시", member_source)
        self.assertNotIn("pollIntervalMode", member_source)
        self.assertIn('"/api/live-agent-sessions/resume-agent"', api_source)
        self.assertIn('"/api/live-agent-sessions/stop-agent"', api_source)
        self.assertIn('"/api/live-agent-sessions/agent-timing"', api_source)
        self.assertIn('"/api/live-agent-room/expel"', api_source)
        self.assertIn('"/api/live-agent-room/delete-session"', api_source)

    def test_agent_session_api_posts_selected_agent_id(self):
        script = textwrap.dedent(
            """
            import assert from "node:assert/strict";
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const sourcePath = path.resolve("frontend/src/api.ts");
            const source = await fs.readFile(sourcePath, "utf8");
            const output = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
                importsNotUsedAsValues: ts.ImportsNotUsedAsValues.Remove,
              },
              fileName: sourcePath,
            }).outputText.replace(
              /^import[^\\n]+apiErrors[^\\n]+\\n/m,
              "class ApiError extends Error { constructor(status, message) { super(message); this.status = status; } }\\n"
            );
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-session-api-"));
            const modulePath = path.join(tempDir, "api.mjs");
            await fs.writeFile(modulePath, output);

            const calls = [];
            globalThis.fetch = async (url, options = {}) => {
              calls.push({ url, options, body: JSON.parse(String(options.body || "{}")) });
              return {
                ok: true,
                json: async () => ({ status: "ready" }),
              };
            };

            const api = await import(pathToFileURL(modulePath).href);
            await api.resumeAgentSession({
              roomId: "resident-m1",
              agentId: "agent-a",
              sessionId: "session-a",
              displayName: "Agent A",
              providerKind: "codex_live_session",
              sandbox: "read-only",
              permissions: "workspace-write",
            });
            await api.resumeLiveAgentSessionAgent({
              meetingId: "resident-m1",
              groupId: "resident-main--agent-a",
              agentId: "agent-a",
              liveAgentConfigPath: "/tmp/live-agent.json",
            });
            await api.stopLiveAgentSessionAgent({
              meetingId: "resident-m1",
              groupId: "resident-main--agent-a",
              agentId: "agent-a",
            });
            await api.updateLiveAgentSessionAgentTiming({
              meetingId: "resident-m1",
              groupId: "resident-main--agent-a",
              agentId: "agent-a",
              liveAgentConfigPath: "/tmp/live-agent.json",
              pollInterval: 0.25,
            });
            await api.expelLiveAgentFromRoom({
              meetingId: "resident-m1",
              groupId: "resident-main--agent-a",
              agentId: "agent-a",
            });
            await api.deleteLiveAgentSession({
              meetingId: "resident-m1",
              groupId: "resident-main--agent-a",
              agentId: "agent-a",
            });

            assert.equal(calls[0].url, "/api/agent-sessions/resume");
            assert.equal(calls[0].options.method, "POST");
            assert.equal(calls[0].body.room_id, "resident-m1");
            assert.equal(calls[0].body.agent_id, "agent-a");
            assert.equal(calls[0].body.session_id, "session-a");
            assert.equal(calls[0].body.display_name, "Agent A");
            assert.equal(calls[0].body.provider_kind, "codex_live_session");
            assert.equal(calls[0].body.sandbox, "read-only");
            assert.equal(calls[0].body.permissions, "workspace-write");
            assert.equal(calls[0].body.start, false);
            assert.equal(calls[0].body.dry_run, false);
            assert.equal(calls[1].url, "/api/live-agent-sessions/resume-agent");
            assert.equal(calls[1].options.method, "POST");
            assert.equal(calls[1].body.meeting_id, "resident-m1");
            assert.equal(calls[1].body.group_id, "resident-main--agent-a");
            assert.equal(calls[1].body.agent_id, "agent-a");
            assert.equal(calls[2].url, "/api/live-agent-sessions/stop-agent");
            assert.equal(calls[2].options.method, "POST");
            assert.equal(calls[2].body.meeting_id, "resident-m1");
            assert.equal(calls[2].body.group_id, "resident-main--agent-a");
            assert.equal(calls[2].body.agent_id, "agent-a");
            assert.equal(calls[3].url, "/api/live-agent-sessions/agent-timing");
            assert.equal(calls[3].body.meeting_id, "resident-m1");
            assert.equal(calls[3].body.group_id, "resident-main--agent-a");
            assert.equal(calls[3].body.agent_id, "agent-a");
            assert.equal(calls[3].body.live_agent_config_path, "/tmp/live-agent.json");
            assert.equal(calls[3].body.poll_interval, 0.25);
            assert.equal(calls[4].url, "/api/live-agent-room/expel");
            assert.equal(calls[4].body.meeting_id, "resident-m1");
            assert.equal(calls[4].body.group_id, "resident-main--agent-a");
            assert.equal(calls[4].body.agent_id, "agent-a");
            assert.equal(calls[5].url, "/api/live-agent-room/delete-session");
            assert.equal(calls[5].body.meeting_id, "resident-m1");
            assert.equal(calls[5].body.group_id, "resident-main--agent-a");
            assert.equal(calls[5].body.agent_id, "agent-a");
            """
        )
        subprocess.run(
            ["node", "--input-type=module", "-e", script],
            check=True,
            cwd=".",
        )


if __name__ == "__main__":
    unittest.main()
