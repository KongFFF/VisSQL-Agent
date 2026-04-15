import json
import os

def build_golden_ddl(tables_path):
    """
    动作一：重铸 DDL 解析引擎
    目标：深度挖掘 Spider 的 tables.json，提取类型、主键、外键，生成纯正的 DDL 语句。
    """
    with open(tables_path, 'r', encoding='utf-8') as f:
        tables_data = json.load(f)
        
    db_schemas = {}

    for db in tables_data:
        db_id = db['db_id']
        table_names = db['table_names_original']
        col_names = db['column_names_original']  # [table_idx, col_name]
        col_types = db['column_types']           # ["text", "number", ...]
        primary_keys = db['primary_keys']        # [col_idx, ...]
        foreign_keys = db['foreign_keys']        # [[col_idx1, col_idx2], ...]
        
        # 1. 初始化表结构载体
        # 格式: {table_idx: {"name": "表名", "cols": [], "pks": [], "fks": []}}
        tables_dict = {i: {"name": t_name, "cols": [], "pks": [], "fks": []} for i, t_name in enumerate(table_names)}
        
        # 2. 挖掘列名与数据类型
        for col_idx, (table_idx, col_name) in enumerate(col_names):
            if table_idx == -1:  # 跳过开头的通配符 *
                continue
                
            # SQL 类型标准化映射
            c_type = col_types[col_idx].upper()
            if c_type == 'NUMBER': c_type = 'INT'
            elif c_type == 'TEXT': c_type = 'VARCHAR'
            elif c_type == 'TIME': c_type = 'DATETIME'
            elif c_type == 'BOOLEAN': c_type = 'BOOLEAN'
            elif c_type == 'OTHERS': c_type = 'VARCHAR' # 兜底类型
            
            # 记录列名和类型
            tables_dict[table_idx]["cols"].append(f"{col_name} {c_type}")
            
            # 挖掘主键 (如果当前列的全局索引在 primary_keys 列表里)
            if col_idx in primary_keys:
                tables_dict[table_idx]["pks"].append(col_name)
                
        # 3. 挖掘并组装跨表外键桥梁 (极其关键！)
        for fk in foreign_keys:
            src_col_idx, tgt_col_idx = fk
            
            src_table_idx = col_names[src_col_idx][0]
            src_col_name = col_names[src_col_idx][1]
            
            tgt_table_idx = col_names[tgt_col_idx][0]
            tgt_col_name = col_names[tgt_col_idx][1]
            tgt_table_name = table_names[tgt_table_idx]
            
            # 组装纯正的外键约束语句
            fk_str = f"FOREIGN KEY ({src_col_name}) REFERENCES {tgt_table_name}({tgt_col_name})"
            tables_dict[src_table_idx]["fks"].append(fk_str)
            
        # 4. 最终合并为标准 CREATE TABLE 格式
        ddl_lines = [f"-- 数据库: {db_id}"]
        for t_idx, t_info in tables_dict.items():
            t_name = t_info["name"]
            table_body_lines = []
            
            # 塞入列定义
            table_body_lines.extend([f"  {c}" for c in t_info["cols"]])
            # 塞入主键约束
            if t_info["pks"]:
                pks_str = ", ".join(t_info["pks"])
                table_body_lines.append(f"  PRIMARY KEY ({pks_str})")
            # 塞入外键约束
            table_body_lines.extend([f"  {fk}" for c in t_info["fks"]])
            
            # 拼接单张表
            table_body = ",\n".join(table_body_lines)
            ddl = f"CREATE TABLE {t_name} (\n{table_body}\n);"
            ddl_lines.append(ddl)
            
        # 把这个数据库的所有表拼装起来
        db_schemas[db_id] = "\n\n".join(ddl_lines)
        
    return db_schemas


def format_to_sharegpt_golden(train_path, db_schemas, output_path):
    """
    动作二：组装 ShareGPT 格式
    注意：系统提示词也必须随之升级为硬核代码风格。
    """
    with open(train_path, 'r', encoding='utf-8') as f:
        train_data = json.load(f)

    sharegpt_list = []

    for sample in train_data:
        db_id = sample['db_id']
        question = sample['question']
        query = sample['query']

        schema_ddl = db_schemas.get(db_id, "-- 缺少数据库元数据")
        
        # 【极其关键】升级系统提示词！
        # 让模型明确知道它现在是在看极其专业的 DDL，而不是听大白话。
        system_prompt = (
            "你是一个极其专业的数据库架构师。请仔细阅读以下包含数据类型、主键(PRIMARY KEY)和外键(FOREIGN KEY)的数据库 DDL 结构。\n"
            "严格遵循表间关联关系，将用户的自然语言问题转化为极其精确的 SQL 查询语句。不要输出任何解释性文本。\n\n"
            f"{schema_ddl}"
        )

        formatted_data = {
            "system": system_prompt,
            "conversations": [
                {"from": "user", "value": question},
                {"from": "assistant", "value": query}
            ]
        }
        sharegpt_list.append(formatted_data)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(sharegpt_list, f, ensure_ascii=False, indent=2)

    print(f"✅ 黄金弹药库锻造完毕！成功组装 {len(sharegpt_list)} 条包含 PK/FK 的专业训练数据。")


if __name__ == "__main__":
    # 替换为你实际的路径
    data_dir = r"D:\VisSQL-Agent\data"
    tables_file = os.path.join(data_dir, "tables.json")
    train_file = os.path.join(data_dir, "train_spider.json")
    
    # 启用全新命名，与旧时代的青铜数据彻底划清界限！
    output_file = os.path.join(data_dir, "spider_sharegpt_golden.json")

    print("开始深度挖掘数据库 Schema (数据类型 / PK / FK)...")
    schemas = build_golden_ddl(tables_file)
    
    print("⏳ 开始生成黄金级 ShareGPT 训练数据...")
    format_to_sharegpt_golden(train_file, schemas, output_file)