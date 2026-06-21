/**
 * 引擎规则配置页面（UI/UX §6.3.3）
 *
 * 布局结构：
 * 1. 页面标题
 * 2. 评估引擎参数配置表单（form-section + form-row）
 *    - 评估周期/数据窗口/降采样点数/PV 质量码过滤
 *    - 评分阈值/FFT 窗口/粘滞阀 R² 阈值
 * 3. 底部 form-actions：保存按钮（弹出 ConfigConfirmDialog）
 *
 * 设计 grammar（反 AI slop）：
 * - Lucide 图标，不用 emoji
 * - 工业专业配色：状态色驱动
 * - 配置页：form-section + form-row + form-actions
 */

import { useState, useMemo } from 'react';
import { Save } from 'lucide-react';
import {
  ConfigConfirmDialog,
  type ChangeEntry,
} from '../../components/ConfigConfirmDialog';
import { useToast } from '../../components/Toast';
import type { PVQuality } from '../../components/PVQualityBadge';

/** 引擎配置参数 */
interface EngineConfig {
  /** 评估周期（小时） */
  evalPeriod: number;
  /** 数据窗口（天） */
  dataWindowDays: number;
  /** 降采样点数（LTTB） */
  downsamplePoints: number;
  /** PV 质量码过滤 */
  pvQualityFilter: PVQuality[];
  /** 优秀阈值（≥） */
  excellentThreshold: number;
  /** 警告阈值（≥） */
  warningThreshold: number;
  /** 低效阈值（<） */
  lowThreshold: number;
  /** 振荡检测 FFT 窗口长度 */
  fftWindow: number;
  /** 粘滞阀检测 R² 阈值 */
  stickyR2Threshold: number;
}

/** 默认引擎配置 */
const DEFAULT_CONFIG: EngineConfig = {
  evalPeriod: 4,
  dataWindowDays: 30,
  downsamplePoints: 2000,
  pvQualityFilter: ['Good'],
  excellentThreshold: 80,
  warningThreshold: 60,
  lowThreshold: 60,
  fftWindow: 256,
  stickyR2Threshold: 0.5,
};

/** 评估周期选项 */
const EVAL_PERIOD_OPTIONS = [1, 4, 8, 12, 24];

/** FFT 窗口长度选项 */
const FFT_WINDOW_OPTIONS = [128, 256, 512, 1024];

/** PV 质量码选项 */
const PV_QUALITY_OPTIONS: PVQuality[] = ['Good', 'Uncertain', 'Bad'];

export default function EngineConfigPage() {
  const toast = useToast();

  /** 当前编辑中的配置 */
  const [config, setConfig] = useState<EngineConfig>(DEFAULT_CONFIG);
  /** 上次保存的配置（用于变更对比） */
  const [lastSaved, setLastSaved] = useState<EngineConfig>(DEFAULT_CONFIG);

  /** 确认弹窗状态 */
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [pendingChanges, setPendingChanges] = useState<ChangeEntry[]>([]);

  /** 更新配置字段 */
  const updateField = <K extends keyof EngineConfig>(
    key: K,
    value: EngineConfig[K],
  ) => {
    setConfig((prev) => ({ ...prev, [key]: value }));
  };

  /** 切换 PV 质量码过滤选项 */
  const togglePvQuality = (quality: PVQuality) => {
    setConfig((prev) => ({
      ...prev,
      pvQualityFilter: prev.pvQualityFilter.includes(quality)
        ? prev.pvQualityFilter.filter((q) => q !== quality)
        : [...prev.pvQualityFilter, quality],
    }));
  };

  /** 点击保存：对比变更并打开确认弹窗 */
  const handleSave = () => {
    const changes: ChangeEntry[] = [];

    if (config.evalPeriod !== lastSaved.evalPeriod) {
      changes.push({
        field: '评估周期',
        oldValue: `${lastSaved.evalPeriod}h`,
        newValue: `${config.evalPeriod}h`,
      });
    }
    if (config.dataWindowDays !== lastSaved.dataWindowDays) {
      changes.push({
        field: '数据窗口',
        oldValue: `${lastSaved.dataWindowDays}天`,
        newValue: `${config.dataWindowDays}天`,
      });
    }
    if (config.downsamplePoints !== lastSaved.downsamplePoints) {
      changes.push({
        field: '降采样点数',
        oldValue: String(lastSaved.downsamplePoints),
        newValue: String(config.downsamplePoints),
      });
    }
    const oldPv = lastSaved.pvQualityFilter.join('/');
    const newPv = config.pvQualityFilter.join('/');
    if (oldPv !== newPv) {
      changes.push({
        field: 'PV 质量码过滤',
        oldValue: oldPv,
        newValue: newPv,
      });
    }
    if (config.excellentThreshold !== lastSaved.excellentThreshold) {
      changes.push({
        field: '优秀阈值',
        oldValue: `≥${lastSaved.excellentThreshold}`,
        newValue: `≥${config.excellentThreshold}`,
      });
    }
    if (config.warningThreshold !== lastSaved.warningThreshold) {
      changes.push({
        field: '警告阈值',
        oldValue: `≥${lastSaved.warningThreshold}`,
        newValue: `≥${config.warningThreshold}`,
      });
    }
    if (config.lowThreshold !== lastSaved.lowThreshold) {
      changes.push({
        field: '低效阈值',
        oldValue: `<${lastSaved.lowThreshold}`,
        newValue: `<${config.lowThreshold}`,
      });
    }
    if (config.fftWindow !== lastSaved.fftWindow) {
      changes.push({
        field: 'FFT 窗口长度',
        oldValue: String(lastSaved.fftWindow),
        newValue: String(config.fftWindow),
      });
    }
    if (config.stickyR2Threshold !== lastSaved.stickyR2Threshold) {
      changes.push({
        field: '粘滞阀 R² 阈值',
        oldValue: String(lastSaved.stickyR2Threshold),
        newValue: String(config.stickyR2Threshold),
      });
    }

    if (changes.length === 0) {
      toast.warning('无变更内容');
      return;
    }

    setPendingChanges(changes);
    setConfirmOpen(true);
  };

  /** 确认变更 */
  const handleConfirm = (comment: string) => {
    setLastSaved(config);
    setConfirmOpen(false);
    setPendingChanges([]);
    toast.success(`配置已保存，变更已记录审计日志（说明：${comment}）`);
  };

  /** PV 质量码过滤是否为空 */
  const pvFilterEmpty = config.pvQualityFilter.length === 0;

  /** 阈值是否合法 */
  const thresholdValid = useMemo(
    () =>
      config.excellentThreshold > config.warningThreshold &&
      config.warningThreshold >= config.lowThreshold,
    [config],
  );

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1>引擎规则配置</h1>
          <p className="page-subtitle">
            评估引擎参数配置 · 影响全厂 KPI 计算与诊断分析
          </p>
        </div>
      </div>

      {/* 评估周期与数据窗口 */}
      <div className="form-section">
        <div className="form-section-header">
          <h3>评估周期与数据窗口</h3>
        </div>
        <div className="form-section-body">
          <div className="form-row">
            <label>评估周期（小时）</label>
            <select
              value={config.evalPeriod}
              onChange={(e) => updateField('evalPeriod', Number(e.target.value))}
            >
              {EVAL_PERIOD_OPTIONS.map((h) => (
                <option key={h} value={h}>
                  {h} 小时
                </option>
              ))}
            </select>
          </div>
          <div className="form-row">
            <label>数据窗口（天）</label>
            <input
              type="number"
              min={1}
              max={365}
              value={config.dataWindowDays}
              onChange={(e) =>
                updateField('dataWindowDays', Number(e.target.value))
              }
            />
          </div>
          <div className="form-row">
            <label>降采样点数</label>
            <input
              type="number"
              min={100}
              max={10000}
              value={config.downsamplePoints}
              onChange={(e) =>
                updateField('downsamplePoints', Number(e.target.value))
              }
            />
          </div>
          <div className="form-row">
            <label>PV 质量码过滤</label>
            <div style={{ display: 'flex', gap: '16px', alignItems: 'center' }}>
              {PV_QUALITY_OPTIONS.map((q) => (
                <label
                  key={q}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px',
                    cursor: 'pointer',
                    fontSize: '14px',
                  }}
                >
                  <input
                    type="checkbox"
                    checked={config.pvQualityFilter.includes(q)}
                    onChange={() => togglePvQuality(q)}
                  />
                  {q}
                </label>
              ))}
              {pvFilterEmpty && (
                <span
                  className="badge status-danger badge-sm"
                  style={{ marginLeft: '8px' }}
                >
                  至少选择一项
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* 评分阈值 */}
      <div className="form-section">
        <div className="form-section-header">
          <h3>评分阈值</h3>
        </div>
        <div className="form-section-body">
          <div className="form-row">
            <label>优秀（≥）</label>
            <input
              type="number"
              min={0}
              max={100}
              value={config.excellentThreshold}
              onChange={(e) =>
                updateField('excellentThreshold', Number(e.target.value))
              }
            />
          </div>
          <div className="form-row">
            <label>警告（≥）</label>
            <input
              type="number"
              min={0}
              max={100}
              value={config.warningThreshold}
              onChange={(e) =>
                updateField('warningThreshold', Number(e.target.value))
              }
            />
          </div>
          <div className="form-row">
            <label>低效（&lt;）</label>
            <input
              type="number"
              min={0}
              max={100}
              value={config.lowThreshold}
              onChange={(e) =>
                updateField('lowThreshold', Number(e.target.value))
              }
            />
          </div>
          {!thresholdValid && (
            <div className="form-row">
              <label />
              <span
                className="badge status-danger badge-sm"
                style={{ alignSelf: 'flex-start' }}
              >
                阈值关系须满足：优秀 &gt; 警告 ≥ 低效
              </span>
            </div>
          )}
        </div>
      </div>

      {/* 诊断算法参数 */}
      <div className="form-section">
        <div className="form-section-header">
          <h3>诊断算法参数</h3>
        </div>
        <div className="form-section-body">
          <div className="form-row">
            <label>FFT 窗口长度</label>
            <select
              value={config.fftWindow}
              onChange={(e) => updateField('fftWindow', Number(e.target.value))}
            >
              {FFT_WINDOW_OPTIONS.map((w) => (
                <option key={w} value={w}>
                  {w}
                </option>
              ))}
            </select>
          </div>
          <div className="form-row">
            <label>粘滞阀 R² 阈值</label>
            <input
              type="number"
              min={0}
              max={1}
              step={0.1}
              value={config.stickyR2Threshold}
              onChange={(e) =>
                updateField('stickyR2Threshold', Number(e.target.value))
              }
            />
          </div>
        </div>
      </div>

      {/* 底部操作区 */}
      <div className="form-section">
        <div className="form-actions">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => setConfig(DEFAULT_CONFIG)}
          >
            重置默认
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleSave}
            disabled={pvFilterEmpty || !thresholdValid}
          >
            <Save size={14} /> 保存配置
          </button>
        </div>
      </div>

      {/* 配置变更确认弹窗 */}
      <ConfigConfirmDialog
        key={confirmOpen ? 'open' : 'closed'}
        open={confirmOpen}
        configName="引擎规则"
        changes={pendingChanges}
        onCancel={() => {
          setConfirmOpen(false);
          setPendingChanges([]);
        }}
        onConfirm={handleConfirm}
      />
    </div>
  );
}
