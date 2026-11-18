"""入库原始文件解析：读取原始文件内容，提取字段列表、数据行数与校验摘要。

解析结果同时供入库批次清单（ingest_manifest）和字段映射（column_mapping）消费，
两者必须是同一份解析产物，否则清单里记录的行数、摘要和映射字段会对不上。

没有原始文件内容时（示例数据只有落地路径），按来源系统推断一份样例字段，
并在解析结果里用 ``解析来源`` 标注，避免把推断结果当成真实文件头。
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
from typing import Any

# 来源系统 -> 已知字段模板。没有真实文件内容时按这里推断。
SOURCE_TEMPLATES: dict[str, dict[str, str]] = {
    "订单": {
        "源字段名": "order_id,amount,status",
        "目标字段名": "order_id,order_amount,order_status",
        "数据类型": "STRING,DECIMAL,STRING",
        "默认值": ",,",
        "转换函数": "trim,trim,trim",
        "表名称": "ods_orders",
    },
    "支付": {
        "源字段名": "pay_id,amount,pay_time",
        "目标字段名": "pay_id,pay_amount,pay_time",
        "数据类型": "STRING,DECIMAL,TIMESTAMP",
        "默认值": ",,",
        "转换函数": "trim,trim,trim",
        "表名称": "ods_payments",
    },
    "用户": {
        "源字段名": "user_id,register_time,level",
        "目标字段名": "user_id,register_time,user_level",
        "数据类型": "STRING,TIMESTAMP,STRING",
        "默认值": ",,",
        "转换函数": "trim,trim,trim",
        "表名称": "ods_users",
    },
}

TIMESTAMP_HINTS = ("time", "date", "_dt", "ts")
DECIMAL_HINTS = ("amount", "price", "fee", "cost", "total", "qty", "count")


def _dtype_for(field: str) -> str:
    lowered = field.casefold()
    if any(hint in lowered for hint in TIMESTAMP_HINTS):
        return "TIMESTAMP"
    if any(hint in lowered for hint in DECIMAL_HINTS):
        return "DECIMAL"
    return "STRING"


def checksum_of(text: str) -> str:
    """对原始内容取稳定短摘要；空内容返回空串。"""
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def template_for(source: str) -> dict[str, str]:
    lowered = (source or "").casefold()
    for key, template in SOURCE_TEMPLATES.items():
        if key in (source or "") or key in lowered:
            return dict(template)
    # 未知来源：保留旧行为，生成 col/field 结构，表名按来源拼接
    return {
        "源字段名": "col1,col2,col3",
        "目标字段名": "field1,field2,field3",
        "数据类型": "STRING,STRING,STRING",
        "默认值": ",,",
        "转换函数": "trim,trim,trim",
        "表名称": f"ods_{source}" if source else "ods_unknown",
    }


def _split(value: Any) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _field_details(sources: list[str], targets: list[str], dtypes: list[str],
                   defaults: list[str], transforms: list[str]) -> list[dict[str, str]]:
    details: list[dict[str, str]] = []
    for index, source in enumerate(sources):
        details.append({
            "源字段": source,
            "目标字段": targets[index] if index < len(targets) else source,
            "数据类型": dtypes[index] if index < len(dtypes) else _dtype_for(source),
            "默认值": defaults[index] if index < len(defaults) else "",
            "转换函数": transforms[index] if index < len(transforms) else "trim",
        })
    return details


def _details_from_template(template: dict[str, str]) -> list[dict[str, str]]:
    return _field_details(
        _split(template["源字段名"]), _split(template["目标字段名"]),
        _split(template["数据类型"]), _split(template["默认值"]),
        _split(template["转换函数"]),
    )


def _parse_csv(text: str) -> tuple[list[str], int]:
    reader = csv.reader(io.StringIO(text.lstrip("﻿")))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return [], 0
    header = [cell.strip() for cell in rows[0]]
    return header, max(len(rows) - 1, 0)


def _parse_json(text: str) -> tuple[list[str], int]:
    payload = json.loads(text)
    if isinstance(payload, dict):
        records = payload.get("rows") or payload.get("items") or payload.get("data") or []
        if isinstance(payload.get("columns"), list):
            return [str(col) for col in payload["columns"]], len(records)
    else:
        records = payload
    if not isinstance(records, list):
        return [], 0
    fields: list[str] = []
    for record in records:
        if isinstance(record, dict):
            for key in record:
                if key not in fields:
                    fields.append(str(key))
    return fields, len(records)


def parse_raw_text(text: str, fmt: str) -> dict[str, Any]:
    """按文件格式解析原始内容；解析不了时返回空字段而不是抛异常。"""
    normalized = (fmt or "").strip().upper()
    fields: list[str] = []
    row_count = 0
    error = ""
    try:
        if normalized == "JSON" or text.lstrip().startswith(("{", "[")):
            fields, row_count = _parse_json(text)
        elif normalized in ("CSV", "TXT", ""):
            fields, row_count = _parse_csv(text)
        elif normalized == "PARQUET":
            # 演示环境没有列式读取器，允许用换行分隔的 JSON 代表落地内容
            lines = [line for line in text.splitlines() if line.strip()]
            records = [json.loads(line) for line in lines]
            row_count = len(records)
            for record in records:
                if isinstance(record, dict):
                    for key in record:
                        if key not in fields:
                            fields.append(str(key))
        else:
            fields, row_count = _parse_csv(text)
    except (ValueError, json.JSONDecodeError, csv.Error) as exc:  # 原始文件损坏时不静默成功
        error = f"原始文件解析失败：{exc}"
    return {
        "字段列表": fields,
        "行数": row_count,
        "校验摘要": checksum_of(text),
        "格式": normalized or "CSV",
        "解析错误": error,
        "解析来源": "原始文件内容",
    }


def parse_batch(entry: dict[str, Any], raw_text: str | None = None) -> dict[str, Any]:
    """解析一个入库批次的原始文件，返回清单可直接落库的解析摘要。

    优先使用调用方本次提交的原始内容，其次使用批次登记时留存的内容；
    都没有时按来源系统推断模板，并在 ``解析来源`` 里标注为样例推断。
    """
    text = raw_text if raw_text is not None else str(entry.get("原始内容") or "")
    fmt = str(entry.get("文件格式") or "")
    source = str(entry.get("来源系统") or "")
    batch_no = str(entry.get("批次编号") or "")
    template = template_for(source)

    if text:
        parsed = parse_raw_text(text, fmt)
        if parsed["解析错误"]:
            return {**parsed, "字段明细": [], "目标表名": template["表名称"], "映射规则编号": "", "模板": template}
        sources: list[str] = parsed["字段列表"] or _split(template["源字段名"])
        # 文件头与来源模板一致时沿用既有目标字段名，否则源字段即目标字段（新增列原样补写）
        if sources == _split(template["源字段名"]):
            details = _details_from_template(template)
        else:
            template_sources = _split(template["源字段名"])
            template_targets = _split(template["目标字段名"])
            targets: list[str] = []
            for field in sources:
                if field in template_sources:
                    targets.append(template_targets[template_sources.index(field)])
                else:
                    targets.append(field)
            details = _field_details(sources, targets,
                                     [_dtype_for(field) for field in sources], [], [])
        checksum = str(entry.get("校验摘要") or "") or parsed["校验摘要"]
        return {
            "字段列表": [detail["源字段"] for detail in details],
            "字段明细": details,
            "行数": parsed["行数"],
            "校验摘要": checksum,
            "解析校验摘要": parsed["校验摘要"],
            "格式": parsed["格式"],
            "解析错误": "",
            "解析来源": "原始文件内容",
            "目标表名": template["表名称"],
            "映射规则编号": f"MAP-{batch_no.replace('RAW-', '')}" if batch_no else "",
            "模板": template,
        }

    details = _details_from_template(template)
    seed = "|".join([
        batch_no, source, fmt, str(entry.get("原始行数") or ""),
        str(entry.get("落地路径") or ""), template["源字段名"],
    ])
    return {
        "字段列表": [detail["源字段"] for detail in details],
        "字段明细": details,
        "行数": int(str(entry.get("原始行数") or "0").strip() or 0),
        "校验摘要": str(entry.get("校验摘要") or "") or checksum_of(seed),
        "解析校验摘要": checksum_of(seed),
        "格式": fmt or "CSV",
        "解析错误": "",
        "解析来源": "样例推断（未提供原始文件内容）",
        "目标表名": template["表名称"],
        "映射规则编号": f"MAP-{batch_no.replace('RAW-', '')}" if batch_no else "",
        "模板": template,
    }
