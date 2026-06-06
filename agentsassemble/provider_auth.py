from __future__ import annotations


_AUTH_REQUIRED_MARKERS = (
    "unauthenticated",
    "not authenticated",
    "authentication required",
    "not logged in",
    "login required",
    "please sign in",
    "please log in",
    "sign in to",
    "log in to",
)


def provider_login_required_message(provider_label: str, login_command: str) -> str:
    return (
        f"{provider_label} 로그인이 필요합니다. "
        f"터미널에서 {login_command}을 실행해 로그인한 뒤 다시 연결 확인을 누르세요."
    )


def provider_auth_error_message(text: str, *, provider_label: str, login_command: str) -> str:
    normalized = text.casefold()
    if any(marker in normalized for marker in _AUTH_REQUIRED_MARKERS):
        return provider_login_required_message(provider_label, login_command)
    return ""
