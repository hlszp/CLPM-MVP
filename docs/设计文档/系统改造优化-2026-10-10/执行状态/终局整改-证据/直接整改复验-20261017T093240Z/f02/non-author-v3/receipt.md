# F02 v3 测试接续非作者复核

**判定：PASS，仅针对 `test_backfill_recovery_and_skip.py` 的行为断言接续。F02 真实完整容量仍 BLOCKED，不改变 v2 非作者回执的历史6+3和容量边界。**

对象为 `/Users/zhangping/DEV/clpm-remediation-20261017`，按作者 v3 `f02-source-identity.json` 冻结的八文件 SHA-256；本复验没有编写该测试更新，也未修改任何候选源码。`source-before.json` / `source-after.json` 确认 **8/8匹配冻结身份且前后完全相同**；其中 v2 已验七文件全部原样，v3仅增加测试文件接续。

审阅 `test-update.diff`：原两条断言检查旧闭包源码是否包含 `"too many clients" in str(exc).lower()` 和 `attempt < 2`。识别逻辑已经提取为 `_is_resource_exhausted`，旧结构字符串缺失不能证明行为回归。新测试直接执行真实识别器，断言 PG 原始/包裹/大小写签名命中，以及配置/未知网络错误不误判；没有用伪实现返回值或源码字符串迎合通过。

三次尝试上限没有因移除此处字符串检查而失去验证：v2非作者已实跑的26边界和额外5例包含三次尝试、2s/4s、session收束、进度只一次、失败终态/父任务门槛；这些生产实现及对应测试的七文件哈希在v3全部未变。

| 独立执行 | 实测 | 原始证据 |
|---|---|---|
| `pytest -q tests/test_backfill_recovery_and_skip.py -p no:cacheprovider` | **10 passed，0 failed，进程rc=0** | `backfill-targeted.command.json`、`.stdout.log`、`.stderr.log`、`.junit.xml` |
| 另用实际 asyncpg/SQLAlchemy 错误类型构造七个签名反例 | **7/7，rc=0** | `pg-signature-probe.py`、`pg-signature-results.json`、`typed-pg-signatures.command.json`、stdout/stderr |

附加探针中：`asyncpg.TooManyConnectionsError` 原始对象、SQLAlchemy `OperationalError` 包裹该对象、大写 PG 消息均命中；普通 `TimeoutError`、PG 启动暂不可连接、SQL语法错误、业务配置错误均不命中。探针构造错误对象，不连接真实PG，不冒称生产故障注入。保留原 pytest FastAPI 弃用 warning，不影响真实rc=0。

全部运行 `CLPM_TEST_MODE=1 / CLPM_SKIP_EXIT_HOOKS=1`，PG/Redis指向127.0.0.1:1、移除已知DSN环境覆盖；没有读取业务.env、数据库/TD请求、服务/Worker/Beat启动、提交/合并/推送/部署。全量v3由主协调另行运行，本回执不代签全量或容量。
