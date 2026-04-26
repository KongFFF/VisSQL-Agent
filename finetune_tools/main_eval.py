import argparse
import json

from llm_inference_v6 import QwenSQLInference_v6

DEFAULT_BASE_MODEL = "/root/autodl-tmp/qwen2.5-coder-7b-instruct"
DEFAULT_LORA_PATH = "/root/autodl-tmp/LLaMA-Factory/saves/Qwen2.5-7B/lora/qwen_spider_lora_v6"
DEFAULT_DEV_PATH = "data/dev.json"
DEFAULT_TABLES_PATH = "data/tables.json"
DEFAULT_OUTPUT_FILE = "predict.txt"
DEFAULT_GOLD_PATH = "data/dev_gold.sql"
DEFAULT_DB_ROOT = "data/testsuitedatabases/database"


def build_spider_schemas(tables_json_path: str) -> dict:
    """v1/v2: simple CREATE TABLE style schema."""
    with open(tables_json_path, "r", encoding="utf-8") as f:
        tables_data = json.load(f)

    schemas = {}
    for db in tables_data:
        db_id = db["db_id"]
        table_names = db["table_names_original"]
        col_names = db["column_names_original"]
        tables = {i: [] for i in range(len(table_names))}
        for table_idx, col_name in col_names:
            if table_idx >= 0:
                tables[table_idx].append(col_name)
        schema_str = ""
        for i, t_name in enumerate(table_names):
            cols = ", ".join(tables[i])
            schema_str += f"CREATE TABLE {t_name} ({cols});\n"
        schemas[db_id] = schema_str.strip()
    return schemas


def build_golden_ddl(tables_path: str) -> dict:
    """v3/v4: richer DDL with types, PKs and FKs."""
    with open(tables_path, "r", encoding="utf-8") as f:
        tables_data = json.load(f)

    db_schemas = {}
    for db in tables_data:
        db_id = db["db_id"]
        table_names = db["table_names_original"]
        col_names = db["column_names_original"]
        col_types = db["column_types"]
        primary_keys = db["primary_keys"]
        foreign_keys = db["foreign_keys"]

        tables_dict = {
            i: {"name": t_name, "cols": [], "pks": [], "fks": []}
            for i, t_name in enumerate(table_names)
        }

        for col_idx, (table_idx, col_name) in enumerate(col_names):
            if table_idx == -1:
                continue
            c_type = col_types[col_idx].upper()
            if c_type == "NUMBER":
                c_type = "INT"
            elif c_type == "TEXT":
                c_type = "VARCHAR"
            elif c_type == "TIME":
                c_type = "DATETIME"
            elif c_type == "BOOLEAN":
                c_type = "BOOLEAN"
            elif c_type == "OTHERS":
                c_type = "VARCHAR"
            tables_dict[table_idx]["cols"].append(f"{col_name} {c_type}")
            if col_idx in primary_keys:
                tables_dict[table_idx]["pks"].append(col_name)

        for src_col_idx, tgt_col_idx in foreign_keys:
            src_table_idx, src_col_name = col_names[src_col_idx]
            tgt_table_idx, tgt_col_name = col_names[tgt_col_idx]
            tgt_table_name = table_names[tgt_table_idx]
            fk_str = f"FOREIGN KEY ({src_col_name}) REFERENCES {tgt_table_name}({tgt_col_name})"
            tables_dict[src_table_idx]["fks"].append(fk_str)

        ddl_lines = [f"-- Database {db_id}"]
        for t_info in tables_dict.values():
            table_body_lines = [f"  {c}" for c in t_info["cols"]]
            if t_info["pks"]:
                pks_str = ", ".join(t_info["pks"])
                table_body_lines.append(f"  PRIMARY KEY ({pks_str})")
            table_body_lines.extend([f"  {fk}" for fk in t_info["fks"]])
            table_body = ",\n".join(table_body_lines)
            ddl_lines.append(f"CREATE TABLE {t_info['name']} (\n{table_body}\n);")
        db_schemas[db_id] = "\n\n".join(ddl_lines)
    return db_schemas


def build_schema_dict_v5(tables_path: str) -> dict:
    """v5: semi-structured English schema format."""
    with open(tables_path, "r", encoding="utf-8") as f:
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

        schema_lines = [f"Database: {db_id}"]
        for i, t_name in enumerate(table_names):
            schema_lines.append(f"\nTable: {t_name}")
            schema_lines.append("Columns:")
            for col_idx, col_name in tables_dict[i]:
                col_desc = f"- {col_name}"
                if col_idx in pk_set:
                    col_desc += " [PK]"
                if col_idx in fk_map:
                    ref_idx = fk_map[col_idx]
                    ref_table = table_names[column_names[ref_idx][0]]
                    ref_col = column_names[ref_idx][1]
                    col_desc += f" [FK -> {ref_table}.{ref_col}]"
                schema_lines.append(col_desc)
        db_schemas[db_id] = "\n".join(schema_lines)
    return db_schemas


def build_schema_dict_v6(tables_path: str) -> dict:
    """v6: compact Chinese schema with PK/FK semantics."""
    with open(tables_path, "r", encoding="utf-8") as f:
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
        for i, t_name in enumerate(table_names):
            schema_lines.append(f"- 表：{t_name}")
            col_descriptions = []
            for col_idx, col_name in tables_dict[i]:
                col_desc = col_name
                constraints = []
                if col_idx in pk_set:
                    constraints.append("主键")
                if col_idx in fk_map:
                    ref_idx = fk_map[col_idx]
                    ref_table = table_names[column_names[ref_idx][0]]
                    ref_col = column_names[ref_idx][1]
                    constraints.append(f"外键指向 {ref_table}.{ref_col}")
                if constraints:
                    col_desc += f" ({'；'.join(constraints)})"
                col_descriptions.append(col_desc)
            schema_lines.append(f"  字段：{', '.join(col_descriptions)}")

        db_schemas[db_id] = "\n".join(schema_lines)
    return db_schemas


SCHEMA_BUILDERS = {
    "v1": build_spider_schemas,
    "v3": build_golden_ddl,
    "v5": build_schema_dict_v5,
    "v6": build_schema_dict_v6,
}


def parse_args():
    parser = argparse.ArgumentParser(description="运行纯模型 Text-to-SQL 基线评测。")
    parser.add_argument("--base-model", default=DEFAULT_BASE_MODEL, help="基座模型路径")
    parser.add_argument("--lora-path", default=DEFAULT_LORA_PATH, help="LoRA 权重路径")
    parser.add_argument("--no-lora", action="store_true", help="不挂载 LoRA，仅使用基座模型")
    parser.add_argument("--dev-path", default=DEFAULT_DEV_PATH, help="Spider dev.json 路径")
    parser.add_argument("--tables-path", default=DEFAULT_TABLES_PATH, help="Spider tables.json 路径")
    parser.add_argument("--output-file", default=DEFAULT_OUTPUT_FILE, help="预测 SQL 输出文件")
    parser.add_argument("--gold-path", default=DEFAULT_GOLD_PATH, help="官方评测 gold 文件路径")
    parser.add_argument("--db-root", default=DEFAULT_DB_ROOT, help="官方评测数据库根目录")
    parser.add_argument(
        "--schema-format",
        choices=sorted(SCHEMA_BUILDERS),
        default="v6",
        help="构造输入 schema 的格式版本",
    )
    parser.add_argument("--progress-every", type=int, default=50, help="每多少题打印一次进度")
    return parser.parse_args()


def run_evaluation(args=None):
    args = args or parse_args()

    print(">>> 正在加载 Spider 数据集配置...")
    schema_builder = SCHEMA_BUILDERS[args.schema_format]
    schemas_dict = schema_builder(args.tables_path)

    with open(args.dev_path, "r", encoding="utf-8") as f:
        dev_dataset = json.load(f)

    print(">>> 正在启动系统...")
    lora_path = None if args.no_lora else args.lora_path
    llm_engine = QwenSQLInference_v6(
        base_model_path=args.base_model,
        lora_path=lora_path,
    )

    total_count = len(dev_dataset)
    print(f"\n>>> 开始生成预测，共计: {total_count} 道题")

    with open(args.output_file, "w", encoding="utf-8") as out_f:
        for idx, item in enumerate(dev_dataset):
            db_id = item["db_id"]
            question = item["question"]
            schema = schemas_dict.get(db_id, "")

            pred_sql = llm_engine.generate_sql(question, schema)
            clean_sql = pred_sql.replace("\n", " ").replace("\t", " ").strip()
            out_f.write(clean_sql + "\n")

            if (idx + 1) % args.progress_every == 0 or (idx + 1) == total_count:
                print(f"[{idx + 1}/{total_count}] {clean_sql[:60]}...")

    print(f"\n>>> 所有题目预测完毕，结果已写入: {args.output_file}")
    print(">>> 官方评测命令：")
    print(
        f"python -m spider_eval.evaluation --gold {args.gold_path} "
        f"--pred {args.output_file} --db {args.db_root} "
        f"--table {args.tables_path} --etype all"
    )


if __name__ == "__main__":
    run_evaluation()
