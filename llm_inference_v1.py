import torch
from transformers import AutoTokenizer, AutoModelForCausalLM
from peft import PeftModel

class QwenSQLInference_v1:
    """
    Qwen Text-to-SQL 推理引擎。
    负责加载基座模型与 LoRA 权重，并处理自然语言到 SQL 的生成。
    """
    
    def __init__(self, base_model_path: str, lora_path: str):
        """
        初始化引擎：加载 Tokenizer、基础模型，并挂载 LoRA 适配器。
        """
        print("正在唤醒 Qwen 基座模型并加载 Tokenizer...")
        # 使用 AutoTokenizer 加载分词器
        self.tokenizer = AutoTokenizer.from_pretrained(base_model_path, trust_remote_code=True)
        # trust_remote_code=True 是国内大模型（比如 Qwen、ChatGLM）的标准起手式，允许加载模型自带的一些特殊分词脚本。
        
        # 使用 AutoModelForCausalLM 加载基座模型（注意数据类型推荐用 torch.bfloat16，并加到 cuda 上）
        self.base_model = AutoModelForCausalLM.from_pretrained(
            base_model_path,
            torch_dtype=torch.bfloat16,  # 极其关键的显存救星
            #device_map="auto",           # 自动分配显卡的黑魔法(v1版本LoRA可用，但v2版本DoRA报错)
            device_map={"": 0},           # v2版本DoRA应强制绑定到 GPU 0
            trust_remote_code=True
        )
        
        print("正在挂载你的专属 SQL 魂环 (LoRA)...")
        # 使用 PeftModel.from_pretrained 把 LoRA 权重套在基座模型上
        self.model = PeftModel.from_pretrained(self.base_model, lora_path)

        print("模型加载完毕")

    def generate_sql(self, question: str, table_schema: str) -> str:
        """
        核心推理逻辑：接收问题和表结构，生成并返回 SQL 字符串。
        """

        # 构造 Qwen 专属的 Prompt 模板（把 question 和 table_schema 塞进去）
        messages = [
           {"role": "system", "content": "你是一个优秀的数据库专家。请根据提供的表结构，直接输出标准的 SQLite 查询语句，不要包含任何额外的解释。"},
            {"role": "user", "content": f"【表结构】\n{table_schema}\n\n【问题】\n{question}"}
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

        # 解码生成的结果，截取出纯净的 SQL 字符串并返回
        # 计算输入的长度
        input_len = model_inputs["input_ids"].shape[1]
        # 利用 Python 的切片语法，把前面输入的 token 砍掉
        response_ids = generated_ids[0][input_len:]
        # 把纯净的输出 ID 翻译回人类能看懂的 SQL 字符串
        response_sql = self.tokenizer.decode(response_ids, skip_special_tokens=True)

        return response_sql