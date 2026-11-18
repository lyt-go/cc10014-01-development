"""入库批次清单：原始文件解析、重复批次映射边界、导入导出、文件交接与并发写入。"""
from __future__ import annotations

import csv
import io
import json
import threading

from app.seed import SEED_ROWS
from app.services.column_mapping import ColumnMappingService
from app.services.ingest_manifest import IngestManifestService, manifest_checksum
from app.services.raw_ingest import RawIngestService
from app.store import store


def reset_store():
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    store._table_data.clear()
    store._versions.clear()
    store._lineage_edges.clear()
    store._quality_results.clear()
    store._snapshot_data.clear()
    store._archive_records.clear()
    store._backfill_partitions.clear()
    store._manifests.clear()
    store._manifest_history.clear()
    store._mapping_history.clear()


class TestIngestManifest:
    def setup_method(self):
        reset_store()

    def _new_batch(self, batch_no: str, source: str = "订单系统") -> int:
        entry, missing = RawIngestService().create_entry({
            "批次编号": batch_no,
            "来源系统": source,
            "文件格式": "CSV",
            "原始行数": "3",
            "校验摘要": "declared",
        })
        assert entry is not None, missing
        return int(entry["id"])

    def test_confirm_parses_raw_file_and_keeps_summary(self):
        batch_no = "RAW-20261201"
        entry_id = self._new_batch(batch_no)
        raw = "order_id,amount,status\nO1,10,new\nO2,20,paid\nO3,30,new\n"

        entry, message = RawIngestService().run_action(entry_id, "确认入库", raw)
        assert entry is not None, message
        detail = IngestManifestService().get_manifest_detail(batch_no)
        manifest = detail["manifest"]
        assert manifest["原始数据行数"] == 3
        assert manifest["解析校验摘要"]
        assert manifest["原始校验摘要"] == "declared"
        assert manifest["字段列表"] == ["order_id", "amount", "status"]
        assert manifest["映射规则编号"] == "MAP-20261201"
        assert manifest["清单校验摘要"] == manifest_checksum(manifest)
        assert IngestManifestService().verify(manifest)[0]
        # 状态与清单一并落库，状态已推进
        assert entry["status"] == "已入库"

    def test_confirm_blocked_when_raw_file_unparsable(self):
        batch_no = "RAW-20261202"
        entry_id = self._new_batch(batch_no)
        entry, message = RawIngestService().run_action(entry_id, "确认入库", "{not-json")
        assert entry is None
        assert "解析失败" in message
        # 没有清单时状态不得推进，避免半套记录
        assert store.get_manifest(batch_no) is None
        assert store.find("raw_ingest", entry_id)["status"] == "草稿"

    def test_duplicate_batch_keeps_mapping_order_and_appends_fields(self):
        batch_no = "RAW-20261203"
        entry_id = self._new_batch(batch_no)
        first = "order_id,amount,status\nO1,10,new\n"
        assert RawIngestService().run_action(entry_id, "确认入库", first)[0] is not None
        second = "order_id,amount,status,coupon\nO1,10,new,C1\n"
        assert RawIngestService().run_action(entry_id, "确认入库", second)[0] is not None

        detail = IngestManifestService().get_manifest_detail(batch_no)
        manifest = detail["manifest"]
        assert manifest["字段列表"] == ["order_id", "amount", "status", "coupon"]
        assert manifest["补写字段清单"] == ["coupon"]
        assert manifest["冲突状态"] == "重复批次"
        assert manifest["映射规则版本"] == "v2"
        # 既有映射保留原字段顺序，新字段补在末尾
        mapping = detail["mapping"]
        assert mapping["源字段名"] == "order_id,amount,status,coupon"
        assert mapping["目标字段名"] == "order_id,order_amount,order_status,coupon"
        # 旧版本作为历史快照单独可查
        history = detail["mapping_history"]
        assert len(history) == 1
        assert history[0]["数据"]["源字段名"] == "order_id,amount,status"
        # 清单也只保留一个完整版本，旧清单进历史
        assert manifest["清单版本"] == "v2"
        assert len(detail["history"]) == 1
        assert detail["history"][0]["字段列表"] == ["order_id", "amount", "status"]

    def test_repeated_confirm_same_content_is_idempotent(self):
        batch_no = "RAW-20261204"
        entry_id = self._new_batch(batch_no)
        raw = "order_id,amount,status\nO1,10,new\n"
        for _ in range(3):
            entry, message = RawIngestService().run_action(entry_id, "确认入库", raw)
            assert entry is not None, message
        detail = IngestManifestService().get_manifest_detail(batch_no)
        assert detail["manifest"]["清单版本"] == "v1"
        assert detail["manifest"]["确认次数"] == 3
        assert detail["history"] == []
        assert detail["manifest"]["原始数据行数"] == 1

    def test_export_import_roundtrip_and_corrupt_rejection(self):
        batch_no = "RAW-20261205"
        entry_id = self._new_batch(batch_no)
        RawIngestService().run_action(entry_id, "确认入库", "order_id,amount,status\nO1,10,new\n")
        service = IngestManifestService()
        exported = service.export_csv()
        assert batch_no in exported

        result = service.import_csv(exported)
        assert result["ok"] is True
        assert result["imported"][0]["结果"] == "已存在，未重复写入"

        lines = exported.splitlines()
        rows = list(csv.reader(lines))
        header = rows[0]
        data = rows[1]
        data[header.index("原始数据行数")] = "999"
        buffer = io.StringIO()
        csv.writer(buffer, lineterminator="\n").writerows([header, data])
        rejected = service.import_csv(buffer.getvalue())
        assert rejected["ok"] is False
        assert "清单校验摘要不一致" in rejected["rejected"][0]["原因"]

    def test_import_rejects_incomplete_manifest(self):
        service = IngestManifestService()
        header = ("批次编号,来源系统,文件格式,原始行数,原始数据行数,原始校验摘要,清单校验摘要,"
                  "清单版本,映射规则编号,映射规则版本,映射来源,字段列表,补写字段清单,字段明细,状态,生成时间\n")
        row = 'RAW-X,订单系统,CSV,1,1,abc,,v1,MAP-X,,本批次生成,order_id,,[]\n'
        result = service.import_csv(header + row)
        assert result["ok"] is False
        assert "字段明细为空" in result["rejected"][0]["原因"]
        assert store.get_manifest("RAW-X") is None

    def test_handover_roundtrip(self):
        batch_no = "RAW-20261206"
        entry_id = self._new_batch(batch_no)
        RawIngestService().run_action(entry_id, "确认入库", "order_id,amount,status\nO1,10,new\n")
        service = IngestManifestService()
        payload = service.handover(entry_id)
        assert payload is not None
        assert payload["批次编号"] == batch_no
        assert payload["原始摘要"]["原始数据行数"] == 1
        assert payload["映射"]["规则编号"] == "MAP-20261206"
        assert payload["清单校验"] == "清单校验通过"
        received = service.import_handover(payload)
        assert received["ok"] is True
        assert received["manifest"]["批次编号"] == batch_no
        # 交接后再导出仍是一份完整清单
        assert service.export_csv().count(batch_no) == 1

    def test_handover_before_confirm_does_not_invent_manifest(self):
        batch_no = "RAW-20261207"
        entry_id = self._new_batch(batch_no)
        payload = IngestManifestService().handover(entry_id)
        assert payload is not None
        assert payload["清单"] is None
        assert store.get_manifest(batch_no) is None

    def test_mapping_consumes_manifest_and_keeps_one_row(self):
        batch_no = "RAW-20261209"
        entry_id = self._new_batch(batch_no)
        for raw in ("order_id,amount,status\nO1,1,new\n",
                    "order_id,amount,status,coupon\nO1,1,new,C1\n"):
            assert RawIngestService().run_action(entry_id, "确认入库", raw)[0] is not None

        cms = ColumnMappingService()
        rule_no = f"MAP-{batch_no.replace('RAW-', '')}"
        rules = [row for row in store.rows("column_mapping") if row.get("规则编号") == rule_no]
        # 重复确认不追加同号规则，只升版本
        assert len(rules) == 1
        view = cms.get_manifest_view(rule_no)
        assert view is not None
        assert view["批次编号"] == batch_no
        assert view["补写字段清单"] == ["coupon"]
        assert view["字段列表"] == ["order_id", "amount", "status", "coupon"]
        assert cms.get_mapping_history(rule_no)[0]["数据"]["规则版本"] == "v1"

    def test_concurrent_writes_leave_one_complete_version(self):
        batch_no = "RAW-20261208"
        entry_id = self._new_batch(batch_no)
        raw = "order_id,amount,status\nO1,10,new\n"
        RawIngestService().run_action(entry_id, "确认入库", raw)
        service = IngestManifestService()
        base = service.get_manifest(batch_no)
        assert base is not None

        variants: list[dict] = []
        for index in range(8):
            variant = dict(base)
            variant["字段明细"] = list(base["字段明细"])
            variant["字段列表"] = list(base["字段列表"])
            variant["补写字段清单"] = [f"extra_{index}"]
            variant["清单校验摘要"] = manifest_checksum(variant)
            variants.append(variant)

        errors: list[Exception] = []

        def writer(item: dict) -> None:
            try:
                service.import_handover({"清单": item})
            except Exception as exc:  # pragma: no cover - 失败时用于定位
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(item,)) for item in variants]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        current = service.get_manifest(batch_no)
        assert current is not None
        # 单一有效版本：内容必须与某个完整写入一致，不能是拼接出来的半套
        assert current["清单校验摘要"] in {item["清单校验摘要"] for item in variants}
        assert current["字段列表"] == base["字段列表"]
        assert len(current["补写字段清单"]) == 1
        assert service.verify(current)[0]
        # 其余写入都完整落在历史里
        history = service.get_manifest_history(batch_no)
        assert len(history) == len(variants)
        # 每次写入要么是当前版本，要么完整落在历史里，没有丢失也没有拼接
        written = [current, *history]
        assert {item["清单校验摘要"] for item in written} == {
            item["清单校验摘要"] for item in variants} | {base["清单校验摘要"]}
        for snapshot in written:
            if snapshot["清单校验摘要"] == base["清单校验摘要"]:
                continue  # 初始版本本来就未补写字段
            assert len(snapshot["补写字段清单"]) == 1
            assert snapshot["清单校验摘要"] == manifest_checksum(snapshot)
