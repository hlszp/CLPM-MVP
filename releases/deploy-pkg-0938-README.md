# 部署包 deploy-pkg-0938（2026-10-02 整理）

- 对应生产版本：**0938 双镜像**（commit 67fcb427：L1 诊断放开 + 仅可诊断过滤 + 未见异常卡 + 概览树展开到单元/侧栏可折叠 + 删健康面板）
- 内容：`frontend-dist-0938/`（前端完整构建，含 gzip 预压缩产物）+ `Dockerfile.hotfix-0938-backend/frontend`（构建配方）+ 本清单
- 后端无独立构建物：随源码与 deploy/docker compose 部署（口令/初始化见 docs/过程文档/ops-runbook.md §生产部署）
- 部署手册：releases/DEPLOY-GUIDE.md、客户现场部署手册.md
- 历史热修复镜像定义（0928b~0937）归档于 releases/archive/，仅供追溯，勿再用于构建
