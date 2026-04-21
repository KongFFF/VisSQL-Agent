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

    def _build_chat_text(self, memory_messages: list, system_prompt: str | None = None) -> str:
        full_messages = [{"role": "system", "content": system_prompt or self.system_prompt}] + memory_messages
        return self.tokenizer.apply_chat_template(
            full_messages,
            tokenize=False,
            add_generation_prompt=True,
        )

    def generate_text(
        self,
        memory_messages: list,
        system_prompt: str | None = None,
        max_new_tokens: int = 64,
        do_sample: bool = False,
        temperature: float = 0.7,
        top_p: float = 0.9,
    ) -> str:
        text = self._build_chat_text(memory_messages, system_prompt=system_prompt)
        model_inputs = self.tokenizer([text], return_tensors="pt").to(self.model.device)

        generation_kwargs = {
            "max_new_tokens": max_new_tokens,
            "do_sample": do_sample,
            "pad_token_id": self.tokenizer.eos_token_id,
            "eos_token_id": self.tokenizer.eos_token_id,
        }
        if do_sample:
            generation_kwargs["temperature"] = temperature
            generation_kwargs["top_p"] = top_p

        with torch.no_grad():
            generated_ids = self.model.generate(
                **model_inputs,
                **generation_kwargs,
            )

        generated_ids = [
            output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
        ]
        return self.tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0].strip()

    def generate_candidates(
        self,
        memory_messages: list,
        num_candidates: int = 5,
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_new_tokens: int = 512,
    ) -> list[str]:
        return self.generate_candidate_bundle(
            memory_messages=memory_messages,
            num_candidates=num_candidates,
            temperature=temperature,
            top_p=top_p,
            max_new_tokens=max_new_tokens,
        )["candidate_sqls"]

    def generate_candidate_bundle(
        self,
        memory_messages: list,
        num_candidates: int = 5,
        temperature: float = 0.7,
        top_p: float = 0.9,
        max_new_tokens: int = 512,
        initial_raw_count: int = 10,
        max_raw_budget: int = 30,
    ) -> dict:
        if num_candidates <= 0:
            return {
                "candidate_sqls": [],
                "candidate_shortage": False,
                "raw_budget_used": 0,
                "raw_budget_limit": max_raw_budget,
                "raw_generation_rounds": 0,
            }

        text = self._build_chat_text(memory_messages)
        model_inputs = self.tokenizer([text], return_tensors="pt").to(self.model.device)
        prompt_length = model_inputs.input_ids.shape[1]

        unique_candidates = []
        seen = set()
        raw_budget_used = 0
        raw_generation_rounds = 0

        while len(unique_candidates) < num_candidates and raw_budget_used < max_raw_budget:
            remaining_budget = max_raw_budget - raw_budget_used
            if raw_generation_rounds == 0:
                raw_sequence_count = min(initial_raw_count, remaining_budget)
            else:
                needed = max(num_candidates - len(unique_candidates), 1)
                raw_sequence_count = min(max(needed * 2, 2), remaining_budget)

            if raw_sequence_count <= 0:
                break

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

            completion_ids = generated_ids[:, prompt_length:]
            raw_responses = self.tokenizer.batch_decode(completion_ids, skip_special_tokens=True)
            raw_budget_used += raw_sequence_count
            raw_generation_rounds += 1

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
            deterministic_sql = self.generate(memory_messages)
            if deterministic_sql:
                unique_candidates.append(deterministic_sql)

        candidate_shortage = len(unique_candidates) < num_candidates
        return {
            "candidate_sqls": unique_candidates[:num_candidates],
            "candidate_shortage": candidate_shortage,
            "raw_budget_used": raw_budget_used,
            "raw_budget_limit": max_raw_budget,
            "raw_generation_rounds": raw_generation_rounds,
        }

    def generate(self, memory_messages: list) -> str:
        raw_response = self.generate_text(
            memory_messages,
            max_new_tokens=512,
            do_sample=False,
        )
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
