"""结构定义业务规则：管理表的字段结构，版本化管理。"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "schema_registry"
LIST_FIELDS = ["定义编号", "表名称", "字段名称", "字段类型", "可空标识", "默认值", "定义版本", "生效批次"]
REQUIRED_FIELDS = ["定义编号", "表名称", "字段名称"]
STATUS_ORDER = ["草稿", "待校验", "已生效", "已废弃"]
ACTION_RULES = {"提交校验": "待校验", "标记生效": "已生效", "标记废弃": "已废弃"}
NEGATIVE_ACTIONS = []


class SchemaRegistryService:
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
            rows = [row for row in rows if keyword.casefold() in str(row.get("定义编号", "")).casefold()]
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
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"结构定义 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于结构注册可执行范围"
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        store.save_version(MODULE, entry_id, entry)
        return entry, f"结构定义已{action}"

    def get_schema_for_table(self, table_name: str) -> list[dict[str, Any]]:
        return store.find_all_by_field(MODULE, "表名称", table_name)

    def get_latest_schema(self, table_name: str) -> dict[str, Any] | None:
        schemas = self.get_schema_for_table(table_name)
        if not schemas:
            return None
        # 找版本号最大的
        latest = max(schemas, key=lambda s: s.get("定义版本", "v0"))
        return latest

    def derive_schema_from_lineage(self, table_name: str) -> dict[str, Any] | None:
        """从血缘关系推导字段变更：找上游表最新schema并合并。"""
        # 查找血缘中该table_name作为下游的边
        upstream_tables = []
        for edge in store.get_lineage_edges():
            if edge.get("下游表名") == table_name:
                up = edge.get("上游表名", "")
                if up:
                    upstream_tables.append(up)
        if not upstream_tables:
            return None
        # 取第一个上游的最新schema
        up_schema = self.get_latest_schema(upstream_tables[0])
        if not up_schema:
            return None
        latest = self.get_latest_schema(table_name)
        new_version = "v2"
        if latest:
            try:
                vnum = int(latest.get("定义版本", "v1").replace("v", ""))
                new_version = f"v{vnum + 1}"
            except Exception:
                new_version = "v2"
        # 生成新schema定义
        new_entry = {
            "定义编号": f"SCH-DERIVED-{table_name}",
            "表名称": table_name,
            "字段名称": up_schema.get("字段名称", ""),
            "字段类型": up_schema.get("字段类型", ""),
            "可空标识": up_schema.get("可空标识", ""),
            "默认值": up_schema.get("默认值", ""),
            "定义版本": new_version,
            "生效批次": up_schema.get("生效批次", ""),
        }
        return self.create_entry(new_entry)[0]
