"""Persona command parser registrations."""
from __future__ import annotations

import argparse


def register_persona_parsers(subparsers: argparse._SubParsersAction) -> None:
    persona = subparsers.add_parser("persona", help="Inspect and import Play Mode persona cards.")
    persona_subparsers = persona.add_subparsers(dest="persona_command", required=True)

    persona_inspect_risum = persona_subparsers.add_parser(
        "inspect-risum",
        help="Inspect a RisuAI .risum module without executing module runtime features.",
    )
    persona_inspect_risum.add_argument("--file", required=True)
    persona_inspect_risum.add_argument("--rpack-map", default="")
    persona_inspect_risum.add_argument("--json", action="store_true", dest="as_json")

    persona_import_risum = persona_subparsers.add_parser(
        "import-risum",
        help="Import a RisuAI .risum module as a Play Mode persona card.",
    )
    persona_import_risum.add_argument("--file", required=True)
    persona_import_risum.add_argument("--output-root", default=".agentsassemble")
    persona_import_risum.add_argument("--rpack-map", default="")
    persona_import_risum.add_argument("--json", action="store_true", dest="as_json")

    persona_import_ccv3 = persona_subparsers.add_parser(
        "import-ccv3",
        help="Import a Character Card V3 JSON or PNG as a persona card.",
    )
    persona_import_ccv3.add_argument("--file", required=True)
    persona_import_ccv3.add_argument("--output-root", default=".agentsassemble")
    persona_import_ccv3.add_argument("--json", action="store_true", dest="as_json")

    persona_import_charx = persona_subparsers.add_parser(
        "import-charx",
        help="Import a Character Card V3 CHARX bundle as a persona card.",
    )
    persona_import_charx.add_argument("--file", required=True)
    persona_import_charx.add_argument("--output-root", default=".agentsassemble")
    persona_import_charx.add_argument("--json", action="store_true", dest="as_json")

    persona_scan = persona_subparsers.add_parser(
        "scan",
        help="Show which stored persona lore entries activate for a room context.",
    )
    persona_scan.add_argument("--card", required=True)
    persona_scan.add_argument("--context", default="")
    persona_scan.add_argument("--json", action="store_true", dest="as_json")

    persona_render = persona_subparsers.add_parser(
        "render",
        help="Render the persona prompt blocks used by Character Mode.",
    )
    persona_render.add_argument("--card", required=True)
    persona_render.add_argument("--context", default="")
    persona_render.add_argument("--mode", choices=["off", "on", "work_speech_only"], default="on")
    persona_render.add_argument("--surface", choices=["play_speech", "work_speech", "artifact"], default="play_speech")
    persona_render.add_argument("--user", default="user")
    persona_render.add_argument("--persona", default="")
    persona_render.add_argument("--slot", action="append", default=[])
    persona_render.add_argument("--first-message-index", type=int, default=0)
    persona_render.add_argument("--json", action="store_true", dest="as_json")
