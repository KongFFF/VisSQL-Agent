import argparse
import json
import os
import random
import re
import sqlite3
from collections import defaultdict
from itertools import product
from pathlib import Path


def build_db_path(db_root: Path, db_id: str) -> Path:
    return db_root / db_id / f"{db_id}.sqlite"


def parse_args():
    parser = argparse.ArgumentParser(description="Compute oracle@k from a saved candidate pool.")
    parser.add_argument("--candidate-pool-path", required=True, help="Path to candidate_pool.jsonl")
    parser.add_argument("--dev-path", required=True, help="Path to the evaluation dataset json")
    parser.add_argument("--db-root", required=True, help="Root directory of sqlite databases")
    parser.add_argument("--output-path", default=None, help="Optional json file for oracle summary")
    parser.add_argument(
        "--attempt-scope",
        choices=["first", "all"],
        default="first",
        help="Use only attempt 1 candidates or all saved attempts per question",
    )
    return parser.parse_args()


def load_candidate_pool(path: Path) -> list[dict]:
    records = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            records.append(json.loads(line))
    return records


def replace_cur_year(query: str) -> str:
    return re.sub(
        r"YEAR\s*\(\s*CURDATE\s*\(\s*\)\s*\)\s*",
        "2020",
        query,
        flags=re.IGNORECASE,
    )


def postprocess(query: str) -> str:
    return query.replace("> =", ">=").replace("< =", "<=").replace("! =", "!=")


def get_cursor_from_path(sqlite_path: str):
    connection = sqlite3.connect(sqlite_path)
    connection.text_factory = lambda b: b.decode(errors="ignore")
    return connection.cursor()


def exec_on_db(sqlite_path: str, query: str):
    query = replace_cur_year(query)
    cursor = get_cursor_from_path(sqlite_path)
    try:
        cursor.execute(query)
        result = cursor.fetchall()
        cursor.close()
        cursor.connection.close()
        return "result", result
    except Exception as e:
        cursor.close()
        cursor.connection.close()
        return "exception", e


def permute_tuple(element: tuple, perm: tuple) -> tuple:
    return tuple([element[i] for i in perm])


def unorder_row(row: tuple) -> tuple:
    return tuple(sorted(row, key=lambda x: str(x) + str(type(x))))


def quick_rej(result1: list[tuple], result2: list[tuple], order_matters: bool) -> bool:
    s1 = [unorder_row(row) for row in result1]
    s2 = [unorder_row(row) for row in result2]
    return s1 == s2 if order_matters else set(s1) == set(s2)


def multiset_eq(l1: list, l2: list) -> bool:
    if len(l1) != len(l2):
        return False
    counts = defaultdict(int)
    for item in l1:
        counts[item] += 1
    for item in l2:
        counts[item] -= 1
        if counts[item] < 0:
            return False
    return True


def get_constraint_permutation(tab1_sets_by_columns: list[set], result2: list[tuple]):
    num_cols = len(result2[0])
    perm_constraints = [{i for i in range(num_cols)} for _ in range(num_cols)]
    if num_cols <= 3:
        return product(*perm_constraints)

    for _ in range(20):
        random_row = random.choice(result2)
        for tab1_col in range(num_cols):
            for tab2_col in set(perm_constraints[tab1_col]):
                if random_row[tab2_col] not in tab1_sets_by_columns[tab1_col]:
                    perm_constraints[tab1_col].remove(tab2_col)
    return product(*perm_constraints)


def result_eq(result1: list[tuple], result2: list[tuple], order_matters: bool) -> bool:
    if len(result1) == 0 and len(result2) == 0:
        return True
    if len(result1) != len(result2):
        return False
    num_cols = len(result1[0])
    if len(result2[0]) != num_cols:
        return False
    if not quick_rej(result1, result2, order_matters):
        return False

    tab1_sets_by_columns = [{row[i] for row in result1} for i in range(num_cols)]
    for perm in get_constraint_permutation(tab1_sets_by_columns, result2):
        if len(perm) != len(set(perm)):
            continue
        result2_perm = result2 if num_cols == 1 else [permute_tuple(element, perm) for element in result2]
        if order_matters:
            if result1 == result2_perm:
                return True
        else:
            if set(result1) == set(result2_perm) and multiset_eq(result1, result2_perm):
                return True
    return False


def eval_exec_match_simple(db: str, pred_sql: str, gold_sql: str) -> int:
    pred_sql = postprocess(pred_sql)
    gold_sql = postprocess(gold_sql)
    order_matters = "order by" in gold_sql.lower()

    db_dir = os.path.dirname(db)
    db_paths = [os.path.join(db_dir, basename) for basename in os.listdir(db_dir) if basename.endswith(".sqlite")]

    for db_path in db_paths:
        gold_flag, gold_denotation = exec_on_db(db_path, gold_sql)
        pred_flag, pred_denotation = exec_on_db(db_path, pred_sql)

        if gold_flag == "exception":
            raise RuntimeError(f"Gold SQL failed on {db_path}: {gold_sql}")
        if pred_flag == "exception":
            return 0
        if not result_eq(gold_denotation, pred_denotation, order_matters=order_matters):
            return 0

    return 1


def main():
    args = parse_args()

    candidate_pool_path = Path(args.candidate_pool_path)
    dev_path = Path(args.dev_path)
    db_root = Path(args.db_root)

    dev_dataset = json.loads(dev_path.read_text(encoding="utf-8"))
    candidate_records = load_candidate_pool(candidate_pool_path)

    grouped_records: dict[int, list[dict]] = defaultdict(list)
    for record in candidate_records:
        grouped_records[int(record["question_index"])].append(record)

    details = []
    success_count = 0

    for question_index, item in enumerate(dev_dataset):
        db_id = item["db_id"]
        gold_sql = item.get("query") or item.get("gold_sql", "")
        db_path = str(build_db_path(db_root, db_id))
        question_records = grouped_records.get(question_index, [])

        if args.attempt_scope == "first":
            question_records = [record for record in question_records if int(record.get("attempt", 0)) == 1]

        candidate_sqls = []
        seen = set()
        for record in sorted(question_records, key=lambda x: (int(x.get("attempt", 0)), x.get("memory_state_hash", ""))):
            for sql in record.get("candidate_sqls", []):
                normalized_sql = " ".join(str(sql).split()).lower()
                if normalized_sql in seen:
                    continue
                seen.add(normalized_sql)
                candidate_sqls.append(sql)

        matched_candidate_index = None
        matched_candidate_sql = None
        for candidate_index, candidate_sql in enumerate(candidate_sqls):
            is_match = eval_exec_match_simple(
                db=db_path,
                pred_sql=candidate_sql,
                gold_sql=gold_sql,
            )
            if is_match:
                matched_candidate_index = candidate_index
                matched_candidate_sql = candidate_sql
                success_count += 1
                break

        details.append(
            {
                "question_index": question_index,
                "db_id": db_id,
                "question": item["question"],
                "candidate_count": len(candidate_sqls),
                "oracle_success": matched_candidate_index is not None,
                "matched_candidate_index": matched_candidate_index,
                "matched_candidate_sql": matched_candidate_sql,
            }
        )

    total_count = len(dev_dataset)
    oracle_accuracy = success_count / total_count if total_count else 0.0
    summary = {
        "candidate_pool_path": str(candidate_pool_path),
        "dev_path": str(dev_path),
        "db_root": str(db_root),
        "attempt_scope": args.attempt_scope,
        "total_count": total_count,
        "oracle_success_count": success_count,
        "oracle_accuracy": oracle_accuracy,
        "details": details,
    }

    print(json.dumps(
        {
            "total_count": total_count,
            "oracle_success_count": success_count,
            "oracle_accuracy": oracle_accuracy,
            "attempt_scope": args.attempt_scope,
        },
        ensure_ascii=False,
        indent=2,
    ))

    if args.output_path:
        output_path = Path(args.output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
