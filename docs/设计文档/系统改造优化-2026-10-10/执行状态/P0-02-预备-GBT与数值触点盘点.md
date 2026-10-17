# P0-02 预备报告：GB/T 可提取性与数值语义触点盘点

- 盘点子智能体：独立只读代理（未参与执行包编写）
- 开工/收工 HEAD：`2c817248fc1d55e85dfd413ecfd9b4f01b9efbad`（一致，仓库零写入）
- 临时产物（/tmp）：`/tmp/gbt_pages/`（31 页提取图+逐页 OCR 文本）、`/tmp/gbt_ocr_full.txt`（全量 OCR 拼接）、`/tmp/gbt_extract.txt`（pdftotext 空输出证据）
- 日期：2026-10-10（P0 执行期）

## 一、GB/T 原文可提取性

### 1.1 基本事实

- PDF 存在：`docs/预研文档/GBT44693.2-2024危险化学品企业工艺平稳性第2部分：控制回路性能评估与优化技术规范上传.pdf`（33.6MB，31 页）
- **无文本层**：Producer=jsPDF 2.5.1，每页为一张 2310×3270 JPEG 扫描图；`pdftotext` 全文输出仅 31 字节（31 个换页符），**可提取比例 = 0%**
- **OCR 可用**：本机 tesseract 5 + chi_sim（psm 6）可读正文（粗估 80–90% 文字可辨识），**公式区 OCR 噪声大，公式结构必须以页面图像人工/视觉核对为准**。本报告所有公式均已用页面图像逐式核对，非 OCR 猜测。

### 1.2 页码映射与正文/附录分界

PDF 索引 N（0 基）≈ 标准页码 N−5：

| PDF 索引 | 标准页码 | 内容 |
|---|---|---|
| pg-000 | 封面 | 封面（发布 2024-09-29 / 实施 2025-04-01） |
| pg-002 | 目次 | 目次 |
| pg-004/005 | — | 前言 / 引言 |
| pg-006~pg-012 | 1~7 | **正文**（范围/术语/总体要求/基础信息/性能评估/性能诊断/性能优化） |
| pg-013 | 8 | 附录 A（资料性）数据采样周期 |
| pg-014~015 | 9~10 | **附录 B（资料性）指标计算**：B.1 自控率、B.2 有效自控率、**B.3 准确率（第 9 页）**、**B.4 快速率 + B.5 稳定率（第 10 页）** |
| pg-016 | 11 | 附录 C（资料性）权重系数，表 C.1 |
| pg-017 | 12 | 附录 D（资料性）性能定级，表 D.1 |
| pg-018 | 13 | 附录 E（资料性）关键绩效指标 |
| pg-019~022 | 14~17 | 附录 F（资料性）故障诊断指标：F.1 振荡率、F.3 饱和率、**F.4 稳态时间（第 16~17 页）**、F.5 行程指数、F.6 好值率、F.7 相关性指数 |
| pg-023+ | 18+ | 附录 G（PID 参数优化）、附录 H（评估与优化系统） |

**分界结论**：指标公式（B.1~B.5、F.x）、权重表 C.1、定级表 D.1 **全部位于（资料性）附录**，不构成正文强制要求；正文强制点只有 §6.3.1 公式(1) 骨架与 §6.3.3。

### 1.3 关键原文摘录（已视觉核对）

**正文公式(1)（第 5 页，规范性）**：
> P = (A×α + F×φ + S×γ) × R %
> —— R（有效自控率）为乘数；α/φ/γ "应采用相似权重"；A、F、S 计算公式见附录 B。

**正文 6.3.3（第 5 页，规范性）**：
> 企业应将数据异常及时段内自控率低于预设百分比的控制回路判定为回路性能最低等级。

**附录 B.3 准确率（第 9 页，资料性）**：
> A = [1 − (E‾/|E|‾) × (1 − 1/e^(|E|‾/|E|max‾))] × 100 %
> E‾=控制偏差的平均值；|E|‾=Σ|Eᵢ|/n；**|E|max‾ = Σ[max(|Eᵢ|)−|Eᵢ|]/n（偏差绝对值偏离的平均值，数据驱动基准）**；Eᵢ = PVᵢ − SPᵢ

**附录 B.4 快速率（第 10 页，资料性）**：
> F = 1/e^(T/T′) × 100 %（单一连续指数式，**无满分区、指数为 T/T′**）；T=实际稳态时间(s)，计算见附录 F.4；T′=理想稳态时间(s)

**附录 B.5 稳定率（第 10 页，资料性）**：
> S = 1/e^(σ/(0.05×U)) × (1−Osc) × 100 %；σ 分母 n−1（无偏）；**振荡修正 (1−Osc) 为无条件乘项**

**附录 C 表 C.1（第 11 页，资料性）权重**：稳定型 0.2/0.3/0.5、慢速型 0.3/0.1/0.6、快速型 0.2/0.5/0.3、逻辑型 0/0.4/0.6（α准确率/f快速率/s稳定率）——**与代码现行四类 control_type 权重完全一致**。

**附录 D 表 D.1（第 12 页，资料性）定级**：90≤P<100 一级、80≤P<90 二级、70≤P<80 三级、60≤P<70 四级、0≤P<60 五级（**分界 90/80/70/60**）。

**附录 F.4 稳态时间（第 16~17 页，资料性）**：ARMA(p,q) 完整模型（AR 系数 φ 与 MA 系数 θ 均辨识，最小二乘/极大似然等统计方法）；Green 函数递推（F.10）**含 MA 项 θⱼ**；"将单位脉冲响应函数稳定在 **5% 或 2%** 的时间 3T 或 4T 作为控制回路的实际稳态时间"。

## 二、数值语义触点盘点（DEC-02 冻结素材，当前实际值）

| # | 文件：行号 | 常量/公式现状（一句话） | 台账对应 |
|---|---|---|---|
| 1 | `backend/app/services/metric_calculator/accuracy.py:38-41` | `DEFAULT_PV_RANGE=100.0`；`_DEFAULT_E_MAX_TOLERANCE_RATIO=0.02`（工程容差=2% 量程） | STD-01 |
| 1a | accuracy.py:112-120 | `r=\|E\|‾/e_max`，`A=(1−r(1−e^−r))×100`；e_max 优先级：CONFIG 信号 > `tolerance_ratio×U`；mean_abs_error≤0 直接 100 分 | STD-01：与 B.3 双差异——①归一化基准用工程容差而非数据驱动（文件头 L13-23 注明 v2.2 于 2026-10-10 刚从该数据驱动基准改掉）；②第一因子是 r 而非国标的 E‾/\|E\|‾ |
| 2 | `backend/app/services/metric_calculator/fast_rate.py:36-43,103,177,196` | 回退默认 `ideal_settling_ratio=1.0`、`settling_tolerance=0.0`；抗扰分析默认关闭（band_sigma=2.0 等）；阈值 `T'=ideal_t×ratio×(1+tol)`；**T≤阈值→100 分（满分区）**；T>阈值→`F=e^−((T−T′)/T′)×100`；never_settles 以 Green 窗口长（≤3600×采样间隔）作 actual_t 代入衰减 | STD-02：B.4 原文为连续 `1/e^(T/T′)` 无满分区、指数不同 |
| 3 | `backend/app/services/metric_calculator/stability.py:50-62,150,156` | `DECAY_RATIO=0.05`、`BAND_RATIO=0.01`、`SP_STEP_SIGMA=3.0`、`SP_TRACKING_WINDOW=60`、`MIN_POINTS=2`；σ 用 ddof=1 无偏（与 B.5 一致）；**dead_signal 判定：`std_error==0.0 and mean_error!=0.0` → INCONCLUSIVE**，σ=0 且均值=0 保持满分 | CAL-02 反例不命中该分支，问题仍在 |
| 3a | stability.py:206-211 | 振荡修正仅在 `is_oscillating=True` 时乘 `(1−Osc/100)`（P0 修复）；`band_in_score_enabled=True` 时带内率直接作分值（石化惯例口径） | 国标 B.5 差异：原文无条件乘 (1−Osc)；band_in 模式国标无 |
| 4 | `backend/app/services/metric_calculator/effective_auto.py:28-38,114-122` | `DEFAULT_E_MAX_RATIO=0.05`、`EPSILON=2.0`、OP 默认 0~100；**pv/sp 缺失时 `is_deviation_ok=True` 自动成立、op 缺失不判饱和** | CAL-03：MODE_HF 契约 tags=`["mode","op"]` |
| 5 | `backend/app/services/confidence_evaluator.py:55-64,282-285` | `DEFAULT_WEIGHTS={a:0.2,f:0.3,s:0.5}`（STABLE）；四类 control_type 权重与附录 C 表 C.1 **完全一致**；可信度阈值 A/B/C/D=0.95/0.80/0.60/0.20 | 权重 profile 与国标附录 C 一致 |
| 5a | confidence_evaluator.py:419,423-424 | `P=(A·a+F·f+S·s)/(a+f+s)×100 × R/100`——**比正文公式(1) 多除以权重和**（表 C.1 权重和=1 时数值一致） | STD-01 相邻差异，需登记 |
| 5b | confidence_evaluator.py:340-342,439 | 零权重必需检查 `if weight<=0: continue` 跳过；**L439 无条件下标 `metric_results["accuracy_rate"]`——a=0(LOGIC) 且 accuracy 缺失时 KeyError** | CAL-01：现状仍在（P1-01 修） |
| 6 | `backend/app/services/workbench_precalc.py:73-78,103-115` | `score_to_status` 六档：None→INCONCLUSIVE；≥90 EXCELLENT、≥75 GOOD、≥60 FAIR、≥40 POOR、<40 CRITICAL；展示桶 `_LEVEL_BUCKETS` 优(≥90)/良(75-90)/中(60-75)/差(<60) | STD-03a：国标附录 D 五级 90/80/70/60——命名与分界均不同，**不得同名混用** |
| 7 | `backend/app/tasks/arma.py:30-35,71,113` | `DEFAULT_AR_ORDER=2`、`DEFAULT_MA_ORDER=1`（**常量保留但 MA 未实现**）、`SETTLING_THRESHOLD=0.05`（固定 5%，无 2% 选项）、`MAX_GREEN_FUNC_LENGTH=3600`、`MIN_DATA_POINTS=30`；`fit_ar_model`=Yule-Walker；Green 函数=**纯 AR(p) 特征根解析解+递推回退，无 MA 项** | STD-03b：国标 F.4 为 ARMA(p,q) 完整 Green（含 θⱼ）；判稳阈值国标 5%**或**2% 双选项 |

## 三、P0-02 冻结文档骨架草案

### 3.1 产品 profile vs 标准参考 profile 双栏对照表（格式）

| 项目 | 产品 profile（现行代码冻结值） | 标准参考 profile（GB/T 原文，页码） | 差异定性 |
|---|---|---|---|
| 准确率 | r=\|E\|‾/e_max，A=(1−r(1−e^−r))×100，e_max=2%量程或 CONFIG 覆盖 | A=[1−(E‾/\|E\|‾)(1−1/e^(\|E\|‾/\|E|max‾))]×100（B.3，第9页） | 结构性差异（归一化基准+第一因子） |
| 快速率 | 分段：T≤T′×ratio×(1+tol)→100；否则 e^−((T−T′)/T′)×100 | 连续 1/e^(T/T′)×100（B.4，第10页） | 结构性差异（满分区+指数） |
| 稳定率 | 1/e^(σ/(0.05U))×(1−Osc[仅is_oscillating])×100；σ 无偏 ddof=1 | 1/e^(σ/(0.05U))×(1−Osc)×100（B.5，第10页） | 振荡修正条件化差异；σ 口径一致 |
| 有效自控率 | R=有效时长/总时长；缺 pv/sp 时偏差条件自动成立 | AutoEffectiveTime/ALLTime（B.2，第9页） | 数据前提差异（MODE_HF 信号集） |
| 综合评分 | (A·a+F·f+S·s)/(a+f+s)×R/100 | (A·α+F·φ+S·γ)×R 公式(1)（第5页，正文） | 归一化分母差异 |
| 权重表 | 四类 0.2/0.3/0.5 等（confidence_evaluator L55-64） | 表 C.1 完全相同（第11页） | **一致** |
| 治理等级 | 六档 90/75/60/40（EXCELLENT~CRITICAL） | 五级 90/80/70/60（表 D.1，第12页） | 分界与命名均不同 |
| 稳态时间 | AR(2) Yule-Walker + 纯 AR Green(5%)，窗口≤3600 | ARMA(p,q) 完整 Green(5%或2%)（F.4，第16-17页） | 模型近似差异（AR+Green） |
| 强制条款映射 | 待登记（数据异常/低自控→最低等级，正文 6.3.3） | §6.3.3（第5页，规范性） | 需映射现有最低级语义 |

### 3.2 结果账本 calculation_result_record 字段级契约清单

| 字段 | 类型 | 语义 | 可空 | 备注 |
|---|---|---|---|---|
| recordId | UUID PK | 不可变记录身份 | 否 | 追加式，只增不改 |
| logicalRunId | UUID | 逻辑运行身份 | 否 | 同任务重试复用；显式重评生成新值 |
| objectKind | enum(LOOP/NODE) | 对象类别 | 否 | 唯一键成员 |
| objectId | string | 回路或节点 ID | 否 | 唯一键成员 |
| tsStart / tsEnd | timestamptz(UTC) | 分析窗 **[tsStart, tsEnd) 半开** | 否 | 唯一键成员×2 |
| sourceRecordId | UUID FK | 重评来源记录 | **是** | 显式重评时关联，不覆盖失败证据 |
| algorithmVersion | string | 算法版本 | 否 | 未知旧算法=LEGACY_UNVERIFIABLE |
| configRevision | string | 配置版本 | 否 | |
| datasetSnapshotId | UUID | 输入包引用 | **是**（历史/P1 期可空） | 可空时明确"不可完整复现" |
| status | enum(COMPLETED/FAILED…) | 终态 | 否 | FAILED 终态不可变；暂态写 attempt 日志 |
| payload | jsonb | 完整结果+指标分母 | 否 | 节点记录含参与 loop recordIds 及各指标分母 |
| createdAt | timestamptz | 创建时间 | 否 | |

**唯一键**：`(logicalRunId, objectKind, objectId, tsStart, tsEnd)`。配套契约：兼容投影（hourly/latest/node 表）加 `resultRecordId`、同事务切换、旧 ID→recordId 映射、迟到写入不反向覆盖当前投影、重试终点与幂等键在 P0 冻结。

### 3.3 不可变输入包（NPZ+manifest）字段级契约清单

- **元数据表** `calculation_dataset_snapshot`：snapshotId、绑定 datasetRef（绑定身份≠数值身份）、inputHash（内容 SHA256）、窗口、创建引用、保留类别（NORMAL/FROZEN）、resultRecordId 关联
- **NPZ 数组**（NumPy 格式，禁 pickle）：时间轴、各信号值、质量码、逐槽 mask、dtype 显式记录
- **JSON manifest**：单位/量程、tag 绑定、窗口、网格与预处理版本、schemaVersion；内容 SHA256 涵盖上述全部
- **写入次序**：先原子写文件+核 hash，再提交 DB 引用；未引用残留由隔离清理任务按保留契约处理
- **不变式**：相同绑定但值/质量改变→不同 inputHash；重放读原包不重查当前源
- **访问与复现**：仅授权人工离线复现；下载继承回路 scope；复现绑定代码 SHA+依赖锁+构建 digest+受控执行配方；损坏/过期/缺构建→"不可完整复现"
- **保留**（DEC-10）：普通包 90 天默认；被证据引用固定保留；共享存储预算与保留责任人在 P0-02 冻结

### 3.4 动态算子登记表（格式 + 现状初值；正式值 P0-02 逐算子核）

| 算子 | 频带/采样前提 | 抗混叠前提 | 最大 gap | 最短有效段 | 多段归并 | 不足拒绝 |
|---|---|---|---|---|---|---|
| gate（统一门禁） | — | — | gap_ratio≤0.3（`gate.py:16 MAX_GAP_RATIO=0.3`） | MIN_DATA_POINTS=32（L14） | — | DATA_INSUFFICIENT |
| oscillation | 需等距网格；rFFT 主峰 fs/n | 现无显式抗混叠声明（待登记） | 待核 | 窗完整周期数≥peak_idx | 待核（IAE 半周期门控 _MIN/_MAX_RATIO） | 待核 |
| stiction | 1s 采样默认 | 待核 | 待核 | 待核 | ellipse 分段 stiction_segments 计数 | 待核 |
| sensor | 待核 | — | 待核 | MIN_DATA_POINTS=32 | frozen_window=300/frozen_eps=1e-4/frozen_ratio=0.2/noise_ratio=3.0/noise_segment=0.5 | 待核 |
| disturbance | CUSUM | — | 待核 | 待核 | — | 待核（CUSUM 未排 SP 阶跃=DIA-06） |
| saturation | — | — | — | — | — | 待核 |
| tuning（slow response） | 采样间隔参数化 | — | 待核 | 阶跃后响应段 | expected τ 按回路类型默认表 FLOW=10/PRESSURE=30/LEVEL=120/TEMP=600/ANALYSIS=900/OTHER=60 秒（`tuning.py:27-34`） | 响应不足须显式原因 |
| classification/fusion | — | — | — | 次分类置信度≥0.6（`classification.py:74`） | odds 乘积融合（含同源重复计数=DIA-07） | 全部 skip 不得产出健康（DIA-04） |

### 3.5 独立 oracle 清单（每个核心公式一行）

| 公式 | 独立计算方式 | 容差建议 |
|---|---|---|
| 准确率 A=(1−r(1−e^−r))×100 | 手算三点：r=0→100、r=1→(1−0.6321)×100=36.79、r→∞→clamp 0 | ±0.01 |
| 快速率 F=e^−((T−T′)/T′)×100 | 手算：T=T′→100、T=2T′→e^−1×100=36.79、never_settles 窗口代入 | ±0.01 |
| 稳定率 S=1/e^(σ/(d·U))×(1−Osc)×100 | numpy 独立实现 σ(ddof=1) 对照 | ±0.01 |
| 综合评分 P=(A·a+F·f+S·s)/(a+f+s)×R/100 | 手算 100/NULL 等权、A=100,F=100,S=100,R=50→50 等反例 | 精确相等 |
| AR Green 函数 G(k)=−ΣaᵢG(k−i) | 解析解 vs 递推双实现互证 + scipy.signal 独立脉冲响应 | 1e-9 |
| 治理等级 score_to_status | 边界值表驱动：89.99/90/74.99/75/59.99/60/39.99/40/None | 精确相等 |
| 权重聚合 sum(wᵢ·vᵢ)/sum(wᵢ for valid) | 独立 SQL/python 重算（CAL-04 反例：NULL 不留分母） | 精确相等 |

验收要求（03 P0-02）：**不以现有实现生成期望值再验证自己**；标准正文/资料性附录/产品差异分别登记。

### 3.6 三分类清单

**A. 可直接由代码冻结**：权重表（另经附录 C 交叉验证一致）、可信度阈值 0.95/0.80/0.60/0.20、治理等级六档（对内口径冻结）、accuracy 工程容差 0.02+CONFIG 覆盖链、fast_rate ratio=1.0/tolerance=0.0、stability 参数组、effective_auto 参数组、AR 阶数 2/Green 窗 3600/判稳 5%、诊断算子门槛常量、综合评分缺失语义、结果账本/输入包字段契约。

**B. 需 PDF 原文映射**（已定位并视觉核对，页码见 1.2/1.3）：B.3 准确率双比值结构 vs 产品单比值+2% 容差（**注意 v2.2 修正恰好移除了与 B.3 一致的数据驱动基准**）、B.4 快速率连续指数 vs 分段满分区、B.5 振荡修正条件化、B.2 有效自控"控制有效"语义、正文公式(1) 无归一化分母 vs 产品 /(a+f+s)、附录 D 五级 vs 产品六档、附录 F.4 ARMA(p,q) 完整 Green vs AR(2) 近似、正文 6.3.3 强制条款的产品映射。

**C. 需用户裁决**：
- **DEC-02**（细分裁决点）：①准确率归一化基准维持工程容差 2% 还是回归 B.3 数据驱动（v2.2 刚改，回退即数值语义变更）②快速率满分区与合法跳变是否接受③振荡修正是否恢复无条件乘④AR+Green 近似声明保留还是补 MA⑤治理等级双套展示需求（禁止同名混用）
- **DEC-06**（P3-05）：闭环证据三分类 + 带理由人工验收 + actualRollbackConfirmed 独立
- **DEC-09**（P0-02、P1-03）：latest 语义=最新业务窗口的标准评估；custom 不更新 latest；迟到历史不冒充当前
- **DEC-10**（P0-02 冻结字段/保留责任）：追加式账本+当前投影；输入包 90 天默认；共享存储预算与受控旧构建保留责任主体

**关键风险提示**：PDF 无文本层意味着正式冻结文档中所有"标准原文"引用必须注明页码+人工核对方式；不可引用旧合规报告替代原文核对。
