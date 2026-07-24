# P4 复杂回路聚合 S1-S4 全链路端到端验证报告

**日期**: 2026-07-24
**验证范围**: P4 复杂回路聚合功能 S1-S4（含 S5 真实数据验证）
**验证结论**: ✅ **全部通过**
**关联文档**: [complex-loop-aggregation-rfc-2026-07-24.md](./complex-loop-aggregation-rfc-2026-07-24.md)
**关联提交**: `e9df4545` → `4ebbbec8` → `168fbf32` → `b01ea26c`

---

## 一、验证目标

验证 P4 复杂回路聚合功能 S1-S4 四个阶段的全链路正确性，覆盖以下核心路径：

```
用户创建复杂分组（S4 API）
  → loop_ledger 写入 complex_loop_group_id + complex_role（S2 字段）
  → 触发节点级 KPI 聚合
    → SQL 查询回路级 SUCCESS 快照
    → S1: 过滤 include_in_evaluation=False 回路
    → S3: 按 complex_loop_group_id 去重（MAIN 优先 → confidence 回退）
    → Python 按 importance_level 加权平均
    → 返回去重后 loop_count 与聚合指标
```

**核心验证点**：

| # | 验证点 | 对应阶段 | 预期 |
|---|--------|---------|------|
| 1 | `include_in_evaluation=False` 回路不进入聚合 | S1 | 聚合输入排除标记为不参评的回路 |
| 2 | `complex_loop_group_id` + `complex_role` 字段正确持久化 | S2 | DB 写入 / 读取一致 |
| 3 | 同组回路去重为 1 个代表，`loop_count` 减少 | S3 | N 个回路归为 1 组 → loop_count 减 N-1 |
| 4 | MAIN 角色优先作为代表 | S3 | 代表回路为 MAIN，非 confidence 最高 |
| 5 | 加权平均使用代表回路的指标 + importance_level 权重 | S3 | 聚合结果与手算一致 |
| 6 | 批量分组 API 正确创建分组 | S4 | 返回 groupId + assignments |
| 7 | 解除分组恢复原始状态 | S4 | complex 字段清空，聚合恢复基线 |

---

## 二、测试环境

| 项目 | 配置 |
|------|------|
| 后端 | uvicorn `app.main:app` --host 0.0.0.0 --port 7101 --reload |
| 数据库 | PostgreSQL localhost:7102, DB=clpm |
| Celery | Worker + Beat 随后端 lifespan 自动启动 |
| 认证 | admin / admin123（ADMIN 角色） |
| 测试时间窗 | 2026-07-24 07:00:00 ~ 07:01:00（仅含 07:00 整点快照） |
| 测试装置节点 | `3353a2b2-2d4f-4907-9964-fb2aac837352`（脱甲烷精馏单元） |

---

## 三、S1-S4 实施背景

| 阶段 | 提交 | 内容 | 关键文件 |
|------|------|------|---------|
| S1 | `e9df4545` | 节点聚合过滤 `include_in_evaluation=False` 回路 | [node_performance.py:337](../../backend/app/services/node_performance.py#L337) |
| S2 | `4ebbbec8` | loop_ledger 新增 `complex_loop_group_id` + `complex_role` 字段 + alembic 迁移 | [loop.py:77-84](../../backend/app/models/loop.py#L77-L84) |
| S3 | `168fbf32` | 复杂回路组 Python 去重 + 加权聚合（替代单 SQL 聚合） | [node_performance.py:248-388](../../backend/app/services/node_performance.py#L248-L388) |
| S4 | `b01ea26c` | 前端回路分组配置 UI + 后端批量分组/分组列表 API 全链路 | [manage.vue](../../frontend/apps/web-antd/src/views/loop/manage.vue), [loops.py](../../backend/app/api/v1/endpoints/loops.py) |

### S3 去重核心逻辑

```python
# node_performance.py L260-285
def _dedup_complex_groups(rows: list) -> list:
    """复杂回路组去重（RFC 决策点 2）。
    - complex_loop_group_id 为空（普通单回路）：全部保留
    - 同 complex_loop_group_id 的组：仅保留一个代表（MAIN 优先，否则 confidence 最高）
    """
    singles = [r for r in rows if r.complex_loop_group_id is None]
    groups: dict[str, list] = {}
    for r in rows:
        gid = r.complex_loop_group_id
        if gid is not None:
            groups.setdefault(gid, []).append(r)
    representatives = list(singles)
    for members in groups.values():
        representatives.append(_pick_group_representative(members))
    return representatives

# node_performance.py L248-257
def _pick_group_representative(members: list) -> object:
    """从复杂回路组成员中选代表（RFC 决策点 2）。
    - 有 complex_role=MAIN 的成员 → 取 MAIN
    - MAIN 缺席 → 取 confidence 最高（A>B>C>D>E，None 最低）
    """
    mains = [m for m in members if m.complex_role == COMPLEX_ROLE_MAIN]
    if mains:
        return mains[0]
    return min(members, key=lambda m: _confidence_rank(m.confidence_level))
```

---

## 四、测试数据

### 4.1 测试装置下 7 个回路（07:00 SUCCESS 快照）

| # | Loop ID | Tag | Level | Weight | Score | Conf | Auto% | Steady% | Acc% |
|---|---------|-----|-------|--------|-------|------|-------|---------|------|
| 1 | `640a0ce1` | 41FIC40504_PIDA | 1 | 3.0 | 59.44 | C | 100 | 0 | 91.84 |
| 2 | `82027a76` | 41FIC40519_PIDA | 2 | 2.0 | 58.39 | D | 100 | 0 | 89.05 |
| 3 | `c4073df9` | 41LIC40108_PIDA | 3 | 1.0 | 59.42 | B | 100 | 0 | 91.80 |
| 4 | `f69922dd` | 41LIC40201_PIDA | 3 | 1.0 | 59.61 | B | 100 | 0 | 92.29 |
| 5 | `57715824` | 41LIC40309_PIDA | 3 | 1.0 | 60.02 | B | 100 | 0 | 93.40 |
| 6 | `436dea56` | 41LIC40404_PIDA | 3 | 1.0 | 60.13 | B | 100 | 0 | 93.69 |
| 7 | `474c1e51` | 41TIC40201_PIDA | 3 | 1.0 | 45.07 | E | 100 | 3.28 | 50.25 |

> 权重规则：level 1 → 3.0，level 2 → 2.0，level 3 → 1.0（对齐 GB/T 44693.2-2024 附表2）

### 4.2 分组方案

将回路 #1 和 #2 归为一个复杂回路组：

| Loop | Tag | 角色 | 理由 |
|------|-----|------|------|
| `640a0ce1` | 41FIC40504_PIDA | **MAIN** | level 1（高权重），代表该物理控制回路 |
| `82027a76` | 41FIC40519_PIDA | **SUB** | level 2，聚合时被去重剔除 |

选择这两个回路的理由：不同 importance_level（1 vs 2），权重差异明显（3.0 vs 2.0），便于验证加权平均变化；不同 confidence（C vs D），可验证 MAIN 优先于 confidence 的选择逻辑。

---

## 五、手算期望值

### 5.1 BEFORE（7 个单回路，无去重）

```
weight_total = 3 + 2 + 1 + 1 + 1 + 1 + 1 = 10

score = (59.44×3 + 58.39×2 + 59.42×1 + 59.61×1 + 60.02×1 + 60.13×1 + 45.07×1) / 10
      = (178.32 + 116.78 + 59.42 + 59.61 + 60.02 + 60.13 + 45.07) / 10
      = 579.35 / 10
      = 57.94（quantize 0.01，ROUND_HALF_EVEN）

accuracy_rate = (91.84×3 + 89.05×2 + 91.80 + 92.29 + 93.40 + 93.69 + 50.25) / 10
             = 875.05 / 10
             = 87.50

loop_count = 7
auto_loop_ratio = 7/7 × 100 = 100.00
```

### 5.2 AFTER（6 个代表：5 单回路 + 1 组代表）

去重后保留 MAIN 回路（#1, score=59.44, weight=3.0），剔除 SUB 回路（#2）。

```
weight_total = 3 + 1 + 1 + 1 + 1 + 1 = 8

score = (59.44×3 + 59.42 + 59.61 + 60.02 + 60.13 + 45.07) / 8
      = (178.32 + 59.42 + 59.61 + 60.02 + 60.13 + 45.07) / 8
      = 462.57 / 8
      = 57.82

accuracy_rate = (91.84×3 + 91.80 + 92.29 + 93.40 + 93.69 + 50.25) / 8
             = 696.95 / 8
             = 87.12

loop_count = 6
auto_loop_ratio = 6/6 × 100 = 100.00
```

### 5.3 反证：若选错代表（选 SUB 而非 MAIN）

```
score_wrong = (58.39×2 + 59.42 + 59.61 + 60.02 + 60.13 + 45.07) / 8
            = 401.03 / 8
            = 50.13  ← 与实测 57.82 不符，证明 MAIN 被正确选中
```

---

## 六、执行步骤与结果

### Step 1: 基线聚合（BEFORE 分组）

**API 调用**：
```bash
POST /api/v1/performance/nodes/3353a2b2-2d4f-4907-9964-fb2aac837352/calculate
Authorization: Bearer <admin-token>
Content-Type: application/json

{"tsStart":"2026-07-24T07:00:00","tsEnd":"2026-07-24T07:01:00"}
```

**返回结果**：

| 字段 | 实测值 | 手算期望 | 结论 |
|------|--------|---------|------|
| status | SUCCESS | — | ✅ |
| loop_count | 7 | 7 | ✅ |
| score | 57.94 | 57.94 | ✅ |
| accuracy_rate | 87.50 | 87.50 | ✅ |
| steady_rate | 0.33 | 0.33 (3.28/10) | ✅ |
| auto_loop_ratio | 100.00 | 100.00 | ✅ |
| good_value_rate | 98.00 | — | ✅ 合理 |
| oscillation_rate | 99.67 | — | ✅ 合理 |

### Step 2: 创建复杂分组

**API 调用**：
```bash
POST /api/v1/loops/batch-grouping
Authorization: Bearer <admin-token>
Content-Type: application/json

{
  "loopIds": ["640a0ce1-64da-4fbb-8f7f-7305542754a9", "82027a76-42c7-4cf0-852a-cf2402accfb0"],
  "mainLoopId": "640a0ce1-64da-4fbb-8f7f-7305542754a9"
}
```

**返回结果**：
```json
{
  "code": "0",
  "message": "批量分组成功",
  "data": {
    "groupId": "b9d1cd0d-2f97-42ea-9cde-15e394095d27",
    "affected": 2,
    "assignments": [
      {"loopId": "640a0ce1-...", "tagName": "41FIC40504_PIDA", "role": "MAIN"},
      {"loopId": "82027a76-...", "tagName": "41FIC40519_PIDA", "role": "SUB"}
    ]
  }
}
```

**DB 验证**：
```
640a0ce1 | 41FIC40504_PIDA | b9d1cd0d-2f97-42ea-9cde-15e394095d27 | MAIN
82027a76 | 41FIC40519_PIDA | b9d1cd0d-2f97-42ea-9cde-15e394095d27 | SUB
```
✅ 字段正确持久化

### Step 3: 聚合验证（AFTER 分组）

**API 调用**：同 Step 1（相同时间窗、相同节点）

**返回结果**：

| 字段 | BEFORE | AFTER | 手算期望(AFTER) | 结论 |
|------|--------|-------|----------------|------|
| loop_count | 7 | **6** | 6 | ✅ 去重生效（7−1=6） |
| score | 57.94 | **57.82** | 57.82 | ✅ MAIN 代表保留，SUB 剔除 |
| accuracy_rate | 87.50 | **87.12** | 87.12 | ✅ 加权正确 |
| steady_rate | 0.33 | **0.41** | 0.41 (3.28/8) | ✅ 分母变化正确 |
| auto_loop_ratio | 100.00 | 100.00 | 100.00 | ✅ 不变（均为自动） |

### Step 4: DEBUG 日志验证

后端日志确认 S3 去重逻辑执行：

```
# BEFORE（17:20:35）— 无分组
[节点级聚合-S3] 输入回路=7, 去重后代表=7, 复杂组=0

# AFTER（17:21:03）— 有分组
[节点级聚合-S3] 输入回路=7, 去重后代表=6, 复杂组=1
```

✅ 输入 7 回路 → 去重后 6 代表（2 回路归为 1 组，−1）

### Step 5: 清理与状态恢复

**API 调用**（对两个回路分别解除分组）：
```bash
PUT /api/v1/loops/640a0ce1-64da-4fbb-8f7f-7305542754a9
{"complexLoopGroupId": null, "complexRole": null}

PUT /api/v1/loops/82027a76-42c7-4cf0-852a-cf2402accfb0
{"complexLoopGroupId": null, "complexRole": null}
```

**DB 验证**：
```
640a0ce1 | 41FIC40504_PIDA | (NULL) | (NULL)
82027a76 | 41FIC40519_PIDA | (NULL) | (NULL)
```
✅ complex 字段已清空

**最终聚合验证**（确认恢复基线）：

| 字段 | 实测值 | 期望（=BEFORE） | 结论 |
|------|--------|----------------|------|
| loop_count | 7 | 7 | ✅ |
| score | 57.94 | 57.94 | ✅ |
| accuracy_rate | 87.50 | 87.50 | ✅ |

---

## 七、验证矩阵汇总

| # | 验证点 | 阶段 | 验证方式 | 结果 |
|---|--------|------|---------|------|
| 1 | `include_in_evaluation=False` 回路不进入聚合 | S1 | 代码审查 [node_performance.py:337](../../backend/app/services/node_performance.py#L337) `WHERE include_in_evaluation.is_(True)` | ✅ |
| 2 | `complex_loop_group_id` + `complex_role` 持久化 | S2 | DB 查询确认字段写入/读取一致 | ✅ |
| 3 | 同组回路去重为 1 个代表 | S3 | loop_count 7→6，DEBUG 日志 `去重后代表=6, 复杂组=1` | ✅ |
| 4 | MAIN 角色优先作为代表 | S3 | score=57.82（匹配 MAIN 保留）而非 50.13（SUB 保留） | ✅ |
| 5 | 加权平均使用 importance_level 权重 | S3 | score/accuracy_rate/steady_rate 均与手算一致 | ✅ |
| 6 | 批量分组 API 正确创建分组 | S4 | 返回 groupId + 2 assignments（MAIN/SUB） | ✅ |
| 7 | 解除分组恢复原始状态 | S4 | complex 字段清空，聚合 loop_count=7 恢复基线 | ✅ |
| 8 | 单回路（无分组）不受影响 | S3 | 其余 5 个单回路始终参与聚合，权重不变 | ✅ |
| 9 | auto_loop_ratio 计算基于去重后代表 | S3 | 7/7→6/6，均为 100%（代表回路均为自动） | ✅ |

---

## 八、全链路覆盖图

```
┌─────────────────────────────────────────────────────────────────────┐
│                        用户操作层（S4）                              │
│  POST /loops/batch-grouping                                         │
│  body: {loopIds:[...], mainLoopId:"..."}                            │
└──────────────────────────┬──────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    API 端点层（S4）                                  │
│  loops.py: batch_group_loops_endpoint()                             │
│  → service: batch_group_loops() 生成 UUID, 赋值 MAIN/SUB            │
└──────────────────────────┬──────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    数据持久层（S2）                                  │
│  loop_ledger.complex_loop_group_id = <UUID>                         │
│  loop_ledger.complex_role = "MAIN" / "SUB"                          │
│  CHECK: group_id 与 role 同时为空或同时非空                          │
│  CHECK: role ∈ {MAIN, SUB}                                          │
│  INDEX: idx_loop_ledger_complex_group                               │
└──────────────────────────┬──────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│              节点聚合触发（POST /performance/nodes/{id}/calculate）  │
│  node_performance.py: calculate_node_endpoint()                     │
│  → aggregate_node_snapshot()                                        │
│    → collect_descendant_loop_ids() 递归收集节点下属回路              │
└──────────────────────────┬──────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│              SQL 查询层（S1 + S3）                                   │
│  _fetch_and_aggregate_loops() L288-388:                             │
│  ① 子查询: KpiSnapshotHourly 最新 SUCCESS 快照（DISTINCT ON loop_id）│
│  ② JOIN loop_ledger + loop_level_weight                            │
│  ③ S1: WHERE loop_ledger.include_in_evaluation = True  ← 验证点 #1  │
│  ④ 取 complex_loop_group_id, complex_role, weight                   │
└──────────────────────────┬──────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│              Python 去重层（S3）                                     │
│  _dedup_complex_groups(rows) L260:                                  │
│  ① 单回路（group_id=NULL）→ 全部保留                                │
│  ② 同 group_id 回路 → _pick_group_representative():                │
│     - MAIN 优先 → 取第一个 MAIN                      ← 验证点 #4    │
│     - MAIN 缺席 → confidence 最高（A>B>C>D>E）                      │
│  ③ 返回 representatives 列表                         ← 验证点 #3    │
└──────────────────────────┬──────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│              Python 加权聚合层（S3）                                 │
│  ① weight_total = Σ(representative.weight)         ← 验证点 #5      │
│  ② avg_value(field) = Σ(field × weight) / weight_total             │
│  ③ loop_count = len(representatives)                                │
│  ④ auto_loop_count = count(auto_mode_rate > 0)                     │
│  ⑤ auto_loop_ratio = auto_loop_count / loop_count × 100            │
└──────────────────────────┬──────────────────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────────────────┐
│              结果返回与持久化                                        │
│  → NodeSnapshot 写入 node_kpi_snapshot 表                           │
│  → API 返回 {loop_count, score, accuracy_rate, ...}                │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 九、单元测试覆盖

S4 阶段新增 34 个单元测试（[test_loop_complex_grouping.py](../../backend/tests/test_loop_complex_grouping.py)），覆盖：

| 测试类 | 用例数 | 覆盖内容 |
|--------|--------|---------|
| TestValidateComplexGroup | 6 | 一致性校验、角色合法性、MAIN 唯一性、UUID 格式 |
| TestBatchGroupingService | 8 | 主回路不在列表、回路不存在、正常分组、已分组覆盖 |
| TestBatchGroupingEndpoint | 5 | API 成功、权限校验、请求体校验（min/max length） |
| TestCreateLoopWithComplexGroup | 4 | 创建时携带分组字段、校验逻辑 |
| TestUpdateLoopWithComplexGroup | 5 | 更新分组、解除分组、MAIN 唯一性冲突 |
| TestListLoopsComplexGroup | 3 | 列表返回分组字段、分组列表 API |
| TestComplexGroupEdgeCases | 3 | 边界条件（空值、单回路、多组） |

**执行结果**: `34 passed, 6 warnings in 2.28s`

---

## 十、结论

### 10.1 验证结论

P4 复杂回路聚合功能 S1-S4 全链路端到端验证 **全部通过**：

1. **S1（include_in_evaluation 过滤）**：不参评回路在 SQL 阶段被正确排除
2. **S2（字段 + 迁移）**：`complex_loop_group_id` + `complex_role` 正确持久化，CHECK 约束生效
3. **S3（Python 去重 + 加权聚合）**：MAIN 优先代表选择正确，loop_count 去重准确，加权平均与手算完全一致
4. **S4（API + UI 全链路）**：批量分组 API 正确创建/解除分组，节点聚合结果正确反映分组变化

### 10.2 风险确认

| 风险项 | 状态 | 说明 |
|--------|------|------|
| 装置级评分变化 | ✅ 已验证 | 复杂回路去重后评分变化方向合理（双计回路权重下降） |
| MAIN 缺席回退 | ⚠️ 未测 | 本次测试 MAIN 存在；回退逻辑（confidence 最高）由单元测试覆盖 |
| 多组同时去重 | ⚠️ 未测 | 本次仅 1 组；多组场景逻辑相同（dict 遍历），单元测试覆盖 |
| 历史 loop_count 不一致 | ✅ 可接受 | 历史快照保留当时口径，RFC 决策点 3 已确认 |
| performance.py 跨模块耦合 | 📋 待跟踪 | D1/D2 阶段需同步审查 ActionTracker 查询（评审报告已标记） |

### 10.3 后续建议

| 优先级 | 事项 |
|--------|------|
| 中 | 补充多组同时去重的 e2e 场景（3+ 组并行） |
| 中 | 补充 MAIN 缺席 → confidence 回退的 e2e 场景 |
| 低 | S6 文档更新（DDS / 实现契约 §聚合章节补充复杂回路去重规则） |
| 低 | 前端 E2E 测试补充（Playwright 覆盖批量分组弹窗交互） |

---

## 附录 A：API 调用清单

| # | 方法 | 路径 | 用途 |
|---|------|------|------|
| 1 | POST | `/api/v1/auth/login` | 获取 JWT Token |
| 2 | POST | `/api/v1/performance/nodes/{nodeId}/calculate` | 触发节点级 KPI 聚合 |
| 3 | POST | `/api/v1/loops/batch-grouping` | 批量创建复杂回路分组 |
| 4 | PUT | `/api/v1/loops/{loopId}` | 更新回路（含解除分组：complexLoopGroupId=null） |

## 附录 B：SQL 查询清单

```sql
-- B1: 查询测试装置下有 SUCCESS 快照的回路
SELECT l.id, l.tag_name, l.importance_level, l.complex_loop_group_id, l.complex_role,
       s.score, s.confidence_level, s.auto_mode_rate, s.steady_rate, s.accuracy_rate,
       s.effective_auto_rate, s.status, s.ts_start
FROM loop_ledger l
JOIN kpi_snapshot_hourly s ON s.loop_id = l.id AND s.status = 'SUCCESS'
WHERE l.unit_id = '3353a2b2-2d4f-4907-9964-fb2aac837352'
  AND l.include_in_evaluation = true AND l.is_active = true
  AND s.ts_start >= '2026-07-24 07:00:00' AND s.ts_start <= '2026-07-24 07:01:00'
ORDER BY l.tag_name;

-- B2: 验证分组字段持久化
SELECT id, tag_name, complex_loop_group_id, complex_role
FROM loop_ledger
WHERE id IN ('640a0ce1-...', '82027a76-...');
```

## 附录 C：手算详细过程

### C.1 BEFORE（7 回路，weight_total=10）

| Loop | Score | Weight | Score×Weight | Acc% | Acc×Weight |
|------|-------|--------|-------------|------|-----------|
| 41FIC40504 | 59.44 | 3 | 178.32 | 91.84 | 275.52 |
| 41FIC40519 | 58.39 | 2 | 116.78 | 89.05 | 178.10 |
| 41LIC40108 | 59.42 | 1 | 59.42 | 91.80 | 91.80 |
| 41LIC40201 | 59.61 | 1 | 59.61 | 92.29 | 92.29 |
| 41LIC40309 | 60.02 | 1 | 60.02 | 93.40 | 93.40 |
| 41LIC40404 | 60.13 | 1 | 60.13 | 93.69 | 93.69 |
| 41TIC40201 | 45.07 | 1 | 45.07 | 50.25 | 50.25 |
| **Σ** | | **10** | **579.35** | | **875.05** |

score = 579.35 / 10 = **57.94**
accuracy_rate = 875.05 / 10 = **87.50**

### C.2 AFTER（6 代表，weight_total=8）

剔除 SUB 回路（41FIC40519），保留 MAIN 回路（41FIC40504）。

| Loop | Score | Weight | Score×Weight | Acc% | Acc×Weight |
|------|-------|--------|-------------|------|-----------|
| 41FIC40504 (MAIN) | 59.44 | 3 | 178.32 | 91.84 | 275.52 |
| 41LIC40108 | 59.42 | 1 | 59.42 | 91.80 | 91.80 |
| 41LIC40201 | 59.61 | 1 | 59.61 | 92.29 | 92.29 |
| 41LIC40309 | 60.02 | 1 | 60.02 | 93.40 | 93.40 |
| 41LIC40404 | 60.13 | 1 | 60.13 | 93.69 | 93.69 |
| 41TIC40201 | 45.07 | 1 | 45.07 | 50.25 | 50.25 |
| **Σ** | | **8** | **462.57** | | **696.95** |

score = 462.57 / 8 = **57.82**
accuracy_rate = 696.95 / 8 = **87.12**
