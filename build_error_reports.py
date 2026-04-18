import copy
import json
import re
import sys
import types
from collections import Counter
from pathlib import Path


def patch_local_dependencies():
    """
    在本地分析环境里绕过 execution 依赖，只保留 exact/partial 所需能力。
    """
    exec_stub = types.ModuleType("exec_eval")
    exec_stub.eval_exec_match = lambda *args, **kwargs: False
    sys.modules["exec_eval"] = exec_stub

    import process_sql

    process_sql.word_tokenize = lambda s: re.findall(
        r"!=|>=|<=|[(),;=*<>]|\w+(?:\.\w+)?|\"[^\"]*\"|\'[^\']*\'",
        s
    )


def build_reports():
    patch_local_dependencies()

    from evaluation import (
        Evaluator,
        build_foreign_key_map_from_json,
        build_valid_col_units,
        rebuild_sql_col,
        rebuild_sql_val,
    )
    from process_sql import Schema, get_schema, get_sql

    base = Path(__file__).resolve().parent
    paths = {
        "agent_no_empty": base / "eval_no_empty_retry" / "predict_agent.txt",
        "agent_with_empty": base / "eval_with_empty_retry" / "predict_agent.txt",
        "v6": base / "predict_v6.txt",
    }

    with (base / "data" / "dev.json").open("r", encoding="utf-8") as f:
        dev = json.load(f)

    kmaps = build_foreign_key_map_from_json(str(base / "data" / "tables.json"))
    pred_lines = {
        name: [line.rstrip("\n") for line in path.open("r", encoding="utf-8")]
        for name, path in paths.items()
    }

    summary_no = {}
    with (base / "eval_no_empty_retry" / "agent_run_summary.jsonl").open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                obj = json.loads(line)
                summary_no[obj["question_index"]] = obj

    summary_with = {}
    with (base / "eval_with_empty_retry" / "agent_run_summary.jsonl").open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                obj = json.loads(line)
                summary_with[obj["question_index"]] = obj

    def normalize_sql(sql_dict, schema, db_id):
        valid_col_units = build_valid_col_units(sql_dict["from"]["table_units"], schema)
        sql_dict = rebuild_sql_val(sql_dict)
        sql_dict = rebuild_sql_col(valid_col_units, sql_dict, kmaps[db_id])
        return sql_dict

    rows = []
    for idx, item in enumerate(dev):
        db_id = item["db_id"]
        db = str(base / "data" / "database" / db_id / f"{db_id}.sqlite")
        schema = Schema(get_schema(db))
        gold_raw = normalize_sql(get_sql(schema, item["query"]), schema, db_id)
        hardness = Evaluator().eval_hardness(copy.deepcopy(gold_raw))

        row = {
            "question_index": idx,
            "db_id": db_id,
            "hardness": hardness,
            "question": item["question"],
            "gold_sql": item["query"],
        }

        for name, lines in pred_lines.items():
            pred_sql = lines[idx].replace("value", "1")
            try:
                pred_norm = normalize_sql(get_sql(schema, pred_sql), schema, db_id)
                evaluator = Evaluator()
                exact = evaluator.eval_exact_match(copy.deepcopy(pred_norm), copy.deepcopy(gold_raw))
                partial = evaluator.partial_scores
                parse_error = None
            except Exception as e:
                exact = 0
                partial = None
                parse_error = str(e)

            row[name] = {
                "pred_sql": pred_sql,
                "exact": exact,
                "partial": partial,
                "parse_error": parse_error,
            }

        row["agent_no_empty_summary"] = summary_no.get(idx)
        row["agent_with_empty_summary"] = summary_with.get(idx)
        rows.append(row)

    wrong_no_empty = []
    fixed_over_v6 = []
    regressed_by_empty = []

    for row in rows:
        no_exact = row["agent_no_empty"]["exact"]
        with_exact = row["agent_with_empty"]["exact"]
        v6_exact = row["v6"]["exact"]

        if not no_exact:
            failed_parts = []
            if row["agent_no_empty"]["partial"] is None:
                failed_parts = ["parse_error"]
            else:
                failed_parts = [
                    key
                    for key, value in row["agent_no_empty"]["partial"].items()
                    if value["f1"] != 1
                ]

            wrong_no_empty.append({
                "question_index": row["question_index"],
                "db_id": row["db_id"],
                "hardness": row["hardness"],
                "question": row["question"],
                "gold_sql": row["gold_sql"],
                "pred_sql": row["agent_no_empty"]["pred_sql"],
                "failed_parts": failed_parts,
                "parse_error": row["agent_no_empty"]["parse_error"],
                "summary": row["agent_no_empty_summary"],
            })

        if no_exact and not v6_exact:
            fixed_over_v6.append({
                "question_index": row["question_index"],
                "db_id": row["db_id"],
                "hardness": row["hardness"],
                "question": row["question"],
                "gold_sql": row["gold_sql"],
                "v6_pred_sql": row["v6"]["pred_sql"],
                "agent_no_empty_pred_sql": row["agent_no_empty"]["pred_sql"],
                "summary": row["agent_no_empty_summary"],
            })

        if no_exact and not with_exact:
            regressed_by_empty.append({
                "question_index": row["question_index"],
                "db_id": row["db_id"],
                "hardness": row["hardness"],
                "question": row["question"],
                "gold_sql": row["gold_sql"],
                "agent_no_empty_pred_sql": row["agent_no_empty"]["pred_sql"],
                "agent_with_empty_pred_sql": row["agent_with_empty"]["pred_sql"],
                "agent_no_empty_summary": row["agent_no_empty_summary"],
                "agent_with_empty_summary": row["agent_with_empty_summary"],
            })

    report_dir = base / "eval_reports"
    report_dir.mkdir(exist_ok=True)

    with (report_dir / "wrong_questions_no_empty.jsonl").open("w", encoding="utf-8") as f:
        for item in wrong_no_empty:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    with (report_dir / "fixed_over_v6.jsonl").open("w", encoding="utf-8") as f:
        for item in fixed_over_v6:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    with (report_dir / "regressed_by_empty_retry.jsonl").open("w", encoding="utf-8") as f:
        for item in regressed_by_empty:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    summary = {
        "exact_accuracy": {
            "agent_no_empty": sum(row["agent_no_empty"]["exact"] for row in rows) / len(rows),
            "agent_with_empty": sum(row["agent_with_empty"]["exact"] for row in rows) / len(rows),
            "v6": sum(row["v6"]["exact"] for row in rows) / len(rows),
        },
        "wrong_no_empty_count": len(wrong_no_empty),
        "fixed_over_v6_count": len(fixed_over_v6),
        "regressed_by_empty_retry_count": len(regressed_by_empty),
        "wrong_no_empty_hardness": Counter(item["hardness"] for item in wrong_no_empty),
        "wrong_no_empty_failed_parts": Counter(
            part for item in wrong_no_empty for part in item["failed_parts"]
        ),
    }

    with (report_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=dict)

    print(f"已生成分析报告目录: {report_dir}")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=dict))


if __name__ == "__main__":
    build_reports()
