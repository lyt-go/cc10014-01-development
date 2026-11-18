"""入库批次清单接口：解析入库原始文件，读写批次清单，并支持导入导出与文件交接。"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.services.ingest_manifest import IngestManifestService, ensure_manifest_for_entry
from app.services.raw_ingest import RawIngestService

router = APIRouter(prefix="/api/ingest_manifest", tags=["入库批次清单"])

service = IngestManifestService()


@router.get("")
def list_manifests() -> dict[str, Any]:
    """列出已生成的入库批次清单（每个批次编号只出现一份有效版本）。"""
    items = service.list_manifests()
    return {"items": items, "total": len(items)}


@router.get("/export")
def export_manifests(batch_no: str | None = Query(default=None)) -> StreamingResponse:
    """导出清单 CSV；必须声明在 /{batch_no} 之前。"""
    content = "﻿" + service.export_csv(batch_no=batch_no)
    filename = f"ingest_manifest_{batch_no}.csv" if batch_no else "ingest_manifest.csv"
    return StreamingResponse(
        iter([content]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/by-entry/{entry_id}")
def get_manifest_by_entry(entry_id: int) -> dict[str, Any]:
    """从原始入库批次定位清单：已有清单直接返回，未确认时返回按批次数据生成的只读预览。"""
    entry = RawIngestService().get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"入库批次 {entry_id} 不存在")
    manifest = ensure_manifest_for_entry(entry)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"入库批次 {entry_id} 未生成清单")
    return {"entry_id": entry_id, "manifest": manifest,
            "history": service.get_manifest_history(str(manifest.get("批次编号") or "")),
            "confirmed": bool(manifest.get("清单校验摘要"))}


@router.post("/import")
def import_manifests(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """导入清单 CSV：``{"content": "...", "source_name": "..."}``。校验失败的整行拒绝。"""
    content = str(payload.get("content") or "")
    source_name = str(payload.get("source_name") or "导入文件")
    result = service.import_csv(content, source_name=source_name)
    return result


@router.post("/handover/import")
def import_handover(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """接收交接文件：清单校验摘要不一致时整体拒绝，避免落一份坏清单。

    必须声明在 /handover/{entry_id} 之前，否则 import 会被当成 entry_id 解析。
    """
    result = service.import_handover(payload)
    return result


@router.post("/handover/by-batch/{batch_no}")
def handover_manifest_by_batch(batch_no: str) -> dict[str, Any]:
    """按批次编号定位入库批次后生成交接文件；清单列表页不需要知道 entry_id。"""
    payload = service.handover_by_batch(batch_no)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 不存在")
    if payload.get("清单") is None:
        return {"ok": False, "message": payload.get("message", "尚未生成清单"), "handover": payload}
    return {"ok": True, "message": "交接文件已生成", "handover": payload}


@router.get("/handover/{entry_id}/download")
def download_handover(entry_id: int) -> StreamingResponse:
    """把交接文件下载为 JSON，可直接交给下游模块导入。"""
    payload = service.handover(entry_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"入库批次 {entry_id} 不存在")
    if payload.get("清单") is None:
        raise HTTPException(status_code=409, detail=payload.get("message", "尚未生成清单"))
    batch_no = str(payload.get("批次编号") or "")
    content = json.dumps(payload, ensure_ascii=False, indent=2)
    return StreamingResponse(
        iter([content]), media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="handover_{batch_no}.json"'})


@router.post("/handover/{entry_id}")
def handover_manifest(entry_id: int) -> dict[str, Any]:
    """沿原始入库调用链定位批次结果，生成交接文件内容。"""
    payload = service.handover(entry_id)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"入库批次 {entry_id} 不存在")
    if payload.get("清单") is None:
        return {"ok": False, "message": payload.get("message", "尚未生成清单"), "handover": payload}
    return {"ok": True, "message": "交接文件已生成", "handover": payload}


@router.get("/{batch_no}")
def get_manifest(batch_no: str) -> dict[str, Any]:
    """读取单个批次的清单详情：清单本体、历史快照、当前映射与映射历史。"""
    detail = service.get_manifest_detail(batch_no)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"批次 {batch_no} 的入库批次清单不存在")
    ok, reason = service.verify(detail["manifest"])
    return {**detail, "校验": {"ok": ok, "message": reason}}
