# G3 算法契约与 Linux 同 Job 数值诊断移交

本次增加真实 CI 原件采集，不修订辨识算法或验收预期。已有原件只能证明三条失败共同依赖 joint 胜者、以及两轮运行记录存在 SIMD 能力差异；尚不能证明 SIMD 是根因。正式 G3、F03 和 F02 容量结论不因本工具变为通过。

## 1. 身份、范围与原件

- 候选工作树：`/Users/zhangping/DEV/clpm-remediation-20261017`；诊断初版提交 `e2711c384f9104e311a29800362fa9dbbda1b7e6`。
- 实现文件：`backend/scripts/profile_identification_numerics.py`；CI 落点：`.github/workflows/ci.yml` 的 Backend `Pytest with coverage` 后。
- 初版脚本 SHA-256 `6d9ffd634fdeac37d75a7e1b4f3176ac91a53553df2840e99071aacbeca119cf`，429 行。网禁返修副本 SHA-256 `c2eba163d4af6881cf83726311015064ec017c931528ae804a72a29d29c3b858`，437 行；其实际落地/非作者结论由 `network-guard-v2/` 的后续移交记录补充，不能把副本身份当作已提交身份。
- workflow SHA-256 `575d2d19d0a9ac59d987e0df6cbb1659d6a106b91c9f1edfa3a900570d9d6622`。原 workflow、逐行差异、精确补丁、原 SHA 和静态检查原始输出均保存在本目录。
- 既有便携探针 `../g3-budget-readonly/profile_fixed_joint_ci.py`（SHA-256 `fc64277b7cedb08fa65d51e7dec96a9e65123c1a5e8920193d4f79561b23bc09`）保持原字节。旧 Mac 纯诊断的结论和 146 件原件在该目录，不冒充新脚本或真实 Linux 执行结果。
- 原契约八份全文副本及来源 SHA 在 `contract-originals/`；可复核行号与引文在 `contract-reference-excerpts.json`。

## 2. 原契约允许什么

| 原依据 | 明确约束 | 对本问题的含义 |
|---|---|---|
| `P3-06-07-oracle冻结草案-20261011.md:3,74,112,182,322,339` | R1–R19 已冻结；ARMAX 默认 `200/1e-6` 不变；结构/方法集不扩、Occam 5% 不变；ARX 基线保留；BJ/SRIVC 不引入；恢复容差冻结 | 合法配置范围不等于可在失败后调整默认值；不能改方法或容差取绿 |
| `03-阶段任务计划.md:198–208` | 升级既有流程；已知真系统正确恢复或明确拒绝；准确输入不得被无效粗估误降；方法事先固定 | 允许修真实计算缺陷，但不能另建引擎、挑最佳方法或事后筛掉失败样本 |
| `14-G3纠偏任务与执行提示词-2026-10-12.md:66–74` | R9/S1/S1-b/R15 不变；低 SNR 全路径标注；合法支持不能 blanket 拒绝凑通过 | 低 SNR 本身不是新增淘汰条件，不能以拒绝所有 joint 替代正确性 |
| `G3-APP-交接.md:22,94,99–101,124–125` | joint 胜出后的低 SNR 处理只标注、不改选模或评级；D/E 推荐门不放宽；旧缺键不伪补；未实现能力如实声明 | 不能先改聚合 SNR/快照，把 joint 未胜出的上游问题盖掉 |
| `armax.py:22–43,137–169` | `ARMAXResult` 只有迭代次数，没有 `converged`；达到预算仍返回最后迭代；实际停止量只比较 A/B 更新 | `iterations==200` 不能直接当作已收敛或未收敛；需实际 `max_diff < tol`。该量也不能称为全部 PEM 参数或全局目标收敛 |
| `test_armax_vectorized_equivalence.py:85–99` | `max_iter=1` 返回系数并与独立矩阵解逐字节对照 | 全局改成“预算耗尽即抛错”会破坏已存在的返回契约，不是等价修复 |
| `tests/refactor/protected_manifest.py:1–15` | 受保护算法变更须显式授权、解释并核 SHA | 本次不改算法、原测试、golden 或注册保护 hash |

未找到冻结契约明文规定“所有 max_iter 耗尽的候选一律抛错/淘汰”。“明确拒绝不可靠结果”的上位目标不能替代具体候选准入政策的评审和回归。

## 3. 可证伪的最小修复选择

1. **当前执行：只增加诊断。** 不改变任何返回值、选模顺序、线程 profile 或原 pytest 结果。远端原件不足时先保全输入、候选和实际停止量。本工具退出 0 只表示取证完成，不能作为原测试通过或模型合格。
2. **若首分歧证明索引/矩阵/类型/归约等真实计算错误：修该处。** 必须给相同原始输入、首个错误中间量和独立计算反证；保留原方法、预算、完整样本和 oracle，然后核全部原测试及独立对照。当前尚无这条因果证据。
3. **若证明确有未满足停止量的候选参与选模：先明确政策，再改资格。** 可以加性记录终止事实，保留原 API 返回最后迭代的行为；是否在 pipeline 中判不合格是行为变化，不能称作纯性能等价修复。任何获准实现须保留失败候选/原因、ARX 基线和已有合法支持面，不能因为低 SNR 就整类拒绝；也不能仅凭 `iterations==max_iter` 忽略最后一次恰好满足容差的情形。
4. **换估计器、放大迭代、调容差、固定挑某 CPU 或随机 rerun 取绿均无现成因果依据。** 新方法还会碰 R9；不同线性代数实现是否属于等价实现，也须用原输入、矩阵、结果与冻结 oracle 证明，不能按最终绿灯选择。

## 4. 真实 Linux 只运行一次诊断的落点

`backend-ci` 的原 `uv run pytest --cov=app --cov-report=term-missing --cov-fail-under=60` 和原 env 保持一致，只增加 step id。诊断在同 Job 的该 step 后，以 `always()` 且原 outcome 为 success/failure 时执行；它是同机器的新进程，不是假称取到了原失败测试函数的帧。

新 step 实际命令形态如下，路径由 GitHub 提供的真实绝对目录展开，不传凭证：

```bash
timeout --signal=TERM 60s "$GITHUB_WORKSPACE/backend/.venv/bin/python" \
  "$GITHUB_WORKSPACE/backend/scripts/profile_identification_numerics.py" \
  "$RUNNER_TEMP/g3-numerical-originals/observation" "$GITHUB_WORKSPACE"
```

脚本内单进程 `nice +10`，CPU soft/hard 为 45/50 秒；只读取并要求既有五项数值线程变量均为 1，不覆盖其值。它拒绝 `app.main` 导入和已列明 Python socket/DNS 审计事件，使用原测试 AST 生成固定合成输入，调用真实 `identify_from_history` 一次，不连接 PG/TD/Redis/API 或启动生命周期。

父 shell 保存真实 `$?`、开始/结束 UTC、完整 stdout/stderr 与文件哈希，再 `exit "$g3_rc"`。没有 `continue-on-error`、`|| true` 或降级成功。独立 `always()` artifact step 上传整个目录；超时/kill 的部分原件与真实 124 等非零码也保留，不能称完成取证。artifact 名为 `backend-g3-numerics-<run_id>-<run_attempt>`，保留 7 天，需及时下载封存。

Backend Job 总时限由 15 改 17 分钟，只给新增诊断/上传留 120 秒：真实红 run Backend 共 893 秒已接近原 900 秒，若不留空间可能来不及保存原件。此处不改变原单例 30 秒、ARMAX `200/1e-6`、辨识候选预算或 F02 600 秒门槛。

## 5. 采集内容和明确边界

- `environment.json`：真实 `GITHUB_SHA`、Git HEAD/root/backend tree 原命令/rc/输出、Python、NumPy/SciPy/BLAS、CPU 特性与 affinity、白名单线程变量。GitHub SHA 与实际 HEAD 不符即拒绝；不复制完整环境、令牌或业务配置。
- `source/`、`source-identity.json`、前后源身份：实际辨识包全文、探针、原固定测试、依赖锁和保护 manifest；用唯一选模条件 AST 找真实行号，并核导入 callable/source/字节码对应，不固定假设第 1655 行。
- `op.npy` / `pv.npy` 和 `input-identity.json`：原生成器源码/AST、NOISE/SEED、完整 NPY、实际原值字节 SHA。种子相同不替代输入字节比对。
- `selection-events.json`：逐窗结果、joint 候选、single/joint 最佳模型、排序依据与 joint audit 的实际对象全文。
- `armax-XX-u.npy` / `armax-XX-y.npy` 与 `armax-return-observations.json`：实际各次返回的训练数组、结构/时滞/预算、`iterationsDone`、`lastMaxDiff`、真实停止量判定和完整 A/B/C 数值数组。数组使用 `tolist()`，不以 `repr`、省略号或 `default=str` 替代原值。
- full/public result、cProfile、debug log、真实 command/rc、诊断身份与相对文件 SHA/size 清单。非有限值用有类型的显式对象记录，不伪造成有限数值。

本工具不是每个最小二乘矩阵/每一轮系数的轨迹，也不复刻原失败 pytest 进程的全局状态。若新进程通过而全量原测试失败，应继续查测试顺序/全局状态；不能据此否认失败或认定 SIMD 是原因。外层 `ci-sha256.txt` 的原路径含 RUNNER_TEMP，下载后校验须显式映射前缀并保留原清单；内层 manifest 使用相对路径。

## 6. 首分歧证据与独立验收

1. 先核实际合树、backend 子树、源码、锁、线程 profile 与 artifact 完整 SHA；原 pytest 日志/真实 rc 和诊断 rc 分列。
2. 先比 op/pv 的完整原值；一致后，按真实结构/时滞/窗/输入 SHA 对齐各调用的 u/y。不能只按记录序号认同一候选。
3. 输入一致而候选系数或选模不同，再取实际差异调用的首轮 LS 矩阵/response/rcond/output 和逐迭代第一分歧；当前探针没有这些就明确缺项，不把末端差异写成因果。
4. 根因修复的独立反向必须在冻结原件上撤销具体修复后重现、恢复后通过；原样保留三个 joint/SNR/roundtrip 断言、其他冻结 oracle 与完整后端分母。不得用作者口述或重复跑绿替代。

## 7. 本地检查、返修与移交状态

- `structure-fixed-base.*`：14 项纯序列化/AST/YAML/guard/shell 结构检查，真实 rc0；只执行 serializer/审计回调，不执行辨识算法。原 pytest command/env、其他 workflow jobs/settings 等价核在案。
- 初版 ruff/format 均 rc0。独立复验随后发现初版未拦截 `gethostbyaddr/sendto/sendmsg`，初版不因此升级通过；原源与固定反例均保留。
- `network-guard-v2/config100-same-original-red.*`：同一非作者固定 probe 对原源真实 rc1；`config100-same-original-green.*` 对新证据副本真实 rc0。`getnameinfo` 额外纯 AST 控制也拒绝；实际 socket/算法调用均为 0。
- 新副本仅扩审计事件 tuple，`config100-minimal-change.diff` 为 9 加 1 删；替回 tuple 后整模块 AST 相同；ruff、真实 backend 100 列 format/check 均 rc0。E 首轮误用 88 列的格式中间件另存 `format-default88-intermediate.py` 及旧 identity，不作为最终候选。
- 本任务不再重复本机数值探针。真实 Linux 新诊断尚待 root 正常提交/完整 pre-push/推送后观测；当前不声明 G3 数值问题修好、F03 真业务通过或正式 20 批容量完成。
- root 统一所有 commit/push/运行操作；原四项项目用户豁免保持原口径。本文件和工具均不更改历史 c452 协议、旧审计或历史失败结论。

## 8. 网禁 v2 的实际采用记录

2026-10-17T14:35:59Z，F04 已对 `c2eba163…` 与 workflow `575d2d19…` 给出独立局部 PASS；实际记录见 `E/independent/f02-g3-diagnostic-review/static-and-serialization-independent.json`、`network-guard-fixed-green.command.json` 及相同固定 probe 的旧红/新绿原件。其明确范围为工具源/序列化/CI 结构，实际算法、网络与产品导入均为 0；不是远端数值验收。

root 确认原完整 hooks 全部退出后明确批准复制。2026-10-17T14:37:01Z 已仅将这份 437 行脚本复制到候选，实际 SHA 为 `c2eba163d4af6881cf83726311015064ec017c931528ae804a72a29d29c3b858`；workflow 仍为 `575d2d19d0a9ac59d987e0df6cbb1659d6a106b91c9f1edfa3a900570d9d6622`。复制前 HEAD `e2711c384f9104e311a29800362fa9dbbda1b7e6`、工作树 clean；复制后 `git diff --name-only` 严格只有该脚本。真实命令/rc、前后 SHA 与精确 patch 在 `network-guard-v2/candidate-landing.json`、`landing-final-patch.*`。本代理未 commit/push；root 后续必须正常重跑完整提交/推送门禁，不复用被 SIGINT 中止的 Git 推送。
