def pytest_configure(config):
    import asyncio
    from app.services import data_planner
    from app.services.data_source import logical_wide_builder
    data_planner.gather_settled=asyncio.gather
    logical_wide_builder.gather_settled=asyncio.gather
