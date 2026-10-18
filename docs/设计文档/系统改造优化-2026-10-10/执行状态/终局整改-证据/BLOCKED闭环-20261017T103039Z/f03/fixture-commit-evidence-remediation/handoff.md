# fixture INPUT 提交事实记录整改移交

作者局部检查通过；真实业务与非作者验收保持待验。候选两源由 Root 正常提交为 `8a438372d21395fdb5246e4d8f73a0a5b7bf9c97`，本代理未提交、推送、启停服务或访问实际业务数据库。

- `prepare-critical-fixture.py`：写事务成功退出立即持久化 `committed=true`，验证标记保持 false；只读事务读回和退出都成功才置 `persistedReadbackVerified=true`。原 mismatch/断连/事务退出异常继续抛出，不伪报回滚。
- `test_critical_fixture.py`：原 17 个测试保留，writer 测试覆盖 10 个事务子场景（UPDATE/INSERT 各自成功、预提交错误、提交后读回不一致、断连、只读事务退出错误）。
- 两源 SHA/字节数见 `source-manifest.json`；业务 spec、4 case IDs、retry=0、workflow、package/lock 与 f29b 逐字节一致，函数外 AST 不变。原 e39f 证据目录未改写。

固定独立反例脚本未修改（SHA `1688289b47eb5ca86593642a98518df9edbbc3eacccde53a88e6ebe401b0d4f7`）：`01-original-independent-probe.json` 为旧 rc=1；`02-fixed-independent-probe.json` 为外部修正副本 rc=0；`13-candidate-independent-probe.json` 为实际候选 rc=0。旧/新都实际模拟写事务提交 1 次且拒绝读回不一致，区别是 artifact 不再把已提交记成 false；三组完整 JSON 在对应 probe 子目录。

`07-candidate-seventeen.*` 17/0；`08/09` Ruff/format=0；`16-candidate-type-direct-node.*` type=0；`15-candidate-collect-direct-node.*` 原 4 case 收集=0；`12` diffcheck=0。全部实际 argv/cwd/stdout/stderr/rc/hash 已在同目录保留。首次 `11-candidate-collect.*` 因 pnpm 自动依赖状态检查的 EEXIST 真实 rc=1，同批 type 实际 rc=0；此失败保留，后续使用已锁定 Playwright 1.61.1 的 Node CLI 收集通过，未改 package/lock。此处 4 是收集数，业务执行数为 0。

Root 正在独占执行新提交的正常门禁和专属 local 流程；本移交不签真实计算、NPZ 数值、整套 CI 或合入/部署通过。F04 使用其原 probe 独立复验，原始反例与 e39f 原件持续保留。四项项目用户豁免无变化。
