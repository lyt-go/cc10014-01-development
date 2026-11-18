"""原始入库 时间线服务：支持入库提交后触发字段映射规则提取，并关联创建ODS表结构。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.store import store

MODULE = "raw_ingest"
REQUIRED_FIELDS = ["批次编号", "来源系统", "文件格式"]
STATUS_ORDER = ["草稿", "校验中", "已入库", "已归档"]
ACTION_RULES = {"发起校验": "校验中", "确认入库": "已入库", "归档批次": "已归档"}
NEGATIVE_ACTIONS = []


class RawIngestService:
    def get_timeline(self) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        today = date.today()
        items: list[dict] = []
        for i, row in enumerate(rows):
            start = (today - timedelta(days=7 - i * 2)).isoformat()
            end = (today + timedelta(days=3 - i)).isoformat()
            items.append({
                "id": int(row.get("id", i + 1)),
                "name": str(row.get("批次编号", "")),
                "source": str(row.get("来源系统", "")),
                "format": str(row.get("文件格式", "")),
                "start": start,
                "end": end,
                "status": str(row.get("status", STATUS_ORDER[i % len(STATUS_ORDER)])),
                "logs": f"[{start}] 作业启动\n[{start}] 正在处理...\n[{end}] 任务{'完成' if row.get('status') == STATUS_ORDER[-1] else '进行中'}",
            })
        return items

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def get_by_batch_no(self, batch_no: str) -> dict[str, Any] | None:
        return store.find_by_field(MODULE, "批次编号", batch_no)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [f for f in REQUIRED_FIELDS if not str(values.get(f) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(r.get("id", 0)) for r in rows), default=0) + 1}
        entry.update({f: values.get(f) for f in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        # 补全其他字段
        for k in ["原始行数", "入库时间", "字符编码", "校验摘要", "落地路径"]:
            if k not in entry or not entry.get(k):
                entry[k] = values.get(k, "")
        if not entry.get("入库时间"):
            entry["入库时间"] = date.today().isoformat()
        # 登记时带上原始文件内容，确认入库时直接解析，不必二次上传
        if values.get("原始内容"):
            entry["原始内容"] = str(values["原始内容"])
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str,
                   raw_text: str | None = None) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"入库批次 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于原始入库可执行范围"
        target = ACTION_RULES[action]
        # 状态机校验：只能按顺序推进
        current_idx = STATUS_ORDER.index(entry["status"])
        target_idx = STATUS_ORDER.index(target)
        if target_idx < current_idx:
            return None, f"状态不能回退：当前{entry['status']} → 目标{target}"
        if action == "确认入库":
            # 清单是确认入库的落库产物：清单写不完整就不推进状态，避免留下半套记录
            from app.services.ingest_manifest import IngestManifestService
            manifest, message = IngestManifestService().confirm_batch(entry, raw_text)
            if manifest is None:
                return None, message
            entry["status"] = target
            entry["pending"] = target != STATUS_ORDER[-1]
            entry["abnormal"] = action in NEGATIVE_ACTIONS
            store.save_version(MODULE, entry_id, dict(entry))
            self._on_ingest_confirmed(entry, manifest)
            return entry, message
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        # 保存版本
        store.save_version(MODULE, entry_id, entry)
        return entry, f"入库批次已{action}"

    def _on_ingest_confirmed(self, entry: dict[str, Any], manifest: dict[str, Any]) -> None:
        batch_no = entry.get("批次编号", "")
        source = entry.get("来源系统", "")
        fmt = entry.get("文件格式", "")
        # 字段映射已在生成清单时落库，这里直接沿清单快照建 ODS 表与 schema
        mapping_snapshot = manifest.get("映射快照") or {}
        src_fields = str(mapping_snapshot.get("源字段名") or "")
        tgt_fields = str(mapping_snapshot.get("目标字段名") or "")
        dtypes = str(mapping_snapshot.get("数据类型") or "")
        tbl_name = str(mapping_snapshot.get("目标表名") or "") or f"ods_{source}"
        # 自动创建/更新ODS表结构
        from app.services.ods_layer import OdsLayerService
        ods = OdsLayerService()
        existing = store.find_by_field("ods_layer", "表名称", tbl_name)
        if existing is None:
            ods.create_entry({
                "表编号": f"ODS_{batch_no.replace('RAW-', '').replace('-', '_')}",
                "来源系统": source,
                "表名称": tbl_name,
                "分区字段": "dt",
                "存储格式": fmt if fmt else "PARQUET",
                "保留天数": "90",
                "压缩算法": "SNAPPY",
                "创建批次": batch_no,
                "字段列表": tgt_fields,
            })
        else:
            # 更新版本
            existing["创建批次"] = batch_no
            existing["字段列表"] = tgt_fields
            store.save_version("ods_layer", existing["id"], existing)
        # 自动注册schema
        from app.services.schema_registry import SchemaRegistryService
        sch = SchemaRegistryService()
        schema_no = f"SCH-{batch_no.replace('RAW-', '')}"
        field_count = max(len([f for f in tgt_fields.split(",") if f.strip()]), 1)
        type_list = [t.strip() for t in dtypes.split(",") if t.strip()]
        existing_schema = store.find_by_field("schema_registry", "定义编号", schema_no)
        if existing_schema is None:
            sch.create_entry({
                "定义编号": schema_no,
                "表名称": tbl_name,
                "字段名称": tgt_fields,
                "字段类型": dtypes,
                "可空标识": ",".join(["N"] * field_count),
                "默认值": ",".join([""] * len(type_list)) if type_list else "",
                "定义版本": "v1",
                "生效批次": batch_no,
            })
        else:
            # 重复确认同一批次：schema 升版本，不追加同号定义
            from app.services.column_mapping import ColumnMappingService
            store.save_version("schema_registry", existing_schema["id"], existing_schema)
            existing_schema["字段名称"] = tgt_fields
            existing_schema["字段类型"] = dtypes
            existing_schema["定义版本"] = ColumnMappingService._next_version(
                existing_schema.get("定义版本"))
            existing_schema["生效批次"] = batch_no
        # 写入模拟表数据
        row_count = int(manifest.get("原始数据行数") or entry.get("原始行数", "0") or "0")
        if row_count > 0:
            sample_rows = []
            for i in range(min(row_count, 10)):  # 最多模拟10条
                sample_rows.append({"_id": i + 1, "batch": batch_no, "source": source})
            store.set_table_rows(tbl_name, sample_rows)
