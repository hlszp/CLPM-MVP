---
name: "start-work"
description: "双机协作启动检查。每次开始工作时调用，自动读取 AGENTS.md 双机协作规范、检查分支状态、同步 main、查看对方工作进度。Invoke when user says '开始工作'、'start work'、'开始今天的开发'、'/start-work'、'双机协作检查'、'继续工作'，或会话开始时需要确认工作上下文。"
---

# 双机协作启动检查

每次开始工作时执行此检查清单，确保双机协作安全顺畅。

## 执行步骤

### Step 1: 读取双机协作规范

读取 `AGENTS.md` 中的 §双机协作开发规范 章节，确认当前规范要求：
- 分支命名规范（mb 机器用 `mb/*`，zp 机器用 `zp/*`）
- 提交规范（Conventional Commits）
- PR 合并流程
- 每日同步流程
- 安全规则红线

**判断当前机器身份**：根据 git 配置的 user.email 或本地路径判断：
- 路径包含 `/Users/zhangping/` → zp 机器，使用 `zp/*` 分支前缀
- 其他情况 → mb 机器，使用 `mb/*` 分支前缀

> 如无法判断，向用户确认当前所在机器。

### Step 2: 检查当前分支状态

执行以下命令（并行运行以提高效率）：

```bash
# 1. 当前分支与工作树状态
git status -sb

# 2. 本地所有分支
git branch -vv

# 3. 远程所有分支（拉取最新）
git fetch --all --prune 2>&1
git branch -r

# 4. 当前分支与 main 的差异
git log main..HEAD --oneline 2>/dev/null || echo "当前在 main 分支"

# 5. main 最新提交（查看对方工作）
git log origin/main --oneline -10
```

### Step 3: 检查 PR 与远程工作状态

```bash
# 1. 查看所有开放 PR
gh pr list --state open

# 2. 查看最近合并的 PR（对方工作）
gh pr list --state merged --limit 5

# 3. 查看自己的分支是否有待合并的 PR
gh pr list --state open --author @me
```

### Step 4: 同步 main 到工作分支（如不在 main 上）

如果当前在 `main` 分支：
```bash
git pull origin main
```

如果当前在临时工作分支（`mb/*` 或 `zp/*`）：
```bash
# 先检查是否有未提交改动
git status -s

# 如有未提交改动，提示用户处理
# 如工作树干净，同步 main
git checkout main && git pull origin main && git checkout - && git merge main -m "chore: 同步 main 到工作分支"
```

**冲突处理**：
- 如有冲突，先尝试自动解决（文档冲突以版本号更高为准）
- 如无法自动解决，向用户报告冲突文件清单，等待用户决策

### Step 5: 生成工作状态报告

向用户报告以下信息（简洁表格形式）：

```
## 双机协作状态报告

| 项目 | 状态 |
|---|---|
| 当前机器 | mb / zp |
| 当前分支 | <branch-name> |
| 工作树 | 干净 / 有 N 个未提交改动 |
| 与 main 差异 | 领先 X commits / 落后 Y commits |
| 开放 PR | N 个（列出标题） |
| 对方最近工作 | <最近合并 PR 标题> |

## 建议下一步
- 如在 main 分支：建议创建 `mb/feat-xxx` 或 `zp/feat-xxx` 分支
- 如在工作分支：建议继续当前任务或创建 PR
- 如有未合并的对方 PR：建议 review 或等待
- 如工作树有改动：建议提交或 stash
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
- 如发现 `origin/main` 有新提交（对方机器合并），必须先同步再开始工作
- 如发现自己分支落后 main 超过 10 个 commits，强烈建议从 main 重新创建分支
- 如发现对方有开放的 PR，主动提醒用户 review
- 禁止在 main 分支上直接开发或提交（违反双机协作规范 §7）
