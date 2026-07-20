from __future__ import annotations

from agentsassemble.models import MeetingRound


DEMO_MEETING_TEMPLATE = {
    "id": "one_piece_admiral_debate_v0",
    "display_name": "원피스 3대장 최강자 토론",
    "rounds": [
        MeetingRound(
            id="round_1",
            title="Round 1",
            report_label="Round 1: opening positions",
            context_scope="own_research",
            instruction=(
                "Present your opening position from your private research. Cite your strongest evidence "
                "and at least one uncertainty. State the position you are defending and the evidence that "
                "would make you change your mind."
            ),
        ),
        MeetingRound(
            id="round_2",
            title="Round 2",
            report_label="Round 2: rebuttal and evidence comparison",
            context_scope="public_debate",
            instruction=(
                "Compare evidence and rebut weak reasoning without reading private research. Challenge source "
                "quality, unsupported leaps, and missing counterevidence. Hold your position unless the public "
                "evidence crosses your stated change conditions; if you revise, say exactly which evidence caused it."
            ),
        ),
    ],
}
