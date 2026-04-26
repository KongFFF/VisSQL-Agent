import copy
import json
import re
import sys
import types
from collections import Counter
from pathlib import Path


def patch_local_dependencies():
    """
    复用本地分析环境的依赖补丁，只保留 AST/exact 所需能力。
    """
    exec_stub = types.ModuleType("exec_eval")
    exec_stub.eval_exec_match = lambda *args, **kwargs: False
    sys.modules["spider_eval.exec_eval"] = exec_stub

    from spider_eval import process_sql

    process_sql.word_tokenize = lambda s: re.findall(
        r"!=|>=|<=|[(),;=*<>]|\w+(?:\.\w+)?|\"[^\"]*\"|\'[^\']*\'",
        s
    )


def extract_table_units(sql_dict) -> list:
    tables = set()

    def visit_sql(node):
        if not isinstance(node, dict):
            return

        from_clause = node.get("from")
        if isinstance(from_clause, dict):
            for table_unit in from_clause.get("table_units", []):
                if isinstance(table_unit, (tuple, list)) and len(table_unit) == 2:
                    unit_type, value = table_unit
                    if unit_type == "table_unit":
                        tables.add(value)
                    elif unit_type == "sql":
                        visit_sql(value)

        for key in ("intersect", "union", "except"):
            if node.get(key):
                visit_sql(node[key])

        for value in node.values():
            if isinstance(value, dict):
                visit_sql(value)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    if isinstance(item, dict):
                        visit_sql(item)
                    elif isinstance(item, (list, tuple)):
                        for sub_item in item:
                            if isinstance(sub_item, dict):
                                visit_sql(sub_item)

    visit_sql(sql_dict)
    return sorted(tables)


def normalize_table_name(table_name: str) -> str:
    normalized = table_name.strip().lower()
    if normalized.startswith("__") and normalized.endswith("__"):
        return normalized
    return f"__{normalized}__"


def build_export():
    patch_local_dependencies()

    from spider_eval.evaluation import (
        Evaluator,
        build_foreign_key_map_from_json,
        build_valid_col_units,
        rebuild_sql_col,
        rebuild_sql_val,
    )
    from spider_eval.process_sql import Schema, get_schema, get_sql

    base = Path(__file__).resolve().parents[2]
    report_dir = base / "eval_reports"
    report_dir.mkdir(exist_ok=True)

    with (base / "data" / "dev.json").open("r", encoding="utf-8") as f:
        dev = json.load(f)

    rag_pred_lines = [
        line.rstrip("\n")
        for line in (base / "eval_rag" / "predict_agent.txt").open("r", encoding="utf-8")
    ]

    rag_summary = {}
    with (base / "eval_rag" / "agent_run_summary.jsonl").open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                obj = json.loads(line)
                rag_summary[obj["question_index"]] = obj

    kmaps = build_foreign_key_map_from_json(str(base / "data" / "tables.json"))

    def normalize_sql(sql_dict, schema, db_id):
        valid_col_units = build_valid_col_units(sql_dict["from"]["table_units"], schema)
        sql_dict = rebuild_sql_val(sql_dict)
        sql_dict = rebuild_sql_col(valid_col_units, sql_dict, kmaps[db_id])
        return sql_dict

    export_rows = []
    stats = Counter()

    for idx, item in enumerate(dev):
        db_id = item["db_id"]
        db = str(base / "data" / "database" / db_id / f"{db_id}.sqlite")
        schema = Schema(get_schema(db))
        gold_sql = item["query"]
        rag_pred_sql = rag_pred_lines[idx]
        summary = rag_summary.get(idx, {})

        gold_norm = normalize_sql(get_sql(schema, gold_sql), schema, db_id)
        gold_tables = extract_table_units(gold_norm)

        rag_parse_error = None
        rag_exact = 0
        rag_pred_tables = []
        try:
            rag_norm = normalize_sql(get_sql(schema, rag_pred_sql.replace("value", "1")), schema, db_id)
            rag_pred_tables = extract_table_units(rag_norm)
            evaluator = Evaluator()
            rag_exact = evaluator.eval_exact_match(copy.deepcopy(rag_norm), copy.deepcopy(gold_norm))
        except Exception as e:
            rag_parse_error = str(e)

        selected_tables = summary.get("schema_selected_tables", [])
        selected_table_set = {normalize_table_name(table) for table in selected_tables}
        gold_table_set = set(gold_tables)
        rag_pred_table_set = set(rag_pred_tables)

        export_rows.append({
            "question_index": idx,
            "db_id": db_id,
            "question": item["question"],
            "gold_sql": gold_sql,
            "rag_pred_sql": rag_pred_sql,
            "gold_sql_tables": gold_tables,
            "rag_selected_tables": selected_tables,
            "rag_selected_tables_normalized": sorted(selected_table_set),
            "rag_predicted_sql_tables": rag_pred_tables,
            "gold_table_count": len(gold_tables),
            "rag_selected_table_count": len(selected_tables),
            "rag_predicted_sql_table_count": len(rag_pred_tables),
            "rag_exact_match": bool(rag_exact),
            "rag_parse_error": rag_parse_error,
            "selected_covers_gold_tables": gold_table_set.issubset(selected_table_set),
            "predicted_sql_matches_gold_tables": rag_pred_table_set == gold_table_set,
            "selected_but_not_in_gold": sorted(selected_table_set - gold_table_set),
            "gold_but_not_selected": sorted(gold_table_set - selected_table_set),
            "predicted_but_not_in_gold": sorted(rag_pred_table_set - gold_table_set),
            "gold_but_not_in_predicted_sql": sorted(gold_table_set - rag_pred_table_set),
        })

        stats["total_questions"] += 1
        if gold_table_set.issubset(selected_table_set):
            stats["selected_covers_gold_tables"] += 1
        if rag_pred_table_set == gold_table_set:
            stats["predicted_sql_matches_gold_tables"] += 1
        if rag_exact:
            stats["rag_exact_match"] += 1
        if rag_parse_error:
            stats["rag_parse_error"] += 1

    jsonl_path = report_dir / "rag_table_usage.jsonl"
    with jsonl_path.open("w", encoding="utf-8") as f:
        for row in export_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    summary_payload = {
        "total_questions": stats["total_questions"],
        "selected_covers_gold_tables_count": stats["selected_covers_gold_tables"],
        "selected_covers_gold_tables_ratio": (
            stats["selected_covers_gold_tables"] / stats["total_questions"]
            if stats["total_questions"] else 0
        ),
        "predicted_sql_matches_gold_tables_count": stats["predicted_sql_matches_gold_tables"],
        "predicted_sql_matches_gold_tables_ratio": (
            stats["predicted_sql_matches_gold_tables"] / stats["total_questions"]
            if stats["total_questions"] else 0
        ),
        "rag_exact_match_count": stats["rag_exact_match"],
        "rag_exact_match_ratio": (
            stats["rag_exact_match"] / stats["total_questions"]
            if stats["total_questions"] else 0
        ),
        "rag_parse_error_count": stats["rag_parse_error"],
        "output_jsonl": str(jsonl_path),
    }

    summary_path = report_dir / "rag_table_usage_summary.json"
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary_payload, f, ensure_ascii=False, indent=2)

    print(f"已导出逐题表使用对照: {jsonl_path}")
    print(json.dumps(summary_payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    build_export()
