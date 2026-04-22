import time
# 导入我们之前写好的三大组件！
from agent_coder import CoderNode
from agent_executor import SQLSandbox
from agent_memory import WorkingMemory
from superlative_solver import SuperlativePatternSolver

class VisSQLAgent:
    def __init__(
        self,
        base_model_path: str,
        lora_path: str,
        db_path: str,
        max_retries: int = 3,
        retry_on_empty_result: bool = False,
        superlative_mode: str = "v1",
    ):
        """
        初始化 Agent 大堂经理，统筹主厨(Coder)与试吃员(Sandbox)。
        """
        print("\n" + "="*50)
        print("🚀 VisSQL-Agent 系统启动中...")
        print("="*50)
        
        # 1. 雇佣主厨 (加载模型)
        self.coder = CoderNode(base_model_path=base_model_path, lora_path=lora_path)
        
        # 2. 搭建试吃沙盒 (连接数据库)
        self.sandbox = SQLSandbox(db_path=db_path)
        self.superlative_solver = SuperlativePatternSolver(
            coder=self.coder,
            sandbox=self.sandbox,
            retry_on_empty_result=retry_on_empty_result,
            mode=superlative_mode,
        )
        
        # 3. 设定最大反思重试次数 (防止陷入死循环)
        self.max_retries = max_retries
        self.retry_on_empty_result = retry_on_empty_result
        self.superlative_mode = superlative_mode
        print("✅ 系统初始化完成，随时准备接收查询！\n")

    def run_query(self, schema_info: str, user_question: str, db_path: str = None, verbose: bool = True):
        """
        核心状态机（State Machine）：控制流转的生命周期。
        """
        def log(message: str):
            if verbose:
                print(message)

        if db_path:
            self.sandbox.set_db_path(db_path)

        log(f"👤 用户提问: {user_question}")

        pattern_result = self.superlative_solver.try_solve(
            schema_info=schema_info,
            question=user_question,
        )
        if pattern_result.get("applied"):
            execution = pattern_result["execution"]
            log(
                f"🧩 命中 Superlative 模板 {pattern_result['template']}，"
                f"直接生成 SQL:\n{pattern_result['generated_sql']}"
            )
            return {
                "final_sql": pattern_result["generated_sql"],
                "is_success": True,
                "attempts": 1,
                "data": execution,
                "memory_messages": [],
                "attempt_records": [
                    {
                        "attempt": 1,
                        "mode": "pattern",
                        "template": pattern_result["template"],
                        "generated_sql": pattern_result["generated_sql"],
                        "execution_result": execution,
                        "slot_hint": pattern_result.get("slot_hint"),
                        "slot": pattern_result.get("slot"),
                    }
                ],
                "probe_logs": [],
                "had_probe": False,
                "db_path": self.sandbox.db_path,
                "route": "superlative_pattern",
                "pattern_result": pattern_result,
            }
        elif pattern_result.get("matched"):
            log(
                f"🧩 Superlative 模板尝试未通过，原因: {pattern_result.get('reason')}。"
                " 自动回退到通用 LLM Agent。"
            )
        elif pattern_result.get("reason") not in {"not_superlative", None}:
            log(
                f"🧩 Superlative 检测命中但不适合模板化，原因: {pattern_result.get('reason')}。"
                " 继续使用通用 LLM Agent。"
            )
        
        # 1. 初始化当前任务的“记事本”
        memory = WorkingMemory()
        memory.add_initial_query(schema_info, user_question)
        attempt_records = []
        probe_logs = []

        # ==========================================
        # 🔄 Agent 的灵魂：Reflexion (自我反思) 循环
        # ==========================================
        for attempt in range(1, self.max_retries + 1):
            log(f"\n▶️  [第 {attempt}/{self.max_retries} 轮推理] Agent 思考中...")
            
            # 步骤 A：经理把记事本给主厨，主厨写出 SQL
            current_messages = memory.get_current_messages()
            generated_sql = self.coder.generate(current_messages)
            log(f"🧠 模型生成 SQL:\n{generated_sql}")
            
            # 步骤 B：把生成的 SQL 存入记事本
            memory.add_assistant_sql(generated_sql)

            # 步骤 C：经理把 SQL 丢进沙盒试运行
            log(f"🔨 沙盒执行中...")
            result = self.sandbox.execute_query(generated_sql)
            attempt_record = {
                "attempt": attempt,
                "generated_sql": generated_sql,
                "execution_result": result
            }

            # 步骤 D：命运的十字路口 (状态路由)
            if result["status"] == "success":
                log(f"✅ 执行成功！查出 {result['row_count']} 条数据。")
                log(f"📊 数据抽样: {result['results'][:2]}")

                if result["row_count"] > 0 or not self.retry_on_empty_result:
                    attempt_records.append(attempt_record)
                    return {
                        "final_sql": generated_sql,
                        "is_success": True,
                        "attempts": attempt,
                        "data": result,
                        "memory_messages": memory.snapshot(),
                        "attempt_records": attempt_records,
                        "probe_logs": probe_logs,
                        "had_probe": bool(probe_logs),
                        "db_path": self.sandbox.db_path
                    }

                if attempt < self.max_retries:
                    log("🔄 查询虽然执行成功，但结果为空，触发 Reflexion 重新审视筛选条件/连接逻辑...")
                    diagnostics = self.sandbox.run_diagnostic_probes(
                        generated_sql,
                        scenario="empty_result"
                    )
                    probe_logs.append({
                        "attempt": attempt,
                        "diagnostics": diagnostics
                    })
                    attempt_record["diagnostics"] = diagnostics
                    if diagnostics["probes"]:
                        log("🧪 自动探测到以下诊断信息：")
                        log(diagnostics["summary"])
                    memory.add_execution_feedback(
                        "EmptyResultError",
                        "SQL 已成功执行，但返回了 0 行结果。请重新检查筛选条件、连接逻辑，以及相关类别字段的真实取值是否与 Schema 和数据库内容一致。\n"
                        f"{diagnostics['summary']}"
                    )
                    attempt_record["feedback_type"] = "EmptyResultError"
                else:
                    log("💀 已达到最大重试次数，但查询结果始终为空，Agent 停止重试。")
                    attempt_records.append(attempt_record)
                    return {
                        "final_sql": generated_sql,
                        "is_success": False,
                        "attempts": attempt,
                        "error": "SQL 已成功执行，但在所有重试轮次后仍然返回 0 行结果。",
                        "data": result,
                        "memory_messages": memory.snapshot(),
                        "attempt_records": attempt_records,
                        "probe_logs": probe_logs,
                        "had_probe": bool(probe_logs),
                        "db_path": self.sandbox.db_path
                    }
                
            elif result["status"] == "error":
                log(f"❌ 执行失败！捕获错误: {result['error_type']} - {result['error_msg']}")
                
                # 如果还没到最后一次机会，就触发反思！
                if attempt < self.max_retries:
                    log("🔄 触发 Reflexion 机制，正在将报错记录写入 Memory 迫使模型反思...")
                    memory.add_execution_feedback(result["error_type"], result["error_msg"])
                    attempt_record["feedback_type"] = result["error_type"]
                else:
                    log("💀 已达到最大重试次数，Agent 放弃挣扎。")
                    attempt_records.append(attempt_record)
                    return {
                        "final_sql": generated_sql,
                        "is_success": False,
                        "attempts": attempt,
                        "error": result["error_msg"],
                        "memory_messages": memory.snapshot(),
                        "attempt_records": attempt_records,
                        "probe_logs": probe_logs,
                        "had_probe": bool(probe_logs),
                        "db_path": self.sandbox.db_path
                    }

            attempt_records.append(attempt_record)

# ==========================================
# 终极实战测试入口
# ==========================================
if __name__ == "__main__":
    # ⚠️ 【请在这里替换为你真实的物理路径！】
    BASE_MODEL = "/root/autodl-tmp/qwen2.5-coder-7b-instruct"
    LORA_PATH = "/root/autodl-tmp/LLaMA-Factory/saves/Qwen2.5-7B/lora/qwen_spider_lora_v6"  # 如果你的 V6 是独立权重，这里填 None；如果是动态挂载，填 lora 路径
    TEST_DB = "data/testsuitedatabases/database/concert_singer/concert_singer.sqlite"

    # 1. 实例化我们的终极系统
    agent = VisSQLAgent(
        base_model_path=BASE_MODEL, 
        lora_path=LORA_PATH, 
        db_path=TEST_DB, 
        max_retries=3,  # 给它 3 次机会
        retry_on_empty_result=True
    )

    # 2. 伪造一个极其变态的测试场景 (故意少给点信息，考验它的反思能力)
    test_schema = """
    表 stadium (Stadium_ID [PK], Location, Name, Capacity, Highest, Lowest, Average)
    表 singer (Singer_ID [PK], Name, Country, Song_Name, Song_release_year, Age, Is_male)
    表 concert (concert_ID [PK], concert_Name, Theme, Stadium_ID [FK->stadium.Stadium_ID], Year)
    表 singer_in_concert (concert_ID [FK->concert.concert_ID], Singer_ID [FK->singer.Singer_ID])
    """
    
    # 😈 终极陷阱：自然语言的性别陷阱
    test_question = "列出所有女性歌手的名字和她们的歌曲名。"

    # 3. 跑起来！
    agent.run_query(schema_info=test_schema, user_question=test_question)
