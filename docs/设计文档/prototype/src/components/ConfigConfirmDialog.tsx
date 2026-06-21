/**
 * 配置变更确认弹窗（v4.0 核心组件）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §7.8 + §9.6
 *
 * 适用页面：所有配置页（性能指标/引擎规则/诊断指标/工厂层级/Tag 关联）
 *
 * 结构（§7.8）：
 * - 标题：确认 [配置名称] 变更
 * - 变更摘要：列出本次变更的字段与新旧值对比
 * - 变更说明输入框（必填）：填写变更原因/说明
 * - 操作按钮：[取消] [确认变更]
 *
 * 行为（§7.8）：
 * - 确认后调用对应 API，记录审计日志
 * - 成功后全局 Toast"配置已保存，变更已记录审计日志"
 *
 * 交互（§9.6）：
 * - 未填写变更说明时确认按钮置灰
 * - 点击取消关闭弹窗，不保存变更
 *
 * 重置模式：父组件通过传入不同 `key` 控制重新挂载以清空变更说明输入框，
 * 例如 `<ConfigConfirmDialog key={open ? 'open' : 'closed'} open={open} ... />`。
 */

import { useState } from 'react';
import { X } from 'lucide-react';

/** 单个字段变更 */
export interface ChangeEntry {
  field: string;
  oldValue: string;
  newValue: string;
}

interface ConfigConfirmDialogProps {
  /** 是否显示 */
  open: boolean;
  /** 配置名称（用于标题） */
  configName: string;
  /** 变更条目列表 */
  changes: ChangeEntry[];
  /** 取消回调 */
  onCancel: () => void;
  /** 确认回调，参数为变更说明 */
  onConfirm: (comment: string) => void;
}

export function ConfigConfirmDialog({
  open,
  configName,
  changes,
  onCancel,
  onConfirm,
}: ConfigConfirmDialogProps) {
  const [comment, setComment] = useState('');

  if (!open) return null;

  const canConfirm = comment.trim().length > 0;

  return (
    <div className="config-dialog-overlay" onClick={onCancel}>
      <div
        className="config-dialog"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="config-dialog-title"
      >
        <div className="config-dialog-header">
          <h3 id="config-dialog-title">确认 {configName} 变更</h3>
          <button type="button" className="config-dialog-close" onClick={onCancel} aria-label="关闭">
            <X size={18} />
          </button>
        </div>

        <div className="config-dialog-body">
          <div className="config-dialog-section">
            <div className="config-dialog-section-title">变更摘要</div>
            {changes.length === 0 ? (
              <div className="config-dialog-empty">无字段变更</div>
            ) : (
              <table className="config-dialog-table">
                <thead>
                  <tr>
                    <th>字段</th>
                    <th>原值</th>
                    <th>新值</th>
                  </tr>
                </thead>
                <tbody>
                  {changes.map((c, idx) => (
                    <tr key={idx}>
                      <td className="mono">{c.field}</td>
                      <td className="mono config-dialog-old">{c.oldValue || '—'}</td>
                      <td className="mono config-dialog-new">{c.newValue || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <div className="config-dialog-section">
            <label className="config-dialog-label" htmlFor="config-comment">
              变更说明 <span className="slot-required">*</span>
            </label>
            <textarea
              id="config-comment"
              className="config-dialog-textarea"
              placeholder="请填写变更原因/说明（必填，将记录至审计日志）"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              rows={3}
            />
            {!canConfirm && (
              <div className="config-dialog-hint">请填写变更说明后才能确认</div>
            )}
          </div>
        </div>

        <div className="config-dialog-footer">
          <button type="button" className="btn btn-secondary" onClick={onCancel}>
            取消
          </button>
          <button
            type="button"
            className="btn btn-primary"
            disabled={!canConfirm}
            onClick={() => onConfirm(comment.trim())}
          >
            确认变更
          </button>
        </div>
      </div>
    </div>
  );
}
