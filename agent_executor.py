import re
import sqlite3
import time
from pathlib import Path

class SQLSandbox:
    def __init__(self, db_path: str, timeout: int = 5):
        """
        初始化沙盒执行器。
        :param db_path: SQLite 数据库文件的物理路径
        :param timeout: 数据库锁超时时间（防止死锁）
        """
        self.db_path = db_path
        self.timeout = timeout
        # 诊断探针构建器
        self.diagnostic_probe_builders = {
            "empty_result": self._build_empty_result_probes
        }

    def execute_query(self, sql_query: str, row_limit: int = 10) -> dict:
        """
        核心执行引擎：带有极强防御性编程的 SQL 执行器。
        :param sql_query: 大模型生成的 SQL 字符串
        :param row_limit: 限制最大返回行数，【极其关键】防止查询结果太大撑爆大模型的上下文窗口！
        """
        # 清理可能存在的代码块标记 (Markdown 格式)
        clean_sql = sql_query.replace("```sql", "").replace("```", "").strip()
        
        start_time = time.time()

        if not self._is_read_only_query(clean_sql):
            return {
                "status": "error",
                "sql_executed": clean_sql,
                "error_type": "PermissionError",
                "error_msg": "沙盒中只允许执行只读的 SELECT/WITH 查询。"
            }
        
        try:
            # 1. 建立连接（开启 URI 模式可支持只读，此处为标准模式）
            conn = self._connect_read_only()
            cursor = conn.cursor()

            # 2. 尝试执行大模型写的 SQL
            cursor.execute(clean_sql)
            
            # 3. 提取列名和数据
            # 注意：对于没有返回值的操作（如 INSERT），description 会是 None
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            
            # 【架构师细节】：绝不能用 fetchall()！如果模型写了个笛卡尔积查出10万条数据，
            # 你的 Agent 内存会当场崩盘，Token 也会直接爆炸。必须用 fetchmany()。
            results = cursor.fetchmany(row_limit) 
            
            conn.close()
            execution_time = time.time() - start_time

            # 4. 组装“成功战报”
            return {
                "status": "success",
                "sql_executed": clean_sql,
                "columns": columns,
                "results": results,
                "row_count": len(results),
                "execution_time_sec": round(execution_time, 3)
            }

        except sqlite3.Error as e:
            # 【Reflexion 的灵魂】：精准捕获数据库原生报错！
            # 这是将来喂给大模型让她自我反思的“救命稻草”。
            return {
                "status": "error",
                "sql_executed": clean_sql,
                "error_type": type(e).__name__,
                "error_msg": str(e)
            }
            
        except Exception as e:
            # 兜底防御：防止 Python 级别的其他报错搞崩整个系统
            return {
                "status": "error",
                "sql_executed": clean_sql,
                "error_type": "SystemError",
                "error_msg": str(e)
            }

    def run_diagnostic_probes(self, sql_query: str, scenario: str, row_limit: int = 10) -> dict:
        """
        统一的诊断探针入口。
        后续新增“看样例行”“验证 JOIN”等场景时，只需要扩展 builder 即可。
        """
        builder = self.diagnostic_probe_builders.get(scenario)
        if builder is None:
            return {
                "scenario": scenario,
                "probes": [],
                "summary": f"当前场景 {scenario} 尚未注册任何诊断探针。"
            }

        probe_requests = builder(sql_query)
        probe_results = []

        for probe in probe_requests:
            probe_sql = probe["sql"].strip()
            try:
                conn = self._connect_read_only()
                cursor = conn.cursor()
                cursor.execute(probe_sql)
                rows = cursor.fetchmany(row_limit)
                conn.close()

                probe_results.append({
                    "status": "success",
                    "kind": probe["kind"],
                    "target": probe["target"],
                    "sql": probe_sql,
                    "rows": rows
                })
            except sqlite3.Error as e:
                probe_results.append({
                    "status": "error",
                    "kind": probe["kind"],
                    "target": probe["target"],
                    "sql": probe_sql,
                    "error": str(e)
                })

        return {
            "scenario": scenario,
            "probes": probe_results,
            "summary": self._summarize_probe_results(scenario, probe_results)
        }

    def _is_read_only_query(self, sql_query: str) -> bool:
        """
        只允许执行只读查询，避免模型误写出破坏性 SQL。
        """
        normalized_sql = re.sub(r"/\*.*?\*/", " ", sql_query, flags=re.DOTALL)
        normalized_sql = re.sub(r"--.*?$", " ", normalized_sql, flags=re.MULTILINE).strip()
        return bool(re.match(r"^(SELECT|WITH)\b", normalized_sql, re.IGNORECASE))

    def _connect_read_only(self):
        db_uri = f"file:{Path(self.db_path).resolve().as_posix()}?mode=ro"
        return sqlite3.connect(db_uri, timeout=self.timeout, uri=True)

    def _build_empty_result_probes(self, sql_query: str) -> list:
        """
        针对空结果场景，自动探测 WHERE 子句里与字面量比较的列的真实取值。
        """
        alias_map = self._extract_alias_map(sql_query)
        literal_filters = self._extract_literal_filters(sql_query)
        probe_requests = []
        seen_targets = set()

        for filter_info in literal_filters:
            table_name = alias_map.get(filter_info["table_or_alias"], filter_info["table_or_alias"])
            target = f"{table_name}.{filter_info['column']}"
            if target in seen_targets:
                continue

            seen_targets.add(target)
            probe_requests.append({
                "kind": "distinct_values",
                "target": target,
                "sql": f"SELECT DISTINCT {filter_info['column']} FROM {table_name} "
                       f"WHERE {filter_info['column']} IS NOT NULL LIMIT 10"
            })

        return probe_requests

    def _extract_alias_map(self, sql_query: str) -> dict:
        alias_map = {}
        pattern = r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w]*)\s*(?:AS\s+)?([A-Za-z_][\w]*)?"

        for table_name, alias in re.findall(pattern, sql_query, flags=re.IGNORECASE):
            alias_map[table_name] = table_name
            if alias:
                alias_map[alias] = table_name

        return alias_map

    def _extract_literal_filters(self, sql_query: str) -> list:
        filters = []
        pattern = (
            r"(?P<identifier>\b(?:[A-Za-z_][\w]*\.)?[A-Za-z_][\w]*\b)\s*=\s*"
            r"(?P<literal>'[^']*'|\"[^\"]*\"|-?\d+(?:\.\d+)?)"
        )

        for match in re.finditer(pattern, sql_query, flags=re.IGNORECASE):
            identifier = match.group("identifier")
            if "." in identifier:
                table_or_alias, column = identifier.split(".", 1)
            else:
                table_or_alias, column = identifier, identifier

            filters.append({
                "table_or_alias": table_or_alias,
                "column": column,
                "literal": match.group("literal")
            })

        return filters

    def _summarize_probe_results(self, scenario: str, probe_results: list) -> str:
        if not probe_results:
            return f"场景 {scenario} 下未采集到任何诊断证据。"

        summary_lines = [f"{scenario} 场景的诊断探针摘要："]

        for probe in probe_results:
            if probe["status"] == "success":
                observed_values = [row[0] for row in probe["rows"]]
                summary_lines.append(
                    f"- {probe['target']} 的去重取值样本：{observed_values}"
                )
            else:
                summary_lines.append(
                    f"- {probe['target']} 的探针执行失败：{probe['error']}"
                )

        return "\n".join(summary_lines)

# ==========================================
# 独立测试入口 (如果你直接运行这个脚本，它会执行这里的代码)
# ==========================================
if __name__ == "__main__":
    # 假设你有个 dev 数据库，替换成你真实的测试库路径
    test_db = "data/database/concert_singer/concert_singer.sqlite" 
    
    if not __import__("os").path.exists(test_db):
        print(f"⚠️ 找不到测试数据库: {test_db}，请检查路径。")
    else:
        sandbox = SQLSandbox(test_db)
        
        print(">>> 测试 1: 执行完美的 SQL")
        good_sql = "SELECT * FROM stadium LIMIT 2"
        print(sandbox.execute_query(good_sql))
        
        print("\n>>> 测试 2: 执行带语法错误的 SQL (制造 Reflexion 弹药)")
        # 故意写错表名: stadiuuuuum
        bad_sql = "SELECT name, capacity FROM stadiuuuuum"
        print(sandbox.execute_query(bad_sql))
