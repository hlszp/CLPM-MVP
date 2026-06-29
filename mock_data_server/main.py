"""模拟远端数据服务 — FastAPI 应用入口.

提供两个核心接口：
1. ``POST /api/services/v1/HistoryData/Get`` — 历史数据查询（查 TDengine）
2. ``WS /signalr/realValueForClpmHub`` — 实时数据推送（模拟 SignalR Hub）

运行方式::

    cd mock_data_server && python -m uvicorn main:app --host 0.0.0.0 --port 8100 --reload

或::

    cd mock_data_server && python main.py
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from mock_data_server.api.history import router as history_router
from mock_data_server.api.realtime_hub import router as realtime_router, start_broadcast_task
from mock_data_server.config import config
from mock_data_server.services.tdengine_query import close_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """应用生命周期管理."""
    logger.info("模拟远端数据服务启动 (port=%d)", config.PORT)
    logger.info(
        "TDengine: %s@%s:%d, db=%s",
        config.TDENGINE_USER,
        config.TDENGINE_HOST,
        config.TDENGINE_PORT,
        config.TDENGINE_DB,
    )
    await start_broadcast_task()
    yield
    logger.info("模拟远端数据服务关闭")
    await close_client()


def create_app() -> FastAPI:
    """创建 FastAPI 应用."""
    app = FastAPI(
        title="CLPM Mock Data Server",
        description="模拟远端数据源服务（HistoryDataAppService + SignalR Hub）",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(history_router)
    app.include_router(realtime_router)

    @app.get("/health", tags=["health"])
    async def health() -> dict:
        """健康检查端点."""
        return {"status": "ok", "service": "mock_data_server"}

    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=config.PORT,
        reload=True,
    )
