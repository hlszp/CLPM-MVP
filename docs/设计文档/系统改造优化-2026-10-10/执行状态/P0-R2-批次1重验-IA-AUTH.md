# P0-R2 漂移复核报告·批次1：IA-01 / IA-06 / IA-13 / AUTH-03

- 重验子智能体：独立只读代理；区间 `2c817248..baa3f50d`
- 开工/收工 HEAD：`baa3f50d8d883356064d5774c07122ebb5f360eb`（一致）
- 判定统计：✅仍存在 4 ｜ ❌已修复 0 ｜ ⚠️漂移 0（仅行号微漂，按符号核对一致）

## 逐条重验

- **IA-01 ｜ ✅仍存在 ｜** `backend/app/api/v1/endpoints/performance.py:170` ranking 端点 `limit: int = Query(20, ge=1, le=100)` 约束原样（原 :168，a10acc45 在 import 区 +2 行致微漂）；`frontend/.../cockpit/wb-comps/use-drill.ts:207/272` `limit: 200` 与 `:246/293` `catch { list.rows = [] }` 清空吞错原样；`cockpit/components/modals/list-modal.vue:30/57` 空态默认"暂无数据"原样。a10acc45 对 performance.py 为**纯文件末尾追加** 3 个新端点（window-agg / batch-delete / 单条 delete），未触及 ranking 端点与该问题链。

- **IA-06 ｜ ✅仍存在 ｜** `backend/app/schemas/performance.py:198/438/493` 三处 `fitnessLevel: str | None` 声明仍在（行号无漂移）；`endpoints/performance.py:604-652` `KpiSnapshotListItem(...)` 完整构造体仍无任何 fitnessLevel 赋值，**恒 None**。a10acc45 的"停用回路历史快照可见"改动全在 `services/performance.py` 查询层（include_inactive，:1717/1750/2165 等），对 diff 逐行 grep 确认无任何 fitness 相关行——未顺带填该字段。前端接入位 `workbench360/index.vue:400-404` 注释仍自证"后端 G1：snapshots 端点 fitnessLevel 恒 null"。**辨析登记**：`services/performance.py:848` 的 `"fitnessLevel": snap.fitness_level` 属 ranking 组装（get_ranking）且基线前已存在（914179cb 引入），非本条落点，**不可误判为 IA-06 已修复**——快照列表（KpiSnapshotListItem）组装路径至今未填该字段。

- **IA-13 ｜ ✅仍存在（描述无实质漂移）｜** `frontend/.../views/loop/manage.vue:213` 运维圈选注释（当前控制模式筛选）、`:887` "2026-10-10 运维圈选：全选当前筛选结果（分页拉回全部 loopId 后勾选）"（原 :855，fef31fcb 在 :644-690 区净加约 32 行致后移）、`:808-849` 批量配置区、`:771` "本次将影响 N 个回路"确认文案——圈选/批量功能完整保留。fef31fcb 仅改参评口径（不参评提示文案 + 启用停用回路时新增"恢复参评并启用"弹窗），未触及圈选/批量逻辑。

- **AUTH-03 ｜ ✅仍存在 ｜** `backend/app/api/v1/endpoints/tasks.py:120` `_TASK_CREATOR_ROLES = ("IC_ENGINEER", "PE_ENGINEER", "ADMIN")` 未变；cancel（现 :1352-1355）与 delete（现 :1423-1426）端点均仍 `require_roles(*_TASK_CREATOR_ROLES)`（原 :1348-1351/:1419-1422，00d96192 前部 +3 行微漂）。00d96192 仅改 backfill 链路（skip_existing 补差模式透传），零触及角色白名单。前端 `views/task/list.vue` 本轮零变更——按钮无角色控制现状原样。

## 落点文件 diff 摘要（2c817248..baa3f50d，供登记）

| 文件 | 变更 | 主题 |
|---|---|---|
| `endpoints/performance.py` | +107（a10acc45） | 末尾新增 3 端点：窗口聚合快照、快照批量删除（dryRun 预览+无约束拒绝）、单条删除（后两者 ADMIN/IC 守卫） |
| `schemas/performance.py` | +18（a10acc45） | 新增 `SnapshotBatchDeleteRequest` schema |
| `endpoints/tasks.py` | +7/-1（00d96192） | backfill `skip_existing` 补差模式透传 |
| `views/loop/manage.vue` | +34/-8（fef31fcb） | 参评口径统一：不参评提示文案 + 启用停用回路"恢复参评并启用"弹窗 |
| `views/task/list.vue`、`use-drill.ts`、`list-modal.vue` | 无变更 | — |

（a10acc45 另含 services/performance.py +133（include_inactive + batch_delete_snapshots/delete_snapshot_by_id）与评估 Tab 前端等，均不影响四条判定。）
