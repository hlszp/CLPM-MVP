# P0 产物非作者复核报告·B（冻结文档与 manifest）

- 复核者：独立子智能体（未参与 P0 产物编写）
- 开工/收工 HEAD：`2c817248fc1d55e85dfd413ecfd9b4f01b9efbad`（一致）；全程只读，未运行测试/服务/DB
- 日期：2026-10-10
- 复核对象：P0-02 预备/冻结 v1、P0-03 盘点/manifest v1、整合文档 §4/§5；方法=全部声明逐条开代码独立核对

## 逐对象结论

| 对象 | 判定 |
|---|---|
| 1. P0-02-预备-GBT与数值触点盘点.md | **PASS** |
| 2. P0-02-数值语义冻结-v1.md | **PASS**（附 1 条索引计数微勘误，已修复：node 表非唯一索引 4→5） |
| 3. P0-03-容量与测试现状盘点.md | **PASS** |
| 4. P0-03-场景manifest与测试命令表-v1.md | **PASS** |
| 5. 整合文档 §4/§5 | **REWORK**（轻度 3 项，均已由主协调修复） |

## 关键验证事实（抽验 40+ 处全部属实）

1. **三套定级体系**：performance.py:1149-1170 五级 90/80/70/60（None→INCONCLUSIVE）✓；workbench_precalc.py:103-115 六档+展示桶 :73-78 ✓；grading_config.py:67 默认 90/80/60/40（优秀/良好/合格/警告/不合格），**:64 注释确称"对齐 GB/T 44693.2-2024 §6.3"而分界与表 D.1 不符——"错称对齐"断言成立** ✓。
2. **kpi_node_snapshot_hourly 无唯一约束**：node_kpi.py:70-81 仅 2 CheckConstraint + 5 个非唯一 Index（node_id/ts_start/status/node_ts/ts_status）✓（微勘误：冻结文档原记 4 个）。
3. **兼容投影三表约束**：uq_kpi_snapshot_hourly_loop_ts（metric.py:160）、idx_loop_confidence_latest_loop_id unique（:305）、uq_kpi_custom_task_loop（:253）、日/月表（:84/:148）——与冻结 §2.3 逐项一致 ✓。
4. **算子登记表抽 5 行**：gate（32/0.3/三条件 :61-69）、oscillation（0.3/5/2.0/6.0 默认、rFFT peak_idx×fs/n :85、Hann :74、入口 ≥16 :252、_error 落库 :118-124）、sensor（frozen 300/1e-4/0.2、noise 3.0/0.5、drift 2.0/5、置信 0.85、observed_mask R3 :383-396）、disturbance（5.0/1.0/30、CUSUM k=0.5σ/h=5σ :65-67、置信 min(0.95,freq/20) :111、直接作用 pv−sp :59、恒偏差空结果 :62-63）、tuning（overshoot 0.25/decay 0.4/sse 0.05 :93-95、τ 表 :27-34、ratio 2.0/bias 0.2 :199-200）——数值与行号全部属实 ✓。
5. **oracle↔04 case 映射抽 4 行**：C07/C02/C01（60×0.4+90×0.6=78×R0.75=58.5 数值自洽）/C05 均一致 ✓。
6. **P0-03 五项关键事实**：calculate_hourly_kpi 签名（kpi_calc.py:152-154）/time_limit 14400/13800/max_retries 3 countdown 60（:138-150）；_save_confidence_latest try 吞错（:3118-3136）+ on_conflict loop_id（:2877-2884）；ci.yml L59-66 E2E 两步 continue-on-error（另 mypy L137 同）；pyproject addopts `-m 'not integration'`；kpi_capacity_benchmark 7101+size→pageSize(le=100,默认20) 静默早停仅 ~20 回路——全部属实 ✓。
7. **manifest 命令表抽验**：check_api_contract.py（根 scripts/ 存在，CI 同形）、eslint/test:unit/build:antd/check:type（frontend package.json 齐全）、apply_bootstrap_sql.py（backend/scripts/ 存在，CI 同形）、playwright config——均可直接执行 ✓。
8. **15 个 integration 文件**：tests/integration/ 14 个 test 文件与清单逐字一致 + 顶层 test_workbench_tasks.py:272 带标记 ✓。
9. **屏障四条件代码事实**：L1057 fire-and-forget、失败仅 warning :1059-1060、父任务随即 return :1062、latest on_conflict :2880-2883、try 吞错 :3131-3136、uq 约束 metric.py:160——全部属实 ✓。

## REWORK 清单（整合 §4/§5，均已由主协调修复）

1. **R1 DEC-02⑤ 推荐默认与被引冻结文档首选冲突**：冻结 §6.3 首选="修正 grading_config 默认值为 90/80/70/60 对齐表 D.1 并修正注释"，整合原版取括号内弱化路径（仅修注释）且"维持现行"与冻结首选矛盾。→ 已修复：整合版拆 ⑤a（修注释，零数值影响，推荐立即做）/⑤b（是否改默认值 90/80/60/40→90/80/70/60，数值语义变更），两文档口径分歧如实呈报由用户裁决。
2. **R2 DEC-10a 备选表述偏离**：冻结 §2.3/§7.6 备选="显式登记节点投影以最新写入为准"且未设推荐默认；整合原版自设"采纳补建"且备选改写。→ 已修复：备选改回冻结原表述，推荐列标注"整合层建议（冻结文档未设默认）"。
3. **R3 §5 G0 自评三处问题**：实际 6✅/4⏳ 而非 7✅3⏳；✅/⏳判据不统一；两条过期表述（oracle"待并入"——冻结 v1 §5 已含并登记 4 项 case 缺口；profile"起草中"——v1 已成）。→ 已修复：表重构为"产出/冻结条件"双列（10/10 产出完成、7 条待 DEC 冻结），判据统一，过期表述更正。

**范围外附带发现**：整合 §1 漂移计数 5→6 提交（与复核A 第 3 条同源，已修复）。

## E. 越界检查 — 通过

五份文档均无"已执行命令/已改代码/已批准 DEC"表述：命令全部 NOT_RUN，DEC 全部"推荐默认+备选"待裁决，状态均标"v1 草案/待冻结"；/tmp OCR 产物已披露且在仓库外；盘点"已取样"仅指只读环境查询。

## 未验边界

1. GB/T PDF 原文不可独立复核：页码映射与公式摘录仅完成两文档间一致性核对（一致，冻结未引入预备没有依据的新"标准原文"断言）；"视觉核对"本身无法复现，标准侧数值依赖预备报告可信度（代码侧权重等已独立核证）。
2. P0-01 台账 87/87 结论与整合 §1/§2/§3 其余内容未复核（复核A 范围）。
3. 未抽查的行号引用未逐条开码（classification 决策表/fusion/stiction 内部、SETNX 锁 L216-241/Beat L524-531/API 链、部分脚本行号、e2e fixtures/auth.ts、环境事实表、05 号文协议本身）。
4. 行为性结论均为静态代码推断未实测（pytest -m 覆盖 addopts、FastAPI 忽略未知 query 参数均为标准机制）。
5. /tmp OCR 产物真实性与品质未验证。
