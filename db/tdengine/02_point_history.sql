-- =============================================================================
-- 测点子表稳定表: st_point_data_v1（codex/tag-timeseries-refactor P1）
-- 设计依据: docs/设计文档/2026-09-06-tag-timeseries-logical-wide-refactor.md §4.1
--
-- 说明:
--   1. 本文件是拟实施契约的文档化 DDL；实际执行由
--      backend/app/services/data_source/point_history_repository.ensure_schema()
--      完成（库名从 settings.TDENGINE_DB 注入，隔离环境为 clpm_ts_ref，
--      开发/生产为 clpm_ts——两种环境下该脚本均可重复执行，幂等）。
--   2. 旧宽表 st_loop_data 已退役（2026-09-25/26：写入与读取路径删除、DDL 占位化）；
      本文件不再涉及宽表，仅定义测点点表。
--   3. 子表命名固定 p_<tag_registry.id 去连字符小写>，由应用层生成，
--      不从用户可编辑位号拼接（设计 §4.1）。
--   4. P1 已在真实 TDengine 3.3.6.0 / 3.3.6.6（arm64）验证：
--      DOUBLE NULL、毫秒精度、同 ts 重复写、同 ts 更正（UPSERT）、
--      跨子表部分成功语义——证据见
--      backend/tests/integration/test_refactor_point_store.py。
-- =============================================================================

-- 建库：属性与 backend/app/services/data_source/point_history_repository.ensure_schema()
-- 保持一致（KEEP 365 / DURATION 10 / PRECISION 'ms'）。
-- 首次部署时本文件由 TDengine 容器 entrypoint 执行（挂载见 docker-compose.prod.yml）；
-- 应用侧 ensure_schema() 亦会幂等自建，两者互为兜底。
CREATE DATABASE IF NOT EXISTS clpm_ts KEEP 365 DURATION 10 PRECISION 'ms';

CREATE STABLE IF NOT EXISTS clpm_ts.st_point_data_v1 (
    -- ts: 该条来源记录的时间（源 collectTime/历史样本时间），非 flush/接收时间
    ts              TIMESTAMP,
    -- value: 七类数值角色的有限数或 NULL（MODE 存整数值，语义由角色决定）
    value           DOUBLE,
    -- quality_raw: 收到的原码；不同体系（AAS 枚举/OPC DA 位编码）不得混用
    quality_raw     INT,
    -- quality_class: 本层固定三态 1=GOOD / 0=BAD / -1=UNKNOWN(含 Uncertain)
    quality_class   TINYINT,
    -- quality_schema: 1=AAS 枚举 / 2=OPC DA 位编码；未知体系可为 NULL
    quality_schema  TINYINT,
    -- received_at: 接收时间（审计用），绝不作为历史 ts 兜底
    received_at     TIMESTAMP,
    -- source_kind: 1=实时变化 2=首次/恢复快照 3=确认的原始历史事件 4=远端重建样本
    source_kind     TINYINT,
    -- payload_hash: 归一化载荷摘要（点ID|ts|值|质量|来源类别），对账用
    payload_hash    BINARY(64)
) TAGS (
    -- point_id: tag_registry.id（36 字符 UUID）
    point_id        BINARY(36),
    -- source_id: 源身份（AAS 实例/导入任务标识），非网络模式
    source_id       BINARY(64)
);
