# Phase D — D1 自动建单 + D2 Tracker 模型补全：数据库表结构设计

> 生成日期：2026-07-25
> 状态：D2 已实现（迁移 `b2d3e4f5g6h7`）；D1 待实施（需追加 3 列 + 服务层集成）

## 一、现状评估

### D2（Tracker 模型补全）— ✅ 已完成

| D2 要求 | 实现状态 | 位置 |
|---------|---------|------|
| `created_at` 建单时间 | ✅ 已迁移 | `b2d3e4f5g6h7` 迁移 |
| `comment` 处理意见 | ✅ 已迁移 | 同上 |
| `moc_ref` MOC 关联编号 | ✅ 已迁移 | 同上 |
| `moc_not_applicable` MOC 不适用 | ✅ 已迁移 | 同上 |
| `moc_reason` MOC 依据说明 | ✅ 已迁移 | 同上 |
| `diagnosis_result_id` 诊断结果外键 | ✅ 已迁移 | FK → `diagnosis_result.id` ON DELETE SET NULL |
| `(loop_id, diagnosis_label)` 开放态唯一约束 | ✅ 已迁移 | 部分唯一索引 `uk_action_tracker_open` |
| D3 MOC 必填校验 | ✅ 已实现 | `services/tracker.py:84-98` |

### D1（诊断→Tracker 自动建单）— ❌ 未实现

| D1 要求 | 实现状态 | 说明 |
|---------|---------|------|
| 诊断产出 ACTIVE 标签时自动创建 ActionTracker | ❌ | `diagnosis_engine.py` 无 ActionTracker 引用 |
| 同一回路同一标签未闭环前不重复建单 | ✅（schema 层） | 唯一索引已就绪，但无代码调用 |
| 区分自动建单 vs 手工建单 | ❌ | 缺 `trigger_type` 列 |
| 记录建单来源（system / 用户名） | ❌ | 缺 `triggered_by` 列 |
| 承载诊断严重等级 | ❌ | 缺 `severity` 列（便于按优先级筛选） |

### performance.py 跨模块耦合

| 位置 | 状态 | 说明 |
|------|------|------|
| L717-720（回路排行榜预诊断标签） | ✅ 已修复 | 有状态过滤 + `created_at.desc()` 排序 |
| L1436-1438（坏演员分布） | ⚠️ 部分修复 | 有状态过滤，**缺 `created_at.desc()` 排序** |

---

## 二、完整表结构 DDL

### 2.1 `action_tracker` — 异常跟踪记录（D2 已迁移 + D1 追加列）

```sql
-- ============================================================
-- action_tracker：异常跟踪记录表
-- D2 列已由迁移 b2d3e4f5g6h7 添加（2026-07-22）
-- D1 追加列由本次新建迁移添加（trigger_type / triggered_by / severity）
-- ============================================================

CREATE TABLE action_tracker (
    -- 主键
    id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),

    -- 关联回路（软删除不级联，保留历史）
    loop_id         UUID            REFERENCES loop_ledger(id) ON DELETE CASCADE,

    -- 诊断标签（如 OSCILLATION / VALVE_STICTION / OUTPUT_SATURATION）
    diagnosis_label VARCHAR(100),

    -- 状态机：PENDING → IN_PROGRESS → IMPLEMENTED / IGNORED
    action_status   VARCHAR(20)     NOT NULL DEFAULT 'PENDING',

    -- 证据材料 URL
    evidence_url    VARCHAR(255),

    -- 更新人 / 更新时间
    updated_by      VARCHAR(50),
    updated_at      TIMESTAMP,

    -- ========== D2 列（已迁移 b2d3e4f5g6h7）==========
    -- 建单时间（闭环时长 = updated_at - created_at）
    created_at      TIMESTAMP       NOT NULL DEFAULT now(),
    -- 处理意见 / 审查备注
    comment         VARCHAR(500),
    -- D3: MOC 变更管理关联
    moc_ref         VARCHAR(255),
    moc_not_applicable BOOLEAN,
    moc_reason      VARCHAR(500),
    -- 诊断结果外键（软删除不级联，保留历史）
    diagnosis_result_id UUID        REFERENCES diagnosis_result(id) ON DELETE SET NULL,

    -- ========== D1 追加列（本次新增）==========
    -- 建单方式：auto（系统自动）/ manual（用户手工）
    -- 默认 manual 保证存量数据兼容
    trigger_type    VARCHAR(10)     NOT NULL DEFAULT 'manual',
    -- 建单人：auto 时为 'system'，manual 时为用户名
    triggered_by    VARCHAR(50),
    -- 严重等级（从 diagnosis_tag.severity 冗余，便于按优先级筛选）
    -- INFO / WARN / ERROR / CRITICAL
    severity        VARCHAR(20),

    -- 约束
    CONSTRAINT ck_action_tracker_status
        CHECK (action_status IN ('PENDING', 'IN_PROGRESS', 'IGNORED', 'IMPLEMENTED')),
    CONSTRAINT ck_action_tracker_trigger_type
        CHECK (trigger_type IN ('auto', 'manual')),
    CONSTRAINT ck_action_tracker_severity
        CHECK (severity IS NULL OR severity IN ('INFO', 'WARN', 'ERROR', 'CRITICAL'))
);

-- 索引
-- D2: 开放态部分唯一索引（同一回路同一标签在 PENDING/IN_PROGRESS 下唯一）
CREATE UNIQUE INDEX uk_action_tracker_open
    ON action_tracker (loop_id, diagnosis_label)
    WHERE action_status IN ('PENDING', 'IN_PROGRESS')
      AND loop_id IS NOT NULL
      AND diagnosis_label IS NOT NULL;

-- 常规查询索引
CREATE INDEX idx_action_tracker_loop_id        ON action_tracker (loop_id);
CREATE INDEX idx_action_tracker_action_status   ON action_tracker (action_status);

-- D1 新增：按触发方式 + 严重等级筛选（工作台"诊断聚合卡"查询）
CREATE INDEX idx_action_tracker_trigger_type    ON action_tracker (trigger_type);
CREATE INDEX idx_action_tracker_severity_status ON action_tracker (severity, action_status);

-- D1 新增：建单时间排序（performance.py "最新一条"查询依赖）
CREATE INDEX idx_action_tracker_loop_created    ON action_tracker (loop_id, created_at DESC);
```

### 2.2 `diagnosis_tag` — 诊断标签（D1 读取源，已存在无需修改）

```sql
-- ============================================================
-- diagnosis_tag：回路级故障标签（D1 自动建单的触发源）
-- 已存在，无需修改。D1 读取 status='ACTIVE' 的标签。
-- ============================================================

CREATE TABLE diagnosis_tag (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    loop_id             UUID            NOT NULL REFERENCES loop_ledger(id) ON DELETE CASCADE,
    -- 标签代码（映射到 action_tracker.diagnosis_label）
    tag_code            VARCHAR(50)     NOT NULL,
    tag_name            VARCHAR(100),
    -- 严重等级（D1 自动建单时冗余到 action_tracker.severity）
    severity            VARCHAR(20)     NOT NULL,
    source_metric       VARCHAR(50),
    trigger_condition   JSONB,
    trigger_value       NUMERIC(10, 4),
    triggered_at        TIMESTAMP       NOT NULL DEFAULT now(),
    resolved_at         TIMESTAMP,
    resolved_by         UUID,
    resolution_note     TEXT,
    -- ACTIVE（生效中）/ RESOLVED（已解除）/ SUPPRESSED（已抑制）
    status              VARCHAR(20)     NOT NULL DEFAULT 'ACTIVE',

    CONSTRAINT ck_diag_tag_severity CHECK (severity IN ('INFO', 'WARN', 'ERROR', 'CRITICAL')),
    CONSTRAINT ck_diag_tag_status   CHECK (status IN ('ACTIVE', 'RESOLVED', 'SUPPRESSED'))
);

CREATE INDEX ix_diagnosis_tag_loop_status  ON diagnosis_tag (loop_id, status);
CREATE INDEX ix_diagnosis_tag_severity     ON diagnosis_tag (severity, triggered_at);
```

### 2.3 `diagnosis_result` — 诊断结果（D1 关联目标，已存在无需修改）

```sql
-- ============================================================
-- diagnosis_result：诊断引擎结果（D1 关联到 action_tracker）
-- 已存在，无需修改。D1 建单时取最新一条作为 diagnosis_result_id。
-- ============================================================

CREATE TABLE diagnosis_result (
    id                  UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    loop_id             UUID            REFERENCES loop_ledger(id) ON DELETE CASCADE,
    -- 诊断标签（与 diagnosis_tag.tag_code 同口径）
    diag_label          VARCHAR(100),
    confidence          NUMERIC(5, 2),
    feature_values      JSON,
    evidence_chain      JSON,
    algorithm_version   VARCHAR(50),
    threshold_version   INTEGER,
    diagnosed_at        TIMESTAMP       NOT NULL,
    task_id             UUID            REFERENCES diagnosis_task(id) ON DELETE SET NULL,

    CONSTRAINT ck_diagnosis_result_conf CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 100))
);

CREATE INDEX idx_diagnosis_result_loop_id     ON diagnosis_result (loop_id);
CREATE INDEX idx_diagnosis_result_diagnosed   ON diagnosis_result (diagnosed_at);
CREATE INDEX idx_diagnosis_result_task_id     ON diagnosis_result (task_id);
```

---

## 三、D1 追加列迁移 SQL

以下 SQL 对应新的 Alembic 迁移（`down_revision = 'b2d3e4f5g6h7'`）：

```sql
-- ============================================================
-- Migration: D1 追加列 — trigger_type / triggered_by / severity
-- Revises: b2d3e4f5g6h7
-- ============================================================

-- 1. 新增列
ALTER TABLE action_tracker
    ADD COLUMN trigger_type VARCHAR(10) NOT NULL DEFAULT 'manual'
        COMMENT '建单方式：auto(系统自动) / manual(用户手工)';

ALTER TABLE action_tracker
    ADD COLUMN triggered_by VARCHAR(50)
        COMMENT '建单人：auto 时为 system，manual 时为用户名';

ALTER TABLE action_tracker
    ADD COLUMN severity VARCHAR(20)
        COMMENT '严重等级（从 diagnosis_tag 冗余）：INFO/WARN/ERROR/CRITICAL';

-- 2. 新增 CHECK 约束
ALTER TABLE action_tracker
    ADD CONSTRAINT ck_action_tracker_trigger_type
        CHECK (trigger_type IN ('auto', 'manual'));

ALTER TABLE action_tracker
    ADD CONSTRAINT ck_action_tracker_severity
        CHECK (severity IS NULL OR severity IN ('INFO', 'WARN', 'ERROR', 'CRITICAL'));

-- 3. 新增索引
CREATE INDEX idx_action_tracker_trigger_type
    ON action_tracker (trigger_type);

CREATE INDEX idx_action_tracker_severity_status
    ON action_tracker (severity, action_status);

CREATE INDEX idx_action_tracker_loop_created
    ON action_tracker (loop_id, created_at DESC);

-- ============================================================
-- Downgrade
-- ============================================================
-- DROP INDEX idx_action_tracker_loop_created;
-- DROP INDEX idx_action_tracker_severity_status;
-- DROP INDEX idx_action_tracker_trigger_type;
-- ALTER TABLE action_tracker DROP CONSTRAINT ck_action_tracker_severity;
-- ALTER TABLE action_tracker DROP CONSTRAINT ck_action_tracker_trigger_type;
-- ALTER TABLE action_tracker DROP COLUMN severity;
-- ALTER TABLE action_tracker DROP COLUMN triggered_by;
-- ALTER TABLE action_tracker DROP COLUMN trigger_type;
```

---

## 四、D1 自动建单核心 SQL 逻辑

### 4.1 查询需要建单的 ACTIVE 标签

```sql
-- 找出所有 ACTIVE 标签中，尚无开放态 ActionTracker 的（loop_id, tag_code）组合
SELECT
    t.loop_id,
    t.tag_code     AS diagnosis_label,
    t.severity,
    t.id           AS tag_id,
    t.triggered_at AS tag_triggered_at
FROM diagnosis_tag t
WHERE t.status = 'ACTIVE'
  AND NOT EXISTS (
      -- 该回路+标签已有开放态 tracker（唯一索引保证不重复）
      SELECT 1
      FROM action_tracker at
      WHERE at.loop_id = t.loop_id
        AND at.diagnosis_label = t.tag_code
        AND at.action_status IN ('PENDING', 'IN_PROGRESS')
  );
```

### 4.2 批量插入 ActionTracker

```sql
-- 对每个需要建单的标签，取最新 diagnosis_result 作为关联，插入 tracker
INSERT INTO action_tracker (
    id,
    loop_id,
    diagnosis_label,
    action_status,
    trigger_type,
    triggered_by,
    severity,
    diagnosis_result_id,
    created_at
)
SELECT
    gen_random_uuid(),
    t.loop_id,
    t.tag_code,
    'PENDING',
    'auto',
    'system',
    t.severity,
    -- 取该回路+标签的最新一条 diagnosis_result
    (
        SELECT dr.id
        FROM diagnosis_result dr
        WHERE dr.loop_id = t.loop_id
          AND dr.diag_label = t.tag_code
        ORDER BY dr.diagnosed_at DESC
        LIMIT 1
    ),
    now()
FROM diagnosis_tag t
WHERE t.status = 'ACTIVE'
  AND NOT EXISTS (
      SELECT 1
      FROM action_tracker at
      WHERE at.loop_id = t.loop_id
        AND at.diagnosis_label = t.tag_code
        AND at.action_status IN ('PENDING', 'IN_PROGRESS')
  );
```

> **注意**：实际实现中应在 Python 服务层用 `try/except IntegrityError` 捕获唯一索引冲突，
> 而非纯 SQL `NOT EXISTS`（并发场景下 `NOT EXISTS` 与 `INSERT` 之间有竞态窗口）。
> 唯一索引 `uk_action_tracker_open` 是最终防线。

### 4.3 performance.py L1436 修复 SQL（补充排序）

```sql
-- 坏演员分布：当前查询缺 created_at 排序
-- 修复前（L1436-1438）：
SELECT * FROM action_tracker
WHERE loop_id IN (...)
  AND action_status IN ('PENDING', 'IN_PROGRESS');
-- 问题：同一回路多条记录时，Python 侧 label_count 计数可能包含已闭环后新建的记录

-- 修复后：加 created_at DESC 排序，确保取到最新一条
SELECT * FROM action_tracker
WHERE loop_id IN (...)
  AND action_status IN ('PENDING', 'IN_PROGRESS')
ORDER BY created_at DESC;
```

---

## 五、表关系图（ERD）

```
┌─────────────────┐         ┌──────────────────┐         ┌─────────────────────┐
│  diagnosis_tag   │         │ diagnosis_result  │         │   action_tracker     │
│─────────────────││         │──────────────────││         │─────────────────────││
│ id          PK  │         │ id           PK   │◀────────│ diagnosis_result_id FK│
│ loop_id     FK  │──┐      │ loop_id      FK   │         │ loop_id          FK  │
│ tag_code        │  │      │ diag_label        │         │ diagnosis_label      │
│ severity        │  │      │ confidence        │         │ action_status        │
│ status          │  │      │ algorithm_version │         │ trigger_type    [D1] │
│ triggered_at    │  │      │ threshold_version │         │ triggered_by    [D1] │
│ resolved_at     │  │      │ diagnosed_at      │         │ severity        [D1] │
└─────────────────┘  │      └──────────────────┘         │ created_at       [D2] │
                     │                                   │ comment          [D2] │
                     │      ┌──────────────────┐         │ moc_ref          [D3] │
                     │      │    loop_ledger    │         │ moc_not_applicable[D3]│
                     └─────▶│ id           PK   │◀────────│ moc_reason       [D3] │
                            │ tag_name          │         │ evidence_url         │
                            │ is_active         │         │ updated_by / at      │
                            └──────────────────┘         └─────────────────────┘
                                    │                              │
                                    │                              │ 唯一索引（开放态）
                                    │                              │ uk_action_tracker_open
                                    │                              │ (loop_id, diagnosis_label)
                                    │                              │ WHERE status IN (PENDING, IN_PROGRESS)
                                    │
                                    │ cascade
                                    ▼
                            ┌──────────────────┐
                            │ diagnosis_task    │
                            │──────────────────│
                            │ id           PK   │◀──── diagnosis_result.task_id FK
                            │ loop_id      FK   │
                            │ trigger_type      │
                            │ status            │
                            └──────────────────┘
```

---

## 六、实施清单

| # | 任务 | 类型 | 依赖 |
|---|------|------|------|
| 1 | 新建 Alembic 迁移：追加 `trigger_type` / `triggered_by` / `severity` 列 | DB 迁移 | D2 已完成 |
| 2 | ORM 模型 `tracker.py` 同步追加 3 个字段 | 代码 | #1 |
| 3 | `diagnosis_engine.py` 诊断落库段追加 D1 自动建单逻辑 | 服务层 | #1, #2 |
| 4 | `performance.py` L1436 补 `order_by(created_at.desc())` | 代码修复 | 无 |
| 5 | `tracker.py` `update_tracker_status` 修复：最新 tracker 已闭环时新建而非覆盖 | 代码修复 | #2 |
| 6 | 工作台 dashboard "诊断聚合卡" 查询接入 | 前端+后端 | #3 |
| 7 | 单元测试：D1 自动建单 + 防重复 + D3 MOC 校验 | 测试 | #3 |
| 8 | E2E 测试：诊断→自动建单→Tracker 列表可见 | 测试 | #6 |

---

## 七、设计决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| `trigger_type` 默认值 | `'manual'` | 存量数据兼容，不破坏现有手工创建的 tracker |
| `severity` 冗余存储 | 是 | 避免每次查询 tracker 都 JOIN `diagnosis_tag`；severity 在建单后不会变化 |
| `diagnosis_tag_id` FK | 不加 | `diagnosis_result_id` 已提供足够的追溯链路；`diagnosis_label` 字段匹配即可 |
| 并发防重复策略 | 唯一索引 + `try/except IntegrityError` | `NOT EXISTS` + `INSERT` 有竞态窗口，唯一索引是最终防线 |
| 闭环后新建 vs 覆盖 | 新建（保留历史） | 唯一索引仅约束开放态，闭环后允许新建；`update_tracker_status` 需修复为"最新已闭环时新建" |
