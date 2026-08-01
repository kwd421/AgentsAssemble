"""Stable revisions for the public provider-selection contract."""

from __future__ import annotations

import hashlib
import json


def catalog_revision(providers: list[dict[str, object]]) -> str:
    public_contract = [
        {
            "id": provider.get("id"),
            "source": provider.get("catalog_source"),
            "status": provider.get("discovery_status"),
            "catalog_group": provider.get("catalog_group"),
            "login_available": provider.get("login_available"),
            "login_flow": provider.get("login_flow"),
            "controls": provider.get("controls"),
            "fixed_values": provider.get("fixed_values"),
        }
        for provider in providers
    ]
    encoded = json.dumps(
        public_contract,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"cat-{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:16]}"
