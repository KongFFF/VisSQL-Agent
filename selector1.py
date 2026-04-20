import re
from typing import Any


class Selector1:
    """
    Selector 1:
    1. Evaluate K candidate SQL queries.
    2. Prefer executable candidates.
    3. If multiple are executable, prefer non-empty results.
    4. If still tied, prefer structurally richer SQL.
    """

    CLAUSE_PATTERNS = [
        r"\bSELECT\b",
        r"\bFROM\b",
        r"\bWHERE\b",
        r"\bGROUP\s+BY\b",
        r"\bHAVING\b",
        r"\bORDER\s+BY\b",
        r"\bLIMIT\b",
        r"\bJOIN\b",
        r"\bUNION\b",
        r"\bINTERSECT\b",
        r"\bEXCEPT\b",
        r"\bWITH\b",
    ]

    def __init__(self, sandbox):
        self.sandbox = sandbox

    def select(self, candidate_sqls: list[str]) -> dict[str, Any]:
        evaluated_candidates = []

        for index, sql in enumerate(candidate_sqls):
            execution_result = self.sandbox.execute_query(sql)
            clause_count = self._count_clauses(sql)
            sql_length = len(self._normalize_sql(sql))
            row_count = execution_result.get("row_count", 0) if execution_result.get("status") == "success" else 0

            evaluated_candidates.append(
                {
                    "candidate_index": index,
                    "sql": sql,
                    "execution_result": execution_result,
                    "is_executable": execution_result.get("status") == "success",
                    "is_non_empty": execution_result.get("status") == "success" and row_count > 0,
                    "row_count": row_count,
                    "clause_count": clause_count,
                    "sql_length": sql_length,
                }
            )

        if not evaluated_candidates:
            fallback_sql = "SELECT 1"
            fallback_result = self.sandbox.execute_query(fallback_sql)
            selected_candidate = {
                "candidate_index": -1,
                "sql": fallback_sql,
                "execution_result": fallback_result,
                "is_executable": fallback_result.get("status") == "success",
                "is_non_empty": fallback_result.get("status") == "success" and fallback_result.get("row_count", 0) > 0,
                "row_count": fallback_result.get("row_count", 0),
                "clause_count": self._count_clauses(fallback_sql),
                "sql_length": len(self._normalize_sql(fallback_sql)),
            }
            evaluated_candidates.append(selected_candidate)
        else:
            selected_candidate = max(evaluated_candidates, key=self._ranking_key)

        return {
            "module": "Selector 1",
            "selection_rule": "executable > non_empty > clause_count > sql_length > earlier_candidate",
            "candidate_count": len(evaluated_candidates),
            "selected_candidate_index": selected_candidate["candidate_index"],
            "selected_sql": selected_candidate["sql"],
            "selected_execution_result": selected_candidate["execution_result"],
            "executable_candidate_count": sum(1 for candidate in evaluated_candidates if candidate["is_executable"]),
            "non_empty_candidate_count": sum(1 for candidate in evaluated_candidates if candidate["is_non_empty"]),
            "candidates": evaluated_candidates,
        }

    def _ranking_key(self, candidate: dict[str, Any]) -> tuple[int, int, int, int, int]:
        return (
            1 if candidate["is_executable"] else 0,
            1 if candidate["is_non_empty"] else 0,
            candidate["clause_count"],
            candidate["sql_length"],
            -candidate["candidate_index"],
        )

    def _count_clauses(self, sql: str) -> int:
        normalized_sql = self._normalize_sql(sql)
        return sum(1 for pattern in self.CLAUSE_PATTERNS if re.search(pattern, normalized_sql, flags=re.IGNORECASE))

    def _normalize_sql(self, sql: str) -> str:
        return " ".join(str(sql).replace("\n", " ").replace("\t", " ").split())
