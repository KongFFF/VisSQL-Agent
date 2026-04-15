import sqlite3
import time

class SQLSandbox:
    def __init__(self, db_path: str, timeout: int = 5):
        """
        初始化沙盒执行器。
        :param db_path: SQLite 数据库文件的物理路径
        :param timeout: 数据库锁超时时间（防止死锁）
        """
        self.db_path = db_path
        self.timeout = timeout

    def execute_query(self, sql_query: str, row_limit: int = 10) -> dict:
        """
        核心执行引擎：带有极强防御性编程的 SQL 执行器。
        :param sql_query: 大模型生成的 SQL 字符串
        :param row_limit: 限制最大返回行数，【极其关键】防止查询结果太大撑爆大模型的上下文窗口！
        """
        # 清理可能存在的代码块标记 (Markdown 格式)
        clean_sql = sql_query.replace("```sql", "").replace("```", "").strip()
        
        start_time = time.time()
        
        try:
            # 1. 建立连接（开启 URI 模式可支持只读，此处为标准模式）
            conn = sqlite3.connect(self.db_path, timeout=self.timeout)
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