from __future__ import annotations

from copy import deepcopy

from agentsassemble.live_agent_rounds import build_official_round_turns
from agentsassemble.legacy.meeting.core.events import clean_lobby_text


PLAY_PRESETS: dict[str, dict[str, str]] = {
    "meme_debate_fast": {
        "label": "짧은 밈 토론",
        "instruction": (
            "Play Mode 짧은 토론 라운드다. 심판처럼 판정하지 말고 토론자로 말해라. "
            "자기 결론을 분명히 말하고, 상대 주장 하나만 찔러라. 한국어 공식 발언 하나만, 300자 이내."
        ),
    },
    "meme_debate_argument": {
        "label": "강한 반박 라운드",
        "instruction": (
            "Play Mode 강한 반박 라운드다. 누가 이기나에 대해 네 입장을 더 세게 밀어라. "
            "상대 주장 중 하나를 찌르고, 네 결론이 바뀌려면 어떤 조건이 필요한지도 말해라. "
            "심판처럼 판정하지 말고 토론자로 말해라. 한국어 공식 발언 하나만, 700자 이내."
        ),
    },
    "concession_round": {
        "label": "입장 변경 조건 라운드",
        "instruction": (
            "Play Mode 입장 변경 조건 라운드다. 현재 결론을 유지하되, 어떤 증거나 조건이면 "
            "네 결론을 바꿀지 구체적으로 말해라. 상대의 가장 강한 논점을 하나 인정하고 반박하라. "
            "한국어 공식 발언 하나만, 600자 이내."
        ),
    },
}


def available_play_presets() -> list[dict[str, str]]:
    return [
        {"id": preset_id, "label": preset["label"], "instruction": preset["instruction"]}
        for preset_id, preset in PLAY_PRESETS.items()
    ]


def build_play_preset_turns(
    meeting: dict[str, object],
    live_agents: list[dict[str, object]],
    *,
    meeting_id: str,
    preset_id: str,
    role_ids: list[str] | None = None,
) -> dict[str, object]:
    clean_preset_id = clean_lobby_text(preset_id, limit=128)
    preset = PLAY_PRESETS.get(clean_preset_id)
    if preset is None:
        raise ValueError(f"Unknown play preset: {clean_preset_id or '(blank)'}.")
    round_id = f"play_preset:{clean_preset_id}"
    synthetic_meeting = deepcopy(meeting)
    synthetic_meeting["meeting_template"] = {
        "rounds": [
            {
                "id": round_id,
                "instruction": preset["instruction"],
                "turn_control": {
                    "selection": "selected_roles" if role_ids else "all_roles",
                    "speaker_role_ids": list(role_ids or []),
                },
            }
        ]
    }
    round_turns = build_official_round_turns(
        synthetic_meeting,
        live_agents,
        meeting_id=meeting_id,
        round_id=round_id,
        role_ids=role_ids,
    )
    return {
        "preset_id": clean_preset_id,
        "label": preset["label"],
        "round_id": round_turns["round_id"],
        "role_ids": round_turns["role_ids"],
        "turns": round_turns["turns"],
    }
