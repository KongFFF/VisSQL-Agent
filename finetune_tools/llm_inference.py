import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel

class QwenSQLInference:
    """
    Qwen Text-to-SQL 推理引擎。
    负责加载基座模型与 LoRA 权重，并处理自然语言到 SQL 的生成。
    """
    
    def __init__(self, base_model_path: str, lora_path: str):
        """
        初始化引擎：加载 Tokenizer、基础模型，并挂载 LoRA 适配器。
        """
        print("正在唤醒 Qwen 基座模型并加载 Tokenizer...")


        #合并模型模式
        self.model = AutoModelForCausalLM.from_pretrained(
            "/root/autodl-tmp/qwen2.5-7B-Spider-V2-Merged",  # 填入你刚才导出的那个新文件夹路径
            device_map={"": 0}, 
            torch_dtype=torch.bfloat16  # 或者 torch.float16
        )

        self.tokenizer = AutoTokenizer.from_pretrained(
            "/root/autodl-tmp/qwen2.5-7B-Spider-V2-Merged"   # 同样是合并后的文件夹路径
        )
        ###############################################################

        #外挂权重模式
        # 使用 AutoTokenizer 加载分词器
        # self.tokenizer = AutoTokenizer.from_pretrained(base_model_path, trust_remote_code=True)
        # # trust_remote_code=True 是国内大模型（比如 Qwen、ChatGLM）的标准起手式，允许加载模型自带的一些特殊分词脚本。
        
        # # 使用 AutoModelForCausalLM 加载基座模型（注意数据类型推荐用 torch.bfloat16，并加到 cuda 上）
        # self.base_model = AutoModelForCausalLM.from_pretrained(
        #     base_model_path,
        #     torch_dtype=torch.bfloat16,  # 极其关键的显存救星
        #     #device_map="auto",           # 自动分配显卡的黑魔法(v1版本LoRA可用，但v2版本DoRA报错)
        #     device_map={"": 0},           # v2/v4版本DoRA应强制绑定到 GPU 0
        #     trust_remote_code=True
        # )

        # #print("正在挂载你的专属 SQL 魂环 (LoRA)...")
        # # 使用 PeftModel.from_pretrained 把 LoRA 权重套在基座模型上
        # self.model = PeftModel.from_pretrained(self.base_model, lora_path)
        ###############################################################
        
        
        print("模型加载完毕")

    def generate_sql(self, question: str, table_schema: str) -> str:
        """
        核心推理逻辑：接收问题和表结构，生成并返回 SQL 字符串。
        """

        # ======= 旧版代码（注释保留不改动） =======
        # # 【核心修复：系统提示词必须与黄金训练集一字不差！】
        # system_prompt = (
        #     "你是一个极其专业的数据库架构师。请仔细阅读以下包含数据类型、主键(PRIMARY KEY)和外键(FOREIGN KEY)的数据库 DDL 结构。\n"
        #     "严格遵循表间关联关系，将用户的自然语言问题转化为极其精确的 SQL 查询语句。不要输出任何解释性文本。\n\n"
        #     f"{table_schema}"
        # )
        # 
        # 
        # # 严格对齐黄金数据集的角色分配：表结构放 System，问题放 User
        # messages = [
        #     {"role": "system", "content": system_prompt},
        #     {"role": "user", "content": question}
        # ]
        # ==========================================

        # ======= v5适配版本代码 =======
        system_prompt = "Generate SQL based on the database schema. Output only SQL."
        
        user_prompt = f"""Database schema:
{table_schema}

Question:
{question}"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        # ===============================

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

        # 解码生成的结果，截取出纯净的 SQL 字符串并返回
        # 计算输入的长度
        input_len = model_inputs["input_ids"].shape[1]
        # 利用 Python 的切片语法，把前面输入的 token 砍掉
        response_ids = generated_ids[0][input_len:]
        
        # 开启 X光透视模式！
        #raw_output = self.tokenizer.decode(generated_ids[0], skip_special_tokens=False)
        #print("\n========== [X光透视：模型大脑内部的真实 Token 流] ==========")
        #print(raw_output)
        #print("==========================================================\n")
        
        # 把纯净的输出 ID 翻译回人类能看懂的 SQL 字符串
        response_sql = self.tokenizer.decode(response_ids, skip_special_tokens=True)

        return response_sql