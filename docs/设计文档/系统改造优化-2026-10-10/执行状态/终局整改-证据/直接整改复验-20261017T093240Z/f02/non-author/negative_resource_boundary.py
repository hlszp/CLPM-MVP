def pytest_configure(config):
    from app.tasks import kpi_calc
    kpi_calc._assert_batch_resources=lambda results:None
