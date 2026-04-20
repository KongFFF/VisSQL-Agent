import json
import re
from typing import Any


class Selector3:
    """
    Selector 3 uses the same model as a lightweight judge.
    Input: question, schema, candidate SQLs
    Output: choose the most likely correct candidate index
    """

    SYSTEM_PROMPT = (
        "You are a strict SQL candidate selector. "
        "Given a question, schema, and several candidate SQL queries, "
        "choose the candidate most likely to correctly answer the question. "
        "Do not explain. Output only a single candidate number like 1 or JSON like {\"best\": 1}."
    )

    def __init__(self, coder, sandbox):
        self.coder = coder
        self.sandbox = sandbox

    def select(self, question: str, schema_info: str, candidate_sqls: list[str]) -> dict[str, Any]:
        if not candidate_sqls:
            fallback_sql = "SELECT 1"
            fallback_result = self.sandbox.execute_query(fallback_sql)
            return {
                "module": "Selector 3",
                "selection_rule": "same_model_judge",
                "candidate_count": 1,
                "selected_candidate_index": 0,
                "selected_sql": fallback_sql,
                "selected_execution_result": fallback_result,
                "raw_model_output": "1",
                "parse_status": "fallback_empty_candidates",
                "candidates": [
                    {
                        "candidate_index": 0,
                        "sql": fallback_sql,
                    }
                ],
            }

        prompt = self._build_prompt(question, schema_info, candidate_sqls)
        raw_output = self.coder.generate_text(
            memory_messages=[{"role": "user", "content": prompt}],
            system_prompt=self.SYSTEM_PROMPT,
            max_new_tokens=16,
            do_sample=False,
        )

        selected_index, parse_status = self._parse_choice(raw_output, len(candidate_sqls))
        selected_sql = candidate_sqls[selected_index]
        selected_execution_result = self.sandbox.execute_query(selected_sql)

        return {
            "module": "Selector 3",
            "selection_rule": "same_model_judge",
            "candidate_count": len(candidate_sqls),
            "selected_candidate_index": selected_index,
            "selected_sql": selected_sql,
            "selected_execution_result": selected_execution_result,
            "raw_model_output": raw_output,
            "parse_status": parse_status,
            "candidates": [
                {
                    "candidate_index": index,
                    "sql": sql,
                }
                for index, sql in enumerate(candidate_sqls)
            ],
        }

    def _build_prompt(self, question: str, schema_info: str, candidate_sqls: list[str]) -> str:
        candidate_lines = []
        for index, sql in enumerate(candidate_sqls, start=1):
            candidate_lines.append(f"{index}. {sql}")

        return (
            f"Question:\n{question}\n\n"
            f"Schema:\n{schema_info}\n\n"
            f"Candidate SQLs:\n" + "\n".join(candidate_lines) + "\n\n"
            "Which candidate is most likely correct?\n"
            "Output only one number like 1 or JSON like {\"best\": 1}."
        )

    def _parse_choice(self, raw_output: str, candidate_count: int) -> tuple[int, str]:
        text = (raw_output or "").strip()

        if text:
            try:
                payload = json.loads(text)
                if isinstance(payload, dict) and "best" in payload:
                    best = int(payload["best"])
                    if 1 <= best <= candidate_count:
                        return best - 1, "json"
            except Exception:
                pass

            number_match = re.search(r"\b([1-9]\d*)\b", text)
            if number_match:
                best = int(number_match.group(1))
                if 1 <= best <= candidate_count:
                    return best - 1, "number"

        return 0, "fallback_first_candidate"
