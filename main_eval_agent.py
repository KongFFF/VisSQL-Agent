import argparse
import json
from pathlib import Path


def build_schema_dict_v6(tables_path: Path) -> dict:
    """
    解析 Spider tables.json，生成适合当前 Agent 使用的中文半结构化 schema。
    """
    with tables_path.open("r", encoding="utf-8") as f:
        tables_data = json.load(f)

    db_schemas = {}

    for db in tables_data:
        db_id = db["db_id"]
        table_names = db["table_names_original"]
        column_names = db["column_names_original"]
        primary_keys = db["primary_keys"]
        foreign_keys = db["foreign_keys"]

        tables_dict = {i: [] for i in range(len(table_names))}
        for idx, (table_idx, col_name) in enumerate(column_names):
            if table_idx == -1:
                continue
            tables_dict[table_idx].append((idx, col_name))

        pk_set = set(primary_keys)
        fk_map = {src: tgt for src, tgt in foreign_keys}

        schema_lines = [f"【数据库结构】\n数据库名称：{db_id}"]

        for i, table_name in enumerate(table_names):
            schema_lines.append(f"- 表：{table_name}")
            col_descriptions = []

            for col_idx, col_name in tables_dict[i]:
                constraints = []
                if col_idx in pk_set:
                    constraints.append("主键")
                if col_idx in fk_map:
                    ref_idx = fk_map[col_idx]
                    ref_table = table_names[column_names[ref_idx][0]]
                    ref_col = column_names[ref_idx][1]
                    constraints.append(f"外键指向 {ref_table}.{ref_col}")

                if constraints:
                    col_descriptions.append(f"{col_name} ({'，'.join(constraints)})")
                else:
                    col_descriptions.append(col_name)

            schema_lines.append(f"  字段：{', '.join(col_descriptions)}")

        db_schemas[db_id] = "\n".join(schema_lines)

    return db_schemas


def make_jsonable(value):
    if isinstance(value, dict):
        return {key: make_jsonable(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [make_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def clean_sql_for_spider(sql: str) -> str:
    if not sql:
        return "SELECT 1"
    return " ".join(sql.replace("\t", " ").replace("\n", " ").split())


def build_db_path(db_root: Path, db_id: str) -> Path:
    return db_root / db_id / f"{db_id}.sqlite"


def count_existing_lines(file_path: Path) -> int:
    if not file_path.exists():
        return 0
    with file_path.open("r", encoding="utf-8") as f:
        return sum(1 for _ in f)


def should_archive_trajectory(agent_result: dict) -> bool:
    return agent_result.get("attempts", 1) > 1 or agent_result.get("had_probe", False)


def parse_args():
    parser = argparse.ArgumentParser(description="批量评测 VisSQLAgent 在 Spider dev 集上的表现。")
    parser.add_argument("--base-model", required=True, help="基座模型路径")
    parser.add_argument("--lora-path", default=None, help="LoRA 权重路径，可为空")
    parser.add_argument("--dev-path", default="data/dev.json", help="Spider dev.json 路径")
    parser.add_argument("--tables-path", default="data/tables.json", help="Spider tables.json 路径")
    parser.add_argument("--db-root", default="data/database", help="Spider 数据库根目录")
    parser.add_argument("--output-dir", default="eval", help="评测输出目录")
    parser.add_argument("--predict-file", default="predict_agent.txt", help="Spider 官方评测用预测文件名")
    parser.add_argument("--summary-file", default="agent_run_summary.jsonl", help="轻量摘要日志文件名")
    parser.add_argument("--trajectory-file", default="agent_trajectories.jsonl", help="完整轨迹日志文件名")
    parser.add_argument("--max-retries", type=int, default=3, help="Agent 最大重试轮数")
    parser.add_argument("--progress-every", type=int, default=50, help="每多少题打印一次进度")
    parser.add_argument("--resume", action="store_true", help="从已有输出继续跑")
    parser.add_argument("--start-index", type=int, default=0, help="从第几题开始跑（0-based）")
    parser.add_argument("--end-index", type=int, default=None, help="跑到第几题结束（不含，0-based）")
    return parser.parse_args()


def run_evaluation():
    from main_agent import VisSQLAgent

    args = parse_args()

    dev_path = Path(args.dev_path)
    tables_path = Path(args.tables_path)
    db_root = Path(args.db_root)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    predict_path = output_dir / args.predict_file
    summary_path = output_dir / args.summary_file
    trajectory_path = output_dir / args.trajectory_file

    print(">>> 正在加载 Spider 配置与题目集...")
    schemas_dict = build_schema_dict_v6(tables_path)

    with dev_path.open("r", encoding="utf-8") as f:
        dev_dataset = json.load(f)

    total_count = len(dev_dataset)
    start_index = args.start_index

    if args.resume:
        existing_predictions = count_existing_lines(predict_path)
        start_index = max(start_index, existing_predictions)

    end_index = args.end_index if args.end_index is not None else total_count
    if start_index >= end_index:
        print(f">>> 没有需要运行的题目。start_index={start_index}, end_index={end_index}")
        return

    first_db_id = dev_dataset[start_index]["db_id"]
    first_db_path = build_db_path(db_root, first_db_id)

    print(">>> 正在初始化 Agent...")
    agent = VisSQLAgent(
        base_model_path=args.base_model,
        lora_path=args.lora_path,
        db_path=str(first_db_path),
        max_retries=args.max_retries
    )

    predict_mode = "a" if args.resume and predict_path.exists() else "w"
    summary_mode = "a" if args.resume and summary_path.exists() else "w"
    trajectory_mode = "a" if args.resume and trajectory_path.exists() else "w"

    processed_count = 0
    success_count = 0
    reflexion_count = 0
    probe_count = 0
    exception_count = 0

    print(f">>> 开始批量评测，共计划运行 {end_index - start_index} 道题。")

    with predict_path.open(predict_mode, encoding="utf-8") as predict_f, \
         summary_path.open(summary_mode, encoding="utf-8") as summary_f, \
         trajectory_path.open(trajectory_mode, encoding="utf-8") as trajectory_f:
        for idx in range(start_index, end_index):
            item = dev_dataset[idx]
            db_id = item["db_id"]
            question = item["question"]
            gold_sql = item.get("query", "")
            schema = schemas_dict.get(db_id, "")
            db_path = build_db_path(db_root, db_id)

            fallback_sql = "SELECT 1"
            agent_result = None
            runtime_error = None

            try:
                agent_result = agent.run_query(
                    schema_info=schema,
                    user_question=question,
                    db_path=str(db_path),
                    verbose=False
                )
                final_sql = agent_result.get("final_sql", fallback_sql)
            except Exception as e:
                runtime_error = str(e)
                final_sql = fallback_sql
                exception_count += 1

            clean_sql = clean_sql_for_spider(final_sql)
            predict_f.write(clean_sql + "\n")
            predict_f.flush()

            if agent_result is None:
                summary_record = {
                    "question_index": idx,
                    "db_id": db_id,
                    "question": question,
                    "gold_sql": gold_sql,
                    "final_sql": clean_sql,
                    "is_success": False,
                    "attempts": 0,
                    "had_reflexion": False,
                    "had_probe": False,
                    "probe_scenarios": [],
                    "final_failure_type": "RuntimeError",
                    "runtime_error": runtime_error,
                    "db_path": str(db_path)
                }
            else:
                had_reflexion = agent_result.get("attempts", 1) > 1
                had_probe = agent_result.get("had_probe", False)
                probe_scenarios = [
                    probe_log.get("diagnostics", {}).get("scenario")
                    for probe_log in agent_result.get("probe_logs", [])
                    if probe_log.get("diagnostics", {}).get("scenario")
                ]
                final_failure_type = None if agent_result.get("is_success") else (
                    agent_result.get("error")
                    or agent_result.get("data", {}).get("error_type")
                )

                summary_record = {
                    "question_index": idx,
                    "db_id": db_id,
                    "question": question,
                    "gold_sql": gold_sql,
                    "final_sql": clean_sql,
                    "is_success": agent_result.get("is_success", False),
                    "attempts": agent_result.get("attempts", 0),
                    "had_reflexion": had_reflexion,
                    "had_probe": had_probe,
                    "probe_scenarios": probe_scenarios,
                    "final_failure_type": final_failure_type,
                    "db_path": agent_result.get("db_path", str(db_path))
                }

                if "data" in agent_result:
                    summary_record["final_row_count"] = agent_result["data"].get("row_count")
                    summary_record["execution_time_sec"] = agent_result["data"].get("execution_time_sec")

                if agent_result.get("is_success"):
                    success_count += 1
                if had_reflexion:
                    reflexion_count += 1
                if had_probe:
                    probe_count += 1

                if should_archive_trajectory(agent_result):
                    trajectory_record = {
                        "question_index": idx,
                        "db_id": db_id,
                        "question": question,
                        "gold_sql": gold_sql,
                        "final_sql": clean_sql,
                        "agent_result": make_jsonable(agent_result)
                    }
                    trajectory_f.write(json.dumps(trajectory_record, ensure_ascii=False) + "\n")
                    trajectory_f.flush()

            summary_f.write(json.dumps(summary_record, ensure_ascii=False) + "\n")
            summary_f.flush()

            processed_count += 1
            if processed_count % args.progress_every == 0 or idx + 1 == end_index:
                print(
                    f"[{idx + 1}/{end_index}] 已完成 {processed_count} 题 | "
                    f"成功 {success_count} | Reflexion {reflexion_count} | "
                    f"Probe {probe_count} | 异常 {exception_count}"
                )

    print("\n>>> 批量评测完成。")
    print(f">>> 预测文件: {predict_path}")
    print(f">>> 摘要日志: {summary_path}")
    print(f">>> 轨迹日志: {trajectory_path}")


if __name__ == "__main__":
    run_evaluation()
