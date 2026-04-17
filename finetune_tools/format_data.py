import json
import os

def build_schema_dict(tables_path):
    """
    动作一：解析 tables.json
    目标：返回一个字典，键是 db_id，值是拼装好的、大白话形式的表结构字符串。
    """
    # 1. 读取 JSON 文件
    with open(tables_path, 'r', encoding='utf-8') as f:  #读取json文件，并赋值给文件对象f
        tables_data = json.load(f)  #通过文件对象f将文件里的json内容复制到了tables_data变量中
        
    db_schemas = {}

    #2. 遍历每个数据库的信息
    for db in tables_data:
        db_id = db['db_id']
        table_names = db['table_names_original']
        column_names = db['column_names_original']
    
        # 2.1. 初始化一个字典，键是表索引，值是该表的列名列表
        # 例如: {0: [], 1: []}
        tables_dict = {i: [] for i in range(len(table_names))}
        
        # 2.2. 把所有列名按索引塞进对应的列表里
        for col in column_names:
            table_idx = col[0]
            col_name = col[1]
            if table_idx == -1:  # 跳过统配符 *
                continue
            tables_dict[table_idx].append(col_name)
                
        # 2.3. 组装 Schema 字符串 (使用 .join() 拼接字符串在 Python 中效率更高)
        schema_lines = [f"数据库名称: {db_id}"]
        for i, t_name in enumerate(table_names):
            # 将列表里的列名用逗号拼接起来: "id, name, age"
            cols_str = ", ".join(tables_dict[i])
            schema_lines.append(f"表 {t_name} 包含列: {cols_str}")
            
        # 用换行符把所有表的信息拼成一段完整的话
        db_schemas[db_id] = "\n".join(schema_lines)
    
    return db_schemas


def format_to_sharegpt(train_path, db_schemas, output_path):
    """
    动作二：读取 train_spider.json，与 schema 组装并导出
    """
    # 1. 读取 JSON 文件
    with open(train_path, 'r', encoding='utf-8') as f:  
        train_data = json.load(f) 

    # 在循环外建立一个空列表，用来装所有的对话样本
    sharegpt_list = []

    #2. 遍历每个样本
    for sample in train_data:
        db_id = sample['db_id']
        question = sample['question'] # 提取用户的自然语言提问
        query = sample['query']       # 提取标准 SQL 答案

        # 查字典拿到大白话表结构 (使用 .get 防御一下找不到的情况)
        schema = db_schemas.get(db_id, "未找到表结构。")
        
        # 构建强硬的系统提示词
        system_prompt = (
            "你是一个顶尖的数据库架构师和SQL专家。请根据以下数据库的表结构，"
            "将用户的自然语言问题转化为精确的SQL查询语句。不要输出任何解释性的废话。\n\n"
            f"【表结构】\n{schema}"
        )

        # 严格按照 ShareGPT 格式组装
        formatted_data = {
            "conversations": [
                {"from": "system", "value": system_prompt},
                {"from": "user", "value": question},
                {"from": "assistant", "value": query}
            ]
        }
        
        # 把组装好的单条样本塞进大列表里
        sharegpt_list.append(formatted_data)

    # 3. 将最终的列表写入到输出文件中
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(sharegpt_list, f, ensure_ascii=False, indent=2)

    print(f"✅ 数据清洗完毕！成功组装 {len(sharegpt_list)} 条训练数据。")


if __name__ == "__main__":
    # 定义文件路径
    data_dir = r"D:\VisSQL-Agent\data"
    tables_file = os.path.join(data_dir, "tables.json")
    train_file = os.path.join(data_dir, "train_spider.json")
    output_file = os.path.join(data_dir, "spider_sharegpt.json")

    print("开始解析数据库 Schema...")
    schemas = build_schema_dict(tables_file)
    print("解析完成")

    print("⏳ 开始生成 ShareGPT 训练数据...")
    format_to_sharegpt(train_file, schemas, output_file)