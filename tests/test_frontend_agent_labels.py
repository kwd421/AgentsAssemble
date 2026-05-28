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
            const observed = {
              last_observed_event_id: "4fd560ddb0b2",
              last_observed_live_event_id: "live_event_123456789",
              last_reply_at: "2026-05-24T11:09:56.000000+00:00",
            };

            const result = {
              codexExecution: labels.providerExecutionLabel(codex),
              codexContext: labels.contextDurabilityLabel(codex.context_durability),
              codexJoin: labels.joinSemanticsLabel(codex.join_semantics),
              codexSandbox: labels.sandboxEnforcementLabel(codex.sandbox_enforcement),
              codexAdmission: labels.admissionBadge(codex).label,
              statelessExecution: labels.providerExecutionLabel(stateless),
              statelessContext: labels.contextDurabilityLabel(stateless.context_durability),
              statelessAdmission: labels.admissionBadge(stateless).label,
              sparseExecution: labels.providerExecutionLabel(sparse),
              genericStatelessExecution: labels.providerExecutionLabel(genericStateless),
              genericUnknownAdmission: labels.admissionBadge(genericStateless).label,
              observed: labels.lastObservedSummary(observed),
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
        self.assertEqual(payload["codexExecution"], "Codex exec/resume")
        self.assertEqual(payload["codexContext"], "Provider-owned context")
        self.assertEqual(payload["codexJoin"], "Codex exec/resume")
        self.assertEqual(payload["codexSandbox"], "Codex read-only")
        self.assertEqual(payload["codexAdmission"], "Host-approved")
        self.assertEqual(payload["statelessExecution"], "Stateless prompt call")
        self.assertEqual(payload["statelessContext"], "Stateless prompt")
        self.assertEqual(payload["statelessAdmission"], "Not host-approved · 2 conflicts")
        self.assertEqual(payload["sparseExecution"], "Custom provider")
        self.assertEqual(payload["genericStatelessExecution"], "Stateless prompt call")
        self.assertEqual(payload["genericUnknownAdmission"], "Admission unknown · 1 conflict")
        self.assertIn("lobby 4fd560dd...", payload["observed"])
        self.assertIn("official live_eve...", payload["observed"])
