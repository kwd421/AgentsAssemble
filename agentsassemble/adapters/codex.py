from __future__ import annotations

import json
import re
import subprocess
from subprocess import TimeoutExpired
from typing import Any

from agentsassemble.adapters.base import ProviderAdapter
from agentsassemble.models import ResearchDepth, ResearchSteering, Role


class CodexAdapter(ProviderAdapter):
    name = "codex"

    def __init__(
        self,
        timeout_seconds: int | None = None,
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
        depth: ResearchDepth,
        steering: ResearchSteering,
    ) -> dict[str, Any]:
        steering_instruction = (
            "Default stance: investigate freely and form your own conclusion from evidence."
            if steering.is_open
            else (
                "User-requested steering: investigate this angle in extra detail, but do not force the conclusion "
                f"if evidence does not support it: {steering.prompt}"
            )
        )
        prompt = f"""You are {role.display_name} ({role.lens}) in an AgentsAssemble council.

Research question: {question}
Research focus: {role.research_focus}
Source preferences: {json.dumps(role.source_preferences or [], ensure_ascii=False)}
Personality/style: {json.dumps(role.personality or {}, ensure_ascii=False)}
Research depth: {depth.name} / {depth.label}
Minimum sources: {depth.min_sources}
Target sources: {depth.target_sources}
Minimum search queries: {depth.min_queries}
Minimum claim_evidence items: {depth.min_claims}
Minimum counterclaims: {depth.min_counterclaims}
Notes per source: {depth.notes_per_source}
Required source mix: {depth.source_mix}
Depth instructions: {depth.instructions}
Research steering: {json.dumps(steering.to_dict(), ensure_ascii=False)}
Steering instruction: {steering_instruction}

Act independently. Do not assume access to other agents' notes.
If source preferences are provided, use them to guide search queries and source selection.
Treat fan/community sources as claims to inspect, not as canon authority.
You may hold a free opinion by default, but every conclusion must be traceable to evidence.
If the user steers toward a preferred angle, spend more research effort on that angle and its best objections. Do not hide contrary evidence.
For standard/deep research, do not stop after a handful of search results. Iterate queries, compare contradictory sources, and gather enough material for a dense evidence archive. In this single call, satisfy minimum counts before trying to reach target counts.
Every URL in claim_evidence[].evidence, counterclaims[].evidence, and rejected_claims[].sources MUST exactly match one sources[].url string. If you want to cite a URL, include that same URL in sources first.
For each claim_evidence item, set evidence_relation to "supports", "weak", "contradicts", or "irrelevant" when the source relationship is explicit. Use "weak" when the source is only indirect, contested, or insufficient for the exact claim.
If you cannot reach the target source count within tool limits, still return the best evidence and explain the gap in "coverage_gaps".
Write all user-visible fields in Korean. URLs and source titles may stay in their original language.
Return only JSON with this exact shape:
{{
  "research_steering": {json.dumps(steering.to_dict(), ensure_ascii=False)},
  "research_depth": {{"name": "{depth.name}", "label": "{depth.label}", "min_sources": {depth.min_sources}, "target_sources": {depth.target_sources}, "min_queries": {depth.min_queries}, "min_claims": {depth.min_claims}, "min_counterclaims": {depth.min_counterclaims}, "notes_per_source": {depth.notes_per_source}}},
  "queries": ["..."],
  "sources": [{{"url": "...", "title": "...", "source_type": "official|primary|wiki|community|analysis|other", "quality": "high|medium|low", "note": "...", "snippet": "short paraphrase or compliant short excerpt", "extracted_notes": ["..."]}}],
  "summary": "...",
  "confidence": "low|medium|high",
  "uncertainty": "...",
  "coverage_gaps": ["..."],
  "claim_evidence": [{{"claim": "...", "evidence": ["url"], "evidence_relation": "supports|weak|contradicts|irrelevant", "interpretation": "...", "confidence": "low|medium|high", "source_quality": "..."}}],
  "counterclaims": [{{"claim": "...", "evidence": ["url"], "why_it_matters": "...", "confidence": "low|medium|high"}}],
  "rejected_claims": [{{"claim": "...", "reason": "...", "sources": ["url"]}}]
}}
"""
        result = self._invoke_codex(session, "research", prompt, use_search=True)
        parsed = self._parse_json_object(result["text"])
        if parsed is None:
            parsed = self._fallback_research(role, result["text"])
        parsed["role_id"] = role.id
        parsed["display_name"] = role.display_name
        parsed.setdefault("research_steering", steering.to_dict())
        parsed.setdefault("research_depth", self._depth_payload(depth))
        parsed.setdefault("coverage_gaps", [])
        parsed.setdefault("counterclaims", [])
        parsed.setdefault("rejected_claims", [])
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
Maintain your role's distinct stance. Do not converge just to sound cooperative.
Return stance_status as "held", "revised", or "conceded".
Use "revised" or "conceded" only when specific evidence in the public context changes your position.
State change_conditions: what evidence would make you change your mind further.
Return only JSON:
{{"content": "...", "position": "...", "stance_status": "held|revised|conceded", "change_conditions": ["..."], "confidence": "low|medium|high"}}
"""
        result = self._invoke_codex(session, round_name, council_prompt, use_search=False)
        parsed = self._parse_json_object(result["text"]) or {
            "content": result["text"].strip(),
            "position": "",
            "stance_status": "held",
            "change_conditions": [],
            "confidence": "medium",
        }
        session["session_id"] = result["metadata"].get("session_id") or session.get("session_id")
        return {
            "role_id": role.id,
            "display_name": role.display_name,
            "round": round_name,
            "content": parsed.get("content", result["text"].strip()),
            "position": parsed.get("position", ""),
            "stance_status": parsed.get("stance_status", "held"),
            "change_conditions": parsed.get("change_conditions", []),
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
        result = self._invoke_codex(session, "synthesis", prompt, use_search=False)
        parsed = self._parse_json_object(result["text"])
        if parsed is None and not result["metadata"].get("timed_out"):
            retry_prompt = f"""Convert the following moderator output into strict JSON only.

Question: {question}
Public council context:
{json.dumps(public_context, ensure_ascii=False, indent=2)}

Original output:
{result["text"]}

Return only this JSON shape:
{{
  "winner": "...",
  "ranking": ["..."],
  "confidence": "low|medium|high",
  "caveats": ["..."],
  "summary": "...",
  "tasks": {{"role_id": "task"}}
}}
"""
            retry_result = self._invoke_codex(session, "synthesis-repair", retry_prompt, use_search=False)
            parsed = self._parse_json_object(retry_result["text"])
            if parsed is not None:
                result["metadata"]["repair"] = retry_result["metadata"]
            else:
                result["metadata"]["repair_failed"] = retry_result["metadata"]
        parsed = parsed or self._fallback_synthesis(public_context, result["text"])
        parsed["codex"] = result["metadata"]
        return parsed

    def _invoke_codex(self, session: dict[str, Any], step: str, prompt: str, use_search: bool) -> dict[str, Any]:
        meeting_dir = session.get("meeting_dir")
        if not meeting_dir:
            raise ValueError("CodexAdapter requires meeting_dir in session metadata.")

        from pathlib import Path

        meeting_path = Path(meeting_dir)
        output_path = meeting_path / "roles" / session["role_id"] / f"codex-{step}-last-message.txt"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = ["codex"]
        if self.search_enabled and use_search:
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
        try:
            completed = self.command_runner(
                command,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except TimeoutExpired as error:
            return {
                "text": f"Codex call timed out after {self.timeout_seconds} seconds during {step}.",
                "metadata": {
                    "command": command,
                    "returncode": 124,
                    "stdout": self._text(error.stdout),
                    "stderr": self._text(error.stderr),
                    "session_id": None,
                    "output_last_message": str(output_path),
                    "timeout_seconds": self.timeout_seconds,
                    "timed_out": True,
                },
            }
        text = output_path.read_text(encoding="utf-8") if output_path.exists() else completed.stdout
        session_id = self._extract_session_id(completed.stdout + "\n" + completed.stderr)
        return {
            "text": text,
            "metadata": {
                "command": command,
                "returncode": completed.returncode,
                "stdout": self._text(completed.stdout),
                "stderr": self._text(completed.stderr),
                "session_id": session_id,
                "output_last_message": str(output_path),
            },
        }

    @staticmethod
    def _extract_session_id(output: str) -> str | None:
        match = re.search(r"session id:\s*([0-9a-fA-F-]+)", output)
        return match.group(1) if match else None

    @staticmethod
    def _text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any] | None:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
            stripped = re.sub(r"\s*```$", "", stripped)
        for candidate in (stripped, CodexAdapter._extract_json_candidate(stripped)):
            if not candidate:
                continue
            try:
                value = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        return None

    @staticmethod
    def _extract_json_candidate(text: str) -> str | None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        return text[start : end + 1]

    @staticmethod
    def _depth_payload(depth: ResearchDepth) -> dict[str, Any]:
        return {
            "name": depth.name,
            "label": depth.label,
            "min_sources": depth.min_sources,
            "target_sources": depth.target_sources,
            "min_queries": depth.min_queries,
            "min_claims": depth.min_claims,
            "min_counterclaims": depth.min_counterclaims,
            "notes_per_source": depth.notes_per_source,
        }

    @staticmethod
    def _fallback_research(role: Role, text: str) -> dict[str, Any]:
        return {
            "queries": [],
            "sources": [],
            "summary": text.strip(),
            "confidence": "low",
            "uncertainty": "Codex research output was not parseable as structured JSON.",
            "coverage_gaps": ["Codex research output was not parseable as structured JSON."],
            "claim_evidence": [
                {
                    "claim": text.strip(),
                    "evidence": [],
                    "interpretation": role.research_focus,
                    "confidence": "low",
                    "source_quality": "unknown",
                }
            ],
            "counterclaims": [],
            "rejected_claims": [],
        }

    @staticmethod
    def _fallback_synthesis(public_context: dict[str, Any], text: str) -> dict[str, Any]:
        gate = public_context.get("evidence_gate", {})
        fallback_decision = CodexAdapter._fallback_decision_from_rounds(public_context)
        evidence_status = gate.get("status", "unknown")
        supported = gate.get("total_supported_claims", 0)
        unsupported = gate.get("total_unsupported_claims", 0)
        weak = gate.get("total_weak_claims", 0)
        rejected = gate.get("total_verifier_rejected_claims", 0)
        original = text.strip()
        summary = (
            "반복된 입장과 근거 품질을 기준으로 보수적인 대체 결론을 생성했습니다. "
            "최종 판정은 지원된 근거와 각 라운드에서 반복된 입장을 우선해 해석한 결과입니다."
        )
        if fallback_decision["winner"] != "Undetermined":
            summary = (
                f"반복된 입장과 근거 품질을 기준으로 {fallback_decision['winner']}가 가장 방어 가능한 결론입니다. "
                "다만 모더레이터의 구조화 응답이 완성되지 않아 보수적으로 판정했습니다."
            )
        return {
            "winner": fallback_decision["winner"],
            "ranking": fallback_decision["ranking"],
            "confidence": fallback_decision["confidence"],
            "caveats": ["구조화된 모더레이터 응답이 완성되지 않아 반복된 입장과 근거 품질을 기준으로 보수적으로 판정했습니다."],
            "summary": summary,
            "tasks": fallback_decision["tasks"],
            "fallback": "local_synthesis",
            "status": "degraded",
            "diagnostics": {
                "reason": "moderator_synthesis_unavailable",
                "evidence_gate": {
                    "status": evidence_status,
                    "supported": supported,
                    "unsupported": unsupported,
                    "weak": weak,
                    "verifier_rejected": rejected,
                },
                "had_original_output": bool(original),
            },
        }

    @staticmethod
    def _fallback_decision_from_rounds(public_context: dict[str, Any]) -> dict[str, Any]:
        rounds = public_context.get("rounds", {})
        positions = []
        if isinstance(rounds, dict):
            for messages in rounds.values():
                if not isinstance(messages, list):
                    continue
                for message in messages:
                    if isinstance(message, dict):
                        positions.append(str(message.get("position") or message.get("content") or ""))
        candidate_counts: dict[str, int] = {}
        for position in positions:
            candidate = CodexAdapter._candidate_from_position(position)
            if candidate is not None:
                candidate_counts[candidate] = candidate_counts.get(candidate, 0) + 1
        if not candidate_counts:
            return {
                "winner": "Undetermined",
                "ranking": [],
                "confidence": "low",
                "caveat": "Fallback synthesis avoids choosing a winner without repeated round positions.",
                "tasks": {},
            }
        ranking = [
            candidate
            for candidate, _count in sorted(candidate_counts.items(), key=lambda item: (-item[1], item[0]))
        ]
        winner = ranking[0]
        total_positions = sum(candidate_counts.values())
        top_count = candidate_counts[winner]
        confidence = "medium" if top_count >= 2 and top_count >= (total_positions / 2) else "low"
        return {
            "winner": winner,
            "ranking": ranking,
            "confidence": confidence,
            "caveat": "Fallback winner is inferred from repeated round positions, not from a successful moderator decision.",
            "tasks": {},
        }

    @staticmethod
    def _candidate_from_position(position: str) -> str | None:
        normalized = position.lower()
        if "사카즈키" in position or "아카이누" in position or "sakazuki" in normalized or "akainu" in normalized:
            return "Sakazuki / Akainu"
        if "쿠잔" in position or "아오키지" in position or "kuzan" in normalized or "aokiji" in normalized:
            return "Kuzan / Aokiji"
        if "보르살리노" in position or "키자루" in position or "borsalino" in normalized or "kizaru" in normalized:
            return "Borsalino / Kizaru"
        return None
