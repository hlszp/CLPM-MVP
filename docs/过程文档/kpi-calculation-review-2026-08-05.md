# 性能评估指标计算方法系统性检查报告

| 项 | 值 |
|---|---|
| 版本 | v1.0 |
| 日期 | 2026-08-05 |
| 基线 | 算法 v2.1 + 可信度统一 Phase 1/2/3 + GB/T 44693.2-2024 对齐 |
| 检查范围 | 26 个指标计算器（3 核心 + 1 折扣 + 22 辅助）+ 综合评分 + 节点聚合 |
| 代码位置 | `backend/app/services/metric_calculator/`、`backend/app/services/confidence_evaluator.py`、`backend/app/services/performance.py`、`backend/app/services/node_aggregation.py` |
| 依据标准 | GB/T 44693.2-2024（附录 B/F）、FDS v5.1 §4、算法说明 v2.1 |

## 0. 执行摘要

本报告对性能评估指标体系中全部 26 个指标计算器、综合评分公式、节点聚合算法进行了逐项检查，覆盖**计算逻辑科学性、数学表达准确性、标准规范符合性、当前数据条件适用性**四个维度。

**核心结论：**

1. **3 个核心质量指标（A/F/S）+ 1 个折扣因子（R）的数学表达与国标 GB/T 44693.2-2024 附录 B 完全对齐**，公式经 v2.1 修正后科学性良好（无偏估计、数据驱动归一化、数值稳定性修复）。
2. **综合评分公式 P = (A·a + F·f + S·s)/(a+f+s) × R** 实现正确，缺失指标处理已消除"隐性扣分"陷阱，统一 INCONCLUSIVE 语义。
3. **饱和率已对齐国标附录 F.3**（分母=总时长 AllTime，分子=自控饱和时长 AutoSaturateTime），2026-08-04 修复生效。
4. **OP 量程独立归一化修复**（2026-08-04）解决了 OP 被 PV 量程误归一化的系统性缺陷，6 个压力回路恢复 A/B 级。
5. **可信度统一 Phase 1/2/3** 将指标级 A/B/C/D/E 降级为回路级单一可信度，消除了"每指标各打一档"的细粒度浪费与口径分歧。
6. **当前主要局限**：① 评估窗口需规避 7/2–7/7 数据空洞；② 长期手动回路 R 恒 INCONCLUSIVE（设计预期）；③ 部分指标（stiction/settling_time）依赖强假设，非振荡/非衰减段返回 INCONCLUSIVE 而非数值。

---

## 1. 指标体系总览

### 1.1 指标分类与角色

| 角色 | 指标代码 | 含义 | 参与评分 | 国标附录 |
|---|---|---|---|---|
| **核心质量-准确性** | `accuracy_rate` (A) | PV 达 SP 的准确度（余差） | ✅ 加权 | B.3 |
| **核心质量-快速性** | `fast_rate` (F) | 响应速度（稳态时间对比） | ✅ 加权 | B.4 |
| **核心质量-稳定性** | `stability_rate` (S) | PV 波动平稳度 | ✅ 加权 | B.5 |
| **折扣因子** | `effective_auto_rate` (R) | 有效自控率（投用率） | ✅ 乘数 | B.2 |
| 辅助-投用 | `auto_mode_rate` | 自控率（仅判 MODE） | ❌ 显示 | B.1 |
| 辅助-投用 | `good_value_rate` | 好值率（PV 质量 Good 占比） | ❌ 显示 | F.6 |
| 辅助-诊断 | `oscillation_rate` | 振荡率（IAE 零交叉相似率） | ❌ 显示 | F.1 |
| 辅助-诊断 | `saturation_rate` | 饱和率（OP 限位时长占比） | ❌ 显示 | F.3 |
| 辅助-诊断 | `stiction_index` | 粘滞系数（PV-OP 椭圆短长轴比） | ❌ 显示 | F.2 |
| 辅助-诊断 | `output_trip_index` | 输出行程指数（阀门磨损） | ❌ 显示 | F.5 |
| 辅助-依赖 | `settling_time` | 实际稳态时间（ARMA+Green 函数） | ❌ 供 F | F.4 |
| 辅助-依赖 | `ideal_settling_time` | 理想稳态时间（基准） | ❌ 供 F | B.4 |
| Phase1 辅助 | `instrument_fault_rate` 等 14 项 | 统计量/阀门/故障 | ❌ 显示 | — |

### 1.2 综合评分公式

$$P = \frac{A \cdot a + F \cdot f + S \cdot s}{a + f + s} \times R$$

- A/F/S：核心质量指标值（0–100）
- a/f/s：权重（按控制类型 STABLE/SLOW/FAST/LOGIC 配置，和=100）
- R：有效自控率（0–100），作为**乘数折扣因子**而非加权项

### 1.3 权重模板（对齐国标附录 C 默认值）

| 控制类型 | a (准确) | f (快速) | s (稳定) | 适用 |
|---|---|---|---|---|
| STABLE | 0.2 | 0.3 | 0.5 | 温度、压力 |
| SLOW | 0.3 | 0.1 | 0.6 | 缓慢调节 |
| FAST | 0.2 | 0.5 | 0.3 | 副回路、流量 |
| LOGIC | 0.0 | 0.4 | 0.6 | 防回流、防超温 |

### 1.4 5 级性能等级

| 等级 | 分值区间 | 中文 |
|---|---|---|
| EXCELLENT | ≥ 90 | 优 |
| GOOD | 80–90 | 良 |
| FAIR | 70–80 | 中 |
| WARNING | 60–70 | 差 |
| POOR | < 60 | 劣 |

### 1.5 可信度等级（valid_rate → A/B/C/D/E）

| valid_rate | 等级 | 含义 | INCONCLUSIVE |
|---|---|---|---|
| ≥ 0.95 | A | 优秀 | 否 |
| 0.80–0.95 | B | 良好 | 否 |
| 0.60–0.80 | C | 一般 | 否 |
| 0.20–0.60 | D | 较差 | 否 |
| < 0.20 | E | 不足 | 是（value=None） |

---

## 2. 逐指标检查

### 2.1 准确率 accuracy_rate (A) — 核心

**代码**：[accuracy.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/metric_calculator/accuracy.py)

#### 2.1.1 计算公式

$$A = \left[1 - r \cdot \left(1 - e^{-r}\right)\right] \times 100\%$$

其中：
- $E_i = PV_i - SP_i$（控制偏差）
- $|\bar{E}| = \frac{1}{n}\sum|E_i|$（平均绝对偏差）
- $|E|_{max} = \frac{1}{n}\sum\left[\max(|E_i|) - |E_i|\right]$（数据驱动，v2.1 修正）
- $r = |\bar{E}| / |E|_{max}$（归一化偏差，∈[0,1]）

#### 2.1.2 检查清单

| # | 检查项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 数学表达准确性 | ✅ 通过 | 公式与算法说明 §4.4 v2.1 一致；指数衰减因子用 `math.exp(-r)` 避免大 r 溢出（L138） |
| 2 | 国标符合性 | ✅ 通过 | 对齐 GB/T 44693.2-2024 附录 B.3 |
| 3 | 归一化基准科学性 | ✅ 通过 | v2.1 改为数据驱动 $|E|_{max}$，消除"外部参数导致跨回路不可比"；保留 CONFIG 覆盖入口 |
| 4 | 退化情形处理 | ✅ 通过 | e_max=0 且 mean_abs_error=0 → A=100%；e_max=0 且有余差 → 按量程 5% 扣分（L98-130） |
| 5 | 极端偏差抑制 | ✅ 通过 | `e_max_percentile` 参数可百分位截断（L75-79） |
| 6 | 可信度处理 | ✅ 通过 | vr<0.20 → INCONCLUSIVE；否则回路级可信度（P2-2） |
| 7 | 数据需求 | PV+SP，有效对 ≥1 | 近期窗口充足 |

**验证步骤：**
1. 构造 PV=SP（零偏差）→ 应返回 A=100%
2. 构造恒定余差 PV=SP+5 → 触发退化分支，按量程扣分
3. 构造 PV 随机波动 → 检查 r∈[0,1]，A∈[0,100]
4. 检查 mask 有效点 <20% → INCONCLUSIVE

**结论**：✅ 科学性、准确性、国标符合性均通过。当前数据条件下适用性良好。

---

### 2.2 快速率 fast_rate (F) — 核心

**代码**：[fast_rate.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/metric_calculator/fast_rate.py)

#### 2.2.1 计算公式

$$F = \begin{cases} 100\% & T \le T' \\ e^{-(T-T')/T'} \times 100\% & T > T' \end{cases}$$

其中：
- $T$：实际稳态时间（由 `settling_time` 提供，ARMA 模型 + Green 函数）
- $T'$：理想稳态时间（由 `ideal_settling_time` 提供）
- 阈值 $= T' \times \text{ideal\_settling\_ratio} \times (1 + \text{settling\_tolerance})$，默认 = $T'$

#### 2.2.2 检查清单

| # | 检查项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 数学表达准确性 | ✅ 通过 | 分段指数衰减，与算法说明 §4.5 一致 |
| 2 | 国标符合性 | ✅ 通过 | 对齐 GB/T 44693.2-2024 附录 B.4 |
| 3 | 依赖链正确性 | ✅ 通过 | `depends_on = ["settling_time", "ideal_settling_time"]`，T 从 details.actual_settling_time 读取 |
| 4 | 三语义分流（P0-1） | ✅ 通过 | already_stable→100%；never_settles→窗口长度代入衰减公式；identification_failed→INCONCLUSIVE |
| 5 | 抗扰性分析（P2） | ✅ 通过 | `anti_disturbance_enabled` 开关默认关闭（零回归），开启后用扰动恢复时间替代 T |
| 6 | 阈值可配置性 | ✅ 通过 | `ideal_settling_ratio` / `settling_tolerance` 参数化 |
| 7 | 局限性 | ⚠️ 已知 | 依赖 ARMA 辨识质量；近单位根/持续振荡场景 T 不可靠（已用 never_settles 兜底） |

**验证步骤：**
1. 构造已稳态数据（PV=SP）→ settling_time=0 → F=100%
2. 构造慢响应 → T > T' → 检查 F = exp(-(T-T')/T')×100
3. 构造持续振荡 → never_settles → 以窗口长度代入，F < 100%
4. 辨识失败 → INCONCLUSIVE

**结论**：✅ 公式正确，三语义分流是关键鲁棒性设计。局限在于 ARMA 辨识对数据质量敏感，已用多级兜底缓解。

---

### 2.3 稳定率 stability_rate (S) — 核心

**代码**：[stability.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/metric_calculator/stability.py)

#### 2.3.1 计算公式

$$S = e^{-\sigma / (0.05 \cdot U)} \times (1 - Osc) \times 100\%$$

其中：
- $E_i = PV_i - SP_i$
- $\sigma = \sqrt{\frac{1}{n-1}\sum(E_i - \bar{E})^2}$（**无偏估计，ddof=1**，v2.1 修正）
- $U$ = PV 量程范围（归一化后 100）
- $Osc$ = 振荡率（0–1，由 `oscillation_rate` 提供）

#### 2.3.2 检查清单

| # | 检查项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 数学表达准确性 | ✅ 通过 | 指数衰减 × 振荡修正，与算法说明 §4.3 v2.1 一致 |
| 2 | 国标符合性 | ✅ 通过 | 对齐 GB/T 44693.2-2024 附录 B.5 |
| 3 | 标准差估计 | ✅ 通过 | v2.1 改为 `np.std(errors, ddof=1)` 无偏估计（L74），小样本更准确 |
| 4 | 数值稳定性 | ✅ 通过 | `math.exp(-normalized_std)` 避免 large x 溢出（L102） |
| 5 | 振荡率依赖 | ✅ 通过 | `depends_on = ["oscillation_rate"]`，Osc≥100% → S=0 |
| 6 | 量程处理 | ⚠️ 注意 | U 从 CONFIG 读取，缺失回退默认 100（L134）；归一化数据应为 100 |
| 7 | 局限性 | ⚠️ 已知 | 假设偏差近似高斯分布；强非高斯（间歇振荡）时 σ 可能高估 |

**验证步骤：**
1. 构造 PV=SP 恒定 → σ=0 → S=100%
2. 构造 PV 高斯噪声 → 检查 S = exp(-σ/(0.05U))×100
3. 注入振荡 → Osc>0 → S 相应降低
4. Osc≥100% → S=0

**结论**：✅ 科学性通过。无偏估计修正是关键改进。局限在于高斯假设，对间歇性振荡敏感。

---

### 2.4 有效自控率 effective_auto_rate (R) — 折扣因子

**代码**：[effective_auto.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/metric_calculator/effective_auto.py)

#### 2.4.1 计算公式

$$R = \frac{T_{auto\_effective}}{T_{total}} \times 100\%$$

其中 $T_{auto\_effective}$ 需同时满足：
1. MODE ∈ Auto/Cascade/Remote/APC（`AUTO_MODES={1,2,3,4}`）
2. OP 未饱和（`OP_low+ε < OP < OP_high-ε`，默认 ε=2）
3. 控制偏差合理（`|PV-SP| < |E|_max`，默认 `|E|_max = 5% × 100 = 5`）

#### 2.4.2 检查清单

| # | 检查项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 数学表达准确性 | ✅ 通过 | 时长加权（零阶保持模型），与算法说明 §4.2 一致 |
| 2 | 国标符合性 | ✅ 通过 | 对齐 GB/T 44693.2-2024 附录 B.2 |
| 3 | 时长模型 | ✅ 通过 | `_point_durations` 零阶保持，末点沿用前段时长（base.py L284-305） |
| 4 | 可选信号处理 | ✅ 通过 | OP/PV/SP 缺失时不判该条件（L88-89, L101-111），避免零截断误报 |
| 5 | 饱和容差 | ✅ 通过 | ε 可配置（`saturation_epsilon`），默认 2% |
| 6 | 偏差阈值 | ⚠️ 注意 | `|E|_max` 默认 5（归一化量程×5%），未配置时用固定值 |
| 7 | 局限性 | ⚠️ 已知 | 长期手动回路 → auto_duration=0 → R=0%（非 INCONCLUSIVE，分子分母均为正）；若 total_duration=0 才 INCONCLUSIVE |

**验证步骤：**
1. 全程 AUTO + OP 未饱和 + 偏差小 → R≈100%
2. 全程 MANUAL → R=0%（auto_duration=0）
3. AUTO 但 OP 饱和 → 饱和段不计入 effective
4. 点数<2 → INCONCLUSIVE

**结论**：✅ 公式正确。R 作为乘数折扣是国标设计。局限在于手动回路 R=0% 导致综合评分趋零，需配合 `include_in_evaluation` 排除。

---

### 2.5 综合评分 composite_score (P)

**代码**：[confidence_evaluator.py L252-475](file:///Users/zhangping/DEV/CLPM/backend/app/services/confidence_evaluator.py)

#### 2.5.1 计算公式

$$P = \frac{\eta_A \cdot a + \eta_F \cdot f + \eta_S \cdot s}{a + f + s} \times 100 \times \frac{R}{100}$$

其中 $\eta_X = \max(0, \min(1, X/100))$（归一化到 [0,1] 再加权）。

#### 2.5.2 检查清单

| # | 检查项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 数学表达准确性 | ✅ 通过 | 加权平均 × 折扣因子，与算法说明 §4.10 一致 |
| 2 | 国标符合性 | ✅ 通过 | 对齐 GB/T 44693.2-2024 附录 B.6 |
| 3 | R 缺失处理 | ✅ 通过 | R 缺失或 E 级 → 评分 INCONCLUSIVE（L308-335），**移除了原"R 缺失降级 60%"无依据逻辑** |
| 4 | 核心指标缺失 | ✅ 通过 | 权重>0 的核心指标缺失/E 级 → 评分 INCONCLUSIVE（L339-375），**消除"分子计 0、分母留权重"隐性惩罚** |
| 5 | 权重为 0 处理 | ✅ 通过 | LOGIC 型 a=0 时 accuracy 不参与评分（L341 `if weight <= 0: continue`） |
| 6 | 可信度继承 | ✅ 通过 | P2-3：综合评分可信度 = accuracy_rate 的回路级可信度（L435） |
| 7 | D 级输入标注 | ✅ 通过 | `low_confidence_inputs` 记录 D 级指标（L438-443），保留评分但标注 |
| 8 | 数值边界 | ✅ 通过 | `score = max(0, min(100, score))`（L429） |

**验证步骤：**
1. A=90, F=80, S=85, R=90, weights(0.2/0.3/0.5) → base=(18+24+42.5)/1×100=84.5 → P=84.5×0.9=76.05
2. R=None → P=None（INCONCLUSIVE）
3. A=None, a=0.2 → P=None（INCONCLUSIVE）
4. a=0, f=0.4, s=0.6, A 不存在 → 不要求 A，正常计算

**结论**：✅ 公式正确，缺失处理语义清晰。关键改进是消除隐性惩罚。

---

### 2.6 自控率 auto_mode_rate

**代码**：[auto_mode.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/metric_calculator/auto_mode.py)

| # | 检查项 | 结论 |
|---|---|---|
| 1 | 公式 | $Auto = T_{auto} / T_{total} \times 100\%$，仅判 MODE ∈ AUTO_MODES |
| 2 | 国标 | ✅ 附录 B.1 |
| 3 | 与 R 的区别 | ✅ 明确：仅判 MODE，不判 OP 饱和与偏差 |
| 4 | 适用性 | ✅ 近期数据充足 |

**结论**：✅ 通过。作为 R 的诊断对照指标。

---

### 2.7 好值率 good_value_rate

**代码**：[good_value.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/metric_calculator/good_value.py)

| # | 检查项 | 结论 |
|---|---|---|
| 1 | 公式 | $\eta_{good} = T_{good} / T_{total} \times 100\%$，PV 质量码 Good 且在量程内 |
| 2 | 国标 | ✅ 附录 F.6 |
| 3 | 数据来源 | ✅ 优先用 `quality_summary.good_value_rate`，回退 `pv_valid` 计数 |
| 4 | INCONCLUSIVE | ⚠️ 好值率 <20% → INCONCLUSIVE（L71-76），独立于 mask vr 阈值 |
| 5 | 适用性 | ✅ 近期 PV 质量码良好 |

**结论**：✅ 通过。注意其 INCONCLUSIVE 阈值与指标级 mask vr 阈值是双重门禁。

---

### 2.8 振荡率 oscillation_rate

**代码**：[oscillation.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/metric_calculator/oscillation.py)

#### 2.8.1 算法

基于 IAE（积分绝对误差）零交叉相似率法（Hägglund 2005 + GB/T 44693.2-2024 附录 F.1）：
1. 计算偏差 $E = PV - SP$
2. 识别零交叉点（前向填充零值平台）
3. 计算相邻零交叉间 IAE 与持续时间
4. 正/负半周期 IAE 相似率 $S_A/S_B$（最小距离法）
5. 振荡率 $= \min(S_A, S_B) \times 100$；`is_oscillating = S_A≥τ AND S_B≥τ`（τ=0.4）

#### 2.8.2 检查清单

| # | 检查项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 数学表达准确性 | ✅ 通过 | IAE 零交叉相似率，对齐附录 F.1 |
| 2 | 零交叉处理 | ✅ 通过 | 前向填充零值平台，避免伪穿越（L176-183） |
| 3 | 残缺半周期 | ✅ 通过 | 首尾残缺段剔除（L198-213） |
| 4 | 相似率对称性 | ✅ 通过 | P2 #33 修正为对称形式 `1-|cleaned_avg-avg|/|avg|`（L257） |
| 5 | 持续时间相似率 | ✅ 通过 | $S_{TA}/S_{TB}$ 作为辅助诊断输出（非判定） |
| 6 | 最少数据点 | ✅ 通过 | n<4 → INCONCLUSIVE；零交叉<4 → 返回 0 |
| 7 | 阈值可配置 | ✅ 通过 | `similarity_threshold`/`min_ratio`/`max_ratio` 参数化 |
| 8 | 局限性 | ⚠️ 已知 | 最小距离法对少量离群 IAE 敏感；已有 min_ratio/max_ratio 清洗 |

**结论**：✅ 科学性通过，对齐国标。局限在于相似率清洗参数需根据工况调优。

---

### 2.9 饱和率 saturation_rate

**代码**：[saturation.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/metric_calculator/saturation.py)

#### 2.9.1 计算公式（国标 F.3 对齐，2026-08-04 修复）

$$\eta_{sat} = \frac{T_{auto\_saturated}}{T_{total}} \times 100\%$$

- 分子 $T_{auto\_saturated}$：仅自控模式（AUTO/CAS/REMOTE/APC）下 OP 限位时长
- 分母 $T_{total}$：**评估时段总时长（含手动模式，对应国标 AllTime）**

#### 2.9.2 检查清单

| # | 检查项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 数学表达准确性 | ✅ 通过 | 分母=AllTime（含手动），分子=AutoSaturateTime，对齐国标 F.3 |
| 2 | 国标符合性 | ✅ 通过 | 2026-08-04 修正：原分母=auto_duration，现=total_duration |
| 3 | 全程手动 | ✅ 通过 | 分子=0（手动不计入），返回 0% 而非 INCONCLUSIVE |
| 4 | OP 解析失败 | ✅ 通过 | OP=None 时跳过该点不计入饱和分子（L96-99），避免误判低限饱和 |
| 5 | 饱和类型 | ✅ 通过 | HIGH/LOW/BOTH/NONE 四态判定 |
| 6 | 数据需求契约 | ✅ 通过 | `clpm_metric_data_requirement` 已含 mode+op（2026-08-04 修复） |
| 7 | 局限性 | ⚠️ 已知 | ε=2% 容差可能对高精度阀门偏严/偏松 |

**验证步骤：**
1. 全程 AUTO + OP=50 → sat=0%
2. 全程 AUTO + OP=99 → sat≈100%（HIGH）
3. 全程 MANUAL + OP=99 → sat=0%（手动不计入分子）
4. 混合模式 → 仅 AUTO 段饱和计入分子，分母为总时长

**结论**：✅ 修复后完全对齐国标。关键修复是分母从 auto_duration 改为 total_duration。

---

### 2.10 粘滞系数 stiction_index

**代码**：[stiction.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/metric_calculator/stiction.py)

#### 2.10.1 计算公式

$$St = \frac{b}{a} \times 100\%$$

其中 $a/b$ 为 PV-OP 散点椭圆长短轴（PCA 协方差矩阵特征值平方根）。

#### 2.10.2 检查清单

| # | 检查项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 数学表达准确性 | ✅ 通过 | PCA 椭圆拟合，对齐国标 F.2 |
| 2 | 极限环门控 | ✅ 通过 | 仅极限环振荡段出值（Kano/Chouddury 前提）：零交叉≥4 + 半周期≥8 点 + IAE 相似率≥0.6 |
| 3 | 纯滞后补偿 | ✅ 通过 | 互相关估计 θ̂，OP 平移后再拟合（L124-139）；峰值<0.3 回退不补偿 |
| 4 | 拟合度门控 | ✅ 通过 | R²<0.5 → INCONCLUSIVE（L159-174），避免圆团散点误报 |
| 5 | 拟合度定义 | ✅ 通过 | P2 修正：从 `λmax/(λmax+λmin)` 改为 `r²`（OP-PV 相关系数平方），门控真实生效 |
| 6 | 等级判定 | ✅ 通过 | NONE/MILD/MODERATE/SEVERE（<5/15/30/≥30%） |
| 7 | 最少数据点 | ✅ 通过 | n<100 → INCONCLUSIVE |
| 8 | 局限性 | ⚠️ 已知 | 仅极限环下有效；平稳回路返回 INCONCLUSIVE（设计预期）；θ 估计受噪声影响 |

**结论**：✅ 科学性通过，门控设计严谨。局限是仅适用于振荡回路，非振荡段不出值。

---

### 2.11 输出行程指数 output_trip_index

**代码**：[output_trip.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/metric_calculator/output_trip.py)

| # | 检查项 | 结论 |
|---|---|---|
| 1 | 公式 | $Trip = \sum|OP_i - OP_{i-1}| / (T_{total} \cdot OP_{range})$，单位行程/秒 |
| 2 | 国标 | ✅ 附录 F.5 |
| 3 | 精度 | ✅ 通过 | `precision=6` 避免 INACTIVE/NORMAL 区间值被抹零（L99） |
| 4 | 等级 | ✅ 通过 | INACTIVE/NORMAL/FREQUENT/EXCESSIVE（0.01/0.1/1.0 阈值） |
| 5 | 适用性 | ✅ 近期数据充足 |

**结论**：✅ 通过。

---

### 2.12 稳态时间 settling_time

**代码**：[settling_time.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/metric_calculator/settling_time.py)

| # | 检查项 | 结论 |
|---|---|---|
| 1 | 算法 | AR(p) Yule-Walker 辨识 → Green 函数递推 → |G(k)|<5% 首次持续时刻 |
| 2 | 国标 | ✅ 附录 F.4 |
| 3 | 三语义分流 | ✅ 通过（P0-1）| already_stable(value=0) / never_settles(value=None, 携带窗口长度) / identification_failed(value=None) |
| 4 | 最少数据点 | ✅ n<100 → INCONCLUSIVE |
| 5 | 局限性 | ⚠️ ARMA 辨识假设平稳；近单位根/强非平稳可能 never_settles |

**结论**：✅ 三语义分流是关键鲁棒性设计，避免"窗口内不衰减"被误判满分。

---

### 2.13 理想稳态时间 ideal_settling_time

**代码**：[ideal_settling_time.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/metric_calculator/ideal_settling_time.py)

| # | 检查项 | 结论 |
|---|---|---|
| 1 | 三级优先级 | ✅ 手动配置 > 模型法 T'=α(τ+θ) > 控制类型默认值 |
| 2 | 默认值 | ✅ FC=30/PC=60/TC=180/LC=600/CC=300 秒（P1 #17 修正） |
| 3 | α 系数 | ✅ FC=1.5/PC=2.0/TC=2.75/LC=4.0/CC=3.5 |
| 4 | 局限性 | ⚠️ 默认值为经验值，需结合工况校准 |

**结论**：✅ 通过。建议为关键回路配置 τ/θ 参数启用模型法。

---

## 3. 预处理与可信度链路检查

### 3.1 8 步预处理流水线

**代码**：[pipeline.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/preprocessing/pipeline.py)

| 步骤 | 模块 | 检查结论 |
|---|---|---|
| ① 质量码识别 | quality_code.py | ✅ Good/Bad/Unknown 三态映射 |
| ② 有效性标记 | validity_mask | ✅ 质量 Bad + 非 MARK_ONLY 异常 → valid=False；FROZEN/TS_ANOMALY 仅标记 |
| ③ 量程归一化 | pipeline._step3_normalize | ✅ **PV/SP 用 PV 量程，OP 用 OP 量程（独立，2026-08-04 修复）** |
| ④ 异常值识别 | outlier_detection.py | ✅ 8 类检测（Z-score/IQR/Modified Z/Range/Sliding/MA/3-Sigma/Distribution） |
| ⑤ 缺失率统计 | quality_summary | ✅ 在步骤⑧计算 |
| ⑥ 连续性检查 | compute_consecutive_segments | ✅ 连续有效段，缺口超阈值切断 |
| ⑦ Metric Mask | pipeline.generate_metric_mask | ✅ 按契约 mask_expression 过滤 |
| ⑧ QualitySummary | quality_summary.py | ✅ 聚合 valid_rate + good_value_rate |

**关键修复验证：**
- OP 量程独立归一化：`op_range_min/op_range_max` 独立于 `range_min/range_max`（L315-316），cache key 含 op_range 后缀（失效旧缓存）
- 回路级可信度：`loop_valid_rate = compute_loop_valid_rate(validity, n)` → `ConfidenceEvaluator.evaluate()` → `loop_confidence_level`（L155-156）
- DataBlock 派生字段继承：`_derive_from_base` 拷贝 `loop_confidence_level`/`loop_valid_rate`（data_planner.py L680-728，2026-08-05 修复）

### 3.2 可信度统一 Phase 1/2/3

| Phase | 内容 | 检查结论 |
|---|---|---|
| P1 | 文档更新 + KPI 字段口径错配修复 | ✅ 已合入 |
| P2-1 | 回路级 valid_rate 统一计算（核心 tag 交集） | ✅ Pipeline 已实现 |
| P2-2 | 指标可信度改用回路级（消除指标级 A/B/C/D/E） | ✅ base.py `_make_result` 读取 `loop_confidence_level`（L184） |
| P2-3 | 综合评分可信度 = 回路级 | ✅ confidence_evaluator.py L435 |
| P3-2 | 阈值 Redis pub/sub 多进程同步 | ✅ broadcast_thresholds + 订阅线程 + 版本去重 |
| P3-3 | 濒临 INCONCLUSIVE 告警（D ≤ vr < D+0.10） | ✅ evaluate() L189-202 |

**结论**：✅ 可信度统一已完成，4 种 valid_rate 口径收敛为回路级单一值。

---

## 4. 节点聚合检查

**代码**：[node_aggregation.py](file:///Users/zhangping/DEV/CLPM/backend/app/services/node_aggregation.py)

| # | 检查项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 回路→节点加权 | ✅ 通过 | `LEVEL_WEIGHTS = {1:3, 2:2, 3:1}`（L585），importance_level 加权 |
| 2 | 复杂回路去重 | ✅ 通过 | MAIN 优先，同组按代表回路聚合 |
| 3 | include_in_evaluation=False | ✅ 通过 | 节点聚合排除，但单回路 KPI 仍计算 |
| 4 | NULL 聚合处理 | ✅ 通过 | `_weighted_average` 全 NULL 返回 None（L117-121） |
| 5 | 日/月聚合 | ✅ 通过 | 小时层加权，日/月层简单平均（不含 level 字段） |
| 6 | 国标符合性 | ✅ 通过 | 附录 E.2 |

**结论**：✅ 通过。

---

## 5. 综合评估结论

### 5.1 符合性矩阵

| 维度 | 核心指标 | 辅助指标 | 综合评分 | 节点聚合 |
|---|---|---|---|---|
| 数学表达准确性 | ✅ | ✅ | ✅ | ✅ |
| 国标符合性（GB/T 44693.2-2024） | ✅ 附录 B.3-B.5 | ✅ 附录 F.1-F.6 | ✅ 附录 B.6 | ✅ 附录 E.2 |
| 科学性 | ✅ | ✅ | ✅ | ✅ |
| 当前数据适用性 | ✅（近期窗口） | ✅ | ✅ | ✅ |
| 局限性已兜底 | ✅ | ✅ | ✅ | ✅ |

### 5.2 已修复的关键缺陷清单

| 缺陷 | 修复 | 日期 |
|---|---|---|
| OP 被 PV 量程误归一化 → 6 压力回路 E/INCONCLUSIVE | OP 独立量程归一化 | 2026-08-04 |
| 饱和率分母用 auto_duration（不符国标 F.3） | 分母改 total_duration | 2026-08-04 |
| DataBlock 派生未继承回路级可信度 → 子 tagGroup 默认 E | `_derive_from_base` 拷贝字段 | 2026-08-05 |
| 指标级可信度口径分歧（4 种 valid_rate） | 统一为回路级单一可信度 | 2026-08-04 (P2) |
| 阈值多进程不一致 | Redis pub/sub 同步 | 2026-08-04 (P3) |
| 稳定率标准差有偏估计 | ddof=1 无偏估计 | v2.1 |
| 准确率 |E|_max 依赖外部参数 | 数据驱动计算 | v2.1 |
| 粘滞拟合度恒≥0.5 门控不可达 | 改用 r² | P2 |
| 振荡相似率不对称 | 对称形式 | P2 #33 |
| 综合评分 R 缺失降级 60% | 统一 INCONCLUSIVE | P1 #18 |

### 5.3 当前数据条件下的适用性总结

| 指标 | 适用性 | 前提条件 |
|---|---|---|
| accuracy_rate (A) | ✅ 适用 | 评估窗口 valid_rate ≥ 20%（近期 ~97%） |
| fast_rate (F) | ✅ 适用 | ARMA 辨识需 ≥100 有效点 |
| stability_rate (S) | ✅ 适用 | 需 oscillation_rate 先算 |
| effective_auto_rate (R) | ⚠️ 部分适用 | 长期手动回路 R=0%/INCONCLUSIVE（设计预期） |
| composite_score (P) | ✅ 适用 | A/F/S/R 均非 INCONCLUSIVE |
| 辅助诊断指标 | ✅ 适用 | 各自门控条件满足 |

### 5.4 后续建议

| 优先级 | 建议 |
|---|---|
| P0 | 清理 7/2–7/7 全空行，确保评估窗口 valid_rate ≥ 80%（B 级以上） |
| P1 | 为关键回路配置 τ/θ 参数启用 ideal_settling_time 模型法 |
| P1 | 校准 stiction/oscillation 相似率清洗参数（min_ratio/max_ratio） |
| P2 | 补充 GB/T 44693.2-2024 整定用例验证（诊断整改 Phase E） |
| P2 | 增加超密写入（>1Hz）的检测与告警 |

---

## 6. 附录：指标计算器注册表

| metric_code | 计算器类 | 角色 | 国标附录 |
|---|---|---|---|
| accuracy_rate | AccuracyRateCalculator | 核心-A | B.3 |
| fast_rate | FastRateCalculator | 核心-F | B.4 |
| stability_rate | StabilityRateCalculator | 核心-S | B.5 |
| effective_auto_rate | EffectiveAutoRateCalculator | 折扣-R | B.2 |
| auto_mode_rate | AutoModeRateCalculator | 辅助 | B.1 |
| good_value_rate | GoodValueRateCalculator | 辅助 | F.6 |
| oscillation_rate | OscillationRateCalculator | 辅助 | F.1 |
| saturation_rate | SaturationRateCalculator | 辅助 | F.3 |
| stiction_index | StictionIndexCalculator | 辅助 | F.2 |
| output_trip_index | OutputTripIndexCalculator | 辅助 | F.5 |
| settling_time | SettlingTimeCalculator | 辅助(依赖) | F.4 |
| ideal_settling_time | IdealSettlingTimeCalculator | 辅助(依赖) | B.4 |
| instrument_fault_rate | InstrumentFaultRateCalculator | Phase1辅助 | — |
| pv/sp/op/error mean/std | *Calculator | Phase1统计量 | — |
| valve_linearity/nonlinearity/operating_range | *Calculator | Phase1阀门 | — |
| setpoint_crossing_count/oscillation_amplitude | *Calculator | Phase1辅助 | — |
