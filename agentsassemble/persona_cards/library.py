"""Operator-facing library and provider-selection boundary for persona assets."""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
import tempfile

from agentsassemble.character_mode import clean_persona_card_id
from agentsassemble.persona_cards.imports import (
    import_ccv3_persona,
    import_charx_persona,
    import_risum_persona,
)
from agentsassemble.persona_cards.models import PersonaCard
from agentsassemble.persona_cards.storage import load_persona_card
from agentsassemble.providers.launch_specs import native_cli_provider_definition


MAX_PERSONA_UPLOAD_BYTES = 10 * 1024 * 1024
_CARD_SUFFIXES = {".json", ".png", ".apng", ".charx"}
_MODULE_SUFFIXES = {".risum"}


class PersonaLibraryError(ValueError):
    pass


class PersonaSelectionError(PersonaLibraryError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def list_persona_assets(output_root: Path) -> list[dict[str, object]]:
    root = Path(output_root) / "personas"
    if not root.is_dir():
        return []
    assets: list[dict[str, object]] = []
    for directory in root.iterdir():
        if not directory.is_dir() or directory.is_symlink():
            continue
        card_path = directory / "card.json"
        try:
            card = load_persona_card(card_path)
        except (OSError, ValueError):
            continue
        clean_id = clean_persona_card_id(card.id)
        if not clean_id or clean_id != directory.name:
            continue
        assets.append(persona_asset_summary(card))
    return sorted(
        assets,
        key=lambda item: (
            str(item.get("asset_kind") or ""),
            str(item.get("display_name") or "").casefold(),
            str(item.get("id") or ""),
        ),
    )


def persona_asset_summary(card: PersonaCard) -> dict[str, object]:
    source_kind = str(card.source.get("kind") or "").strip()
    asset_kind = "module" if source_kind == "risu_module" else "card"
    ignored_feature_count = sum(
        max(0, int(value)) for value in card.ignored_features.values()
    )
    return {
        "id": clean_persona_card_id(card.id),
        "display_name": card.display_name,
        "asset_kind": asset_kind,
        "source_kind": source_kind,
        "lorebook_count": len(card.lorebook),
        "asset_count": len(card.assets),
        "ignored_feature_count": ignored_feature_count,
        "tag_count": len(card.tags),
        "thumbnail_url": (
            f"/api/personas/{clean_persona_card_id(card.id)}/thumbnail"
            if asset_kind == "card" and persona_thumbnail_path_for_card(card)
            else ""
        ),
    }


def resolve_persona_selection(
    output_root: Path,
    provider_id: str,
    persona_card_id: str,
) -> dict[str, object]:
    clean_id = clean_persona_card_id(persona_card_id)
    if not clean_id:
        return {}
    definition = native_cli_provider_definition(provider_id)
    if definition is None or definition.catalog_group not in {"api", "local"}:
        raise PersonaSelectionError(
            "Bot cards and Risu modules are available only to API and Local Agent Sessions.",
            code="persona_provider_unsupported",
        )
    card = load_persona_asset(output_root, clean_id)
    return persona_asset_summary(card)


def load_persona_asset(output_root: Path, persona_card_id: str) -> PersonaCard:
    clean_id = clean_persona_card_id(persona_card_id)
    if not clean_id:
        raise PersonaSelectionError(
            "Persona asset id is invalid.",
            code="persona_not_found",
        )
    card_path = Path(output_root) / "personas" / clean_id / "card.json"
    try:
        card = load_persona_card(card_path)
    except (OSError, ValueError) as error:
        raise PersonaSelectionError(
            "The selected bot card or module is unavailable.",
            code="persona_not_found",
        ) from error
    if clean_persona_card_id(card.id) != clean_id:
        raise PersonaSelectionError(
            "The selected bot card or module is invalid.",
            code="persona_not_found",
        )
    return card


def import_persona_asset(
    output_root: Path,
    *,
    filename: str,
    data_base64: object,
) -> dict[str, object]:
    clean_filename = Path(str(filename or "").replace("\\", "/")).name
    suffix = Path(clean_filename).suffix.casefold()
    if not clean_filename or suffix not in _CARD_SUFFIXES | _MODULE_SUFFIXES:
        raise PersonaLibraryError(
            "Supported persona files are .json, .png, .apng, .charx, and .risum."
        )
    raw = _decode_upload(data_base64)
    with tempfile.TemporaryDirectory(prefix="agentsassemble-persona-") as directory:
        source_path = Path(directory) / clean_filename
        source_path.write_bytes(raw)
        if suffix == ".charx":
            report = import_charx_persona(source_path, output_root=Path(output_root))
        elif suffix == ".risum":
            report = import_risum_persona(source_path, output_root=Path(output_root))
        else:
            report = import_ccv3_persona(source_path, output_root=Path(output_root))
    return persona_asset_summary(report.card)


def persona_thumbnail_path(output_root: Path, persona_card_id: str) -> tuple[Path, str]:
    card = load_persona_asset(output_root, persona_card_id)
    relative_path = persona_thumbnail_path_for_card(card)
    if not relative_path:
        raise PersonaLibraryError("Persona thumbnail is unavailable.")
    persona_root = (Path(output_root) / "personas" / clean_persona_card_id(card.id)).resolve()
    candidate = (persona_root / relative_path).resolve()
    try:
        candidate.relative_to(persona_root)
    except ValueError as error:
        raise PersonaLibraryError("Persona thumbnail path is invalid.") from error
    if not candidate.is_file():
        raise PersonaLibraryError("Persona thumbnail is unavailable.")
    mime_type = next(
        (
            asset.mime_type
            for asset in card.assets
            if asset.path == relative_path
        ),
        "image/png",
    )
    return candidate, mime_type


def persona_thumbnail_path_for_card(card: PersonaCard) -> str:
    images = [asset for asset in card.assets if asset.mime_type.startswith("image/")]
    preferred = next(
        (
            asset
            for asset in images
            if str(asset.metadata.get("type") or "").casefold()
            in {"icon", "avatar", "portrait"}
        ),
        None,
    )
    selected = preferred or (images[0] if images else None)
    return selected.path if selected is not None else ""


def _decode_upload(value: object) -> bytes:
    if not isinstance(value, str) or not value.strip():
        raise PersonaLibraryError("Persona file data is required.")
    encoded = value.strip()
    if "," in encoded and encoded[:64].casefold().startswith("data:"):
        encoded = encoded.split(",", 1)[1]
    if len(encoded) > ((MAX_PERSONA_UPLOAD_BYTES + 2) // 3) * 4:
        raise PersonaLibraryError("Persona file is too large.")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise PersonaLibraryError("Persona file data is invalid.") from error
    if len(raw) > MAX_PERSONA_UPLOAD_BYTES:
        raise PersonaLibraryError("Persona file is too large.")
    return raw


__all__ = [
    "MAX_PERSONA_UPLOAD_BYTES",
    "PersonaLibraryError",
    "PersonaSelectionError",
    "import_persona_asset",
    "list_persona_assets",
    "load_persona_asset",
    "persona_asset_summary",
    "persona_thumbnail_path",
    "resolve_persona_selection",
]
