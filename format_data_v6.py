import json
import os

def build_schema_dict_v6(tables_path):
    """
    v6 动作一：解析 tables.json
    目标：
    - 结构清晰的中文半结构化表示
    - 融入主键(PK)和外键(FK)语义说明，代替生硬的缩写，降低认知负担
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

        # 外键映射 (子节点 -> 父节点)
        fk_map = {}
        for fk in foreign_keys:
            src, tgt = fk
            fk_map[src] = tgt

        schema_lines = []
        schema_lines.append(f"【数据库结构】\n数据库名称：{db_id}")

        for i, t_name in enumerate(table_names):
            schema_lines.append(f"- 表：{t_name}")
            col_descriptions = []

            for col_idx, col_name in tables_dict[i]:
                col_desc = col_name
                
                # 收集该列的约束信息
                constraints = []
                if col_idx in pk_set:
                    constraints.append("主键")
                
                if col_idx in fk_map:
                    ref_idx = fk_map[col_idx]
                    ref_table = table_names[column_names[ref_idx][0]]
                    ref_col = column_names[ref_idx][1]
                    constraints.append(f"外键指向 {ref_table}.{ref_col}")
                
                # 如果有约束，附加在字段名后面
                if constraints:
                    col_desc += f" ({'，'.join(constraints)})"
                
                col_descriptions.append(col_desc)

            # 将字段用逗号拼接，使其更紧凑、更接近自然语言表述
            schema_lines.append(f"  字段：{', '.join(col_descriptions)}")

        # 每个数据库的 schema 用换行符连接
        db_schemas[db_id] = "\n".join(schema_lines)

    return db_schemas


def format_to_sharegpt_v6(train_path, db_schemas, output_path):
    """
    v6 动作二：生成训练数据
    核心改动：
    - 恢复中文 System Prompt，对齐模型原生预训练习惯
    - 明确的 User Prompt 结构划分（【数据库结构】与【问题】）
    """
    with open(train_path, 'r', encoding='utf-8') as f:
        train_data = json.load(f)

    sharegpt_list = []

    # ✅ 核心修复：回归最适合 Qwen-Coder 的中文指令，语气专业且明确
    system_prompt = "你是一个顶尖的数据库架构师和SQL专家。请根据提供的数据库结构，将用户的自然语言问题转化为精确的SQL查询语句。不要输出任何解释性的废话。"

    for sample in train_data:
        db_id = sample['db_id']
        question = sample['question']
        query = sample['query']

        schema = db_schemas.get(db_id, "Schema not found.")

        # 拼接最终的用户输入
        user_prompt = f"{schema}\n\n【问题】\n{question}"

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

    print(f"✅ v6 数据生成完成！共 {len(sharegpt_list)} 条样本")


if __name__ == "__main__":
    # 路径配置
    data_dir = r"D:\VisSQL-Agent\data"
    tables_file = os.path.join(data_dir, "tables.json")
    train_file = os.path.join(data_dir, "train_spider.json")
    output_file = os.path.join(data_dir, "spider_sharegpt_v6.json")

    print("⏳ 构建 v6 schema...")
    schemas = build_schema_dict_v6(tables_file)
    print("✅ schema 构建完成，示例如下：\n")
    # 打印第一个 key 的示例，方便你直观检查格式是否符合预期
    sample_key = list(schemas.keys())[0]
    print(schemas[sample_key])
    print("-" * 50)

    print("⏳ 生成 v6 训练数据...")
    format_to_sharegpt_v6(train_file, schemas, output_file)