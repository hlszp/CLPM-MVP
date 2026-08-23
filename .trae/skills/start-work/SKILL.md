---
name: "start-work"
description: "双机协作启动检查。每次开始工作时调用，自动读取 AGENTS.md Git 工作流口径、检查分支状态、从 github 同步 main、查看对方机器工作进度。Invoke when user says '开始工作'、'start work'、'开始今天的开发'、'/start-work'、'双机协作检查'、'继续工作'，或会话开始时需要确认工作上下文。"
---

# 双机协作启动检查

每次开始工作时执行此检查清单，确保双机协作安全顺畅。口径以 AGENTS.md「Git 工作流（MVP 口径，2026-08-20 修订，双机分支策略 2026-08-22 起）」为准。

## 执行步骤

### Step 1: 读取双机协作规范

读取 `AGENTS.md` 中的 §Git 工作流（MVP 口径） 章节，确认当前规范要求：

- **远端**：`github` = `https://github.com/hlszp/CLPM-MVP`（主远端，唯一可推送目标）；`origin` = 原 CLPM gitea，pushurl 已锁死 `DISABLE_PUSH_TO_UPSTREAM`，**严禁对 origin 做任何推送**
- **分支策略**：macbook 机在 `macbook` 分支开发、zpdev 机在 `zpdev` 分支开发，各自 `git push -u github <分支>` 备份；**仅在用户显式要求时**才合并回 main（`--no-ff`）；定期把 main 合入各自分支保鲜
- **提交规范**：Conventional Commits，单 commit ≤500 行
- **纪律**：提交/推送/CI 仅在用户显式要求时执行；DB 迁移/种子数据变更尽量集中单机，避免 alembic 多 head 冲突

**判断当前机器身份**：根据本地路径判断：

- 路径包含 `/Users/zhangping/` → macbook 机器，工作分支为 `macbook`
- 路径包含 `/home/zhangping/`（ssh-remote zpdev） → zpdev 机器，工作分支为 `zpdev`

> 如无法判断，向用户确认当前所在机器。

### Step 2: 检查当前分支状态

执行以下命令（并行运行以提高效率）：

```bash
# 1. 当前分支与工作树状态
git status -sb

# 2. 本地所有分支
git branch -vv

# 3. 从 github 拉取远端状态（注意：同步源是 github，不是 origin）
git fetch github --prune 2>&1
git branch -r

# 4. 当前分支与 main 的差异
git log main..HEAD --oneline 2>/dev/null || echo "当前在 main 分支"

# 5. main 最新提交
git log github/main --oneline -10
```

### Step 3: 查看对方机器工作进度

MVP 口径不走 PR 流程（分支直推 github，仅在用户显式要求时合并回 main），因此检查对方分支而非 PR：

```bash
# 对方分支最近工作（当前在 macbook 就看 zpdev，反之亦然）
git log github/zpdev --oneline -10
git log github/macbook --oneline -10

# 对方分支领先/落后 main 情况
git log github/main..github/zpdev --oneline
git log github/main..github/macbook --oneline
```

### Step 4: 同步 main 到工作分支（保鲜）

仅以 `github` 为同步源；**不要** `git pull origin main`（origin 是原 CLPM gitea，拉取会引入错误历史）。

如果当前在 `main` 分支：

```bash
git pull github main
```

如果在工作分支（`macbook` 或 `zpdev`）：

```bash
# 先检查是否有未提交改动
git status -s

# 如有未提交改动，提示用户先处理（提交或 stash）
# 如工作树干净，把 main 合入当前分支保鲜
git merge github/main -m "chore: 合并 main 到工作分支保鲜"
```

**合并回 main**：仅在用户显式要求时执行（`git checkout main && git merge --no-ff <分支>`），日常不主动合并。

**冲突处理**：

- 如有冲突，先尝试自动解决（文档冲突以版本号更高/日期更新为准，设计口径以 `docs/MVP设计/` 为事实来源）
- 如无法自动解决，向用户报告冲突文件清单，等待用户决策

### Step 5: 生成工作状态报告

向用户报告以下信息（简洁表格形式）：

```
## 双机协作状态报告

| 项目 | 状态 |
|---|---|
| 当前机器 | macbook / zpdev |
| 当前分支 | <branch-name> |
| 工作树 | 干净 / 有 N 个未提交改动 |
| 与 github/main 差异 | 领先 X commits / 落后 Y commits |
| 对方分支进度 | <对方分支最近提交标题> |
| 待同步迁移 | 对方是否有未合并的 alembic 迁移（尽量集中单机变更） |

## 建议下一步
- 如落后 main：先合并 github/main 保鲜再开始工作
- 如对方分支有新提交已合入 main：同步后再开工
- 如有未提交改动：建议先提交（Conventional Commits）
```

### Step 6: 验证基础环境（可选）

如用户是第一次在当前会话工作，或代码有变更，运行快速验证：

```bash
# 后端快速检查（10 秒内完成）
cd backend && uv run ruff check . 2>&1 | tail -5

# 前端类型检查
cd frontend && pnpm run check:type 2>&1 | tail -5
```

## 触发条件

本 skill 在以下情况自动调用：

1. 用户说"开始工作"、"start work"、"继续工作"
2. 用户说"检查双机协作状态"、"同步 main"
3. 会话开始时需要确认工作上下文
4. 用户输入 `/start-work`

## 注意事项

- 所有 git 命令通过 RunCommand 工具执行，不直接调用 shell
- **严禁对 origin（原 CLPM gitea）做任何推送**；同步/拉取也优先使用 github 远端
- 提交、推送、CI 仅在用户显式要求时执行，完成后报告结果即可，不主动等待 CI
- 如发现自己分支落后 main 超过 10 个 commits，先合并 main 保鲜再开始工作
- DB 迁移/种子数据变更尽量集中单机：开工前留意对方分支是否有未合并的 alembic 迁移，避免多 head 冲突
- 双机并行期在本机工作分支（`macbook`/`zpdev`）开发；合并回 main 必须用户显式要求
