from __future__ import annotations

import json
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendAgentLabelTests(unittest.TestCase):
    def test_agent_label_helpers_render_honest_provider_context(self):
        script = textwrap.dedent(
            """
            import fs from "node:fs/promises";
            import os from "node:os";
            import path from "node:path";
            import { pathToFileURL } from "node:url";
            import ts from "./frontend/node_modules/typescript/lib/typescript.js";

            const sourcePath = path.resolve("frontend/src/lib/agentLabels.ts");
            const source = await fs.readFile(sourcePath, "utf8");
            const compiled = ts.transpileModule(source, {
              compilerOptions: {
                module: ts.ModuleKind.ES2022,
                target: ts.ScriptTarget.ES2022,
              },
            }).outputText;
            const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), "aa-agent-labels-"));
            const modulePath = path.join(tempDir, "agentLabels.mjs");
            await fs.writeFile(modulePath, compiled, "utf8");
            const labels = await import(pathToFileURL(modulePath).href);

            const codex = {
              provider_kind: "codex_live_session",
              connection_kind: "live_session",
              join_semantics: "codex_exec_resume",
              context_durability: "provider_managed_resume",
              sandbox_enforcement: "codex_readonly",
              persona_card_id: "yanagi",
              character_mode: "work_speech_only",
              admission_status: "approved",
              host_approved_binding: true,
              binding_conflicts: [],
            };
            const stateless = {
              provider_kind: "local_cli",
              connection_kind: "local_cli",
              join_semantics: "stateless_prompt_call",
              context_durability: "stateless_prompt",
              sandbox_enforcement: "advisory",
              admission_status: "requested",
              host_approved_binding: false,
              binding_conflicts: ["provider_kind_mismatch", "role_missing", "ignored_extra"],
            };
            const sparse = {
              provider_kind: "custom_provider",
              connection_kind: "",
              sandbox_enforcement: "",
            };
            const genericStateless = {
              provider_kind: "claude_code",
              connection_kind: "local_cli",
              join_semantics: "stateless_prompt_call",
              sandbox_enforcement: "advisory",
              binding_conflicts: ["role_missing"],
            };
            const residentKinds = [
              ["kiro_live_session", "kiro_chat_resume"],
              ["cursor_live_session", "cursor_chat_resume"],
              ["grok_live_session", "grok_session_resume"],
              ["antigravity_live_session", "antigravity_conversation_resume"],
              ["hermes_live_session", "hermes_chat_resume"],
            ];
            const bridge = {
              provider_kind: "remote_http_bridge",
              connection_kind: "remote_bridge",
              join_semantics: "remote_bridge_room_loop",
              context_durability: "remote_owner_managed",
              sandbox_enforcement: "advisory",
            };
            const manual = {
              provider_kind: "manual",
              connection_kind: "manual",
              join_semantics: "manual_room_loop",
              context_durability: "external_owner_managed",
              sandbox_enforcement: "unknown",
            };
            const observed = {
              last_observed_event_id: "4fd560ddb0b2",
              last_observed_live_event_id: "live_event_123456789",
              last_reply_at: "2026-05-24T11:09:56.000000+00:00",
            };
            const residentNonDuplicates = Object.fromEntries(
              residentKinds.map(([provider_kind, join_semantics]) => {
                const agent = {
                  provider_kind,
                  connection_kind: "live_session",
                  join_semantics,
                  context_durability: "provider_managed_resume",
                  sandbox_enforcement: "advisory",
                };
                return [
                  provider_kind,
                  labels.providerExecutionLabel(agent) !== labels.joinSemanticsLabel(join_semantics),
                ];
              })
            );

            const result = {
              codexExecution: labels.providerExecutionLabel(codex),
              codexContext: labels.contextDurabilityLabel(codex.context_durability),
              codexContextKind: labels.contextDurabilityKind(codex.context_durability),
              codexContextBadge: labels.contextBadge(codex),
              codexCharacterMode: labels.characterModeLabel(codex.character_mode),
              codexCharacterModeKind: labels.characterModeKind(codex.character_mode),
              codexCharacterBadge: labels.characterBadge(codex),
              codexJoin: labels.joinSemanticsLabel(codex.join_semantics),
              codexExecutionDiffersFromJoin:
                labels.providerExecutionLabel(codex) !== labels.joinSemanticsLabel(codex.join_semantics),
              codexSandbox: labels.sandboxEnforcementLabel(codex.sandbox_enforcement),
              codexAdmission: labels.admissionBadge(codex).label,
              statelessExecution: labels.providerExecutionLabel(stateless),
              statelessContext: labels.contextDurabilityLabel(stateless.context_durability),
              statelessContextKind: labels.contextDurabilityKind(stateless.context_durability),
              statelessContextBadge: labels.contextBadge(stateless),
              statelessExecutionDiffersFromJoin:
                labels.providerExecutionLabel(stateless) !== labels.joinSemanticsLabel(stateless.join_semantics),
              statelessAdmission: labels.admissionBadge(stateless).label,
              bridgeExecution: labels.providerExecutionLabel(bridge),
              bridgeJoin: labels.joinSemanticsLabel(bridge.join_semantics),
              bridgeExecutionDiffersFromJoin:
                labels.providerExecutionLabel(bridge) !== labels.joinSemanticsLabel(bridge.join_semantics),
              manualExecution: labels.providerExecutionLabel(manual),
              manualJoin: labels.joinSemanticsLabel(manual.join_semantics),
              manualExecutionDiffersFromJoin:
                labels.providerExecutionLabel(manual) !== labels.joinSemanticsLabel(manual.join_semantics),
              roomLoopKind: labels.contextDurabilityKind("provider_managed_room_loop"),
              processKind: labels.contextDurabilityKind("process_lifetime"),
              remoteKind: labels.contextDurabilityKind("remote_owner_managed"),
              externalKind: labels.contextDurabilityKind("external_owner_managed"),
              unknownKind: labels.contextDurabilityKind(""),
              sparseExecution: labels.providerExecutionLabel(sparse),
              genericStatelessExecution: labels.providerExecutionLabel(genericStateless),
              genericUnknownAdmission: labels.admissionBadge(genericStateless).label,
              residentNonDuplicates,
              observed: labels.lastObservedSummary(observed),
              roomSummary: labels.summarizeRoomContext([codex, stateless, bridge, manual]),
              roomSummaryBadges: labels.roomContextSummaryBadges([codex, stateless, bridge, manual]).map((badge) => ({
                label: badge.label,
                tone: badge.tone,
              })),
              emptyRoomSummaryBadges: labels.roomContextSummaryBadges([]).length,
            };
            console.log(JSON.stringify(result));
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
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["codexExecution"], "Codex")
        self.assertEqual(payload["codexContext"], "Provider-owned context")
        self.assertEqual(payload["codexContextKind"], "provider_owned")
        self.assertEqual(payload["codexContextBadge"]["label"], "기억 유지")
        self.assertEqual(payload["codexContextBadge"]["tone"], "online")
        self.assertEqual(payload["codexCharacterMode"], "Work speech")
        self.assertEqual(payload["codexCharacterModeKind"], "work_speech")
        self.assertEqual(payload["codexCharacterBadge"]["label"], "캐릭터 · Work speech")
        self.assertEqual(payload["codexCharacterBadge"]["tone"], "accent")
        self.assertEqual(payload["codexCharacterBadge"]["title"], "Persona yanagi")
        self.assertEqual(payload["codexJoin"], "Codex exec/resume")
        self.assertEqual(payload["codexExecutionDiffersFromJoin"], True)
        self.assertEqual(payload["codexSandbox"], "Codex read-only")
        self.assertEqual(payload["codexAdmission"], "승인됨")
        self.assertEqual(payload["statelessExecution"], "CLI")
        self.assertEqual(payload["statelessContext"], "Stateless prompt")
        self.assertEqual(payload["statelessContextKind"], "stateless")
        self.assertEqual(payload["statelessContextBadge"]["label"], "이번만")
        self.assertEqual(payload["statelessContextBadge"]["tone"], "idle")
        self.assertEqual(payload["statelessExecutionDiffersFromJoin"], True)
        self.assertEqual(payload["statelessAdmission"], "승인 대기 · 충돌 2")
        self.assertEqual(payload["bridgeExecution"], "Remote")
        self.assertEqual(payload["bridgeJoin"], "Remote bridge")
        self.assertEqual(payload["bridgeExecutionDiffersFromJoin"], True)
        self.assertEqual(payload["manualExecution"], "Guest")
        self.assertEqual(payload["manualJoin"], "Manual room loop")
        self.assertEqual(payload["manualExecutionDiffersFromJoin"], True)
        self.assertEqual(payload["roomLoopKind"], "provider_owned")
        self.assertEqual(payload["processKind"], "process_lifetime")
        self.assertEqual(payload["remoteKind"], "external_owned")
        self.assertEqual(payload["externalKind"], "external_owned")
        self.assertEqual(payload["unknownKind"], "unknown")
        self.assertEqual(payload["sparseExecution"], "Custom provider")
        self.assertEqual(payload["genericStatelessExecution"], "CLI")
        self.assertEqual(payload["genericUnknownAdmission"], "확인 필요 · 충돌 1")
        self.assertTrue(all(payload["residentNonDuplicates"].values()))
        self.assertIn("lobby 4fd560dd...", payload["observed"])
        self.assertIn("official live_eve...", payload["observed"])
        self.assertEqual(
            payload["roomSummary"],
            {
                "total": 4,
                "resident_session": 1,
                "stateless": 1,
                "external_owned": 2,
                "advisory_sandbox": 2,
                "pending_admission": 1,
            },
        )
        self.assertEqual(
            payload["roomSummaryBadges"],
            [
                {"label": "상주 1", "tone": "online"},
                {"label": "단발 1", "tone": "idle"},
                {"label": "외부 2", "tone": "muted"},
                {"label": "주의 2", "tone": "idle"},
                {"label": "확인 1", "tone": "idle"},
            ],
        )
        self.assertEqual(payload["emptyRoomSummaryBadges"], 0)
