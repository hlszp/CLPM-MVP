"""Dedicated-process negative control: restore pre-F02 minority failure policy."""


def pytest_runtest_setup(item):
    from app.tasks import kpi_calc

    kpi_calc._assert_batch_resources = lambda results: None
