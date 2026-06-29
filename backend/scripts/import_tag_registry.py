#!/usr/bin/env python3
"""标签信息导入脚本。

从 Excel 文件读取诚志永清的 Tag 测点清单，写入 PostgreSQL 的 tag_registry 表。

数据来源：docs/预研文档/标签信息_20260625110953.xlsx

用法::

    cd backend && uv run python scripts/import_tag_registry.py
    cd backend && uv run python scripts/import_tag_registry.py --clean  # 清空现有数据后重新导入
"""

from __future__ import annotations

import argparse
import asyncio
import re
import uuid
from collections import defaultdict
from pathlib import Path

import openpyxl
from sqlalchemy import text

from app.core.db import AsyncSessionLocal

PROJECT_ROOT = Path(__file__).parent.parent.parent
TAG_INFO_XLSX = PROJECT_ROOT / "docs" / "预研文档" / "标签信息_20260625110953.xlsx"

FACTORY_NAME = "诚志永清"

# 区域代码 → 区域名称
AREA_NAME_MAP = {
    "UT": "公用工程",
    "BD": "丁二烯装置",
    "MTO": "MTO装置",
    "OPU": "烯烃分离装置",
    "2EH": "2EH辛醇装置",
}

# 公用工程单元映射（HU001~HU012 → 中文名称）
UT_UNIT_MAP = {
    "HU001": "空压站",
    "HU002": "循环水站",
    "HU003": "脱盐水站",
    "HU004": "中水回用站",
    "HU005": "余热回收锅炉",
    "HU006": "火炬",
    "HU007": "综合泵房",
    "HU008": "深度回用",
    "HU009": "公辅介质外管",
    "HU010": "辛醇循环水",
    "HU011": "污水",
    "HU012": "高架火炬",
    "Other": "其他",
}

# 各装置的单元定义（数字前缀 → 中文单元名称）
UNIT_PREFIX_MAP = {
    "MTO": {
        "10": "反应再生单元",
        "20": "急冷分离单元",
        "30": "烯烃压缩单元",
    },
    "OPU": {
        "30": "预处理单元",
        "40": "脱甲烷精馏单元",
        "50": "乙烯丙烯精馏单元",
    },
    "BD": {
        "11": "加氢反应单元",
        "12": "萃取精馏单元",
        "13": "丁二烯精制单元",
        "14": "溶剂回收单元",
        "15": "压缩制冷单元",
    },
    "2EH": {
        "10": "醛化反应单元",
        "11": "缩合单元",
        "30": "加氢精馏单元",
        "31": "辛醇精制单元",
    },
}


def infer_tag_type(tag_name: str) -> str:
    """从位号后缀推断标签类型。"""
    if tag_name.endswith("_PV") or tag_name.endswith("_PIDA_PV"):
        return "PV"
    elif tag_name.endswith("_SP") or tag_name.endswith("_PIDA_SP"):
        return "SP"
    elif tag_name.endswith("_OP") or tag_name.endswith("_PIDA_OP"):
        return "OP"
    elif tag_name.endswith("_MODE"):
        return "MODE"
    elif "_P_" in tag_name or "_PID_P" in tag_name:
        return "PID_P"
    elif "_I_" in tag_name or "_PID_I" in tag_name:
        return "PID_I"
    elif "_D_" in tag_name or "_PID_D" in tag_name:
        return "PID_D"
    else:
        return "OTHER"


MEASURE_TYPE_MAP = {
    "F": "FLOW",
    "L": "LEVEL",
    "P": "PRESSURE",
    "T": "TEMPERATURE",
    "A": "ANALYSIS",
    "X": "POSITION",
}


def parse_tag_info() -> list[dict]:
    """解析标签信息 Excel。"""
    wb = openpyxl.load_workbook(TAG_INFO_XLSX, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))[1:]
    wb.close()

    tags = []
    for row in rows:
        (
            tag_name,
            tag_desc,
            tag_type,
            range_max,
            range_min,
            unit,
            area,
            system,
            gateway,
            sensor,
            is_alarm,
        ) = row[:11]
        if not tag_name:
            continue

        tags.append(
            {
                "tag_name": str(tag_name).strip(),
                "tag_description": str(tag_desc).strip() if tag_desc else None,
                "tag_type": str(tag_type).strip() if tag_type else None,
                "range_max": float(range_max) if range_max else None,
                "range_min": float(range_min) if range_min else None,
                "unit": str(unit).strip() if unit else None,
                "area": str(area).strip() if area else "OTHER",
                "is_alarm": str(is_alarm).strip() == "是" if is_alarm else False,
            }
        )
    return tags


def infer_measure_type(tag_name: str) -> str:
    """从位号推断测量类型。"""
    match = re.match(r"^\d*([A-Z])", tag_name)
    if match:
        letter = match.group(1)
        if letter in MEASURE_TYPE_MAP:
            return MEASURE_TYPE_MAP[letter]
    return "OTHER"


PV_UNIT_MAP = {
    "TEMPERATURE": "℃",
    "PRESSURE": "KPa",
    "LEVEL": "%",
    "FLOW": "",
    "ANALYSIS": "",
    "POSITION": "%",
}


def infer_unit(tag_name: str, tag_type: str) -> str | None:
    """根据参数类型和测量类型推断单位。"""
    if tag_type == "OP":
        return "%"
    if tag_type in ("PV", "SP", "KP", "TI", "TD"):
        measure_type = infer_measure_type(tag_name)
        return PV_UNIT_MAP.get(measure_type) or None
    return None


def assign_unit_code(tag: dict) -> str | None:
    """根据标签位号分配到具体单元。"""
    area = tag["area"]

    if area in UT_UNIT_MAP:
        return UT_UNIT_MAP[area]

    if area.startswith("HU"):
        return UT_UNIT_MAP.get(area)

    if area == "UT" or area == "UT-GDS":
        return "其他"

    if area.endswith("-GY"):
        area = area.replace("-GY", "")

    if area.endswith("-GDS"):
        area = area.replace("-GDS", "")

    unit_defs = UNIT_PREFIX_MAP.get(area)
    if not unit_defs:
        return None

    tag_name = tag["tag_name"]
    num_match = re.search(r"[A-Z]+(\d{2,5})", tag_name)
    if num_match:
        prefix = num_match.group(1)[:2]
        if prefix in unit_defs:
            return unit_defs[prefix]

    return list(unit_defs.values())[0]


async def clean_tag_data(session) -> None:
    """清空现有标签数据。"""
    await session.execute(
        text("""
        DELETE FROM loop_tag_mapping
        WHERE tag_id IN (SELECT id FROM tag_registry)
    """)
    )
    await session.execute(
        text("""
        DELETE FROM tag_registry
    """)
    )
    print("  ✓ 旧标签数据已清理")


async def get_unit_id_map(session) -> dict[str, str]:
    """获取单元名称到 node_id 的映射。"""
    result = await session.execute(
        text("""
        SELECT pn.id, pn.name, parent.name AS area_name
        FROM plant_node pn
        JOIN plant_node parent ON pn.parent_id = parent.id
        JOIN plant_node factory ON parent.parent_id = factory.id
        WHERE factory.name = :factory_name AND pn.type = 'UNIT'
    """),
        {"factory_name": FACTORY_NAME},
    )

    unit_id_map = {}
    for row in result.fetchall():
        unit_id, unit_name, area_name = row
        unit_id_map[unit_name] = unit_id

    return unit_id_map


async def import_tags(session, tags: list[dict]) -> list[dict]:
    """导入标签数据。"""
    unit_id_map = await get_unit_id_map(session)

    imported = []
    for tag in tags:
        tag_name = tag["tag_name"]
        area = tag["area"]

        unit_code = assign_unit_code(tag)
        unit_id = unit_id_map.get(unit_code) if unit_code else None

        if not unit_id:
            area_name = AREA_NAME_MAP.get(area)
            if area_name and area_name in unit_id_map:
                unit_id = unit_id_map[area_name]

        measure_type = infer_measure_type(tag_name)
        tag_type = infer_tag_type(tag_name)
        unit = infer_unit(tag_name, tag_type)

        tag_id = str(uuid.uuid4())
        await session.execute(
            text("""
            INSERT INTO tag_registry (
                id, tag_name, tag_description, tag_type,
                current_value, quality, last_sync_at, is_linked,
                range_min, range_max, unit, measure_type, tdengine_tag_id
            ) VALUES (
                :id, :tag_name, :desc, :type,
                NULL, 'GOOD', NOW(), FALSE,
                :range_min, :range_max, :unit, :measure_type, :td_tag_id
            )
            ON CONFLICT (tag_name) DO UPDATE SET
                tag_description = EXCLUDED.tag_description,
                tag_type = EXCLUDED.tag_type,
                range_min = EXCLUDED.range_min,
                range_max = EXCLUDED.range_max,
                unit = EXCLUDED.unit,
                measure_type = EXCLUDED.measure_type
        """),
            {
                "id": tag_id,
                "tag_name": tag_name,
                "desc": tag["tag_description"] or tag_name,
                "type": tag_type,
                "range_min": tag["range_min"],
                "range_max": tag["range_max"],
                "unit": unit,
                "measure_type": measure_type,
                "td_tag_id": tag_name,
            },
        )

        imported.append(
            {
                "tag_name": tag_name,
                "area": area,
                "unit_code": unit_code,
                "unit_id": unit_id,
                "measure_type": measure_type,
            }
        )

    await session.commit()
    print(f"  ✓ 标签导入完成：{len(imported)} 条")

    area_count = defaultdict(int)
    unit_count = defaultdict(int)
    for item in imported:
        area_count[item["area"]] += 1
        unit_count[item["unit_code"]] += 1

    print("    按上级编号分布:")
    for area in sorted(area_count.keys()):
        print(f"      - {area}: {area_count[area]} 条")

    print("    按单元分布:")
    for unit in sorted(unit_count.keys(), key=lambda x: (x is None, str(x))):
        label = unit if unit else "未分配"
        print(f"      - {label}: {unit_count[unit]} 条")

    return imported


async def main(clean: bool = False) -> None:
    if not TAG_INFO_XLSX.exists():
        print(f"错误: 标签信息文件不存在: {TAG_INFO_XLSX}")
        return

    print(f"\n{'=' * 60}")
    print("1. 解析标签信息 Excel")
    print(f"{'=' * 60}")
    tags = parse_tag_info()
    print(f"  ✓ 标签信息: {len(tags)} 条")

    async with AsyncSessionLocal() as session:
        if clean:
            print(f"\n{'=' * 60}")
            print("2. 清理旧数据")
            print(f"{'=' * 60}")
            await clean_tag_data(session)

        print(f"\n{'=' * 60}")
        print("3. 导入标签数据")
        print(f"{'=' * 60}")
        imported = await import_tags(session, tags)

    print(f"\n{'=' * 60}")
    print("导入完成！")
    print(f"{'=' * 60}")
    print(f"标签总数: {len(imported)}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="标签信息导入")
    parser.add_argument("--clean", action="store_true", help="清空现有数据后重新导入")
    args = parser.parse_args()
    asyncio.run(main(clean=args.clean))
