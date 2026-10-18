"""In-process negative control: revert only both call sites to ordinary gather.

Loaded explicitly in a dedicated offline pytest process; never edits source files.
"""
import asyncio


def pytest_runtest_setup(item):
    from app.services import data_planner
    from app.services.data_source import logical_wide_builder

    async def abandoned_siblings(*operations):
        return await asyncio.gather(*operations)

    data_planner.gather_settled = abandoned_siblings
    logical_wide_builder.gather_settled = abandoned_siblings
