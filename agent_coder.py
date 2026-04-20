import re

import torch
from peft import PeftModel
from transformers import AutoModelForCausalLM, AutoTokenizer


class CoderNode:
    def __init__(self, base_model_path: str, lora_path: str):
        print(f">>> Loading base model: {base_model_path} ...")
        self.tokenizer = AutoTokenizer.from_pretrained(base_model_path, trust_remote_code=True)

        base_model = AutoModelForCausalLM.from_pretrained(
            base_model_path,
            device_map="auto",
            torch_dtype=torch.bfloat16,
            trust_remote_code=True,
        )

        if lora_path:
            print(f">>> Loading LoRA weights: {lora_path} ...")
            self.model = PeftModel.from_pretrained(base_model, lora_path).eval()
        else:
            print(">>> No LoRA path provided, using the base model directly.")
            self.model = base_model.eval()

        self.system_prompt = (
            "You are an expert database architect and SQL specialist. "
            "Given the database schema and the user question, produce a precise SQL query. "
            "Output SQL only, without explanation."
        )

    def _extract_sql(self, text: str) -> str:
        pattern = r"```(?:sql)?\s*(.*?)\s*```"
        match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return text.strip()

    def _build_chat_text(self, memory_messages: list) -> str:
        full_messages = [{"role": "system", "content": self.system_prompt}] + memory_messages
        return self.tokenizer.apply_chat_template(
            full_messages,
            tokenize=False,
            add_generation_prompt=True,
        )

    def generate_candidates(
        self,
        memory_messages: list,
        num_candidates: int = 5,
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_new_tokens: int = 512,
    ) -> list[str]:
        if num_candidates <= 0:
            return []

        text = self._build_chat_text(memory_messages)
        model_inputs = self.tokenizer([text], return_tensors="pt").to(self.model.device)

        # Oversample a bit, then deduplicate to increase candidate diversity.
        raw_sequence_count = min(max(num_candidates * 2, num_candidates), 10)

        with torch.no_grad():
            generated_ids = self.model.generate(
                **model_inputs,
                max_new_tokens=max_new_tokens,
                do_sample=True,
                temperature=temperature,
                top_p=top_p,
                num_return_sequences=raw_sequence_count,
                pad_token_id=self.tokenizer.eos_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )

        prompt_length = model_inputs.input_ids.shape[1]
        completion_ids = generated_ids[:, prompt_length:]
        raw_responses = self.tokenizer.batch_decode(completion_ids, skip_special_tokens=True)

        unique_candidates = []
        seen = set()
        for raw_response in raw_responses:
            clean_sql = self._extract_sql(raw_response)
            normalized_sql = " ".join(clean_sql.split()).lower()
            if not clean_sql or normalized_sql in seen:
                continue
            seen.add(normalized_sql)
            unique_candidates.append(clean_sql)
            if len(unique_candidates) >= num_candidates:
                break

        if not unique_candidates:
            return [self.generate(memory_messages)]

        if len(unique_candidates) < num_candidates:
            deterministic_sql = self.generate(memory_messages)
            normalized_sql = " ".join(deterministic_sql.split()).lower()
            if deterministic_sql and normalized_sql not in seen:
                unique_candidates.append(deterministic_sql)

        return unique_candidates[:num_candidates]

    def generate(self, memory_messages: list) -> str:
        text = self._build_chat_text(memory_messages)
        model_inputs = self.tokenizer([text], return_tensors="pt").to(self.model.device)

        with torch.no_grad():
            generated_ids = self.model.generate(
                **model_inputs,
                max_new_tokens=512,
                do_sample=False,
                pad_token_id=self.tokenizer.eos_token_id,
                eos_token_id=self.tokenizer.eos_token_id,
            )

        generated_ids = [
            output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
        ]
        raw_response = self.tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]
        return self._extract_sql(raw_response)


if __name__ == "__main__":
    model_dir = "/root/autodl-tmp/qwen2.5-coder-7b-instruct"
    lora_dir = "/root/autodl-tmp/LLaMA-Factory/saves/Qwen2.5-7B/lora/qwen_spider_lora_v6"

    try:
        coder = CoderNode(model_dir, lora_dir)
        working_memory = [
            {
                "role": "user",
                "content": (
                    "Question: Find all employee names living in New York.\n"
                    "Schema:\n"
                    "- employee (emp_id [PK], name, city_id [FK->city.city_id])\n"
                    "- city (city_id [PK], city_name)"
                ),
            }
        ]
        print(">>> Deterministic SQL:")
        print(coder.generate(working_memory))
        print("\n>>> Sampled candidates:")
        for i, sql in enumerate(coder.generate_candidates(working_memory, num_candidates=3), 1):
            print(f"[{i}] {sql}")
    except Exception as e:
        print(f"Model load or inference failed: {e}")
