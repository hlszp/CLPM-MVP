"""Common response envelope schemas (IDS v3.2 §6)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ResponseEnvelope(BaseModel):
    """Unified response envelope: ``{code, message, data}``."""

    code: str = "0"
    message: str = "success"
    data: Any = None


def success(data: Any = None, message: str = "success") -> dict[str, Any]:
    """Build a success response dict."""
    return {"code": "0", "message": message, "data": data}
