# Critical CI 输入采证器移交

作者实现与离线自验完成，候选源码冻结；真实远端 PG 采证和独立数值验收仍待新 CI，本文不签整体 F03 通过。

本次只改候选树的五个文件，基线 `c6fa2b489af66da392ea72fa387629f1607ba613`。Backend 数值诊断、产品源码、原四业务用例与 gate 命令未变。原 RID39041829350 缺失的 PG 原件不补造，不以本机数值结果代替远端证据。

## 冻结身份

精确字节与全部 SHA 见 `source-manifest.json`，全文副本在 `frozen-source/`。

- collector: `879b46ff4736a433cf37e2ac40a85ed30491f5b302187dfc5e98c36cbe27b295`
- test: `71fa0cedb7f87b41570b05b0147db3696f0320472d8394c621049fdf975a247d`
- workflow: `674f03f52ad884e6ac686e7a551b512efd48f33ddd4e5f4fae4470c61dbc0167`
- policy SQL: `d20262d5e0b4d9e47ed21f76e60ad998bd993e2b4b659e96f93f2a3945c1d294`
- dataset-row SQL: `9adea1d352a35dbf1c44e25c9f07a06ce166dc5fe30c15e0247f15b09f42dcff`

建议两逻辑提交：`01-collector-and-sql.patch` 新增 450 行；`02-test-and-workflow.patch` 新增 325 行。仅 Root 正常提交与推送。未绕过 hook，作者未提交/推送。

## 行为与原始证据

`capture-critical-input-evidence.py` 的 scope 仅允许 disposable-github-ci、真实 GITHUB_SHA/HEAD/fixture SHA 一致、专属 PG/Redis/TD loopback 身份与合成 INPUT 标记；拒绝本机/容量/共享库。源码 tracked 且整个 tracked tree 必须 clean。SQL 的逐字节原件与冻结 SHA 一致，实际 psql 子进程只接最小环境；口令仅 `PGPASSWORD` 子环境，不在 argv/日志列出。

before 在 Prepare inputs 后、原 gate 前执行固定 RR READ ONLY UTC SQL。原 SQL 导出实际 PG loop、release、参数、配置、权重及 held 缺键事实；不拿 manifest 的 effectiveParams 冒充 PG 配置。事务身份、原 stdout/stderr/SQL/真实 rc 与时间保留。

after 以 `if: always()` 在原 gate 后执行同 SQL；仅从 INPUT 表按 fixture loop/window/kind 唯一查包，再按原 dataset-row SQL 真读行。0 行、多行、错 scope、行/包/hash/build/ref/size 不符都非零，失败原件仍保留。NPZ/manifest 的完整性按现有 compute_input_hash 复核；不读 KPI 结果表，也不据结果调整预期。before 与 after 是各自独立只读事务，比较非事务字段语义；不声称跨阶段同一 snapshot。

所有新采证步骤保持阻塞，未引入 continue-on-error。原 gate exit、四 ID/重试/分母、Backend 与 uploader 语义保持；现有 always uploader 包含整个 `$RUNNER_TEMP/critical-e2e`，因此采证失败的 launch stdout/stderr/exit-code/header 也上传。

## 作者离线验证

每个目录有真实命令 argv/cwd/rc/stdout/stderr/SHA；纯控不连接真实数据库，不启动服务，不投递任务。

| 原始目录 | 结果 | 范围 |
|---|---|---|
| `unit-precision-final/` | rc=0，9 个 test methods（含 subtests）全通过 | scope、真实 PG 身份、0/多包、错 loop/window/kind、真实子进程失败捕获、实际自有 NPZ/manifest/hash 绑定、精度差异 |
| `lint-precision-final/` | rc=0 | Ruff check |
| `format-check-precision-final/` | rc=0 | Ruff format --check |
| `actionlint/` | rc=0 | 实际 actionlint；shellcheck/pyflakes 集成未启用，另做下面真实 Bash 控制 |
| `workflow-shell-check/` | rc=0 | 扣除新增两 env/两 step 后完整 YAML 语义等于原件；5 个真实 Bash 成功/失败 rc 控制 |
| `precision-original-red/` | rc=1 | 原 30f0 collector 默认 float 把两个不同高精度 PG 数值误比相等 |
| `precision-new-green/` | rc=0 | 同未改 probe，Decimal 解析保留差异 |

高精度修复仅采证 JSON 比较，未改产品算法、输入生成或数值门槛。原 30f0 完整字节保留在 `original-30f0-capture.py`。早期 lint/format 失败原件均留档；最终 check 已通过。

`workflow-and-shell-result.json` 列完整五种 Bash 对照：before unit=1 → step 1；before capture=3 → step 3；after capture=2 → step 2；前后成功均 0。实际 shell 使用自有 fake python，仅证明 shell 失败传递，不是业务验收。

## 后续独立与真实验收

F04 需以冻结 SHA 独立纯控；本作者不签非作者验收。Root 独立通过后正常提交并推送，由新 PR run 在实际 checkout 上采真实 before/after/dataset row，取得全部三 job 终态、四业务原件、完整 NPZ/manifest 与 SQL。F04 按先封输入预期后读 actual 的协议做远端数值复算。任何原 gate 或采证失败都保留原始失败，不以摘要、重跑或本机 PASS 替代。

四项用户豁免和生产环境未改变。作者未启停 API/Worker/Beat，未接真实 DB/TD/Redis、未提交/推送/触发 GitHub run。当前额度现场查询 remaining=18%，低于 15% 即停止。
