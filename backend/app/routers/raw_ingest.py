"""原始入库 时间线接口：返回作业时间序列结构。"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload
from app.services.ingest_parse import parse_batch
from app.services.raw_ingest import RawIngestService

router = APIRouter(prefix="/api/raw_ingest", tags=["原始入库"])
service = RawIngestService()
LIST_FIELDS = ["批次编号", "来源系统", "文件格式", "原始行数", "入库时间", "字符编码", "校验摘要", "落地路径"]
STATUSES = ["草稿", "校验中", "已入库", "已归档"]


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


@router.get("")
def list_entries() -> dict[str, Any]:
    items = service.get_timeline()
    return {"items": items}


@router.get("/export")
def export_entries() -> StreamingResponse:
    items = service.get_timeline()
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    all_keys = ["id"]
    if items:
        all_keys = list(items[0].keys())
    writer.writerow(all_keys)
    for item in items:
        writer.writerow([csv_cell(item.get(k, "")) for k in all_keys])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=raw_ingest.csv"})


@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"入库批次 {entry_id} 不存在")
    return entry


@router.post("/preview")
def preview_raw_file(payload: dict[str, Any] = Body(...)) -> dict:
    """解析原始入库文件，返回字段、行数与校验摘要；确认入库前可先核对。"""
    content = str(payload.get("content") or "")
    if not content.strip():
        return {"ok": False, "message": "原始文件内容为空", "parsed": None}
    entry = {
        "批次编号": str(payload.get("批次编号") or ""),
        "来源系统": str(payload.get("来源系统") or ""),
        "文件格式": str(payload.get("文件格式") or ""),
        "原始行数": str(payload.get("原始行数") or ""),
        "校验摘要": str(payload.get("校验摘要") or ""),
        "落地路径": str(payload.get("落地路径") or ""),
    }
    parsed = parse_batch(entry, content)
    if parsed["解析错误"]:
        return {"ok": False, "message": parsed["解析错误"], "parsed": parsed}
    return {"ok": True, "message": f"解析出 {len(parsed['字段列表'])} 个字段、{parsed['行数']} 行", "parsed": parsed}


@router.post("")
def create_entry(payload: EntryPayload) -> ActionResult:
    entry_or_none, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="入库批次已登记", entry=entry_or_none)


@router.post("/{entry_id}/actions")
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    raw_text = payload.raw_content
    entry_or_none, message = service.run_action(entry_id, payload.action, raw_text)
    if entry_or_none is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry_or_none)
