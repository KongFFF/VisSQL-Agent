import argparse
import json
from collections import Counter
from pathlib import Path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate a static explainability dashboard for a GroundedSQL-Agent experiment."
    )
    parser.add_argument(
        "--experiment-dir",
        required=True,
        help="Experiment directory containing agent_run_summary.jsonl and optional agent_trajectories.jsonl.",
    )
    parser.add_argument(
        "--dashboard-dir",
        default=None,
        help="Output dashboard directory. Defaults to <experiment-dir>/dashboard.",
    )
    parser.add_argument(
        "--title",
        default=None,
        help="Optional dashboard title. Defaults to 'GroundedSQL-Agent Dashboard · <experiment-name>'.",
    )
    return parser.parse_args()


def load_jsonl(path: Path):
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def load_json(path: Path):
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def make_jsonable(value):
    if isinstance(value, dict):
        return {str(k): make_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [make_jsonable(v) for v in value]
    return value


def build_overview(summary_rows, metrics=None):
    total = len(summary_rows)
    if total == 0:
        return {}

    success_count = sum(1 for row in summary_rows if row.get("is_success"))
    route_counts = Counter(row.get("route", "<missing>") for row in summary_rows)
    pattern_reason_counts = Counter(
        row.get("pattern_reason") for row in summary_rows if row.get("pattern_reason")
    )
    pattern_template_counts = Counter(
        row.get("pattern_template") for row in summary_rows if row.get("pattern_template")
    )
    skill_rows = [row for row in summary_rows if row.get("route") == "superlative_pattern"]
    pattern_signal_rows = [
        row
        for row in summary_rows
        if row.get("pattern_reason") not in (None, "not_superlative")
        or row.get("pattern_template")
    ]
    generic_pattern_rows = [
        row
        for row in summary_rows
        if row.get("route") == "generic_llm"
        and row.get("pattern_reason") not in (None, "not_superlative")
    ]

    avg_attempts = sum(row.get("attempts", 0) for row in summary_rows) / total
    reflexion_count = sum(1 for row in summary_rows if row.get("had_reflexion"))
    probe_count = sum(1 for row in summary_rows if row.get("had_probe"))
    fallback_count = sum(1 for row in summary_rows if row.get("used_success_fallback"))

    return {
        "total_questions": total,
        "official_metrics": metrics,
        "execution_success_count": success_count,
        "execution_success_rate": round(success_count / total, 4),
        "average_attempts": round(avg_attempts, 3),
        "had_reflexion_count": reflexion_count,
        "had_reflexion_rate": round(reflexion_count / total, 4),
        "had_probe_count": probe_count,
        "had_probe_rate": round(probe_count / total, 4),
        "success_fallback_count": fallback_count,
        "success_fallback_rate": round(fallback_count / total, 4),
        "route_counts": dict(route_counts),
        "route_rates": {key: round(val / total, 4) for key, val in route_counts.items()},
        "skill_coverage_count": len(skill_rows),
        "skill_coverage_rate": round(len(skill_rows) / total, 4),
        "pattern_signal_count": len(pattern_signal_rows),
        "pattern_signal_rate": round(len(pattern_signal_rows) / total, 4),
        "skill_execution_success_count": sum(1 for row in skill_rows if row.get("is_success")),
        "skill_execution_success_rate": round(
            sum(1 for row in skill_rows if row.get("is_success")) / len(skill_rows), 4
        )
        if skill_rows
        else None,
        "pattern_template_counts": dict(pattern_template_counts),
        "pattern_reason_counts": dict(pattern_reason_counts),
        "generic_pattern_reason_counts": dict(Counter(row.get("pattern_reason") for row in generic_pattern_rows)),
    }


def build_cases(summary_rows, trajectory_map):
    cases = []
    for row in summary_rows:
        idx = row["question_index"]
        trajectory = trajectory_map.get(idx)
        agent_result = trajectory.get("agent_result") if trajectory else None
        attempt_records = agent_result.get("attempt_records", []) if agent_result else []
        probe_logs = agent_result.get("probe_logs", []) if agent_result else []

        cases.append(
            {
                "question_index": idx,
                "db_id": row.get("db_id"),
                "question": row.get("question"),
                "gold_sql": row.get("gold_sql"),
                "final_sql": row.get("final_sql"),
                "route": row.get("route"),
                "is_success": row.get("is_success"),
                "attempts": row.get("attempts"),
                "had_reflexion": row.get("had_reflexion"),
                "had_probe": row.get("had_probe"),
                "probe_scenarios": row.get("probe_scenarios"),
                "final_failure_type": row.get("final_failure_type"),
                "final_row_count": row.get("final_row_count"),
                "execution_time_sec": row.get("execution_time_sec"),
                "superlative_mode": row.get("superlative_mode"),
                "pattern_reason": row.get("pattern_reason"),
                "pattern_template": row.get("pattern_template"),
                "pattern_candidate_templates": row.get("pattern_candidate_templates"),
                "pattern_router_decision": make_jsonable(row.get("pattern_router_decision")),
                "schema_selected_tables": row.get("schema_selected_tables"),
                "schema_seed_tables": row.get("schema_seed_tables"),
                "schema_selected_foreign_keys": row.get("schema_selected_foreign_keys"),
                "schema_join_paths": row.get("schema_join_paths"),
                "schema_bridge_completion_enabled": row.get("schema_bridge_completion_enabled"),
                "schema_bridge_anchor_tables": row.get("schema_bridge_anchor_tables"),
                "schema_bridge_paths": row.get("schema_bridge_paths"),
                "schema_bridge_added_tables": row.get("schema_bridge_added_tables"),
                "schema_retrieval_explanation": make_jsonable(
                    row.get("schema_retrieval_explanation")
                ),
                "schema_column_hints_enabled": row.get("schema_column_hints_enabled"),
                "schema_column_hint_columns": row.get("schema_column_hint_columns"),
                "schema_value_hints_enabled": row.get("schema_value_hints_enabled"),
                "schema_value_hint_question_entities": row.get(
                    "schema_value_hint_question_entities"
                ),
                "schema_value_hint_entity_matches": row.get(
                    "schema_value_hint_entity_matches"
                ),
                "schema_value_hint_sampled_values": row.get(
                    "schema_value_hint_sampled_values"
                ),
                "schema_value_hint_candidate_columns": row.get(
                    "schema_value_hint_candidate_columns"
                ),
                "schema_table_scores_lexical": row.get("schema_table_scores_lexical"),
                "schema_table_column_boosts": row.get("schema_table_column_boosts"),
                "schema_column_scores": row.get("schema_column_scores"),
                "semantic_retry_count": row.get("semantic_retry_count"),
                "final_verifier_result": make_jsonable(row.get("final_verifier_result")),
                "used_success_fallback": row.get("used_success_fallback"),
                "success_fallback_reason": row.get("success_fallback_reason"),
                "selected_success_attempt": row.get("selected_success_attempt"),
                "attempt_records": make_jsonable(attempt_records),
                "probe_logs": make_jsonable(probe_logs),
            }
        )
    return cases


def write_data_json(dashboard_dir: Path, payload: dict):
    data_path = dashboard_dir / "data.json"
    with data_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)


def build_html(title: str, embedded_data_json: str):
    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>__TITLE__</title>
  <style>
    :root {
      --bg: #f6f7f9;
      --card: #ffffff;
      --ink: #1f2937;
      --muted: #6b7280;
      --line: #e5e7eb;
      --accent: #0f766e;
      --accent-soft: #ccfbf1;
      --ok: #166534;
      --ok-soft: #dcfce7;
      --bad: #991b1b;
      --bad-soft: #fee2e2;
      --warn: #92400e;
      --warn-soft: #fef3c7;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background: var(--bg);
      color: var(--ink);
    }
    .page {
      display: grid;
      grid-template-columns: 360px 1fr;
      min-height: 100vh;
    }
    .sidebar {
      border-right: 1px solid var(--line);
      background: #fbfbfc;
      padding: 18px 16px;
      overflow-y: auto;
    }
    .content {
      padding: 20px;
      overflow-y: auto;
    }
    h1, h2, h3 {
      margin: 0 0 10px;
      line-height: 1.2;
    }
    .muted { color: var(--muted); }
    .card {
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 16px;
      margin-bottom: 16px;
      box-shadow: 0 4px 18px rgba(15, 23, 42, 0.04);
    }
    .metrics {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
    }
    .metric {
      background: linear-gradient(180deg, #ffffff, #f8fafc);
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px 14px;
    }
    .metric .label {
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
    }
    .metric .value {
      font-size: 24px;
      font-weight: 700;
    }
    .pill {
      display: inline-block;
      padding: 3px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 600;
      margin-right: 6px;
      margin-bottom: 6px;
      border: 1px solid transparent;
    }
    .pill.ok { color: var(--ok); background: var(--ok-soft); }
    .pill.bad { color: var(--bad); background: var(--bad-soft); }
    .pill.warn { color: var(--warn); background: var(--warn-soft); }
    .pill.accent { color: var(--accent); background: var(--accent-soft); }
    .chips { margin-top: 6px; }
    input, select {
      width: 100%;
      padding: 10px 12px;
      border-radius: 10px;
      border: 1px solid var(--line);
      margin-bottom: 10px;
      font: inherit;
      background: #fff;
    }
    .case-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
      max-height: calc(100vh - 280px);
      overflow-y: auto;
    }
    .case-item {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 12px;
      cursor: pointer;
      background: #fff;
    }
    .case-item:hover { border-color: #cbd5e1; }
    .case-item.active {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(15, 118, 110, 0.1);
    }
    .case-item .title {
      font-size: 13px;
      font-weight: 700;
      margin-bottom: 6px;
    }
    .case-item .subtitle {
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 8px;
    }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      background: #0f172a;
      color: #e2e8f0;
      border-radius: 12px;
      padding: 12px;
      overflow-x: auto;
      font-size: 12px;
      line-height: 1.5;
      margin: 0;
    }
    .grid-two {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 16px;
    }
    .kv {
      display: grid;
      grid-template-columns: 180px 1fr;
      gap: 8px;
      align-items: start;
      margin-bottom: 8px;
      font-size: 14px;
    }
    .kv .k {
      color: var(--muted);
      font-weight: 600;
    }
    .section-title {
      margin-bottom: 10px;
      font-size: 16px;
      font-weight: 700;
    }
    .table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    .table th, .table td {
      border-bottom: 1px solid var(--line);
      padding: 8px 6px;
      text-align: left;
      vertical-align: top;
    }
    .tag-list {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }
    .tag {
      display: inline-block;
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 12px;
      background: #eef2ff;
      color: #374151;
      border: 1px solid #dbe3f4;
    }
    .stack {
      display: grid;
      gap: 10px;
    }
    .mini-card {
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px 12px;
      background: #fafafa;
    }
    .mini-card-title {
      font-size: 12px;
      color: var(--muted);
      margin-bottom: 6px;
      font-weight: 600;
    }
    details {
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fff;
      padding: 8px 10px;
    }
    details summary {
      cursor: pointer;
      font-weight: 600;
      color: var(--muted);
      outline: none;
    }
    .small { font-size: 12px; }
    @media (max-width: 1100px) {
      .page { grid-template-columns: 1fr; }
      .sidebar { border-right: none; border-bottom: 1px solid var(--line); }
      .grid-two { grid-template-columns: 1fr; }
      .case-list { max-height: none; }
    }
  </style>
</head>
<body>
  <div class="page">
    <aside class="sidebar">
      <div class="card">
        <h1>__TITLE__</h1>
        <div class="muted small" id="meta"></div>
      </div>

      <div class="card">
        <h3>筛选</h3>
        <input id="searchInput" placeholder="搜索题号 / db_id / 问题关键词" />
        <select id="routeFilter">
          <option value="">全部 route</option>
          <option value="superlative_pattern">superlative_pattern</option>
          <option value="generic_llm">generic_llm</option>
        </select>
        <select id="successFilter">
          <option value="">全部结果</option>
          <option value="true">只看成功</option>
          <option value="false">只看失败</option>
        </select>
        <select id="patternFilter">
          <option value="">全部 pattern 情况</option>
          <option value="applied">Skill 命中</option>
          <option value="signal">有 pattern signal</option>
          <option value="fallback">Pattern fallback</option>
          <option value="value">启用 value hints</option>
          <option value="fallback_used">使用 success fallback</option>
        </select>
      </div>

      <div class="card">
        <h3>题目列表</h3>
        <div class="muted small" id="caseCount"></div>
        <div class="case-list" id="caseList"></div>
      </div>
    </aside>

    <main class="content">
      <div class="card">
        <div class="section-title">总览</div>
        <div class="metrics" id="metrics"></div>
      </div>

      <div class="grid-two">
        <div class="card">
          <div class="section-title">Route / Skill 指标</div>
          <div id="routeStats"></div>
        </div>
        <div class="card">
          <div class="section-title">Pattern / Template 分布</div>
          <div id="patternStats"></div>
        </div>
      </div>

      <div class="card">
        <div class="section-title">案例详情</div>
        <div id="caseDetail" class="muted">请先从左侧选择一道题。</div>
      </div>
    </main>
  </div>

  <script id="dashboard-data" type="application/json">__DATA__</script>
  <script>
    const state = {
      data: null,
      filteredCases: [],
      selectedIndex: null,
    };

    function escapeHtml(value) {
      if (value === null || value === undefined) return '';
      return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
    }

    function formatPercent(value) {
      if (value === null || value === undefined) return '-';
      return (value * 100).toFixed(1) + '%';
    }

    function renderDictTable(obj) {
      const rows = Object.entries(obj || {});
      if (!rows.length) return '<div class="muted">暂无数据。</div>';
      return `
        <table class="table">
          <thead><tr><th>项目</th><th>值</th></tr></thead>
          <tbody>
            ${rows.map(([k, v]) => `<tr><td>${escapeHtml(k)}</td><td>${escapeHtml(typeof v === 'object' ? JSON.stringify(v) : v)}</td></tr>`).join('')}
          </tbody>
        </table>
      `;
    }

    function renderOverview() {
      const data = state.data;
      const overview = data.overview;
      const official = overview.official_metrics || null;
      document.getElementById('meta').innerHTML = `
        实验目录：<code>${escapeHtml(data.meta.experiment_dir_name)}</code><br/>
        题目数量：<strong>${overview.total_questions}</strong>
      `;

      const metrics = [];
      if (official) {
        metrics.push(
          ['官方 EX 准确率', official.execution_accuracy == null ? '-' : formatPercent(official.execution_accuracy)],
          ['官方 Exact 准确率', official.exact_match_accuracy == null ? '-' : formatPercent(official.exact_match_accuracy)],
          ['Easy EX', official.difficulty_breakdown?.easy == null ? '-' : formatPercent(official.difficulty_breakdown.easy)],
          ['Medium EX', official.difficulty_breakdown?.medium == null ? '-' : formatPercent(official.difficulty_breakdown.medium)],
          ['Hard EX', official.difficulty_breakdown?.hard == null ? '-' : formatPercent(official.difficulty_breakdown.hard)],
          ['Extra EX', official.difficulty_breakdown?.extra == null ? '-' : formatPercent(official.difficulty_breakdown.extra)],
        );
      }
      metrics.push(
        ['SQL 运行成功率', formatPercent(overview.execution_success_rate)],
        ['Skill 覆盖率', formatPercent(overview.skill_coverage_rate)],
        ['Pattern Signal 覆盖率', formatPercent(overview.pattern_signal_rate)],
        ['平均尝试次数', overview.average_attempts],
        ['Reflexion 比例', formatPercent(overview.had_reflexion_rate)],
        ['Probe 比例', formatPercent(overview.had_probe_rate)],
        ['Success Fallback 比例', formatPercent(overview.success_fallback_rate)],
        ['Skill 运行成功率', overview.skill_execution_success_rate === null ? '-' : formatPercent(overview.skill_execution_success_rate)],
      );

      document.getElementById('metrics').innerHTML = metrics.map(([label, value]) => `
        <div class="metric">
          <div class="label">${label}</div>
          <div class="value">${value}</div>
        </div>
      `).join('');

      document.getElementById('routeStats').innerHTML = renderDictTable({
        ...overview.route_counts,
        ...Object.fromEntries(Object.entries(overview.route_rates).map(([k, v]) => [`${k} rate`, formatPercent(v)])),
      });

      const patternLines = {
        ...Object.fromEntries(Object.entries(overview.pattern_template_counts || {}).map(([k, v]) => [`template:${k}`, v])),
        ...Object.fromEntries(Object.entries(overview.generic_pattern_reason_counts || {}).slice(0, 10).map(([k, v]) => [`fallback:${k}`, v])),
      };
      document.getElementById('patternStats').innerHTML = renderDictTable(patternLines);
    }

    function getFilters() {
      return {
        search: document.getElementById('searchInput').value.trim().toLowerCase(),
        route: document.getElementById('routeFilter').value,
        success: document.getElementById('successFilter').value,
        pattern: document.getElementById('patternFilter').value,
      };
    }

    function applyFilters() {
      const filters = getFilters();
      const cases = state.data.cases.filter((item) => {
        const haystack = `${item.question_index} ${item.db_id || ''} ${item.question || ''}`.toLowerCase();
        if (filters.search && !haystack.includes(filters.search)) return false;
        if (filters.route && item.route !== filters.route) return false;
        if (filters.success && String(Boolean(item.is_success)) !== filters.success) return false;
        if (filters.pattern === 'applied' && item.route !== 'superlative_pattern') return false;
        if (filters.pattern === 'signal' && !((item.pattern_reason && item.pattern_reason !== 'not_superlative') || item.pattern_template)) return false;
        if (filters.pattern === 'fallback' && !(item.route === 'generic_llm' && item.pattern_reason && item.pattern_reason !== 'not_superlative')) return false;
        if (filters.pattern === 'value' && !item.schema_value_hints_enabled) return false;
        if (filters.pattern === 'fallback_used' && !item.used_success_fallback) return false;
        return true;
      });
      state.filteredCases = cases;
      if (!cases.find((item) => item.question_index === state.selectedIndex)) {
        state.selectedIndex = cases.length ? cases[0].question_index : null;
      }
      renderCaseList();
      renderCaseDetail();
    }

    function renderCaseList() {
      const listEl = document.getElementById('caseList');
      const countEl = document.getElementById('caseCount');
      countEl.textContent = `当前筛选结果：${state.filteredCases.length} 题`;
      if (!state.filteredCases.length) {
        listEl.innerHTML = '<div class="muted">没有匹配的题目。</div>';
        return;
      }
      listEl.innerHTML = state.filteredCases.map((item) => {
        const active = item.question_index === state.selectedIndex ? 'active' : '';
        const pills = [
          `<span class="pill ${item.is_success ? 'ok' : 'bad'}">${item.is_success ? 'success' : 'failure'}</span>`,
          `<span class="pill accent">${escapeHtml(item.route || 'unknown')}</span>`,
        ];
        if (item.pattern_template) pills.push(`<span class="pill warn">${escapeHtml(item.pattern_template)}</span>`);
        if (item.used_success_fallback) pills.push('<span class="pill warn">fallback</span>');
        return `
          <div class="case-item ${active}" data-case-id="${item.question_index}">
            <div class="title">#${item.question_index} · ${escapeHtml(item.db_id || '')}</div>
            <div class="subtitle">${escapeHtml((item.question || '').slice(0, 90))}</div>
            <div class="chips">${pills.join('')}</div>
          </div>
        `;
      }).join('');

      listEl.querySelectorAll('.case-item').forEach((node) => {
        node.addEventListener('click', () => {
          state.selectedIndex = Number(node.dataset.caseId);
          renderCaseList();
          renderCaseDetail();
        });
      });
    }

    function buildKvRows(rows) {
      return rows.map(([k, v]) => `<div class="kv"><div class="k">${escapeHtml(k)}</div><div>${escapeHtml(v)}</div></div>`).join('');
    }

    function asPrettyJson(value) {
      return escapeHtml(JSON.stringify(value ?? null, null, 2));
    }

    function renderTagList(items, emptyText = '暂无') {
      const values = Array.isArray(items) ? items : [];
      if (!values.length) return `<div class="muted">${escapeHtml(emptyText)}</div>`;
      return `<div class="tag-list">${values.map((item) => `<span class="tag">${escapeHtml(item)}</span>`).join('')}</div>`;
    }

    function renderStringList(items, emptyText = '暂无') {
      const values = Array.isArray(items) ? items : [];
      if (!values.length) return `<div class="muted">${escapeHtml(emptyText)}</div>`;
      return `<div class="stack">${values.map((item) => `<div class="mini-card">${escapeHtml(item)}</div>`).join('')}</div>`;
    }

    function renderFkTable(items) {
      const values = Array.isArray(items) ? items : [];
      if (!values.length) return '<div class="muted">暂无外键信息。</div>';
      return `
        <table class="table">
          <thead><tr><th>源表.列</th><th>目标表.列</th></tr></thead>
          <tbody>
            ${values.map((item) => `
              <tr>
                <td>${escapeHtml(`${item.source_table || ''}.${item.source_column || ''}`)}</td>
                <td>${escapeHtml(`${item.target_table || ''}.${item.target_column || ''}`)}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    }

    function renderEntityMatches(items) {
      const values = Array.isArray(items) ? items : [];
      if (!values.length) return '<div class="muted">没有实体匹配。</div>';
      return `
        <table class="table">
          <thead><tr><th>值</th><th>候选列</th><th>分数</th></tr></thead>
          <tbody>
            ${values.map((item) => `
              <tr>
                <td>${escapeHtml(item.value ?? item.entity ?? '-')}</td>
                <td>${escapeHtml(item.column ? `${item.table || ''}.${item.column}` : (item.target || '-'))}</td>
                <td>${escapeHtml(item.score ?? '-')}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    }

    function renderSampledValues(items) {
      const values = Array.isArray(items) ? items : [];
      if (!values.length) return '<div class="muted">没有采样值。</div>';
      return `
        <div class="stack">
          ${values.map((item) => `
            <div class="mini-card">
              <div class="mini-card-title">${escapeHtml(`${item.table || ''}.${item.column || ''}`)}</div>
              ${renderTagList(item.values || [], '无采样值')}
            </div>
          `).join('')}
        </div>
      `;
    }

    function renderCandidateColumns(items) {
      const values = Array.isArray(items) ? items : [];
      if (!values.length) return '<div class="muted">没有候选列。</div>';
      return `
        <table class="table">
          <thead><tr><th>列</th><th>分数</th></tr></thead>
          <tbody>
            ${values.map((item) => `
              <tr>
                <td>${escapeHtml(`${item.table || ''}.${item.column || ''}`)}</td>
                <td>${escapeHtml(item.score ?? '-')}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    }

    function renderRouterDecision(decision) {
      if (!decision || typeof decision !== 'object') {
        return '<div class="muted">没有路由决策信息。</div>';
      }
      const rows = [
        ['selected_template', decision.selected_template ?? '-'],
        ['use_template_score', decision.use_template_score ?? '-'],
        ['selected_template_score', decision.selected_template_score ?? '-'],
        ['route_score', decision.route_score ?? '-'],
        ['reason', decision.reason ?? '-'],
      ];
      return `
        ${buildKvRows(rows)}
        <details>
          <summary>展开原始路由 JSON</summary>
          <pre>${asPrettyJson(decision)}</pre>
        </details>
      `;
    }

    function renderVerifier(verifier) {
      if (!verifier || typeof verifier !== 'object') {
        return '<div class="muted">没有 verifier 信息。</div>';
      }
      const flags = Array.isArray(verifier.risk_flags) ? verifier.risk_flags : [];
      const hints = Array.isArray(verifier.repair_hints) ? verifier.repair_hints : [];
      return `
        ${buildKvRows([
          ['score', verifier.score ?? '-'],
          ['should_retry', verifier.should_retry ?? '-'],
        ])}
        <div class="mini-card" style="margin-bottom:10px;">
          <div class="mini-card-title">Risk Flags</div>
          ${flags.length ? renderDictTable(Object.fromEntries(flags.map((f, i) => [`${i + 1}. ${f.type || 'flag'}`, `${f.severity || '-'} | ${f.message || '-'}`]))) : '<div class="muted">没有风险标记。</div>'}
        </div>
        <div class="mini-card">
          <div class="mini-card-title">Repair Hints</div>
          ${renderStringList(hints, '没有修复提示。')}
        </div>
      `;
    }

    function renderRetrievalExplanation(explanation) {
      if (!explanation || typeof explanation !== 'object') {
        return '<div class="muted">没有 explanation 信息。</div>';
      }
      const tableRationales = Array.isArray(explanation.table_rationales) ? explanation.table_rationales : [];
      const ambiguities = Array.isArray(explanation.ambiguities) ? explanation.ambiguities : [];
      return `
        ${buildKvRows([
          ['confidence', explanation.confidence ?? '-'],
          ['table rationales', tableRationales.length],
          ['ambiguities', ambiguities.length],
        ])}
        <details>
          <summary>展开 explanation 详情</summary>
          <pre>${asPrettyJson(explanation)}</pre>
        </details>
      `;
    }

    function renderCaseDetail() {
      const detail = document.getElementById('caseDetail');
      const item = state.filteredCases.find((row) => row.question_index === state.selectedIndex);
      if (!item) {
        detail.innerHTML = '<div class="muted">当前筛选条件下没有题目。</div>';
        return;
      }

      const summaryRows = [
        ['题号', item.question_index],
        ['数据库', item.db_id],
        ['Route', item.route],
        ['Pattern Template', item.pattern_template || '-'],
        ['Pattern Reason', item.pattern_reason || '-'],
        ['是否成功', item.is_success],
        ['尝试次数', item.attempts],
        ['是否触发 Reflexion', item.had_reflexion],
        ['是否触发 Probe', item.had_probe],
        ['语义重试次数', item.semantic_retry_count],
        ['是否使用 Success Fallback', item.used_success_fallback],
        ['Fallback 原因', item.success_fallback_reason || '-'],
        ['执行时间（秒）', item.execution_time_sec ?? '-'],
        ['结果行数', item.final_row_count ?? '-'],
      ];

      detail.innerHTML = `
        <div class="card">
          <div class="section-title">问题与结果</div>
          <div class="kv"><div class="k">问题</div><div>${escapeHtml(item.question || '')}</div></div>
          <div class="kv"><div class="k">Gold SQL</div><div><pre>${escapeHtml(item.gold_sql || '')}</pre></div></div>
          <div class="kv"><div class="k">Final SQL</div><div><pre>${escapeHtml(item.final_sql || '')}</pre></div></div>
          ${buildKvRows(summaryRows)}
        </div>

        <div class="grid-two">
          <div class="card">
            <div class="section-title">Retrieval</div>
            <div class="kv"><div class="k">Seed Tables</div><div>${renderTagList(item.schema_seed_tables || [], '没有 seed tables')}</div></div>
            <div class="kv"><div class="k">Selected Tables</div><div>${renderTagList(item.schema_selected_tables || [], '没有 selected tables')}</div></div>
            <div class="kv"><div class="k">Selected FKs</div><div>${renderFkTable(item.schema_selected_foreign_keys || [])}</div></div>
            <div class="kv"><div class="k">Join Paths</div><div>${renderStringList(item.schema_join_paths || [], '没有 join path')}</div></div>
            <div class="kv"><div class="k">Explanation</div><div>${renderRetrievalExplanation(item.schema_retrieval_explanation || {})}</div></div>
          </div>

          <div class="card">
            <div class="section-title">Value Grounding</div>
            <div class="kv"><div class="k">是否启用 Value Hints</div><div>${escapeHtml(item.schema_value_hints_enabled)}</div></div>
            <div class="kv"><div class="k">问题实体</div><div>${renderTagList(item.schema_value_hint_question_entities || [], '没有识别到实体')}</div></div>
            <div class="kv"><div class="k">实体匹配</div><div>${renderEntityMatches(item.schema_value_hint_entity_matches || [])}</div></div>
            <div class="kv"><div class="k">采样值</div><div>${renderSampledValues(item.schema_value_hint_sampled_values || [])}</div></div>
            <div class="kv"><div class="k">候选列</div><div>${renderCandidateColumns(item.schema_value_hint_candidate_columns || [])}</div></div>
          </div>
        </div>

        <div class="grid-two">
          <div class="card">
            <div class="section-title">Pattern / Skill Route</div>
            <div class="kv"><div class="k">候选模板</div><div>${renderTagList(item.pattern_candidate_templates || [], '没有候选模板')}</div></div>
            <div class="kv"><div class="k">路由决策</div><div>${renderRouterDecision(item.pattern_router_decision || {})}</div></div>
          </div>

          <div class="card">
            <div class="section-title">Verifier / Fallback</div>
            <div class="kv"><div class="k">最终 Verifier 结果</div><div>${renderVerifier(item.final_verifier_result || {})}</div></div>
            <div class="kv"><div class="k">是否使用 Success Fallback</div><div>${escapeHtml(item.used_success_fallback)}</div></div>
            <div class="kv"><div class="k">选中的成功尝试</div><div>${escapeHtml(item.selected_success_attempt ?? '-')}</div></div>
          </div>
        </div>

        <div class="grid-two">
          <div class="card">
            <div class="section-title">Column Signals</div>
            <div class="kv"><div class="k">是否启用 Column Hints</div><div>${escapeHtml(item.schema_column_hints_enabled)}</div></div>
            <div class="kv"><div class="k">Hint 列</div><div>${renderTagList((item.schema_column_hint_columns || []).map((x) => x.table && x.column ? `${x.table}.${x.column}` : JSON.stringify(x)), '没有 hint 列')}</div></div>
            <div class="kv"><div class="k">表词面分数</div><div><details><summary>展开表词面分数</summary><pre>${asPrettyJson(item.schema_table_scores_lexical || [])}</pre></details></div></div>
            <div class="kv"><div class="k">列增益分数</div><div><details><summary>展开列增益分数</summary><pre>${asPrettyJson(item.schema_table_column_boosts || [])}</pre></details></div></div>
            <div class="kv"><div class="k">列分数</div><div><details><summary>展开列分数</summary><pre>${asPrettyJson(item.schema_column_scores || [])}</pre></details></div></div>
          </div>

          <div class="card">
            <div class="section-title">Attempts / Probe Logs</div>
            <div class="kv"><div class="k">尝试记录</div><div><details><summary>展开尝试记录</summary><pre>${asPrettyJson(item.attempt_records || [])}</pre></details></div></div>
            <div class="kv"><div class="k">Probe 日志</div><div><details><summary>展开 Probe 日志</summary><pre>${asPrettyJson(item.probe_logs || [])}</pre></details></div></div>
          </div>
        </div>
      `;
    }

    function bootstrap() {
      const embedded = document.getElementById('dashboard-data');
      if (!embedded || !embedded.textContent.trim()) {
        throw new Error('Embedded dashboard data is missing.');
      }
      state.data = JSON.parse(embedded.textContent);
      renderOverview();

      ['searchInput', 'routeFilter', 'successFilter', 'patternFilter'].forEach((id) => {
        document.getElementById(id).addEventListener('input', applyFilters);
        document.getElementById(id).addEventListener('change', applyFilters);
      });

      applyFilters();
    }

    try {
      bootstrap();
    } catch (err) {
      document.getElementById('caseDetail').innerHTML = `<pre>${escapeHtml(String(err))}</pre>`;
    }
  </script>
</body>
</html>
"""
    return html.replace("__TITLE__", title).replace("__DATA__", embedded_data_json)


def main():
    args = parse_args()
    experiment_dir = Path(args.experiment_dir).resolve()
    dashboard_dir = (
        Path(args.dashboard_dir).resolve()
        if args.dashboard_dir
        else experiment_dir / "dashboard"
    )
    dashboard_dir.mkdir(parents=True, exist_ok=True)

    summary_path = experiment_dir / "agent_run_summary.jsonl"
    trajectory_path = experiment_dir / "agent_trajectories.jsonl"
    metrics_path = experiment_dir / "metrics.json"
    if not summary_path.exists():
        raise SystemExit(f"Missing summary file: {summary_path}")

    summary_rows = load_jsonl(summary_path)
    trajectory_rows = load_jsonl(trajectory_path)
    metrics = load_json(metrics_path)
    trajectory_map = {
        row["question_index"]: row for row in trajectory_rows if "question_index" in row
    }

    title = args.title or f"GroundedSQL-Agent Dashboard · {experiment_dir.name}"
    payload = {
        "meta": {
            "title": title,
            "experiment_dir_name": experiment_dir.name,
            "experiment_dir": str(experiment_dir),
            "summary_file": summary_path.name,
            "trajectory_file": trajectory_path.name if trajectory_path.exists() else None,
            "metrics_file": metrics_path.name if metrics_path.exists() else None,
        },
        "overview": build_overview(summary_rows, metrics=metrics),
        "cases": build_cases(summary_rows, trajectory_map),
    }

    write_data_json(dashboard_dir, payload)
    embedded_data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html_path = dashboard_dir / "index.html"
    html_path.write_text(build_html(title, embedded_data_json), encoding="utf-8")

    print(f"Dashboard written to: {html_path}")


if __name__ == "__main__":
    main()
