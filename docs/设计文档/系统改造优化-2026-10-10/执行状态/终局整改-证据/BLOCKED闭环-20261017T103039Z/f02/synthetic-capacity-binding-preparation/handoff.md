# S01 合成历史绑定工具移交（作者，实际尚未执行）

当前正式仍0/20，实际S01绑定未由本Agent更新。本轮只在E写工具/离线数据副本；无数据库、Redis、TD连接，无候选产品/CI修改、任务投递、服务操作或commit/push。四项用户豁免未改。

最终待独立门执行入口是 `redis-url-guard-v2/prepare_bindings.py`（SHA e213f27d9abc631ad3cb11233366e6f631a9fb4d88bcbf3b81cdf0d60464650b）。顶层旧e70保留为历史，不能执行它；独立发现Redis URL query覆盖DB的反证及最小2句guard增量见v2/minimal-change.diff与counterexample-old-red/new-green。原33纯控制测试完整字节不变，新增6个URL反控，v2共39passed/0failed；Ruff、format及真实native.env分离Redis字段静态guard均0。没有由这些纯测试宣称真实PG更新或容量通过。所有 *_tmp*/before.dump 均为明确标记的纯控桩，不是真实可恢复数据库备份。

## 精确输入修正

原Root RR只读证据与c452原25表覆行一致，7000 MVP_INIT历史valid_from为Oct17，而24窗为Oct15T12至Oct16T12。`offline-plan`按20正常+4附加计划实际计算earliest=2026-10-15T12:00:00Z，不改计划、原7000点/TD输入、回路参评或数据值。1000回路必须精确PERF-T0001..1000，remark/description均有synthetic与非现场真实标记，7000唯一当前映射/历史/七角色/测点名一一匹配。只UPDATE既有7000原UUID行的valid_from和basis=SYNTHETIC_FIXTURE；原created_at、loop/role/point、mapping_version=1、valid_to=NULL及其他字段不变。

模型/DDL与consumer完整源副本在consumer-contract。更新这批尚未正式执行的新合成INPUT，明确不retcon旧历史验收；新相邻段会改变7000行/UUID分母，故按已获主审批准的最小两字段方案，不改产品有历史按历史的正确查询契约。

## 执行顺序与边界

1. 先等当前F03真实数值独立验证、after容量隔离对照完成，并由唯一Keeper停尽该轮自有API/Worker/Beat/前端；本工具不替代真实停机/PID所有权证据。未满足不得执行S01写入。
2. F04在自有副本做纯工具复验；主审接受后才由Keeper执行 `keeper-apply-command.json` 中完整argv。命令目前仅准备，未执行。它使用固定v2/e213、精确S01专库loopback17102/用户clpm、专Redis17123/0，并拒绝任何URL query/fragment。OUT放冻结目录之外，现有目录即拒绝覆盖。
3. apply先重核原输入/25表文件SHA；独立RR READ ONLY导出完整before覆盖原件与12张KPI/ledger/latest/引用结果表零计数、Redis task/index/排队/unacked/Celery结果为空。之后真实全库pg_dump custom保存before.dump与rc/hash，失败不更新。
4. 单SERIALIZABLE事务锁定25配置表+12结果表，重新导出before并逐表等于原参考；再核synthetic/7k/任务空。唯一UPDATE必须精确7000行，事务内after必须严格只差两个允许字段，其他24表既有覆行等、所有结果仍零，任何差异回滚且非0。
5. 成功退出事务后立即持久记录committed=true；新PG连接独立RR READ ONLY完整after读回/所有覆行对比通过，才记persistedReadbackVerified=true。提交后断连/读回失败仍非0且committed=true；提交确认丢失记null/unknown，不伪称已回滚。原before、SQL/行数、after、各事务身份、备份和失败永留。
6. 该实际修正及非作者RO验过之后，必须用新候选H、新目录与新digest重新冻结完整配置/输入上下文；旧f565不能复用。offline-plan/old-freeze-not-valid-for-formal.json是独立新增失效指针，c452/f565原件没有改写。正式20仍需按原全量分母/SLO/终态屏障执行。

25表是原c452目录真实捕获覆盖范围，包含history；sys_config明确继承原已审method01白名单，原已登记省略19条。其他24表按其既有覆盖逐行相等，不把它冒充全库所有秘密配置原行；按主审明确裁决不扩大秘密行读取。完整pg_dump是Keeper实际执行时的独立恢复备份，和这个配置比较范围不同。代码没有TD client/API，不写TD；仍需投前保全/复核原24窗输入归档及原7000点身份，不从纯工具推断实时TD已验。

## 纯复验入口与证据

将v2的prepare_bindings.py、test_prepare_bindings.py、test_redis_url_guard.py、offline-plan复制到自己的新目录，再用runtime venv执行 pytest 两个test文件（测试禁止socket/connect和subprocess；假PG会话与假备份明确纯控）。其余原始结果在顶层offline-plan-command、pure-controls-frozen和v2/pure-controls-final、counterexample-old-red/new-green、lint-final/format-final、native-profile-static-check，每项有argv/cwd/真实rc/stdout/stderr/SHA。

负控覆盖错误专库/非合成/非1000或7000/角色或点改变/禁参评/旧代次或闭区间、结果非空/备份失败/before漂移/UPDATE6999/额外列或其他表触发变更、回滚、提交后读回断连/数据漂移、提交确认丢失、任务非空、URL query/fragment重定向；正控逐字段与其余表比较。作者不签非作者PASS或实际INPUT准备PASS。
