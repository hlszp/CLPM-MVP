<script lang="ts" setup>
/**
 * 策略配置 — 标准评估任务执行策略（Tab 内嵌组件）
 *
 * 对接后端 GET/PUT /api/v1/performance/rules
 * 基于 EngineRule 表的 3 类核心策略：
 * - EVAL_CALC_CYCLE  (CALC_CYCLE):  计算周期 {"cycle_minutes": 60}
 * - DATA_FETCH_WINDOW (DATA_FETCH):  数据拉取窗口 {"window_days": 30, "sample_interval_seconds": 1}
 * - SCHEDULE_CONCURRENCY (SCHEDULE): 调度并发 {"concurrency": 16}
 *
 * 嵌入位置：评估任务模块 → "策略配置" Tab
 * 权限：ADMIN 可编辑，其他角色只读
 */
import type { MetricApi } from '#/api/metric';

import { computed, onMounted, reactive, ref } from 'vue';

import {
  Alert,
  Card,
  Form,
  FormItem,
  InputNumber,
  message,
  Select,
  Switch,
  Tag,
} from 'ant-design-vue';

import { ClpmDangerConfirmModal, ClpmPageToolbar, ClpmToolbarButton } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { getRulesApi, updateRuleApi } from '#/api/metric';

defineOptions({ name: 'MetricTaskStrategy' });

const { themeColors } = useClpmTheme();

const loading = ref(false);
const saving = ref(false);

// ============ 规则数据 ============
const rules = ref<MetricApi.RuleItem[]>([]);

/** 按 ruleCode 索引规则 */
const ruleMap = computed<Record<string, MetricApi.RuleItem>>(() => {
  const map: Record<string, MetricApi.RuleItem> = {};
  for (const r of rules.value) {
    map[r.ruleCode] = r;
  }
  return map;
});

// ============ 编辑态（按 ruleCode 分组） ============
interface CalcCycleParams {
  cycle_minutes: number;
}
interface DataFetchParams {
  window_days: number;
  sample_interval_seconds: number;
}
interface ScheduleParams {
  concurrency: number;
}

const calcCycle = reactive<CalcCycleParams>({ cycle_minutes: 60 });
const dataFetch = reactive<DataFetchParams>({
  window_days: 30,
  sample_interval_seconds: 1,
});
const schedule = reactive<ScheduleParams>({ concurrency: 10 });

/** 各规则的启用状态 */
const ruleEnabled = reactive<Record<string, boolean>>({
  EVAL_CALC_CYCLE: true,
  DATA_FETCH_WINDOW: true,
  SCHEDULE_CONCURRENCY: true,
});

// ============ 计算周期选项 ============
const calcCycleOptions = [
  { label: '5 分钟', value: 5 },
  { label: '15 分钟', value: 15 },
  { label: '30 分钟', value: 30 },
  { label: '1 小时', value: 60 },
  { label: '2 小时', value: 120 },
  { label: '6 小时', value: 360 },
  { label: '12 小时', value: 720 },
  { label: '24 小时', value: 1440 },
];

// ============ 加载策略 ============
async function loadStrategy() {
  loading.value = true;
  try {
    const data = await getRulesApi();
    rules.value = data?.items ?? [];
    // 同步编辑态
    for (const r of rules.value) {
      ruleEnabled[r.ruleCode] = r.isEnabled;
      if (r.ruleCode === 'EVAL_CALC_CYCLE' && r.params) {
        calcCycle.cycle_minutes = Number(r.params.cycle_minutes ?? 60);
      } else if (r.ruleCode === 'DATA_FETCH_WINDOW' && r.params) {
        dataFetch.window_days = Number(r.params.window_days ?? 30);
        dataFetch.sample_interval_seconds = Number(
          r.params.sample_interval_seconds ?? 1,
        );
      } else if (r.ruleCode === 'SCHEDULE_CONCURRENCY' && r.params) {
        schedule.concurrency = Number(r.params.concurrency ?? 10);
      }
    }
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

// ============ 保存确认 ============
const confirmVisible = ref(false);

/** 变更摘要 */
const changeSummary = computed(() => {
  const summary: { field: string; from: string; to: string }[] = [];
  const cc = ruleMap.value.EVAL_CALC_CYCLE;
  if (cc?.params) {
    const oldMin = Number(cc.params.cycle_minutes ?? 60);
    if (oldMin !== calcCycle.cycle_minutes) {
      summary.push({
        field: '计算周期',
        from: `${oldMin} 分钟`,
        to: `${calcCycle.cycle_minutes} 分钟`,
      });
    }
  }
  const df = ruleMap.value.DATA_FETCH_WINDOW;
  if (df?.params) {
    const oldDays = Number(df.params.window_days ?? 30);
    if (oldDays !== dataFetch.window_days) {
      summary.push({
        field: '数据拉取窗口',
        from: `${oldDays} 天`,
        to: `${dataFetch.window_days} 天`,
      });
    }
  }
  const sc = ruleMap.value.SCHEDULE_CONCURRENCY;
  if (sc?.params) {
    const oldConc = Number(sc.params.concurrency ?? 10);
    if (oldConc !== schedule.concurrency) {
      summary.push({
        field: '调度并发数',
        from: `${oldConc}`,
        to: `${schedule.concurrency}`,
      });
    }
  }
  // 启用状态变更
  for (const code of [
    'EVAL_CALC_CYCLE',
    'DATA_FETCH_WINDOW',
    'SCHEDULE_CONCURRENCY',
  ]) {
    const r = ruleMap.value[code];
    if (r && r.isEnabled !== ruleEnabled[code]) {
      summary.push({
        field: `${r.ruleName} 启用状态`,
        from: r.isEnabled ? '启用' : '禁用',
        to: ruleEnabled[code] ? '启用' : '禁用',
      });
    }
  }
  return summary;
});

const hasChange = computed(() => changeSummary.value.length > 0);

function handleSave() {
  if (!hasChange.value) {
    message.info('未检测到变更');
    return;
  }
  confirmVisible.value = true;
}

// ============ 确认保存 ============
async function confirmSave() {
  saving.value = true;
  try {
    // 按规则逐个更新
    const updates: Promise<unknown>[] = [];

    const cc = ruleMap.value.EVAL_CALC_CYCLE;
    if (cc) {
      updates.push(
        updateRuleApi(cc.ruleId, {
          params: { cycle_minutes: calcCycle.cycle_minutes },
          isEnabled: ruleEnabled.EVAL_CALC_CYCLE,
        }),
      );
    }

    const df = ruleMap.value.DATA_FETCH_WINDOW;
    if (df) {
      updates.push(
        updateRuleApi(df.ruleId, {
          params: {
            window_days: dataFetch.window_days,
            sample_interval_seconds: dataFetch.sample_interval_seconds,
          },
          isEnabled: ruleEnabled.DATA_FETCH_WINDOW,
        }),
      );
    }

    const sc = ruleMap.value.SCHEDULE_CONCURRENCY;
    if (sc) {
      updates.push(
        updateRuleApi(sc.ruleId, {
          params: { concurrency: schedule.concurrency },
          isEnabled: ruleEnabled.SCHEDULE_CONCURRENCY,
        }),
      );
    }

    await Promise.all(updates);
    message.success('策略配置已保存');
    confirmVisible.value = false;
    await loadStrategy();
  } catch {
    // 错误已由拦截器处理
  } finally {
    saving.value = false;
  }
}

onMounted(() => {
  loadStrategy();
});
</script>

<template>
  <div>
    <ClpmPageToolbar
      title="策略配置"
      subtitle="管理标准评估任务的执行周期、数据窗口与调度并发（基于引擎规则 EngineRule）"
    >
      <ClpmToolbarButton
        icon="ant-design:reload-outlined"
        :loading="loading"
        label="刷新"
        @click="loadStrategy"
      />
      <ClpmToolbarButton
        v-permission="['ADMIN']"
        icon="ant-design:save-outlined"
        variant="primary"
        :loading="saving"
        :disabled="!hasChange"
        label="保存配置"
        @click="handleSave"
      />
    </ClpmPageToolbar>

    <div class="mt-4 space-y-4">
      <!-- Beat 重启提示 -->
      <Alert
        v-if="ruleMap.EVAL_CALC_CYCLE?.warning"
        type="warning"
        show-icon
        :message="ruleMap.EVAL_CALC_CYCLE.warning"
      />

      <!-- 标准评估任务 -->
      <Card title="标准评估任务" :loading="loading">
        <template #extra>
          <Switch
            v-model:checked="ruleEnabled.EVAL_CALC_CYCLE"
            v-permission="['ADMIN']"
          />
        </template>
        <Form layout="vertical" class="pt-2">
          <div class="grid grid-cols-2 gap-4">
            <FormItem label="计算周期" name="cycle_minutes">
              <Select
                v-model:value="calcCycle.cycle_minutes"
                :options="calcCycleOptions"
                :disabled="!ruleEnabled.EVAL_CALC_CYCLE"
              />
              <span class="mt-1 block text-xs" :style="{ color: themeColors.NEUTRAL }">
                标准评估任务的执行间隔（由 EngineRule EVAL_CALC_CYCLE 配置）
              </span>
            </FormItem>
          </div>
        </Form>
      </Card>

      <!-- 数据拉取窗口 -->
      <Card title="数据拉取窗口">
        <template #extra>
          <Switch
            v-model:checked="ruleEnabled.DATA_FETCH_WINDOW"
            v-permission="['ADMIN']"
          />
        </template>
        <Form layout="vertical">
          <div class="grid grid-cols-2 gap-4">
            <FormItem label="历史数据窗口（天）" name="window_days">
              <InputNumber
                v-model:value="dataFetch.window_days"
                :min="1"
                :max="90"
                class="w-full"
                :disabled="!ruleEnabled.DATA_FETCH_WINDOW"
              />
              <span class="mt-1 block text-xs" :style="{ color: themeColors.NEUTRAL }">
                评估时拉取的历史数据天数（默认 30 天）
              </span>
            </FormItem>
            <FormItem label="采样间隔（秒）" name="sample_interval_seconds">
              <InputNumber
                v-model:value="dataFetch.sample_interval_seconds"
                :min="1"
                :max="60"
                class="w-full"
                :disabled="!ruleEnabled.DATA_FETCH_WINDOW"
              />
              <span class="mt-1 block text-xs" :style="{ color: themeColors.NEUTRAL }">
                数据采样间隔（默认 1 秒，由 DataPlanner 按需降采样）
              </span>
            </FormItem>
          </div>
        </Form>
      </Card>

      <!-- 调度策略 -->
      <Card title="调度策略">
        <template #extra>
          <Switch
            v-model:checked="ruleEnabled.SCHEDULE_CONCURRENCY"
            v-permission="['ADMIN']"
          />
        </template>
        <Form layout="vertical">
          <div class="grid grid-cols-2 gap-4">
            <FormItem label="调度并发数" name="concurrency">
              <InputNumber
                v-model:value="schedule.concurrency"
                :min="1"
                :max="100"
                class="w-full"
                :disabled="!ruleEnabled.SCHEDULE_CONCURRENCY"
              />
              <span class="mt-1 block text-xs" :style="{ color: themeColors.NEUTRAL }">
                Celery Worker 并发处理回路数（默认 10）
              </span>
            </FormItem>
            <FormItem label="回路级别优先">
              <div class="flex items-center gap-2">
                <Tag color="error">1 级</Tag>
                <Tag color="warning">2 级</Tag>
                <Tag color="processing">3 级</Tag>
                <span class="text-xs" :style="{ color: themeColors.NEUTRAL }">
                  关键回路优先调度（按 loop.importance_level）
                </span>
              </div>
            </FormItem>
          </div>
        </Form>
      </Card>
    </div>

    <!-- 高危确认弹窗（策略变更影响评估调度） -->
    <ClpmDangerConfirmModal
      v-model:open="confirmVisible"
      title="确认变更策略配置"
      action="保存"
      target="标准评估任务策略"
      :impact-scope="changeSummary.length > 0
        ? changeSummary.map((c) => `${c.field}: ${c.from} → ${c.to}`).join('；')
        : '策略配置变更'"
      rollback-tip="变更后下一次评估调度将按新策略执行；计算周期变更需重启 Celery Beat 进程才能生效"
      confirm-code="确认变更"
      confirm-code-placeholder="请输入 确认变更 以确认"
      :loading="saving"
      @confirm="confirmSave"
    />
  </div>
</template>
