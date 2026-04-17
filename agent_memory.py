class WorkingMemory:
    def __init__(self):
        """
        初始化工作记忆。
        （注意：去掉了 system_prompt 的管理，因为那是 Coder 节点内部的逻辑）
        """
        self.messages = [] # 原生 ChatML 格式的列表
        
    def add_initial_query(self, schema_info: str, question: str):
        """
        记录第一次的完整用户查询 (组装 V6 格式)
        """
        # 将表结构和用户问题拼接，作为第一把推开大门的钥匙
        combined_content = f"问题：{question}\n\n数据库结构：\n{schema_info}"
        self.messages.append({"role": "user", "content": combined_content})

    def add_assistant_sql(self, sql: str):
        """记录模型生成的 SQL 代码"""
        self.messages.append({"role": "assistant", "content": sql})

    def add_execution_feedback(self, error_type: str, error_msg: str):
        """
        极其核心的加工厂：将冰冷的 SQLite 报错，转化为温暖且严厉的 Prompt
        """
        feedback_prompt = (
            f"【沙盒执行拦截】\n"
            f"你刚刚生成的 SQL 在真实数据库中执行失败。\n"
            f"错误类型: {error_type}\n"
            f"错误详情: {error_msg}\n"
            f"请仔细核对上方提供的数据库 Schema，深刻反思并输出修正后的 SQL。"
        )
        # 这里的 user 代表“沙盒环境”向大模型发出的反馈
        self.messages.append({"role": "user", "content": feedback_prompt})
        
    def get_current_messages(self) -> list:
        """吐出干干净净的对话列表，直接喂给 Coder"""
        return self.messages

# --- 极简测试 ---
if __name__ == "__main__":
    memory = WorkingMemory()
    memory.add_initial_query("表 student (id, name)", "查所有学生")
    memory.add_assistant_sql("SELECT names FROM student")
    memory.add_execution_feedback("OperationalError", "no such column: names")
    
    print("当前记忆流：")
    for msg in memory.get_current_messages():
        print(f"[{msg['role'].upper()}]: {msg['content'][:50]}...")