"""入库批次清单：把原始入库文件解析结果与字段映射一起固化成可追溯、可交接的清单。

设计边界（确认冲突的裁决尚未定案，这里只固定兼容边界，不代替业务裁决）：

- 同一批次编号只保留一份完整清单；重新确认时整份替换，旧清单单独进入历史，不会出现
  「状态已更新、字段没落库」的半套记录。
- 重复批次与既有映射在同一目标表冲突时：既有映射保留原字段顺序，本批次新字段追加到
  末尾并在清单里单列「补写字段清单」；映射记录升版本，旧版本作为历史快照单独可查。
- 清单写入、导入、文件交接都在同一把锁内完成，并发写入只保留一个完整版本。
"""
from __future__ import annotations

import csv
import io
import json
import threading
from datetime import date
from typing import Any

from app.services.ingest_parse import parse_batch
from app.store import store

FIELDS = [
    "批次编号",
    "来源系统",
    "文件格式",
    "原始行数",
    "原始数据行数",
    "原始校验摘要",
    "清单校验摘要",
    "清单版本",
    "映射规则编号",
    "映射规则版本",
    "映射来源",
    "字段列表",
    "补写字段清单",
    "字段明细",
    "状态",
    "生成时间",
]
REQUIRED_IMPORT_FIELDS = [
    "批次编号",
    "原始数据行数",
    "原始校验摘要",
    "清单校验摘要",
    "映射规则编号",
    "映射规则版本",
    "字段列表",
    "字段明细",
]
_EXPORT_HEADERS = [
    "批次编号", "来源系统", "文件格式", "原始行数", "原始数据行数", "原始校验摘要",
    "清单校验摘要", "清单版本", "映射规则编号", "映射规则版本", "映射来源",
    "字段列表", "补写字段清单", "字段明细", "状态", "生成时间",
]

_LOCK = threading.RLock()


def _canonical(*, batch_no: str, source: str, fmt: str, row_count: int, raw_checksum: str,
               fields: list[str], details: list[dict[str, Any]], appended: list[str],
               rule_no: str, rule_version: str, mapping_source: str) -> str:
    payload = json.dumps(
        {
            "批次编号": batch_no,
            "来源系统": source,
            "文件格式": fmt,
            "原始数据行数": int(row_count),
            "原始校验摘要": raw_checksum,
            "字段列表": list(fields),
            "字段明细": details,
            "补写字段清单": list(appended),
            "映射规则编号": rule_no,
            "映射规则版本": rule_version,
            "映射来源": mapping_source,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    from app.services.ingest_parse import checksum_of
    return checksum_of(payload)


def manifest_checksum(manifest: dict[str, Any]) -> str:
    """覆盖清单全部业务字段（不含确认次数/生成时间等易变列），改动任何一处都能验出来。"""
    return _canonical(
        batch_no=str(manifest.get("批次编号") or ""),
        source=str(manifest.get("来源系统") or ""),
        fmt=str(manifest.get("文件格式") or ""),
        row_count=int(str(manifest.get("原始数据行数") or 0) or 0),
        raw_checksum=str(manifest.get("原始校验摘要") or ""),
        fields=[str(item) for item in (manifest.get("字段列表") or [])],
        details=[dict(item) for item in (manifest.get("字段明细") or [])],
        appended=[str(item) for item in (manifest.get("补写字段清单") or [])],
        rule_no=str(manifest.get("映射规则编号") or ""),
        rule_version=str(manifest.get("映射规则版本") or ""),
        mapping_source=str(manifest.get("映射来源") or ""),
    )


def _batch_row(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "entry_id": int(entry.get("id", 0) or 0),
        "批次编号": str(entry.get("批次编号") or ""),
        "来源系统": str(entry.get("来源系统") or ""),
        "文件格式": str(entry.get("文件格式") or ""),
        "原始行数": str(entry.get("原始行数") or ""),
        "入库时间": str(entry.get("入库时间") or ""),
        "字符编码": str(entry.get("字符编码") or ""),
        "校验摘要": str(entry.get("校验摘要") or ""),
        "落地路径": str(entry.get("落地路径") or ""),
        "状态": str(entry.get("status") or ""),
    }


class IngestManifestService:
    """入库批次清单的读写、导入导出与文件交接。"""

    # ---- 写入 ----

    def confirm_batch(self, entry: dict[str, Any], raw_text: str | None = None) -> tuple[dict[str, Any] | None, str]:
        """确认入库时把原始摘要与字段映射一起落进清单。

        返回 ``(manifest, message)``；无法完成落库时 manifest 为 None，调用方不得推进状态。
        """
        batch_no = str(entry.get("批次编号") or "").strip()
        if not batch_no:
            return None, "批次编号为空，无法生成入库批次清单"
        with _LOCK:
            parsed = parse_batch(entry, raw_text)
            if parsed["解析错误"]:
                return None, parsed["解析错误"]
            if not parsed["字段列表"]:
                return None, "原始文件未解析出字段，清单缺少字段列表，已阻止确认"

            from app.services.column_mapping import ColumnMappingService
            mapping_result = ColumnMappingService().apply_batch_mapping(batch_no, parsed)
            mapping = mapping_result.get("entry")
            if not mapping:
                return None, "字段映射落库失败，清单不完整，已阻止状态更新"
            previous = store.get_manifest(batch_no) or {}

            manifest: dict[str, Any] = {
                **_batch_row(entry),
                "原始数据行数": int(parsed["行数"]),
                "原始校验摘要": str(parsed["校验摘要"]),
                "解析校验摘要": str(parsed.get("解析校验摘要") or ""),
                "解析来源": parsed.get("解析来源", ""),
                "映射规则编号": str(mapping.get("规则编号") or ""),
                "映射规则版本": str(mapping.get("规则版本") or ""),
                "映射来源": ("本批次生成" if str(mapping.get("来源批次") or "") in ("", batch_no)
                             else "既有映射"),
                "字段列表": list(parsed["字段列表"]),
                "字段明细": [dict(detail) for detail in parsed["字段明细"]],
                "补写字段清单": list(mapping_result.get("补写字段清单") or []),
                "映射快照": {
                    "规则编号": mapping.get("规则编号", ""),
                    "规则版本": mapping.get("规则版本", ""),
                    "源字段名": mapping.get("源字段名", ""),
                    "目标字段名": mapping.get("目标字段名", ""),
                    "数据类型": mapping.get("数据类型", ""),
                    "默认值": mapping.get("默认值", ""),
                    "转换函数": mapping.get("转换函数", ""),
                    "来源批次": mapping.get("来源批次", ""),
                    "目标表名": mapping.get("目标表名", ""),
                },
                "冲突状态": mapping_result.get("冲突状态", "无冲突"),
                "冲突说明": mapping_result.get("冲突说明", ""),
                "确认次数": int(previous.get("确认次数", 0)) + 1,
            }
            manifest["清单校验摘要"] = manifest_checksum(manifest)
            record = store.save_manifest(batch_no, manifest)
            # 回填批次表，便于列表/导出直接看到清单是否齐备
            entry["清单校验摘要"] = record["清单校验摘要"]
            entry["清单版本"] = record["清单版本"]
            entry["字段列表"] = ",".join(record["字段列表"])
            message = f"入库批次清单已生成（{record['清单版本']}）"
            if record["补写字段清单"]:
                message += f"，补写字段：{'、'.join(record['补写字段清单'])}"
            return record, message

    def _write_mapping_history_on_reload(self, manifest: dict[str, Any]) -> None:
        """导入/交接回来的清单不改写映射主记录，只在历史里留一份来源说明。"""
        from app.services.column_mapping import ColumnMappingService
        mapping = ColumnMappingService().get_by_rule_no(str(manifest.get("映射规则编号") or ""))
        if mapping is None:
            return
        store.save_mapping_history(
            str(mapping.get("规则编号")),
            mapping,
            f"入库批次清单 {manifest.get('批次编号', '')} 由文件交接写入",
        )

    # ---- 读取 ----

    def list_manifests(self) -> list[dict[str, Any]]:
        with _LOCK:
            rows = store.list_manifests()
        return sorted(rows, key=lambda item: str(item.get("生成时间", "")), reverse=True)

    def get_manifest(self, batch_no: str) -> dict[str, Any] | None:
        with _LOCK:
            return store.get_manifest(batch_no)

    def get_manifest_detail(self, batch_no: str) -> dict[str, Any] | None:
        with _LOCK:
            manifest = store.get_manifest(batch_no)
            if manifest is None:
                return None
            from app.services.column_mapping import ColumnMappingService
            cms = ColumnMappingService()
            rule_no = str(manifest.get("映射规则编号") or "")
            return {
                "manifest": manifest,
                "history": store.get_manifest_history(batch_no),
                "mapping": cms.get_by_rule_no(rule_no) if rule_no else None,
                "mapping_history": store.get_mapping_history(rule_no) if rule_no else [],
            }

    def verify(self, manifest: dict[str, Any]) -> tuple[bool, str]:
        expected = str(manifest.get("清单校验摘要") or "")
        actual = manifest_checksum(manifest)
        if expected != actual:
            return False, f"清单校验摘要不一致：清单记录 {expected or '空'}，重算 {actual}"
        return True, "清单校验通过"

    def get_manifest_history(self, batch_no: str) -> list[dict[str, Any]]:
        with _LOCK:
            return store.get_manifest_history(batch_no)

    # ---- 导出 ----

    def export_csv(self, *, batch_no: str | None = None) -> str:
        rows = self.list_manifests()
        if batch_no:
            rows = [row for row in rows if row.get("批次编号") == batch_no]
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(_EXPORT_HEADERS)
        for row in rows:
            writer.writerow([
                row.get("批次编号", ""),
                row.get("来源系统", ""),
                row.get("文件格式", ""),
                row.get("原始行数", ""),
                row.get("原始数据行数", ""),
                row.get("原始校验摘要", ""),
                row.get("清单校验摘要", ""),
                row.get("清单版本", ""),
                row.get("映射规则编号", ""),
                row.get("映射规则版本", ""),
                row.get("映射来源", ""),
                ",".join(row.get("字段列表") or []),
                ",".join(row.get("补写字段清单") or []),
                json.dumps(row.get("字段明细") or [], ensure_ascii=False),
                row.get("状态", ""),
                row.get("生成时间", ""),
            ])
        return output.getvalue()

    # ---- 导入 ----

    def import_csv(self, content: str, *, source_name: str = "导入文件") -> dict[str, Any]:
        """导入清单 CSV：逐行校验必填列、行数与清单校验摘要，校验不过的整行拒绝。"""
        if not content.strip():
            return {"ok": False, "message": "导入文件为空", "imported": [], "rejected": []}
        reader = csv.DictReader(io.StringIO(content.lstrip("﻿")))
        headers = [str(name or "").strip() for name in (reader.fieldnames or [])]
        # 兼容带 BOM 或不带 BOM 的导出文件
        normalized_headers = [header.lstrip("﻿").strip() for header in headers]
        reader.fieldnames = normalized_headers
        missing_headers = [field for field in REQUIRED_IMPORT_FIELDS if field not in normalized_headers]
        if missing_headers:
            return {
                "ok": False,
                "message": f"导入文件缺少列：{'、'.join(missing_headers)}",
                "imported": [],
                "rejected": [],
            }
        imported: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        with _LOCK:
            for line_no, raw in enumerate(reader, start=2):
                row = {(key or "").strip(): (value or "").strip() for key, value in raw.items() if key}
                batch_no = row.get("批次编号", "")
                if not batch_no:
                    rejected.append({"行号": str(line_no), "原因": "批次编号为空"})
                    continue
                try:
                    details = json.loads(row.get("字段明细") or "[]")
                except json.JSONDecodeError:
                    rejected.append({"行号": str(line_no), "原因": "字段明细不是合法 JSON"})
                    continue
                if not isinstance(details, list) or not details:
                    rejected.append({"行号": str(line_no), "原因": "字段明细为空，清单不完整"})
                    continue
                if not row.get("映射规则编号") or not row.get("映射规则版本"):
                    rejected.append({"行号": str(line_no), "原因": "缺少映射来源，拒绝写入半套清单"})
                    continue
                try:
                    row_count = int(row.get("原始数据行数") or "0")
                except ValueError:
                    rejected.append({"行号": str(line_no), "原因": "原始数据行数不是整数"})
                    continue
                fields = [item.strip() for item in row.get("字段列表", "").split(",") if item.strip()]
                manifest: dict[str, Any] = {
                    "批次编号": batch_no,
                    "来源系统": row.get("来源系统", ""),
                    "文件格式": row.get("文件格式", ""),
                    "原始行数": row.get("原始行数", ""),
                    "原始数据行数": row_count,
                    "原始校验摘要": row.get("原始校验摘要", ""),
                    "清单校验摘要": row.get("清单校验摘要", ""),
                    "映射规则编号": row.get("映射规则编号", ""),
                    "映射规则版本": row.get("映射规则版本", ""),
                    "映射来源": row.get("映射来源", "导入文件") or "导入文件",
                    "字段列表": fields,
                    "补写字段清单": [item.strip() for item in row.get("补写字段清单", "").split(",") if item.strip()],
                    "字段明细": details,
                    "状态": row.get("状态", ""),
                    "生成时间": row.get("生成时间", ""),
                    "导入来源": source_name,
                    "解析来源": "导入清单文件",
                }
                ok, reason = self.verify(manifest)
                if not ok:
                    rejected.append({"行号": str(line_no), "原因": reason})
                    continue
                if len(fields) != len(details):
                    rejected.append({"行号": str(line_no), "原因": "字段列表与字段明细数量不一致"})
                    continue
                existing = store.get_manifest(batch_no)
                if existing is not None:
                    same, _ = self.verify(existing)
                    if same and existing.get("清单校验摘要") == manifest["清单校验摘要"]:
                        imported.append({"批次编号": batch_no, "结果": "已存在，未重复写入"})
                        continue
                    # 导入版本与现有版本不同：现有版本保留为历史，导入版本成为唯一有效版本
                    store.save_manifest(batch_no, manifest)
                    imported.append({"批次编号": batch_no, "结果": "已替换现有清单，旧版本转入历史"})
                else:
                    store.save_manifest(batch_no, manifest, supersede=False)
                    imported.append({"批次编号": batch_no, "结果": "新增"})
                self._write_mapping_history_on_reload(manifest)
        message = f"导入完成：成功 {len(imported)} 行，拒绝 {len(rejected)} 行"
        return {"ok": not rejected, "message": message, "imported": imported, "rejected": rejected}

    # ---- 文件交接 ----

    def handover(self, entry_id: int) -> dict[str, Any] | None:
        """沿原始入库调用链定位批次结果，生成可交接的清单文件内容。"""
        from app.services.raw_ingest import RawIngestService
        ri = RawIngestService()
        batch = ri.get_entry(entry_id)
        if batch is None:
            return None
        return self._handover_batch(batch)

    def handover_by_batch(self, batch_no: str) -> dict[str, Any] | None:
        """按批次编号沿原始入库调用链定位入库批次，再生成交接文件。"""
        from app.services.raw_ingest import RawIngestService
        batch = RawIngestService().get_by_batch_no(batch_no)
        if batch is None:
            return None
        return self._handover_batch(batch)

    def _handover_batch(self, batch: dict[str, Any]) -> dict[str, Any]:
        entry_id = int(batch.get("id", 0) or 0)
        batch_no = str(batch.get("批次编号") or "")
        with _LOCK:
            manifest = store.get_manifest(batch_no)
            if manifest is None:
                # 尚未确认入库：不凭空编造清单，交给调用方提示先确认
                return {
                    "批次编号": batch_no,
                    "entry_id": entry_id,
                    "清单": None,
                    "message": "该批次尚未生成入库批次清单，请先确认入库",
                }
            from app.services.column_mapping import ColumnMappingService
            mapping = ColumnMappingService().get_by_rule_no(str(manifest.get("映射规则编号") or ""))
            payload = {
                "交接编号": f"HO-{batch_no}-{date.today().strftime('%Y%m%d')}",
                "交接时间": date.today().isoformat(),
                "entry_id": entry_id,
                "批次编号": batch_no,
                "原始摘要": {
                    "来源系统": manifest.get("来源系统", ""),
                    "文件格式": manifest.get("文件格式", ""),
                    "原始行数": manifest.get("原始行数", ""),
                    "原始数据行数": manifest.get("原始数据行数", 0),
                    "原始校验摘要": manifest.get("原始校验摘要", ""),
                    "落地路径": manifest.get("落地路径", ""),
                },
                "映射": {
                    "规则编号": manifest.get("映射规则编号", ""),
                    "规则版本": manifest.get("映射规则版本", ""),
                    "映射来源": manifest.get("映射来源", ""),
                    "快照": manifest.get("映射快照", {}),
                    "当前记录": mapping,
                },
                "清单": manifest,
                "清单校验": self.verify(manifest)[1],
            }
            store.add_archive_record({"类型": "入库批次清单交接", **payload})
            return payload

    def import_handover(self, payload: dict[str, Any]) -> dict[str, Any]:
        """接收交接文件：校验清单后写回，清单不完整时整体拒绝。"""
        manifest = payload.get("清单") if isinstance(payload, dict) else None
        if not isinstance(manifest, dict) or not manifest:
            return {"ok": False, "message": "交接文件缺少入库批次清单"}
        with _LOCK:
            ok, reason = self.verify(manifest)
            if not ok:
                return {"ok": False, "message": reason}
            batch_no = str(manifest.get("批次编号") or "")
            if not batch_no:
                return {"ok": False, "message": "清单缺少批次编号"}
            store.save_manifest(batch_no, manifest)
            self._write_mapping_history_on_reload(manifest)
        return {"ok": True, "message": f"交接清单已接收：{batch_no}", "manifest": store.get_manifest(batch_no)}


def ensure_manifest_for_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    """只读场景补一份清单视图：已有清单直接返回，没有就按批次数据生成（不落库）。

    列表页进入详情页时用，避免为了看一眼摘要就写库。
    """
    batch_no = str(entry.get("批次编号") or "")
    existing = store.get_manifest(batch_no)
    if existing is not None:
        return existing
    with _LOCK:
        parsed = parse_batch(entry)
        manifest = {
            **_batch_row(entry),
            "原始数据行数": int(parsed["行数"]),
            "原始校验摘要": str(parsed["校验摘要"]),
            "解析校验摘要": str(parsed.get("解析校验摘要") or ""),
            "解析来源": parsed.get("解析来源", ""),
            "映射规则编号": str(parsed.get("映射规则编号") or ""),
            "映射规则版本": "",
            "映射来源": "尚未写入（待确认）",
            "字段列表": list(parsed["字段列表"]),
            "字段明细": [dict(detail) for detail in parsed["字段明细"]],
            "补写字段清单": [],
            "映射快照": {},
            "冲突状态": "尚未确认",
            "冲突说明": "",
            "清单版本": "未生成",
            "确认次数": 0,
        }
        manifest["清单校验摘要"] = ""
        return manifest
