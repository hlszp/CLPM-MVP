<script setup lang="ts">
/**
 * 三性判定说明弹窗（2026-10-05：让用户直观了解回路为什么可评估/可诊断/可整定）
 *
 * 口径来源：backend/app/services/loop_fitness.py（数据红线 + 7 原子 tag +
 * 三性映射）与三模块消费行为；2026-10-05 用户裁决更新：
 * - 可评估性：仅 L0 限制（L1 手动主导取消）
 * - 可诊断性：全档位放行（L0 也改警告放行，引擎给正式"数据不足"结论）
 * - 可整定性：L0/L1 阻断，L2/L3 提示放行
 * 阈值默认值可在「配置 → 指标配置」调整（sys_config fitness.*）。
 */
import { Modal, Table } from 'ant-design-vue';

const open = defineModel<boolean>('open', { default: false });

const gateRows = [
  { cond: '窗口内有效数据点', limit: '≥ 32 点' },
  { cond: '数据可信度', limit: '非 E 级' },
  { cond: '断点比例（缺口/应有点数）', limit: '≤ 30%' },
];

const tagColumns = [
  { dataIndex: 'tag', title: '判定条件', width: 220 },
  { dataIndex: 'threshold', title: '默认阈值（可配置）', width: 200 },
];

const tagRows = [
  { tag: '手动主导（MANUAL_DOMINANT）', threshold: '手动模式时间 > 80%' },
  { tag: '低自控率（LOW_AUTO_RATE）', threshold: '自控率 < 20%' },
  { tag: 'OP 严重饱和（OP_SATURATED）', threshold: 'OP 距量程限 ≤2% 且 >30% 时间' },
  { tag: 'SP-PV 持续大偏差（SP_PV_DEVIATION）', threshold: '|SP−PV| > 量程 10% 且 >30% 时间' },
  { tag: '无有效激励（NO_EXCITATION）', threshold: 'OP 变化范围 < 量程 2%' },
  { tag: '响应极弱（WEAK_RESPONSE）', threshold: 'PV 对 OP 增益 < 0.05' },
];

const mapColumns = [
  { dataIndex: 'cond', title: '命中条件', width: 200 },
  { dataIndex: 'assess', title: '可评估性', width: 96, align: 'center' as const },
  { dataIndex: 'diagnose', title: '可诊断性', width: 96, align: 'center' as const },
  { dataIndex: 'tune', title: '可整定性', width: 96, align: 'center' as const },
];

const L = (v: string) => v;

const mapRows = [
  {
    cond: '数据红线未过（上述三条任一失败）',
    assess: L('L0 不计分'),
    diagnose: L('L0 警告放行'),
    tune: L('L0 阻断'),
  },
  {
    cond: '手动主导 / 低自控率',
    assess: L('L4 参与评估'),
    diagnose: L('L1 警告放行'),
    tune: L('L1 阻断'),
  },
  {
    cond: 'OP 饱和 / SP-PV 大偏差',
    assess: L('L4 参与评估'),
    diagnose: L('L2 警告放行'),
    tune: L('L2 提示放行'),
  },
  {
    cond: '无激励 / 弱响应',
    assess: L('L4 参与评估'),
    diagnose: L('L3 提示放行'),
    tune: L('L3 提示放行'),
  },
  {
    cond: '全部未命中',
    assess: L('L4 全开'),
    diagnose: L('L4 全开'),
    tune: L('L4 全开'),
  },
];
</script>

<template>
  <Modal
    v-model:open="open"
    footer="null"
    title="回路可评估 / 可诊断 / 可整定 —— 判定条件说明"
    width="720px"
  >
    <section class="fr-sec">
      <h4>① 数据红线（三性共同前提）</h4>
      <p class="fr-sec__desc">
        以下三条任一不满足 → 该回路判定为 <b>L0（数据严重不足）</b>：
      </p>
      <ul class="fr-list">
        <li v-for="r in gateRows" :key="r.cond">
          {{ r.cond }}：<b>{{ r.limit }}</b>
        </li>
      </ul>
      <p class="fr-sec__note">
        L0 回路请到「配置 → 数据检查 → 历史数据导入」补齐数据后自动恢复。
      </p>
    </section>

    <section class="fr-sec">
      <h4>② 七个判定条件（原子 tag）</h4>
      <Table
        :columns="tagColumns"
        :data-source="tagRows"
        :pagination="false"
        size="small"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'threshold'">
            <span class="text-xs">{{ record.threshold }}</span>
          </template>
        </template>
      </Table>
    </section>

    <section class="fr-sec">
      <h4>③ 三性映射与各模块准入</h4>
      <Table
        :columns="mapColumns"
        :data-source="mapRows"
        :pagination="false"
        size="small"
      />
      <p class="fr-sec__note">
        口径（2026-10-05 裁决）：评估仅数据红线限制；诊断全档位可发起（L0~L2
        附带条件警告，L0 可能产出「数据不足」结论）；整定 L0/L1
        阻断、L2/L3 提示放行。各档徽标显示在回路工作台、诊断概览、整定总览。
      </p>
    </section>
  </Modal>
</template>

<style scoped>
.fr-sec {
  margin-bottom: 16px;
}

.fr-sec h4 {
  margin-bottom: 6px;
  font-size: 13px;
  font-weight: 600;
}

.fr-sec__desc {
  margin-bottom: 4px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.fr-sec__note {
  padding-top: 6px;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

.fr-list {
  padding-left: 18px;
  font-size: 12px;
}

.fr-list li {
  margin-bottom: 2px;
}
</style>
