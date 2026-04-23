import ast
import json
import re
import sqlite3
from pathlib import Path

from schema_retriever import normalize_identifier_tokens


POSITIVE_PATTERNS = [
    "最小",
    "最大",
    "最多",
    "最少",
    "最低",
    "最高",
    "minimum",
    "maximum",
    "most",
    "least",
    "smallest",
    "largest",
    "fewest",
    "lowest",
    "highest",
    "youngest",
    "oldest",
    "earliest",
    "latest",
    "longest",
    "shortest",
    "greatest",
    "biggest",
]

NEGATIVE_PATTERNS = [
    r"\bat\s+least\b",
    r"\bat\s+most\b",
]

COUNT_CUES = [
    "number of",
    "how many",
    "count of",
    "most number of",
    "fewest number of",
    "least number of",
    "highest number of",
    "lowest number of",
    "largest number of",
    "smallest number of",
    "highest count of",
    "lowest count of",
    "largest count of",
    "smallest count of",
    "most common",
    "most frequent",
    "least frequent",
]

VALUE_CUES = [
    "what is the minimum",
    "what is the maximum",
    "what is the smallest",
    "what is the largest",
    "what is the lowest",
    "what is the highest",
    "average, minimum, and maximum",
    "minimum and maximum",
]

OBJECT_CUES = [
    "which",
    "who",
    "what is the name",
    "what is the id",
    "what is the model",
    "with the",
]

GROUP_CUES = [
    "for each",
    "each type",
    "each country",
    "different number of",
    "for all the different",
]

AGG_CUES = [
    "maximum",
    "minimum",
    "average",
    "largest percentage",
    "smallest percentage",
]

TOP1_CUES = [
    "most number of",
    "least number of",
    "highest number of",
    "lowest number of",
    "largest number of",
    "smallest number of",
    "the most",
    "the fewest",
    "most common",
    "most frequent",
    "least frequent",
]

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}

TEMPLATE_ORDER = [
    "ORDER_BY",
    "NESTED",
    "GROUP_COUNT_TOP1",
    "ENTITY_BY_RELATED_COUNT_TOP1",
    "JOIN_GROUP_COUNT_TOP1",
    "JOIN_ORDER_BY",
]

TEMPLATE_DESCRIPTIONS = {
    "ORDER_BY": "Single-table top-1 object retrieval via ORDER BY ... LIMIT 1.",
    "NESTED": "Single-table extrema object retrieval via nested MIN/MAX comparison.",
    "GROUP_COUNT_TOP1": "Top-1 group by count via GROUP BY ... ORDER BY COUNT(*) ... LIMIT 1.",
    "ENTITY_BY_RELATED_COUNT_TOP1": "Top-1 entity or entity attribute by counting related fact rows over an enumerated FK plan.",
    "JOIN_GROUP_COUNT_TOP1": "Top-1 entity by related row count via JOIN + GROUP BY + COUNT(*) + ORDER BY + LIMIT 1.",
    "JOIN_ORDER_BY": "Single-hop join object retrieval where target and measure are on different tables.",
}


POS_RE = re.compile(
    r"\b(?:"
    + "|".join(re.escape(x) for x in POSITIVE_PATTERNS if x.isascii())
    + r")\b",
    re.I,
)
NEG_RES = [re.compile(pattern, re.I) for pattern in NEGATIVE_PATTERNS]


def _clean_optional_text(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(_clean_optional_text(item) for item in value if _clean_optional_text(item))
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() in {"null", "none", "n/a"}:
            return ""
        return stripped
    return str(value).strip()


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _coerce_float(value, default=0.0):
    try:
        if value is None or value == "":
            return float(default)
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        return float(value)
    except Exception:
        return float(default)


def _split_csv(expr):
    parts = []
    current = []
    depth = 0
    for char in expr:
        if char == "(":
            depth += 1
        elif char == ")" and depth > 0:
            depth -= 1
        elif char == "," and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            continue
        current.append(char)

    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _strip_quotes(value):
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"', "`"}:
        return value[1:-1]
    return value


def _normalize_condition(condition):
    condition = _clean_optional_text(condition)
    if not condition:
        return ""
    return re.sub(r"^\s*where\s+", "", condition, flags=re.I).strip()


def _normalize_join_clause(join_clause):
    join_clause = _clean_optional_text(join_clause)
    if not join_clause:
        return ""
    return re.sub(r"^\s*join\s+", "JOIN ", join_clause, flags=re.I).strip()


def _contains_any_regex(text, patterns):
    return any(re.search(pattern, text, flags=re.I) for pattern in patterns)


def _extract_qualified_identifiers(expr):
    return re.findall(r"\b([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\b", _clean_optional_text(expr))


def _clean_string_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [_clean_optional_text(item) for item in value if _clean_optional_text(item)]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = ast.literal_eval(text)
                if isinstance(parsed, list):
                    return [_clean_optional_text(item) for item in parsed if _clean_optional_text(item)]
            except Exception:
                pass
        return [_clean_optional_text(part) for part in _split_csv(text) if _clean_optional_text(part)]
    return [_clean_optional_text(value)]


def _projection_count(expr):
    return len(_split_csv(_clean_optional_text(expr)))


def expected_projection_count(question):
    q = question.lower()
    prefix = q
    stop_markers = [
        " ordered by ",
        " with the ",
        " with most ",
        " with least ",
        " whose ",
        " who ",
        " that ",
        " by the ",
        " for the ",
        " from the ",
        " in the ",
        " where ",
    ]
    for marker in stop_markers:
        idx = prefix.find(marker)
        if idx != -1:
            prefix = prefix[:idx]
            break

    if re.search(r"\b(list|show|return|give|find)\s+both\b", q):
        return 2

    comma_count = prefix.count(",")
    if comma_count:
        return min(6, comma_count + 1)

    if re.search(r"\b(name|names|id|ids|code|codes|year|date|capacity|country|district|surface area|release year|winner|loser|first name|last name|summary|line 1|line 2)\b.*\band\b.*\b(name|names|id|ids|code|codes|year|date|capacity|country|district|surface area|release year|winner|loser|first name|last name|summary|line 1|line 2)\b", prefix):
        return 2

    return 1


def _parse_json_like(text):
    if not text:
        return None

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    payload = text[start : end + 1].strip()
    for loader in (json.loads, ast.literal_eval):
        try:
            parsed = loader(payload)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            continue
    return None


class SQLiteSchemaGraph:
    def __init__(self, db_path):
        self.db_path = str(db_path)
        self.tables = {}
        self.foreign_keys = []
        self._all_columns = set()
        self._load()

    def _connect(self):
        db_uri = f"file:{Path(self.db_path).resolve().as_posix()}?mode=ro"
        return sqlite3.connect(db_uri, uri=True)

    def _load(self):
        conn = self._connect()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            table_names = [row[0] for row in cursor.fetchall()]

            for table_name in table_names:
                self.tables[table_name.lower()] = {
                    "name": table_name,
                    "columns": {},
                    "tokens": set(normalize_identifier_tokens(table_name)),
                }

                for cid, col_name, *_ in cursor.execute(f"PRAGMA table_info('{table_name}')"):
                    del cid
                    self.tables[table_name.lower()]["columns"][col_name.lower()] = col_name
                    self._all_columns.add(col_name.lower())

                for fk_row in cursor.execute(f"PRAGMA foreign_key_list('{table_name}')"):
                    _, _, target_table, source_col, target_col, *_ = fk_row
                    self.foreign_keys.append(
                        {
                            "source_table": table_name,
                            "source_column": source_col,
                            "target_table": target_table,
                            "target_column": target_col,
                        }
                    )
        finally:
            conn.close()

    @property
    def all_columns(self):
        return self._all_columns

    def canonical_table_name(self, table_expr):
        table_expr = _clean_optional_text(table_expr)
        if not table_expr:
            return ""

        tokens = table_expr.replace(",", " ").split()
        if not tokens:
            return ""
        return _strip_quotes(tokens[0]).lower()

    def table_exists(self, table_expr):
        return self.canonical_table_name(table_expr) in self.tables

    def extract_alias_map(self, table_exprs):
        alias_map = {}
        default_table = None

        for table_expr in table_exprs:
            expr = _clean_optional_text(table_expr)
            if not expr:
                continue

            match = re.match(
                r"^\s*([A-Za-z_][\w]*)\s*(?:AS\s+([A-Za-z_][\w]*)|([A-Za-z_][\w]*))?\s*$",
                expr,
                flags=re.I,
            )
            if not match:
                continue

            base_table = match.group(1)
            alias = match.group(2) or match.group(3)
            if default_table is None:
                default_table = base_table

            alias_map[base_table.lower()] = base_table
            if alias and alias.lower() != base_table.lower():
                alias_map[alias.lower()] = base_table

        return alias_map, default_table

    def resolve_identifier(self, identifier, alias_map, default_table=None):
        identifier = _clean_optional_text(identifier)
        if not identifier:
            return None, None

        identifier = re.sub(r"\s+AS\s+[A-Za-z_][\w]*$", "", identifier, flags=re.I).strip()
        if "(" in identifier or ")" in identifier:
            return None, None

        if "." in identifier:
            table_or_alias, column_name = identifier.split(".", 1)
            base_table = alias_map.get(table_or_alias.lower(), table_or_alias)
            return self.canonical_table_name(base_table), _strip_quotes(column_name).lower()

        if default_table:
            return self.canonical_table_name(default_table), _strip_quotes(identifier).lower()

        column_name = _strip_quotes(identifier).lower()
        candidates = []
        for table_key, table in self.tables.items():
            if column_name in table["columns"]:
                candidates.append(table_key)
        if len(candidates) == 1:
            return candidates[0], column_name

        return None, column_name

    def column_exists(self, table_name, column_name):
        if not table_name or not column_name:
            return False
        table = self.tables.get(table_name.lower())
        if not table:
            return False
        return column_name.lower() in table["columns"]

    def has_exactly_one_fk_path(self, left_table, right_table, max_hops=1):
        left = self.canonical_table_name(left_table)
        right = self.canonical_table_name(right_table)
        if not left or not right or left == right:
            return False

        if max_hops != 1:
            return False

        direct_edges = 0
        for edge in self.foreign_keys:
            source = edge["source_table"].lower()
            target = edge["target_table"].lower()
            if {source, target} == {left, right}:
                direct_edges += 1

        return direct_edges == 1

    def has_direct_fk_path(self, left_table, right_table):
        left = self.canonical_table_name(left_table)
        right = self.canonical_table_name(right_table)
        if not left or not right or left == right:
            return False

        for edge in self.foreign_keys:
            source = edge["source_table"].lower()
            target = edge["target_table"].lower()
            if {source, target} == {left, right}:
                return True
        return False

    def render_column_candidates(self):
        lines = []
        for table_key in sorted(self.tables):
            table = self.tables[table_key]
            columns = [
                f"{table['name']}.{column_name}"
                for column_name in table["columns"].values()
            ]
            lines.append(f"- {table['name']}: {', '.join(columns)}")
        return "\n".join(lines)

    def get_table(self, table_name):
        return self.tables.get(self.canonical_table_name(table_name))

    def qualified_column(self, table_name, column_name):
        table = self.get_table(table_name)
        if not table:
            return ""
        column = table["columns"].get(_strip_quotes(column_name).lower())
        if not column:
            return ""
        return f"{table['name']}.{column}"

    def table_columns(self, table_name):
        table = self.get_table(table_name)
        if not table:
            return []
        return [f"{table['name']}.{column_name}" for column_name in table["columns"].values()]

    def entity_related_count_plans(self):
        plans = []
        seen = set()
        for edge in self.foreign_keys:
            fact_table = edge["source_table"]
            entity_table = edge["target_table"]
            fact_fk = edge["source_column"]
            entity_key = edge["target_column"]
            key = (
                fact_table.lower(),
                fact_fk.lower(),
                entity_table.lower(),
                entity_key.lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            plan_id = f"P{len(plans)}"
            join_on = (
                f"{entity_table}.{entity_key} = "
                f"{fact_table}.{fact_fk}"
            )
            plans.append(
                {
                    "plan_id": plan_id,
                    "entity_table": entity_table,
                    "fact_table": fact_table,
                    "entity_key": self.qualified_column(entity_table, entity_key),
                    "fact_fk": self.qualified_column(fact_table, fact_fk),
                    "join_on": join_on,
                    "entity_columns": self.table_columns(entity_table),
                    "fact_columns": self.table_columns(fact_table),
                }
            )
        return plans


class SuperlativePatternSolver:
    ROUTE_SYSTEM_PROMPT = (
        "You are a routing assistant for a SQL pattern solver. "
        "Return only valid JSON with no explanation."
    )

    TEMPLATE_ROUTER_SYSTEM_PROMPT = (
        "You are a conservative router for SQL templates. "
        "You must decide whether a question should use a template route or fall back to the baseline SQL generator. "
        "Prefer precision over recall. Return only valid JSON with no explanation."
    )

    SLOT_SYSTEM_PROMPT = (
        "You extract SQL template slots from a question and schema. "
        "Return only valid JSON with no explanation."
    )

    def __init__(
        self,
        coder,
        sandbox,
        retry_on_empty_result=False,
        mode="v1",
        router_use_template_threshold=0.70,
        router_template_threshold=0.65,
    ):
        self.coder = coder
        self.sandbox = sandbox
        self.retry_on_empty_result = retry_on_empty_result
        self.mode = (mode or "v1").lower()
        self.router_use_template_threshold = router_use_template_threshold
        self.router_template_threshold = router_template_threshold

    def try_solve(self, schema_info, question):
        if not is_superlative(question):
            return {
                "matched": False,
                "reason": "not_superlative",
            }

        if is_plain_extrema_value_query(question):
            return {
                "matched": False,
                "reason": "plain_extrema_value_query",
            }

        if is_group_agg_query(question):
            return {
                "matched": False,
                "reason": "group_agg_query",
            }

        exclusion_reason = get_superlative_exclusion_reason(question, mode=self.mode)
        if exclusion_reason:
            return {
                "matched": False,
                "reason": exclusion_reason,
            }

        schema = SQLiteSchemaGraph(self.sandbox.db_path)
        slot_hint = self._extract_slot_hint(schema_info, question)
        router_decision = None
        candidate_templates = []

        if uses_phase1_router(self.mode):
            candidate_templates = get_candidate_templates(question, slot_hint, schema, mode=self.mode)
            if not candidate_templates:
                return {
                    "matched": False,
                    "reason": "no_phase1_candidates",
                    "slot_hint": slot_hint,
                }

            router_decision = self._route_templates(
                schema_info=schema_info,
                question=question,
                slot_hint=slot_hint,
                candidate_templates=candidate_templates,
                schema=schema,
            )
            if not self._router_accepts(router_decision):
                return {
                    "matched": False,
                    "reason": "low_router_confidence",
                    "slot_hint": slot_hint,
                    "candidate_templates": candidate_templates,
                    "router_decision": router_decision,
                }

            template = router_decision["selected_template"]
        else:
            template = choose_template(question, slot_hint, schema, mode=self.mode)

        if template == "FALLBACK":
            return {
                "matched": False,
                "reason": "template_fallback",
                "slot_hint": slot_hint,
                "candidate_templates": candidate_templates,
                "router_decision": router_decision,
            }

        slot = self._extract_slots(schema_info, question, template, schema=schema)
        if not slot:
            return {
                "matched": True,
                "applied": False,
                "reason": "slot_extraction_failed",
                "template": template,
                "slot_hint": slot_hint,
                "candidate_templates": candidate_templates,
                "router_decision": router_decision,
            }

        if uses_controlled_slot_filling(self.mode):
            slot = self._ensure_projection_complete(
                schema_info=schema_info,
                question=question,
                template=template,
                slot=slot,
                schema=schema,
            )
            if not slot or not projection_is_complete(question, slot):
                return {
                    "matched": True,
                    "applied": False,
                    "reason": "projection_incomplete",
                    "template": template,
                    "slot_hint": slot_hint,
                    "slot": slot,
                    "candidate_templates": candidate_templates,
                    "router_decision": router_decision,
                }

        if uses_projection_validator(self.mode) and not projection_is_complete(question, slot):
            return {
                "matched": True,
                "applied": False,
                "reason": "projection_incomplete",
                "template": template,
                "slot_hint": slot_hint,
                "slot": slot,
                "candidate_templates": candidate_templates,
                "router_decision": router_decision,
            }

        sql = build_sql(template, slot)
        if not sql:
            return {
                "matched": True,
                "applied": False,
                "reason": "sql_build_failed",
                "template": template,
                "slot_hint": slot_hint,
                "slot": slot,
                "candidate_templates": candidate_templates,
                "router_decision": router_decision,
            }

        if not validate_by_template(template, slot, schema):
            return {
                "matched": True,
                "applied": False,
                "reason": "validation_failed",
                "template": template,
                "slot_hint": slot_hint,
                "slot": slot,
                "generated_sql": sql,
                "candidate_templates": candidate_templates,
                "router_decision": router_decision,
            }

        execution = self.sandbox.execute_query(sql)
        if execution["status"] != "success":
            return {
                "matched": True,
                "applied": False,
                "reason": "execution_error",
                "template": template,
                "slot_hint": slot_hint,
                "slot": slot,
                "generated_sql": sql,
                "execution": execution,
                "candidate_templates": candidate_templates,
                "router_decision": router_decision,
            }

        if self.retry_on_empty_result and execution.get("row_count", 0) == 0:
            return {
                "matched": True,
                "applied": False,
                "reason": "empty_result",
                "template": template,
                "slot_hint": slot_hint,
                "slot": slot,
                "generated_sql": sql,
                "execution": execution,
                "candidate_templates": candidate_templates,
                "router_decision": router_decision,
            }

        return {
            "matched": True,
            "applied": True,
            "reason": "pattern_success",
            "template": template,
            "slot_hint": slot_hint,
            "slot": slot,
            "generated_sql": sql,
            "execution": execution,
            "candidate_templates": candidate_templates,
            "router_decision": router_decision,
        }

    def _extract_slot_hint(self, schema_info, question):
        prompt = f"""Schema:
{schema_info}

Question:
{question}

Return JSON with this format:
{{
  "target_table": "",
  "measure_table": "",
  "needs_group_by": false,
  "needs_nested": false
}}

Rules:
- target_table and measure_table must be valid table names from the schema when possible.
- needs_group_by is true only when the question asks for per-group counting or aggregation.
- needs_nested is true only when the question needs a nested extrema comparison.
- Output JSON only.
"""
        raw = self.coder.generate_response(
            [{"role": "user", "content": prompt}],
            system_prompt=self.ROUTE_SYSTEM_PROMPT,
            max_new_tokens=220,
        )
        data = _parse_json_like(raw) or {}
        return {
            "target_table": _clean_optional_text(data.get("target_table")),
            "measure_table": _clean_optional_text(data.get("measure_table")),
            "needs_group_by": _coerce_bool(data.get("needs_group_by")),
            "needs_nested": _coerce_bool(data.get("needs_nested")),
        }

    def _extract_slots(self, schema_info, question, template, schema=None):
        if template == "ENTITY_BY_RELATED_COUNT_TOP1":
            return self._extract_entity_related_count_slot(schema_info, question, schema)

        prompt = self._slot_prompt(schema_info, question, template, schema=schema)
        raw = self.coder.generate_response(
            [{"role": "user", "content": prompt}],
            system_prompt=self.SLOT_SYSTEM_PROMPT,
            max_new_tokens=320,
        )
        data = _parse_json_like(raw)
        if not isinstance(data, dict):
            return None

        slot = normalize_slot(data)
        if "condition" in slot:
            slot["condition"] = _normalize_condition(slot["condition"])
        if "join_clause" in slot:
            slot["join_clause"] = _normalize_join_clause(slot["join_clause"])
        return slot

    def _extract_entity_related_count_slot(self, schema_info, question, schema):
        if schema is None:
            return None

        plans = schema.entity_related_count_plans()
        if not plans:
            return None

        plan_lines = []
        for plan in plans:
            plan_lines.append(
                "\n".join(
                    [
                        f"- plan_id: {plan['plan_id']}",
                        f"  entity_table: {plan['entity_table']}",
                        f"  fact_table: {plan['fact_table']}",
                        f"  join_on: {plan['join_on']}",
                        f"  default_group_by: {plan['entity_key']}",
                        f"  entity_columns: {', '.join(plan['entity_columns'])}",
                        f"  fact_columns: {', '.join(plan['fact_columns'])}",
                    ]
                )
            )

        prompt = f"""Schema:
{schema_info}

Question:
{question}

Enumerated FK count plans:
{chr(10).join(plan_lines)}

Return JSON with this exact format:
{{
  "plan_id": "",
  "target_columns": [],
  "group_by_column": "",
  "order": "ASC or DESC",
  "condition": "",
  "include_count": false
}}

Rules:
- Choose exactly one plan_id from Enumerated FK count plans.
- Choose a plan where entity_table is the answer object and fact_table rows are what should be counted.
- target_columns must come from the chosen plan's entity_columns. Include every entity column explicitly requested by the question.
- group_by_column must come from the chosen plan's entity_columns. Prefer default_group_by for an entity answer; use a target attribute only when the question asks for an attribute group such as city, country, language, grade, or continent.
- order is DESC for most/highest/largest/greatest and ASC for fewest/least/lowest/smallest.
- condition may reference columns from the chosen entity/fact plan. Keep it empty when no WHERE clause is needed.
- include_count is true only when the question explicitly asks to output the count/number itself.
- Do not write SQL.
- Output JSON only.
"""
        raw = self.coder.generate_response(
            [{"role": "user", "content": prompt}],
            system_prompt=self.SLOT_SYSTEM_PROMPT,
            max_new_tokens=360,
        )
        data = _parse_json_like(raw)
        if not isinstance(data, dict):
            return None

        plan_id = _clean_optional_text(data.get("plan_id"))
        plan = next((item for item in plans if item["plan_id"].lower() == plan_id.lower()), None)
        if not plan:
            return None

        target_columns = _clean_string_list(data.get("target_columns"))
        group_by_column = _clean_optional_text(data.get("group_by_column")) or plan["entity_key"]
        include_count = _coerce_bool(data.get("include_count"))
        if include_count and "COUNT(*)" not in {col.upper() for col in target_columns}:
            target_columns.append("COUNT(*)")

        slot = {
            "plan_id": plan["plan_id"],
            "entity_table": plan["entity_table"],
            "fact_table": plan["fact_table"],
            "entity_key": plan["entity_key"],
            "fact_fk": plan["fact_fk"],
            "join_on": plan["join_on"],
            "target_columns": target_columns,
            "target": ", ".join(target_columns),
            "group_by_column": group_by_column,
            "order": _clean_optional_text(data.get("order")) or "DESC",
            "condition": _normalize_condition(data.get("condition")),
            "include_count": include_count,
        }
        return slot

    def _ensure_projection_complete(self, schema_info, question, template, slot, schema):
        if projection_is_complete(question, slot):
            return slot

        repaired_slot = dict(slot)
        repaired_targets = self._repair_target_columns(
            schema_info=schema_info,
            question=question,
            template=template,
            slot=slot,
            schema=schema,
        )
        if repaired_targets:
            repaired_slot["target_columns"] = repaired_targets
            repaired_slot["target"] = ", ".join(repaired_targets)
        return repaired_slot

    def _repair_target_columns(self, schema_info, question, template, slot, schema):
        prompt = f"""Schema:
{schema_info}

Available column candidates:
{schema.render_column_candidates()}

Question:
{question}

Template:
{template}

Current slot:
{json.dumps(slot, ensure_ascii=False)}

The current target projection is incomplete. The question appears to require at least {expected_projection_count(question)} output column(s).

Return JSON with this exact format:
{{
  "target_columns": []
}}

Rules:
- Choose target_columns only from Available column candidates.
- Include every column explicitly requested by the question.
- Do not include the ranking/count/measure column unless the question asks to output it.
- Do not output SQL.
- Output JSON only.
"""
        raw = self.coder.generate_response(
            [{"role": "user", "content": prompt}],
            system_prompt=self.SLOT_SYSTEM_PROMPT,
            max_new_tokens=220,
        )
        data = _parse_json_like(raw) or {}
        return _clean_string_list(data.get("target_columns"))

    def _route_templates(self, schema_info, question, slot_hint, candidate_templates, schema):
        candidate_lines = "\n".join(
            f'- {name}: {TEMPLATE_DESCRIPTIONS[name]}'
            for name in candidate_templates
        )
        all_template_scores = ",\n    ".join(f'"{name}": 0.0' for name in TEMPLATE_ORDER)
        signal_lines = [
            f"- is_count_superlative: {str(is_count_superlative(question)).lower()}",
            f"- is_group_count_superlative: {str(is_group_count_superlative(question, slot_hint)).lower()}",
            f"- is_join_group_count_superlative: {str(is_join_group_count_superlative(question, slot_hint, schema)).lower()}",
            f"- has_entity_related_count_plans: {str(bool(schema.entity_related_count_plans())).lower()}",
            f"- is_single_hop_join_superlative: {str(is_single_hop_join_superlative(slot_hint, schema)).lower()}",
            f"- looks_like_nested_extrema: {str(any(token in question.lower() for token in ['minimum', 'smallest'])).lower()}",
            f"- target_table: {slot_hint.get('target_table', '')}",
            f"- measure_table: {slot_hint.get('measure_table', '')}",
            f"- needs_group_by: {str(slot_hint.get('needs_group_by', False)).lower()}",
            f"- needs_nested: {str(slot_hint.get('needs_nested', False)).lower()}",
        ]
        prompt = f"""Schema:
{schema_info}

Question:
{question}

Candidate templates:
{candidate_lines}

Structural signals:
{chr(10).join(signal_lines)}

Return JSON with this format:
{{
  "use_template_score": 0.0,
  "selected_template": "ORDER_BY | NESTED | GROUP_COUNT_TOP1 | ENTITY_BY_RELATED_COUNT_TOP1 | JOIN_GROUP_COUNT_TOP1 | JOIN_ORDER_BY | FALLBACK",
  "template_scores": {{
    {all_template_scores}
  }},
  "reason": ""
}}

Rules:
- Scores must be floats between 0 and 1.
- selected_template must be one of the candidate templates or FALLBACK.
- Prefer precision over recall.
- If uncertain, return low scores and choose FALLBACK.
- Base your decision on structural fit, not just lexical overlap.
- For entity-by-related-row-count questions, prefer ENTITY_BY_RELATED_COUNT_TOP1 over GROUP_COUNT_TOP1 because its join plan is schema-enumerated.
- Output JSON only.
"""
        raw = self.coder.generate_response(
            [{"role": "user", "content": prompt}],
            system_prompt=self.TEMPLATE_ROUTER_SYSTEM_PROMPT,
            max_new_tokens=260,
        )
        data = _parse_json_like(raw) or {}
        template_scores = data.get("template_scores", {}) if isinstance(data.get("template_scores"), dict) else {}
        normalized_scores = {
            name: max(0.0, min(1.0, _coerce_float(template_scores.get(name), default=0.0)))
            for name in TEMPLATE_ORDER
        }
        selected_template = _clean_optional_text(data.get("selected_template")).upper()
        use_template_score = max(0.0, min(1.0, _coerce_float(data.get("use_template_score"), default=0.0)))
        if selected_template not in candidate_templates:
            best_candidate = max(candidate_templates, key=lambda name: normalized_scores.get(name, 0.0))
            if normalized_scores.get(best_candidate, 0.0) > 0:
                selected_template = best_candidate
            else:
                selected_template = "FALLBACK"
        selected_template_score = (
            normalized_scores.get(selected_template, 0.0)
            if selected_template != "FALLBACK"
            else 0.0
        )
        return {
            "use_template_score": use_template_score,
            "selected_template": selected_template,
            "selected_template_score": selected_template_score,
            "route_score": use_template_score * selected_template_score,
            "template_scores": normalized_scores,
            "candidate_templates": list(candidate_templates),
            "reason": _clean_optional_text(data.get("reason")),
            "raw_response": raw,
        }

    def _router_accepts(self, router_decision):
        if not router_decision:
            return False
        if router_decision.get("selected_template") in {"", "FALLBACK"}:
            return False
        if router_decision.get("use_template_score", 0.0) < self.router_use_template_threshold:
            return False
        if router_decision.get("selected_template_score", 0.0) < self.router_template_threshold:
            return False
        return True

    def _slot_prompt(self, schema_info, question, template, schema=None):
        controlled = uses_controlled_slot_filling(self.mode)
        target_columns_line = '  "target_columns": [],\n' if controlled else ""
        if template == "ORDER_BY":
            slot_schema = f"""{{
{target_columns_line}  "target": "",
  "table": "",
  "measure": "",
  "order": "ASC or DESC",
  "condition": ""
}}"""
        elif template == "NESTED":
            slot_schema = f"""{{
{target_columns_line}  "target": "",
  "table": "",
  "measure": "",
  "agg_func": "MIN or MAX",
  "condition": ""
}}"""
        elif template == "GROUP_COUNT_TOP1":
            slot_schema = f"""{{
{target_columns_line}  "target": "",
  "table": "",
  "join_clause": "",
  "group_key": "",
  "order": "ASC or DESC",
  "condition": ""
}}"""
        elif template == "JOIN_GROUP_COUNT_TOP1":
            slot_schema = f"""{{
{target_columns_line}  "target": "",
  "entity_table": "",
  "fact_table": "",
  "join_on": "",
  "group_key": "",
  "order": "ASC or DESC",
  "condition": ""
}}"""
        elif template == "JOIN_ORDER_BY":
            slot_schema = f"""{{
{target_columns_line}  "target": "",
  "left_table": "",
  "right_table": "",
  "join_on": "",
  "measure": "",
  "order": "ASC or DESC",
  "condition": ""
}}"""
        else:
            raise ValueError(f"Unsupported template: {template}")

        candidate_block = ""
        if controlled and schema is not None:
            candidate_block = f"""

Available column candidates:
{schema.render_column_candidates()}
"""

        controlled_rules = ""
        if controlled:
            controlled_rules = """
- In phase1_c, fill target_columns first. It must be an array of fully qualified columns such as ["singer.song_name", "singer.song_release_year"].
- Choose target_columns only from Available column candidates.
- Include every column explicitly requested by the question; do not silently drop second or third requested output columns.
- Also fill target as the comma-separated version of target_columns for backward compatibility.
"""

        return f"""Schema:
{schema_info}
{candidate_block}

Question:
{question}

Template:
{template}

Return JSON with this exact format:
{slot_schema}

Rules:
- Use only valid table names and column names from the schema.
- Keep condition empty when no WHERE clause is needed.
- For GROUP_COUNT_TOP1, use it only when target and group_key both come from the grouped source side. Use join_clause only when a join is needed for filtering or context.
- For JOIN_GROUP_COUNT_TOP1, use it when the final target belongs to an entity table but the count happens on a related fact table. In this case target and group_key should come from entity_table.
{controlled_rules}
- Do not output SQL.
- Output JSON only.
"""


def is_superlative(question):
    q = question.lower()

    if any(x in q for x in ["最小", "最大", "最多", "最少", "最低", "最高"]):
        return True

    if any(pattern.search(q) for pattern in NEG_RES):
        return False

    return bool(POS_RE.search(q))


def is_plain_extrema_value_query(question):
    q = question.lower()
    return any(cue in q for cue in VALUE_CUES) and not any(cue in q for cue in OBJECT_CUES)


def is_group_agg_query(question):
    q = question.lower()
    return (
        any(cue in q for cue in GROUP_CUES)
        and any(cue in q for cue in AGG_CUES)
        and not any(cue in q for cue in TOP1_CUES)
    )


def is_multi_agg_extrema_query(question):
    q = question.lower()
    dual_extrema_pairs = [
        "maximum and minimum",
        "minimum and maximum",
        "max and min",
        "min and max",
    ]
    avg_extrema_pairs = [
        "average, minimum, and maximum",
        "average and maximum",
        "average and minimum",
    ]
    return any(cue in q for cue in dual_extrema_pairs + avg_extrema_pairs)


def is_topk_superlative_query(question):
    q = question.lower()

    if re.search(r"\btop\s+([2-9]|10)\b", q):
        return True

    if re.search(r"\b(top)\s+(two|three|four|five|six|seven|eight|nine|ten)\b", q):
        return True

    ranked_patterns = [
        r"\b([2-9]|10)\s+(youngest|oldest|earliest|latest|largest|smallest|highest|lowest|most|fewest)\b",
        r"\b(two|three|four|five|six|seven|eight|nine|ten)\s+(youngest|oldest|earliest|latest|largest|smallest|highest|lowest|most|fewest)\b",
    ]
    return any(re.search(pattern, q) for pattern in ranked_patterns)


def is_count_superlative_with_count_output(question):
    q = question.lower()
    if not is_count_superlative(q):
        return False

    count_output_cues = [
        "and how many",
        "and number of",
        "and the number of",
        "and the numbers of",
        "how many channels use it",
        "how many does it have",
        "how many does they have",
        "number of tv channel it has",
        "number of tv channels it has",
    ]
    return any(cue in q for cue in count_output_cues)


def is_temporal_superlative_query(question):
    q = question.lower()
    return any(token in q for token in ["earliest", "latest"])


def is_ambiguous_popularity_query(question):
    q = question.lower()
    return "most popular" in q or "popular" in q


def is_ordered_list_superlative_query(question):
    q = question.lower()
    if not re.search(r"\border(?:ed)?\s+by\b", q):
        return False

    list_cues = [
        "show ",
        "list ",
        "return ",
        "for all",
        " all ",
        "from the oldest to the youngest",
        "from oldest to youngest",
        "from the youngest to the oldest",
        "from youngest to oldest",
        "from the largest to the smallest",
        "from largest to smallest",
        "from the highest to the lowest",
        "from highest to lowest",
    ]
    return any(cue in q for cue in list_cues)


def is_per_group_percentage_extrema_query(question):
    q = question.lower()
    group_signal = any(cue in q for cue in ["for each", "each country", "different countries", "different country"])
    extrema_signal = any(cue in q for cue in ["greatest percentage", "highest percentage", "lowest percentage", "largest percentage", "smallest percentage"])
    return group_signal and extrema_signal


def is_count_superlative(question):
    q = question.lower()
    return is_superlative(q) and any(cue in q for cue in COUNT_CUES)


def is_group_count_superlative(question, slot_hint=None):
    q = question.lower()
    if is_count_superlative(q):
        return True

    soft_count_patterns = [
        r"\bhas\s+the\s+(most|fewest|least|greatest|highest|lowest|largest|smallest)\b",
        r"\bwith\s+the\s+(most|fewest|least|greatest|highest|lowest|largest|smallest)\b",
        r"\b(the|which|what)\b.*\b(most|fewest|least|greatest|highest|lowest|largest|smallest)\b",
    ]
    if _contains_any_regex(q, soft_count_patterns):
        if slot_hint is None:
            return True
        if slot_hint.get("needs_group_by"):
            return True

    return False


def normalize_slot(data):
    slot = {}
    for key, value in data.items():
        if key == "target_columns":
            slot[key] = _clean_string_list(value)
        else:
            slot[key] = _clean_optional_text(value)

    if slot.get("target_columns"):
        slot["target"] = ", ".join(slot["target_columns"])
    elif isinstance(data.get("target"), list):
        target_columns = _clean_string_list(data.get("target"))
        slot["target_columns"] = target_columns
        slot["target"] = ", ".join(target_columns)

    return slot


def projection_is_complete(question, slot):
    expected_count = expected_projection_count(question)
    actual_count = _projection_count(slot.get("target", ""))
    return actual_count >= expected_count


def uses_phase0_exclusion_layer(mode):
    mode = (mode or "v1").lower()
    return mode in {"phase0", "v2", "phase1", "phase1_c", "phase1_d"}


def uses_phase1_router(mode):
    mode = (mode or "").lower()
    return mode in {"phase1", "phase1_c", "phase1_d"}


def uses_controlled_slot_filling(mode):
    mode = (mode or "").lower()
    return mode == "phase1_c"


def uses_entity_count_plan_mode(mode):
    mode = (mode or "").lower()
    return mode == "phase1_d"


def uses_projection_validator(mode):
    mode = (mode or "").lower()
    return mode == "phase1_d"


def get_superlative_exclusion_reason(question, mode="v1"):
    if not uses_phase0_exclusion_layer(mode):
        return None

    if is_multi_agg_extrema_query(question):
        return "multi_agg_extrema_query"

    if is_topk_superlative_query(question):
        return "topk_superlative_query"

    if is_count_superlative_with_count_output(question):
        return "count_superlative_with_count_output"

    if is_temporal_superlative_query(question):
        return "temporal_superlative_query"

    if is_ambiguous_popularity_query(question):
        return "ambiguous_popularity_query"

    if uses_controlled_slot_filling(mode) and is_ordered_list_superlative_query(question):
        return "ordered_list_superlative_query"

    if uses_controlled_slot_filling(mode) and is_per_group_percentage_extrema_query(question):
        return "per_group_percentage_extrema_query"

    return None


def get_candidate_templates(question, slot_hint, schema, mode="phase1"):
    q = question.lower()
    candidates = []

    if uses_entity_count_plan_mode(mode) and is_join_group_count_superlative(question, slot_hint, schema):
        candidates.append("ENTITY_BY_RELATED_COUNT_TOP1")

    if is_join_group_count_superlative(question, slot_hint, schema):
        candidates.append("JOIN_GROUP_COUNT_TOP1")

    if is_group_count_superlative(q, slot_hint):
        candidates.append("GROUP_COUNT_TOP1")

    if is_single_hop_join_superlative(slot_hint, schema):
        candidates.append("JOIN_ORDER_BY")

    if any(token in q for token in ["minimum", "smallest"]):
        candidates.append("NESTED")

    candidates.append("ORDER_BY")

    deduped = []
    for name in candidates:
        if name not in deduped:
            deduped.append(name)
    return deduped


def is_join_group_count_superlative(question, slot_hint, schema):
    if not is_group_count_superlative(question, slot_hint):
        return False

    target_table = schema.canonical_table_name(slot_hint.get("target_table"))
    measure_table = schema.canonical_table_name(slot_hint.get("measure_table"))
    if not target_table or not measure_table:
        return False

    if target_table == measure_table:
        return False

    return schema.has_direct_fk_path(target_table, measure_table)


def is_single_hop_join_superlative(slot_hint, schema):
    target_table = slot_hint.get("target_table")
    measure_table = slot_hint.get("measure_table")
    if not target_table or not measure_table:
        return False

    if schema.canonical_table_name(target_table) == schema.canonical_table_name(measure_table):
        return False

    if slot_hint.get("needs_group_by"):
        return False

    if slot_hint.get("needs_nested"):
        return False

    return schema.has_exactly_one_fk_path(target_table, measure_table, max_hops=1)


def choose_template(question, slot_hint, schema, mode="v1"):
    q = question.lower()

    if is_plain_extrema_value_query(q):
        return "FALLBACK"

    if is_group_agg_query(q):
        return "FALLBACK"

    if get_superlative_exclusion_reason(q, mode=mode):
        return "FALLBACK"

    if is_join_group_count_superlative(question, slot_hint, schema):
        return "JOIN_GROUP_COUNT_TOP1"

    if is_group_count_superlative(q, slot_hint):
        return "GROUP_COUNT_TOP1"

    if is_single_hop_join_superlative(slot_hint, schema):
        return "JOIN_ORDER_BY"

    if any(token in q for token in ["minimum", "smallest", "earliest"]):
        return "NESTED"

    return "ORDER_BY"


def build_sql(template, slot):
    if template == "ORDER_BY":
        return build_sql_order(slot)
    if template == "NESTED":
        return build_sql_nested(slot)
    if template == "GROUP_COUNT_TOP1":
        return build_sql_group_count(slot)
    if template == "ENTITY_BY_RELATED_COUNT_TOP1":
        return build_sql_entity_related_count(slot)
    if template == "JOIN_GROUP_COUNT_TOP1":
        return build_sql_join_group_count(slot)
    if template == "JOIN_ORDER_BY":
        return build_sql_join_order(slot)
    return ""


def build_sql_order(slot):
    table = _clean_optional_text(slot.get("table"))
    target = _clean_optional_text(slot.get("target"))
    measure = _clean_optional_text(slot.get("measure"))
    order = _clean_optional_text(slot.get("order")) or "ASC"
    condition = _normalize_condition(slot.get("condition"))

    if not table or not target or not measure:
        return ""

    sql = f"SELECT {target} FROM {table}"
    if condition:
        sql += f" WHERE {condition}"
    sql += f" ORDER BY {measure} {order} LIMIT 1"
    return sql


def build_sql_nested(slot):
    table = _clean_optional_text(slot.get("table"))
    target = _clean_optional_text(slot.get("target"))
    measure = _clean_optional_text(slot.get("measure"))
    agg_func = _clean_optional_text(slot.get("agg_func")) or "MIN"
    condition = _normalize_condition(slot.get("condition"))

    if not table or not target or not measure:
        return ""

    sql = (
        f"SELECT {target} FROM {table} "
        f"WHERE {measure} = (SELECT {agg_func}({measure}) FROM {table}"
    )
    if condition:
        sql += f" WHERE {condition}"
    sql += ")"
    return sql


def build_sql_group_count(slot):
    table = _clean_optional_text(slot.get("table"))
    target = _clean_optional_text(slot.get("target"))
    join_clause = _normalize_join_clause(slot.get("join_clause"))
    group_key = _clean_optional_text(slot.get("group_key"))
    order = _clean_optional_text(slot.get("order")) or "DESC"
    condition = _normalize_condition(slot.get("condition"))

    if not table or not target or not group_key:
        return ""

    sql = f"SELECT {target} FROM {table}"
    if join_clause:
        if not join_clause.upper().startswith("JOIN "):
            join_clause = f"JOIN {join_clause}"
        sql += f" {join_clause}"
    if condition:
        sql += f" WHERE {condition}"
    sql += f" GROUP BY {group_key} ORDER BY COUNT(*) {order} LIMIT 1"
    return sql


def build_sql_join_group_count(slot):
    entity_table = _clean_optional_text(slot.get("entity_table"))
    fact_table = _clean_optional_text(slot.get("fact_table"))
    target = _clean_optional_text(slot.get("target"))
    join_on = _clean_optional_text(slot.get("join_on"))
    group_key = _clean_optional_text(slot.get("group_key"))
    order = _clean_optional_text(slot.get("order")) or "DESC"
    condition = _normalize_condition(slot.get("condition"))

    if not entity_table or not fact_table or not target or not join_on or not group_key:
        return ""

    sql = (
        f"SELECT {target} FROM {entity_table} "
        f"JOIN {fact_table} ON {join_on}"
    )
    if condition:
        sql += f" WHERE {condition}"
    sql += f" GROUP BY {group_key} ORDER BY COUNT(*) {order} LIMIT 1"
    return sql


def build_sql_entity_related_count(slot):
    entity_table = _clean_optional_text(slot.get("entity_table"))
    fact_table = _clean_optional_text(slot.get("fact_table"))
    target = _clean_optional_text(slot.get("target"))
    join_on = _clean_optional_text(slot.get("join_on"))
    group_by_column = _clean_optional_text(slot.get("group_by_column")) or _clean_optional_text(slot.get("entity_key"))
    order = _clean_optional_text(slot.get("order")) or "DESC"
    condition = _normalize_condition(slot.get("condition"))

    if not entity_table or not fact_table or not target or not join_on or not group_by_column:
        return ""

    sql = (
        f"SELECT {target} FROM {entity_table} "
        f"JOIN {fact_table} ON {join_on}"
    )
    if condition:
        sql += f" WHERE {condition}"
    sql += f" GROUP BY {group_by_column} ORDER BY COUNT(*) {order} LIMIT 1"
    return sql


def build_sql_join_order(slot):
    left_table = _clean_optional_text(slot.get("left_table"))
    right_table = _clean_optional_text(slot.get("right_table"))
    target = _clean_optional_text(slot.get("target"))
    join_on = _clean_optional_text(slot.get("join_on"))
    measure = _clean_optional_text(slot.get("measure"))
    order = _clean_optional_text(slot.get("order")) or "ASC"
    condition = _normalize_condition(slot.get("condition"))

    if not left_table or not right_table or not target or not join_on or not measure:
        return ""

    sql = (
        f"SELECT {target} FROM {left_table} "
        f"JOIN {right_table} ON {join_on}"
    )
    if condition:
        sql += f" WHERE {condition}"
    sql += f" ORDER BY {measure} {order} LIMIT 1"
    return sql


def _validate_projection(expr, alias_map, default_table, schema):
    projection_parts = _split_csv(expr)
    if not projection_parts:
        return False

    for part in projection_parts:
        table_name, column_name = schema.resolve_identifier(part, alias_map, default_table)
        if table_name is None and column_name is None:
            continue
        if not schema.column_exists(table_name, column_name):
            return False
    return True


def _collect_projection_tables(expr, alias_map, default_table, schema):
    projection_tables = set()
    projection_parts = _split_csv(expr)
    if not projection_parts:
        return projection_tables

    for part in projection_parts:
        table_name, column_name = schema.resolve_identifier(part, alias_map, default_table)
        if table_name is None and column_name is None:
            continue
        if schema.column_exists(table_name, column_name):
            projection_tables.add(table_name)
    return projection_tables


def _validate_join_predicate(expr, alias_map, schema, required_tables=None):
    join_expr = _clean_optional_text(expr)
    if not join_expr:
        return False

    if re.search(r"\bOR\b", join_expr, flags=re.I):
        return False

    if "=" not in join_expr:
        return False

    qualified_refs = _extract_qualified_identifiers(join_expr)
    if len(qualified_refs) < 2:
        return False

    referenced_tables = set()
    for table_or_alias, column_name in qualified_refs:
        base_table = alias_map.get(table_or_alias.lower(), table_or_alias)
        table_name = schema.canonical_table_name(base_table)
        if not schema.column_exists(table_name, column_name):
            return False
        referenced_tables.add(table_name)

    if required_tables and not set(required_tables).issubset(referenced_tables):
        return False

    return True


def validate(slot, schema):
    table_expr = _clean_optional_text(slot.get("table"))
    if not schema.table_exists(table_expr):
        return False

    alias_map, default_table = schema.extract_alias_map([table_expr])
    if not _validate_projection(slot.get("target", ""), alias_map, default_table, schema):
        return False

    measure_table, measure_column = schema.resolve_identifier(
        slot.get("measure", ""),
        alias_map,
        default_table,
    )
    return schema.column_exists(measure_table, measure_column)


def validate_group_count(slot, schema):
    table_expr = _clean_optional_text(slot.get("table"))
    if not schema.table_exists(table_expr):
        return False

    table_exprs = [table_expr]
    join_clause = _normalize_join_clause(slot.get("join_clause"))
    for match in re.findall(
        r"\bJOIN\s+([A-Za-z_][\w]*)\s*(?:AS\s+([A-Za-z_][\w]*)|([A-Za-z_][\w]*))?",
        join_clause,
        flags=re.I,
    ):
        table_name = match[0]
        alias = match[1] or match[2]
        table_exprs.append(f"{table_name} AS {alias}" if alias else table_name)

    alias_map, default_table = schema.extract_alias_map(table_exprs)

    if not _validate_projection(slot.get("target", ""), alias_map, default_table, schema):
        return False

    group_table, group_column = schema.resolve_identifier(
        slot.get("group_key", ""),
        alias_map,
        default_table,
    )
    if not schema.column_exists(group_table, group_column):
        return False

    projection_tables = _collect_projection_tables(slot.get("target", ""), alias_map, default_table, schema)
    if not projection_tables:
        return False

    if any(table_name != group_table for table_name in projection_tables):
        return False

    if join_clause:
        on_clauses = re.findall(r"\bON\b\s+(.+?)(?=\bJOIN\b|$)", join_clause, flags=re.I)
        if not on_clauses:
            return False
        for on_clause in on_clauses:
            if not _validate_join_predicate(on_clause, alias_map, schema):
                return False

    return True


def validate_join_group_count(slot, schema):
    entity_table = _clean_optional_text(slot.get("entity_table"))
    fact_table = _clean_optional_text(slot.get("fact_table"))
    if not schema.table_exists(entity_table) or not schema.table_exists(fact_table):
        return False

    entity_base = schema.canonical_table_name(entity_table)
    fact_base = schema.canonical_table_name(fact_table)
    if not entity_base or not fact_base or entity_base == fact_base:
        return False

    alias_map, default_table = schema.extract_alias_map([entity_table, fact_table])

    if not _validate_projection(slot.get("target", ""), alias_map, default_table, schema):
        return False

    projection_tables = _collect_projection_tables(slot.get("target", ""), alias_map, default_table, schema)
    if not projection_tables or any(table_name != entity_base for table_name in projection_tables):
        return False

    group_table, group_column = schema.resolve_identifier(
        slot.get("group_key", ""),
        alias_map,
        default_table,
    )
    if not schema.column_exists(group_table, group_column):
        return False

    if group_table != entity_base:
        return False

    return _validate_join_predicate(
        slot.get("join_on", ""),
        alias_map,
        schema,
        required_tables={entity_base, fact_base},
    )


def validate_entity_related_count(slot, schema):
    entity_table = _clean_optional_text(slot.get("entity_table"))
    fact_table = _clean_optional_text(slot.get("fact_table"))
    if not schema.table_exists(entity_table) or not schema.table_exists(fact_table):
        return False

    entity_base = schema.canonical_table_name(entity_table)
    fact_base = schema.canonical_table_name(fact_table)
    if not entity_base or not fact_base or entity_base == fact_base:
        return False

    if not schema.has_direct_fk_path(entity_table, fact_table):
        return False

    alias_map, default_table = schema.extract_alias_map([entity_table, fact_table])
    del default_table

    if not _validate_projection(slot.get("target", ""), alias_map, None, schema):
        return False

    projection_tables = _collect_projection_tables(slot.get("target", ""), alias_map, None, schema)
    if not projection_tables:
        return False
    if any(table_name != entity_base for table_name in projection_tables):
        return False

    group_table, group_column = schema.resolve_identifier(
        slot.get("group_by_column", "") or slot.get("entity_key", ""),
        alias_map,
        None,
    )
    if not schema.column_exists(group_table, group_column):
        return False
    if group_table != entity_base:
        return False

    return _validate_join_predicate(
        slot.get("join_on", ""),
        alias_map,
        schema,
        required_tables={entity_base, fact_base},
    )


def validate_single_hop_join(slot, schema):
    left_table = _clean_optional_text(slot.get("left_table"))
    right_table = _clean_optional_text(slot.get("right_table"))
    if not schema.table_exists(left_table) or not schema.table_exists(right_table):
        return False

    if not schema.has_exactly_one_fk_path(left_table, right_table, max_hops=1):
        return False

    alias_map, default_table = schema.extract_alias_map([left_table, right_table])
    if not _validate_projection(slot.get("target", ""), alias_map, default_table, schema):
        return False

    measure_table, measure_column = schema.resolve_identifier(
        slot.get("measure", ""),
        alias_map,
        default_table,
    )
    if not schema.column_exists(measure_table, measure_column):
        return False

    return _validate_join_predicate(
        slot.get("join_on", ""),
        alias_map,
        schema,
        required_tables={
            schema.canonical_table_name(left_table),
            schema.canonical_table_name(right_table),
        },
    )


def validate_by_template(template, slot, schema):
    if template in {"ORDER_BY", "NESTED"}:
        return validate(slot, schema)
    if template == "GROUP_COUNT_TOP1":
        return validate_group_count(slot, schema)
    if template == "ENTITY_BY_RELATED_COUNT_TOP1":
        return validate_entity_related_count(slot, schema)
    if template == "JOIN_GROUP_COUNT_TOP1":
        return validate_join_group_count(slot, schema)
    if template == "JOIN_ORDER_BY":
        return validate_single_hop_join(slot, schema)
    return False
