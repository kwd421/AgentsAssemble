from __future__ import annotations

from typing import Any

from agentsassemble.adapters.base import ProviderAdapter
from agentsassemble.models import ResearchDepth, Role


class MockAdapter(ProviderAdapter):
    name = "mock"

    def start_session(self, role: Role, meeting_context: dict[str, Any]) -> dict[str, Any]:
        return {
            "adapter": self.name,
            "role_id": role.id,
            "session_id": f"mock-{meeting_context['meeting_id']}-{role.id}",
            "status": "active",
        }

    def run_research(
        self,
        role: Role,
        session: dict[str, Any],
        question: str,
        depth: ResearchDepth,
    ) -> dict[str, Any]:
        source_map = {
            "lore_lawyer": [
                "https://onepiece.fandom.com/wiki/Sakazuki",
                "https://onepiece.fandom.com/wiki/Kuzan",
                "https://onepiece.fandom.com/wiki/Borsalino",
            ],
            "show_me_the_feats": [
                "https://onepiece.fandom.com/wiki/Marineford_Arc",
                "https://onepiece.fandom.com/wiki/Punk_Hazard",
                "https://onepiece.fandom.com/wiki/Sabaody_Archipelago_Arc",
            ],
            "fanboard_skeptic": [
                "https://gall.dcinside.com/mgallery/board/lists/?id=onepieceblood",
                "https://gall.dcinside.com/board/lists/?id=comic_new3",
                "https://onepiece.fandom.com/wiki/Admiral",
            ],
        }
        claims = {
            "lore_lawyer": "공식 지위, 서사상 위치, 펑크 하자드 결투 결과를 가장 높게 보면 사카즈키가 제일 강하다는 쪽이 가장 정합적입니다.",
            "show_me_the_feats": "직접 보여준 결과만 놓고 보면 사카즈키가 쿠잔과의 장기 결투에서 이겼으니 우세로 보는 게 맞습니다.",
            "fanboard_skeptic": "사카즈키 1위가 제일 무난하지만, 보르살리노는 전력을 다한 장면이 적어서 확신도는 너무 높이면 안 됩니다.",
        }
        queries = [
            f"{question} {role.display_name}",
            f"One Piece admirals strength {role.lens}",
        ]
        urls = self._expand_sources(source_map[role.id], depth.target_sources, role.id)
        claim_evidence = [
            {
                "claim": f"{claims[role.id]} (근거 항목 {index + 1})",
                "evidence": urls[index : index + max(1, min(3, len(urls)))],
                "interpretation": role.research_focus,
                "confidence": "medium",
                "source_quality": "mixed",
            }
            for index in range(depth.min_claims)
        ]
        counterclaims = [
            {
                "claim": f"키자루나 쿠잔을 더 높게 볼 여지도 있다. (반론 {index + 1})",
                "evidence": urls[-(index + 1) :],
                "why_it_matters": "전투력 비교는 직접 승패, 설정, 미공개 전력의 가중치에 따라 결론이 흔들릴 수 있습니다.",
                "confidence": "medium",
            }
            for index in range(max(1, depth.min_counterclaims))
        ]
        return {
            "role_id": role.id,
            "display_name": role.display_name,
            "research_depth": {
                "name": depth.name,
                "label": depth.label,
                "min_sources": depth.min_sources,
                "target_sources": depth.target_sources,
                "min_queries": depth.min_queries,
                "min_claims": depth.min_claims,
                "min_counterclaims": depth.min_counterclaims,
                "notes_per_source": depth.notes_per_source,
            },
            "queries": self._expand_queries(queries, depth.min_queries, role),
            "sources": [
                {
                    "url": url,
                    "note": f"{role.display_name} 관점에서 {role.research_focus} 기준으로 본 참고 자료입니다.",
                    "snippet": "목 데모용 짧은 출처 메모입니다. 실제 웹 리서치 단계에서는 원문 근거나 요약이 들어갑니다.",
                    "source_type": "mock",
                    "quality": "demo",
                    "extracted_notes": [
                        f"{role.display_name}의 {depth.name} 리서치 노트 {index + 1}: 이 자료가 주장 검증에 어떻게 쓰이는지 기록합니다."
                        for index in range(depth.notes_per_source)
                    ],
                }
                for url in urls
            ],
            "summary": claims[role.id],
            "confidence": "medium",
            "uncertainty": "전투력 비교는 공식 언급, 전투 맥락, 아직 덜 공개된 전력이 완전히 맞물리지 않아서 해석 여지가 있습니다.",
            "claim_evidence": claim_evidence,
            "counterclaims": counterclaims,
            "rejected_claims": [
                {
                    "claim": "팬덤에서 자주 보이지만 근거가 약한 확정식 서열 주장",
                    "reason": "직접 근거와 공식 근거가 부족해서 보류 처리합니다.",
                    "sources": urls[-2:],
                }
            ],
        }

    @staticmethod
    def _expand_queries(queries: list[str], count: int, role: Role) -> list[str]:
        expanded = list(queries)
        while len(expanded) < count:
            expanded.append(f"{role.display_name} depth query {len(expanded) + 1} {role.research_focus}")
        return expanded

    @staticmethod
    def _expand_sources(urls: list[str], count: int, role_id: str) -> list[str]:
        expanded = list(urls)
        while len(expanded) < count:
            expanded.append(f"https://example.com/agentsassemble/mock/{role_id}/source-{len(expanded) + 1}")
        return expanded

    def run_round(
        self,
        role: Role,
        session: dict[str, Any],
        round_name: str,
        prompt: str,
        public_context: dict[str, Any],
    ) -> dict[str, Any]:
        if round_name == "round_1":
            research = public_context["own_research"]
            openers = {
                "lore_lawyer": "공식 설정상 먼저 근거 등급부터 따져야 합니다.",
                "show_me_the_feats": "보여준 걸 가져와야죠. 말보다 전투 결과가 우선입니다.",
                "fanboard_skeptic": "게이야 그건 근거가 아니라 팬심인지부터 봐야 한다 ㅋㅋ",
            }
            content = f"{role.display_name}: {openers[role.id]} 내 결론은 아카이누 우세입니다. 근거는 {research['summary']} 다만 {research['uncertainty']}"
        else:
            rebuttals = {
                "lore_lawyer": "전적만 보면 맥락을 놓칩니다. 공식 지위와 서사 배치도 같이 봐야 합니다.",
                "show_me_the_feats": "설정은 말이고 전투는 결과입니다. 실제로 승부가 난 쪽을 무시하면 안 됩니다.",
                "fanboard_skeptic": "작중에 안 나온 걸 왜 확정 박노? 키자루는 표본 부족이라 보류가 맞다.",
            }
            content = f"{role.display_name}: {rebuttals[role.id]} 그래서 아카이누 1위는 유지하되, 근거별 확신도는 분리해서 적어야 합니다."
        return {
            "role_id": role.id,
            "display_name": role.display_name,
            "round": round_name,
            "content": content,
            "confidence": "medium",
        }

    def synthesize(
        self,
        session: dict[str, Any],
        question: str,
        public_context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "winner": "Sakazuki / Akainu",
            "ranking": ["Sakazuki / Akainu", "Kuzan / Aokiji", "Borsalino / Kizaru"],
            "confidence": "medium",
            "caveats": [
                "키자루는 전력을 다한 장면이 적어서 평가가 흔들릴 수 있습니다.",
                "만화, 애니, 설정집, 팬덤 해석은 근거 가중치가 서로 다를 수 있습니다.",
            ],
            "summary": (
                "세 역할 모두 아카이누를 가장 설득력 있는 1위로 보았다. "
                "설정충은 공식/서사 지위와 Punk Hazard 결과를, 공식이뭘알아는 직접 전적을, "
                "만갤러는 팬덤 과장 가능성과 불확실성을 근거로 confidence를 조정했다."
            ),
            "tasks": {
                "lore_lawyer": "공식 설정과 원작 근거의 source hierarchy를 더 정리한다.",
                "show_me_the_feats": "주요 전투 장면과 결과를 feat table로 정리한다.",
                "fanboard_skeptic": "반례와 팬덤 과장 주장 목록을 분리해 검토한다.",
            },
        }
