import os
import json
# 注意：确保这里导入了你实际使用的引擎类
from llm_inference import QwenSQLInference
from llm_inference_v1 import QwenSQLInference_v1
from llm_inference_v6 import QwenSQLInference_v6  # 新增：v6 推理引擎

########################################################################################################################
# 版本一的数据清洗模块 (适用于 v1, v2)
def build_spider_schemas(tables_json_path: str) -> dict:
    """把 Spider 极其反人类的 JSON 格式，解析成大模型最爱看的 CREATE TABLE 字符串字典。"""
    with open(tables_json_path, 'r', encoding='utf-8') as f:
        tables_data = json.load(f)
        
    schemas = {}
    for db in tables_data:
        db_id = db['db_id']
        table_names = db['table_names_original']
        col_names = db['column_names_original']
        tables = {i: [] for i in range(len(table_names))}
        for col in col_names:
            table_idx, col_name = col[0], col[1]
            if table_idx >= 0:
                tables[table_idx].append(col_name)
        schema_str = ""
        for i, t_name in enumerate(table_names):
            cols = ", ".join(tables[i])
            schema_str += f"CREATE TABLE {t_name} ({cols});\n"
        schemas[db_id] = schema_str.strip()
    return schemas

##########################################################################################################################
# 版本二的数据清洗模块 (适用于 v3, v4)
def build_golden_ddl(tables_path):
    """深度挖掘 Spider 的 tables.json，提取类型、主键、外键，生成纯正的 DDL 语句。"""
    with open(tables_path, 'r', encoding='utf-8') as f:
        tables_data = json.load(f)
    db_schemas = {}
    for db in tables_data:
        db_id = db['db_id']
        table_names = db['table_names_original']
        col_names = db['column_names_original']  
        col_types = db['column_types']            
        primary_keys = db['primary_keys']        
        foreign_keys = db['foreign_keys']        
        
        tables_dict = {i: {"name": t_name, "cols": [], "pks": [], "fks": []} for i, t_name in enumerate(table_names)}
        
        for col_idx, (table_idx, col_name) in enumerate(col_names):
            if table_idx == -1: continue
            c_type = col_types[col_idx].upper()
            if c_type == 'NUMBER': c_type = 'INT'
            elif c_type == 'TEXT': c_type = 'VARCHAR'
            elif c_type == 'TIME': c_type = 'DATETIME'
            elif c_type == 'BOOLEAN': c_type = 'BOOLEAN'
            elif c_type == 'OTHERS': c_type = 'VARCHAR' 
            tables_dict[table_idx]["cols"].append(f"{col_name} {c_type}")
            if col_idx in primary_keys:
                tables_dict[table_idx]["pks"].append(col_name)
                
        for fk in foreign_keys:
            src_col_idx, tgt_col_idx = fk
            src_table_idx, src_col_name = col_names[src_col_idx][0], col_names[src_col_idx][1]
            tgt_table_idx, tgt_col_name = col_names[tgt_col_idx][0], col_names[tgt_col_idx][1]
            tgt_table_name = table_names[tgt_table_idx]
            fk_str = f"FOREIGN KEY ({src_col_name}) REFERENCES {tgt_table_name}({tgt_col_name})"
            tables_dict[src_table_idx]["fks"].append(fk_str)
            
        ddl_lines = [f"-- 数据库: {db_id}"]
        for t_idx, t_info in tables_dict.items():
            t_name = t_info["name"]
            table_body_lines = []
            table_body_lines.extend([f"  {c}" for c in t_info["cols"]])
            if t_info["pks"]:
                pks_str = ", ".join(t_info["pks"])
                table_body_lines.append(f"  PRIMARY KEY ({pks_str})")
            table_body_lines.extend([f"  {fk}" for fk in t_info["fks"]])
            table_body = ",\n".join(table_body_lines)
            ddl = f"CREATE TABLE {t_name} (\n{table_body}\n);"
            ddl_lines.append(ddl)
        db_schemas[db_id] = "\n\n".join(ddl_lines)
    return db_schemas

##########################################################################################################################
# 版本三的数据清洗模块 (v5 适配版本 - 半结构化英文格式)
def build_schema_dict_v5(tables_path):
    """提取半结构化（表 + 列 + PK + FK）降噪格式。"""
    with open(tables_path, 'r', encoding='utf-8') as f:
        tables_data = json.load(f)
    db_schemas = {}
    for db in tables_data:
        db_id = db['db_id']
        table_names = db['table_names_original']
        column_names = db['column_names_original']
        primary_keys = db['primary_keys']
        foreign_keys = db['foreign_keys']

        tables_dict = {i: [] for i in range(len(table_names))}
        for idx, col in enumerate(column_names):
            table_idx = col[0]
            col_name = col[1]
            if table_idx == -1: continue
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


##########################################################################################################################
# 版本四的数据清洗模块 (v6 适配版本 - 纯中文指令 + 主外键语义约束)
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

##################################################################

def run_evaluation():
    print(">>> 正在加载 Spider 数据集配置...")
    
    # 【根据你要跑的版本，打开对应的注释】
    # schemas_dict = build_spider_schemas("data/tables.json")  # v1/v2版本
    # schemas_dict = build_golden_ddl("data/tables.json")      # v3/v4版本
    # schemas_dict = build_schema_dict_v5("data/tables.json")    # v5版本
    schemas_dict = build_schema_dict_v6("data/tables.json")      # v6版本 (当前开启)
    
    # 读取 1034 道真实测试题
    with open("data/dev.json", 'r', encoding='utf-8') as f:
        dev_dataset = json.load(f)
        
    # ==========================================
    # 实例化引擎
    # ==========================================
    print(">>> 正在启动系统...")
    base_model_dir = "/root/autodl-tmp/qwen2.5-coder-7b-instruct"  # 确保这里是纯净基座（非挂载测试时，改成合并版路径即可）
    
    # 切换到 v6 的 lora 权重路径
    lora_weight_dir = "/root/autodl-tmp/LLaMA-Factory/saves/Qwen2.5-7B/lora/qwen_spider_lora_v6"
    
    # 实例化 v6 引擎类
    llm_engine = QwenSQLInference_v6(
        base_model_path=base_model_dir, 
        lora_path=lora_weight_dir
    )
    
    test_batch = dev_dataset  
    total_count = len(test_batch)

    print(f"\n>>> 考场答题机已启动，开始生成预测试卷... 共计: {total_count} 道题")
    
    # ==========================================
    # 核心改造：放弃裁判身份，只负责把答案一行一行写进 predict.txt
    # ==========================================
    with open("predict.txt", "w", encoding="utf-8") as out_f:
        for idx, item in enumerate(test_batch):
            db_id = item["db_id"]
            question = item["question"]
            
            schema = schemas_dict.get(db_id, "")
            
            # 推理生成 SQL
            pred_sql = llm_engine.generate_sql(question, schema)

            # 极其关键：官方打分脚本极其死板，它要求每道题必须只占单行。
            # 如果大模型输出了带有换行符的复杂 SQL，我们要把它压扁成一行。
            clean_sql = pred_sql.replace("\n", " ").replace("\t", " ").strip()

            # 将纯净的单行 SQL 写入答题卡
            out_f.write(clean_sql + "\n")
            
            # 打印优雅的进度条，防止看着像死机
            if (idx + 1) % 50 == 0 or (idx + 1) == total_count:
                print(f"[{idx + 1}/{total_count}] 答卷生成中... 最新一题的回答: {clean_sql[:60]}...")

    print("\n>>> 🏁 所有题目预测完毕！答题卡已锁入: predict.txt")
    print(">>> 接下来，请在终端召唤官方无情裁判：")
    print("python evaluation.py --gold data/dev_gold.sql --pred predict.txt --db data/database_test_suite/ --table data/tables.json --etype all")

if __name__ == "__main__":
    run_evaluation()