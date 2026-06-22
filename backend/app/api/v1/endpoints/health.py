"""Health check endpoint.

Mounted at root level so Kubernetes/container probes can reach ``/health``
without the ``/api/v1`` business prefix.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness/readiness probe."""
    return {"status": "ok", "version": settings.APP_VERSION}
