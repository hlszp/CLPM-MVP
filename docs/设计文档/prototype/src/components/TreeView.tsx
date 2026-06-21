/**
 * 树形视图（v4.0 通用组件）
 *
 * 权威来源：ui-ux-design-guidelines.md v4.0 §7.9
 *
 * 用于工厂层级配置页（左树）与回路监控列表的层级筛选。
 * 支持递归渲染、展开/折叠、节点选中、节点类型图标。
 */

import { useState } from 'react';
import { ChevronRight, Factory, Boxes, Layers } from 'lucide-react';
import type { NodeType } from '../mock/types';

/** 树节点数据（通用接口） */
export interface TreeNodeData {
  id: string;
  name: string;
  type: NodeType | string;
  children?: TreeNodeData[];
  /** 附加数据（如回路数） */
  meta?: string;
}

interface TreeViewProps {
  data: TreeNodeData[];
  /** 选中节点 ID */
  selectedId?: string;
  onSelect: (node: TreeNodeData) => void;
  /** 默认展开所有节点 */
  defaultExpandAll?: boolean;
}

/** 节点类型图标（静态映射，避免渲染期间创建组件） */
function NodeIcon({ type, size = 14 }: { type: string; size?: number }) {
  if (type === 'factory') return <Factory size={size} className="tree-icon" />;
  if (type === 'unit') return <Boxes size={size} className="tree-icon" />;
  return <Layers size={size} className="tree-icon" />;
}

function TreeItem({
  node,
  level,
  selectedId,
  onSelect,
  expandedSet,
  toggleExpand,
}: {
  node: TreeNodeData;
  level: number;
  selectedId?: string;
  onSelect: (node: TreeNodeData) => void;
  expandedSet: Set<string>;
  toggleExpand: (id: string) => void;
}) {
  const hasChildren = node.children && node.children.length > 0;
  const isExpanded = expandedSet.has(node.id);
  const isSelected = selectedId === node.id;

  return (
    <div>
      <div
        className={`tree-item ${isSelected ? 'selected' : ''}`}
        style={{ paddingLeft: `${level * 16 + 8}px` }}
        onClick={() => onSelect(node)}
      >
        {hasChildren ? (
          <button
            type="button"
            className="tree-toggle"
            onClick={(e) => {
              e.stopPropagation();
              toggleExpand(node.id);
            }}
          >
            <ChevronRight size={14} className={isExpanded ? 'expanded' : ''} />
          </button>
        ) : (
          <span className="tree-toggle-placeholder" />
        )}
        <NodeIcon type={node.type} />
        <span className="tree-label">{node.name}</span>
        {node.meta && <span className="tree-meta">{node.meta}</span>}
      </div>
      {hasChildren && isExpanded && (
        <div>
          {node.children!.map((child) => (
            <TreeItem
              key={child.id}
              node={child}
              level={level + 1}
              selectedId={selectedId}
              onSelect={onSelect}
              expandedSet={expandedSet}
              toggleExpand={toggleExpand}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function TreeView({ data, selectedId, onSelect, defaultExpandAll = true }: TreeViewProps) {
  const [expandedSet, setExpandedSet] = useState<Set<string>>(() => {
    if (!defaultExpandAll) return new Set();
    const ids = new Set<string>();
    const collect = (nodes: TreeNodeData[]) => {
      nodes.forEach((n) => {
        if (n.children && n.children.length > 0) {
          ids.add(n.id);
          collect(n.children);
        }
      });
    };
    collect(data);
    return ids;
  });

  const toggleExpand = (id: string) => {
    setExpandedSet((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="tree-view">
      {data.map((node) => (
        <TreeItem
          key={node.id}
          node={node}
          level={0}
          selectedId={selectedId}
          onSelect={onSelect}
          expandedSet={expandedSet}
          toggleExpand={toggleExpand}
        />
      ))}
    </div>
  );
}
