"""Execution for current persona CLI commands."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from agentsassemble.persona_cards import (
    PersonaImportReport,
    import_ccv3_persona,
    import_charx_persona,
    import_risum_persona,
    load_persona_card,
    persona_card_from_risu_module,
    read_risum_module,
    render_persona_prompt,
    scan_persona_lore,
)


def run_persona_command(args: argparse.Namespace) -> int:
    rpack_map_path = Path(args.rpack_map) if getattr(args, "rpack_map", "") else None
    if args.persona_command == "inspect-risum":
        payload = read_risum_module(Path(args.file), rpack_map_path=rpack_map_path)
        card = persona_card_from_risu_module(payload.module, source_name=Path(args.file).name)
        report = PersonaImportReport(
            card=card,
            card_path=Path(""),
            asset_count=len(payload.asset_payloads),
            source_path=str(args.file),
        )
        if args.as_json:
            print(json.dumps(report.to_safe_dict(), ensure_ascii=False, indent=2))
        else:
            print(
                f"{card.display_name}: {len(card.lorebook)} lore entries, "
                f"{len(payload.asset_payloads)} assets, ignored {card.ignored_features}"
            )
        return 0
    if args.persona_command == "import-risum":
        report = import_risum_persona(
            Path(args.file),
            output_root=Path(args.output_root),
            rpack_map_path=rpack_map_path,
        )
        if args.as_json:
            print(json.dumps(report.to_safe_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"Imported {report.card.display_name} persona card: {report.card_path}")
        return 0
    if args.persona_command == "import-ccv3":
        report = import_ccv3_persona(
            Path(args.file),
            output_root=Path(args.output_root),
        )
        if args.as_json:
            print(json.dumps(report.to_safe_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"Imported {report.card.display_name} persona card: {report.card_path}")
        return 0
    if args.persona_command == "import-charx":
        report = import_charx_persona(
            Path(args.file),
            output_root=Path(args.output_root),
        )
        if args.as_json:
            print(json.dumps(report.to_safe_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"Imported {report.card.display_name} persona card: {report.card_path}")
        return 0
    if args.persona_command == "scan":
        card = load_persona_card(Path(args.card))
        scan = scan_persona_lore(card, args.context)
        payload = {
            "persona": card.safe_summary(),
            "active_lore": [
                {
                    "key": entry.key,
                    "comment": entry.comment,
                    "content": entry.content,
                    "insert_order": entry.insert_order,
                }
                for entry in scan.entries
            ],
            "state": scan.state,
            "ignored_features": scan.ignored_features,
        }
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"{card.display_name}: {len(scan.entries)} active lore entries")
            for entry in scan.entries:
                print(f"- {entry.comment or entry.key or 'lore'}")
        return 0
    if args.persona_command == "render":
        card = load_persona_card(Path(args.card))
        render = render_persona_prompt(
            card,
            recent_messages=args.context,
            user_name=args.user,
            persona=args.persona,
            variables=parse_persona_slot_values(args.slot),
            mode=args.mode,
            surface=args.surface,
            first_message_index=int(args.first_message_index),
        )
        payload = {
            "persona": card.safe_summary(),
            "mode": render.mode,
            "surface": render.surface,
            "lines": render.lines,
            "active_lore_count": len(render.scan.entries),
            "state": render.scan.state,
        }
        if args.as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("\n".join(render.lines))
        return 0
    return 1


def parse_persona_slot_values(values: list[str]) -> dict[str, str]:
    slots: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError(f"persona slot must be KEY=VALUE: {value}")
        key, slot_value = value.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("persona slot key must not be empty")
        slots[key] = slot_value
    return slots
