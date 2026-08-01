"""Local-operator routes for the imported bot-card and Risu-module library."""

from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus

from agentsassemble.persona_cards.library import (
    PersonaLibraryError,
    import_persona_asset,
    list_persona_assets,
    persona_thumbnail_path,
)
from agentsassemble.web.router import RequestContext, Router


def register_persona_routes(
    router: Router,
    *,
    is_local_operator: Callable[[RequestContext], bool],
) -> None:
    def require_local_operator(ctx: RequestContext) -> bool:
        if is_local_operator(ctx):
            return True
        ctx.send_error(
            HTTPStatus.FORBIDDEN,
            "The bot-card library is limited to the local operator UI.",
        )
        return False

    @router.get("/api/personas")
    def list_personas(ctx: RequestContext) -> None:
        if not require_local_operator(ctx):
            return
        ctx.send_json({"items": list_persona_assets(ctx.deps.output_root)})

    @router.post("/api/personas/import")
    def import_persona(ctx: RequestContext) -> None:
        if not require_local_operator(ctx):
            return
        payload = ctx.read_json_body()
        if payload is None:
            return
        try:
            persona = import_persona_asset(
                ctx.deps.output_root,
                filename=str(payload.get("filename") or ""),
                data_base64=payload.get("data_base64"),
            )
        except (OSError, PersonaLibraryError, ValueError) as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json({"persona": persona})

    @router.get_dynamic("/api/personas/{persona_id}/thumbnail")
    def persona_thumbnail(ctx: RequestContext, params: dict[str, str]) -> None:
        if not require_local_operator(ctx):
            return
        try:
            file_path, content_type = persona_thumbnail_path(
                ctx.deps.output_root,
                params["persona_id"],
            )
        except (OSError, PersonaLibraryError, ValueError) as error:
            ctx.send_error(HTTPStatus.NOT_FOUND, str(error))
            return
        ctx.send_attachment_file(
            file_path,
            {
                "filename": file_path.name,
                "content_type": content_type,
                "size": file_path.stat().st_size,
                "supported": True,
                "is_image": True,
            },
            inline=True,
        )


__all__ = ["register_persona_routes"]
