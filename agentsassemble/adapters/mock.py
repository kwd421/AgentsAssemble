from __future__ import annotations

from typing import Any

from agentsassemble.adapters.base import ProviderAdapter
from agentsassemble.models import Role


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
                "https://www.reddit.com/r/OnePiece/",
                "https://onepiece.fandom.com/wiki/Admiral",
                "https://onepiece.fandom.com/wiki/Haki",
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
        urls = source_map[role.id]
        return {
            "role_id": role.id,
            "display_name": role.display_name,
            "queries": queries,
            "sources": [
                {
                    "url": url,
                    "note": f"{role.display_name} 관점에서 {role.research_focus} 기준으로 본 참고 자료입니다.",
                    "snippet": "목 데모용 짧은 출처 메모입니다. 실제 웹 리서치 단계에서는 원문 근거나 요약이 들어갑니다.",
                }
                for url in urls
            ],
            "summary": claims[role.id],
            "confidence": "medium",
            "uncertainty": "전투력 비교는 공식 언급, 전투 맥락, 아직 덜 공개된 전력이 완전히 맞물리지 않아서 해석 여지가 있습니다.",
            "claim_evidence": [
                {
                    "claim": claims[role.id],
                    "evidence": urls,
                    "interpretation": role.research_focus,
                    "confidence": "medium",
                }
            ],
        }

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
