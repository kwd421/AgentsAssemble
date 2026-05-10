from __future__ import annotations


ROUND_SPEECH_POLICY = """Speech policy:
- Research is raw material, not your spoken message. Do not dump research notes, source lists, or long evidence tables into content.
- Speak like a council participant: state your judgment, the 2-3 reasons that changed your mind most, and one caveat if needed.
- Keep content to 4-8 Korean sentences in at most 2 short paragraphs.
- In rebuttal or later rounds, reference at least one previous speaker by name and respond to what they said.
- Do not repeat evidence already stated unless you are using it to agree, challenge, or refine another speaker's claim.
- Preserve your role/persona and stance persistence. Revise only when specific supported evidence changes your reasoning.
- Use stance_status values: held|qualified|reframed|revised|conceded.
- Use qualified when you keep the conclusion but add limits; reframed when you keep the conclusion but change the decision criterion.
- If your stance changes, include stance_delta, changed_by, change_reason, and remaining_resistance.
- Apply persona dynamics from personality/style such as stubbornness, respect_for_evidence, conflict_style, and concession_style.
- Include emotion as tone plus numeric friction, stubbornness, respect, and engagement from 0.0 to 1.0.
"""

ROUND_RESPONSE_SCHEMA = """Return only JSON:
{"content":"...","position":"...","stance_status":"held|qualified|reframed|revised|conceded","stance_delta":"none|minor|moderate|major","changed_by":["agent_id_or_name"],"change_reason":"...","remaining_resistance":"...","emotion":{"tone":"calm|playful|heated|reluctant|impressed|skeptical","friction":0.0,"stubbornness":0.0,"respect":0.0,"engagement":0.0},"change_conditions":["..."],"confidence":"low|medium|high"}
"""
