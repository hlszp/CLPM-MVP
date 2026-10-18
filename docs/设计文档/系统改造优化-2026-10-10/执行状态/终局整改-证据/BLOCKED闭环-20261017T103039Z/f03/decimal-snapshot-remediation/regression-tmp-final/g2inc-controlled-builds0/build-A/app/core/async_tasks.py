"""Scoped async fan-out that never leaves sibling operations behind on failure."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import cast


async def gather_settled[T](*operations: Awaitable[T]) -> list[T]:
    """Preserve result order and the first observed error after siblings settle.

    ``gather`` without ``return_exceptions`` propagates a first error while other
    operations keep running. Retrying then overlaps those abandoned reads.
    On caller cancellation, gather cancels and settles its children before raising;
    a native call already executing in a thread still cannot be forcibly stopped.
    """
    first_error: BaseException | None = None

    async def observed(operation: Awaitable[T]) -> T:
        nonlocal first_error
        try:
            return await operation
        except BaseException as exc:
            if first_error is None:
                first_error = exc
            raise

    results = await asyncio.gather(
        *(observed(operation) for operation in operations), return_exceptions=True
    )
    if first_error is not None:
        raise first_error
    return cast("list[T]", results)
