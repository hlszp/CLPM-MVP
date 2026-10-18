# F03 权限契约与本地隔离准备件：非作者复核

结论：**局部 PASS；F03 四项真实业务验收仍 BLOCKED**。本回执只认可冻结的权限测试契约修正与隔离准备件，不代表新版本的 AUTH/PERM/CALC/EVIDENCE 已运行通过。

输入：作者 `f03/permission-local-remediation/source-manifest.json`，SHA-256 `2896bd00f595214e581bcae4d85183eece6202cd2ba5d426eea13103deecd23f`。三个候选源在独立检查前后字节一致：

| 源 | SHA-256 |
|---|---|
| `e2e/tests/critical-business.spec.ts` | `d8aa48efd47f95ed1d7e1c8c895a3347b5e33f578f0f7a97eb2fca8c330b486c` |
| `e2e/scripts/prepare-critical-fixture.py` | `f8084521ceeb216d776cd8b6bdf42e98b788aadf084815463f7c8d705bc2b12b` |
| `e2e/scripts/test_critical_fixture.py` | `796fc3f4bbac526738f55048a9adb743b6e0453fc3ca4cfff10c13277fff47fb` |

## 独立核对

1. 直接从 run `39031454818` 的原始 trace.zip 恢复 SPONSOR `/auth/me` 与 POST `/tasks/custom/evaluate` 的原始响应体，验证真实身份/缺页与动作权限、HTTP 403、`ERR_PERMISSION_DENIED`、data=null；未凭作者口述。原响应体分别 SHA-256 `f42759034148af7549e208e2f15e8e8f74e738c9f20e9ef0ab741db60ddd2463`、`435615dcf4b3d7d32bbe58b37488c53e1254554302d125befa4bbecfdab81fa7`。见 `trace-permission-crosscheck.json` 及两份 `.raw.json`。
2. 六份实际产品路由/权限契约源与作者保全件逐字节一致。页面权限过滤同步移除路由与 known patterns，直链 404 是现行产品契约。新 PERM 同时要求真实 SPONSOR 权限、精确 API 拒绝、精确目标 path/query、两段 404 文案及 adapter 不存在；另以独立 ADMIN context 在同目标验证真实 adapter 可见且无错误状态。没有改产品权限。
3. AST/语句级核实 AUTH、CALC、EVIDENCE、全部 auth/route helpers 与先前冻结源不变，四个 case ID/serial/retry 契约不变；实际输入生成与写入调用未改。见 `spec-ast-contract.*`、`input-numerical-calls-unchanged.json`。
4. 自编隔离反例 29/29 passed、rc=0：逐一拒绝共享命名空间、错端口/DB、远程/错误 API、缺失或不等于实际 HEAD 的 40 位 SHA、GHA 冒充 local 等。只运行合法 profile 的只读计划与非法 `--execute` 的前门拒绝；没有执行合法写入器、接业务服务或投任务。
5. 独立重跑作者 12 个 guards（含 30 个 subtests）rc=0，E2E typecheck rc=0，gate-contract 8/8 rc=0，收集恰为四个业务 case。`--list` 的 skipped=4 是未执行的收集输出，不是四项业务 skip，也不计业务 PASS。原始命令、stdout/stderr、退出码均在本目录。

## 边界

旧远端 run `39031454818` 保留 AUTH passed、PERM failed、CALC/EVIDENCE 未执行及 NPZ=0 的失败事实。新三源需要正常提交后在诚实的实际 SHA、专属真实输入与完整服务上重新运行四项；指标还须依冻结输入由独立 oracle 复算。Backend/Frontend 通过或本回执的局部 PASS 不能代替四项业务门。四项用户豁免、原 20×1000/600s 容量合同、原 30s 与 coverage 60% 均未改变。

复核人：`/root/f04_restore`，非 F03 作者；本轮未修改任何候选源、启动产品服务、写业务数据库或投任务。
