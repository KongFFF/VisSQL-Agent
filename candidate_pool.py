import hashlib
import json
from pathlib import Path
from typing import Any, Callable


def build_memory_state_hash(memory_messages: list[dict[str, Any]]) -> str:
    payload = json.dumps(memory_messages, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class CandidatePoolManager:
    def __init__(self, path: str | Path, mode: str = "disabled"):
        self.path = Path(path)
        self.mode = mode
        self.records: dict[tuple[int, int, str], dict[str, Any]] = {}
        self._load_existing_records()

    def _load_existing_records(self) -> None:
        if not self.path.exists():
            return

        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                record = json.loads(line)
                key = (
                    int(record["question_index"]),
                    int(record["attempt"]),
                    str(record["memory_state_hash"]),
                )
                self.records[key] = record

    def resolve_candidates(
        self,
        *,
        question_index: int,
        db_id: str,
        question: str,
        attempt: int,
        schema_info: str,
        memory_messages: list[dict[str, Any]],
        generator_fn: Callable[[], dict[str, Any]],
        generation_config: dict[str, Any],
    ) -> tuple[list[str], dict[str, Any]]:
        memory_state_hash = build_memory_state_hash(memory_messages)
        key = (int(question_index), int(attempt), memory_state_hash)

        if key in self.records:
            record = self.records[key]
            return list(record.get("candidate_sqls", [])), {
                "source": "loaded",
                "record": record,
            }

        if self.mode in {"disabled", "load"}:
            if self.mode == "load":
                raise KeyError(
                    f"Candidate pool missing for question_index={question_index}, attempt={attempt}, "
                    f"memory_state_hash={memory_state_hash}"
                )
            generated_bundle = generator_fn()
            return list(generated_bundle.get("candidate_sqls", [])), {
                "source": "generated_no_cache",
                "record": generated_bundle,
            }

        if self.mode == "generate_or_load":
            generated_bundle = generator_fn()
            record = {
                "question_index": int(question_index),
                "db_id": db_id,
                "question": question,
                "attempt": int(attempt),
                "memory_state_hash": memory_state_hash,
                "memory_message_count": len(memory_messages),
                "schema_hash": hashlib.sha256(schema_info.encode("utf-8")).hexdigest(),
                "generation_config": generation_config,
                "candidate_sqls": generated_bundle.get("candidate_sqls", []),
                "candidate_shortage": generated_bundle.get("candidate_shortage", False),
                "raw_budget_used": generated_bundle.get("raw_budget_used"),
                "raw_budget_limit": generated_bundle.get("raw_budget_limit"),
                "raw_generation_rounds": generated_bundle.get("raw_generation_rounds"),
            }
            self._append_record(record)
            self.records[key] = record
            return list(record["candidate_sqls"]), {
                "source": "generated_and_saved",
                "record": record,
            }

        generated_bundle = generator_fn()
        record = {
            "question_index": int(question_index),
            "db_id": db_id,
            "question": question,
            "attempt": int(attempt),
            "memory_state_hash": memory_state_hash,
            "memory_message_count": len(memory_messages),
            "schema_hash": hashlib.sha256(schema_info.encode("utf-8")).hexdigest(),
            "generation_config": generation_config,
            "candidate_sqls": generated_bundle.get("candidate_sqls", []),
            "candidate_shortage": generated_bundle.get("candidate_shortage", False),
            "raw_budget_used": generated_bundle.get("raw_budget_used"),
            "raw_budget_limit": generated_bundle.get("raw_budget_limit"),
            "raw_generation_rounds": generated_bundle.get("raw_generation_rounds"),
        }
        self._append_record(record)
        self.records[key] = record
        return list(record["candidate_sqls"]), {
            "source": "generated_and_saved",
            "record": record,
        }

    def _append_record(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
