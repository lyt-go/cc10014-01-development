"""快照管理 时间线服务：支持创建快照时冻结目标数据集当前状态，版本化管理。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.store import store

MODULE = "snapshot_mgr"
REQUIRED_FIELDS = ["快照编号", "目标数据集", "快照时间"]
STATUS_ORDER = ["草稿", "创建中", "已完成", "已过期"]
ACTION_RULES = {"创建快照": "创建中", "确认完成": "已完成", "标记过期": "已过期"}
NEGATIVE_ACTIONS = []


class SnapshotMgrService:
    def get_timeline(self) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        today = date.today()
        items: list[dict] = []
        for i, row in enumerate(rows):
            start = (today - timedelta(days=7 - i * 2)).isoformat()
            end = (today + timedelta(days=3 - i)).isoformat()
            items.append({
                "id": int(row.get("id", i + 1)),
                "name": str(row.get("快照编号", "")),
                "start": start,
                "end": end,
                "status": str(row.get("status", STATUS_ORDER[i % len(STATUS_ORDER)])),
                "logs": f"[{start}] 作业启动\n[{start}] 正在处理...\n[{end}] 任务{'完成' if row.get('status') == STATUS_ORDER[-1] else '进行中'}",
            })
        return items

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

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
        for k in ["数据行数", "存储路径", "压缩算法", "保留天数", "快照版本"]:
            if k in values:
                entry[k] = values[k]
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"数据快照 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于快照管理可执行范围"
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        store.save_version(MODULE, entry_id, entry)
        if action == "创建快照":
            self._freeze_data(entry)
        return entry, f"数据快照已{action}"

    def _freeze_data(self, entry: dict[str, Any]) -> None:
        dataset = entry.get("目标数据集", "")
        snapshot_id = entry.get("快照编号", f"SNAP-{entry['id']}")
        # 读取目标表当前数据并冻结
        rows = store.table_rows(dataset)
        if not rows:
            # 尝试从ODS找
            ods = store.find_by_field("ods_layer", "表名称", dataset)
            if ods:
                from app.services.ods_layer import OdsLayerService
                svc = OdsLayerService()
                rows = svc.get_table_data(dataset)
        store.save_snapshot_data(snapshot_id, dataset, rows or [])
        entry["数据行数"] = str(len(rows or []))
        entry["快照时间"] = date.today().isoformat()
        # 读取分区策略的冷热配置，写入快照元数据
        from app.services.partition_mgr import PartitionMgrService
        pm = PartitionMgrService()
        part_entry = store.find_by_field("partition_mgr", "目标表名", dataset)
        if part_entry:
            entry["冷热阈值"] = part_entry.get("冷热阈值", "30")
            entry["保留天数"] = part_entry.get("保留天数", "30")

    def check_expired_snapshots(self) -> list[dict[str, Any]]:
        """返回已过期（超过保留天数）的快照列表。"""
        expired = []
        today = date.today()
        for row in store.rows(MODULE):
            if row.get("status") != "已完成":
                continue
            snap_time_str = row.get("快照时间", "")
            retain_days = int(row.get("保留天数", "30") or "30")
            try:
                snap_time = date.fromisoformat(snap_time_str)
                if (today - snap_time).days > retain_days:
                    expired.append(dict(row))
            except Exception:
                pass
        return expired

    def archive_expired_snapshots(self) -> dict[str, Any]:
        """对过期快照自动触发归档。"""
        expired = self.check_expired_snapshots()
        if not expired:
            return {"ok": True, "message": "无过期快照", "archived": 0}
        from app.services.cold_archive import ColdArchiveService
        ca = ColdArchiveService()
        archived = 0
        for snap in expired:
            dataset = snap.get("目标数据集", "")
            # 找到对应归档策略
            arc_entry = store.find_by_field("cold_archive", "目标表名", dataset)
            if arc_entry:
                ca.execute_archive(arc_entry["id"], snap.get("快照编号", ""))
                archived += 1
                snap["status"] = "已归档"
                # 更新store中的快照状态
                for r in store.rows(MODULE):
                    if r.get("id") == snap.get("id"):
                        r["status"] = "已归档"
                        break
        return {"ok": True, "message": f"已归档 {archived} 个过期快照", "archived": archived}
