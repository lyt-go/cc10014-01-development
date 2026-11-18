"""字段映射业务规则：支持从入库批次提取源字段，映射到目标字段，并驱动ODS表结构创建。"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.store import store

MODULE = "column_mapping"
LIST_FIELDS = ["规则编号", "源字段名", "目标字段名", "数据类型", "默认值", "转换函数", "规则版本", "生效日期"]
REQUIRED_FIELDS = ["规则编号", "源字段名", "目标字段名"]
STATUS_ORDER = ["草稿", "待校验", "已生效", "已废弃"]
ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "标记废弃": "已废弃"}
NEGATIVE_ACTIONS = []


class ColumnMappingService:
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("规则编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        # 同一规则编号已经存在时不再追加一条同号记录：升版本并保留旧版本快照
        existing = self.get_by_rule_no(str(values.get("规则编号") or ""))
        if existing is not None:
            store.save_mapping_history(
                str(existing.get("规则编号")),
                existing,
                f"重新登记 {values.get('规则编号')}，旧版本转为历史快照",
            )
            for field in [*LIST_FIELDS, "来源批次", "目标表名"]:
                if field in values and values[field] is not None:
                    existing[field] = values[field]
            existing["规则版本"] = self._next_version(existing.get("规则版本"))
            store.save_version(MODULE, int(existing["id"]), existing)
            return existing, []
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        # 关联来源批次
        if values.get("来源批次"):
            entry["来源批次"] = values["来源批次"]
        if values.get("目标表名"):
            entry["目标表名"] = values["目标表名"]
        rows.append(entry)
        return entry, []

    @staticmethod
    def _next_version(version: Any) -> str:
        text = str(version or "v0").strip().lower()
        if text.startswith("v"):
            text = text[1:]
        try:
            return f"v{int(text) + 1}"
        except ValueError:
            return "v2"

    def get_by_rule_no(self, rule_no: str) -> dict[str, Any] | None:
        if not rule_no:
            return None
        return store.find_by_field(MODULE, "规则编号", rule_no)

    def apply_batch_mapping(self, batch_no: str, parsed: dict[str, Any]) -> dict[str, Any]:
        """消费入库解析结果，落一条与本批次一致的字段映射。

        重复批次与既有映射冲突时的处理边界：
        - 既有映射保留原字段顺序，本批次新字段按解析顺序追加到末尾；
        - 映射记录升版本，旧版本作为历史快照单独可查；
        - 返回本批次实际补写的字段，供清单单列「补写字段清单」。
        """
        rule_no = str(parsed.get("映射规则编号") or "") or f"MAP-{batch_no.replace('RAW-', '')}"
        sources = [str(detail["源字段"]) for detail in parsed.get("字段明细") or []]
        targets = [str(detail["目标字段"]) for detail in parsed.get("字段明细") or []]
        dtypes = [str(detail["数据类型"]) for detail in parsed.get("字段明细") or []]
        defaults = [str(detail.get("默认值") or "") for detail in parsed.get("字段明细") or []]
        transforms = [str(detail.get("转换函数") or "") for detail in parsed.get("字段明细") or []]
        target_table = str(parsed.get("目标表名") or "")
        existing = self.get_by_rule_no(rule_no)
        if existing is None:
            entry, missing = self.create_entry({
                "规则编号": rule_no,
                "源字段名": ",".join(sources),
                "目标字段名": ",".join(targets),
                "数据类型": ",".join(dtypes),
                "默认值": ",".join(defaults),
                "转换函数": ",".join(transforms),
                "规则版本": "v1",
                "生效日期": date.today().isoformat(),
                "来源批次": batch_no,
                "目标表名": target_table,
            })
            return {
                "entry": entry,
                "missing": missing,
                "existing": False,
                "补写字段清单": [],
                "冲突状态": "无冲突",
                "冲突说明": "",
            }

        old_sources = self._split(existing.get("源字段名"))
        old_targets = self._split(existing.get("目标字段名"))
        old_dtypes = self._split(existing.get("数据类型"))
        old_defaults = self._split(existing.get("默认值"))
        old_transforms = self._split(existing.get("转换函数"))
        appended: list[str] = []
        merged_sources = list(old_sources)
        merged_targets = list(old_targets)
        merged_dtypes = list(old_dtypes)
        merged_defaults = list(old_defaults)
        merged_transforms = list(old_transforms)
        for index, source in enumerate(sources):
            if source in merged_sources:
                continue
            appended.append(source)
            merged_sources.append(source)
            merged_targets.append(targets[index] if index < len(targets) else source)
            merged_dtypes.append(dtypes[index] if index < len(dtypes) else "")
            merged_defaults.append(defaults[index] if index < len(defaults) else "")
            merged_transforms.append(transforms[index] if index < len(transforms) else "")
        conflict = bool(appended)
        if conflict:
            store.save_mapping_history(
                rule_no, existing, f"批次 {batch_no} 补写字段：{'、'.join(appended)}",
            )
            existing["源字段名"] = ",".join(merged_sources)
            existing["目标字段名"] = ",".join(merged_targets)
            existing["数据类型"] = ",".join(merged_dtypes)
            existing["默认值"] = ",".join(merged_defaults)
            existing["转换函数"] = ",".join(merged_transforms)
            existing["规则版本"] = self._next_version(existing.get("规则版本"))
            existing["来源批次"] = batch_no
            existing["目标表名"] = target_table
            store.save_version(MODULE, int(existing["id"]), existing)
        return {
            "entry": existing,
            "missing": [],
            "existing": True,
            "补写字段清单": appended,
            "冲突状态": "重复批次" if conflict else "重复批次无新增字段",
            "冲突说明": (
                f"批次 {batch_no} 命中既有映射 {rule_no}，保留原字段顺序，补写 {len(appended)} 个字段"
                if conflict else f"批次 {batch_no} 命中既有映射 {rule_no}，字段完全一致，未改动映射"
            ),
        }

    @staticmethod
    def _split(value: Any) -> list[str]:
        return [part.strip() for part in str(value or "").split(",")]

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"映射规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于字段映射可执行范围"
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        store.save_version(MODULE, entry_id, entry)
        return entry, f"映射规则已{action}"

    def get_mappings_by_batch(self, batch_no: str) -> list[dict[str, Any]]:
        return store.find_all_by_field(MODULE, "来源批次", batch_no)

    def get_mapping_history(self, rule_no: str) -> list[dict[str, Any]]:
        """映射旧版本作为历史快照单独可查。"""
        return store.get_mapping_history(rule_no)

    def get_manifest_view(self, rule_no: str) -> dict[str, Any] | None:
        """跨模块消费入库批次清单：按规则编号找到引用该映射的清单摘要。"""
        for manifest in store.list_manifests():
            if str(manifest.get("映射规则编号") or "") == rule_no:
                return {
                    "批次编号": manifest.get("批次编号"),
                    "清单版本": manifest.get("清单版本"),
                    "原始数据行数": manifest.get("原始数据行数"),
                    "原始校验摘要": manifest.get("原始校验摘要"),
                    "清单校验摘要": manifest.get("清单校验摘要"),
                    "字段列表": list(manifest.get("字段列表") or []),
                    "补写字段清单": list(manifest.get("补写字段清单") or []),
                }
        return None
