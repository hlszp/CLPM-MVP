"""任务 Schema 测试."""

from app.schemas.task import (
    BackfillPreviewResult,
    BackfillTaskCreate,
    TaskResponse,
    TaskType,
)


def test_task_type_includes_backfill():
    """TaskType 枚举应包含 BACKFILL."""
    assert TaskType.BACKFILL.value == "BACKFILL"


def test_task_response_includes_backfill_fields():
    """TaskResponse 应包含 tsStart/tsEnd/loopIds/plantNodeIds 可选字段."""
    resp = TaskResponse(
        taskId="t1",
        taskType=TaskType.BACKFILL,
        status="PENDING",
        createdAt="2026-07-05T00:00:00Z",
        createdBy="admin",
        tsStart="2026-07-04T00:00:00Z",
        tsEnd="2026-07-05T00:00:00Z",
        loopIds=["loop-1"],
        plantNodeIds=["node-1"],
    )
    assert resp.tsStart == "2026-07-04T00:00:00Z"
    assert resp.tsEnd == "2026-07-05T00:00:00Z"
    assert resp.loopIds == ["loop-1"]
    assert resp.plantNodeIds == ["node-1"]


def test_backfill_task_create_camel_case():
    """BackfillTaskCreate 应支持 camelCase 别名."""
    body = BackfillTaskCreate.model_validate(
        {
            "tsStart": "2026-07-04T00:00:00Z",
            "tsEnd": "2026-07-05T00:00:00Z",
            "plantNodeIds": ["node-1"],
            "loopIds": ["loop-1"],
            "dryRun": True,
        }
    )
    assert body.tsStart == "2026-07-04T00:00:00Z"
    assert body.dryRun is True


def test_backfill_preview_result_fields():
    """BackfillPreviewResult 应包含 loopCount/windowCount/estimatedDurationSec/sampleLoopNames."""
    result = BackfillPreviewResult(
        loopCount=10,
        windowCount=24,
        estimatedDurationSec=480,
        sampleLoopNames=["L-001", "L-002"],
    )
    assert result.loopCount == 10
    assert result.windowCount == 24
