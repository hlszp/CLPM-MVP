# 控制回路秒级演示数据

本目录包含 CLPM 原型演示用的控制回路秒级模拟数据。

该数据是独立演示资产，不依赖 `prototype/` 应用，也不修改任何原型系统源代码。

## 文件清单

| 文件 | 用途 |
|---|---|
| `generate-control-loop-demo-data.mjs` | 确定性数据生成脚本，重复运行输出一致 |
| `control_loop_second_level_24loops_1h.csv` | 秒级长表格式时序数据 |
| `loops_metadata.json` | 回路元数据、字段定义和数据集契约 |
| `events.csv` | 模式切换、质量变化、SP 变化等事件标记 |
| `dataset_summary.json` | 数据集规模和场景汇总 |

## 数据规模

| 项目 | 值 |
|---|---|
| 回路数 | 24 |
| 时间范围 | `2026-06-16T08:00:00+08:00` 至 `2026-06-16T09:00:00+08:00` |
| 采样间隔 | 1 秒 |
| 每回路样本点 | 3601 |
| 总数据行数 | 86424 |

## CSV 字段

| 字段 | 含义 |
|---|---|
| `timestamp` | 带 `+08:00` 时区的时间戳 |
| `second` | 距离样本窗口开始的秒偏移 |
| `loop_id` | 稳定回路 ID |
| `loop_tag` | DCS 风格回路位号 |
| `unit_name` | 装置名称 |
| `loop_group` | 回路组 |
| `control_type` | 回路类型：flow、level、temperature、pressure、composition |
| `scenario` | 演示场景 |
| `pv` | PV，过程测量值 |
| `sp` | SP，设定值 |
| `op` | OP，阀位/控制器输出百分比 |
| `mode` | MODE，控制方式，如 `AUTO`、`MAN`、`CAS`、`UNKNOWN` |
| `p` | P，PID 比例参数 |
| `i` | I，PID 积分参数，单位秒 |
| `d` | D，PID 微分参数，单位秒 |
| `engineering_unit` | PV/SP 工程单位 |
| `quality` | 数据质量标记 |
| `event_marker` | 可选事件标记 |

## 演示场景

| 场景 | 用途 |
|---|---|
| `normal` | 正常回路，含小幅 SP 变化 |
| `oscillation` | 振荡回路 |
| `valve_stiction` | 阀门粘滞，OP 阶梯化变化 |
| `manual_mode` | 长时间手动模式 |
| `data_quality_issue` | BAD/FROZEN 数据质量片段 |
| `disturbance` | 工艺扰动及恢复过程 |
| `tuning_candidate` | 可用于 1 条整定样例演示 |

## 重新生成

在本目录运行：

```bash
node generate-control-loop-demo-data.mjs
```

生成脚本是确定性的，重复运行会得到相同数据。

## 边界说明

这是模拟演示数据，不是真实 DCS 数据，不能用于安全、生产操作或算法有效性声明。
