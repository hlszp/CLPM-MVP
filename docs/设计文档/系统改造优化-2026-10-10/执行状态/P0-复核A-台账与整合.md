# P0 产物非作者复核报告·A（台账报告与整合文档）

- 复核者：独立子智能体（未参与 P0 产物编写与执行包编制）
- 开工/收工 HEAD：`2c817248fc1d55e85dfd413ecfd9b4f01b9efbad`（一致）；全程只读（git 只读/Read/docker ps/inspect/exec 版本查询/ps）
- 日期：2026-10-10
- 复核对象：P0-01 四份批次报告 + P0-整合文档 §1/§2/§3

## 总判定

| 复核对象 | 判定 |
|---|---|
| 1. 批次1 IA-AUTH | **PASS** |
| 2. 批次2 CAL-STD | **PASS** |
| 3. 批次3 DIA-TUN-ENG | **REWORK**（1 条补充观察与代码不符，已由主协调修复） |
| 4. 批次4 CFG-PERF-TEST | **REWORK**（1 条计数注记矛盾，轻量勘误级，已修复） |
| 5. 整合文档 §1/§2/§3 | **REWORK**（2 条：漂移提交计数、dev 容器表述，均已修复） |

## A. 抽样独立复核（20 条：12 必抽 + 每批自选 2）——20/20 判定与报告一致

必抽：CAL-11（upsert 覆盖三落点）、CAL-05（无参级联+级联目标窗口错位）、AUTH-09（三 realtime 活链逐点）、AUTH-05（loop:* vs ADMIN-only 删除）、DIA-01（effectiveThreshold 顶层恒 None）、DIA-09（create_task 返回值被丢/身份分裂）、TUN-06（保存链六字段丢失+服务端不写版本/窗口）、ENG-04（复诊不验同回路+无理由闭环）、PERF-07（size/pageSize 错配→仅 20 回路+replay）、IA-03（latest 取当前页首条）、STD-03（六档+AR 无 MA 自证）、DOC-01（MVP 文档旧口径）。
自选：IA-12（占位报告 COMPLETED）、AUTH-02（PE 轮询 403）、CAL-08（missing 矛盾分支）、STD-02（满分区跳变）、DIA-05（ellipse 无量程缺省 100）、ENG-01（向导恒推进）、CFG-01（前后端 key 漂移 v2.2）、TEST-01（ci.yml 61-66 非阻塞）。
每条复核者均亲自开码验证行号与行为，全部与批次报告一致。

## B. 计数核对

批次1=23 ✓、批次2=14 ✓、批次3=21 ✓、批次4=29 ✓、加总 **87** ✓ 与整合文档一致。批次4 原勘误注记"以 88 条计"自相矛盾（已修复为 87）。

## C. 整合文档事实核对

通过：HEAD/基线存在性 ✓；漂移区间无 DB schema 变更（`git diff --stat 20eb321b..2c817248 -- backend/alembic backend/app/models` 为空）✓；两功能性提交影响面与批次3 声明相符 ✓；版本三件套（TD taos -V 实测 3.3.6.6/Redis 7.4.10/PG16）✓；Celery 单 master `CLPM-MVP@%h` prefork×4 + Beat ✓；§2 抽查 11 任务关键落点全部存在（两处简写可解析：02_seed_data.sql 在仓库根 db/postgresql/；workbench360 sections 子目录）✓；§3 W1~W7 路径全部存在 ✓（W8 为声明性条目）。

## REWORK 清单（均已由主协调按建议修正）

1. **批次3 补充观察第 6 条失实**：原称 schedule/event 两链"均未调用 set_celery_task_ids"——实际 `diagnosis_schedule.py:205-207`、`dispatcher.py:437-439` 两链均在 delay 后调用了该钩子，但传入自生成 ID（uuid_B）而非登记 ID（uuid_A），**钩子已挂但挂错目标**。影响：误导 P3-02 修复方向（真实修法=消费 create_task 返回值或把自生成 ID 传入 create_task，而非补调用）。DIA-09 主体判定不受影响。→ 已修正。
2. **批次4 计数勘误自错**："以 88 条计"无法从任何批次组合推出，应为 87。→ 已修正。
3. **整合 §1 漂移计数**：20eb321b..2c817248 实为 **6 提交**（2 功能性+4 部署包固化，HEAD 自身 full-1031 亦属区间），原"5 提交"漏计 HEAD。→ 已修正。
4. **整合 §1 容器状态失实**：`docker ps -a` 显示 clpm-mvp-postgres/tdengine/redis 三容器 **Up 6 days (healthy)** 持续覆盖 P0 全程；原"CLPM dev 容器当前未运行"系主协调 docker ps 输出 head 截断误判。影响 DEC-08 环境裁决前提。→ 已修正（基础设施三容器运行中、应用层本机 uvicorn+Celery）。

## D. 一致性

未发现"误导实施"级遗漏。两个轻量注记（不构成 REWORK）：① CAL-07 双读路径分叉涉及的 workbench_overview.py:112-119 未列入整合 §2 P1-02 落点（信息在批次2 报告内可查）；② 批次1 补充观察的 change_password 提交次序问题与 docstring 角色口径过时未入整合文档，属实施注意项。

## 未验边界

1. 台账 87 条仅独立抽验 20 条，其余 67 条采信报告自证（20/20 通过使报告可信度高，但不外推为全量担保）。
2. PERF-07 四个子落点（custom 不更新 latest :3078-3092 / custom 批尾无级联 :1262-1277 / fire-and-forget :1055-1062 / benchmark 计时窗 :361-391）未逐一开码。
3. 整合 §4/§5 不在本次范围；DEC-02"三套定级体系"未独立开码（由复核B 负责）。
4. 端口监听与 mock 服务状态未探测；并存容器（clm-*、clpm-sim-*）影响未评估。
5. 批次4 自报其余微漂未逐一复核；DIA-05 的 BASE 块归一化链未深挖 data_planner。
