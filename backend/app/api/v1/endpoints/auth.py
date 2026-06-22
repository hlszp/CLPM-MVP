"""Authentication endpoints (placeholder).

Concrete login/logout/refresh flows are implemented in the auth task. This
module only registers the router so the API surface is stable.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/ping")
async def auth_ping() -> dict[str, str]:
    """Placeholder auth route to verify the router is wired."""
    return {"module": "auth", "status": "placeholder"}
