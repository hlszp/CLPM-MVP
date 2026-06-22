"""Shared FastAPI dependencies (placeholder).

``get_current_user`` and friends are implemented in the auth task. They are
declared here so endpoint signatures can reference them without import cycles.
"""

from __future__ import annotations

from typing import Any


async def get_current_user() -> dict[str, Any]:
    """Placeholder dependency returning a stub user.

    TODO(auth-task): replace with real JWT decoding and user lookup.
    """
    return {"id": "anonymous", "username": "anonymous", "roles": []}
