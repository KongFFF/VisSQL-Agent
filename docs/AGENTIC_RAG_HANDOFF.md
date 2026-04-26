# VisSQL-Agent 进度交接文档

## 当前状态

项目当前处于 README 中“阶段二：Agentic Pipeline 与闭环反思引擎”早期探索阶段，但**真正带来收益的核心改动并不是 Reflexion/probe，而是把首轮 prompt 恢复到与 V6 训练/推理格式严格一致**。

当前已经完成：

- 基于 `agent_coder.py / agent_memory.py / agent_executor.py / main_agent.py` 搭建了最小版 ReAct-Reflexion Agent 骨架。
- 新建了批量评测脚本 [main_eval_agent.py](D:/VisSQL-Agent/main_eval_agent.py)，支持跑 Spider `dev.json`、落盘 `predict_agent.txt`、以及日志与轨迹存档。
- 新建了错题分析脚本 [build_error_reports.py](D:/VisSQL-Agent/build_error_reports.py)，可生成错题清单与对照报告。
- 已经做完多组 A/B 测试，结论比较明确：**prompt 对齐远比 probe 有效。**


## 已验证的关键结论

### 1. 当前系统仍然是“全量 schema 喂入”

README 里计划做的 Agentic RAG（动态表结构检索）**尚未实现**。

当前每道题的 schema 构造逻辑在 [main_eval_agent.py](D:/VisSQL-Agent/main_eval_agent.py:6) 的 `build_schema_dict_v6()` 中，会把当前数据库的**全部表结构**都拼进去；随后在 [agent_memory.py](D:/VisSQL-Agent/agent_memory.py:14) 以：

```text
{schema_info}

【问题】
{question}
```

的格式喂给模型。

也就是说：

- 不是“整个 Spider 全量 schema”
- 而是“当前 `db_id` 对应数据库的全量 schema”


### 2. 之前 Agent 掉分的主要原因不是重试机制，而是 prompt 格式偏移

最初 Agent 版本把首轮 prompt 改成了：

- 新 system prompt
- `问题：...\n\n数据库结构：...`
- 问题在前，schema 在后

这和 V6 原始推理格式不一致。V6 的原始格式见 [finetune_tools/llm_inference_v6.py](D:/VisSQL-Agent/finetune_tools/llm_inference_v6.py:40)：

- 固定 system prompt
- `user = "{schema}\n\n【问题】\n{question}"`

修复后已在以下文件中对齐：

- [agent_coder.py](D:/VisSQL-Agent/agent_coder.py:29)
- [agent_memory.py](D:/VisSQL-Agent/agent_memory.py:13)

这个修复带来了显著收益，是目前最重要的正向发现。


### 3. 空结果重试 / probe 没有形成稳定增益

当前 Agent 支持 `retry_on_empty_result` 开关：

- [main_agent.py](D:/VisSQL-Agent/main_agent.py:14)
- [main_eval_agent.py](D:/VisSQL-Agent/main_eval_agent.py:102)

实验结果：

- `agent_no_empty`（只在执行报错时重试）表现最好
- `agent_with_empty`（空结果也触发重试/probe）略差

这说明：

- `empty result` 不是一个足够干净的错误信号
- 当前 probe 覆盖面太窄
- 多轮反馈格式仍然偏离训练分布

因此当前阶段**不建议继续沿“加更多 probe”这条路线猛推**。


### 4. 当前大部分错题是“SQL 能执行，但语义不对”

这点已经明确确认过。

当前最好版本错题一共 241 道，其中：

- 只有 13 道属于 `parse_error`
- Agent 内部真正执行失败的最终样本只有 11 道左右

说明主要问题不是：

- 语法错误
- SQL 无法执行

而是：

- `WHERE` 条件挂错表/列
- join path 错误
- 极值/代表行选择错误
- 否定/集合逻辑错误
- `GROUP BY / SELECT` 粒度问题


## 关键实验结果

### 官方评测（用户在云端实际跑出的结果）

用户明确说明：实际评测时用的是官方测试库路径，而不是随手猜的本地路径。

已知的几组核心结果：

1. **V6 单步基线**
   - README 中记录的阶段一最佳为 **74.2% Execution Accuracy**
   - 本地 `predict_v6.txt` 对齐分析得到 exact 约 **0.754**

2. **错误 prompt 的 Agent 版本**
   - Execution Accuracy 约 **69%**
   - 证明“直接 Agent 化且改 prompt”会明显掉分

3. **修复 prompt 后，关闭空结果重试**
   - Execution Accuracy 约 **75.4%**
   - Exact Match 约 **76.7%**
   - 这是目前 Agent 线上的最好版本

4. **修复 prompt 后，开启空结果重试**
   - Execution Accuracy 约 **75.2%**
   - Exact Match 约 **76.3%**
   - 比不开空结果重试略差

结论：

- **prompt 对齐修复 > 空结果 probe**
- 当前最优策略是：**V6 prompt 严格对齐 + 不启用空结果重试**


## 当前保留的重要文件

### 核心 Agent 文件

- [agent_coder.py](D:/VisSQL-Agent/agent_coder.py)
- [agent_memory.py](D:/VisSQL-Agent/agent_memory.py)
- [agent_executor.py](D:/VisSQL-Agent/agent_executor.py)
- [main_agent.py](D:/VisSQL-Agent/main_agent.py)
- [main_eval_agent.py](D:/VisSQL-Agent/main_eval_agent.py)

### 错题分析文件

- [build_error_reports.py](D:/VisSQL-Agent/build_error_reports.py)
- [eval_reports/summary.json](D:/VisSQL-Agent/eval_reports/summary.json)
- [eval_reports/wrong_questions_no_empty.jsonl](D:/VisSQL-Agent/eval_reports/wrong_questions_no_empty.jsonl)
- [eval_reports/fixed_over_v6.jsonl](D:/VisSQL-Agent/eval_reports/fixed_over_v6.jsonl)
- [eval_reports/regressed_by_empty_retry.jsonl](D:/VisSQL-Agent/eval_reports/regressed_by_empty_retry.jsonl)

### 批量评测输出

- [eval_no_empty_retry/predict_agent.txt](D:/VisSQL-Agent/eval_no_empty_retry/predict_agent.txt)
- [eval_no_empty_retry/agent_run_summary.jsonl](D:/VisSQL-Agent/eval_no_empty_retry/agent_run_summary.jsonl)
- [eval_no_empty_retry/agent_trajectories.jsonl](D:/VisSQL-Agent/eval_no_empty_retry/agent_trajectories.jsonl)

- [eval_with_empty_retry/predict_agent.txt](D:/VisSQL-Agent/eval_with_empty_retry/predict_agent.txt)
- [eval_with_empty_retry/agent_run_summary.jsonl](D:/VisSQL-Agent/eval_with_empty_retry/agent_run_summary.jsonl)
- [eval_with_empty_retry/agent_trajectories.jsonl](D:/VisSQL-Agent/eval_with_empty_retry/agent_trajectories.jsonl)

- [predict_v6.txt](D:/VisSQL-Agent/predict_v6.txt)


## 错题分析结论（当前最好版本：agent_no_empty）

错题分析基于 [eval_reports/wrong_questions_no_empty.jsonl](D:/VisSQL-Agent/eval_reports/wrong_questions_no_empty.jsonl)。

### 总体分布

- 错题总数：241
- 按难度：
  - easy: 20
  - medium: 76
  - hard: 65
  - extra: 80

### 高频失败部位

- `where`: 102
- `where(no OP)`: 89
- `keywords`: 72
- `select`: 59
- `group`: 53
- `IUEN`: 49
- `parse_error`: 13

### WHERE 错题最常见的 3 种模式

这是后续是否做 Agentic RAG 的直接依据。

1. **negation / set logic 错误（50 题）**
   - 典型问题：
     - `NOT IN / EXCEPT / INTERSECT / UNION`
     - “没有……的对象”
     - “A 但不是 B”
   - 主要错误：
     - 集合操作挂在错误实体层级
     - 子查询返回列和外层过滤列不对齐

2. **extreme value / representative row 选择错误（22 题）**
   - 典型问题：
     - 最大/最小
     - 最早/最晚
     - “拥有最大 X 的那一行”
   - 主要错误：
     - 把“极值”误当成“代表行”
     - `MAX/MIN` 与 `ORDER BY ... LIMIT 1` 使用不当

3. **wrong table / join path for filtering（21 题）**
   - 典型问题：
     - country / continent / full name / maker 等跨表属性
   - 主要错误：
     - 过滤条件挂错表
     - 没走对桥接表/维表
     - 忽略真正承载自然语言值的 lookup table

结论：

- 当前最值得优先解决的是 `WHERE` + schema linking 问题
- 这也是计划做 Agentic RAG 的最合理出发点


## 对“加 probe”路线的当前判断

用户已经明确表达担忧，这个判断需要被新会话继承：

1. **有“面向答案的作弊”嫌疑**
   - 尤其是直接读取测试库真实取值的 probe
   - 会削弱 Spider benchmark 的可比性

2. **即使触发反思，也不一定能修对**
   - 因为当前模型主要训练在单轮 Text-to-SQL 分布
   - 没有系统训练在 “错误 SQL + 执行反馈 -> 修正 SQL” 分布

因此目前总体策略已经转向：

- **不优先继续加复杂 probe**
- 更重视：
  - prompt 对齐
  - 错题分析
  - schema linking 改进
  - 后续的 Agentic RAG


## 当前对 Agentic RAG 的共识

### 1. 它要解决的问题

不是去“看测试库真实取值”，而是要在推理前先回答：

- 这个问题最相关的是哪几张表？
- 相关外键链路是什么？
- 哪些表是 lookup / 维表，承担自然语言属性值？

目标是把当前“全量 schema 输入”改成：

- 先检索出相关表/列/FK 子图
- 再用与 V6 一致的 prompt 格式喂给模型

### 2. 它能不能直接用

结论：**可以先直接用，不必一上来重训模型。**

前提是：

- **必须保持 V6 prompt 形态不变**
- 只把 `schema_info` 从“全量 schema”替换成“检索后的 schema 子集”

也就是：

- system prompt 保持 V6 原文
- user prompt 仍然是：
  `"{retrieved_schema}\n\n【问题】\n{question}"`

### 3. 需不需要重训

当前共识是：

- **第一阶段：不重训，先做 inference-time Agentic RAG A/B**
- **第二阶段：如果证明它有效但不稳定，再考虑做 partial-schema / retrieved-schema 适配训练**


## 新会话建议优先做的事情

### 最高优先级

设计并实现 **最小可行版 Agentic RAG**，但要满足以下约束：

1. **不读取测试库真实值，不走“答案探针”路线**
2. **只做 schema / table / FK 检索**
3. **保持 V6 prompt 格式严格不变**
4. **做成可开关，方便 A/B**

### 推荐实现顺序

1. 新增一个 `Schema Router / Retriever`
   - 输入：`question + 当前 db 的完整 schema metadata`
   - 输出：`若干候选表 + 相关外键 + 子图 schema`

2. 第一版先用规则或轻量启发式
   - 不必一上来用 embedding / 向量库
   - 先按：
     - 问题词与表名/列名重叠
     - 外键邻接扩展 1-hop / 2-hop
   - 保证召回而不是追求极致精度

3. 让 `main_agent.py / main_eval_agent.py` 支持：
   - 全量 schema 模式
   - RAG schema 模式

4. 保持 prompt 模板为：

```text
【数据库结构】
...

【问题】
...
```

只替换 schema 内容，不改 system prompt 和 user 模板。

### 评测建议

至少做这两组对照：

1. **当前最好版本**
   - prompt 对齐
   - 无空结果重试
   - 全量 schema

2. **Agentic RAG 版本**
   - prompt 对齐
   - 无空结果重试
   - 检索后 schema 子图

只比较这一处变量，避免再次混淆。


## 不建议在新会话优先做的事情

以下方向暂时不建议作为第一优先级：

- 继续扩展空结果 probe
- 继续扩展 join path / provenance probe
- 做复杂工具 SQL 反思链
- 贸然改 system prompt 或多轮反馈模板

原因：

- 当前最优结果不是这些方向带来的
- 这些方向容易再次引入评测争议或训练/推理分布偏移


## 一句话交接结论

当前最重要的已知结论是：

**VisSQL-Agent 目前最优版本来自“V6 prompt 严格对齐 + 不启用空结果重试”，其主要剩余问题集中在 WHERE/schema linking/join path 上。下一步最值得推进的不是继续加 probe，而是实现一个不改变 V6 prompt 格式的 Agentic RAG（动态 schema 子图检索）。**
