"""补数调度 时间线服务：作用于具体表，修改分区水位，记录补数区间。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.store import store

MODULE = "backfill_job"
REQUIRED_FIELDS = ["任务编号", "目标表名", "起始日期"]
STATUS_ORDER = ["草稿", "运行中", "已完成", "已取消"]
ACTION_RULES = {"启动任务": "运行中", "确认完成": "已完成", "取消任务": "已取消"}
NEGATIVE_ACTIONS = []


class BackfillJobService:
    def get_timeline(self) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        today = date.today()
        items: list[dict] = []
        for i, row in enumerate(rows):
            start = (today - timedelta(days=7 - i * 2)).isoformat()
            end = (today + timedelta(days=3 - i)).isoformat()
            items.append({
                "id": int(row.get("id", i + 1)),
                "name": str(row.get("任务编号", "")),
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
        for k in ["截止日期", "并行度", "覆盖策略", "完成水位", "失败区间"]:
            if k in values:
                entry[k] = values[k]
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"补数任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于补数调度可执行范围"
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        store.save_version(MODULE, entry_id, entry)
        if action == "启动任务":
            self._execute_backfill(entry)
        return entry, f"补数任务已{action}"

    def _execute_backfill(self, entry: dict[str, Any]) -> None:
        table = entry.get("目标表名", "")
        start = entry.get("起始日期", "")
        end = entry.get("截止日期", "")
        strategy = entry.get("覆盖策略", "覆盖写入")
        # 模拟补数：生成区间内的分区数据并写入目标表
        current = store.table_rows(table)
        new_partitions = []
        try:
            s = date.fromisoformat(start)
            e = date.fromisoformat(end)
            d = s
            while d <= e:
                new_partitions.append({"dt": d.isoformat(), "_backfilled": True})
                d += timedelta(days=1)
        except Exception:
            new_partitions = [{"dt": start, "_backfilled": True}]
        if strategy == "覆盖写入":
            store.set_table_rows(table, new_partitions)
        else:
            store.append_table_rows(table, new_partitions)
        # 记录补数分区
        for p in new_partitions:
            store.save_backfill_partition(entry.get("任务编号", ""), p)
        entry["完成水位"] = end
        # 更新关联DWD/ODS的水位
        for row in store.rows("dwd_layer"):
            if row.get("表名称") == table:
                row["刷新水位"] = end
                store.save_version("dwd_layer", row["id"], row)
        for row in store.rows("ods_layer"):
            if row.get("表名称") == table:
                store.save_version("ods_layer", row["id"], row)
