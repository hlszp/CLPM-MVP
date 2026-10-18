# F03 真实 CI 失败与本轮最小补丁交接

作者自验通过，未签独立验收；新 Linux CI、两条 smoke 执行、四条真实业务 case 与独立数值复算仍待执行。

## 原始远端事实

- [run 39027107897](https://github.com/hlszp/CLPM-MVP/actions/runs/39027107897)，pull_request，head `148dd21ac8450cd85d763f89a557adf68fd29fdb`，两 job 实际 checkout merge `21b13b246b613d3fadd4fadefc846d30f1bf8562`。最新 `20261017T110736035204Z-run.json` / `-jobs.json` / `-artifacts.json` 原件与 GET 命令/rc 在本目录。
- Backend job `117380159482` failure：`job-logs/117380159482.log:3294` 为原 `test_core_loops_identification_accuracy` 30s timeout，3384 为真实退出 1；stack 3366–3383 指向 uncertainty/ARX 小矩阵方差循环。未跑完全量，不能声明完整后端分母通过。既有 Mypy 2256 errors 235 files 为原先非阻塞步骤（2843），不是本次 pytest 失败原因。
- Frontend job `117380159591` failure：`job-logs/117380159591.log:3160` 原 app config 缺 `@playwright/test`，3175 真实退出 1；此前 1021 unit、type/build 通过。关键业务 job `117380652905` 被旧 needs skip；artifacts 数 0，没有实际输入包可交数值验收。
- 完整日志 ZIP `complete-run-logs.zip` 的 GET rc=0，SHA-256 `64a43efdad8750060965d3af26eb1110033836b5eec431052a976d86f4e2f608`。单 job 原始日志独立保留。

## 源码变更及固定身份

冻结时间 `2026-10-17T11:15:33.072007+00:00`，基于 `148dd21ac8450cd85d763f89a557adf68fd29fdb`；精确字节/SHA 见 `supplement-source-manifest.json`，完整三文件 diff 在 `supplement.patch`。

1. `.github/workflows/ci.yml`：Backend/critical 固定 OPENBLAS/OMP/MKL/NUMEXPR/VECLIB 五个线程变量为 1，输出平台、NumPy 构建、CPU/affinity、实际变量；critical 另存 `numeric-runtime.json` 至原 artifact 根。移除仅编排用 `needs: backend-ci`，各 job 自有 PG/Redis/TD 前置；三 job 仍阻塞，critical/frontend 所有步骤均阻塞。Backend 原有唯一 Mypy 非阻塞例外未改，不隐去、不扩大；后端 pytest 失败仍令 workflow failure，禁止 merge。
2. `frontend/apps/web-antd/package.json`：增加直接 devDependency `@playwright/test: catalog:`。
3. `frontend/pnpm-lock.yaml`：仅 app importer 增三行，重用已经存在的 resolution `1.60.0`；catalog、其它 lock 内容均未变。无无锁安装、无升级、无应用源码更改。

算法、原 30s timeout、coverage >=60、原两 smoke 内容/配置、critical 四 case/分母均未变。可按线程/编排 workflow 一组与依赖 package+lock 一组正常分组提交；提交/推送只由 root 执行。

## 真实本机证据

- `frontend-smoke-dependency/before-collect.*` 原样缺包 rc=1；首次 `pnpm add catalog --offline --lockfile-only` 因本机离线 metadata 缺失 rc=1，`declare-locked-dependency.*` 全保留且当时未改 package/lock。之后补正规 catalog/importer 声明，`pnpm install --offline --frozen-lockfile --ignore-scripts` rc=0，原文件 SHA 前后相同。
- `after-collect.*` / `ci-collect.*`：Playwright 1.60.0 加载原 config、列出原 2 case，rc=0。注意 `--list` JSON 的 skipped=2 是仅收集语义，两 smoke 均未执行，不作为业务 PASS。
- `unit.*`：106 test files / 1021 tests passed，rc=0；`type.*`：全 workspace 2 targets successful，rc=0。应用 src 完整 SHA 映射前后相同。
- `resource-profile/default.*` vs `one-thread.*`：同一当前准确性单例带 coverage，真实 rc 都 0，28.75s vs 28.47s；22 行数值/核心 16/18=88.9% 完全相同，测试/输入/算法四文件 SHA 前后相同。本机是 Apple Accelerate，此差值很小，不能证明远端 Linux OpenBLAS 根因，也不能证明 Linux 超时已解。
- `resource-profile/actionlint-after.*` rc=0（官方 actionlint 1.7.12，之前已校验官方 archive checksum；本次禁用外部 shellcheck/pyflakes）。真实执行两个 YAML 数值诊断 here-doc 均 rc=0，变量实际为 1，critical 文件落点正确。纯诊断不连接业务库。
- `verify-supplement.py` 的原始 stdout/stderr/rc 在 `verify-supplement.*`，全部 31 条检查 true，`supplement-check.json`。首次临时核验把既有 Mypy 步骤名误写为另一字符串，误报 false；原 JSON `resource-profile/comparison.initial-verifier-false-name.json` 保留，最终改为对照 HEAD 的实际步骤名重验，未为此改产品/CI。

## 下一步门槛

root 正常提交、推送并等待三 job 全部真实执行；Backend 全量/coverage、Frontend 原 2 smoke、critical 原 4 case 必须各自满足原门槛。若任何失败，保存原始日志/checkout/rc，修真正缺陷，不能 skip、放宽分母/断言/timeout。关键业务成功后把固定 inputs +有效 profile/config + calc snapshot manifest/NPZ 交 F04 非作者独立预期/公式复算；作者不代签。未启停任何共享服务、未投共享任务、未写共享 DB、未 commit/push/触发 GitHub run。
