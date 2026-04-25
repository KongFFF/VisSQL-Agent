import argparse
import json
from pathlib import Path

from schema_retriever import (
    SchemaRetriever,
    build_schema_metadata_dict,
    render_schema_v6,
)

def build_schema_dict_v6(tables_path: Path) -> dict:
    """
    兼容旧逻辑：解析 Spider tables.json，生成全量 schema 文本。
    """
    schema_meta_dict = build_schema_metadata_dict(tables_path)
    return {
        db_id: render_schema_v6(schema_meta)
        for db_id, schema_meta in schema_meta_dict.items()
    }


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
    parser.add_argument("--db-root", default="data/testsuitedatabases/database", help="Spider 数据库根目录")
    parser.add_argument("--output-dir", default="eval", help="评测输出目录")
    parser.add_argument("--predict-file", default="predict_agent.txt", help="Spider 官方评测用预测文件名")
    parser.add_argument("--summary-file", default="agent_run_summary.jsonl", help="轻量摘要日志文件名")
    parser.add_argument("--trajectory-file", default="agent_trajectories.jsonl", help="完整轨迹日志文件名")
    parser.add_argument("--max-retries", type=int, default=3, help="Agent 最大重试轮数")
    parser.add_argument("--retry-on-empty-result", action="store_true", help="是否在空结果时触发额外的 Reflexion / probe")
    parser.add_argument(
        "--superlative-mode",
        choices=["v1", "v2", "phase0", "phase1", "phase1_c", "phase1_d", "phase1_e", "phase2_a", "phase2_b"],
        default="v1",
        help="superlative mode: v1=original, phase0=current template + exclusion layer, v2=alias of phase0, phase1=non-trained router, phase1_c=phase1 with controlled slot filling, phase1_d=phase1 plus enumerated entity-count planner, phase1_e=phase1_d plus structured projection selection for count-family templates, phase2_a=unified count-family planner, phase2_b=phase2_a plus decomposed count slotting",
    )
    parser.add_argument(
        "--superlative-router-use-threshold",
        type=float,
        default=0.70,
        help="minimum p(use_template) for phase1 router to allow template takeover",
    )
    parser.add_argument(
        "--superlative-router-template-threshold",
        type=float,
        default=0.65,
        help="minimum selected template confidence for phase1 router to allow template takeover",
    )
    parser.add_argument(
        "--schema-mode",
        choices=["full", "rag", "auto"],
        default="full",
        help="schema 提供方式：full=全量 schema，rag=检索子图，auto=低置信时自动回退全量 schema",
    )
    parser.add_argument("--retrieval-max-seed-tables", type=int, default=3, help="Schema Retriever 初始种子表上限")
    parser.add_argument("--retrieval-max-return-tables", type=int, default=6, help="Schema Retriever 最终返回的表上限")
    parser.add_argument("--retrieval-expand-hops", type=int, default=1, help="Schema Retriever 的 FK 邻接扩展 hop 数")
    parser.add_argument("--retrieval-min-table-score", type=float, default=1.0, help="Schema Retriever 选入种子表的最低分数")
    parser.add_argument("--retrieval-auto-threshold", type=float, default=3.0, help="auto 模式下触发子图检索的最低置信阈值")
    parser.add_argument("--schema-path-hints", action="store_true", help="是否在检索后的 schema 中附加候选连接关系与连接路径提示")
    parser.add_argument("--schema-path-hints-selective", action="store_true", help="是否只在高结构风险题上选择性注入主路径提示")
    parser.add_argument("--disable-value-hints", action="store_true", help="disable schema value hints built from live DB values")
    parser.add_argument("--value-hint-max-columns", type=int, default=10, help="max candidate columns for schema value hints")
    parser.add_argument("--value-hint-max-columns-per-table", type=int, default=4, help="max candidate columns per table for schema value hints")
    parser.add_argument("--value-hint-max-samples", type=int, default=5, help="max preview values per hinted column")
    parser.add_argument("--disable-bridge-completion", action="store_true", help="disable explicit bridge-table shortest-path completion while keeping seed-first table retention")
    parser.add_argument("--progress-every", type=int, default=50, help="每多少题打印一次进度")
    parser.add_argument("--resume", action="store_true", help="从已有输出继续跑")
    parser.add_argument("--start-index", type=int, default=0, help="从第几题开始跑（0-based）")
    parser.add_argument("--end-index", type=int, default=None, help="跑到第几题结束（不含，0-based）")
    return parser.parse_args()


def run_evaluation():
    args = parse_args()
    from main_agent import VisSQLAgent

    dev_path = Path(args.dev_path)
    tables_path = Path(args.tables_path)
    db_root = Path(args.db_root)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    predict_path = output_dir / args.predict_file
    summary_path = output_dir / args.summary_file
    trajectory_path = output_dir / args.trajectory_file

    print(">>> 正在加载 Spider 配置与题目集...")
    schema_meta_dict = build_schema_metadata_dict(tables_path)
    if args.schema_path_hints_selective:
        path_hint_mode = "selective"
    elif args.schema_path_hints:
        path_hint_mode = "all"
    else:
        path_hint_mode = "off"

    schema_retriever = SchemaRetriever(
        max_seed_tables=args.retrieval_max_seed_tables,
        max_return_tables=args.retrieval_max_return_tables,
        expand_hops=args.retrieval_expand_hops,
        min_table_score=args.retrieval_min_table_score,
        auto_mode_threshold=args.retrieval_auto_threshold,
        path_hint_mode=path_hint_mode,
        enable_value_hints=not args.disable_value_hints,
        value_hint_max_columns=args.value_hint_max_columns,
        value_hint_max_columns_per_table=args.value_hint_max_columns_per_table,
        value_hint_max_samples=args.value_hint_max_samples,
        enable_bridge_completion=not args.disable_bridge_completion,
    )

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
        max_retries=args.max_retries,
        retry_on_empty_result=args.retry_on_empty_result,
        superlative_mode=args.superlative_mode,
        superlative_router_use_threshold=args.superlative_router_use_threshold,
        superlative_router_template_threshold=args.superlative_router_template_threshold
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
            schema_meta = schema_meta_dict.get(db_id)
            if schema_meta is None:
                raise KeyError(f"未找到数据库 {db_id} 的 schema metadata。")
            db_path = build_db_path(db_root, db_id)
            retrieval_info = schema_retriever.retrieve(
                question=question,
                schema_meta=schema_meta,
                mode=args.schema_mode,
                db_path=str(db_path),
            )

            schema = retrieval_info["schema_text"]

            fallback_sql = "SELECT 1"
            agent_result = None
            runtime_error = None

            try:
                agent_result = agent.run_query(
                    schema_info=schema,
                    user_question=question,
                    db_path=str(db_path),
                    verbose=False,
                    retrieval_info=retrieval_info,
                    schema_meta=schema_meta,
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
                    "db_path": str(db_path),
                    "superlative_mode": args.superlative_mode,
                    "schema_mode_requested": retrieval_info["requested_mode"],
                    "schema_mode_applied": retrieval_info["applied_mode"],
                    "schema_fallback_reason": retrieval_info["fallback_reason"],
                    "schema_table_count": len(retrieval_info["selected_tables"]),
                    "schema_selected_tables": retrieval_info["selected_tables"],
                    "schema_seed_tables": retrieval_info["seed_tables"],
                    "schema_selected_foreign_keys": retrieval_info["selected_foreign_keys"],
                    "schema_join_paths": retrieval_info["join_paths"],
                    "schema_bridge_completion_enabled": retrieval_info["bridge_completion_enabled"],
                    "schema_bridge_anchor_tables": retrieval_info["bridge_anchor_tables"],
                    "schema_bridge_paths": retrieval_info["bridge_paths"],
                    "schema_bridge_added_tables": retrieval_info["bridge_added_tables"],
                    "schema_path_hint_requested_mode": retrieval_info["path_hint_requested_mode"],
                    "schema_path_hint_applied_mode": retrieval_info["path_hint_applied_mode"],
                    "schema_path_hints_enabled": retrieval_info["path_hints_enabled"],
                    "schema_path_hint_trigger_reasons": retrieval_info["path_hint_trigger_reasons"],
                    "schema_path_hint_focus_tables": retrieval_info["path_hint_focus_tables"],
                    "schema_path_hint_foreign_keys": retrieval_info["path_hint_foreign_keys"],
                    "schema_path_hint_join_paths": retrieval_info["path_hint_join_paths"],
                    "schema_path_hint_primary_join_path": retrieval_info["path_hint_primary_join_path"],
                    "schema_column_hints_enabled": retrieval_info["column_hints_enabled"],
                    "schema_column_hint_columns": retrieval_info["column_hint_columns"],
                    "schema_value_hints_enabled": retrieval_info["value_hints_enabled"],
                    "schema_value_hint_question_entities": retrieval_info["value_hint_question_entities"],
                    "schema_value_hint_entity_matches": retrieval_info["value_hint_entity_matches"],
                    "schema_value_hint_sampled_values": retrieval_info["value_hint_sampled_values"],
                    "schema_value_hint_candidate_columns": retrieval_info["value_hint_candidate_columns"],
                    "schema_table_scores_lexical": retrieval_info["table_scores_lexical"],
                    "schema_table_column_boosts": retrieval_info["table_column_boosts"],
                    "schema_column_scores": retrieval_info["column_scores"],
                    "semantic_retry_count": 0,
                    "final_verifier_result": None,
                    "used_success_fallback": False,
                    "success_fallback_reason": None,
                    "selected_success_attempt": None,
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
                    "db_path": agent_result.get("db_path", str(db_path)),
                    "superlative_mode": args.superlative_mode,
                    "schema_mode_requested": retrieval_info["requested_mode"],
                    "schema_mode_applied": retrieval_info["applied_mode"],
                    "schema_fallback_reason": retrieval_info["fallback_reason"],
                    "schema_table_count": len(retrieval_info["selected_tables"]),
                    "schema_selected_tables": retrieval_info["selected_tables"],
                    "schema_seed_tables": retrieval_info["seed_tables"],
                    "schema_selected_foreign_keys": retrieval_info["selected_foreign_keys"],
                    "schema_join_paths": retrieval_info["join_paths"],
                    "schema_bridge_completion_enabled": retrieval_info["bridge_completion_enabled"],
                    "schema_bridge_anchor_tables": retrieval_info["bridge_anchor_tables"],
                    "schema_bridge_paths": retrieval_info["bridge_paths"],
                    "schema_bridge_added_tables": retrieval_info["bridge_added_tables"],
                    "schema_path_hint_requested_mode": retrieval_info["path_hint_requested_mode"],
                    "schema_path_hint_applied_mode": retrieval_info["path_hint_applied_mode"],
                    "schema_path_hints_enabled": retrieval_info["path_hints_enabled"],
                    "schema_path_hint_trigger_reasons": retrieval_info["path_hint_trigger_reasons"],
                    "schema_path_hint_focus_tables": retrieval_info["path_hint_focus_tables"],
                    "schema_path_hint_foreign_keys": retrieval_info["path_hint_foreign_keys"],
                    "schema_path_hint_join_paths": retrieval_info["path_hint_join_paths"],
                    "schema_path_hint_primary_join_path": retrieval_info["path_hint_primary_join_path"],
                    "schema_column_hints_enabled": retrieval_info["column_hints_enabled"],
                    "schema_column_hint_columns": retrieval_info["column_hint_columns"],
                    "schema_value_hints_enabled": retrieval_info["value_hints_enabled"],
                    "schema_value_hint_question_entities": retrieval_info["value_hint_question_entities"],
                    "schema_value_hint_entity_matches": retrieval_info["value_hint_entity_matches"],
                    "schema_value_hint_sampled_values": retrieval_info["value_hint_sampled_values"],
                    "schema_value_hint_candidate_columns": retrieval_info["value_hint_candidate_columns"],
                    "schema_table_scores_lexical": retrieval_info["table_scores_lexical"],
                    "schema_table_column_boosts": retrieval_info["table_column_boosts"],
                    "schema_column_scores": retrieval_info["column_scores"],
                    "semantic_retry_count": agent_result.get("semantic_retry_count", 0),
                    "final_verifier_result": make_jsonable(agent_result.get("final_verifier_result")),
                    "used_success_fallback": agent_result.get("used_success_fallback", False),
                    "success_fallback_reason": agent_result.get("success_fallback_reason"),
                    "selected_success_attempt": agent_result.get("selected_success_attempt"),
                }
                summary_record["route"] = agent_result.get("route", "generic_llm")
                if agent_result.get("pattern_result"):
                    pattern_result = agent_result["pattern_result"]
                    summary_record["pattern_reason"] = pattern_result.get("reason")
                    summary_record["pattern_template"] = pattern_result.get("template")
                    summary_record["pattern_candidate_templates"] = pattern_result.get("candidate_templates")
                    summary_record["pattern_router_decision"] = make_jsonable(pattern_result.get("router_decision"))

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
                        "schema_retrieval": make_jsonable(retrieval_info),
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
