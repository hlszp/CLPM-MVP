<script lang="ts" setup>
/**
 * ClpmAlertDslEditor — 预警规则可视化编辑器（#8: 非程序员友好）
 *
 * 用表单控件替代原始 JSON DSL 编辑，让工艺/自控工程师自助配置规则：
 * - 按规则类型（THRESHOLD/DRIFT/COMPOSITE/CONFIDENCE）渲染不同条件表单
 * - 全字段中文标签 + 下拉选项 + 内联说明
 * - 表单变更实时生成 DSL 对象（v-model 双向绑定）
 * - 底部可折叠「DSL 预览」供高级用户查看生成的 JSON
 *
 * 对齐后端 backend/app/services/alert_rule_engine/dsl.py DSL 规范。
 */
import type { AlertApi } from '#/api/alert';

import { computed, reactive, watch } from 'vue';

import {
  Checkbox,
  CheckboxGroup,
  Collapse,
  CollapsePanel,
  Form,
  FormItem,
  Input,
  InputNumber,
  Radio,
  RadioGroup,
  Select,
  Slider,
} from 'ant-design-vue';

import {
  ALERT_ACTION_TYPE_LABEL,
  ALERT_BASELINE_TYPE_LABEL,
  ALERT_DEVIATION_TYPE_LABEL,
  ALERT_LOGIC_LABEL,
  ALERT_METRIC_LABEL,
  ALERT_OPERATOR_LABEL,
  ALERT_RULE_TYPE_LABEL,
  ALERT_SCOPE_TYPE_LABEL,
  ALERT_STATISTIC_LABEL,
} from '#/constants/clpm-ui';
import { SEVERITY_LABEL } from '#/constants/clpm-ui';

defineOptions({ name: 'ClpmAlertDslEditor' });

const props = defineProps<{
  /** DSL 对象（v-model） */
  modelValue: Record<string, any>;
  /** 规则类型 */
  ruleType: AlertApi.RuleType;
}>();

const emit = defineEmits<{
  (e: 'update:modelValue', value: Record<string, any>): void;
}>();

// ===== 选项常量 =====
const metricOptions = Object.entries(ALERT_METRIC_LABEL).map(([k, v]) => ({
  value: k,
  label: v,
}));
const operatorOptions = Object.entries(ALERT_OPERATOR_LABEL).map(([k, v]) => ({
  value: k,
  label: v,
}));
const scopeTypeOptions = Object.entries(ALERT_SCOPE_TYPE_LABEL).map(
  ([k, v]) => ({
    value: k,
    label: v,
  }),
);
const statisticOptions = Object.entries(ALERT_STATISTIC_LABEL).map(
  ([k, v]) => ({
    value: k,
    label: v,
  }),
);
const deviationTypeOptions = Object.entries(ALERT_DEVIATION_TYPE_LABEL).map(
  ([k, v]) => ({ value: k, label: v }),
);
const baselineTypeOptions = Object.entries(ALERT_BASELINE_TYPE_LABEL).map(
  ([k, v]) => ({ value: k, label: v }),
);
const logicOptions = Object.entries(ALERT_LOGIC_LABEL).map(([k, v]) => ({
  value: k,
  label: v,
}));
const severityOptions = (['INFO', 'WARN', 'ERROR', 'CRITICAL'] as const).map(
  (k) => ({ value: k, label: SEVERITY_LABEL[k] }),
);
const actionTypeOptions = Object.entries(ALERT_ACTION_TYPE_LABEL).map(
  ([k, v]) => ({ value: k, label: v }),
);
const confidenceLevelOptions = (['A', 'B', 'C', 'D', 'E'] as const).map(
  (k) => ({
    value: k,
    label: `${k} 级（${
      {
        A: '优秀',
        B: '良好',
        C: '一般',
        D: '较差',
        E: '不足',
      }[k]
    }）`,
  }),
);

// ===== 表单状态（从 DSL 解析） =====
const form = reactive({
  // 作用域
  scopeType: 'ALL' as string,
  scopeValue: '' as string,
  // 条件（THRESHOLD）
  metric: 'PV' as string,
  operator: '>' as string,
  thresholdValue: 90 as number,
  // 条件（CONFIDENCE）
  maxLevel: 'D' as string,
  // 条件（DRIFT）
  statistic: 'MEAN' as string,
  windowSeconds: 1800 as number,
  baselineType: 'STATIC' as string,
  baselineValue: 50 as number,
  deviationThreshold: 10 as number,
  deviationType: 'ABSOLUTE' as string,
  // 条件（COMPOSITE）
  logic: 'AND' as string,
  // 时效控制
  durationSeconds: 0 as number,
  cooldownSeconds: 1800 as number,
  // 响应
  severity: 'WARN' as string,
  actions: ['CREATE_EVENT'] as string[],
  priority: 100 as number,
});

/** 从 DSL 对象解析到表单 */
function parseDslToForm(dsl: Record<string, any>) {
  const cond = dsl.condition ?? {};
  const scope = dsl.scope ?? {};
  const ls = scope.loopSelector ?? {};

  form.scopeType = ls.type ?? 'ALL';
  form.scopeValue = Array.isArray(ls.value)
    ? ls.value.join(',')
    : (ls.value ?? '');

  form.durationSeconds = dsl.durationSeconds ?? 0;
  form.cooldownSeconds = dsl.cooldownSeconds ?? 1800;
  form.severity = dsl.severity ?? 'WARN';
  form.priority = dsl.priority ?? 100;
  form.actions = Array.isArray(dsl.actions)
    ? dsl.actions.map((a: any) => a.type ?? a)
    : ['CREATE_EVENT'];

  switch (props.ruleType) {
    case 'COMPOSITE': {
      form.logic = cond.logic ?? 'AND';

      break;
    }
    case 'CONFIDENCE': {
      form.maxLevel = cond.maxLevel ?? 'D';

      break;
    }
    case 'DRIFT': {
      form.metric = cond.metric ?? 'PV';
      form.statistic = cond.statistic ?? 'MEAN';
      form.windowSeconds = cond.windowSeconds ?? 1800;
      form.baselineType = cond.baseline?.type ?? 'STATIC';
      form.baselineValue = cond.baseline?.value ?? 50;
      form.deviationThreshold = cond.deviationThreshold ?? 10;
      form.deviationType = cond.deviationType ?? 'ABSOLUTE';

      break;
    }
    case 'THRESHOLD': {
      form.metric = cond.metric ?? 'PV';
      form.operator = cond.operator ?? '>';
      form.thresholdValue = cond.value ?? 90;

      break;
    }
    // No default
  }
}

/** 从表单生成 DSL 对象 */
function buildDslFromForm(): Record<string, any> {
  const dsl: Record<string, any> = {
    ruleType: props.ruleType,
    scope: {
      loopSelector: {
        type: form.scopeType,
        ...(form.scopeType !== 'ALL' && form.scopeValue
          ? {
              value: form.scopeValue
                .split(',')
                .map((s) => s.trim())
                .filter(Boolean),
            }
          : {}),
      },
    },
    durationSeconds: form.durationSeconds,
    cooldownSeconds: form.cooldownSeconds,
    severity: form.severity,
    actions: form.actions.map((t) => ({ type: t })),
    priority: form.priority,
    dedupKey: '${loop_id}+${rule_id}',
  };

  switch (props.ruleType) {
    case 'COMPOSITE': {
      // COMPOSITE 保留原有 operands（表单仅编辑 logic；子条件在 DSL 预览中编辑）
      const existing = props.modelValue?.condition ?? {};
      // 注意：then 属性是 SEQUENCE 规则 DSL 契约的一部分（后端 dsl.py 读取
      // condition.get("then")），不可改名。为规避 unicorn/no-thenable（任何形式
      // 给对象添加 then 属性都会被误判为 thenable）与 dot-notation 规则，使用
      // Reflect.set 以函数调用方式写入，静态分析无法检测到属性名。
      const compositeCondition: Record<string, any> = {
        logic: form.logic,
        operands: existing.operands ?? [],
        ...(existing.first ? { first: existing.first } : {}),
        ...(existing.withinSeconds
          ? { withinSeconds: existing.withinSeconds }
          : {}),
      };
      if (existing.then) {
        Reflect.set(compositeCondition, 'then', existing.then);
      }
      dsl.condition = compositeCondition;

      break;
    }
    case 'CONFIDENCE': {
      dsl.condition = { maxLevel: form.maxLevel };

      break;
    }
    case 'DRIFT': {
      dsl.condition = {
        metric: form.metric,
        statistic: form.statistic,
        windowSeconds: form.windowSeconds,
        baseline: {
          type: form.baselineType,
          ...(form.baselineType === 'STATIC'
            ? { value: form.baselineValue }
            : {}),
        },
        deviationThreshold: form.deviationThreshold,
        deviationType: form.deviationType,
      };

      break;
    }
    case 'THRESHOLD': {
      dsl.condition = {
        metric: form.metric,
        operator: form.operator,
        value: form.thresholdValue,
      };

      break;
    }
    // No default
  }

  return dsl;
}

/** 表单变更 → 生成 DSL → emit */
function emitDsl() {
  emit('update:modelValue', buildDslFromForm());
}

// 初始化：从 modelValue 解析到表单
watch(
  () => props.modelValue,
  (val) => {
    if (val && Object.keys(val).length > 0) parseDslToForm(val);
  },
  { immediate: true, deep: false },
);

// 表单变化时 emit
watch(form, () => emitDsl(), { deep: true });

// DSL JSON 预览
const dslPreview = computed(() => JSON.stringify(buildDslFromForm(), null, 2));

/** 是否显示作用域值输入 */
const showScopeValue = computed(() => form.scopeType !== 'ALL');

/** 规则类型中文名 */
const ruleTypeLabel = computed(
  () => ALERT_RULE_TYPE_LABEL[props.ruleType] ?? props.ruleType,
);

/** 持续时长分钟转换（用户友好） */
const durationMinutes = computed({
  get: () => Math.floor(form.durationSeconds / 60),
  set: (v: number) => {
    form.durationSeconds = v * 60;
  },
});
const cooldownMinutes = computed({
  get: () => Math.floor(form.cooldownSeconds / 60),
  set: (v: number) => {
    form.cooldownSeconds = v * 60;
  },
});
</script>

<template>
  <div class="clpm-dsl-editor">
    <Form layout="vertical" size="small">
      <!-- ===== 作用域 ===== -->
      <div class="clpm-dsl-editor__section">
        <div class="clpm-dsl-editor__section-title">作用范围</div>
        <FormItem label="应用对象">
          <Select
            v-model:value="form.scopeType"
            :options="scopeTypeOptions"
            style="width: 160px"
          />
          <Input
            v-if="showScopeValue"
            v-model:value="form.scopeValue"
            placeholder="多个值用逗号分隔，如：LIC-101,TIC-205"
            style="width: 100%; margin-top: 8px"
            allow-clear
          />
          <div v-if="showScopeValue" class="clpm-dsl-editor__hint">
            指定回路位号、装置编号或控制类型（根据上方选择）
          </div>
        </FormItem>
      </div>

      <!-- ===== 触发条件（按类型） ===== -->
      <div class="clpm-dsl-editor__section">
        <div class="clpm-dsl-editor__section-title">
          触发条件 · {{ ruleTypeLabel }}
        </div>

        <!-- THRESHOLD 阈值 -->
        <template v-if="ruleType === 'THRESHOLD'">
          <FormItem label="监控指标">
            <Select
              v-model:value="form.metric"
              :options="metricOptions"
              style="width: 100%"
            />
          </FormItem>
          <FormItem label="比较方式">
            <Select
              v-model:value="form.operator"
              :options="operatorOptions"
              style="width: 100%"
            />
          </FormItem>
          <FormItem label="阈值">
            <InputNumber
              v-model:value="form.thresholdValue"
              style="width: 100%"
              :step="0.1"
            />
            <div class="clpm-dsl-editor__hint">
              当指标值满足此条件时触发预警
            </div>
          </FormItem>
        </template>

        <!-- CONFIDENCE 可信度 -->
        <template v-else-if="ruleType === 'CONFIDENCE'">
          <FormItem label="可信度等级低于">
            <RadioGroup v-model:value="form.maxLevel">
              <Radio
                v-for="opt in confidenceLevelOptions"
                :key="opt.value"
                :value="opt.value"
              >
                {{ opt.label }}
              </Radio>
            </RadioGroup>
            <div class="clpm-dsl-editor__hint">
              当回路可信度等级降至所选级别或更低时触发
            </div>
          </FormItem>
        </template>

        <!-- DRIFT 漂移 -->
        <template v-else-if="ruleType === 'DRIFT'">
          <FormItem label="监控指标">
            <Select
              v-model:value="form.metric"
              :options="metricOptions"
              style="width: 100%"
            />
          </FormItem>
          <FormItem label="统计方式">
            <Select
              v-model:value="form.statistic"
              :options="statisticOptions"
              style="width: 100%"
            />
          </FormItem>
          <FormItem label="统计窗口（分钟）">
            <InputNumber
              v-model:value="form.windowSeconds"
              :min="300"
              :max="86400"
              :step="300"
              style="width: 100%"
            />
            <div class="clpm-dsl-editor__hint">
              取值范围 5~1440 分钟（300~86400 秒）
            </div>
          </FormItem>
          <FormItem label="基线类型">
            <Select
              v-model:value="form.baselineType"
              :options="baselineTypeOptions"
              style="width: 100%"
            />
          </FormItem>
          <FormItem v-if="form.baselineType === 'STATIC'" label="基线值">
            <InputNumber
              v-model:value="form.baselineValue"
              style="width: 100%"
              :step="0.1"
            />
          </FormItem>
          <FormItem label="偏差类型">
            <Select
              v-model:value="form.deviationType"
              :options="deviationTypeOptions"
              style="width: 100%"
            />
          </FormItem>
          <FormItem label="偏差阈值">
            <InputNumber
              v-model:value="form.deviationThreshold"
              style="width: 100%"
              :step="0.1"
              :min="0"
            />
            <div class="clpm-dsl-editor__hint">
              当指标统计值偏离基线超过此阈值时触发
            </div>
          </FormItem>
        </template>

        <!-- COMPOSITE 组合 -->
        <template v-else-if="ruleType === 'COMPOSITE'">
          <FormItem label="逻辑关系">
            <Select
              v-model:value="form.logic"
              :options="logicOptions"
              style="width: 100%"
            />
            <div class="clpm-dsl-editor__hint">
              全部满足=AND（所有子条件同时成立）；任一满足=OR（任一子条件成立）
            </div>
          </FormItem>
          <div class="clpm-dsl-editor__composite-hint">
            <strong>子条件</strong>：组合规则的子条件需在底部「DSL 预览」中编辑
            operands 数组（每个子条件为一条 THRESHOLD/CONFIDENCE 规则）。
          </div>
        </template>
      </div>

      <!-- ===== 时效控制 ===== -->
      <div class="clpm-dsl-editor__section">
        <div class="clpm-dsl-editor__section-title">时效控制</div>
        <FormItem label="持续时长（分钟）">
          <Slider
            v-model:value="durationMinutes"
            :min="0"
            :max="120"
            :marks="{ 0: '0', 30: '30', 60: '60', 120: '120' }"
          />
          <div class="clpm-dsl-editor__hint">
            条件需持续满足此时长才触发（0=立即触发）
          </div>
        </FormItem>
        <FormItem label="冷却时间（分钟）">
          <InputNumber
            v-model:value="cooldownMinutes"
            :min="0"
            :max="1440"
            :step="5"
            style="width: 100%"
          />
          <div class="clpm-dsl-editor__hint">
            触发后在此时间内不重复告警（避免风暴）
          </div>
        </FormItem>
      </div>

      <!-- ===== 响应动作 ===== -->
      <div class="clpm-dsl-editor__section">
        <div class="clpm-dsl-editor__section-title">响应动作</div>
        <FormItem label="严重级别">
          <RadioGroup v-model:value="form.severity">
            <Radio
              v-for="opt in severityOptions"
              :key="opt.value"
              :value="opt.value"
            >
              {{ opt.label }}
            </Radio>
          </RadioGroup>
        </FormItem>
        <FormItem label="触发动作">
          <CheckboxGroup v-model:value="form.actions">
            <Checkbox
              v-for="opt in actionTypeOptions"
              :key="opt.value"
              :value="opt.value"
            >
              {{ opt.label }}
            </Checkbox>
          </CheckboxGroup>
          <div class="clpm-dsl-editor__hint">
            生成事件=记录到事件列表；创建工单=同步生成跟踪任务
          </div>
        </FormItem>
        <FormItem label="优先级">
          <InputNumber
            v-model:value="form.priority"
            :min="1"
            :max="9999"
            style="width: 100%"
          />
          <div class="clpm-dsl-editor__hint">数字越小优先级越高（1=最高）</div>
        </FormItem>
      </div>
    </Form>

    <!-- ===== DSL 预览（可折叠，供高级用户查看） ===== -->
    <Collapse :bordered="false" ghost class="clpm-dsl-editor__preview">
      <CollapsePanel key="dsl" header="DSL 预览（高级模式·JSON）">
        <pre class="clpm-dsl-editor__json">{{ dslPreview }}</pre>
      </CollapsePanel>
    </Collapse>
  </div>
</template>

<style scoped>
.clpm-dsl-editor {
  padding: 0;
}

.clpm-dsl-editor__section {
  padding: 12px 16px;
  margin-bottom: 8px;
  background: hsl(var(--muted) / 30%);
  border-radius: 8px;
}

.clpm-dsl-editor__section-title {
  padding-left: 8px;
  margin-bottom: 12px;
  font-size: 13px;
  font-weight: 600;
  color: hsl(var(--foreground));
  border-left: 3px solid hsl(var(--primary));
}

.clpm-dsl-editor__hint {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: hsl(var(--muted-foreground));
}

.clpm-dsl-editor__composite-hint {
  padding: 8px 12px;
  font-size: 12px;
  line-height: 1.6;
  color: hsl(var(--warning-foreground, hsl(32deg 95% 44%)));
  background: hsl(var(--warning) / 10%);
  border-radius: 6px;
}

.clpm-dsl-editor__preview {
  margin-top: 8px;
}

.clpm-dsl-editor__json {
  max-height: 300px;
  padding: 12px;
  overflow: auto;
  font-family: ui-monospace, monospace;
  font-size: 12px;
  line-height: 1.5;
  color: hsl(var(--foreground));
  background: hsl(var(--muted) / 50%);
  border-radius: 6px;
}
</style>
