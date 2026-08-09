# 监控—回路工作台闭环整改：智能体实施交接提示词

> 状态：待执行
> 工作区：`/Users/zhangping/DEV/CLPM`
> 用途：将本文件交给实施智能体，按既定计划完成代码、测试和文档闭环。

## 可直接使用的提示词

你是 CLPM 项目的主实施智能体。请在 `/Users/zhangping/DEV/CLPM` 持续完成“监控—回路工作台闭环整改”，不要停留在分析、建议或原型。

### 1. 必读顺序

修改前完整阅读，并按顺序理解：

1. `/Users/zhangping/DEV/CLPM/AGENTS.md`
2. `/Users/zhangping/DEV/CLPM/README.md`
3. `/Users/zhangping/DEV/CLPM/docs/过程文档/stale-docs.md`
4. `/Users/zhangping/DEV/CLPM/docs/设计文档/00-BASELINE/implementation-contract.md`
5. `/Users/zhangping/DEV/CLPM/docs/设计文档/06-UIUX/ui-ux-design-guidelines.md`
6. `/Users/zhangping/DEV/CLPM/docs/设计文档/06-UIUX/ui-ux-rectification-checklist-2026-08-08.md`
7. `/Users/zhangping/DEV/CLPM/docs/设计文档/06-UIUX/p2-exit-report-2026-08-09.md`
8. `/Users/zhangping/DEV/CLPM/docs/设计文档/06-UIUX/monitor-workbench-rectification-plan-2026-08-09.md`
9. `/Users/zhangping/DEV/CLPM/docs/设计文档/06-UIUX/monitor-workbench-rectification-checklist-2026-08-09.md`
10. 当前代码、测试和 Git 状态。

事实来源：实现契约定义当前基线，UI/UX 规范定义交互视觉约束，本轮整改计划定义目标增量，本轮整改清单是唯一进度事实来源。旧文档引用前必须检查 `stale-docs.md`。

### 2. 整改目标

1. 预警不设一级模块；规则保留在 `/config/alert-rules`。
2. `/monitor/attention` 承载当前行动项；`/monitor/alerts` 保留为预警历史、审计与导出入口。
3. 监控和回路工作台共享筛选、回路、时间窗、实时状态和深链接上下文。
4. 工作台形成“监控→评估→诊断→整定→人工实施/MOC→效果验证→持续监控”闭环。
5. 保留单回路工作台和批量表格两种模式，不丢失旧监控页的筛选、导出、列设置和保存视图能力。
6. 不增加 DCS PID 参数下写能力。

### 3. 固定边界

- 关注队列聚合 ALERT、DEGRADATION、DATA_QUALITY、TRACKER、VERIFICATION 五类来源。
- 优先聚合现有表；未完成 1000 回路/10000 开放项压测前不得新增业务表。
- 工作台首屏使用 summary 聚合接口，大图、波形、FFT、趋势和仿真按需加载。
- URL 是监控上下文真相源；深链接不得回退到其他回路。
- PE 工作台只读；EXPERT 仅允许整定相关动作且不能进入批量模式；Sponsor 不得进入单回路工作台；Tracker 写入仅 ADMIN/IC；预警规则仅 ADMIN。
- 前后端都必须执行权限校验，隐藏按钮不能代替后端授权。

### 4. 执行顺序

严格执行整改清单：

1. G0：签认当前 IA 基线、保存性能/安全基线、建立实施分支。
2. Phase 0：修复虚拟列表、深链接、请求竞态和首屏加载；未通过不得扩展页面。
3. Phase 1：共享上下文、实时 WS、断连轮询和列表分页。
4. Phase 2：关注队列 API、页面、铃铛联动和权限。
5. Phase 3：summary、生命周期、nextAction、实施与验证时间线。
6. Phase 4：批量表格嵌入和旧路由兼容。
7. Phase 5：全量门禁、性能、安全、可访问性和文档回写。

每完成一项，立即在整改清单中记录状态、变更文件、Commit、测试命令和结果。没有验证证据不得勾选 `[x]`；未达指标不得用“后续优化”关闭。

### 5. 工程规则

- 开工先检查 `git status`、`git diff`，保护用户已有修改。
- 不得删除或提交 `.trae-html-share-packages/`、`clpm-ui-refactor-assessment/`、`docs/设计文档/prototype/ui-refactor-prototypes/`。
- 禁止 `git reset --hard`、`git checkout --`、`git clean`、`git add -A` 和 force push。
- 使用 `rg` 搜索、`apply_patch` 修改；只精确暂存本轮文件。
- 可按清单拆分本地提交，但不要 push、merge 或创建 PR。
- 不得手工启动第二个 Celery Worker/Beat；修改 Celery 任务后需重启后端生命周期再验证。
- 计算类历史数据只能读取本地 TDengine。
- 不重写评估、诊断、整定算法，不新增自动实施能力。
- 前端使用现有设计 Token、Lucide 和中文业务文案；不得新增硬编码 hex、Emoji 图标或裸英文业务枚举。

### 6. 必须达到的验收指标

- WS 到 UI 更新不超过 2 秒；断连状态不超过 5 秒；首次降级刷新不超过 30 秒。
- 连续切换 20 次回路，旧响应覆盖 0 次。
- 超过 100 条时，深链接仍精确打开目标回路。
- 除回路列表外，工作台首屏最多 1 个 summary 请求。
- attention 在 1000 回路/10000 开放项下 p95 不超过 500ms；summary p95 不超过 400ms。
- DOM 同时渲染回路条目不超过 100。
- ADMIN、IC、PE、EXPERT、SPONSOR 权限 E2E 全通过，无新增 403 toast。
- OpenAPI 检查确认没有新增 DCS PID 写入端点。

### 7. 验证与收口

至少执行：

```bash
cd /Users/zhangping/DEV/CLPM/backend
uv run ruff check .
uv run ruff format --check .
uv run alembic check
uv run pytest -q

cd /Users/zhangping/DEV/CLPM/frontend
pnpm run format
pnpm run check:type
pnpm run test:unit

cd /Users/zhangping/DEV/CLPM/e2e
pnpm exec playwright test
```

全部完成后，按计划回写实现契约、UI/UX 规范、README、DESIGN、AGENTS、路由/OpenAPI 文档和两份整改清单，并创建出口报告。

除非遇到会改变产品决策、权限、安全边界或数据库结构的真实阻塞，否则自主推进。最终汇报必须列出完成项、文件、提交、测试、性能数据和遗留；没有证据不得宣称完成。
