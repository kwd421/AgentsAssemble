from __future__ import annotations

import json
import re
import subprocess
from typing import Any

from agentsassemble.adapters.base import ProviderAdapter
from agentsassemble.models import Role


class CodexAdapter(ProviderAdapter):
    name = "codex"

    def __init__(
        self,
        timeout_seconds: int = 240,
        command_runner: Any | None = None,
        search_enabled: bool = True,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.command_runner = command_runner or subprocess.run
        self.search_enabled = search_enabled

    def start_session(self, role: Role, meeting_context: dict[str, Any]) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "role_id": role.id,
            "session_id": None,
            "status": "ready",
            "context_dir": f"roles/{role.id}",
            "meeting_dir": meeting_context.get("meeting_dir"),
        }

    def run_research(
        self,
        role: Role,
        session: dict[str, Any],
        question: str,
    ) -> dict[str, Any]:
        prompt = f"""You are {role.display_name} ({role.lens}) in an AgentsAssemble council.

Research question: {question}
Research focus: {role.research_focus}
Personality/style: {json.dumps(role.personality or {}, ensure_ascii=False)}

Act independently. Do not assume access to other agents' notes.
Write all user-visible fields in Korean. URLs and source titles may stay in their original language.
Return only JSON with this exact shape:
{{
  "queries": ["..."],
  "sources": [{{"url": "...", "note": "...", "snippet": "short paraphrase or short excerpt"}}],
  "summary": "...",
  "confidence": "low|medium|high",
  "uncertainty": "...",
  "claim_evidence": [{{"claim": "...", "evidence": ["url"], "interpretation": "...", "confidence": "low|medium|high"}}]
}}
"""
        result = self._invoke_codex(session, "research", prompt)
        parsed = self._parse_json_object(result["text"])
        if parsed is None:
            parsed = self._fallback_research(role, result["text"])
        parsed["role_id"] = role.id
        parsed["display_name"] = role.display_name
        parsed["codex"] = result["metadata"]
        session["session_id"] = result["metadata"].get("session_id") or session.get("session_id")
        return parsed

    def run_round(
        self,
        role: Role,
        session: dict[str, Any],
        round_name: str,
        prompt: str,
        public_context: dict[str, Any],
    ) -> dict[str, Any]:
        council_prompt = f"""You are {role.display_name} ({role.lens}) in an AgentsAssemble council.

Round: {round_name}
Instruction: {prompt}
Personality/style: {json.dumps(role.personality or {}, ensure_ascii=False)}
Public context:
{json.dumps(public_context, ensure_ascii=False, indent=2)}

Write the visible message in Korean and follow the configured personality/style.
Return only JSON:
{{"content": "...", "confidence": "low|medium|high"}}
"""
        result = self._invoke_codex(session, round_name, council_prompt)
        parsed = self._parse_json_object(result["text"]) or {
            "content": result["text"].strip(),
            "confidence": "medium",
        }
        session["session_id"] = result["metadata"].get("session_id") or session.get("session_id")
        return {
            "role_id": role.id,
            "display_name": role.display_name,
            "round": round_name,
            "content": parsed.get("content", result["text"].strip()),
            "confidence": parsed.get("confidence", "medium"),
            "codex": result["metadata"],
        }

    def synthesize(
        self,
        session: dict[str, Any],
        question: str,
        public_context: dict[str, Any],
    ) -> dict[str, Any]:
        prompt = f"""You are the moderator for an AgentsAssemble council.

Question: {question}
Public council context:
{json.dumps(public_context, ensure_ascii=False, indent=2)}

Write all user-visible fields in Korean. Keep names and source URLs in their original language when useful.
Return only JSON:
{{
  "winner": "...",
  "ranking": ["..."],
  "confidence": "low|medium|high",
  "caveats": ["..."],
  "summary": "...",
  "tasks": {{"role_id": "task"}}
}}
"""
        result = self._invoke_codex(session, "synthesis", prompt)
        parsed = self._parse_json_object(result["text"]) or {
            "winner": "Undetermined",
            "ranking": [],
            "confidence": "low",
            "caveats": ["Codex synthesis did not return parseable JSON."],
            "summary": result["text"].strip(),
            "tasks": {},
        }
        parsed["codex"] = result["metadata"]
        return parsed

    def _invoke_codex(self, session: dict[str, Any], step: str, prompt: str) -> dict[str, Any]:
        meeting_dir = session.get("meeting_dir")
        if not meeting_dir:
            raise ValueError("CodexAdapter requires meeting_dir in session metadata.")

        from pathlib import Path

        meeting_path = Path(meeting_dir)
        output_path = meeting_path / "roles" / session["role_id"] / f"codex-{step}-last-message.txt"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = ["codex"]
        if self.search_enabled:
            command.append("--search")
        command.extend(
            [
                "exec",
                "--skip-git-repo-check",
                "--cd",
                str(meeting_path),
                "--output-last-message",
                str(output_path),
                "-",
            ]
        )
        completed = self.command_runner(
            command,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=self.timeout_seconds,
            check=False,
        )
        text = output_path.read_text(encoding="utf-8") if output_path.exists() else completed.stdout
        session_id = self._extract_session_id(completed.stdout + "\n" + completed.stderr)
        return {
            "text": text,
            "metadata": {
                "command": command,
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "session_id": session_id,
                "output_last_message": str(output_path),
            },
        }

    @staticmethod
    def _extract_session_id(output: str) -> str | None:
        match = re.search(r"session id:\s*([0-9a-fA-F-]+)", output)
        return match.group(1) if match else None

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any] | None:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
            stripped = re.sub(r"\s*```$", "", stripped)
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _fallback_research(role: Role, text: str) -> dict[str, Any]:
        return {
            "queries": [],
            "sources": [],
            "summary": text.strip(),
            "confidence": "low",
            "uncertainty": "Codex research output was not parseable as structured JSON.",
            "claim_evidence": [
                {
                    "claim": text.strip(),
                    "evidence": [],
                    "interpretation": role.research_focus,
                    "confidence": "low",
                }
            ],
        }
