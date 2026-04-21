import json
import re
from collections import deque
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
    ):
        self.max_seed_tables = max_seed_tables
        self.max_return_tables = max_return_tables
        self.expand_hops = expand_hops
        self.min_table_score = min_table_score
        self.auto_mode_threshold = auto_mode_threshold
        self.path_hint_mode = path_hint_mode

    def retrieve(self, question: str, schema_meta: dict, mode: str = "rag") -> dict:
        scores = self._score_tables(question, schema_meta)
        ranked_tables = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        seed_tables = [
            table_name
            for table_name, score in ranked_tables
            if score >= self.min_table_score
        ][: self.max_seed_tables]

        selected_tables = self._expand_tables(seed_tables, schema_meta)
        fallback_reason = None
        applied_mode = mode

        if mode not in {"full", "rag", "auto"}:
            raise ValueError(f"Unsupported schema mode: {mode}")

        if mode == "full":
            selected_tables = list(schema_meta["table_order"])
            seed_tables = []
        elif not seed_tables:
            selected_tables = list(schema_meta["table_order"])
            applied_mode = "full"
            fallback_reason = "no_seed_table"
        elif mode == "auto" and scores.get(seed_tables[0], 0.0) < self.auto_mode_threshold:
            selected_tables = list(schema_meta["table_order"])
            applied_mode = "full"
            fallback_reason = "low_confidence"

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
        schema_text = render_schema_v6(
            schema_meta,
            selected_tables=selected_tables,
            seed_tables=seed_tables,
            path_hint_plan=path_hint_plan,
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
            "path_hint_requested_mode": path_hint_plan["requested_mode"],
            "path_hint_applied_mode": path_hint_plan["applied_mode"],
            "path_hints_enabled": path_hint_plan["enabled"],
            "path_hint_trigger_reasons": path_hint_plan["trigger_reasons"],
            "path_hint_focus_tables": path_hint_plan["focus_tables"],
            "path_hint_foreign_keys": path_hint_plan["foreign_keys"],
            "path_hint_join_paths": path_hint_plan["join_paths"],
            "path_hint_primary_join_path": path_hint_plan["primary_join_path"],
            "table_scores": [
                {"table": table_name, "score": round(score, 3)}
                for table_name, score in ranked_tables
            ],
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

    def _expand_tables(self, seed_tables: list, schema_meta: dict) -> list:
        if not seed_tables:
            return []

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

        ordered = [table for table in schema_meta["table_order"] if table in selected]
        return ordered[: self.max_return_tables]
