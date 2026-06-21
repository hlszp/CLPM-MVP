/**
 * Tag 关联选择器（v4.0 核心组件）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §7.7 + §6.2.3 + §9.5
 *
 * 结构：
 * - 左侧：AAS 同步 tag 列表（搜索/筛选/数据质量徽章/关联状态）
 * - 右侧：7 槽位关联区（PV/SP/OP/MODE/PID_P/PID_I/PID_D）
 *
 * 交互（§9.5）：
 * - 拖拽：从左侧 tag 列表拖拽至右侧槽位，槽位高亮反馈（150ms）
 * - 下拉选择：点击槽位下拉框，从 tag 列表选择
 * - 清除：点击槽位清除按钮移除关联
 * - 校验：必填槽位（PV/SP/OP/MODE）缺失时标红 + tooltip
 *
 * 视觉（§7.7）：
 * - 槽位标签：必填项标注红色 *，可选项标注灰色 (可选)
 * - 已关联 tag：等宽字体显示 tag 名 + 数据质量徽章
 * - 缺失槽位：红色虚线边框 + "未关联"占位文本
 */

import { useMemo, useState } from 'react';
import { Search, X, CheckCircle2, AlertCircle } from 'lucide-react';
import { PVQualityBadge, type PVQuality } from './PVQualityBadge';

/** Tag 槽位标识（7 个 OPC Tag 槽位） */
export type TagSlotKey = 'PV' | 'SP' | 'OP' | 'MODE' | 'PID_P' | 'PID_I' | 'PID_D';

/** Tag 槽位元数据 */
interface SlotMeta {
  key: TagSlotKey;
  label: string;
  required: boolean;
  description: string;
}

const SLOT_META: SlotMeta[] = [
  { key: 'PV', label: 'PV', required: true, description: '过程测量值，含质量码' },
  { key: 'SP', label: 'SP', required: true, description: '设定值' },
  { key: 'OP', label: 'OP', required: true, description: '输出值' },
  { key: 'MODE', label: 'MODE', required: true, description: '控制模式' },
  { key: 'PID_P', label: 'PID_P', required: false, description: '比例参数（可选）' },
  { key: 'PID_I', label: 'PID_I', required: false, description: '积分参数（可选）' },
  { key: 'PID_D', label: 'PID_D', required: false, description: '微分参数（可选）' },
];

/** AAS 同步 Tag 数据（对应 DDS tag_registry） */
export interface AasTag {
  tagId: string;
  tagName: string;
  description: string;
  currentValue: string | number;
  quality: PVQuality;
  /** 已关联回路名（空字符串表示未关联） */
  linkedLoop: string;
}

/** 回路当前 Tag 关联状态（slotKey → tagId） */
export type LoopTagMapping = Partial<Record<TagSlotKey, string>>;

interface TagAssociationSelectorProps {
  /** AAS 同步的 tag 列表 */
  tags: AasTag[];
  /** 当前选中回路的 tag 关联（受控） */
  mapping: LoopTagMapping;
  /** 当前回路名 */
  loopName: string;
  /** 关联变更回调 */
  onChange: (mapping: LoopTagMapping) => void;
}

export function TagAssociationSelector({
  tags,
  mapping,
  loopName,
  onChange,
}: TagAssociationSelectorProps) {
  const [search, setSearch] = useState('');
  const [qualityFilter, setQualityFilter] = useState<'all' | PVQuality>('all');
  const [linkFilter, setLinkFilter] = useState<'all' | 'linked' | 'unlinked'>('all');
  const [dragOverSlot, setDragOverSlot] = useState<TagSlotKey | null>(null);
  const [dragTagId, setDragTagId] = useState<string | null>(null);

  /** 左侧 tag 列表过滤 */
  const filteredTags = useMemo(() => {
    return tags.filter((tag) => {
      if (search) {
        const q = search.toLowerCase();
        if (!tag.tagName.toLowerCase().includes(q) && !tag.description.toLowerCase().includes(q)) {
          return false;
        }
      }
      if (qualityFilter !== 'all' && tag.quality !== qualityFilter) return false;
      if (linkFilter === 'linked' && !tag.linkedLoop) return false;
      if (linkFilter === 'unlinked' && tag.linkedLoop) return false;
      return true;
    });
  }, [tags, search, qualityFilter, linkFilter]);

  /** 校验状态：必填槽位是否完整 */
  const validation = useMemo(() => {
    const missing: TagSlotKey[] = [];
    for (const slot of SLOT_META) {
      if (slot.required && !mapping[slot.key]) missing.push(slot.key);
    }
    return { complete: missing.length === 0, missing };
  }, [mapping]);

  /** 反查：tagId → tag */
  const tagMap = useMemo(() => {
    const m = new Map<string, AasTag>();
    tags.forEach((t) => m.set(t.tagId, t));
    return m;
  }, [tags]);

  /** 拖拽放置 */
  const handleDrop = (slotKey: TagSlotKey) => {
    if (dragTagId) {
      onChange({ ...mapping, [slotKey]: dragTagId });
    }
    setDragOverSlot(null);
    setDragTagId(null);
  };

  /** 清除槽位 */
  const handleClear = (slotKey: TagSlotKey) => {
    const next = { ...mapping };
    delete next[slotKey];
    onChange(next);
  };

  /** 下拉选择 */
  const handleSelect = (slotKey: TagSlotKey, tagId: string) => {
    if (tagId) {
      onChange({ ...mapping, [slotKey]: tagId });
    } else {
      handleClear(slotKey);
    }
  };

  return (
    <div className="tag-selector">
      {/* 左侧：AAS Tag 列表 */}
      <div className="tag-selector-left">
        <div className="tag-selector-header">
          <h3>AAS 同步 Tag 列表</h3>
          <div className="tag-search">
            <Search size={14} />
            <input
              type="text"
              placeholder="搜索 tag 名/描述"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="tag-filters">
            <select value={qualityFilter} onChange={(e) => setQualityFilter(e.target.value as 'all' | PVQuality)}>
              <option value="all">全部质量</option>
              <option value="Good">Good</option>
              <option value="Bad">Bad</option>
              <option value="Uncertain">Uncertain</option>
            </select>
            <select value={linkFilter} onChange={(e) => setLinkFilter(e.target.value as 'all' | 'linked' | 'unlinked')}>
              <option value="all">全部关联</option>
              <option value="linked">已关联</option>
              <option value="unlinked">未关联</option>
            </select>
          </div>
        </div>
        <div className="tag-list">
          {filteredTags.length === 0 ? (
            <div className="tag-list-empty">无匹配 tag</div>
          ) : (
            filteredTags.map((tag) => (
              <div
                key={tag.tagId}
                className="tag-list-item"
                draggable
                onDragStart={() => setDragTagId(tag.tagId)}
                onDragEnd={() => {
                  setDragTagId(null);
                  setDragOverSlot(null);
                }}
              >
                <div className="tag-list-item-name mono">{tag.tagName}</div>
                <div className="tag-list-item-desc">{tag.description}</div>
                <div className="tag-list-item-meta">
                  <span className="tag-list-item-value mono">{tag.currentValue}</span>
                  <PVQualityBadge quality={tag.quality} />
                  {tag.linkedLoop && (
                    <span className="tag-list-item-linked" title={`已关联至 ${tag.linkedLoop}`}>
                      → {tag.linkedLoop}
                    </span>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      {/* 右侧：7 槽位关联区 */}
      <div className="tag-selector-right">
        <div className="tag-selector-header">
          <h3>回路 Tag 关联</h3>
          <div className="tag-selector-loop">
            当前回路：<span className="mono">{loopName || '未选择'}</span>
          </div>
        </div>

        <div className="slot-list">
          {SLOT_META.map((slot) => {
            const linkedTagId = mapping[slot.key];
            const linkedTag = linkedTagId ? tagMap.get(linkedTagId) : undefined;
            const isMissing = slot.required && !linkedTag;
            const isDragOver = dragOverSlot === slot.key;
            return (
              <div
                key={slot.key}
                className={`slot ${isMissing ? 'slot-missing' : ''} ${isDragOver ? 'slot-dragover' : ''}`}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOverSlot(slot.key);
                }}
                onDragLeave={() => setDragOverSlot(null)}
                onDrop={() => handleDrop(slot.key)}
              >
                <div className="slot-label">
                  <span className="slot-key mono">{slot.label}</span>
                  {slot.required ? (
                    <span className="slot-required" title="必填">*</span>
                  ) : (
                    <span className="slot-optional">(可选)</span>
                  )}
                  <span className="slot-desc">{slot.description}</span>
                </div>
                <div className="slot-control">
                  {linkedTag ? (
                    <div className="slot-filled">
                      <span className="slot-tag-name mono">{linkedTag.tagName}</span>
                      <PVQualityBadge quality={linkedTag.quality} />
                      <button
                        type="button"
                        className="slot-clear"
                        onClick={() => handleClear(slot.key)}
                        title="清除关联"
                      >
                        <X size={14} />
                      </button>
                    </div>
                  ) : (
                    <select
                      className="slot-select"
                      value=""
                      onChange={(e) => handleSelect(slot.key, e.target.value)}
                    >
                      <option value="">拖入或选择 tag</option>
                      {tags.map((tag) => (
                        <option key={tag.tagId} value={tag.tagId}>
                          {tag.tagName} ({tag.quality})
                        </option>
                      ))}
                    </select>
                  )}
                </div>
                {isMissing && (
                  <div className="slot-error">{slot.label} tag 必填</div>
                )}
              </div>
            );
          })}
        </div>

        {/* 校验状态 */}
        <div className={`slot-validation ${validation.complete ? 'valid' : 'invalid'}`}>
          {validation.complete ? (
            <>
              <CheckCircle2 size={16} />
              <span>校验通过：7 槽位完整</span>
            </>
          ) : (
            <>
              <AlertCircle size={16} />
              <span>缺失必填槽位：{validation.missing.join(', ')}</span>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
