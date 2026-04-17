import re
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from peft import PeftModel

class CoderNode:
    def __init__(self, base_model_path: str, lora_path: str):
        """
        初始化大模型推理引擎 (动态挂载模式)
        """
        print(f">>> 🧠 正在唤醒基座模型: {base_model_path} ...")
        self.tokenizer = AutoTokenizer.from_pretrained(base_model_path, trust_remote_code=True)
        
        # 1. 先把笨重的“游戏本体”加载进显存
        base_model = AutoModelForCausalLM.from_pretrained(
            base_model_path,
            device_map="auto",
            torch_dtype=torch.bfloat16,
            trust_remote_code=True
        )
        # 2. 核心魔法：给基座模型穿上 V6 的“外骨骼装甲”
        print(f">>> 🛡️ 正在挂载 V6 LoRA 权重: {lora_path} ...")
        self.model = PeftModel.from_pretrained(base_model, lora_path).eval()
        
        # 极其强硬的 System Prompt，确立 Agent 的身份和输出规范
        self.system_prompt = (
            "你是一个极其严谨的顶级 SQL 架构师。你的任务是根据提供的数据库结构和自然语言问题，"
            "编写出极其准确、可以直接在 SQLite 中执行的 SQL 语句。\n"
            "【严格指令】：\n"
            "1. 你只能输出 SQL 代码，绝对不要输出任何自然语言解释、客套话或前置/后置说明。\n"
            "2. 如果遇到执行报错反馈，请深刻反思并修正之前的 SQL。\n"
            "3. 只输出最终修正后的 SQL，不要保留旧版本的代码。"
        )

    def _extract_sql(self, text: str) -> str:
        """
        防御性编程：从模型的啰嗦输出中，极其精准地扒出 SQL 代码块。
        """
        # 尝试匹配 ```sql ... ``` 或者 ``` ... ```
        pattern = r"sql)?\s*(.*?)\s*```"
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        # 如果模型很乖没有加代码块，直接返回清理两端空格的原文
        return text.strip()

    def generate(self, memory_messages: list) -> str:
        """
        核心推理节点：接受结构化记忆，返回思考后的 SQL。
        :param memory_messages: 纯净的 JSON 对话列表，例如 [{"role": "user", "content": "..."}]
        """
        # 1. 注入系统级护栏 (System Prompt)
        # 我们在每次对话的最开头，强行塞入 System 设定
        full_messages = [{"role": "system", "content": self.system_prompt}] + memory_messages

        # 2. 核心魔法：使用 apply_chat_template 自动组装 ChatML 格式！
        # 底层框架会根据 Qwen 的专属格式，全自动帮你加上 <|im_start|> 等特殊 Token。
        text = self.tokenizer.apply_chat_template(
            full_messages,
            tokenize=False,
            add_generation_prompt=True # 告诉模型：现在轮到 assistant 说话了
        )
        
        model_inputs = self.tokenizer([text], return_tensors="pt").to(self.model.device)

        # 3. 生成配置 (彻底抹杀随机性)
        # 因为我们写的是严谨的 SQL Agent，不是写诗，所以关掉 temperature (等效于 do_sample=False)
        with torch.no_grad():
            generated_ids = self.model.generate(
                **model_inputs,
                max_new_tokens=512,
                do_sample=False,
                eos_token_id=self.tokenizer.eos_token_id
            )

        # 4. 截取并解码大模型最新生成的回复
        generated_ids = [
            output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
        ]
        raw_response = self.tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]

        # 5. 清洗并返回纯净的 SQL
        clean_sql = self._extract_sql(raw_response)
        return clean_sql

# ==========================================
# 独立测试入口 (模拟 Agent 第一次提问和第二次反思)
# ==========================================
if __name__ == "__main__":
    # 请替换为你真实的 V6 模型路径
    model_dir = "/root/autodl-tmp/qwen2.5-coder-7b-instruct" 
    lora_dir = "/root/autodl-tmp/LLaMA-Factory/saves/Qwen2.5-7B/lora/qwen_spider_lora_v6"
    
    try:
        coder = CoderNode(model_dir,lora_dir)
        
        # --- 场景 1：第一次请求 ---
        print("\n>>> 场景 1：初始查询")
        # 这就是我们在 Agent 架构里的 Working Memory（工作记忆）！
        working_memory = [
            {
                "role": "user",
                "content": """问题：找出所有住在纽约的员工姓名。
数据库结构：
- 表 employee (emp_id [PK], name, city_id [FK->city.city_id])
- 表 city (city_id [PK], city_name)"""
            }
        ]
        sql_v1 = coder.generate(working_memory)
        print(f"🧠 大脑输出 V1:\n{sql_v1}\n")
        
        # --- 场景 2：沙盒报错，触发反思 (Reflexion) ---
        print(">>> 场景 2：沙盒报错，触发反思重写")
        # 我们把大模型写的错代码，以及沙盒的报错，原封不动地追加到记忆流里
        working_memory.append({"role": "assistant", "content": sql_v1})
        working_memory.append({
            "role": "user", 
            "content": "执行报错：no such column: city.name。请查阅表结构，找出正确的列名并重写 SQL。"
        })
        
        sql_v2 = coder.generate(working_memory)
        print(f"🧠 大脑反思后的输出 V2:\n{sql_v2}\n")
        
    except Exception as e:
        print(f"未找到模型或加载失败，此代码仅作架构演示。报错信息：{e}")