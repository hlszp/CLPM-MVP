# CLPM Agent Guidance

本项目是危化企业控制回路性能评估与优化平台 — **CLPM-MVP**（自 CLPM v6.2 派生的精简 + 闭环重建版，"监控-评估-诊断-整定/处置"闭环已重建，详见 `AGENTS.md` 顶部 MVP 覆盖说明）。

**所有 Agent 指引统一维护在 [`AGENTS.md`](AGENTS.md)，本文件仅为入口指针（2026-07-20 起瘦身，消除双写腐坏）。**

## 必读入口

1. [`AGENTS.md`](AGENTS.md) — 项目约定的事实来源：顶部 MVP 覆盖说明（端口 17101/15666、远端 github 唯一可推送、CI 现状）、当前基线、核心架构组件、开发环境运行指南、CI 本地检查、关键注意事项、核心决策（含 2026-07-20「导入走远端、计算全本地」数据架构决策）、Git 工作流、下阶段规则
2. `docs/MVP设计/` — **MVP 设计事实来源**：01~10 设计与实施文档（诊断/处置/整定模块重建方案与实施记录）
3. [`README.md`](README.md) — 项目简介、技术栈、快速开始、数据源与数据链路（§7）
4. `docs/设计文档/00-BASELINE/implementation-contract.md` — 重构后 IA/路由/API/权限/状态机/KPI 事实来源（v2.7，原项目基线，MVP 差异以 docs/MVP设计/ 为准）
5. `docs/设计文档/01-PRD/PRD.md` — 产品需求事实来源（v6.2，原项目基线，MVP 差异以 docs/MVP设计/ 为准）

## 拆分文档（AGENTS.md 2026-07-21 瘦身拆出，按需阅读）

- `docs/过程文档/ops-runbook.md` — 运维手册（网络模式切换验证、worker 挂死处置、回填性能、断点续传细节）
- `docs/过程文档/v6-delivery-history.md` — v6 交付历史追溯
- `docs/过程文档/stale-docs.md` — 已失效文档清单

## 速查（以 AGENTS.md 对应章节为准）

- 启动服务 / 测试与验证 / CI 提交前本地检查 → AGENTS.md「开发环境运行指南」
- 网络模式切换验证 → ops-runbook.md「网络模式切换」
- Celery worker 重启、端口、默认账号 → AGENTS.md「关键注意事项」
- 远端命名 / 提交规范 / PR 执行方式 → AGENTS.md「Git 工作流」
