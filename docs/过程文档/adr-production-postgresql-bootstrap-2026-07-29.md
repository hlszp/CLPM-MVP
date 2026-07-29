# ADR：生产 PostgreSQL Bootstrap 以 Head DDL 为真相源

> 日期：2026-07-29  
> 状态：Accepted（Phase 0 单元 B）  
> 范围：生产首次部署的 PostgreSQL schema 初始化

## 背景

生产 Compose 在空数据卷上依次执行：

1. `db/postgresql/01_schema.sql`
2. `db/postgresql/02_seed_data.sql`
3. 后端启动后 `deploy/lib-migrate.sh` 检测不到 Alembic revision，执行
   `alembic stamp head`

Alembic 首迁移 `772edf67d12d` 是空迁移，假定基础 DDL 已建好全部基线表。
因此不能简单移除 `01_schema.sql` 后对空库执行 `alembic upgrade head`。

2026-07-29 核验发现 ORM 已有 37 张表，而生产 DDL 只有 21 张。
首次部署随后直接 `stamp head`，会让缺失表对应迁移永久跳过。

## 决策

Phase 0 采用风险较低的方案：

- 保留 `01_schema.sql` 作为**空库初始化到当前 head schema** 的唯一入口；
- DDL 必须覆盖全部 ORM 表及其最终字段、类型、可空性、外键、唯一约束、
  CHECK 和索引；
- 首次部署完成 DDL 后继续 `stamp head`，后续版本继续
  `alembic upgrade head`；
- 每次 ORM/schema 变更必须同时更新 Alembic 迁移与 head DDL；
- 用机械测试保证 `DDL tables == Base.metadata.tables`；
- 用显式 opt-in、loopback-only 的临时数据库集成测试执行真实
  `01_schema.sql` 两次，验证可安全重放，并与 ORM 元数据做字段、类型、
  可空性、外键、唯一约束、CHECK 和索引的结构 diff。

长期若要切换为 Alembic 单一 bootstrap，必须先建立可从空库创建全量 schema
的新 baseline/squash migration，同时重新设计种子初始化。当前空的首迁移不支持
直接切换。

## 安全测试约束

集成测试只读取显式环境变量：

```bash
CLPM_BOOTSTRAP_TEST_ADMIN_DSN=postgresql://user:pass@127.0.0.1:7102/postgres \
  uv run pytest tests/integration/test_production_bootstrap.py \
  -v -m integration --no-header
```

测试会：

- 拒绝远程主机；
- 拒绝连接业务数据库，只接受 `postgres` 或 `template1` 管理库；
- 创建随机命名的 `clpm_bootstrap_test_<uuid>` 数据库；
- 在同一空库连续执行两次 `01_schema.sql`，防止部署重试触发
  `duplicate_object`；
- 在 `finally` 中终止该临时库连接并删除临时库；
- 未提供专用 DSN 时直接 skip，不猜测项目 `.env`，也不复用开发库。

## 合并门禁

- `tests/test_schema_convergence.py::test_production_bootstrap_ddl_covers_all_orm_tables`
  通过；
- 专用临时库 bootstrap 集成测试通过且不得 skip；
- `uv run alembic check` 退出码为 0；
- 全量后端测试和 ruff 门禁通过。
