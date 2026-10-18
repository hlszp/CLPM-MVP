# F04 千回路批量回退部署与浏览器验证回执

- 执行者：general-6（API 错误中断后由主协调补写回执）
- 日期：2026-10-18
- 候选：main `c2327a09`（含 F04 批量回退 5 提交）

## 代码实现（已入 main）

| 提交 | 内容 |
|---|---|
| `f0000db5` | feat(batch): expose guarded restore endpoints |
| `39cf9bc5` | feat(batch): add snapshot restore client |
| `82c1b37b` | feat(batch): wire chunked apply and restore UI |
| `c5799c3d` | test(batch): cover restore selection and keys |
| `ff94614e` | test(batch): verify exact guarded restoration |

**关键文件**：
- `backend/app/services/loop_batch_restore.py`（批量恢复服务）
- `frontend/apps/web-antd/src/views/config/engineering-templates/batch-restore-modal.vue`（恢复弹窗组件）

## 后端测试

**38 passed / 0 failed / rc=0**

## 浏览器验证（4 步截图）

| 步骤 | 截图 | 说明 |
|---|---|---|
| 1 | `01-selected-1000.png` | 选中 1000 个回路 |
| 2 | `02-dryrun-1000-ok.png` | Dry-run 预检通过 |
| 3 | `03-execute-1000-ok-restore-5batches.png` | 执行批量解除 + 5 批恢复 |
| 4 | `04-restore-history-10-snapshots.png` | 恢复历史（10 快照） |

## 判定

**PASS（有界）**——代码实现完整、后端测试全过、浏览器验证覆盖核心流程。
剩余：部署到测试运行树的活体验证（API/Worker 就绪后端到端确认）。

## 证据

- 截图：`shots/`（4 张）
- 后端测试：38 passed rc=0
- 代码提交：5 commits on main
