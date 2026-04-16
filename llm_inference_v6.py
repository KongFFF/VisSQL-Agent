import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel

class QwenSQLInference_v6:
    """
    Qwen Text-to-SQL 推理引擎 (V6 终极对齐版)。
    核心改进：System Prompt 与 User Prompt 格式与 V6 训练集 100% 严格一致。
    """
    
    def __init__(self, base_model_path: str, lora_path: str = None):
        """
        初始化引擎：加载 Tokenizer、基础模型，并挂载 LoRA 适配器。
        """
        print("正在唤醒 Qwen 基座模型并加载 Tokenizer...")
        self.tokenizer = AutoTokenizer.from_pretrained(base_model_path, trust_remote_code=True)
        
        # 保持 bfloat16 数据类型，确保显存不爆炸且精度损失最小
        self.base_model = AutoModelForCausalLM.from_pretrained(
            base_model_path,
            torch_dtype=torch.bfloat16,  
            device_map={"": 0},           # 明确指定显卡设备，避免潜在的分配错误
            trust_remote_code=True
        )
        
        # 【核心改动】：加一个 if 判断
        if lora_path:
            print(f">>> 🛡️ 正在挂载 V6 LoRA 权重: {lora_path} ...")
            self.model = PeftModel.from_pretrained(base_model, lora_path).eval()
        else:
            print(f">>> 裸奔模式！使用纯净版基座模型进行 Zero-Shot 评测...")
            self.model = base_model.eval()
        print("模型加载完毕，准备就绪。")

    def generate_sql(self, question: str, table_schema: str) -> str:
        """
        核心推理逻辑：接收问题和半结构化表结构，生成并返回纯净的 SQL 字符串。
        """

        # ✅ 【核心命门】：这里的提示词必须与 format_data_v6.py 中的一字不差！
        system_prompt = "你是一个顶尖的数据库架构师和SQL专家。请根据提供的数据库结构，将用户的自然语言问题转化为精确的SQL查询语句。不要输出任何解释性的废话。"
        
        # ✅ 【严格对齐】：使用与训练时完全相同的拼接方式
        user_prompt = f"{table_schema}\n\n【问题】\n{question}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        # 1. 纯净模板拼接：只拼成带 <|im_start|> 的字符串，不做 Tensor 转化
        prompt_text = self.tokenizer.apply_chat_template(
            messages, 
            tokenize=False, # 极其关键：设为 False，只输出字符串
            add_generation_prompt=True 
        )

        # 2. 显式张量化：把字符串转成包含 input_ids 和 attention_mask 的字典，并推上显卡
        model_inputs = self.tokenizer([prompt_text], return_tensors="pt").to(self.model.device)

        # 3. 传入大模型：使用 ** 解包字典，喂给 generate 函数
        generated_ids = self.model.generate(
            **model_inputs,
            max_new_tokens=512,
            do_sample=False  # 极其关键：关闭采样，强制进入贪心解码模式，替代温度控制
        )

        # 4. 解码结果，截出纯净 SQL
        input_len = model_inputs["input_ids"].shape[1]
        response_ids = generated_ids[0][input_len:]
        response_sql = self.tokenizer.decode(response_ids, skip_special_tokens=True)

        # 为了防止模型偶尔发神经输出 Markdown 代码块，可以做个防御性清理
        response_sql = response_sql.strip()
        if response_sql.startswith("```sql"):
            response_sql = response_sql[6:]
        if response_sql.startswith("```"):
            response_sql = response_sql[3:]
        if response_sql.endswith("```"):
            response_sql = response_sql[:-3]

        return response_sql.strip()

# 简单的测试入口
if __name__ == "__main__":
    # 替换为你自己的路径
    BASE_MODEL = "/root/autodl-tmp/qwen2.5-coder-7b-instruct"
    LORA_MODEL = "saves/Qwen2.5-7B/lora/qwen_spider_lora_v6" # 假设你的 V6 lora 存放在这
    
    engine = QwenSQLInference_v6(BASE_MODEL, LORA_MODEL)
    
    # 构造一段 V6 格式的测试 Schema
    test_schema = """【数据库结构】
数据库名称：school_management
- 表：student
  字段：student_id (主键), name, age
- 表：course
  字段：course_id (主键), course_name
- 表：student_course
  字段：student_id (外键指向 student.student_id), course_id (外键指向 course.course_id)"""
    
    test_question = "选修了数学课的学生的平均年龄是多少？"
    
    result = engine.generate_sql(test_question, test_schema)
    print("\n========= 生成的 SQL =========")
    print(result)