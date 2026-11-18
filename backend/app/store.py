"""内存数据仓库：支持模块间关联、版本管理、模拟表数据、血缘记录与状态流转。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 模拟真实表数据（分层存储的实际行数据）
        self._table_data: dict[str, list[dict[str, Any]]] = {}
        # 版本历史: "module:id" -> [{version, timestamp, data}, ...]
        self._versions: dict[str, list[dict[str, Any]]] = {}
        # 血缘边记录
        self._lineage_edges: list[dict[str, Any]] = []
        # 质量稽核结果
        self._quality_results: dict[str, list[dict[str, Any]]] = {}
        # 快照冻结数据: "snapshot_id" -> {table_name, rows, frozen_at}
        self._snapshot_data: dict[str, dict[str, Any]] = {}
        # 归档记录
        self._archive_records: list[dict[str, Any]] = []
        # 补数区间记录
        self._backfill_partitions: dict[str, list[dict[str, Any]]] = {}
        # 入库批次清单: 批次编号 -> 当前唯一有效清单
        self._manifests: dict[str, dict[str, Any]] = {}
        # 入库批次清单历史（被覆盖的完整清单快照）
        self._manifest_history: dict[str, list[dict[str, Any]]] = {}
        # 字段映射历史快照: 规则编号 -> [{版本, 时间, 数据, 原因}]
        self._mapping_history: dict[str, list[dict[str, Any]]] = {}

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def find_by_field(self, module: str, field: str, value: Any) -> dict[str, Any] | None:
        for row in self.rows(module):
            if row.get(field) == value:
                return row
        return None

    def find_all_by_field(self, module: str, field: str, value: Any) -> list[dict[str, Any]]:
        return [row for row in self.rows(module) if row.get(field) == value]

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}

    # ---- 模拟真实表数据 ----

    def table_rows(self, table_name: str) -> list[dict[str, Any]]:
        return self._table_data.setdefault(table_name, [])

    def set_table_rows(self, table_name: str, rows: list[dict[str, Any]]) -> None:
        self._table_data[table_name] = rows

    def append_table_rows(self, table_name: str, rows: list[dict[str, Any]]) -> None:
        self._table_data.setdefault(table_name, []).extend(rows)

    # ---- 版本管理 ----

    def _version_key(self, module: str, entry_id: int) -> str:
        return f"{module}:{entry_id}"

    def save_version(self, module: str, entry_id: int, data: dict[str, Any]) -> dict[str, Any]:
        key = self._version_key(module, entry_id)
        versions = self._versions.setdefault(key, [])
        version = {
            "version": len(versions) + 1,
            "timestamp": datetime.now().isoformat(),
            "data": dict(data),
        }
        versions.append(version)
        return version

    def get_versions(self, module: str, entry_id: int) -> list[dict[str, Any]]:
        key = self._version_key(module, entry_id)
        return list(self._versions.get(key, []))

    def get_latest_version(self, module: str, entry_id: int) -> dict[str, Any] | None:
        versions = self.get_versions(module, entry_id)
        return versions[-1] if versions else None

    # ---- 血缘 ----

    def add_lineage(self, upstream: str, downstream: str, field_map: str,
                    transform_logic: str = "", batch_id: str = "") -> dict[str, Any]:
        edge = {
            "id": len(self._lineage_edges) + 1,
            "上游表名": upstream,
            "下游表名": downstream,
            "来源字段": field_map,
            "目标字段": field_map,
            "转换逻辑": transform_logic,
            "采集批次": batch_id,
            "链路版本": f"v{len(self._lineage_edges) + 1}",
            "status": "已生效",
            "pending": False,
            "abnormal": False,
        }
        self._lineage_edges.append(edge)
        # 同时写入 lineage_track 模块表供查询
        self.rows("lineage_track").append(edge)
        return edge

    def get_lineage_edges(self) -> list[dict[str, Any]]:
        return list(self._lineage_edges)

    # ---- 质量稽核结果 ----

    def save_quality_result(self, rule_id: str, result: dict[str, Any]) -> None:
        self._quality_results.setdefault(rule_id, []).append(result)

    def get_quality_results(self, rule_id: str) -> list[dict[str, Any]]:
        return list(self._quality_results.get(rule_id, []))

    # ---- 快照 ----

    def save_snapshot_data(self, snapshot_id: str, table_name: str, rows: list[dict[str, Any]]) -> None:
        self._snapshot_data[snapshot_id] = {
            "table_name": table_name,
            "rows": [dict(r) for r in rows],
            "frozen_at": datetime.now().isoformat(),
            "row_count": len(rows),
        }

    def get_snapshot_data(self, snapshot_id: str) -> dict[str, Any] | None:
        return self._snapshot_data.get(snapshot_id)

    # ---- 归档 ----

    def add_archive_record(self, record: dict[str, Any]) -> None:
        self._archive_records.append(record)

    def get_archive_records(self) -> list[dict[str, Any]]:
        return list(self._archive_records)

    # ---- 补数分区 ----

    def save_backfill_partition(self, job_id: str, partition: dict[str, Any]) -> None:
        self._backfill_partitions.setdefault(job_id, []).append(partition)

    def get_backfill_partitions(self, job_id: str) -> list[dict[str, Any]]:
        return list(self._backfill_partitions.get(job_id, []))

    # ---- 入库批次清单 ----

    def get_manifest(self, batch_no: str) -> dict[str, Any] | None:
        manifest = self._manifests.get(batch_no)
        return dict(manifest) if manifest else None

    def save_manifest(self, batch_no: str, manifest: dict[str, Any], *, supersede: bool = True) -> dict[str, Any]:
        """写入批次清单。

        同一批次只保留一个完整版本：新清单整份替换旧清单，旧清单作为历史快照单独留存，
        不会出现新旧字段混在一条记录里的半套数据。``supersede=False`` 用于确认重放的
        幂等写入——内容和摘要都一致时直接返回已存在的清单。
        """
        current = self._manifests.get(batch_no)
        if current is not None and not supersede:
            return dict(current)
        if current is not None:
            # 同批次重复确认完全一致：不重复记历史，只刷新确认次数与时间
            same_payload = (
                str(current.get("清单校验摘要") or "") != ""
                and current.get("清单校验摘要") == manifest.get("清单校验摘要")
            )
            if same_payload:
                current["确认次数"] = max(
                    int(current.get("确认次数", 1)) + 1,
                    int(manifest.get("确认次数", 0) or 0),
                )
                current["生成时间"] = datetime.now().isoformat()
                return dict(current)
            history = self._manifest_history.setdefault(batch_no, [])
            snapshot = dict(current)
            snapshot["被替换时间"] = datetime.now().isoformat()
            history.append(snapshot)
        record = dict(manifest)
        record["清单版本"] = f"v{len(self._manifest_history.get(batch_no, [])) + 1}"
        record["生成时间"] = datetime.now().isoformat()
        self._manifests[batch_no] = record
        return dict(record)

    def get_manifest_history(self, batch_no: str) -> list[dict[str, Any]]:
        return [dict(item) for item in self._manifest_history.get(batch_no, [])]

    def list_manifests(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._manifests.values()]

    # ---- 字段映射历史快照 ----

    def save_mapping_history(self, rule_no: str, data: dict[str, Any], reason: str) -> dict[str, Any]:
        history = self._mapping_history.setdefault(rule_no, [])
        snapshot = {
            "版本": str(data.get("规则版本") or f"v{len(history) + 1}"),
            "时间": datetime.now().isoformat(),
            "原因": reason,
            "数据": dict(data),
        }
        history.append(snapshot)
        return snapshot

    def get_mapping_history(self, rule_no: str) -> list[dict[str, Any]]:
        return [dict(item) for item in self._mapping_history.get(rule_no, [])]


store = Store()
