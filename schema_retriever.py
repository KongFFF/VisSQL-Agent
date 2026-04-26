import difflib
import json
import re
import sqlite3
from collections import Counter, deque
from pathlib import Path


STOPWORDS = {
    "a",
    "all",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "did",
    "do",
    "does",
    "each",
    "find",
    "for",
    "from",
    "get",
    "give",
    "have",
    "having",
    "how",
    "in",
    "is",
    "it",
    "its",
    "list",
    "many",
    "me",
    "most",
    "name",
    "names",
    "number",
    "of",
    "on",
    "or",
    "people",
    "person",
    "record",
    "records",
    "row",
    "rows",
    "show",
    "that",
    "the",
    "their",
    "them",
    "there",
    "these",
    "those",
    "to",
    "what",
    "which",
    "who",
    "with",
}

SELECTIVE_PATH_HINT_KEYWORDS = {
    "across",
    "both",
    "each",
    "except",
    "not",
    "without",
}

VALUE_HINT_COLUMN_KEYWORDS = {
    "name",
    "title",
    "type",
    "category",
    "status",
    "country",
    "continent",
    "region",
    "city",
    "state",
    "province",
    "language",
    "code",
    "airport",
    "airline",
    "maker",
    "model",
    "official",
    "form",
    "gender",
    "sex",
}

VALUE_HINT_EXACT_COLUMN_SCORES = {
    "name": 4.5,
    "title": 4.0,
    "type": 4.0,
    "country": 5.0,
    "continent": 5.0,
    "region": 5.5,
    "city": 4.5,
    "state": 4.5,
    "language": 5.0,
    "code": 2.5,
    "airportcode": 5.0,
    "airportname": 4.5,
    "airline": 4.5,
    "abbreviation": 3.5,
    "fullname": 4.5,
    "maker": 4.0,
    "model": 4.0,
    "governmentform": 2.5,
    "isofficial": 5.0,
}

VALUE_HINT_MEASURE_KEYWORDS = {
    "id",
    "year",
    "age",
    "count",
    "number",
    "amount",
    "total",
    "sum",
    "avg",
    "average",
    "min",
    "max",
    "population",
    "price",
    "cost",
    "weight",
    "height",
    "capacity",
    "score",
    "rank",
    "time",
    "date",
    "duration",
    "percentage",
    "percent",
}

AGGREGATION_HINT_KEYWORDS = {
    "avg",
    "average",
    "count",
    "many",
    "max",
    "maximum",
    "min",
    "minimum",
    "number",
    "sum",
    "total",
}

ENTITY_FRIENDLY_COLUMN_NAMES = {
    "name",
    "title",
    "type",
    "category",
    "status",
    "country",
    "continent",
    "region",
    "city",
    "state",
    "province",
    "language",
    "airportname",
    "airportcode",
    "airline",
    "maker",
    "model",
    "fullname",
}

GENERIC_KEYLIKE_COLUMN_NAMES = {
    "id",
    "uid",
    "code",
    "number",
    "no",
}

AMBIGUOUS_COLUMN_PENALTY_NAMES = {
    "name",
    "title",
    "type",
    "model",
    "maker",
    "population",
    "city",
    "country",
    "state",
    "language",
    "region",
    "continent",
}

QUESTION_ENTITY_STOPWORDS = {
    "How",
    "What",
    "Which",
    "Who",
    "Where",
    "When",
    "Why",
    "Give",
    "Show",
    "List",
    "Find",
    "Return",
    "Count",
    "Tell",
    "Airport",
    "Airports",
    "Airline",
    "Airlines",
    "Country",
    "Countries",
    "City",
    "Cities",
    "State",
    "Language",
    "Languages",
}


def normalize_identifier_tokens(text: str) -> list:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    raw_tokens = re.findall(r"[a-z0-9]+", normalized.lower())
    tokens = []

    for token in raw_tokens:
        if token.endswith("ies") and len(token) > 4:
            tokens.append(token[:-3] + "y")
        elif token.endswith("s") and len(token) > 3:
            tokens.append(token[:-1])
        else:
            tokens.append(token)

    return tokens


def question_tokens(question: str) -> list:
    return [token for token in normalize_identifier_tokens(question) if token not in STOPWORDS]


def preview_literal(value, max_len: int = 48) -> str:
    text = str(value).strip()
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def extract_question_entities(question: str) -> list:
    entities = []
    seen = set()

    def add_entity(value: str):
        value = value.strip().strip("`\"'")
        if not value:
            return
        key = value.lower()
        if key in seen:
            return
        seen.add(key)
        entities.append(value)

    for value in re.findall(r'["\'`]{1}([^"\'`]+)["\'`]{1}', question):
        add_entity(value)

    for value in re.findall(r"\b[A-Z]{2,5}\b", question):
        add_entity(value)

    for match in re.finditer(r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b", question):
        value = match.group(0)
        if value in QUESTION_ENTITY_STOPWORDS:
            continue
        add_entity(value)

    return entities[:8]


class SQLiteValueSketcher:
    def __init__(
        self,
        enabled: bool = True,
        max_candidate_columns: int = 10,
        max_columns_per_table: int = 4,
        max_samples_per_column: int = 5,
        max_entity_matches: int = 10,
    ):
        self.enabled = enabled
        self.max_candidate_columns = max_candidate_columns
        self.max_columns_per_table = max_columns_per_table
        self.max_samples_per_column = max_samples_per_column
        self.max_entity_matches = max_entity_matches
        self._table_info_cache = {}
        self._distinct_value_cache = {}

    def build_plan(
        self,
        question: str,
        schema_meta: dict,
        selected_tables: list,
        seed_tables: list,
        db_path: str | None,
    ) -> dict:
        default_plan = {
            "enabled": False,
            "question_entities": [],
            "entity_matches": [],
            "sampled_values": [],
            "candidate_columns": [],
        }
        if not self.enabled or not db_path or not selected_tables:
            return default_plan

        question_entity_values = extract_question_entities(question)
        candidate_columns = self._rank_candidate_columns(
            question=question,
            schema_meta=schema_meta,
            selected_tables=selected_tables,
            seed_tables=seed_tables,
            db_path=db_path,
        )
        entity_matches = self._collect_entity_matches(
            db_path=db_path,
            question_entities=question_entity_values,
            candidate_columns=candidate_columns,
        )
        sampled_values = self._collect_sampled_values(
            db_path=db_path,
            candidate_columns=candidate_columns,
            matched_columns={
                (item["table"], item["column"])
                for item in entity_matches
            },
        )

        return {
            "enabled": bool(entity_matches or sampled_values),
            "question_entities": question_entity_values,
            "entity_matches": entity_matches,
            "sampled_values": sampled_values,
            "candidate_columns": [
                {
                    "table": item["table"],
                    "column": item["column"],
                    "score": round(item["score"], 3),
                }
                for item in candidate_columns
            ],
        }

    def _connect(self, db_path: str):
        db_uri = f"file:{Path(db_path).resolve().as_posix()}?mode=ro"
        return sqlite3.connect(db_uri, uri=True)

    def _get_table_columns(self, db_path: str, table_name: str) -> dict:
        cache_key = (db_path, table_name)
        cached = self._table_info_cache.get(cache_key)
        if cached is not None:
            return cached

        columns = {}
        conn = None
        try:
            conn = self._connect(db_path)
            cursor = conn.cursor()
            cursor.execute(f"PRAGMA table_info({quote_identifier(table_name)})")
            for _, name, declared_type, _, _, _ in cursor.fetchall():
                type_text = (declared_type or "").upper()
                is_text_like = any(token in type_text for token in ("CHAR", "TEXT", "CLOB"))
                is_numeric_like = any(token in type_text for token in ("INT", "REAL", "FLOA", "DOUB", "NUM"))
                columns[name] = {
                    "declared_type": declared_type or "",
                    "is_text_like": is_text_like or not type_text,
                    "is_numeric_like": is_numeric_like,
                }
        except sqlite3.Error:
            columns = {}
        finally:
            try:
                conn.close()
            except Exception:
                pass

        self._table_info_cache[cache_key] = columns
        return columns

    def _rank_candidate_columns(
        self,
        question: str,
        schema_meta: dict,
        selected_tables: list,
        seed_tables: list,
        db_path: str,
    ) -> list:
        token_set = set(question_tokens(question))
        question_entities = extract_question_entities(question)
        has_code_like_entity = any(re.fullmatch(r"[A-Z]{2,5}", value) for value in question_entities)
        seed_table_set = set(seed_tables)
        ranked = []

        for table_name in selected_tables:
            table_columns = self._get_table_columns(db_path, table_name)
            table_ranked = []

            for column in schema_meta["tables"][table_name]["columns"]:
                column_name = column["name"]
                column_tokens = column["tokens"]
                normalized_column_name = column_name.lower()
                info = table_columns.get(
                    column_name,
                    {
                        "declared_type": "",
                        "is_text_like": True,
                        "is_numeric_like": False,
                    },
                )

                overlap = len(token_set & column_tokens)
                semantic_bonus = 0.0
                semantic_bonus += VALUE_HINT_EXACT_COLUMN_SCORES.get(normalized_column_name, 0.0)
                if any(keyword in normalized_column_name for keyword in VALUE_HINT_COLUMN_KEYWORDS):
                    semantic_bonus += 2.0
                if has_code_like_entity and "code" in normalized_column_name:
                    semantic_bonus += 3.0
                if normalized_column_name.startswith("is_") or normalized_column_name.startswith("has_"):
                    semantic_bonus += 2.0

                score = overlap * 5.0 + semantic_bonus
                if info["is_text_like"]:
                    score += 1.0
                if table_name in seed_table_set:
                    score += 0.75

                looks_like_measure = (
                    not info["is_text_like"]
                    and any(keyword in normalized_column_name for keyword in VALUE_HINT_MEASURE_KEYWORDS)
                )
                if looks_like_measure and overlap == 0 and semantic_bonus == 0:
                    continue
                if score <= 0:
                    continue

                table_ranked.append(
                    {
                        "table": table_name,
                        "column": column_name,
                        "score": score,
                        "is_text_like": info["is_text_like"],
                        "normalized_column_name": normalized_column_name,
                    }
                )

            table_ranked.sort(key=lambda item: (-item["score"], item["column"].lower()))
            ranked.extend(table_ranked[: self.max_columns_per_table])

        ranked.sort(key=lambda item: (-item["score"], item["table"].lower(), item["column"].lower()))
        return ranked[: self.max_candidate_columns]

    def _collect_entity_matches(
        self,
        db_path: str,
        question_entities: list,
        candidate_columns: list,
    ) -> list:
        matches = []

        for entity in question_entities:
            exact_matches = []
            fuzzy_matches = []

            for column_info in candidate_columns:
                if not column_info["is_text_like"]:
                    continue

                matched_values, match_type = self._find_matching_values(
                    db_path=db_path,
                    table_name=column_info["table"],
                    column_name=column_info["column"],
                    entity=entity,
                )
                if not matched_values:
                    continue

                record = {
                    "question_value": entity,
                    "table": column_info["table"],
                    "column": column_info["column"],
                    "match_type": match_type,
                    "values": matched_values,
                }
                if match_type == "exact":
                    exact_matches.append(record)
                else:
                    fuzzy_matches.append(record)

            entity_records = exact_matches or fuzzy_matches
            matches.extend(entity_records[:2])
            if len(matches) >= self.max_entity_matches:
                break

        return matches[: self.max_entity_matches]

    def _find_matching_values(
        self,
        db_path: str,
        table_name: str,
        column_name: str,
        entity: str,
    ) -> tuple[list, str | None]:
        column_expr = f"CAST({quote_identifier(column_name)} AS TEXT)"
        base_sql = (
            f"SELECT DISTINCT {column_expr} "
            f"FROM {quote_identifier(table_name)} "
            f"WHERE {column_expr} IS NOT NULL"
        )

        conn = None
        try:
            conn = self._connect(db_path)
            cursor = conn.cursor()
            cursor.execute(
                base_sql + f" AND lower({column_expr}) = lower(?) LIMIT ?",
                (entity, self.max_samples_per_column),
            )
            exact_rows = [preview_literal(row[0]) for row in cursor.fetchall() if row and row[0] is not None]
            if exact_rows:
                return exact_rows, "exact"

            if len(entity) >= 4 and not re.fullmatch(r"[A-Z]{2,5}", entity):
                cursor.execute(
                    base_sql + f" AND {column_expr} LIKE ? COLLATE NOCASE LIMIT ?",
                    (f"%{entity}%", self.max_samples_per_column),
                )
                fuzzy_rows = [preview_literal(row[0]) for row in cursor.fetchall() if row and row[0] is not None]
                if fuzzy_rows:
                    return fuzzy_rows, "fuzzy"

                preview_values = self._get_distinct_values(
                    db_path=db_path,
                    table_name=table_name,
                    column_name=column_name,
                    limit=max(25, self.max_samples_per_column),
                )
                approx_rows = difflib.get_close_matches(
                    entity,
                    preview_values,
                    n=self.max_samples_per_column,
                    cutoff=0.72,
                )
                if approx_rows:
                    return approx_rows, "approx"
        except sqlite3.Error:
            return [], None
        finally:
            try:
                conn.close()
            except Exception:
                pass

        return [], None

    def _collect_sampled_values(
        self,
        db_path: str,
        candidate_columns: list,
        matched_columns: set,
    ) -> list:
        sampled = []

        for column_info in candidate_columns:
            if not column_info["is_text_like"] and (column_info["table"], column_info["column"]) not in matched_columns:
                continue

            values = self._get_distinct_values(
                db_path=db_path,
                table_name=column_info["table"],
                column_name=column_info["column"],
            )
            if not values:
                continue

            sampled.append(
                {
                    "table": column_info["table"],
                    "column": column_info["column"],
                    "values": values,
                }
            )
            if len(sampled) >= self.max_candidate_columns:
                break

        return sampled

    def _get_distinct_values(
        self,
        db_path: str,
        table_name: str,
        column_name: str,
        limit: int | None = None,
    ) -> list:
        limit = limit or self.max_samples_per_column
        cache_key = (db_path, table_name, column_name, limit)
        cached = self._distinct_value_cache.get(cache_key)
        if cached is not None:
            return cached

        values = []
        column_expr = f"CAST({quote_identifier(column_name)} AS TEXT)"
        sql = (
            f"SELECT DISTINCT {column_expr} "
            f"FROM {quote_identifier(table_name)} "
            f"WHERE {column_expr} IS NOT NULL "
            f"AND TRIM({column_expr}) != '' "
            f"LIMIT ?"
        )

        conn = None
        try:
            conn = self._connect(db_path)
            cursor = conn.cursor()
            cursor.execute(sql, (limit,))
            values = [preview_literal(row[0]) for row in cursor.fetchall() if row and row[0] is not None]
        except sqlite3.Error:
            values = []
        finally:
            try:
                conn.close()
            except Exception:
                pass

        self._distinct_value_cache[cache_key] = values
        return values


def build_schema_metadata_dict(tables_path: Path) -> dict:
    with tables_path.open("r", encoding="utf-8") as f:
        tables_data = json.load(f)

    db_schemas = {}

    for db in tables_data:
        db_id = db["db_id"]
        table_names = db["table_names_original"]
        column_names = db["column_names_original"]
        primary_keys = set(db["primary_keys"])
        foreign_keys = db["foreign_keys"]

        tables = {}
        table_order = []

        for table_name in table_names:
            table_order.append(table_name)
            tables[table_name] = {
                "name": table_name,
                "tokens": set(normalize_identifier_tokens(table_name)),
                "columns": [],
                "neighbors": set(),
            }

        for col_idx, (table_idx, col_name) in enumerate(column_names):
            if table_idx == -1:
                continue

            table_name = table_names[table_idx]
            tables[table_name]["columns"].append(
                {
                    "index": col_idx,
                    "name": col_name,
                    "tokens": set(normalize_identifier_tokens(col_name)),
                    "is_primary_key": col_idx in primary_keys,
                    "foreign_key": None,
                }
            )

        index_to_table = {}
        index_to_column = {}
        for table_name, table in tables.items():
            for column in table["columns"]:
                index_to_table[column["index"]] = table_name
                index_to_column[column["index"]] = column

        fk_edges = []
        for src_idx, tgt_idx in foreign_keys:
            src_table = index_to_table[src_idx]
            tgt_table = index_to_table[tgt_idx]
            src_column = index_to_column[src_idx]
            tgt_column = index_to_column[tgt_idx]

            src_column["foreign_key"] = {
                "target_table": tgt_table,
                "target_column": tgt_column["name"],
            }

            tables[src_table]["neighbors"].add(tgt_table)
            tables[tgt_table]["neighbors"].add(src_table)
            fk_edges.append(
                {
                    "source_table": src_table,
                    "source_column": src_column["name"],
                    "target_table": tgt_table,
                    "target_column": tgt_column["name"],
                }
            )

        db_schemas[db_id] = {
            "db_id": db_id,
            "table_order": table_order,
            "tables": tables,
            "foreign_keys": fk_edges,
        }

    return db_schemas


def shortest_table_path(schema_meta: dict, start: str, goal: str) -> list:
    if start == goal:
        return [start]

    visited = {start}
    queue = deque([(start, [start])])

    while queue:
        table_name, path = queue.popleft()
        if len(path) > 4:
            continue

        for neighbor in schema_meta["tables"][table_name]["neighbors"]:
            if neighbor in visited:
                continue

            next_path = path + [neighbor]
            if neighbor == goal:
                return next_path

            visited.add(neighbor)
            queue.append((neighbor, next_path))

    return []


def build_join_paths(schema_meta: dict, seed_tables: list) -> list:
    if len(seed_tables) < 2:
        return []

    paths = []
    seen = set()

    for idx, left in enumerate(seed_tables):
        for right in seed_tables[idx + 1:]:
            path = shortest_table_path(schema_meta, left, right)
            if len(path) < 2:
                continue

            path_key = tuple(path)
            if path_key in seen:
                continue

            seen.add(path_key)
            paths.append(path)

    return paths


def build_selected_fk_edges(schema_meta: dict, selected_tables: list, seed_tables: list | None = None) -> list:
    selected_set = set(selected_tables)
    seed_set = set(seed_tables or [])
    join_paths = build_join_paths(schema_meta, seed_tables or [])

    path_nodes = set()
    for path in join_paths:
        path_nodes.update(path)

    edges = []
    for edge in schema_meta["foreign_keys"]:
        source = edge["source_table"]
        target = edge["target_table"]
        if source not in selected_set or target not in selected_set:
            continue

        if not path_nodes and not seed_set:
            edges.append(edge)
            continue

        if path_nodes:
            if source in path_nodes and target in path_nodes:
                edges.append(edge)
            continue

        if source in seed_set or target in seed_set:
            edges.append(edge)

    return edges


def build_path_edges(schema_meta: dict, path: list) -> list:
    if len(path) < 2:
        return []

    edges = []
    for left, right in zip(path, path[1:]):
        for edge in schema_meta["foreign_keys"]:
            source = edge["source_table"]
            target = edge["target_table"]
            if {source, target} == {left, right}:
                edges.append(edge)
    return edges


def choose_primary_join_path(join_paths: list) -> list:
    if not join_paths:
        return []

    ranked_paths = sorted(
        join_paths,
        key=lambda path: (-len(path), " -> ".join(path)),
    )
    return ranked_paths[0]


def build_path_hint_plan(
    question: str,
    selected_tables: list,
    seed_tables: list,
    all_join_paths: list,
    all_selected_fk_edges: list,
    path_hint_mode: str,
    schema_meta: dict,
) -> dict:
    default_plan = {
        "requested_mode": path_hint_mode,
        "applied_mode": "off",
        "enabled": False,
        "trigger_reasons": [],
        "focus_tables": seed_tables,
        "foreign_keys": [],
        "join_paths": [],
        "primary_join_path": [],
    }

    if path_hint_mode == "off":
        return default_plan

    if path_hint_mode == "all":
        return {
            "requested_mode": path_hint_mode,
            "applied_mode": "all",
            "enabled": True,
            "trigger_reasons": ["explicit_all"],
            "focus_tables": seed_tables,
            "foreign_keys": all_selected_fk_edges,
            "join_paths": [" -> ".join(path) for path in all_join_paths],
            "primary_join_path": choose_primary_join_path(all_join_paths),
        }

    if path_hint_mode != "selective":
        raise ValueError(f"Unsupported path hint mode: {path_hint_mode}")

    trigger_reasons = []
    if len(seed_tables) >= 2:
        trigger_reasons.append("multi_seed")
    if len(selected_tables) >= 4:
        trigger_reasons.append("large_subgraph")
    if any(len(path) >= 3 for path in all_join_paths):
        trigger_reasons.append("multi_hop_path")

    question_token_set = set(question_tokens(question))
    keyword_hits = sorted(question_token_set & SELECTIVE_PATH_HINT_KEYWORDS)
    if keyword_hits:
        trigger_reasons.append(f"keywords:{','.join(keyword_hits)}")

    if "multi_seed" not in trigger_reasons:
        return default_plan

    if len(trigger_reasons) == 1:
        return default_plan

    primary_join_path = choose_primary_join_path(all_join_paths)
    primary_edges = build_path_edges(schema_meta, primary_join_path)
    if not primary_join_path or not primary_edges:
        return default_plan

    return {
        "requested_mode": path_hint_mode,
        "applied_mode": "selective",
        "enabled": True,
        "trigger_reasons": trigger_reasons,
        "focus_tables": seed_tables,
        "foreign_keys": primary_edges,
        "join_paths": [" -> ".join(primary_join_path)],
        "primary_join_path": primary_join_path,
    }


def render_schema_v6(
    schema_meta: dict,
    selected_tables: list | None = None,
    seed_tables: list | None = None,
    path_hint_plan: dict | None = None,
    column_hint_plan: dict | None = None,
    value_hint_plan: dict | None = None,
) -> str:
    if selected_tables is None:
        selected_tables = list(schema_meta["table_order"])
    if seed_tables is None:
        seed_tables = []
    if path_hint_plan is None:
        path_hint_plan = {
            "enabled": False,
            "focus_tables": seed_tables,
            "foreign_keys": [],
            "join_paths": [],
        }
    if column_hint_plan is None:
        column_hint_plan = {
            "enabled": False,
            "columns": [],
        }
    if value_hint_plan is None:
        value_hint_plan = {
            "enabled": False,
            "question_entities": [],
            "entity_matches": [],
            "sampled_values": [],
            "candidate_columns": [],
        }

    selected_table_set = set(selected_tables)
    schema_lines = [f"【数据库结构】\n数据库名称：{schema_meta['db_id']}"]

    for table_name in schema_meta["table_order"]:
        if table_name not in selected_table_set:
            continue

        schema_lines.append(f"- 表：{table_name}")
        column_descriptions = []

        for column in schema_meta["tables"][table_name]["columns"]:
            constraints = []
            if column["is_primary_key"]:
                constraints.append("主键")

            if column["foreign_key"]:
                target = column["foreign_key"]
                constraints.append(
                    f"外键指向 {target['target_table']}.{target['target_column']}"
                )

            if constraints:
                column_descriptions.append(f"{column['name']} ({'，'.join(constraints)})")
            else:
                column_descriptions.append(column["name"])

        schema_lines.append(f"  字段：{', '.join(column_descriptions)}")

    if path_hint_plan.get("enabled"):
        focus_tables = path_hint_plan.get("focus_tables") or seed_tables
        if focus_tables:
            schema_lines.append("【优先关注表】")
            schema_lines.append(f"- {', '.join(focus_tables)}")

        fk_edges = path_hint_plan.get("foreign_keys", [])
        if fk_edges:
            schema_lines.append("【候选连接关系】")
            for edge in fk_edges:
                schema_lines.append(
                    f"- {edge['source_table']}.{edge['source_column']} = "
                    f"{edge['target_table']}.{edge['target_column']}"
                )

        join_paths = path_hint_plan.get("join_paths", [])
        if join_paths:
            schema_lines.append("【候选连接路径】")
            for path in join_paths:
                schema_lines.append(f"- {path}")

    if column_hint_plan.get("enabled"):
        schema_lines.append("【优先关注字段】")
        for item in column_hint_plan.get("columns", []):
            schema_lines.append(
                f"- {item['table']}.{item['column']} (列级检索分={item['score']})"
            )

    if value_hint_plan.get("enabled"):
        question_entities = value_hint_plan.get("question_entities", [])
        if question_entities:
            schema_lines.append("【问题实体】")
            schema_lines.append(f"- {', '.join(question_entities)}")

        entity_matches = value_hint_plan.get("entity_matches", [])
        if entity_matches:
            schema_lines.append("【实体值匹配】")
            for match in entity_matches:
                values = json.dumps(match["values"], ensure_ascii=False)
                schema_lines.append(
                    f"- {match['question_value']} -> {match['table']}.{match['column']} "
                    f"({match['match_type']}): {values}"
                )

        sampled_values = value_hint_plan.get("sampled_values", [])
        if sampled_values:
            schema_lines.append("【候选值样例】")
            for sample in sampled_values:
                values = json.dumps(sample["values"], ensure_ascii=False)
                schema_lines.append(f"- {sample['table']}.{sample['column']}: {values}")

    return "\n".join(schema_lines)


class SchemaRetriever:
    def __init__(
        self,
        max_seed_tables: int = 3,
        max_return_tables: int = 6,
        expand_hops: int = 1,
        min_table_score: float = 1.0,
        auto_mode_threshold: float = 3.0,
        path_hint_mode: str = "off",
        enable_value_hints: bool = True,
        value_hint_max_columns: int = 10,
        value_hint_max_columns_per_table: int = 4,
        value_hint_max_samples: int = 5,
        column_retrieval_max_hits: int = 12,
        column_retrieval_max_hits_per_table: int = 2,
        enable_bridge_completion: bool = True,
        bridge_max_anchor_tables: int = 4,
        bridge_max_paths: int = 2,
        bridge_max_path_length: int = 4,
    ):
        self.max_seed_tables = max_seed_tables
        self.max_return_tables = max_return_tables
        self.expand_hops = expand_hops
        self.min_table_score = min_table_score
        self.auto_mode_threshold = auto_mode_threshold
        self.path_hint_mode = path_hint_mode
        self.value_sketcher = SQLiteValueSketcher(
            enabled=enable_value_hints,
            max_candidate_columns=value_hint_max_columns,
            max_columns_per_table=value_hint_max_columns_per_table,
            max_samples_per_column=value_hint_max_samples,
        )
        self.column_retrieval_max_hits = column_retrieval_max_hits
        self.column_retrieval_max_hits_per_table = column_retrieval_max_hits_per_table
        self.enable_bridge_completion = enable_bridge_completion
        self.bridge_max_anchor_tables = bridge_max_anchor_tables
        self.bridge_max_paths = bridge_max_paths
        self.bridge_max_path_length = bridge_max_path_length

    def retrieve(self, question: str, schema_meta: dict, mode: str = "rag", db_path: str | None = None) -> dict:
        lexical_scores = self._score_tables(question, schema_meta)
        column_scores = self._score_columns(question, schema_meta)
        scores, table_column_boosts = self._merge_table_and_column_scores(
            lexical_scores=lexical_scores,
            column_scores=column_scores,
        )
        ranked_tables = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        seed_tables = [
            table_name
            for table_name, score in ranked_tables
            if score >= self.min_table_score
        ][: self.max_seed_tables]

        fallback_reason = None
        applied_mode = mode

        if mode not in {"full", "rag", "auto"}:
            raise ValueError(f"Unsupported schema mode: {mode}")

        if mode == "full":
            seed_tables = []
        elif not seed_tables:
            applied_mode = "full"
            fallback_reason = "no_seed_table"
        elif mode == "auto" and scores.get(seed_tables[0], 0.0) < self.auto_mode_threshold:
            applied_mode = "full"
            fallback_reason = "low_confidence"

        bridge_plan = self._build_bridge_completion_plan(
            seed_tables=seed_tables,
            ranked_tables=ranked_tables,
            column_scores=column_scores,
            schema_meta=schema_meta,
        )

        if applied_mode == "full":
            selected_tables = list(schema_meta["table_order"])
        else:
            selected_tables = self._expand_tables(seed_tables, schema_meta, bridge_plan=bridge_plan)

        all_join_paths = build_join_paths(schema_meta, seed_tables)
        selected_fk_edges = build_selected_fk_edges(schema_meta, selected_tables, seed_tables)
        path_hint_plan = build_path_hint_plan(
            question=question,
            selected_tables=selected_tables,
            seed_tables=seed_tables,
            all_join_paths=all_join_paths,
            all_selected_fk_edges=selected_fk_edges,
            path_hint_mode=self.path_hint_mode,
            schema_meta=schema_meta,
        )
        column_hint_plan = self._build_column_hint_plan(
            column_scores=column_scores,
            selected_tables=selected_tables,
        )
        value_hint_plan = self.value_sketcher.build_plan(
            question=question,
            schema_meta=schema_meta,
            selected_tables=selected_tables,
            seed_tables=seed_tables,
            db_path=db_path,
        )
        retrieval_explanation = self._build_retrieval_explanation(
            applied_mode=applied_mode,
            fallback_reason=fallback_reason,
            ranked_tables=ranked_tables,
            seed_tables=seed_tables,
            selected_tables=selected_tables,
            column_scores=column_scores,
            lexical_scores=lexical_scores,
            table_column_boosts=table_column_boosts,
            bridge_plan=bridge_plan,
            value_hint_plan=value_hint_plan,
            schema_meta=schema_meta,
        )
        schema_text = render_schema_v6(
            schema_meta,
            selected_tables=selected_tables,
            seed_tables=seed_tables,
            path_hint_plan=path_hint_plan,
            column_hint_plan=column_hint_plan,
            value_hint_plan=value_hint_plan,
        )
        return {
            "schema_text": schema_text,
            "applied_mode": applied_mode,
            "requested_mode": mode,
            "fallback_reason": fallback_reason,
            "question_tokens": question_tokens(question),
            "seed_tables": seed_tables,
            "selected_tables": selected_tables,
            "selected_foreign_keys": selected_fk_edges,
            "join_paths": [" -> ".join(path) for path in all_join_paths],
            "bridge_completion_enabled": bridge_plan["enabled"],
            "bridge_anchor_tables": bridge_plan["anchor_tables"],
            "bridge_paths": bridge_plan["paths"],
            "bridge_added_tables": bridge_plan["added_tables"],
            "path_hint_requested_mode": path_hint_plan["requested_mode"],
            "path_hint_applied_mode": path_hint_plan["applied_mode"],
            "path_hints_enabled": path_hint_plan["enabled"],
            "path_hint_trigger_reasons": path_hint_plan["trigger_reasons"],
            "path_hint_focus_tables": path_hint_plan["focus_tables"],
            "path_hint_foreign_keys": path_hint_plan["foreign_keys"],
            "path_hint_join_paths": path_hint_plan["join_paths"],
            "path_hint_primary_join_path": path_hint_plan["primary_join_path"],
            "column_hints_enabled": column_hint_plan["enabled"],
            "column_hint_columns": column_hint_plan["columns"],
            "value_hints_enabled": value_hint_plan["enabled"],
            "value_hint_question_entities": value_hint_plan["question_entities"],
            "value_hint_entity_matches": value_hint_plan["entity_matches"],
            "value_hint_sampled_values": value_hint_plan["sampled_values"],
            "value_hint_candidate_columns": value_hint_plan["candidate_columns"],
            "table_scores": [
                {"table": table_name, "score": round(score, 3)}
                for table_name, score in ranked_tables
            ],
            "table_scores_lexical": [
                {"table": table_name, "score": round(score, 3)}
                for table_name, score in sorted(lexical_scores.items(), key=lambda item: (-item[1], item[0]))
            ],
            "table_column_boosts": [
                {"table": table_name, "score": round(score, 3)}
                for table_name, score in sorted(table_column_boosts.items(), key=lambda item: (-item[1], item[0]))
                if score > 0
            ],
            "column_scores": column_scores,
            "retrieval_explanation": retrieval_explanation,
        }

    def _score_tables(self, question: str, schema_meta: dict) -> dict:
        tokens = set(question_tokens(question))
        normalized_question = " ".join(tokens)
        scores = {}

        for table_name, table in schema_meta["tables"].items():
            score = 0.0
            table_token_overlap = len(tokens & table["tokens"])
            score += table_token_overlap * 4.0

            full_table_name = " ".join(normalize_identifier_tokens(table_name))
            if full_table_name and full_table_name in normalized_question:
                score += 3.0

            for column in table["columns"]:
                overlap = len(tokens & column["tokens"])
                score += overlap * 1.5

                full_column_name = " ".join(normalize_identifier_tokens(column["name"]))
                if full_column_name and full_column_name in normalized_question:
                    score += 1.0

                if column["name"].lower() in {"name", "title", "type", "country", "maker"}:
                    score += overlap * 0.5

            scores[table_name] = score

        return scores

    def _score_columns(self, question: str, schema_meta: dict) -> list:
        tokens = set(question_tokens(question))
        normalized_question = " ".join(tokens)
        question_entities = extract_question_entities(question)
        has_code_like_entity = any(re.fullmatch(r"[A-Z]{2,5}", value) for value in question_entities)
        aggregation_cues = tokens & AGGREGATION_HINT_KEYWORDS
        column_name_frequency = Counter()
        for table in schema_meta["tables"].values():
            for column in table["columns"]:
                column_name_frequency[column["name"].lower()] += 1
        ranked = []

        for table_name, table in schema_meta["tables"].items():
            table_overlap = len(tokens & table["tokens"])
            table_ranked = []

            for column in table["columns"]:
                column_name = column["name"]
                normalized_column_name = column_name.lower()
                overlap = len(tokens & column["tokens"])
                score = 0.0
                reasons = []
                duplicate_count = column_name_frequency[normalized_column_name]
                is_duplicate_name = duplicate_count > 1
                is_generic_keylike = self._is_generic_keylike_column(normalized_column_name)

                if overlap:
                    score += overlap * 4.0
                    reasons.append("token_overlap")

                    exact_bonus = VALUE_HINT_EXACT_COLUMN_SCORES.get(normalized_column_name, 0.0) * 0.4
                    if exact_bonus:
                        score += exact_bonus
                        reasons.append("semantic_exact_bonus")

                full_column_name = " ".join(normalize_identifier_tokens(column_name))
                if full_column_name and full_column_name in normalized_question:
                    score += 2.5
                    reasons.append("full_column_name_match")

                if table_overlap and overlap:
                    score += table_overlap * 1.25
                    reasons.append("table_column_alignment")

                if has_code_like_entity and "code" in normalized_column_name:
                    code_bonus = 2.5
                    if "airport" in normalized_column_name:
                        code_bonus += 0.5
                    score += code_bonus
                    reasons.append("code_entity_bonus")

                if (
                    question_entities
                    and table_overlap
                    and normalized_column_name in ENTITY_FRIENDLY_COLUMN_NAMES
                    and (not is_duplicate_name or full_column_name in normalized_question)
                ):
                    score += 1.5
                    reasons.append("entity_friendly_column")

                if (
                    aggregation_cues
                    and any(keyword in normalized_column_name for keyword in VALUE_HINT_MEASURE_KEYWORDS)
                    and not is_generic_keylike
                ):
                    score += 1.5
                    reasons.append("aggregation_measure_bonus")

                if column["is_primary_key"] and overlap:
                    score += 0.5
                    reasons.append("primary_key_overlap")

                if column["foreign_key"] and overlap:
                    score += 0.5
                    reasons.append("foreign_key_overlap")

                if (
                    is_generic_keylike
                    and not has_code_like_entity
                    and full_column_name not in normalized_question
                    and overlap == 0
                ):
                    continue

                if (
                    is_duplicate_name
                    and full_column_name not in normalized_question
                    and normalized_column_name in AMBIGUOUS_COLUMN_PENALTY_NAMES
                ):
                    score -= min(2.0, 0.8 * (duplicate_count - 1))
                    reasons.append("duplicate_name_penalty")

                if (
                    (column["is_primary_key"] or column["foreign_key"] or is_generic_keylike)
                    and full_column_name not in normalized_question
                    and not has_code_like_entity
                    and overlap <= 1
                ):
                    score -= 1.5
                    reasons.append("generic_key_penalty")

                if score < 3.5:
                    continue

                strong_signal_count = sum(
                    1
                    for reason in reasons
                    if reason in {
                        "token_overlap",
                        "full_column_name_match",
                        "semantic_exact_bonus",
                        "code_entity_bonus",
                        "aggregation_measure_bonus",
                    }
                )

                table_ranked.append(
                    {
                        "table": table_name,
                        "column": column_name,
                        "score": round(score, 3),
                        "reasons": reasons,
                        "is_duplicate_name": is_duplicate_name,
                        "is_generic_keylike": is_generic_keylike,
                        "strong_signal_count": strong_signal_count,
                        "supports_prompt_hint": (
                            score >= 8.0
                            and strong_signal_count >= 2
                            and not is_duplicate_name
                            and (not is_generic_keylike or has_code_like_entity or full_column_name in normalized_question)
                        ),
                        "supports_table_boost": (
                            score >= 5.5
                            and strong_signal_count >= 1
                            and not (is_duplicate_name and full_column_name not in normalized_question)
                        ),
                    }
                )

            table_ranked.sort(key=lambda item: (-item["score"], item["column"].lower()))
            ranked.extend(table_ranked[: self.column_retrieval_max_hits_per_table])

        ranked.sort(key=lambda item: (-item["score"], item["table"].lower(), item["column"].lower()))
        return ranked[: self.column_retrieval_max_hits]

    def _merge_table_and_column_scores(self, lexical_scores: dict, column_scores: list) -> tuple[dict, dict]:
        column_boosts = {table_name: 0.0 for table_name in lexical_scores}
        hits_by_table = {}
        for item in column_scores:
            if not item.get("supports_table_boost"):
                continue
            hits_by_table.setdefault(item["table"], []).append(item)

        weights = (0.3, 0.12)
        for table_name, hits in hits_by_table.items():
            boost = 0.0
            for idx, hit in enumerate(hits[: len(weights)]):
                boost += hit["score"] * weights[idx]
            column_boosts[table_name] = min(boost, 2.0)

        fused_scores = {
            table_name: lexical_scores.get(table_name, 0.0) + column_boosts.get(table_name, 0.0)
            for table_name in lexical_scores
        }
        return fused_scores, column_boosts

    def _build_column_hint_plan(self, column_scores: list, selected_tables: list) -> dict:
        selected_set = set(selected_tables)
        filtered = [
            {
                "table": item["table"],
                "column": item["column"],
                "score": item["score"],
            }
            for item in column_scores
            if item["table"] in selected_set and item.get("supports_prompt_hint")
        ]
        if len(filtered) >= 2 and filtered[0]["score"] - filtered[1]["score"] < 1.5:
            filtered = filtered[:1]
        else:
            filtered = filtered[:2]
        return {
            "enabled": bool(filtered),
            "columns": filtered,
        }

    def _is_generic_keylike_column(self, normalized_column_name: str) -> bool:
        if normalized_column_name in GENERIC_KEYLIKE_COLUMN_NAMES:
            return True
        return (
            normalized_column_name.endswith("id")
            or normalized_column_name.endswith("_id")
            or normalized_column_name.endswith("code")
            or normalized_column_name.endswith("_code")
        )

    def _build_bridge_completion_plan(
        self,
        seed_tables: list,
        ranked_tables: list,
        column_scores: list,
        schema_meta: dict,
    ) -> dict:
        default_plan = {
            "enabled": False,
            "anchor_tables": seed_tables,
            "paths": [],
            "added_tables": [],
        }
        if not self.enable_bridge_completion or len(seed_tables) < 2:
            return default_plan

        anchor_tables = []
        seen = set()
        for table_name in seed_tables:
            if table_name in seen:
                continue
            seen.add(table_name)
            anchor_tables.append(table_name)

        for item in column_scores:
            table_name = item["table"]
            if table_name in seen:
                continue
            if not item.get("supports_table_boost"):
                continue
            if item["score"] < 6.5:
                continue
            seen.add(table_name)
            anchor_tables.append(table_name)
            if len(anchor_tables) >= self.bridge_max_anchor_tables:
                break

        if len(anchor_tables) < 2:
            return default_plan

        seed_set = set(seed_tables)
        candidate_paths = []
        seen_paths = set()
        rank_position = {table_name: idx for idx, (table_name, _) in enumerate(ranked_tables)}
        for idx, left in enumerate(anchor_tables):
            for right in anchor_tables[idx + 1:]:
                path = shortest_table_path(schema_meta, left, right)
                if len(path) < 3 or len(path) > self.bridge_max_path_length:
                    continue
                path_key = tuple(path)
                if path_key in seen_paths:
                    continue
                bridge_nodes = [table for table in path[1:-1] if table not in seed_set]
                if not bridge_nodes:
                    continue
                seen_paths.add(path_key)
                candidate_paths.append(
                    {
                        "path": path,
                        "bridge_nodes": bridge_nodes,
                        "priority": (
                            len(path),
                            rank_position.get(left, 999),
                            rank_position.get(right, 999),
                            " -> ".join(path),
                        ),
                    }
                )

        if not candidate_paths:
            return default_plan

        candidate_paths.sort(key=lambda item: item["priority"])
        selected_paths = candidate_paths[: self.bridge_max_paths]
        added_tables = []
        seen_added = set()
        for item in selected_paths:
            for table_name in item["bridge_nodes"]:
                if table_name in seen_added:
                    continue
                seen_added.add(table_name)
                added_tables.append(table_name)

        if not added_tables:
            return default_plan

        return {
            "enabled": True,
            "anchor_tables": anchor_tables,
            "paths": [" -> ".join(item["path"]) for item in selected_paths],
            "added_tables": added_tables,
        }

    def _build_retrieval_explanation(
        self,
        applied_mode: str,
        fallback_reason: str | None,
        ranked_tables: list,
        seed_tables: list,
        selected_tables: list,
        column_scores: list,
        lexical_scores: dict,
        table_column_boosts: dict,
        bridge_plan: dict,
        value_hint_plan: dict,
        schema_meta: dict,
    ) -> dict:
        selected_set = set(selected_tables)
        seed_set = set(seed_tables)
        bridge_added_set = set(bridge_plan.get("added_tables", []))
        join_path_nodes = set()
        for path in build_join_paths(schema_meta, seed_tables):
            join_path_nodes.update(path)

        value_matches_by_table = {}
        for match in value_hint_plan.get("entity_matches", []):
            value_matches_by_table.setdefault(match["table"], []).append(
                {
                    "question_value": match["question_value"],
                    "column": match["column"],
                    "match_type": match["match_type"],
                }
            )

        top_columns_by_table = {}
        for item in column_scores:
            top_columns_by_table.setdefault(item["table"], []).append(
                {
                    "column": item["column"],
                    "score": item["score"],
                    "reasons": item.get("reasons", []),
                }
            )

        table_rationales = []
        for table_name in selected_tables:
            selected_as = []
            reasons = []

            if table_name in seed_set:
                selected_as.append("seed")
                reasons.append("selected as a top retrieval seed table")
            if table_name in bridge_added_set:
                selected_as.append("bridge_path")
                reasons.append("added as a bridge table on a shortest foreign-key path")
            elif table_name in join_path_nodes and table_name not in seed_set:
                selected_as.append("seed_join_path")
                reasons.append("kept because it lies on a join path between seed tables")
            elif table_name not in seed_set:
                selected_as.append("expanded_context")
                reasons.append("kept as supporting context under schema subgraph expansion")

            if table_column_boosts.get(table_name, 0.0) > 0:
                selected_as.append("column_relevant")
                reasons.append("received extra score from matched columns")
            if table_name in value_matches_by_table:
                selected_as.append("value_linked")
                reasons.append("contains a matched question value from live database inspection")
            if applied_mode == "full" and table_name not in seed_set:
                selected_as.append("full_schema_context")
                if fallback_reason == "low_confidence":
                    reasons.append("included because retrieval confidence was too low for subgraph-only mode")
                elif fallback_reason == "no_seed_table":
                    reasons.append("included because no reliable seed table was found")
                else:
                    reasons.append("included because full schema mode was requested")

            table_rationales.append(
                {
                    "table": table_name,
                    "selected_as": selected_as,
                    "fused_score": round(next((score for name, score in ranked_tables if name == table_name), 0.0), 3),
                    "lexical_score": round(lexical_scores.get(table_name, 0.0), 3),
                    "column_boost": round(table_column_boosts.get(table_name, 0.0), 3),
                    "top_columns": top_columns_by_table.get(table_name, [])[:2],
                    "value_matches": value_matches_by_table.get(table_name, [])[:2],
                    "reasons": reasons[:4],
                }
            )

        dropped_candidates = []
        for table_name, score in ranked_tables:
            if table_name in selected_set:
                continue
            if score < self.min_table_score and table_column_boosts.get(table_name, 0.0) <= 0:
                continue
            dropped_candidates.append(
                {
                    "table": table_name,
                    "fused_score": round(score, 3),
                    "lexical_score": round(lexical_scores.get(table_name, 0.0), 3),
                    "column_boost": round(table_column_boosts.get(table_name, 0.0), 3),
                    "reason": "ranked below the retained subgraph budget",
                }
            )
            if len(dropped_candidates) >= 4:
                break

        ambiguities = []
        entity_groups = {}
        for match in value_hint_plan.get("entity_matches", []):
            entity_groups.setdefault(match["question_value"], []).append(match)
        for question_value, matches in entity_groups.items():
            unique_targets = sorted({f"{item['table']}.{item['column']}" for item in matches})
            if len(unique_targets) >= 2:
                ambiguities.append(
                    {
                        "type": "entity_match_ambiguity",
                        "value": question_value,
                        "candidates": unique_targets[:4],
                    }
                )

        top_column_name_groups = {}
        for item in column_scores[:8]:
            top_column_name_groups.setdefault(item["column"].lower(), []).append(item)
        for column_name, matches in top_column_name_groups.items():
            candidate_tables = sorted({item["table"] for item in matches})
            if len(candidate_tables) >= 2:
                ambiguities.append(
                    {
                        "type": "column_name_ambiguity",
                        "column": column_name,
                        "candidate_tables": candidate_tables[:4],
                    }
                )

        confidence = self._estimate_retrieval_confidence(
            applied_mode=applied_mode,
            fallback_reason=fallback_reason,
            ranked_tables=ranked_tables,
            seed_tables=seed_tables,
            value_hint_plan=value_hint_plan,
            ambiguities=ambiguities,
        )

        return {
            "confidence": confidence,
            "table_rationales": table_rationales,
            "dropped_candidates": dropped_candidates,
            "ambiguities": ambiguities[:6],
        }

    def _estimate_retrieval_confidence(
        self,
        applied_mode: str,
        fallback_reason: str | None,
        ranked_tables: list,
        seed_tables: list,
        value_hint_plan: dict,
        ambiguities: list,
    ) -> str:
        if applied_mode == "full" and fallback_reason in {"low_confidence", "no_seed_table"}:
            return "low"
        if not seed_tables:
            return "low"

        top_score = ranked_tables[0][1] if ranked_tables else 0.0
        second_score = ranked_tables[1][1] if len(ranked_tables) > 1 else 0.0
        score_gap = top_score - second_score
        has_value_match = bool(value_hint_plan.get("entity_matches"))
        ambiguity_count = len(ambiguities)

        if top_score >= 8.0 and (score_gap >= 2.0 or has_value_match) and ambiguity_count <= 1:
            return "high"
        if top_score >= 4.0 and ambiguity_count <= 3:
            return "medium"
        return "low"

    def _expand_tables(self, seed_tables: list, schema_meta: dict, bridge_plan: dict | None = None) -> list:
        if not seed_tables:
            return []
        if bridge_plan is None:
            bridge_plan = {
                "enabled": False,
                "anchor_tables": seed_tables,
                "paths": [],
                "added_tables": [],
            }

        selected = set(seed_tables)
        queue = deque((table_name, 0) for table_name in seed_tables)

        while queue:
            table_name, depth = queue.popleft()
            if depth >= self.expand_hops:
                continue

            for neighbor in schema_meta["tables"][table_name]["neighbors"]:
                if neighbor in selected:
                    continue
                selected.add(neighbor)
                queue.append((neighbor, depth + 1))

        for idx, left in enumerate(seed_tables):
            for right in seed_tables[idx + 1:]:
                path = shortest_table_path(schema_meta, left, right)
                if path:
                    selected.update(path)

        if bridge_plan.get("enabled"):
            for path_text in bridge_plan.get("paths", []):
                selected.update(path_text.split(" -> "))

        priority_order = []
        for table_name in seed_tables:
            if table_name not in priority_order:
                priority_order.append(table_name)
        for table_name in bridge_plan.get("added_tables", []):
            if table_name in selected and table_name not in priority_order:
                priority_order.append(table_name)
        for table_name in bridge_plan.get("anchor_tables", []):
            if table_name in selected and table_name not in priority_order:
                priority_order.append(table_name)

        ordered = [table for table in priority_order if table in selected]
        ordered.extend(
            table for table in schema_meta["table_order"]
            if table in selected and table not in ordered
        )
        return ordered[: self.max_return_tables]
