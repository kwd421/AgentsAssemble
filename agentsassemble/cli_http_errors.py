from __future__ import annotations


class CliHttpError(ValueError):
    """Machine-readable HTTP failure for CLI control flows."""

    def __init__(self, message: str, *, status_code: int, code: str = "") -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = code or _status_code_name(self.status_code)


def _status_code_name(status_code: int) -> str:
    return {
        400: "bad_request",
        401: "unauthenticated",
        403: "permission_denied",
        404: "not_found",
        409: "conflict",
    }.get(int(status_code), "http_error")
