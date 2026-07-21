# 回路管理模块完善与优化计划

## 背景

对回路管理模块 5 个子模块（链路配置 / 测点配置 / 回路配置 / 回路监控 / 数据管理）做了并行只读审查（前端 `frontend/apps/web-antd/src/views/` + 后端 `backend/app/api/v1/endpoints/` 与 `services/`，对照 implementation-contract v2.0 与 PRD v6.0）。共发现 **3 个 P0 崩溃级、~20 个 P1 功能/契约级、~40 个 P2 健壮性/性能/UX 级**问题。本计划按优先级分四个阶段，关键 P0 已逐一核实属实。

## Phase 1：P0 崩溃修复（已核实，先行）

| # | 问题 | 证据 | 修复方向 |
|---|---|---|---|
| 1.1 | **批量配置更新必崩 500**：引用已改名字段 `loop.level`（现名 `importance_level`），且白名单缺 `importance_level`/`include_in_evaluation` | `services/loop_batch.py:126,137,144`、`endpoints/loops.py:193-196` | 字段名统一 + 扩充白名单 + 补集成测试 |
| 1.2 | **批量删除必崩 500**：service 返回 int，endpoint 读 `del_result["deleted"]`；审计把逗号拼接串写入 UUID 列 | `loop_batch.py:263`（return int）vs `loops.py:177-183`；`loop_batch.py:251` | 返回 `{"deleted","skipped"}`，审计逐条写 |
| 1.3 | **tag-mapping 页整体失效**：`getAasTagsApi({pageSize:10000})` 触发 `le=100` 校验 422，下拉永远为空；页面无路由注册（孤儿页）；自动关联规则用过时 KP/TI/TD 枚举 | `views/loop/tag-mapping.vue:146`、`endpoints/aas.py:128`、路由 `router/routes/modules/loop.ts` | **决策：下线该页**（功能已被 manage.vue 内嵌抽屉覆盖且实现更正确），删死代码 |
| 1.4 | **Excel 导入/新建回路对缺省值必炸**：`importance_level=None`、`include_in_evaluation=None` 直传 NOT NULL 列 → IntegrityError | `services/loop.py:695,1709-1726` vs `models/loop.py:68,75` | 服务层默认值兜底（等级=2、参评=True） |
| 1.5 | **时区 8h 偏移风险（需先核实再修）**：`_parse_dt`/`_parse_ts_str` 用 `replace(tzinfo=None)` 丢弃时区而非 `astimezone` 转换；实时链路 ts 是 collectTime 原样字符串，两条写入路径可能差 8h | `data_import.py:857-873`、`realtime_subscriber.py` _build_row | 实测远端 API 时区语义 → 统一"naive=本地时间"约定并显式转换 |

## Phase 2：P1 数据正确性与契约对齐

### 2.1 断点续传加固（昨日新功能，审查发现 4 个缺口）
- **checkpoint 条件推进**：`realtime_subscriber.py:453` 在部分/全部补数失败时仍推进 `_last_data_at`，缺口永久丢失（昨晚已实际发生 2 回路失败）→ 仅 `failed==0` 才推进，失败回路留待重试
- **补数失败无重试**：仅在重连时触发检测，连接保持在线时失败即搁置 → 失败后启动延迟重试定时器（如 5min）
- **补数不可观测**：不进 Redis 任务索引、无告警 → 登记任务记录（标记 auto-backfill 来源）+ 接 alerting
- **绕过熔断/限流**：`_fetch_remote_history` 不经 `RemoteApiProvider` 熔断器，补数(API进程)+导入(worker)+provider 三路并发可达 8，夜间抖动期叠加压远端 → 统一共享熔断器与全局限流

### 2.2 回路监控契约对齐（缺陷最密集，合并为一次改动）
- `status` 字段语义撞车：后端放 KPI 状态（SUCCESS/PARTIAL/INCONCLUSIVE），前端类型是 LoopStatus（READY/PARTIAL/INACTIVE），PARTIAL 撞名 → 拆 `loopStatus`+`kpiStatus`（`monitor.py:518` vs `api/loop.ts:359`）
- `currentValues.unit` 前端用了后端没给（`monitor.vue:1368` vs `monitor.py:406-413`）
- WS 推送 MODE 映射前端写死 0/1/2，MODE=3/4 显示 Unknown 覆盖后端正确值（`monitor.vue:561-583`）
- 后端返回合同外状态 `"GOOD"` 前端无映射原样显示英文（`monitor.py:727`）
- 监控列表不过滤 `is_active` 与统计卡片口径不一致（`monitor.py:323` vs `loop.py:162`）
- `last_7_days` 前端有后端无，非法窗口静默回退 24h（`api/loop.ts:38` vs `monitor.py:667`）

### 2.3 回路配置契约修复
- **PUT 更新静默丢弃 unitId**（已核实：LoopUpdate schema 无此字段，pydantic 静默丢弃，确认弹窗却展示该 diff）→ schema 补字段 + `CamelModel extra="forbid"` 机制性防漂移（`schemas/loop.py:99`、`manage.vue:649-659,1735`）
- MODE→控制方式硬编码 `{0:Manual,1:Auto,2/3:Cascade}`，无视已配置的 dcs_model 映射（`loop.py:529-534`）→ 走 dcs_model→dcs_mode_mapping→默认回退链
- 删除弹窗承诺"级联解绑"但后端有 Tag 即拒绝删除，回路永远删不掉（`manage.vue:2891` vs `loop.py:1166-1176`）→ 对齐行为与文案
- PRD 要求 PID 参数只读展示，后端已返回 `runtimeParams.pidP/I/D` 前端未展示 → 抽屉/详情补只读区
- 前后端权限不一致：后端允许 PE_ENGINEER，前端按钮仅 ADMIN/IC_ENGINEER（`loops.py:124` vs `manage.vue:1888`）
- signalrSubscriberRunning 返回 settings 镜像而非订阅器真实状态，保存后"需重启"提示立即消失（`datasource_config.py:170,207`）

### 2.4 测点配置契约修复
- **枚举全面不一致**：前端 KP/TI/TD、POSITION vs 后端 PID_P/I/D、SPEED → 筛选查空、编辑提交 500（`api/tag.ts:12-22` vs `models/tag.py:52-63`）
- 编辑"参数类型"被静默丢弃（TagUpdate schema 无字段）；列表"所属单元"/详情"关联回路"永远显示"—"（嵌套 loop 对象 vs 前端读扁平字段）
- **is_linked 误清/污染**：解除关联未查其他回路引用（`tag_mapping.py:216-225`）；导入"是否启用"列直接覆盖 is_linked，可绕过删除保护并污染实时订阅集合（`services/tag.py:766`）→ is_linked 只能由映射关系派生
- PRD §4.2.6 只读原则 vs 现有编辑/导入能力矛盾；AAS 同步每次回冲手工编辑的描述 → 产品口径二选一

### 2.5 链路配置安全与一致性
- 历史 API Token 明文落审计日志 + GET 明文回传（`datasource_config.py:193-235,150-154`）→ 打码 + "不填即不变"
- "测试连接"隐式保存配置无提示（`aas.vue:189-221`）→ 显式提示或临时参数测试
- 已保存 URL/Token 无法清空（`|| undefined` 丢弃空串）；Tailscale 切换失败无回滚（DB 与路由发散）；网络模式切换无二次确认（瞬断实时链路的高危操作）
- `_cast_value` 脏数据可致 GET /config 500；配置读写 27 次串行查询可合并

## Phase 3：P2 韧性、性能与 UX

### 3.1 韧性（针对远端不稳定/夜间抖动，本环境最痛）
- **数据停滞看门狗**：WS 连接活着但上游停推时 `_last_data_at` 无限期停滞、无任何检测 → N 分钟无消息主动重连/触发补数（夜间"Hub 假死"盲区）
- **TDengine 落库 checkpoint 与接收 checkpoint 分离**：flush 重试 3 次失败即丢缓冲但 checkpoint 照常推进（`realtime_subscriber.py:596`）
- WS 客户端参数放宽：`ping_interval=20/ping_timeout=20/open_timeout=10` 默认对过载边缘服务器太激进 → 30/60/15（昨日诊断结论）
- `data_link_monitor.py` 整模块死代码：TDengine 新鲜度检查未接 beat/告警 → 接线
- 导入任务生命周期治理：chunk 级取消检查（现在取消延迟可达数分钟）；Celery 层 finally 兜底终态 + RUNNING 超时清扫（worker 被杀任务永久卡"执行中"——正是昨天你遇到的现象成因之一）；Redis 任务 TTL + 索引修剪
- 断点续传多副本分布式锁（当前单进程守卫，多副本部署会重复补数）

### 3.2 性能
- KPI 快照"最新一条"查询拉回全部历史快照内存去重 → 真 `DISTINCT ON`（`monitor.py:369-379`，注释自称用了但没用）
- `_get_descendant_node_ids` 递归逐层 SQL（loop.py 与 monitor.py 两处重复实现）→ CTE 一次查询
- 树节点计数循环分页串行拉全量 → `GROUP BY unit_id` 聚合接口
- 导入/导出全量读内存 + openpyxl 同步阻塞事件循环，大文件必撞 30s 超时 → 任务化 + 大小/行数上限
- `/tags/match-loop` 循环内 7 次串行查询 → 一次 IN 查询

### 3.3 UX 打包
- 监控页：错误态与空态不分、WS 在线时倒计时冻结误导、统计卡片不随 WS 联动、pageSize 兜底错误（`|| 100` vs 默认 20）
- 数据管理页：默认全选+默认 overwrite 误操作半径大；取消无确认；错误提示丢后端 ERR_* 明细；回路列表 pageSize=100 硬编码超量静默丢失
- 回路配置页：导入失败明细不可见；changeRemark 死 UI 不入审计；自控率 unknown 不计分母虚高
- 通用：Tag 质量码 REST 与 WS 语义不一致（2 一边 GOOD 一边 UNCERTAIN）

## Phase 4：文档与产品口径决策项（需人决策，非纯代码）

1. **AAS 同步 UI 去留**：后端 6 个端点+任务齐全但前端零调用（`api/aas.ts` 6 个死函数）；补「链路配置」页 AAS Tab 还是删代码改 PRD
2. **测点只读原则**：PRD §4.2.6 说不可编辑，实现有完整编辑/导入——收回能力还是改 PRD
3. **数据管理权限**：契约 §5 规定 PE_ENGINEER 仅查看，实现允许其发起导入/删除（`loop_data.py:35`）
4. **断点续传登记入文档**：GAP_BACKFILL_* 机制与 24h 截断运维流程未进 implementation-contract/PRD（昨日新功能）
5. 版本口径：PRD 引"实现契约 v2.1"而基线文档自称 v2.0
6. 卡片视图（PRD §4.2.5）与监控导出功能：实现 or 文档降级

## 建议执行顺序与验证

- **Phase 1**（约 1 天）：P0 五项，每项补回归测试；1.5 时区先实测确认再改
- **Phase 2.1**（约半天）：断点续传加固——昨晚真实环境已暴露，防数据静默丢失最紧迫
- **Phase 2.2-2.5**（约 2 天）：契约对齐批量修，每子模块一组 commit，补接口级测试
- **Phase 3**（约 2-3 天）：韧性 > 性能 > UX
- **Phase 4**：穿插决策，决策后随对应阶段落地
- 每阶段验收：`uv run pytest -q` 全绿 + `ruff check/format` + 前端 `pnpm run check:type`；Phase 1/2 核心路径建议补 E2E（e2e/）

## 关键已核实结论（本次审查抽查）

- `loop_batch.py:126,137,144` 确实引用不存在的 `loop.level`（模型字段为 `importance_level`）——批量配置崩溃属实
- `batch_delete_loops` 确实 `return len(loops)`（int）而 endpoint 读 `del_result["deleted"]`——批量删除崩溃属实
- `LoopUpdate` schema 确实无 `unitId`——静默丢弃属实
- `endpoints/aas.py:128` 确实 `pageSize le=100`——tag-mapping 页 422 属实
