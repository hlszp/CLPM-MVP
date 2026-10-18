# ARX 首版边界回归整改增量

本机复验完成；独立复验、正常 pre-push 和新 Linux coverage CI 仍分别核验。此增量替代首版 ARX `5645aa9c…`，不改写旧证据。

## 真实反证和修复范围

主协调 `../root-arx-list-extreme-probe.json` 证明 Python int list 的 `-(2**63)` 经首版 `np.asarray` 提前变为 int64 后取负溢出，回归矩阵和 b 系数符号被改变。原标量实现并无此问题；首版 1,536 组合和原 22 例并未覆盖这个输入边界，不能扩称全部支持输入等价。

只修改 ARX 矩阵构造：快路仅用于确切的一维 `np.ndarray`、非负模型阶次和足够的输入跨度；其它输入完整保留原逐标量构造。保留 Python int/list/tuple/ndarray 子类的读取与负号、ndarray int64 自身原溢出、signed zero，以及短输入原 IndexError。没有新增清洗、拒绝数据、改变算法参数/候选/重复次数/门槛。原测试源码、数据集、30s timeout 与 coverage 60% 未变。

源码 `source-manifest.json` 于 `2026-10-17T11:55:58.697163+00:00` 冻结，SHA-256 `c661b5ec650f27be7dcbd81f5e82919622e88ba0422c73e8fe2ffdbcc9c0876e`。其中 testsComplete=false 是冻结时相关套件尚在运行的历史快照；`completion-check.json` 已补齐最终完成事实，冻结 manifest 不回写。

- ARX SHA：`2fdf77affb47d8edfb451e6445c5d8e2e818cfd450afc5204321cff923a9e011`。
- 17 例数学/边界测试 SHA：`52ead2de84e319d79afd8a73868e2337449ca3b59f1965fbdc9be8a1c72200bf`。
- 主协调已正常提交 `fb8c5464f919b870977bef51ffa8e255ae745b22`，作者未提交/推送。完整旧/首版/修后源与最终测试快照均保留在本目录。

## 原始证据和结论

- `legacy-boundary-v2.*`：冻结旧函数运行当前完整 17 例，17 passed、rc=0。
- `first-vectorized-boundary-v2.*`：首版函数运行同一 17 例，list/tuple/subclass/短输入 4 failed、13 passed、rc=1。
- `fixed-boundary.*`：修后同一 17 例，17 passed、rc=0。以上是外部加载函数或当前源码，不通过 checkout/revert 修改产品树。
- `root-probe-replayed.json`：原主协调输入再现。旧与修后 matrix SHA 均为 `ef97203ff6d59cc9cd743a5fc9314a67315244485578651c7d03715815d8cb1d`，lagAt21 为正 `2**63`，b 均 `4.907284471830195e-39`；首版 matrix SHA `db6c3c…`、lag 与 b 为负。
- `fallback-parity.json`：额外 17 个场景的旧/新矩阵、结果或错误类型和消息相同，覆盖大整数 list/tuple、原溢出拒绝、MaskedArray、Nx1、负阶次旧行为、负时滞与短 u。它是等价检查，不增加产品支持范围。
- `fixed-full-results.*` / `fixed-loop-results.json` / `completion-check.json`：原 22 例的完整 IdentificationResult 和准确性行与旧 baseline 严格一致，核心仍 16/18=88.9%。
- `fixed-single.*`：原节点带 coverage、原 30s 不变，1 passed，17.61s，rc=0。本机 Apple Accelerate 不能代替 Linux。
- `related-tests.*`：283 passed、1 既有 xfailed、0 failed，82.43s，rc=0；没有新增 skip/xfail。摘要在 stdout 第 40 行；末尾 DLASCL 诊断来自既有数值边界。
- `final-ruff.*` / `final-format.*`：均 rc=0。

`completion-check.json` 还独立重算旧 `remote-run-39028560853` 的 242 个文件，均保持原字节/SHA，旧 manifest 仍为 `30dd2f59435031f4f556e9f89a3851e49421ab15282cdfa1427a444d7162aecc`。该目录完全未改。

最终仍须由非作者核相同边界、主协调正常 pre-push，以及新 CI 原 22 例/30s/全 coverage 分母。此处不签完整后端或真实业务四例 PASS；未启动共享 API/Worker/Beat、未写共享业务库。

补充可恢复复跑：`fallback_parity.py` 自带 17 组输入重建，直接加载本目录冻结 legacy/fixed 字节；`fallback-replay.command.json` 为真实 rc=0，`fallback-parity-replay.json` 逐例矩阵/响应 hash、返回值或异常一致。此旁证不改变旧版本性能超时和真实 Linux 待验结论。
