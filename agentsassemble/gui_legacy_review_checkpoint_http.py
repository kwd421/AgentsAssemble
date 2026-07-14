"""HTTP route for retained resident review checkpoints."""
from __future__ import annotations

from http import HTTPStatus

from agentsassemble.gui_router import RequestContext, Router
from agentsassemble.legacy_review_checkpoint import LegacyReviewCheckpointService


def register_legacy_review_checkpoint_route(
    router: Router,
    *,
    service: LegacyReviewCheckpointService,
) -> None:
    @router.post_dynamic("/api/meetings/{meeting_id}/review-checkpoints")
    def create_review_checkpoint(ctx: RequestContext, params: dict[str, str]) -> None:
        payload = ctx.read_json_body()
        if payload is None:
            service.record_invalid_json()
            return
        try:
            result = service.create(params["meeting_id"], payload)
        except ValueError as error:
            ctx.send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        ctx.send_json(result)
