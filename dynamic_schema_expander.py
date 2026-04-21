import re
from itertools import combinations
from typing import Callable

from schema_retriever import (
    build_join_paths,
    build_path_hint_plan,
    build_selected_columns,
    build_selected_column_map,
    build_selected_fk_edges,
    ensure_key_columns_for_selected_tables,
    normalize_identifier_tokens,
    question_tokens,
    render_schema_v6,
    shortest_table_path,
)


METRIC_KEYWORDS = {
    "highest",
    "lowest",
    "maximum",
    "minimum",
    "average",
    "avg",
    "total",
    "sum",
}

METRIC_PHRASES = []

METRIC_COLUMN_HINTS = {
    "age",
    "amount",
    "avg",
    "average",
    "count",
    "duration",
    "height",
    "length",
    "max",
    "maximum",
    "min",
    "minimum",
    "number",
    "population",
    "price",
    "quantity",
    "rate",
    "rank",
    "score",
    "size",
    "sum",
    "total",
    "weight",
    "year",
}

FILTER_CATEGORY_SPECS = {
    "time": {
        "question_keywords": {"year", "date", "before", "after", "during", "between", "latest", "earliest"},
        "column_keywords": {"year", "date", "time", "season", "month", "day"},
        "column_types": {"time"},
    },
    "type": {
        "question_keywords": {"type", "category", "kind", "genre"},
        "column_keywords": {"type", "category", "kind", "genre"},
        "column_types": {"text"},
    },
    "status": {
        "question_keywords": {"status", "active", "inactive", "charter", "funded", "approved", "cancelled"},
        "column_keywords": {"status", "active", "charter", "funded", "approved", "cancelled", "state"},
        "column_types": {"text"},
    },
    "location": {
        "question_keywords": {"location", "country", "city", "state", "province", "continent", "town", "home"},
        "column_keywords": {"location", "country", "city", "state", "province", "continent", "town", "home"},
        "column_types": {"text"},
    },
}


class DynamicSchemaExpander:
    TABLE_REF_RE = re.compile(
        r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w]*)",
        re.IGNORECASE,
    )
    IDENTIFIER_RE = re.compile(
        r"\b([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\b",
        re.IGNORECASE,
    )

    def __init__(
        self,
        draft_sql_generator: Callable[[str, str], str],
        *,
        path_hint_mode: str = "off",
        max_bridge_tables: int = 3,
        max_added_columns: int = 5,
        max_candidate_tables: int = 5,
        candidate_table_score_ratio: float = 0.4,
        min_candidate_table_score: float = 1.0,
    ):
        self.draft_sql_generator = draft_sql_generator
        self.path_hint_mode = path_hint_mode
        self.max_bridge_tables = max_bridge_tables
        self.max_added_columns = max_added_columns
        self.max_candidate_tables = max_candidate_tables
        self.candidate_table_score_ratio = candidate_table_score_ratio
        self.min_candidate_table_score = min_candidate_table_score

    def expand(self, *, question: str, schema_meta: dict, initial_retrieval: dict) -> dict:
        if initial_retrieval.get("applied_mode") == "full":
            result = dict(initial_retrieval)
            result["dynamic_schema_expansion"] = {
                "enabled": True,
                "attempted": False,
                "applied": False,
                "skip_reason": "full_schema",
            }
            return result

        initial_tables = list(initial_retrieval.get("selected_tables", []))
        initial_edges = list(initial_retrieval.get("selected_edges") or initial_retrieval.get("selected_foreign_keys") or [])
        initial_column_map = self._column_descriptors_to_map(initial_retrieval.get("selected_columns") or [])
        if not initial_column_map:
            initial_column_map = ensure_key_columns_for_selected_tables(
                schema_meta=schema_meta,
                selected_tables=initial_tables,
            )
        initial_columns = build_selected_columns(
            schema_meta=schema_meta,
            selected_tables=initial_tables,
            selected_column_map=initial_column_map,
        )

        try:
            draft_sql = self._generate_draft_sql(
                question=question,
                schema_text=initial_retrieval.get("schema_text", ""),
            )
        except Exception as exc:
            result = dict(initial_retrieval)
            result["initial_selected_tables"] = initial_tables
            result["initial_selected_columns"] = initial_columns
            result["initial_selected_edges"] = initial_edges
            result["dynamic_schema_expansion"] = {
                "enabled": True,
                "attempted": True,
                "applied": False,
                "skip_reason": "draft_sql_exception",
                "error": str(exc),
            }
            return result

        draft_analysis = self._analyze_draft_sql(draft_sql)
        gap_signals = self._detect_gap_signals(
            question=question,
            schema_meta=schema_meta,
            initial_retrieval=initial_retrieval,
            initial_tables=initial_tables,
            initial_columns=initial_columns,
            draft_analysis=draft_analysis,
        )

        expansion_result = self._apply_controlled_expansion(
            question=question,
            schema_meta=schema_meta,
            initial_retrieval=initial_retrieval,
            initial_tables=initial_tables,
            initial_column_map=initial_column_map,
            gap_signals=gap_signals,
        )

        final_tables = expansion_result["selected_tables"]
        final_column_map = ensure_key_columns_for_selected_tables(
            schema_meta=schema_meta,
            selected_tables=final_tables,
            selected_column_map=expansion_result["selected_column_map"],
        )
        final_seed_tables = expansion_result["focus_tables"]
        final_join_paths = build_join_paths(schema_meta, final_seed_tables)
        final_edges = build_selected_fk_edges(schema_meta, final_tables, final_seed_tables)
        path_hint_plan = build_path_hint_plan(
            question=question,
            selected_tables=final_tables,
            seed_tables=final_seed_tables,
            all_join_paths=final_join_paths,
            all_selected_fk_edges=final_edges,
            path_hint_mode=self.path_hint_mode,
            schema_meta=schema_meta,
        )
        final_schema_text = render_schema_v6(
            schema_meta=schema_meta,
            selected_tables=final_tables,
            seed_tables=final_seed_tables,
            path_hint_plan=path_hint_plan,
            selected_column_map=final_column_map,
        )

        result = dict(initial_retrieval)
        result["schema_text"] = final_schema_text
        result["selected_tables"] = final_tables
        result["selected_columns"] = build_selected_columns(
            schema_meta=schema_meta,
            selected_tables=final_tables,
            selected_column_map=final_column_map,
        )
        result["selected_foreign_keys"] = final_edges
        result["selected_edges"] = final_edges
        result["join_paths"] = [" -> ".join(path) for path in final_join_paths]
        result["path_hint_requested_mode"] = path_hint_plan["requested_mode"]
        result["path_hint_applied_mode"] = path_hint_plan["applied_mode"]
        result["path_hints_enabled"] = path_hint_plan["enabled"]
        result["path_hint_trigger_reasons"] = path_hint_plan["trigger_reasons"]
        result["path_hint_focus_tables"] = path_hint_plan["focus_tables"]
        result["path_hint_foreign_keys"] = path_hint_plan["foreign_keys"]
        result["path_hint_join_paths"] = path_hint_plan["join_paths"]
        result["path_hint_primary_join_path"] = path_hint_plan["primary_join_path"]
        result["initial_selected_tables"] = initial_tables
        result["initial_selected_columns"] = initial_columns
        result["initial_selected_edges"] = initial_edges
        result["dynamic_schema_expansion"] = {
            "enabled": True,
            "attempted": True,
            "applied": expansion_result["applied"],
            "skip_reason": None if expansion_result["applied"] else "no_gap_signal",
            "draft_sql": draft_sql,
            "draft_sql_analysis": draft_analysis,
            "gap_signals": gap_signals,
            "table_budget": self.max_bridge_tables,
            "column_budget": self.max_added_columns,
            "added_tables": expansion_result["added_tables"],
            "bridge_added_tables": expansion_result["bridge_added_tables"],
            "metric_added_columns": expansion_result["metric_added_columns"],
            "filter_added_columns": expansion_result["filter_added_columns"],
            "focus_tables": final_seed_tables,
        }
        return result

    def _generate_draft_sql(self, *, question: str, schema_text: str) -> str:
        return str(self.draft_sql_generator(schema_text, question) or "").strip()

    def _analyze_draft_sql(self, draft_sql: str) -> dict:
        normalized_sql = " ".join(str(draft_sql or "").split())
        tables = []
        seen_tables = set()
        for table_name in self.TABLE_REF_RE.findall(normalized_sql):
            lower_name = table_name.lower()
            if lower_name in seen_tables:
                continue
            seen_tables.add(lower_name)
            tables.append(table_name)

        columns = []
        seen_columns = set()
        for table_name, column_name in self.IDENTIFIER_RE.findall(normalized_sql):
            full_name = f"{table_name}.{column_name}"
            lower_name = full_name.lower()
            if lower_name in seen_columns:
                continue
            seen_columns.add(lower_name)
            columns.append(full_name)

        lower_sql = normalized_sql.lower()
        return {
            "sql": normalized_sql,
            "tables": tables,
            "columns": columns,
            "has_where": " where " in f" {lower_sql} ",
            "has_group_by": " group by " in f" {lower_sql} ",
            "has_order_by": " order by " in f" {lower_sql} ",
            "has_limit": " limit " in f" {lower_sql} ",
            "has_agg": bool(re.search(r"\b(count|sum|avg|min|max)\s*\(", lower_sql)),
        }

    def _detect_gap_signals(
        self,
        *,
        question: str,
        schema_meta: dict,
        initial_retrieval: dict,
        initial_tables: list,
        initial_columns: list,
        draft_analysis: dict,
    ) -> dict:
        bridge_signals = self._detect_bridge_gap(
            question=question,
            schema_meta=schema_meta,
            initial_retrieval=initial_retrieval,
            initial_tables=initial_tables,
            draft_tables=draft_analysis["tables"],
        )
        metric_signal = self._detect_metric_gap(
            question=question,
            schema_meta=schema_meta,
            initial_retrieval=initial_retrieval,
            initial_columns=initial_columns,
            draft_analysis=draft_analysis,
        )
        filter_signal = self._detect_filter_gap(
            question=question,
            schema_meta=schema_meta,
            initial_retrieval=initial_retrieval,
            initial_columns=initial_columns,
            draft_analysis=draft_analysis,
        )
        return {
            "bridge": bridge_signals,
            "metric": metric_signal,
            "filter": filter_signal,
        }

    def _detect_bridge_gap(
        self,
        *,
        question: str,
        schema_meta: dict,
        initial_retrieval: dict,
        initial_tables: list,
        draft_tables: list,
    ) -> list[dict]:
        table_score_map = {
            item["table"]: float(item.get("score", 0.0) or 0.0)
            for item in initial_retrieval.get("table_scores", [])
        }
        candidate_tables = self._collect_candidate_tables(initial_retrieval)
        seed_tables = list(initial_retrieval.get("seed_tables", []))

        anchor_tables = []
        for table_name in seed_tables + draft_tables + candidate_tables:
            if table_name not in schema_meta.get("tables", {}):
                continue
            if table_name not in anchor_tables:
                anchor_tables.append(table_name)

        bridge_signals = []
        seen_paths = set()
        for left_table, right_table in combinations(anchor_tables, 2):
            path = shortest_table_path(schema_meta, left_table, right_table)
            if len(path) < 2:
                continue

            path_key = tuple(path)
            if path_key in seen_paths:
                continue
            seen_paths.add(path_key)

            missing_tables = [table for table in path if table not in initial_tables]
            if not missing_tables:
                continue

            if (
                left_table not in initial_tables
                and right_table not in initial_tables
                and left_table not in draft_tables
                and right_table not in draft_tables
            ):
                continue

            bridge_signals.append(
                {
                    "type": "bridge_table_missing",
                    "left_table": left_table,
                    "right_table": right_table,
                    "path": path,
                    "missing_tables": missing_tables,
                    "score": round(
                        table_score_map.get(left_table, 0.0) + table_score_map.get(right_table, 0.0),
                        3,
                    ),
                    "reason": "path_between_relevant_tables_not_fully_covered",
                }
            )

        bridge_signals.sort(
            key=lambda item: (-item["score"], len(item["missing_tables"]), " -> ".join(item["path"])),
        )
        return bridge_signals

    def _detect_metric_gap(
        self,
        *,
        question: str,
        schema_meta: dict,
        initial_retrieval: dict,
        initial_columns: list,
        draft_analysis: dict,
    ) -> dict:
        question_lower = str(question or "").lower()
        raw_question_tokens = set(normalize_identifier_tokens(question))
        metric_support_columns = [
            column
            for column in initial_columns
            if self._looks_like_metric_column(column)
            and (set(normalize_identifier_tokens(column.get("column", ""))) & raw_question_tokens)
        ]
        keyword_hits = sorted({
            token for token in raw_question_tokens
            if token in METRIC_KEYWORDS
        })
        phrase_hits = [phrase for phrase in METRIC_PHRASES if phrase in question_lower]
        if not keyword_hits and not phrase_hits:
            return {
                "triggered": False,
                "reason": "no_metric_signal",
                "keyword_hits": [],
                "candidate_columns": [],
        }

        if metric_support_columns:
            return {
                "triggered": False,
                "reason": "metric_columns_already_present",
                "keyword_hits": keyword_hits + phrase_hits,
                "candidate_columns": [],
            }

        has_metric_column = any(self._looks_like_metric_column(column) for column in initial_columns)
        ranking_signal = any(
            hit in question_lower
            for hit in ["most", "least", "highest", "lowest", "top", "maximum", "minimum"]
        )
        draft_suspicious = (
            (ranking_signal and not draft_analysis["has_order_by"])
            or (("average" in question_lower or "avg" in question_lower or "total" in question_lower) and not draft_analysis["has_agg"])
            or (ranking_signal and len(draft_analysis["tables"]) <= 1 and not draft_analysis["has_group_by"])
        )

        if has_metric_column and not draft_suspicious:
            return {
                "triggered": False,
                "reason": "metric_columns_already_present",
                "keyword_hits": keyword_hits + phrase_hits,
                "candidate_columns": [],
            }

        selected_tables = set(initial_retrieval.get("selected_tables", []))
        selected_full_names = {column["full_name"] for column in initial_columns}
        candidate_columns = self._rank_candidate_columns(
            question=question,
            schema_meta=schema_meta,
            allowed_categories={"metric"},
            selected_tables=selected_tables,
            excluded_full_names=selected_full_names,
            initial_retrieval=initial_retrieval,
        )[:3]
        return {
            "triggered": bool(candidate_columns),
            "reason": "metric_signal_without_good_metric_support" if candidate_columns else "no_metric_candidate_found",
            "keyword_hits": keyword_hits + phrase_hits,
            "draft_suspicious": draft_suspicious,
            "candidate_columns": candidate_columns,
        }

    def _detect_filter_gap(
        self,
        *,
        question: str,
        schema_meta: dict,
        initial_retrieval: dict,
        initial_columns: list,
        draft_analysis: dict,
    ) -> dict:
        question_token_set = set(normalize_identifier_tokens(question))
        active_categories = []
        for category_name, spec in FILTER_CATEGORY_SPECS.items():
            hits = sorted(question_token_set & spec["question_keywords"])
            if hits:
                active_categories.append({"category": category_name, "hits": hits})

        if not active_categories:
            return {
                "triggered": False,
                "reason": "no_filter_signal",
                "categories": [],
                "candidate_columns": [],
            }

        has_matching_filter_column = False
        for category in active_categories:
            spec = FILTER_CATEGORY_SPECS[category["category"]]
            if any(self._column_matches_filter_spec(column, spec) for column in initial_columns):
                has_matching_filter_column = True
                break

        if has_matching_filter_column:
            return {
                "triggered": False,
                "reason": "filter_columns_already_present",
                "categories": active_categories,
                "candidate_columns": [],
            }

        selected_tables = set(initial_retrieval.get("selected_tables", []))
        selected_full_names = {column["full_name"] for column in initial_columns}
        allowed_categories = {category["category"] for category in active_categories}
        candidate_columns = self._rank_candidate_columns(
            question=question,
            schema_meta=schema_meta,
            allowed_categories=allowed_categories,
            selected_tables=selected_tables,
            excluded_full_names=selected_full_names,
            initial_retrieval=initial_retrieval,
        )[:2]
        return {
            "triggered": bool(candidate_columns),
            "reason": "filter_signal_without_good_filter_support" if candidate_columns else "no_filter_candidate_found",
            "categories": active_categories,
            "draft_missing_where": not draft_analysis["has_where"],
            "candidate_columns": candidate_columns,
        }

    def _apply_controlled_expansion(
        self,
        *,
        question: str,
        schema_meta: dict,
        initial_retrieval: dict,
        initial_tables: list,
        initial_column_map: dict,
        gap_signals: dict,
    ) -> dict:
        working_tables = list(initial_tables)
        working_column_map = build_selected_column_map(
            schema_meta=schema_meta,
            selected_tables=initial_tables,
            selected_column_map=initial_column_map,
        )
        focus_tables = list(initial_retrieval.get("seed_tables", []))
        added_tables = []
        bridge_added_tables = []
        metric_added_columns = []
        filter_added_columns = []
        added_column_budget = 0

        for bridge_signal in gap_signals.get("bridge", []):
            for table_name in bridge_signal.get("missing_tables", []):
                if table_name in working_tables:
                    continue
                if len(added_tables) >= self.max_bridge_tables:
                    break
                working_tables.append(table_name)
                added_tables.append(table_name)
                bridge_added_tables.append(table_name)
                if table_name not in focus_tables:
                    focus_tables.append(table_name)
                working_column_map[table_name] = self._minimal_bridge_columns(
                    schema_meta=schema_meta,
                    table_name=table_name,
                    path=bridge_signal.get("path", []),
                    question=question,
                )
            if len(added_tables) >= self.max_bridge_tables:
                break

        for candidate in gap_signals.get("metric", {}).get("candidate_columns", []):
            if added_column_budget >= self.max_added_columns:
                break
            did_add_table, did_add_column = self._apply_column_candidate(
                candidate=candidate,
                schema_meta=schema_meta,
                working_tables=working_tables,
                working_column_map=working_column_map,
                added_tables=added_tables,
            )
            if did_add_table and candidate["table"] not in focus_tables:
                focus_tables.append(candidate["table"])
            if did_add_column:
                metric_added_columns.append(candidate["full_name"])
                added_column_budget += 1
            if len(added_tables) >= self.max_bridge_tables and added_column_budget >= self.max_added_columns:
                break

        for candidate in gap_signals.get("filter", {}).get("candidate_columns", []):
            if added_column_budget >= self.max_added_columns:
                break
            did_add_table, did_add_column = self._apply_column_candidate(
                candidate=candidate,
                schema_meta=schema_meta,
                working_tables=working_tables,
                working_column_map=working_column_map,
                added_tables=added_tables,
            )
            if did_add_table and candidate["table"] not in focus_tables:
                focus_tables.append(candidate["table"])
            if did_add_column:
                filter_added_columns.append(candidate["full_name"])
                added_column_budget += 1
            if len(added_tables) >= self.max_bridge_tables and added_column_budget >= self.max_added_columns:
                break

        if not focus_tables:
            focus_tables = list(initial_retrieval.get("seed_tables", []))

        return {
            "applied": bool(added_tables or metric_added_columns or filter_added_columns),
            "selected_tables": working_tables,
            "selected_column_map": working_column_map,
            "focus_tables": focus_tables,
            "added_tables": added_tables,
            "bridge_added_tables": bridge_added_tables,
            "metric_added_columns": metric_added_columns,
            "filter_added_columns": filter_added_columns,
        }

    def _rank_candidate_columns(
        self,
        *,
        question: str,
        schema_meta: dict,
        allowed_categories: set[str],
        selected_tables: set[str],
        excluded_full_names: set[str],
        initial_retrieval: dict,
    ) -> list[dict]:
        question_token_set = set(question_tokens(question))
        table_score_map = {
            item["table"]: float(item.get("score", 0.0) or 0.0)
            for item in initial_retrieval.get("table_scores", [])
        }

        candidates = []
        for table_name in schema_meta.get("table_order", []):
            for column in schema_meta["tables"][table_name]["columns"]:
                full_name = f"{table_name}.{column['name']}"
                if full_name in excluded_full_names:
                    continue

                score = 0.0
                categories = set()
                column_token_set = set(column["tokens"])
                score += len(question_token_set & column_token_set) * 2.0
                score += table_score_map.get(table_name, 0.0) * 0.5

                if "metric" in allowed_categories and self._looks_like_metric_column(
                    {
                        "table": table_name,
                        "column": column["name"],
                        "full_name": full_name,
                        "column_type": column.get("column_type", ""),
                    }
                ):
                    score += 2.5
                    categories.add("metric")

                for category_name in allowed_categories:
                    if category_name == "metric":
                        continue
                    spec = FILTER_CATEGORY_SPECS.get(category_name)
                    if spec and self._raw_column_matches_filter_spec(column, spec):
                        score += 2.0
                        categories.add(category_name)

                if not categories:
                    continue

                if table_name in selected_tables:
                    score += 0.5
                else:
                    shortest_distance = self._distance_to_selected_table(
                        schema_meta=schema_meta,
                        table_name=table_name,
                        selected_tables=selected_tables,
                    )
                    if shortest_distance == 1:
                        score += 1.0
                    elif shortest_distance == 2:
                        score += 0.5

                candidates.append(
                    {
                        "table": table_name,
                        "column": column["name"],
                        "full_name": full_name,
                        "column_type": column.get("column_type", ""),
                        "categories": sorted(categories),
                        "score": round(score, 3),
                    }
                )

        candidates.sort(
            key=lambda item: (
                -item["score"],
                item["table"] in selected_tables,
                item["table"],
                item["column"],
            ),
        )
        return candidates

    def _apply_column_candidate(
        self,
        *,
        candidate: dict,
        schema_meta: dict,
        working_tables: list,
        working_column_map: dict,
        added_tables: list,
    ) -> tuple[bool, bool]:
        table_name = candidate["table"]
        column_name = candidate["column"]
        did_add_table = False
        did_add_column = False

        if table_name not in working_tables:
            if len(added_tables) >= self.max_bridge_tables:
                return False, False
            working_tables.append(table_name)
            added_tables.append(table_name)
            working_column_map[table_name] = self._default_entity_columns(schema_meta, table_name)
            did_add_table = True

        allowed_columns = working_column_map.setdefault(table_name, set())
        if column_name not in allowed_columns:
            allowed_columns.add(column_name)
            did_add_column = True

        return did_add_table, did_add_column

    def _minimal_bridge_columns(self, *, schema_meta: dict, table_name: str, path: list, question: str) -> set[str]:
        selected_columns = set()
        path_pairs = list(zip(path, path[1:]))
        for left_table, right_table in path_pairs:
            for edge in schema_meta.get("foreign_keys", []):
                if {edge["source_table"], edge["target_table"]} != {left_table, right_table}:
                    continue
                if edge["source_table"] == table_name:
                    selected_columns.add(edge["source_column"])
                if edge["target_table"] == table_name:
                    selected_columns.add(edge["target_column"])

        selected_columns.update(self._default_entity_columns(schema_meta, table_name))
        for column in schema_meta["tables"][table_name]["columns"]:
            if column["is_primary_key"] or column["foreign_key"]:
                selected_columns.add(column["name"])
        if not selected_columns:
            selected_columns = {
                column["name"]
                for column in schema_meta["tables"][table_name]["columns"][:3]
            }
        return selected_columns

    def _default_entity_columns(self, schema_meta: dict, table_name: str) -> set[str]:
        preferred_tokens = {"name", "title", "type", "country", "city", "year", "date"}
        selected = set()
        for column in schema_meta["tables"][table_name]["columns"]:
            if set(column["tokens"]) & preferred_tokens:
                selected.add(column["name"])
        return selected

    def _collect_candidate_tables(self, initial_retrieval: dict) -> list[str]:
        table_scores = list(initial_retrieval.get("table_scores", []))
        if not table_scores:
            return []
        max_score = max(float(item.get("score", 0.0) or 0.0) for item in table_scores)
        score_floor = max(self.min_candidate_table_score, max_score * self.candidate_table_score_ratio)
        candidate_tables = [
            item["table"]
            for item in table_scores
            if float(item.get("score", 0.0) or 0.0) >= score_floor
        ]
        return candidate_tables[: self.max_candidate_tables]

    def _column_descriptors_to_map(self, columns: list[dict]) -> dict[str, set[str]]:
        column_map: dict[str, set[str]] = {}
        for column in columns:
            table_name = column.get("table")
            column_name = column.get("column")
            if not table_name or not column_name:
                continue
            column_map.setdefault(table_name, set()).add(column_name)
        return column_map

    def _looks_like_metric_column(self, column: dict) -> bool:
        column_type = str(column.get("column_type", "") or "").lower()
        column_tokens = set(normalize_identifier_tokens(column.get("column", "")))
        if column_type in {"number", "time"}:
            return True
        if column_tokens & METRIC_COLUMN_HINTS:
            return True
        return False

    def _column_matches_filter_spec(self, column: dict, spec: dict) -> bool:
        column_type = str(column.get("column_type", "") or "").lower()
        column_tokens = set(normalize_identifier_tokens(column.get("column", "")))
        if column_type in spec["column_types"] and spec["column_types"]:
            return True
        if column_tokens & spec["column_keywords"]:
            return True
        return False

    def _raw_column_matches_filter_spec(self, column: dict, spec: dict) -> bool:
        column_type = str(column.get("column_type", "") or "").lower()
        if column_type in spec["column_types"] and spec["column_types"]:
            return True
        if set(column.get("tokens", set())) & spec["column_keywords"]:
            return True
        return False

    def _distance_to_selected_table(self, *, schema_meta: dict, table_name: str, selected_tables: set[str]) -> int | None:
        shortest_distance = None
        for selected_table in selected_tables:
            path = shortest_table_path(schema_meta, table_name, selected_table)
            if not path:
                continue
            distance = len(path) - 1
            if shortest_distance is None or distance < shortest_distance:
                shortest_distance = distance
        return shortest_distance
