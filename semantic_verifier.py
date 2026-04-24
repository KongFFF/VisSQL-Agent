import re


RISK_SEVERITY_PENALTY = {
    "high": 0.35,
    "medium": 0.18,
    "low": 0.08,
}


def _clean_text(value):
    if value is None:
        return ""
    return str(value).strip()


def _normalize_text(value):
    return _clean_text(value).lower()


def _split_csv(expr):
    parts = []
    current = []
    depth = 0

    for char in _clean_text(expr):
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


def _strip_table_prefix(identifier):
    identifier = _clean_text(identifier)
    if "." in identifier:
        return identifier.split(".", 1)[1]
    return identifier


def _normalize_identifier(identifier):
    return _strip_table_prefix(identifier).strip('"').strip("`").lower()


def _extract_alias_map(sql):
    alias_map = {}
    pattern = r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w]*)\s*(?:AS\s+)?([A-Za-z_][\w]*)?"

    for table_name, alias in re.findall(pattern, sql, flags=re.IGNORECASE):
        alias_map[table_name.lower()] = table_name
        if alias:
            alias_map[alias.lower()] = table_name

    return alias_map


def _extract_literal_filters(sql):
    filters = []
    alias_map = _extract_alias_map(sql)
    unique_tables = sorted({table_name for table_name in alias_map.values()})
    fallback_table = unique_tables[0] if len(unique_tables) == 1 else ""
    pattern = (
        r"(?P<identifier>\b(?:[A-Za-z_][\w]*\.)?[A-Za-z_][\w]*\b)\s*"
        r"(?P<operator>=|!=|<>|LIKE|like)\s*"
        r"(?P<literal>'[^']*'|\"[^\"]*\"|-?\d+(?:\.\d+)?)"
    )

    for match in re.finditer(pattern, sql, flags=re.IGNORECASE):
        identifier = match.group("identifier")
        if "." in identifier:
            table_or_alias, column = identifier.split(".", 1)
        else:
            table_or_alias, column = "", identifier

        table_name = alias_map.get(table_or_alias.lower(), table_or_alias) if table_or_alias else fallback_table
        literal = match.group("literal").strip()
        if literal and literal[0] in {"'", '"'} and literal[-1] == literal[0]:
            literal = literal[1:-1]

        filters.append(
            {
                "raw_identifier": identifier,
                "table": table_name,
                "column": column,
                "operator": match.group("operator").upper(),
                "literal": literal,
            }
        )

    return filters


def _extract_select_clause(sql):
    match = re.search(
        r"^\s*SELECT\s+(?:DISTINCT\s+)?(?P<select>.*?)\s+FROM\s",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group("select").strip() if match else ""


def _extract_group_by_clause(sql):
    match = re.search(
        r"\bGROUP\s+BY\s+(?P<group>.*?)(?:\bHAVING\b|\bORDER\s+BY\b|\bLIMIT\b|$)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group("group").strip() if match else ""


def _extract_order_by_clause(sql):
    match = re.search(
        r"\bORDER\s+BY\s+(?P<order>.*?)(?:\bLIMIT\b|$)",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group("order").strip() if match else ""


def _extract_select_expressions(sql):
    return _split_csv(_extract_select_clause(sql))


def _extract_group_expressions(sql):
    return _split_csv(_extract_group_by_clause(sql))


def _identifier_pairs(expr):
    return re.findall(r"\b([A-Za-z_][\w]*)\.([A-Za-z_][\w]*)\b", _clean_text(expr))


def _contains_count(expr):
    return bool(re.search(r"\bcount\s*\(", _clean_text(expr), flags=re.IGNORECASE))


def _contains_aggregate(expr):
    return bool(re.search(r"\b(count|sum|avg|min|max)\s*\(", _clean_text(expr), flags=re.IGNORECASE))


def _extract_non_agg_identifiers(select_exprs):
    identifiers = []
    for expr in select_exprs:
        if _contains_aggregate(expr):
            continue
        identifiers.extend(_identifier_pairs(expr))
    return identifiers


def _question_requests_count(question):
    q = _normalize_text(question)
    return any(token in q for token in ["how many", "number of", "count of", "count "])


def _question_requests_each(question):
    q = _normalize_text(question)
    return any(token in q for token in ["for each", "each ", " per "])


def _question_requests_entity_only(question):
    q = _normalize_text(question)
    if _question_requests_count(question):
        return False
    return any(token in q for token in ["which", "what are", "what is the name", "list", "show", "find"])


def _question_has_negation(question):
    q = _normalize_text(question)
    return any(token in q for token in [" not ", " without ", " except ", " but not ", " do not ", " did not "])


def _question_has_both(question):
    q = _normalize_text(question)
    return " both " in f" {q} "


class SemanticVerifier:
    def verify(self, question, sql, retrieval_info=None, schema_meta=None):
        sql = _clean_text(sql)
        retrieval_info = retrieval_info or {}
        schema_meta = schema_meta or {}
        risk_flags = []

        risk_flags.extend(self._check_entity_filter_mismatch(sql, retrieval_info))
        risk_flags.extend(self._check_group_projection_mismatch(question, sql, schema_meta))
        risk_flags.extend(self._check_setop_semantics(question, sql))

        score = 1.0
        for flag in risk_flags:
            score -= RISK_SEVERITY_PENALTY.get(flag["severity"], 0.0)
        score = max(0.0, round(score, 3))

        repair_hints = []
        seen_hints = set()
        for flag in risk_flags:
            hint = flag.get("repair_hint")
            if not hint or hint in seen_hints:
                continue
            seen_hints.add(hint)
            repair_hints.append(hint)

        return {
            "score": score,
            "risk_flags": risk_flags,
            "repair_hints": repair_hints,
            "should_retry": any(flag["severity"] == "high" for flag in risk_flags) or score < 0.7,
        }

    def _check_entity_filter_mismatch(self, sql, retrieval_info):
        flags = []
        entity_matches = retrieval_info.get("value_hint_entity_matches") or []
        if not entity_matches:
            return flags

        filters = _extract_literal_filters(sql)
        fk_edges = retrieval_info.get("selected_foreign_keys") or []

        for match in entity_matches:
            question_value = _clean_text(match.get("question_value"))
            matched_table = _clean_text(match.get("table"))
            matched_column = _clean_text(match.get("column"))
            matched_values = {_normalize_text(question_value)}
            matched_values.update(_normalize_text(value) for value in match.get("values", []))

            relevant_filters = [
                filter_info
                for filter_info in filters
                if _normalize_text(filter_info["literal"]) in matched_values
            ]
            if not relevant_filters:
                continue

            accepted_pairs = {
                (_normalize_text(matched_table), _normalize_identifier(matched_column))
            }
            for edge in fk_edges:
                source_pair = (
                    _normalize_text(edge.get("source_table")),
                    _normalize_identifier(edge.get("source_column")),
                )
                target_pair = (
                    _normalize_text(edge.get("target_table")),
                    _normalize_identifier(edge.get("target_column")),
                )
                if target_pair in accepted_pairs:
                    accepted_pairs.add(source_pair)
                if source_pair in accepted_pairs:
                    accepted_pairs.add(target_pair)

            accepted_columns = {_normalize_identifier(column) for _, column in accepted_pairs}
            if any(
                (
                    (_normalize_text(filter_info["table"]), _normalize_identifier(filter_info["column"])) in accepted_pairs
                    or (
                        not _clean_text(filter_info["table"])
                        and _normalize_identifier(filter_info["column"]) in accepted_columns
                    )
                )
                for filter_info in relevant_filters
            ):
                continue

            observed_targets = [
                f"{filter_info['table']}.{filter_info['column']}" if filter_info["table"] else filter_info["column"]
                for filter_info in relevant_filters
            ]
            flags.append(
                {
                    "type": "entity_filter_mismatch",
                    "severity": "high",
                    "message": (
                        f"Question value '{question_value}' matched {matched_table}.{matched_column}, "
                        f"but SQL filters {', '.join(observed_targets)}."
                    ),
                    "repair_hint": (
                        f"Re-check where '{question_value}' should be filtered. Prefer {matched_table}.{matched_column} "
                        "or an FK-equivalent code column, and avoid comparing the literal on unrelated columns."
                    ),
                }
            )

        return flags

    def _check_group_projection_mismatch(self, question, sql, schema_meta):
        flags = []
        select_exprs = _extract_select_expressions(sql)
        group_exprs = _extract_group_expressions(sql)
        order_expr = _extract_order_by_clause(sql)
        has_group = bool(group_exprs)
        has_count = any(_contains_count(expr) for expr in select_exprs)

        if _question_requests_entity_only(question) and has_count and len(select_exprs) > 1:
            flags.append(
                {
                    "type": "projection_extra_count",
                    "severity": "medium",
                    "message": "Question asks for entity output, but SQL also projects COUNT(*).",
                    "repair_hint": "Only keep COUNT(*) in the final projection when the question explicitly asks to output the count itself.",
                }
            )

        q = _normalize_text(question)
        top1_count_entity_question = (
            has_count
            and len(select_exprs) > 1
            and any(token in q for token in ["which ", "what ", "who "])
            and any(token in q for token in ["most", "fewest", "least", "highest number", "lowest number"])
        )
        if top1_count_entity_question:
            flags.append(
                {
                    "type": "projection_extra_count",
                    "severity": "medium",
                    "message": "Top-1 entity question likely needs the entity only, but SQL also projects COUNT(*).",
                    "repair_hint": "For top-1 entity questions, keep COUNT(*) only for ranking unless the question explicitly asks to output the count.",
                }
            )

        if _question_requests_each(question) and not has_group:
            flags.append(
                {
                    "type": "missing_group_by",
                    "severity": "high",
                    "message": "Question implies per-group output, but SQL has no GROUP BY clause.",
                    "repair_hint": "The question asks for per-group results. Add a GROUP BY on the requested entity or grouping key.",
                }
            )

        if has_group:
            non_agg_identifiers = _extract_non_agg_identifiers(select_exprs)
            group_columns = {_normalize_identifier(expr) for expr in group_exprs}
            select_columns = {_normalize_identifier(column) for _, column in non_agg_identifiers}
            if select_columns and group_columns and select_columns.isdisjoint(group_columns):
                flags.append(
                    {
                        "type": "group_projection_mismatch",
                        "severity": "medium",
                        "message": "Non-aggregated selected columns do not align with GROUP BY columns.",
                        "repair_hint": "Re-check the grouping grain. Group by the same entity grain needed by the selected non-aggregated columns.",
                    }
                )

            if has_count and _question_requests_entity_only(question) and re.search(r"\bLIMIT\s+1\b", sql, flags=re.IGNORECASE):
                flags.append(
                    {
                        "type": "top1_projection_overcomplete",
                        "severity": "medium",
                        "message": "Top-1 entity question likely needs the entity only, but SQL still projects COUNT(*).",
                        "repair_hint": "For top-1 entity questions, use COUNT(*) only for ordering, not as an output column unless the question explicitly asks for it.",
                    }
                )

            if schema_meta:
                table_pk_map = {}
                for table_name, table_info in schema_meta.get("tables", {}).items():
                    pk_columns = [
                        _normalize_identifier(column["name"])
                        for column in table_info.get("columns", [])
                        if column.get("is_primary_key")
                    ]
                    if pk_columns:
                        table_pk_map[_normalize_text(table_name)] = set(pk_columns)

                select_pairs = {( _normalize_text(table), _normalize_identifier(column)) for table, column in non_agg_identifiers}
                group_pairs = set()
                for expr in group_exprs:
                    for table, column in _identifier_pairs(expr):
                        group_pairs.add((_normalize_text(table), _normalize_identifier(column)))

                for table_name, column_name in select_pairs:
                    pk_columns = table_pk_map.get(table_name, set())
                    if not pk_columns:
                        continue
                    if any(group_table == table_name and group_col in pk_columns for group_table, group_col in group_pairs):
                        continue
                    if any(group_table == table_name and group_col == column_name for group_table, group_col in group_pairs):
                        continue
                    if group_pairs and any(group_table == table_name for group_table, _ in group_pairs):
                        flags.append(
                            {
                                "type": "group_by_display_column",
                                "severity": "low",
                                "message": f"SQL groups {table_name} by a display column instead of its primary key.",
                                "repair_hint": "When counting per entity, prefer grouping by the entity primary key and selecting the display column alongside it.",
                            }
                        )
                        break

        if _question_requests_count(question) and not has_count and "count(" not in _normalize_text(order_expr):
            flags.append(
                {
                    "type": "missing_count_aggregation",
                    "severity": "medium",
                    "message": "Question asks for a count-like answer, but SQL does not project COUNT(*).",
                    "repair_hint": "The question asks for a number/count. Make sure the SQL computes COUNT(*) or another count aggregation when appropriate.",
                }
            )

        return flags

    def _check_setop_semantics(self, question, sql):
        flags = []
        normalized_sql = _normalize_text(sql)
        select_exprs = _extract_select_expressions(sql)
        select_has_count = any(_contains_count(expr) for expr in select_exprs)

        if _question_has_both(question):
            if " intersect " in f" {normalized_sql} " and select_has_count:
                flags.append(
                    {
                        "type": "setop_on_aggregate_risk",
                        "severity": "high",
                        "message": "Question asks for overlap/both semantics, but SQL intersects aggregate counts instead of entity sets.",
                        "repair_hint": "For 'both' questions, intersect the entity set first, then count or project from that set if needed.",
                    }
                )
            elif " intersect " not in f" {normalized_sql} " and " and " not in f" {normalized_sql} ":
                flags.append(
                    {
                        "type": "both_semantics_risk",
                        "severity": "medium",
                        "message": "Question mentions 'both', but SQL does not clearly encode overlap semantics.",
                        "repair_hint": "Re-check whether the question requires an INTERSECT, dual membership condition, or two constrained subqueries.",
                    }
                )

        if " intersect " in f" {normalized_sql} " and select_has_count:
            flags.append(
                {
                    "type": "setop_on_aggregate_risk",
                    "severity": "high",
                    "message": "SQL intersects aggregate counts directly, which is often a sign that set semantics were applied at the wrong level.",
                    "repair_hint": "Apply INTERSECT/EXCEPT on the entity set first, then aggregate if the question asks for a count.",
                }
            )

        if _question_has_negation(question):
            has_negation_structure = any(
                token in f" {normalized_sql} "
                for token in [" except ", " not in ", " not exists ", " left join "]
            )
            if not has_negation_structure and "!=" not in normalized_sql and "<>" not in normalized_sql:
                flags.append(
                    {
                        "type": "negation_semantics_risk",
                        "severity": "medium",
                        "message": "Question contains negation/set-difference cues, but SQL has no clear negation structure.",
                        "repair_hint": "Re-check whether the question needs EXCEPT, NOT IN, NOT EXISTS, or another set-difference pattern.",
                    }
                )

        return flags
