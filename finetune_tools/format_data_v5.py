import json
import os

def build_schema_dict_v5(tables_path):
    """
    v5 动作一：解析 tables.json
    目标：
    - 英文 schema（tokenizer 友好）
    - 半结构化（表 + 列 + PK + FK）
    """

    with open(tables_path, 'r', encoding='utf-8') as f:
        tables_data = json.load(f)

    db_schemas = {}

    for db in tables_data:
        db_id = db['db_id']
        table_names = db['table_names_original']
        column_names = db['column_names_original']
        primary_keys = db['primary_keys']
        foreign_keys = db['foreign_keys']

        # 表 -> 列
        tables_dict = {i: [] for i in range(len(table_names))}

        for idx, col in enumerate(column_names):
            table_idx = col[0]
            col_name = col[1]
            if table_idx == -1:
                continue
            tables_dict[table_idx].append((idx, col_name))

        # 主键集合
        pk_set = set(primary_keys)

        # 外键映射
        fk_map = {}
        for fk in foreign_keys:
            src, tgt = fk
            fk_map[src] = tgt

        schema_lines = []
        schema_lines.append(f"Database: {db_id}")

        for i, t_name in enumerate(table_names):
            schema_lines.append(f"\nTable: {t_name}")
            schema_lines.append("Columns:")

            for col_idx, col_name in tables_dict[i]:
                col_desc = f"- {col_name}"

                # 主键
                if col_idx in pk_set:
                    col_desc += " [PK]"

                # 外键
                if col_idx in fk_map:
                    ref_idx = fk_map[col_idx]
                    ref_table = table_names[column_names[ref_idx][0]]
                    ref_col = column_names[ref_idx][1]
                    col_desc += f" [FK -> {ref_table}.{ref_col}]"

                schema_lines.append(col_desc)

        db_schemas[db_id] = "\n".join(schema_lines)

    return db_schemas


def format_to_sharegpt_v5(train_path, db_schemas, output_path):
    """
    v5 动作二：生成训练数据
    核心改动：
    - 全英文 prompt
    - schema + question 强绑定
    """

    with open(train_path, 'r', encoding='utf-8') as f:
        train_data = json.load(f)

    sharegpt_list = []

    # ✅ 极简英文 system（更贴近预训练）
    system_prompt = "Generate SQL based on the database schema. Output only SQL."

    for sample in train_data:
        db_id = sample['db_id']
        question = sample['question']
        query = sample['query']

        schema = db_schemas.get(db_id, "Schema not found.")

        user_prompt = f"""Database schema:
{schema}

Question:
{question}"""

        formatted_data = {
            "conversations": [
                {"from": "system", "value": system_prompt},
                {"from": "user", "value": user_prompt},
                {"from": "assistant", "value": query}
            ]
        }

        sharegpt_list.append(formatted_data)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(sharegpt_list, f, ensure_ascii=False, indent=2)

    print(f"✅ v5 数据生成完成！共 {len(sharegpt_list)} 条样本")


if __name__ == "__main__":
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    data_dir = os.path.join(repo_root, "data")
    tables_file = os.path.join(data_dir, "tables.json")
    train_file = os.path.join(data_dir, "train_spider.json")
    output_file = os.path.join(data_dir, "spider_sharegpt_v5.json")

    print("⏳ 构建 v5 schema...")
    schemas = build_schema_dict_v5(tables_file)
    print("✅ schema 构建完成")

    print("⏳ 生成 v5 训练数据...")
    format_to_sharegpt_v5(train_file, schemas, output_file)
