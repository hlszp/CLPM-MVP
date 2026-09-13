"""运维端点鉴权边界（整改 G38）。

背景：/health/db-connections 挂在**根路径**且此前无任何鉴权依赖，而
deploy/nginx.conf 的 `location /health` 是**前缀匹配**——外部可直接读取
PG 连接池明细（total/max/byApp/utilization）与 503 分支的异常原文
（asyncpg 报错常含主机名/库名）。

本文件守护鉴权边界：
1. 运维类端点（db-connections）必须要求认证；
2. liveness（/health）必须保持公开——容器探针依赖它，加鉴权会导致探针失败。
"""

from __future__ import annotations


class TestHealthAuthBoundary:
    """运维端点需鉴权，liveness 保持公开。"""

    def test_liveness_stays_public(self, client) -> None:
        """GET /health 无 token 也必须 200（容器探针依赖）。"""
        resp = client.get("/health")
        assert resp.status_code == 200, resp.text

    def test_db_connections_requires_auth(self, client) -> None:
        """GET /health/db-connections 无 token → 401，不得返回连接池明细。"""
        resp = client.get("/health/db-connections")
        assert resp.status_code == 401, (
            f"运维端点未要求认证（HTTP {resp.status_code}）：外部可读 PG 连接池明细"
        )
        assert "byApp" not in resp.text
