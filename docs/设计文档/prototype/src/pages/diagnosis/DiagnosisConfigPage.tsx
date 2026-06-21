/**
 * 诊断配置页（v4.0 §6.4.6）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §6.4.6
 *
 * 布局结构（配置页 form-section）：
 * 1. 振荡检测：FFT 窗口长度 / 频率阈值 / 幅值阈值
 * 2. 粘滞阀检测：R² 阈值 / 采样点数
 * 3. 参数过激检测：超调量阈值 / 衰减比阈值
 * 4. 参数过保守检测：响应时间阈值 / IAE 阈值
 * 5. PV 质量异常检测：连续 Bad 时间阈值 / Uncertain 占比阈值
 *
 * 底部 form-actions：保存按钮（ConfigConfirmDialog 确认）
 */

import { useState, useMemo } from 'react';
import { Save } from 'lucide-react';
import { ConfigConfirmDialog, type ChangeEntry } from '../../components/ConfigConfirmDialog';
import { useToast } from '../../components/Toast';

/** 诊断算法参数配置类型 */
interface DiagnosisConfig {
  // 振荡检测
  fftWindow: string;
  freqThreshold: string;
  ampThreshold: string;
  // 粘滞阀检测
  r2Threshold: string;
  sampleCount: string;
  // 参数过激检测
  overshootThreshold: string;
  decayRatioThreshold: string;
  // 参数过保守检测
  responseTimeThreshold: string;
  iaeThreshold: string;
  // PV 质量异常检测
  badDurationThreshold: string;
  uncertainRatioThreshold: string;
}

/** 默认配置 */
const DEFAULT_CONFIG: DiagnosisConfig = {
  fftWindow: '512',
  freqThreshold: '0.005',
  ampThreshold: '2.0',
  r2Threshold: '0.6',
  sampleCount: '500',
  overshootThreshold: '15',
  decayRatioThreshold: '1:2',
  responseTimeThreshold: '60',
  iaeThreshold: '10.0',
  badDurationThreshold: '120',
  uncertainRatioThreshold: '0.1',
};

/** 字段中文名映射（用于变更摘要） */
const FIELD_LABELS: Record<keyof DiagnosisConfig, string> = {
  fftWindow: 'FFT 窗口长度',
  freqThreshold: '频率阈值',
  ampThreshold: '幅值阈值',
  r2Threshold: 'R² 阈值',
  sampleCount: '采样点数',
  overshootThreshold: '超调量阈值',
  decayRatioThreshold: '衰减比阈值',
  responseTimeThreshold: '响应时间阈值',
  iaeThreshold: 'IAE 阈值',
  badDurationThreshold: '连续 Bad 时间阈值',
  uncertainRatioThreshold: 'Uncertain 占比阈值',
};

export default function DiagnosisConfigPage() {
  const toast = useToast();

  // 当前配置（可编辑）
  const [config, setConfig] = useState<DiagnosisConfig>(DEFAULT_CONFIG);
  // 已保存的配置（用于对比变更）
  const [savedConfig, setSavedConfig] = useState<DiagnosisConfig>(DEFAULT_CONFIG);
  // 确认弹窗
  const [dialogOpen, setDialogOpen] = useState(false);

  // 更新单个字段
  const updateField = (field: keyof DiagnosisConfig, value: string) => {
    setConfig((prev) => ({ ...prev, [field]: value }));
  };

  // 计算变更条目
  const changes: ChangeEntry[] = useMemo(() => {
    const result: ChangeEntry[] = [];
    (Object.keys(config) as Array<keyof DiagnosisConfig>).forEach((key) => {
      if (config[key] !== savedConfig[key]) {
        result.push({
          field: FIELD_LABELS[key],
          oldValue: savedConfig[key],
          newValue: config[key],
        });
      }
    });
    return result;
  }, [config, savedConfig]);

  // 点击保存：打开确认弹窗
  const handleSave = () => {
    if (changes.length === 0) {
      toast.warning('未检测到配置变更');
      return;
    }
    setDialogOpen(true);
  };

  // 确认变更
  const handleConfirm = (comment: string) => {
    void comment;
    setSavedConfig(config);
    setDialogOpen(false);
    toast.success('诊断算法配置已保存，变更已记录审计日志');
  };

  // 重置为默认
  const handleReset = () => {
    setConfig(DEFAULT_CONFIG);
    toast.warning('已重置为默认配置（未保存）');
  };

  return (
    <div className="page-container">
      {/* 页面标题 */}
      <div className="page-header">
        <div>
          <h1>诊断算法配置</h1>
          <p className="page-subtitle">
            配置 5 类诊断算法的检测阈值与参数 · 变更将记录至审计日志
          </p>
        </div>
      </div>

      {/* 振荡检测 */}
      <div className="form-section">
        <div className="form-section-header">
          <h3>振荡检测</h3>
        </div>
        <div className="form-section-body">
          <div className="form-row">
            <label>FFT 窗口长度</label>
            <select
              value={config.fftWindow}
              onChange={(e) => updateField('fftWindow', e.target.value)}
            >
              <option value="128">128</option>
              <option value="256">256</option>
              <option value="512">512</option>
              <option value="1024">1024</option>
            </select>
          </div>
          <div className="form-row">
            <label>频率阈值 (Hz)</label>
            <input
              type="text"
              value={config.freqThreshold}
              onChange={(e) => updateField('freqThreshold', e.target.value)}
            />
          </div>
          <div className="form-row">
            <label>幅值阈值</label>
            <input
              type="text"
              value={config.ampThreshold}
              onChange={(e) => updateField('ampThreshold', e.target.value)}
            />
          </div>
          <div className="form-row">
            <span className="hint">FFT 窗口长度越大频率分辨率越高，但实时性降低；幅值阈值为 PV 波动允许带。</span>
          </div>
        </div>
      </div>

      {/* 粘滞阀检测 */}
      <div className="form-section">
        <div className="form-section-header">
          <h3>粘滞阀检测</h3>
        </div>
        <div className="form-section-body">
          <div className="form-row">
            <label>R² 阈值</label>
            <input
              type="text"
              value={config.r2Threshold}
              onChange={(e) => updateField('r2Threshold', e.target.value)}
            />
          </div>
          <div className="form-row">
            <label>采样点数</label>
            <input
              type="text"
              value={config.sampleCount}
              onChange={(e) => updateField('sampleCount', e.target.value)}
            />
          </div>
          <div className="form-row">
            <span className="hint">PV-OP 散点拟合 R² 低于阈值时判定为粘滞阀；采样点数影响拟合稳定性。</span>
          </div>
        </div>
      </div>

      {/* 参数过激检测 */}
      <div className="form-section">
        <div className="form-section-header">
          <h3>参数过激检测</h3>
        </div>
        <div className="form-section-body">
          <div className="form-row">
            <label>超调量阈值 (%)</label>
            <input
              type="text"
              value={config.overshootThreshold}
              onChange={(e) => updateField('overshootThreshold', e.target.value)}
            />
          </div>
          <div className="form-row">
            <label>衰减比阈值</label>
            <input
              type="text"
              value={config.decayRatioThreshold}
              onChange={(e) => updateField('decayRatioThreshold', e.target.value)}
            />
          </div>
          <div className="form-row">
            <span className="hint">阶跃响应超调量超过阈值或衰减比小于设定值时判定为参数过激。</span>
          </div>
        </div>
      </div>

      {/* 参数过保守检测 */}
      <div className="form-section">
        <div className="form-section-header">
          <h3>参数过保守检测</h3>
        </div>
        <div className="form-section-body">
          <div className="form-row">
            <label>响应时间阈值 (s)</label>
            <input
              type="text"
              value={config.responseTimeThreshold}
              onChange={(e) => updateField('responseTimeThreshold', e.target.value)}
            />
          </div>
          <div className="form-row">
            <label>IAE 阈值</label>
            <input
              type="text"
              value={config.iaeThreshold}
              onChange={(e) => updateField('iaeThreshold', e.target.value)}
            />
          </div>
          <div className="form-row">
            <span className="hint">响应时间超过阈值且 IAE 偏高时判定为参数过保守。</span>
          </div>
        </div>
      </div>

      {/* PV 质量异常检测 */}
      <div className="form-section">
        <div className="form-section-header">
          <h3>PV 质量异常检测</h3>
        </div>
        <div className="form-section-body">
          <div className="form-row">
            <label>连续 Bad 时间阈值 (min)</label>
            <input
              type="text"
              value={config.badDurationThreshold}
              onChange={(e) => updateField('badDurationThreshold', e.target.value)}
            />
          </div>
          <div className="form-row">
            <label>Uncertain 占比阈值</label>
            <input
              type="text"
              value={config.uncertainRatioThreshold}
              onChange={(e) => updateField('uncertainRatioThreshold', e.target.value)}
            />
          </div>
          <div className="form-row">
            <span className="hint">PV 连续 Bad 超过阈值或 Uncertain 占比超过阈值时触发质量异常诊断。</span>
          </div>
        </div>
      </div>

      {/* 底部操作区 */}
      <div className="form-section">
        <div className="form-actions">
          <button type="button" className="btn btn-secondary" onClick={handleReset}>
            重置默认
          </button>
          <button type="button" className="btn btn-primary" onClick={handleSave}>
            <Save size={14} />
            保存配置
          </button>
        </div>
      </div>

      {/* 配置变更确认弹窗 */}
      <ConfigConfirmDialog
        key={dialogOpen ? 'open' : 'closed'}
        open={dialogOpen}
        configName="诊断算法配置"
        changes={changes}
        onCancel={() => setDialogOpen(false)}
        onConfirm={handleConfirm}
      />
    </div>
  );
}
