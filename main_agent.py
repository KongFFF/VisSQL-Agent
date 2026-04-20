from agent_coder import CoderNode
from agent_executor import SQLSandbox
from agent_memory import WorkingMemory
from selector1 import Selector1
from selector2 import Selector2


class VisSQLAgent:
    def __init__(
        self,
        base_model_path: str,
        lora_path: str,
        db_path: str,
        max_retries: int = 3,
        retry_on_empty_result: bool = False,
        selector1_k: int = 5,
        selector1_temperature: float = 0.7,
        selector1_top_p: float = 0.9,
        selector_mode: str = "selector1",
    ):
        print("\n" + "=" * 50)
        print("VisSQL-Agent starting up...")
        print("=" * 50)

        self.coder = CoderNode(base_model_path=base_model_path, lora_path=lora_path)
        self.sandbox = SQLSandbox(db_path=db_path)
        self.selector1 = Selector1(self.sandbox)
        self.selector2 = Selector2(self.sandbox)

        self.max_retries = max_retries
        self.retry_on_empty_result = retry_on_empty_result
        self.selector1_k = selector1_k
        self.selector1_temperature = selector1_temperature
        self.selector1_top_p = selector1_top_p
        self.selector_mode = selector_mode

        print("System initialized.")

    def run_query(
        self,
        schema_info: str,
        user_question: str,
        db_path: str = None,
        verbose: bool = True,
        schema_meta: dict | None = None,
    ):
        def log(message: str):
            if verbose:
                print(message)

        if db_path:
            self.sandbox.set_db_path(db_path)

        log(f"User question: {user_question}")

        memory = WorkingMemory()
        memory.add_initial_query(schema_info, user_question)
        attempt_records = []
        probe_logs = []

        for attempt in range(1, self.max_retries + 1):
            log(f"\n[Attempt {attempt}/{self.max_retries}]")

            current_messages = memory.get_current_messages()
            candidate_sqls = self.coder.generate_candidates(
                current_messages,
                num_candidates=self.selector1_k,
                temperature=self.selector1_temperature,
                top_p=self.selector1_top_p,
            )

            active_selector_key = self.selector_mode if self.selector_mode in {"selector1", "selector2"} else "selector1"
            selector1_result = None
            selector2_result = None

            if active_selector_key == "selector1":
                selector1_result = self.selector1.select(candidate_sqls)
                active_selector_result = selector1_result
            else:
                selector2_result = self.selector2.select(candidate_sqls, schema_meta=schema_meta)
                active_selector_result = selector2_result

            generated_sql = active_selector_result["selected_sql"]
            result = active_selector_result["selected_execution_result"]

            if active_selector_key == "selector1":
                log("Selector 1 candidates:")
                for candidate in active_selector_result["candidates"]:
                    log(
                        f"  - #{candidate['candidate_index']} executable={candidate['is_executable']} "
                        f"non_empty={candidate['is_non_empty']} clauses={candidate['clause_count']} "
                        f"len={candidate['sql_length']}"
                    )
                    log(f"    {candidate['sql']}")
            else:
                log("Selector 2 candidates:")
                for candidate in active_selector_result["candidates"]:
                    log(
                        f"  - #{candidate['candidate_index']} score={candidate['score']} "
                        f"executable={candidate['is_executable']} non_empty={candidate['is_non_empty']} "
                        f"schema_valid={candidate['schema_valid']} suspicious={candidate['suspicious_structure']}"
                    )
                    log(f"    breakdown={candidate['score_breakdown']}")
                    log(f"    {candidate['sql']}")

            log(f"Active selector: {active_selector_key}")
            log(f"Selected SQL:\n{generated_sql}")

            memory.add_assistant_sql(generated_sql)

            attempt_record = {
                "attempt": attempt,
                "candidate_sqls": candidate_sqls,
                "selector1": selector1_result,
                "selector2": selector2_result,
                "active_selector": active_selector_key,
                "active_selector_result": active_selector_result,
                "generated_sql": generated_sql,
                "execution_result": result,
            }

            if result["status"] == "success":
                log(f"Execution succeeded with {result['row_count']} rows.")
                log(f"Sample rows: {result['results'][:2]}")

                if result["row_count"] > 0 or not self.retry_on_empty_result:
                    attempt_records.append(attempt_record)
                    return {
                        "final_sql": generated_sql,
                        "is_success": True,
                        "attempts": attempt,
                        "data": result,
                        "memory_messages": memory.snapshot(),
                        "attempt_records": attempt_records,
                        "probe_logs": probe_logs,
                        "had_probe": bool(probe_logs),
                        "db_path": self.sandbox.db_path,
                        "selector1_config": {
                            "k": self.selector1_k,
                            "temperature": self.selector1_temperature,
                            "top_p": self.selector1_top_p,
                        },
                        "selector_mode": active_selector_key,
                    }

                if attempt < self.max_retries:
                    log("Execution returned 0 rows. Triggering empty-result reflexion.")
                    diagnostics = self.sandbox.run_diagnostic_probes(
                        generated_sql,
                        scenario="empty_result",
                    )
                    probe_logs.append(
                        {
                            "attempt": attempt,
                            "diagnostics": diagnostics,
                        }
                    )
                    attempt_record["diagnostics"] = diagnostics

                    memory.add_execution_feedback(
                        "EmptyResultError",
                        "SQL executed successfully but returned 0 rows.\n"
                        f"{diagnostics['summary']}",
                    )
                    attempt_record["feedback_type"] = "EmptyResultError"
                else:
                    attempt_records.append(attempt_record)
                    return {
                        "final_sql": generated_sql,
                        "is_success": False,
                        "attempts": attempt,
                        "error": "SQL executed successfully but still returned 0 rows after all retries.",
                        "data": result,
                        "memory_messages": memory.snapshot(),
                        "attempt_records": attempt_records,
                        "probe_logs": probe_logs,
                        "had_probe": bool(probe_logs),
                        "db_path": self.sandbox.db_path,
                        "selector1_config": {
                            "k": self.selector1_k,
                            "temperature": self.selector1_temperature,
                            "top_p": self.selector1_top_p,
                        },
                        "selector_mode": active_selector_key,
                    }

            elif result["status"] == "error":
                log(f"Execution failed: {result['error_type']} - {result['error_msg']}")

                if attempt < self.max_retries:
                    memory.add_execution_feedback(result["error_type"], result["error_msg"])
                    attempt_record["feedback_type"] = result["error_type"]
                else:
                    attempt_records.append(attempt_record)
                    return {
                        "final_sql": generated_sql,
                        "is_success": False,
                        "attempts": attempt,
                        "error": result["error_msg"],
                        "memory_messages": memory.snapshot(),
                        "attempt_records": attempt_records,
                        "probe_logs": probe_logs,
                        "had_probe": bool(probe_logs),
                        "db_path": self.sandbox.db_path,
                        "selector1_config": {
                            "k": self.selector1_k,
                            "temperature": self.selector1_temperature,
                            "top_p": self.selector1_top_p,
                        },
                        "selector_mode": active_selector_key,
                    }

            attempt_records.append(attempt_record)


if __name__ == "__main__":
    BASE_MODEL = "/root/autodl-tmp/qwen2.5-coder-7b-instruct"
    LORA_PATH = "/root/autodl-tmp/LLaMA-Factory/saves/Qwen2.5-7B/lora/qwen_spider_lora_v6"
    TEST_DB = "data/testsuitedatabases/database/concert_singer/concert_singer.sqlite"

    agent = VisSQLAgent(
        base_model_path=BASE_MODEL,
        lora_path=LORA_PATH,
        db_path=TEST_DB,
        max_retries=3,
        retry_on_empty_result=True,
    )

    test_schema = """
    Table stadium (Stadium_ID [PK], Location, Name, Capacity, Highest, Lowest, Average)
    Table singer (Singer_ID [PK], Name, Country, Song_Name, Song_release_year, Age, Is_male)
    Table concert (concert_ID [PK], concert_Name, Theme, Stadium_ID [FK->stadium.Stadium_ID], Year)
    Table singer_in_concert (concert_ID [FK->concert.concert_ID], Singer_ID [FK->singer.Singer_ID])
    """
    test_question = "List all female singers and their song names."
    agent.run_query(schema_info=test_schema, user_question=test_question)
