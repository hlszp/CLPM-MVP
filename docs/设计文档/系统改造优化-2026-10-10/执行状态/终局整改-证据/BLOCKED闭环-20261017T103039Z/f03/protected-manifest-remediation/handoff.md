# 保护指纹单条更新交接

本次只更新 `backend/tests/golden/refactor_protected_manifest.json` 中 `app/services/tuning_identification/arx.py` 的 SHA-256，旧值 `89c757376c37c827239ff3d12f092c9b9bcb036b5307db55f09d7b6d6f00eba0`，新值 `2fdf77affb47d8edfb451e6445c5d8e2e818cfd450afc5204321cff923a9e011`。41 条中另外 40 条、`baseline_head`、字节布局不变；保护测试、收集器和 ARX 实现未在本增量改动。

授权来源：用户本轮授权必要提交/测试环境闭环修复；主协调 `/root` 本轮交接明确批准：『只更新已独立证明等价且用户授权F03必要修复的ARX【单条hash】，禁止批量盲重生成/改assert/改timeout。』其要求是主审批准的现有测试基线维护，不是新增用户豁免。设计 §2.2 明写：『以下“冻结”是本次重构的评审要求，不是阻止用户日后有证据的算法改进』；`design.before.md` 保全整份历史规则，非改写需求。测试现行 docstring 允许用户/主审显式授权后重新固化，但本次未运行全量重生成模式。

真实失败在 `../../git/arx-route-full-gates-push.*`，正常 pre-push rc=1，5713 passed 后 -x 停止，尚未推送。局部复跑 `guard-before.*` rc=1（仅 ARX changed）；单条更新后 `guard-after.*` 1 passed、rc=0。`golden.before.json` / `golden.after.json`、`pre-update-diff.json`、`single-entry.patch`、`result.json` 保存原件和差异。原设计与测试/collector 字节完整保全。

等价依据仍为相邻 `../arx-boundary-remediation/`：17 例 legacy 全通过→首版 4 失败→修后 17 通过；原 22 case 的完整结果严格相等、16/18=88.9%；相邻相关 283 passed / 1 既有 xfail，原单例 coverage 17.61s（原 30s 不变）。源优化的 Linux 修复前两次真实 30s 超时仍保留，修后 Linux 完整 CI 和四业务目前未验，不凭 hash 更新签通过。

实现者不签独立验收。已交 F04 非作者核唯一 entry、另 40 条及测试原字节；唯一 Keeper / 提交推送人为主协调。四项项目用户豁免保留，不改代码门禁、算法、22 分母、17 边界、原 30s、skip、CI 重试配置。
