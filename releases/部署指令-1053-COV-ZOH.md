# full-1053：COV 保持有效性修复与现场取证

日期：2026-10-23。源码基线：`macbook / 3f6dee798`，本次未提交、未推送。

## 结论与已取得的现场证据

**1053 已由用户部署，`41LIC20130_PIDA` 北京时间 14:00–15:00 窗口的 ZOH 空分问题已完成现场落库验收：新快照于 15:55 写入，`SUCCESS / valid_rate=1 / confidence_level=A / score=0.01`，血缘为 `pre_v4`。此结论限该回路该窗口，其他回路及页面显示未据此验收。**

1052 的现场根因为：OP 的 3471 个秒级槽因 `held_too_long` 被标 UNKNOWN，有效自控率 R 仅剩 130/3601 的覆盖，综合评分因此留空。以下保留完整取证过程。

用户回传的容器原文：

```text
/clpm-backend clpm-backend:full-1052 2026-10-23T06:25:20.596580827Z
/clpm-celery-worker clpm-backend:full-1052 2026-10-23T06:25:20.59619221Z
/clpm-celery-beat clpm-backend:full-1052 2026-10-23T06:25:20.595150639Z
```

用户回传 SQL 的关键原值，时间为 UTC：

| ts_start → ts_end | written_at_utc | source | valid_rate | conf | score | R knownSeconds / denominatorSeconds |
|---|---|---|---|---|---|---|
| 2026-10-23 06:00 → 07:00 | 07:10:43.923219 | SCHEDULED | 1.0000 | E | NULL | 130 / 3601，coverageRatio=0.0361 |
| 2026-10-23 05:00 → 06:00 | 06:45:41.827987 | BACKFILL | 1.0000 | E | NULL | 61 / 3601，coverageRatio=0.0169 |
| 2026-10-23 04:00 → 05:00 | 06:45:41.826321 | BACKFILL | 1.0000 | E | NULL | 0 / 3601，coverageRatio=0 |

最新窗口的 `source_task_id=9584a847-90f7-47e5-9724-3facaad86df1`，北京时间窗口为 14:00–15:00，15:10:43 写入。

```json
{"value":null,"details":{"reason":"effective_auto_rate INCONCLUSIVE"},"assessmentState":null,"confidenceLevel":"E"}
```

R 明细原值：`knownSeconds=130.0`、`unknownSeconds=3471.0`、`denominatorSeconds=3601.0`、`bad_quality_points=0`、`confidenceLevel=E`。`3601` 是当前含尾时间窗契约，不在本批变更。

快照 `valid_rate` 来自 BASE 数据块；`confidence_level` 来自综合评分。两者为 1/E 并不证明计算未执行。还须核对各 TagGroup、真实 gap 与 R 的输入。

现场日志观察：ToDesk 中执行 `docker logs --since 2026-10-23T06:25:20Z --timestamps clpm-celery-worker 2>&1 | grep -E 'Traceback|ERROR|Exception'`，可见匹配均为 `"level": "INFO"`、`"logger": "app.services.alert_rule_engine.dispatcher"` 的业务预警创建消息，消息末尾 `severity=ERROR` 触发了 grep。可见输出未出现 Python traceback，不能把业务预警级别当成 worker 异常。

用户随后回传了按回路 UUID `037c3ed1-ff2e-483a-aee3-a6958928df75` 正确筛选的最近 25 条日志，原文保存为 `现场日志-1052-41LIC20130-20261023.txt`。该截取范围明确，不代表 worker 全部日志。

- `07:10:33Z` 五组均出现 `DataPlanner 回源查询`，随后执行预处理并写入五块缓存；本轮不是旧缓存直接返回。
- `07:10:41Z` 的 `OP_HF / PVOP_HF / MODE_HF` 均为 `points=3601, loop_valid_rate=0.0361`；各自存在的 PV/OP 原始有效性均为 `3601/3601`。MODE_HF 的 SP/MODE 也均为 `3601/3601`。
- 完整一小时秒级网格结合 1052 的 `compute_time_coverage` 实现，时间覆盖因子为 1；原始有效性全真而结果只有 0.0361，将降幅定位到 `role_coverage → _zoh_validity` 分支，与快照 R 的 `130/3601` 一致。这是现场日志结合源码的定位，尚无逐角色覆盖明细证明具体哪些槽因 `held_too_long`、真实 `gap` 或窗前 HELD 初值丢失而被扣除。
- 两个回填窗口分别返回 `success=0, inconclusive=1, failed=0`；汇总虽为 `status=SUCCESS`，实际 `loop_success=0, loop_inconclusive=2, loop_failed=0`。任务执行完成不等于评分有值。所给 25 条中未见执行异常。
- `QUALITY_HF` 仅有 `pv_quality`，无 PV/OP，因此组级有效率为 0、可信度 E。源码及本地穿透测试确认：好值率独立读取质量码摘要，综合评分仅检查 R/A/F/S；该组 E 不是本次 `effective_auto_rate INCONCLUSIVE` 的来源，本批不扩大修复范围。

### 15:38 现场探针补证：本窗根因已确认

用户已完成下文第 1–3 步，SHA256 校验成功。探针于 `2026-10-23T07:38:39.655467+00:00` 返回 `status=COMPLETE`、`postgres_transaction_read_only=on`、`provider=TDengineProvider`；运行版本仍为 `pre_v3/tqpv3`（1052）。原文见 `现场探针-1052-41LIC20130-20261023.txt`，完整 COMPLETE JSON 提取为 `现场探针-1052-before-zoh.json`。

窗口为北京时间 14:00–15:00。`MODE_HF / OP_HF / PVOP_HF` 三组一致：

```json
{
  "coverage_present": true,
  "raw_valid_rate": 1.0,
  "zoh_valid_rate": 0.036101083032490974,
  "time_coverage": 1.0,
  "actual_pipeline_confidence": "E"
}
```

OP 原文为 `coverage_slots={unknown:3471, observed:12, held:118}`、`unknown_reason_slots={held_too_long:3471}`、`raw_valid_count=3601`、`zoh_valid_count=130`。PV/SP/MODE 均为 3601 个有效槽、无 UNKNOWN。BASE 为 721 槽、有效率 1、可信度 A。这与已落库 R 的 `knownSeconds=130 / unknownSeconds=3471` 完全对应。

因此，本窗已确认走带 `role_coverage` 的 ZOH 分支；1052 的直接扣分原因是上游 COV 保持时长限制，探针本窗未报告 `gap`。`time_coverage` 无需放宽。窗前 HELD 初值的修复仍有本地回归依据，但不是本窗这 3471 槽的原因。

**15:42 部署状态补证：** 用户回传三个容器均 Healthy，镜像均为 `clpm-backend:full-1053`，启动时间为 `2026-10-23T07:42:14Z`；nginx 校验及重载成功；影子 `enabled=false / active=null`。随后旧宿主机目录缺少 `scripts/verify-prod-deploy.sh`，原命令块在 `set -e` 下退出 sudo shell；普通 clpm 用户执行探针时 Docker 与证据文件写入均被拒绝，**该次 after-zoh 探针未执行成功**。恢复步骤是单独 `sudo -s`，确认 root 后重新运行第 5 步，不重建容器。

### 15:51 同窗复测：1053 预处理修复已生效

重新进入 root 后探针返回 `status=COMPLETE`、`preprocess_version=pre_v4`、`time_quality_policy_version=tqpv4`、`postgres_transaction_read_only=on`，生成时间为 `2026-10-23T07:51:17.764820+00:00`。原文及提取 JSON 分别为 `现场探针-1053-41LIC20130-20261023.txt`、`现场探针-1053-after-zoh.json`。

| 同窗指标 | 1052 | 1053 |
|---|---|---|
| OP observed / held / unknown 槽 | 12 / 118 / 3471 | 12 / 3589 / 0 |
| OP 有效槽 | 130 / 3601 | 3601 / 3601 |
| MODE_HF / OP_HF / PVOP_HF ZOH 有效率 | 0.036101083032490974 | 1.0 |
| 上述三组可信度 | E | A |
| 上述三组 time_coverage | 1.0 | 1.0 |

三组 `unknown_reason_slots` 均为空，`held_too_long` 消失；BASE 仍为 1/A。QUALITY_HF 的单组 E 为前述质量伪角色口径，不参与本次 R 阻断原因。

探针列出的快照仍是 `record_created_at=2026-10-23T07:10:43.923219+00:00`、`data_policy_version=pre_v3` 的旧记录，早于 1053 启动。只读复测不写快照，旧 `score=NULL` 不能用来否定本次预处理结果。

### 15:55 正式回算落库：该回路该窗口验收通过

用户随后回传同窗查询，原文保存为 `现场快照-1053-41LIC20130-20261023.txt`，完整 JSON 为 `现场快照-1053-after-snapshots.json`。关键原值：

```text
ts_start=2026-10-23T06:00:00+00:00
ts_end=2026-10-23T07:00:00+00:00
record_created_at=2026-10-23T07:55:00.503146+00:00
source=BACKFILL
result_record_id=0bc8acfa-a4b8-408a-9446-3de742f9471b
data_policy_version=pre_v4
status=SUCCESS
valid_rate=1.0
confidence_level=A
score=0.01
```

写入晚于 1053 启动 `07:42:14Z`，同一 hourly 快照已关联新的结果记录。R 原值为 `knownSeconds=3601 / unknownSeconds=0 / coverageRatio=1 / numeratorSeconds=294 / value=8.16 / confidenceLevel=A`，自动模式比例为 100%。

评分可对账：准确率 0、快速率 0.4、平稳率 0，权重分别为 0.375/0.25/0.375，基础分 0.1；再乘有效自控率 8.16%，得到 0.00816，按两位小数落库为 0.01。可信度 A 表示数据有效性，不表示控制性能优良。

本回路该窗口的“ZOH 保持段被扣成未知 → E → 空分”已闭环。第 4A 节剩余通用巡检、其他回路/窗口及页面显示尚无本轮验收证据；不将本例推广为全平台验收通过。

## 代码定位与修复范围

仅覆盖以下运行文件，完整差异见同目录 `fix-1053-cov-zoh.patch`：

| 文件 / 函数 | 1052 问题 | 1053 行为 |
|---|---|---|
| `backend/app/services/data_source/logical_wide_builder.py / build_logical_wide` | 数值已经 ZOH，但 PV/OP 保持超过 60 秒就标 UNKNOWN；其他角色也受 7200 秒上限影响 | 全点表按用户确认的 COV 语义保持；不以变化频率推断断流；真实 gap、无初值、无绑定仍 UNKNOWN |
| 同上，gap 查询及逐槽判断 | 仅看当前窗口、当前槽是否在 gap，gap 后可能自动恢复旧状态 | 从候选初值追溯 gap；跨 gap 的旧值保持 UNKNOWN，直到本点新事件提供恢复依据；数值仍保留供审计 |
| `backend/app/services/preprocessing/pipeline.py / _zoh_validity` | `last_valid=False` 丢窗前 HELD 初值；再次保持布尔可能覆盖当前坏质量/异常 | 对 builder 已展开的当前槽取 `validity AND known`，不跨槽保持布尔 |
| `pipeline.py / process` | 缺少 raw/ZOH 对比 | `[Pipeline][ZOH]` 记录窗口、角色、raw/ZOH/time_coverage/effective/conf 与 unknown 原因 |
| `pipeline.py / PREPROCESS_VERSION` 与 `metric_calculator/time_axis.py / TIME_QUALITY_POLICY_VERSION` | 1052 未升预处理版本，旧 L1/L2 可命中 | `pre_v4 / tqpv4`，新键避开旧块，无需清 Redis |

**R14-2 的 `time_coverage` 乘法保留。** 本地反例和本次现场完整网格结合源码均指向该因子为 1；删除它不能修复 coverage/ZOH 阶段扣除的时段，legacy 稀疏输入仍需原保护。

本批遵循现行主要针对 PV 的质量码口径；没有改好值率分母或新增全角色质量策略。缺少 gap 后重获状态依据时仍可能无分，不能用旧状态补成正常。已有锚点只按其现有来源时间参与判定，未新增“旧 sourceTime 恢复快照”的证明模型。

## 本地验证

- 原代码新增回归：7 failed / 2 passed；追加 gap 边界：3 failed / 1 passed。
- 修复后预处理、KPI、缓存等定向回归：**570 passed**。
- 最终后端全量：`7397 passed, 335 skipped, 205 deselected, 12 xfailed, 4 xpassed, 382 warnings in 198.26s`，退出码 0；跳过项不计现场验收。原始输出见 `验证日志-1053-pytest.txt`。
- `ruff check .` 与 `ruff format --check .` 通过。
- 受保护清单仅更新本次授权变更的 `pipeline.py` 和 `time_axis.py` 哈希；旧 SP/MODE 硬门禁测试按已上线的 `(pv,op)` 口径同步。
- 本地可控数据：PV 每秒、OP 每 20 分钟变化，无真实 gap，3601 槽从 `known=184 / valid_rate=0.0510969 / E` 变为 `known=3601 / valid_rate=1 / A`。此项是回归证据，**不是现场新评分**。
- 用户已完成 Linux 镜像构建、三个服务更新、同窗只读预处理复测及正式回算落库验收（该回路该窗口）。

## 交付包

- `clpm-src-1053.tar.gz`：约 39 KiB，仅三个运行源码文件、一个只读探针、固定基底 Dockerfile、镜像覆盖文件与 manifest/diff。
- SHA256：`b7db530555ec7773dbd2e9c1d70cbd50217d0505dfe8197c4b5dfe6bbf44b661`
- Dockerfile 字面量：`FROM clpm-backend:full-1052`。没有 `sort | tail -1`，没有自动选基底。
- 复用 1052 的依赖、其他后端模块与前端；没有数据库迁移。
- 下面新增独立镜像覆盖文件，原 `docker-compose.prod.yml`、`.env.prod`、`sys_config` 不改。覆盖项只有三个服务的镜像和版本标识。影子配置继续沿用现有 `docker-compose.shadow-1046.yml`。

## 1. Windows CMD：上传、登录

先经 ToDesk 文件传输将压缩包及 `.sha256` 放到 Windows 的 `D:\CLPM\`。以下命令每次一条：

```bat
scp D:\CLPM\clpm-src-1053.tar.gz D:\CLPM\clpm-src-1053.tar.gz.sha256 clpm@192.168.60.132:/tmp/
```

```bat
ssh clpm@192.168.60.132
```

## 2. Linux：sudo 单独执行

```bash
sudo -s
```

等待密码完成、出现 `root@clpm`，再粘贴后续整块。不要把 sudo 和其他命令同时粘贴。

## 3. Linux root：校验、解包、1052 部署前取证（整块）

新目录必须不存在；存在则停止，避免覆盖前次证据。只读探针不提交任务、不写评分、不读写 Redis，PG 连接设为只读，计算只查本地 TDengine。

```bash
bash -euo pipefail <<'CLPM_1053_STEP'
cd /tmp
sha256sum -c clpm-src-1053.tar.gz.sha256
test ! -e /opt/clpm-src-1053
mkdir /opt/clpm-src-1053
tar -xzf /tmp/clpm-src-1053.tar.gz -C /opt/clpm-src-1053
docker inspect --format '{{.Name}} {{.Config.Image}} {{.State.StartedAt}}' clpm-backend clpm-celery-worker clpm-celery-beat | tee /opt/clpm-src-1053/before-containers.log
docker logs --since 2026-10-23T06:25:20Z --timestamps clpm-celery-worker > /opt/clpm-src-1053/before-worker.log 2>&1
docker exec -i clpm-celery-worker python - --tag 41LIC20130_PIDA --snapshots-only < /opt/clpm-src-1053/scripts/verify_cov_zoh.py | tee /opt/clpm-src-1053/before-snapshots.json
docker exec -i clpm-celery-worker python - --tag 41LIC20130_PIDA --start 2026-10-23T14:00:00+08:00 --end 2026-10-23T15:00:00+08:00 < /opt/clpm-src-1053/scripts/verify_cov_zoh.py | tee /opt/clpm-src-1053/before-zoh.json
CLPM_1053_STEP
```

先核对 `before-zoh.json`：`coverage_present`、`requested_roles`、每角色 `unknown_reason_slots`、`raw_valid_rate / zoh_valid_rate / time_coverage`。`held_too_long` 证实年龄阈值路径；`gap` 需要真实恢复状态，不能解除保护。`coverage_present=false` 才是缺覆盖上下文分支。探针失败会返回 `PROBE_FAILED` 和阶段，不算 PASS。

## 4. Linux root：构建与更新三个后端进程（整块）

沿用当前部署目录。若目录或影子覆盖文件与现场不同，先停止核对，不自动另选配置。

```bash
bash -euo pipefail <<'CLPM_1053_STEP'
docker image inspect clpm-backend:full-1052 --format '{{.Id}}' | tee /opt/clpm-src-1053/base-image-id.log
cd /opt/clpm-src-1053
docker build --pull=false -f Dockerfile.backend --build-arg SOURCE_REVISION=3f6dee798-plus-1053 -t clpm-backend:full-1053 .
cd /opt/clpm-delivery-20261005-full
test -f docker-compose.prod.yml
test -f docker-compose.shadow-1046.yml
docker exec clpm-backend python -m scripts.ten_minute_shadow status
docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.shadow-1046.yml -f /opt/clpm-src-1053/compose.image-1053.yml --profile tdengine up -d --no-deps --force-recreate --wait --wait-timeout 180 backend celery-worker celery-beat
docker exec clpm-frontend nginx -t
docker exec clpm-frontend nginx -s reload
docker inspect --format '{{.Name}} {{.Config.Image}} {{.State.StartedAt}}' clpm-backend clpm-celery-worker clpm-celery-beat | tee /opt/clpm-src-1053/after-containers.log
docker exec clpm-backend python -m scripts.ten_minute_shadow status
CLPM_1053_STEP
```

前后影子状态均须为 `enabled=false`；本批不运行任何 enable 命令。`--wait` 确认健康后才重载 nginx，使前端上游重新解析后端地址。若本机 Compose 不支持 `--wait`，不要删掉健康验收就宣称完成。

现场旧交付目录可能没有 `scripts/verify-prod-deploy.sh`，1053 源码覆盖包也不包含该宿主机脚本。不要直接调用不存在的相对路径；改用下列第 4A 节的只读命令完成其检查范围。各整块命令使用子 shell，失败会停止该块并返回非零，但不会退出当前 root shell。

后续使用 Compose 管理这三个服务时，应继续携带 `/opt/clpm-src-1053/compose.image-1053.yml`；省略它会按原文件重新选择旧镜像。

## 4A. Linux root：通用巡检（每次一条）

这些是实际状态和 SQL 原值检查，不生成汇总 PASS；命令失败保留原文，不能按空结果处理。已回传的容器版本、影子关闭证据可直接使用，无需重复重建或重启。

```bash
docker inspect --format '{{.Name}} {{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}NO_HEALTHCHECK{{end}}' clpm-backend clpm-celery-worker clpm-celery-beat clpm-frontend clpm-postgres clpm-redis clpm-tdengine
```

```bash
docker exec clpm-backend curl -fsS --max-time 10 http://127.0.0.1:7101/health
```

```bash
docker exec clpm-backend printenv APP_VERSION
```

```bash
docker exec clpm-postgres psql -X -v ON_ERROR_STOP=1 -U clpm -d clpm -P pager=off -c "SELECT COUNT(*) AS total,COUNT(*) FILTER (WHERE is_active) AS active,COUNT(*) FILTER (WHERE NOT is_active) AS disabled FROM loop_ledger; SELECT COUNT(DISTINCT k.loop_id) AS active_evaluation_loops_with_snapshot_24h FROM kpi_snapshot_hourly k JOIN loop_ledger l ON l.id=k.loop_id WHERE k.ts_start >= now()-interval '24 hours' AND l.is_active AND l.include_in_evaluation; SELECT COUNT(*) FILTER (WHERE score IS NOT NULL) AS scored_rows,COUNT(*) AS total_rows FROM workbench_window_summary WHERE scope_type='UNIT' AND window='24h';"
```

```bash
docker exec clpm-postgres psql -X -v ON_ERROR_STOP=1 -U clpm -d clpm -P pager=off -c "SELECT width_bucket(auto_mode_rate,0,100,10) AS bucket,COUNT(*) FROM (SELECT DISTINCT ON (loop_id) loop_id,auto_mode_rate FROM kpi_snapshot_hourly WHERE ts_start >= now()-interval '1 day' AND auto_mode_rate IS NOT NULL ORDER BY loop_id,ts_start DESC) t GROUP BY 1 ORDER BY 1;"
```

```bash
docker exec clpm-frontend curl -sS -o /dev/null -w 'auth_login HTTP %{http_code}\n' --max-time 10 http://127.0.0.1:7141/api/v1/auth/login
```

```bash
docker exec clpm-frontend curl -sS -o /dev/null -w 'system_modules HTTP %{http_code}\n' --max-time 10 http://127.0.0.1:7141/api/v1/system/modules
```

对账：三个计算服务应为 full-1053；影子仍为 `enabled=false / active=null`；所有预期容器正常运行；health 返回实际健康信息；台账按现场 `1209/961/248` 核对；快照和 UNIT 有分行数按 SQL 实数报告。API 的 200/401/405 仅证明请求到达应用，不能替代登录或业务验收。UNIT 无分、HTTP 503 或命令错误都要明确保留，不能计为通过。

## 5. Linux root：同窗只读对比（整块）

```bash
bash -euo pipefail <<'CLPM_1053_STEP'
docker exec clpm-celery-worker python -m scripts.verify_cov_zoh --tag 41LIC20130_PIDA --start 2026-10-23T14:00:00+08:00 --end 2026-10-23T15:00:00+08:00 | tee /opt/clpm-src-1053/after-zoh.json
CLPM_1053_STEP
```

核对版本 `pre_v4/tqpv4`、`postgres_transaction_read_only=on`，对比同一窗口各组数值。`fresh_preprocessing` 只是绕过缓存的只读复算，不能当新落库评分。

## 6. 页面回算后：核对新快照与 worker 原文

工作台选中 `41LIC20130_PIDA` →「评估」→「发起评估」→「整点回算（覆盖整点评估，参与聚合）」→ 北京时间 `2026-10-23 14:00–15:00` →「开始回算」，记录任务 ID。该入口固定当前回路，默认 `skipExisting=false`，覆盖 hourly 并绕过缓存。若从评估任务页发起，保持「补差模式」未勾，预览必须为 1 回路 × 1 小时窗口。

「任意时段」写 custom，不覆盖旧 hourly。任务执行完成后，核对同窗新记录：

```bash
if bash -o pipefail -c 'docker exec clpm-celery-worker python -m scripts.verify_cov_zoh --tag 41LIC20130_PIDA --start 2026-10-23T14:00:00+08:00 --end 2026-10-23T15:00:00+08:00 --snapshots-only | tee /opt/clpm-src-1053/after-snapshots.json'; then
  printf 'Snapshot query completed.\n'
else
  CLPM_PROBE_RC=$?
  printf 'Snapshot query failed: exit=%s\n' "$CLPM_PROBE_RC"
fi
```

```bash
docker logs --since 30m --timestamps clpm-celery-worker > /opt/clpm-src-1053/after-worker.log 2>&1
```

hourly 的落库时间取 `calculation_result_record.created_at`，该表本身没有 `created_at`。自定义评估写 `kpi_snapshot_custom`，探针同时查询两表；不要仅看 hourly 判断按钮没有执行。

通过条件：新 `record_created_at` 晚于 1053 启动、窗口和任务匹配，`valid_rate/confidence_level/score` 有原文证据；若仍 E，检查 R 与其他评分输入的 `metricsDetail/metricsSemantic`，按实际 gap、质量或业务条件解释。真实低分可以是 0；不能为了“有数据”强制给分。

## 7. 需要回退时（Linux root，整块）

只生成独立的 1052 镜像覆盖文件，不覆盖配置或数据。新快照不会随镜像回退自动撤销，需按实际任务和记录区分。

```bash
bash -euo pipefail <<'CLPM_1053_STEP'
sed 's/full-1053/full-1052/g; s/v7.5.6-1053-full/v7.5.6-1052-full/g' /opt/clpm-src-1053/compose.image-1053.yml > /opt/clpm-src-1053/compose.rollback-1052.yml
cd /opt/clpm-delivery-20261005-full
docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.shadow-1046.yml -f /opt/clpm-src-1053/compose.rollback-1052.yml --profile tdengine up -d --no-deps --force-recreate --wait --wait-timeout 180 backend celery-worker celery-beat
docker exec clpm-frontend nginx -t
docker exec clpm-frontend nginx -s reload
docker exec clpm-backend python -m scripts.ten_minute_shadow status
CLPM_1053_STEP
```
