def pytest_configure(config):
    from pathlib import Path
    from app.tasks import kpi_calc
    source=Path('/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/直接整改复验-20261017T093240Z/f02/non-author/old-candidate-calculate-loop-kpi.py').read_text()
    exec(compile(source,"old-candidate-calculate-loop-kpi.py","exec"),kpi_calc.__dict__)
