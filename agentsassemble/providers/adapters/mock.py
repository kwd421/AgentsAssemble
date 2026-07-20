from __future__ import annotations

from typing import Any

from agentsassemble.providers.adapters.base import ProviderAdapter
from agentsassemble.models import ResearchDepth, ResearchSteering, Role


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
        steering: ResearchSteering,
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
        default_sources = [
            f"https://example.com/agentsassemble/mock/{role.id}/overview",
            f"https://example.com/agentsassemble/mock/{role.id}/counterpoint",
            f"https://example.com/agentsassemble/mock/{role.id}/constraints",
        ]
        default_claim = f"{role.display_name} 관점에서는 {role.research_focus} 기준으로 조건부 결론을 방어할 수 있습니다."
        queries = [
            f"{question} {role.display_name}",
            f"{question} {role.lens}",
        ]
        urls = self._expand_sources(source_map.get(role.id, default_sources), depth.target_sources, role.id)
        role_claim = claims.get(role.id, default_claim)
        claim_evidence = [
            {
                "claim": f"{role_claim} (근거 항목 {index + 1})",
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
            "research_steering": steering.to_dict(),
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
            "summary": role_claim,
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
            spoken_reasons = {
                "lore_lawyer": "공식 지위, 서사 배치, 펑크 하자드 결과를 같이 보면 사카즈키 쪽이 가장 안정적입니다.",
                "show_me_the_feats": "직접 승부가 난 기록을 우선하면 사카즈키가 쿠잔보다 앞섭니다.",
                "fanboard_skeptic": "아카이누 1위는 무난하지만, 키자루 전력 표본이 적다는 점은 계속 걸립니다.",
                "animal_spec_nerd": "고릴라의 체중, 악력, 순간 폭발력은 개인 단위 인간을 압도합니다.",
                "gym_tactics_bro": "100명이 진짜 조율되고 도망을 금지당하면 숫자와 압박이 전투력을 만듭니다.",
                "playground_skeptic": "100명이 동시에 덮친다는 말은 쉽지만 앞줄 공포와 부상 연쇄를 빼면 반쪽짜리 룰입니다.",
            }
            opener = openers.get(role.id, f"{role.research_focus}부터 따져보겠습니다.")
            spoken_reason = spoken_reasons.get(role.id, research.get("summary", f"{role.lens} 관점의 근거가 있습니다."))
            content = (
                f"{role.display_name}: {opener} "
                f"내 판단은 조건부 우세입니다. {spoken_reason} "
                f"다만 룰과 전제에 따라 결론이 흔들릴 수 있어서 불확실합니다."
            )
            position = "조건부 우세"
            stance_status = "held"
            change_conditions = [
                "현재 결론보다 강한 직접 근거",
                "룰 전제를 바꾸는 공개 반례",
            ]
        else:
            rebuttals = {
                "lore_lawyer": "전적만 보면 맥락을 놓칩니다. 공식 지위와 서사 배치도 같이 봐야 합니다.",
                "show_me_the_feats": "설정은 말이고 전투는 결과입니다. 실제로 승부가 난 쪽을 무시하면 안 됩니다.",
                "fanboard_skeptic": "작중에 안 나온 걸 왜 확정 박노? 키자루는 표본 부족이라 보류가 맞다.",
                "animal_spec_nerd": "숫자가 많아도 첫 접촉에서 몇 명이 바로 무너지면 대형동물 쪽 공포 효과가 커집니다.",
                "gym_tactics_bro": "겁먹는다는 반박은 맞지만, 룰이 넓은 경기장과 조율을 보장하면 사람 쪽 플랜이 생깁니다.",
                "playground_skeptic": "둘 다 조건빨입니다. 경기장 크기, 도망 금지, 사전 합의 없으면 결론 확정은 오바죠.",
            }
            rebuttal = rebuttals.get(role.id, "상대 주장은 전제와 근거 품질을 분리해서 봐야 합니다.")
            content = f"{role.display_name}: {rebuttal} 그래서 내 결론은 유지하되, 확신도는 근거별로 분리해서 적어야 합니다."
            position = "조건부 우세, 확신도는 근거별 분리"
            stance_status = "held"
            change_conditions = [
                "반대편이 supported claim으로 직접 승패나 공식 비교를 제시할 때",
            ]
        return {
            "role_id": role.id,
            "display_name": role.display_name,
            "round": round_name,
            "content": content,
            "position": position,
            "stance_status": stance_status,
            "change_conditions": change_conditions,
            "confidence": "medium",
        }

    def synthesize(
        self,
        session: dict[str, Any],
        question: str,
        public_context: dict[str, Any],
    ) -> dict[str, Any]:
        if "고릴라" in question or "gorilla" in question.casefold():
            return {
                "winner": "100명의 조율된 보디빌더",
                "ranking": ["100명의 조율된 보디빌더", "성체 수컷 실버백 고릴라"],
                "confidence": "low",
                "caveats": [
                    "100명이 실제로 도망치지 않고 조율된다는 전제가 강합니다.",
                    "고릴라의 초반 돌진과 부상 위험은 매우 크게 봐야 합니다.",
                ],
                "summary": (
                    "목 데모 판정은 조율된 100명의 숫자 우위를 더 높게 보되, "
                    "심리 붕괴와 부상 리스크 때문에 confidence를 낮게 둔다."
                ),
                "tasks": {
                    "animal_spec_nerd": "고릴라 신체 스펙과 행동 리스크를 더 검토한다.",
                    "gym_tactics_bro": "100명 협동 전술의 현실성을 더 검토한다.",
                    "playground_skeptic": "룰 허점과 공포로 인한 붕괴 가능성을 더 검토한다.",
                },
            }
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
