# ============================================================
# Mock Data Server Dockerfile（仅开发环境）
# 模拟远端数据源服务：HistoryDataAppService API + SignalR Hub
# 正式项目可整体移除，不影响主应用
# ============================================================

FROM python:3.12-slim

WORKDIR /app

# 安装依赖
COPY mock_data_server/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# 复制应用代码
COPY mock_data_server/ /app/mock_data_server/

# 设置 PYTHONPATH 以支持 `from mock_data_server.xxx import yyy` 导入
ENV PYTHONPATH=/app

EXPOSE 8100

CMD ["python", "-m", "uvicorn", "mock_data_server.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8100"]
