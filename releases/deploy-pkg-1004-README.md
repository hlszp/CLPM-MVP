# 部署包 deploy-pkg-1004（2026-10-04）

- 对应生产版本：**1004 六批合流 + 工作台规整**（main `20b1ac6a..97c9e93f`，10 个提交：回路工作台升一级菜单 → 驾驶舱五项 → 手动评估修复 → 评估整合 / 评估总览改版 / 预警事件页 / 监视页 P1 / 整定审计 / 四工作台规整 D1-D4 / hex 基线登记）
- **双镜像（前后端都有变更）**：
  - `Dockerfile.hotfix-1004-frontend`：底座 `clpm-frontend:subpath-hotfix-1003`，替换静态产物，`APP_VERSION=v7.3.0-1004-consolidation`
  - `Dockerfile.hotfix-1004-backend`：底座 `clpm-backend:hotfix-0938`，整目录覆盖 `backend/app` + `backend/alembic`（pyproject 无依赖变更）
- 内容：`frontend-dist-1004/`（前端完整构建，--mode clpm 子路径 + gzip 预压缩，694 件）+ 双 Dockerfile + 本清单
- ⚠️ **本批含 2 个数据库迁移，部署顺序严格**：
  1. `docker exec clpm-backend alembic upgrade head`（依次落 `17fdbfa579af` 快照 source 字段、`daa4f06b6e26` 三性 dimension 列；alembic check 已验证零漂移）
  2. 重建后端容器（Worker/Beat 由 lifespan 自动拉起，严禁手工再启动）
  3. 重建前端容器
- 用户可感变化（验收要点）：
  - 全局「工作台」更名**运维工作台**；诊断菜单变三页（概览/记录/任务）；整定菜单变**整定总览**/记录/验证
  - 诊断/整定单回路操作统一入口 = 回路工作台剖面（`?section=` 直达）；旧书签路径全部 redirect
  - 操作角色放开：EXPERT 可发起诊断、PE 可走整定流程（SPONSOR 仍只读）
  - 评估模块三视图合并「回路评估」；性能总览三性分离；预警事件双 Tab + 批量确认
- 部署手册：releases/DEPLOY-GUIDE.md；生产通道与守则见 ops-runbook / clpm-prod-ops skill
- 上一版：deploy-pkg-1003（仅前端）；历史热修镜像定义归档于 releases/archive/
