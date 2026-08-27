# GB/T 44693.2 符合性验证报告（Phase 6）

> 日期：2026-07-28 ｜ 基线：main `713dd6e3`（推送后） ｜ 执行方式：4 个专项子任务（G1 增强 + G2/G3/G4/G5 验证套件）
> 套件位置：`backend/tests/compliance/` ｜ 运行：`cd backend && uv run pytest tests/compliance/ -q`

## 一、总体结论

- **套件规模：371 用例通过 + 36 项 xfail（通过率 91.1% ≥ 90% 目标）**
- 附录 B.1-B.6、F.1-F.5 公式级验证**全部通过**（除 1 项已登记偏差 xfail）
- 附录 F 34 用例零 xfail：振荡两侧一致、粘滞 θ 补偿生效、饱和数值 MODE、ARMA 误差带（τ=30s 偏差 0.1%、τ=60s 偏差 0.9%）、输出行程精度全部达标
- 整定算法：辨识精度（FOPDT 两点法 K/τ/θ 误差 3.0%/10.7%/0.9%）、五种整定公式查表全对、RK4 收敛误差 2.6e-11、SIMC min 规则边界全过
- **主要质量发现：3 个诊断标签误报率 100%（见 §四），建议立项修复**

## 二、覆盖矩阵

| 附录条目 | 用例数 | 结果 | 备注 |
|---|---|---|---|
| B.1 自控率 | 3 | ✅ | 混合 MODE 精确时长、APC 计入、末点零阶保持 |
| B.2 有效自控率 | 4 | ✅ | OP 饱和剔除、偏差严格小于边界、MANUAL 双剔除 |
| B.3 准确度 | 7 | ✅ | 含恒定余差不得满分（Phase 1 修复固化） |
| B.4 快速性 | 12 | ✅ | 边界满分/指数衰减/never_settles 不得满分/扰动覆盖 |
| B.5 稳定率 | 7 | ⚠️ 1 xfail | ddof=1 口径确认；xfail 为 pv_range 列表解包偏差（D-2） |
| B.6 综合评分 | 13 | ✅ | 四套权重模板手算例、D2 缺失口径、R 非加权 |
| F.1 振荡 | 5 | ✅ | KPI/诊断两侧一致，周期恢复 ±10% 内 |
| F.2 粘滞椭圆 | 5 | ✅ | b/a 精度 0.15% 偏差、大滞后不误报、θ 补偿粘滑注入 |
| F.3 饱和率 | 14 | ✅ | 数值 MODE（含 APC）、ε=2% 边界精确时长 |
| F.4 ARMA 稳态时间 | 7 | ✅ | τ=30/60s 偏差 <1%、缺口/稀疏/不足/恒定行为正确 |
| F.5 输出行程 | 3 | ✅ | 锯齿手算值精确、6 位精度不抹零 |
| 数值健壮性矩阵 | 234 单元 | ⚠️ 26 xfail | 26 计算器 × 9 退化模式，xfail 集中非有限值守护 |
| 异常检测边界 | 52 | ✅ | 8 类检测器阈值 ±ε 全部与手算一致 |
| 整定查表比对 | 21 | ⚠️ 1 xfail | SOPDT 5% 噪声可辨识性边界（能力基线已记录） |
| 诊断故障注入基线 | 14 | ⚠️ 3 xfail | 召回率全部 100% 达标；3 标签误报率 100%（§四） |

## 三、T1.6 粘滞方法论增强（本阶段唯一生产代码变更）

`stiction.py` 两项增强（10/10 用例）：

1. **θ 补偿**：OP-PV 互相关估计滞后 θ̂（归一化相关峰 ≥0.3 显著才启用），PV[t]↔OP[t−θ̂] 配对后拟合椭圆；details 携带 `theta_hat_seconds/theta_compensated/fitting_score_uncompensated` 证据链。FOPDT θ=30s 大滞后回路补偿后 St≈1.2（NONE），不再误报；已知 θ=30s 粘滑注入补偿后 St 落手算带 [6,11]
2. **振荡段门控**：零交叉≥4 + 平均半周期≥8 采样点（剔除白噪声伪振荡）+ 双侧 IAE 相似率≥0.6，非极限环 INCONCLUSIVE(`no_limit_cycle`)——平稳回路不再强行出粘滞值（Kano/Choudhury 适用前提）

**行为变更注意**：纯相位滞后椭圆（旧测试判 MODERATE 的样本）补偿后 St≈5（NONE），物理上更正确；平稳/线性 ramp 回路由出值改为 `no_limit_cycle`。

## 四、质量发现（建议立项修复，不属本阶段整改范围）

### F-1 三个诊断标签误报率 100%（严重，xfail 基线已记录）

| 标签 | 根因 | 位置 |
|---|---|---|
| OSCILLATION | IAE 相似率对白噪声不免疫（纯白噪声相似率 0.962≥0.4）；FFT 路径噪声致过零数>5 误报 | `diagnosis_engine._detect_oscillation_iae/_fft` |
| VALVE_STICTION | Choudhury NGI 阈值 0.001 对任何非高斯 OP 必然击穿（正弦 OP NGI≈0.25） | `diagnosis_engine` Choudhury 检测 |
| EXTERNAL_DISTURBANCE | CUSUM 在纯白噪声上 shift_count=10>5 阈值即误报；对慢漂/振荡无区分度 | `diagnosis_engine._detect_bias_shift` |

正常对照（白噪声、慢漂+小噪声）下三标签均 2/2 误报。建议方向：振荡检测加半周期下限（借鉴 G1 门控）；NGI 阈值提数量级或改分位数归一；扰动检测加幅度门限与方向一致性判据。召回率基线（振荡/粘滞/饱和/外扰/质量异常各 2 例注入）全部 100% 达标，修复时不得破坏。

### F-2 非有限值守护缺失（26 项 xfail）

NaN/Inf 泄漏进 9 个统计类指标 value；`statistics.pstdev` 对全 NaN/Inf 抛异常（4 个 std 指标）；ARMA 辨识入口未校验非有限值；`_clamp` 对 NaN 钳到上界满分（accuracy/stability 垃圾输入给 100 分）。建议在 `MetricCalculatorBase` 层统一加非有限点过滤 + `_clamp` NaN 守护（一处修复可消掉大半 xfail）。

### F-3 文档偏差（2 处，建议修订）

- 算法说明 §4.3.2 行内 σ 公式写 1/n（有偏），与 §4.3.4/v2.1 变更记录/FDS 的 n-1 不一致——以 ddof=1 为准（实现一致），修订行内公式
- 算法说明 §6.3 IMC 表格 Kp 分母缺 θ/2 项（代码实现与 Rivera-Morari 1986 经典一致），修订表格

### F-4 实现小偏差（1 项 xfail）

`stability.py:130 _read_pv_range` 未按契约解包列表形式标量（生产无实害，DataPlanner 不注入 pv_range）。

### F-5 设计观察

极限环门控只检测 PV，对"有极限环但 PV 噪声大"的回路可能误拒（s_a/s_b 被伪穿越拉低）——建议用真实数据回归一次门控灵敏度。

## 五、套件维护说明

- xfail 全部为 `strict=False` 基线登记，修复对应问题后应移除 xfail 标记转为正式断言
- 期望值全部手算推导写入 docstring，禁止用实现输出反推期望
- 套件已纳入全量 pytest（默认收集路径），无需额外标记

## 六、2026-08-27 振荡率口径修正与偏离登记（P0~P3）

振荡率指标算法评审（对照 GB/T 44693.2-2024 附录 F.1 / 算法说明 §4.6）发现的
问题按 P0→P3 分级修复完毕，本节登记全部口径变更与对标准/设计文档的持续偏离。

### 6.1 已修复项（均有 TDD 用例固化）

| 级别 | 问题 | 修复 | 位置 |
|---|---|---|---|
| P0 | 稳定率误扣：振荡率恒输出 min(S_A,S_B)×100，未判振荡回路（相似率 30%）被 (1−Osc) 因子扣分 | 稳定率修正仅在 details.is_oscillating=True 时生效，缺失标志回退 True 兼容历史注入 | `stability.py`；`test_stability.py` 2 用例 |
| P1 | KPI 侧缺抗噪门控，白噪声相似率 0.962 误判振荡（F-1 OSCILLATION 根因的 KPI 侧）；且与诊断侧 `min_half_period_samples=8` 门控口径不一致 | 门控下沉 KPI 侧 `calculate`，两侧白噪声同判非振荡 | `oscillation.py`；`test_oscillation.py` TestHalfPeriodGate 4 用例 + `test_diagnosis_engine.py` 一致性断言更新 |
| P1 | 振荡周期输出采样点数，非 1s 采样时单位错误（5s 采样周期 100s 输出 ~20） | 按零交叉点时间戳逐段换算秒，时间戳不可用回退平均间隔估计 | `oscillation.py _mean_half_period_seconds`；TestPeriodUnitSeconds 3 用例 |
| P2 | 无幅度门控：量程 0.2% 以下规则微振荡（与测量噪声不可区分）仍判振荡 | 特征幅度（段 IAE/段时长秒的均值）< min_amplitude_ratio×U 判非振荡，默认 0.002 | `oscillation.py`；TestAmplitudeGate 4 用例 |
| P2 | SP 阶跃跟踪暂态同型段相似率 1.0，被误判为振荡 | sp_step_exclusion_enabled=True 剔除阶跃跟踪窗（复用 stability 同款 detect_sp_tracking_windows），默认关闭零回归 | `oscillation.py`；TestSpStepExclusion 3 用例 |
| P3 | IAE 离散化漏乘 Δt：非均匀采样（缺口回补/降采样混合）下长短采样段同权 | IAE = Σ\|E_i\|·Δt_i（零阶保持点时长），均匀 1s 采样与旧实现逐位一致 | `oscillation.py _compute_iae_segments(point_durations=)`；TestIaeDtWeighting 3 用例 |

配置链：oscillation_rate 新增 `min_half_period_samples`（P1）、`min_amplitude_ratio`、
`sp_step_exclusion_enabled`、`sp_step_sigma`、`sp_tracking_window`（P2）共 5 参数，
入 `_DEFAULTS`×4 控制类型 + `PARAM_META` + `PARAM_CATEGORY`，配置页可见可调；
DB 种子行（f8a9b0c1d2e3）不含新键时由默认层兜底，无需迁移。
诊断侧 `_iae_kernel`/`_detect_oscillation_iae` 未传入 point_durations
（按采样点计权，均匀采样下与秒计权等价），两侧相似率在均匀采样下仍逐位一致。

### 6.2 对标准/设计文档的持续偏离登记（有意保留，勿当 bug 修）

| # | 偏离点 | 标准/设计文档口径 | 实现口径 | 保留理由 | 固化测试 |
|---|---|---|---|---|---|
| D-1 | 相似率末步公式 | S = 1−\|min(A′avg,Aavg)−Aavg\|/Aavg（单边） | 1−\|A′avg−Aavg\|/\|Aavg\|（对称） | 标准公式单边不对称：A′avg>Aavg 时恒为 1.0，清洗后均值上偏不受罚 | TestSymmetricSimilarity（[2.0,0.01,3.0]→0.75 手算） |
| D-2 | 非振荡时的数值输出 | 伪代码 line 27 ELSE oscillation_rate=0 | 恒输出 min(S_A,S_B)×100，is_oscillating 标志单独判定 | 保留相似率连续值供展示/趋势分析；下游误扣风险已由 P0 门控消除 | test_osc_value_is_min_sa_sb_times_100 |
| D-3 | cleaned_avg 计算方式 | 伪代码 find_min_distance_mean(cleaned) | np.mean 算术平均 | 清洗后序列已收敛，两种均值差异小于清洗效应本身；严格对齐可另行立项 | —（差异量级已在评审记录） |
| D-4 | 判定门控扩展 | 仅 S_A/S_B ≥ 0.4 | 追加半周期/幅度门控（D-4a min_half_period_samples=8；D-4b min_amplitude_ratio=0.002） | GB/T F.1 相似率法对白噪声不免疫（本报告 §四 F-1 实测 0.962）；国标未规定预处理门控，属实现自由度，且两侧（KPI/诊断）已统一 | TestHalfPeriodGate / TestAmplitudeGate |

### 6.3 行为变更注意（回归基线）

- 白噪声/高频伪振荡：KPI 侧 is_oscillating 由可 True 改为恒 False（与诊断侧对齐），
  `test_diagnosis_engine.py::test_random_noise_similarity_consistent_with_kpi`
  断言已由"两侧可有差异"改为"两侧一致判非振荡"
- 未判振荡回路的稳定率普遍回升（不再被相似率连续值扣减）；振荡回路不变
- 周期 4s@1s 采样的高频正弦不再判振荡（半周期 2 采样点 < 8，旧测试
  test_high_frequency_oscillation 已删除，由 TestHalfPeriodGate::test_high_frequency_not_detected 替代）
- 验证：全量 pytest 通过 + ruff check/format 通过（GitHub Actions CI 同口径）
