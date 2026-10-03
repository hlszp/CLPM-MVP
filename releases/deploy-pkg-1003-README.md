# 部署包 deploy-pkg-1003（2026-10-03）

- 对应生产版本：**1003 回路工作台（新版）上线 + 旧工作台下线**（P1-P4 四阶段 + 终验优化 + 旧页删除 + 1209 回路渐进加载提速）
- 核心变更：新增「回路工作台（新版）」单页闭环（`/loop/workbench360`，监控菜单下）；**旧回路工作台已删除**（旧路径兼容 redirect 到新版）；回路清单渐进加载（首页即渲染+余页 4 并发，取消 500 条截断）
- 产物版本：`clpm-frontend:subpath-hotfix-1003`（ENV APP_VERSION=v7.2.0-workbench360），底座 subpath-hotfix-0938
- 内容：`frontend-dist-1003/`（前端完整构建，--mode clpm 子路径，含 gzip 预压缩产物 305 件）+ `Dockerfile.hotfix-1003-frontend` + 本清单
- 后端无变更（P1-P4 与终验优化全部零 backend 改动）：**仅需重建 clpm-frontend 单容器**
- 部署手册：releases/DEPLOY-GUIDE.md；生产通道与守则见 ops-runbook / clpm-prod-ops skill
- 上一版：deploy-pkg-0938（0938 双镜像）；历史热修复镜像定义归档于 releases/archive/
