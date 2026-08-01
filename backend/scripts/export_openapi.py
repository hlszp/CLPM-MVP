"""导出 OpenAPI 静态规范文件。

将 FastAPI 应用的 OpenAPI 3.1 schema 导出为 JSON 文件，包含所有
response_model 声明，便于前端代码生成、API 网关配置与文档归档。

用法::

    # 方式 1：直接运行脚本（默认输出 backend/openapi.json）
    uv run python scripts/export_openapi.py

    # 方式 2：作为模块运行
    uv run python -m scripts.export_openapi

    # 指定输出路径
    uv run python scripts/export_openapi.py --output /tmp/openapi.json

    # 刷新契约基线（V62-P0-038）：API 变更后需同步刷新基线，
    # 否则 tests/test_openapi_contract_drift.py 会判漂移失败。
    uv run python scripts/export_openapi.py --output tests/golden/openapi_baseline.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.main import create_app

DEFAULT_OUTPUT = "openapi.json"


def export_openapi(output_path: str = DEFAULT_OUTPUT) -> Path:
    """导出 OpenAPI JSON 规范到指定路径。

    Args:
        output_path: 输出文件路径，默认为当前目录下的 ``openapi.json``。

    Returns:
        写入文件的 :class:`~pathlib.Path` 对象。
    """
    app = create_app()
    openapi_schema = app.openapi()
    output = Path(output_path)
    output.write_text(
        json.dumps(openapi_schema, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"OpenAPI 规范已导出到 {output.resolve()}")
    return output


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="导出 CLPM 后端 OpenAPI 静态规范文件。",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=DEFAULT_OUTPUT,
        help=f"输出文件路径（默认：{DEFAULT_OUTPUT}）",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    export_openapi(args.output)
