import re
from typing import Any


class Selector2:
    """
    Selector 2 scores each candidate with lightweight heuristic signals.

    Score example:
    +3 executable
    +2 non-empty result
    +1 all referenced tables/columns are in schema
    +1 no obvious duplicate / malformed SQL
    -1 missing key structures
    -1 unknown column
    -1 executable but structurally suspicious
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

    AGG_FUNCTION_RE = re.compile(r"\b(count|sum|avg|min|max)\s*\(", re.IGNORECASE)
    IDENTIFIER_RE = re.compile(r"\b([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\b")
    TABLE_ALIAS_RE = re.compile(
        r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w]*)\s*(?:AS\s+)?([A-Za-z_][\w]*)?",
        re.IGNORECASE,
    )
    SELECT_SECTION_RE = re.compile(
        r"\bSELECT\b\s+(.*?)\s+\bFROM\b",
        re.IGNORECASE | re.DOTALL,
    )

    RESERVED_WORDS = {
        "select", "from", "where", "group", "by", "order", "limit", "join",
        "left", "right", "inner", "outer", "on", "as", "and", "or", "not",
        "in", "is", "null", "like", "between", "distinct", "having", "union",
        "intersect", "except", "with", "case", "when", "then", "else", "end",
        "asc", "desc", "count", "sum", "avg", "min", "max", "exists", "all",
        "any", "cast", "true", "false", "limit",
    }

    def __init__(self, sandbox):
        self.sandbox = sandbox

    def select(self, candidate_sqls: list[str], schema_meta: dict | None = None) -> dict[str, Any]:
        evaluated_candidates = []

        for index, sql in enumerate(candidate_sqls):
            execution_result = self.sandbox.execute_query(sql)
            score_result = self._score_candidate(sql, execution_result, schema_meta)
            evaluated_candidates.append(
                {
                    "candidate_index": index,
                    "sql": sql,
                    "execution_result": execution_result,
                    "is_executable": execution_result.get("status") == "success",
                    "is_non_empty": execution_result.get("status") == "success"
                    and execution_result.get("row_count", 0) > 0,
                    "row_count": execution_result.get("row_count", 0)
                    if execution_result.get("status") == "success"
                    else 0,
                    "clause_count": self._count_clauses(sql),
                    "sql_length": len(self._normalize_sql(sql)),
                    "score": score_result["score"],
                    "score_breakdown": score_result["score_breakdown"],
                    "schema_valid": score_result["schema_valid"],
                    "unknown_column_detected": score_result["unknown_column_detected"],
                    "malformed_detected": score_result["malformed_detected"],
                    "missing_key_structure": score_result["missing_key_structure"],
                    "suspicious_structure": score_result["suspicious_structure"],
                }
            )

        if not evaluated_candidates:
            fallback_sql = "SELECT 1"
            fallback_result = self.sandbox.execute_query(fallback_sql)
            fallback_score = self._score_candidate(fallback_sql, fallback_result, schema_meta)
            selected_candidate = {
                "candidate_index": -1,
                "sql": fallback_sql,
                "execution_result": fallback_result,
                "is_executable": fallback_result.get("status") == "success",
                "is_non_empty": fallback_result.get("status") == "success"
                and fallback_result.get("row_count", 0) > 0,
                "row_count": fallback_result.get("row_count", 0)
                if fallback_result.get("status") == "success"
                else 0,
                "clause_count": self._count_clauses(fallback_sql),
                "sql_length": len(self._normalize_sql(fallback_sql)),
                "score": fallback_score["score"],
                "score_breakdown": fallback_score["score_breakdown"],
                "schema_valid": fallback_score["schema_valid"],
                "unknown_column_detected": fallback_score["unknown_column_detected"],
                "malformed_detected": fallback_score["malformed_detected"],
                "missing_key_structure": fallback_score["missing_key_structure"],
                "suspicious_structure": fallback_score["suspicious_structure"],
            }
            evaluated_candidates.append(selected_candidate)
        else:
            selected_candidate = max(evaluated_candidates, key=self._ranking_key)

        return {
            "module": "Selector 2",
            "selection_rule": "score > executable > non_empty > clause_count > sql_length > earlier_candidate",
            "candidate_count": len(evaluated_candidates),
            "selected_candidate_index": selected_candidate["candidate_index"],
            "selected_sql": selected_candidate["sql"],
            "selected_execution_result": selected_candidate["execution_result"],
            "selected_score": selected_candidate["score"],
            "executable_candidate_count": sum(1 for candidate in evaluated_candidates if candidate["is_executable"]),
            "non_empty_candidate_count": sum(1 for candidate in evaluated_candidates if candidate["is_non_empty"]),
            "candidates": evaluated_candidates,
        }

    def _score_candidate(self, sql: str, execution_result: dict[str, Any], schema_meta: dict | None) -> dict[str, Any]:
        score = 0
        score_breakdown = []
        normalized_sql = self._normalize_sql(sql)

        is_executable = execution_result.get("status") == "success"
        is_non_empty = is_executable and execution_result.get("row_count", 0) > 0
        missing_key_structure = self._missing_key_structure(normalized_sql)
        malformed_detected = self._has_obvious_malformed_sql(normalized_sql)
        suspicious_structure = is_executable and self._has_suspicious_structure(normalized_sql)
        schema_validation = self._validate_schema_references(normalized_sql, schema_meta, execution_result)

        if is_executable:
            score += 3
            score_breakdown.append("+3 executable")
        if is_non_empty:
            score += 2
            score_breakdown.append("+2 non_empty")
        if schema_validation["schema_valid"]:
            score += 1
            score_breakdown.append("+1 schema_valid")
        if not malformed_detected:
            score += 1
            score_breakdown.append("+1 not_malformed")
        if missing_key_structure:
            score -= 1
            score_breakdown.append("-1 missing_key_structure")
        if schema_validation["unknown_column_detected"]:
            score -= 1
            score_breakdown.append("-1 unknown_column")
        if suspicious_structure:
            score -= 1
            score_breakdown.append("-1 suspicious_structure")

        return {
            "score": score,
            "score_breakdown": score_breakdown,
            "schema_valid": schema_validation["schema_valid"],
            "unknown_column_detected": schema_validation["unknown_column_detected"],
            "malformed_detected": malformed_detected,
            "missing_key_structure": missing_key_structure,
            "suspicious_structure": suspicious_structure,
        }

    def _ranking_key(self, candidate: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
        return (
            candidate["score"],
            1 if candidate["is_executable"] else 0,
            1 if candidate["is_non_empty"] else 0,
            candidate["clause_count"],
            candidate["sql_length"],
            -candidate["candidate_index"],
        )

    def _validate_schema_references(
        self,
        normalized_sql: str,
        schema_meta: dict | None,
        execution_result: dict[str, Any],
    ) -> dict[str, Any]:
        if not schema_meta:
            error_msg = str(execution_result.get("error_msg", "")).lower()
            unknown_column_detected = "no such column" in error_msg
            return {
                "schema_valid": execution_result.get("status") == "success" and not unknown_column_detected,
                "unknown_column_detected": unknown_column_detected,
            }

        schema_tables = {table_name.lower(): table_name for table_name in schema_meta.get("tables", {})}
        schema_columns_by_table = {}
        global_columns = set()
        for table_name, table_meta in schema_meta.get("tables", {}).items():
            columns = {column["name"].lower() for column in table_meta.get("columns", [])}
            schema_columns_by_table[table_name.lower()] = columns
            global_columns.update(columns)

        alias_map = {}
        unknown_table_detected = False
        unknown_column_detected = False

        for table_name, alias in self.TABLE_ALIAS_RE.findall(normalized_sql):
            lower_table_name = table_name.lower()
            if lower_table_name not in schema_tables:
                unknown_table_detected = True
                continue
            alias_map[lower_table_name] = lower_table_name
            if alias:
                alias_map[alias.lower()] = lower_table_name

        for table_or_alias, column_name in self.IDENTIFIER_RE.findall(normalized_sql):
            lower_table_or_alias = table_or_alias.lower()
            lower_column_name = column_name.lower()

            if lower_table_or_alias in alias_map:
                resolved_table = alias_map[lower_table_or_alias]
            elif lower_table_or_alias in schema_tables:
                resolved_table = lower_table_or_alias
            else:
                if lower_table_or_alias not in self.RESERVED_WORDS:
                    unknown_table_detected = True
                continue

            table_columns = schema_columns_by_table.get(resolved_table, set())
            if lower_column_name not in table_columns:
                unknown_column_detected = True

        if "no such column" in str(execution_result.get("error_msg", "")).lower():
            unknown_column_detected = True

        if not unknown_column_detected:
            excluded_tokens = set(schema_tables) | set(alias_map)
            for bare_column in self._extract_bare_columns(normalized_sql, excluded_tokens):
                if bare_column.lower() not in global_columns:
                    unknown_column_detected = True
                    break

        return {
            "schema_valid": not unknown_table_detected and not unknown_column_detected,
            "unknown_column_detected": unknown_column_detected,
        }

    def _extract_bare_columns(self, normalized_sql: str, excluded_tokens: set[str] | None = None) -> list[str]:
        excluded_tokens = excluded_tokens or set()
        bare_columns = []
        masked_sql = self.IDENTIFIER_RE.sub(" ", normalized_sql)
        for token in re.findall(r"\b([A-Za-z_][\w]*)\b", masked_sql):
            lower_token = token.lower()
            if lower_token in self.RESERVED_WORDS:
                continue
            if lower_token in excluded_tokens:
                continue
            if re.fullmatch(r"t\d+", lower_token):
                continue
            bare_columns.append(token)
        return bare_columns

    def _missing_key_structure(self, normalized_sql: str) -> bool:
        has_select = bool(re.search(r"\bSELECT\b", normalized_sql, flags=re.IGNORECASE))
        has_from = bool(re.search(r"\bFROM\b", normalized_sql, flags=re.IGNORECASE))
        return not (has_select and has_from)

    def _has_obvious_malformed_sql(self, normalized_sql: str) -> bool:
        if normalized_sql.count("(") != normalized_sql.count(")"):
            return True

        duplicate_clause_patterns = [
            r"\bSELECT\s+SELECT\b",
            r"\bFROM\s+FROM\b",
            r"\bWHERE\s+WHERE\b",
            r"\bJOIN\s+JOIN\b",
            r"\bORDER\s+BY\s+ORDER\s+BY\b",
            r"\bGROUP\s+BY\s+GROUP\s+BY\b",
        ]
        return any(re.search(pattern, normalized_sql, flags=re.IGNORECASE) for pattern in duplicate_clause_patterns)

    def _has_suspicious_structure(self, normalized_sql: str) -> bool:
        has_limit = bool(re.search(r"\bLIMIT\b", normalized_sql, flags=re.IGNORECASE))
        has_order_by = bool(re.search(r"\bORDER\s+BY\b", normalized_sql, flags=re.IGNORECASE))
        has_group_by = bool(re.search(r"\bGROUP\s+BY\b", normalized_sql, flags=re.IGNORECASE))

        if has_limit and not has_order_by:
            return True

        select_section_match = self.SELECT_SECTION_RE.search(normalized_sql)
        if not select_section_match:
            return False

        select_section = select_section_match.group(1)
        select_items = [item.strip() for item in select_section.split(",") if item.strip()]
        has_aggregate = any(self.AGG_FUNCTION_RE.search(item) for item in select_items)
        has_plain_column = any(self._looks_like_plain_column(item) for item in select_items)
        if has_aggregate and has_plain_column and not has_group_by:
            return True

        return False

    def _looks_like_plain_column(self, select_item: str) -> bool:
        lower_item = select_item.lower().strip()
        if self.AGG_FUNCTION_RE.search(lower_item):
            return False
        if "(" in lower_item or ")" in lower_item:
            return False
        return bool(re.search(r"[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)?", lower_item))

    def _count_clauses(self, sql: str) -> int:
        normalized_sql = self._normalize_sql(sql)
        return sum(1 for pattern in self.CLAUSE_PATTERNS if re.search(pattern, normalized_sql, flags=re.IGNORECASE))

    def _normalize_sql(self, sql: str) -> str:
        return " ".join(str(sql).replace("\n", " ").replace("\t", " ").split())
