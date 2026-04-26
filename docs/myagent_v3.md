**优先级排序**

如果按“最可能涨分”和“最不容易白做”来排，我会建议这样做：

1. `P0`：补 `value grounding / entity linking`
2. `P1`：补 `semantic verifier / reranker`
3. `P1`：补 `clause-level repair`
4. `P2`：再做小规模 `beam search`
5. `P3`：最后才继续扩 `superlative/template`
6. `P3`：RAG 只做小修，不要再当主战场

核心原因很简单：  
你们现在的系统在 [schema_retriever.py](/D:/VisSQL-Agent/schema_retriever.py:440) 已经能把相关表大多找出来，但在 [main_agent.py](/D:/VisSQL-Agent/main_agent.py:138) 里，只要 SQL 执行成功就直接收工。于是大量“可执行但语义错”的题，没有第二道防线。

**1. `P0`：Value Grounding / Entity Linking**

这是最该优先做的，因为它解决的是现在最密集的一类错。

你现在的 schema 输入基本只有表名、列名、PK/FK，见 [schema_retriever.py](/D:/VisSQL-Agent/schema_retriever.py:371)。  
它缺少这几种对 Spider 很关键的信息：

- 列的值域样例
- code/name 对应关系
- 布尔列真实取值
- 类别列常见枚举
- 同义词或自然语言别名

这会直接导致你们现在这些错误：
- `USA` 被写成 `United States`
- `APG` 被当成机场名而不是机场 code
- `Caribbean` 被落到 `Continent` 而不是 `Region`
- `IsOfficial='T'` 被写成 `Yes`
- `Anguilla` 被当成 city 名而不是 country 名

所以这一步不是“优化 prompt”，而是**把 prompt 的信息粒度从 schema-level 提升到 schema+value-level**。

最值得先补的内容：
- 每张候选表，每个高价值列给 5 到 10 个 distinct sample values
- 对低基数字段标注“疑似枚举列”
- 对 `id/code/name/type/status/country/region` 这类字段做专门标记
- 对布尔列自动总结真实值，比如 `T/F`、`Y/N`、`0/1`
- 如果 question 中出现字符串值，先在候选列上做模糊匹配，返回最像的列和值

最直接的落地方式：
- 在 schema 检索后，不是只返回表结构
- 再做一步 `value sketch retrieval`
- 把结果拼进 schema prompt，形成“结构 + 值样例”

预期收益：
- 对 `easy/medium` 题会有最直接提升
- 尤其能修掉现在最亏的“能执行但值错”题
- 这一步往往比继续做模板更稳

**2. `P1`：Semantic Verifier / Reranker**

这是第二优先，因为它负责解决“第一条 SQL 看起来像对，但其实不对”的问题。

当前 [agent_executor.py](/D:/VisSQL-Agent/agent_executor.py:22) 的执行器只判断：
- 有没有报错
- 有没有空结果

但 Spider 很多错题是：
- SQL 有结果，但条件字段错了
- SQL 有结果，但 join 错了
- SQL 有结果，但 group 粒度错了
- SQL 有结果，但输出列不对

这类题必须引入一个 `verifier`，而不是继续靠 SQLite 报错。

我建议 verifier 先做规则化，再考虑 LLM：
- 检查 question 里的显式实体值是否真实落在被过滤的列上
- 检查 SQL 里用了 `AirportName` 还是 `AirportCode`
- 检查是否把 region/continent、maker/fullname、model/make 这类近义字段搞混
- 检查 `GROUP BY` 是否跟 target projection 同一粒度
- 检查 `COUNT` 题是否多投影了不该输出的列
- 检查 `NOT/EXCEPT/INTERSECT/UNION` 是否被错误改写成别的结构

你可以把 verifier 输出成一个分数，用来给多个 SQL 候选 rerank。  
这一步的关键不是“生成更聪明”，而是“选错的候选别直接交卷”。

预期收益：
- 对 hard/extra 题比 value grounding 更重要
- 它会把现在 generic LLM 的很多“八成像对”的坏答案拦下来

**3. `P1`：Clause-Level Repair**

这是第三优先，因为它是把你们现在的 reflexion 从“泛泛重写”升级成“定点修复”。

现在 [agent_memory.py](/D:/VisSQL-Agent/agent_memory.py:20) 的反馈方式太粗了，主要是把错误文本再喂回去。  
这对 `no such column` 还行，但对语义错几乎没有帮助。

更好的 repair 方式是：  
不要让模型“整条 SQL 再想一遍”，而是明确告诉它“哪一块可能错了”。

比如输出这种结构化反馈：
- `filter_value_mismatch`: 列 `Country` 的样例值是 `USA`, `Canada`，没有 `United States`
- `join_suspect`: 当前 join 走到了 `city`，但 question 更像问 `country`
- `group_grain_mismatch`: 当前按 `Name` 分组，gold 模式更像按主键分组
- `projection_incomplete`: question 要两个输出列，你只投影了一个
- `setop_mismatch`: question 表达的是“同时满足/不包含”，当前 SQL 用了错误的 `INTERSECT`/`EXCEPT`

然后只要求模型修一个 clause：
- 只改 `WHERE`
- 只改 `GROUP BY`
- 只改 join path
- 只改 projection

为什么这比整句重写更重要：
- 整句重写很容易把原来已经对的部分一起搞坏
- clause repair 更适合现在这种“SQL 大体对、细节错”的剩余误差分布

**4. `P2`：Beam Search + Rerank**

这一步我会排在 verifier 后面，而不是前面。

原因是：  
没有 verifier 的 beam search，常常只是“生成更多错 SQL”。  
有 verifier 以后，beam 才有意义，因为它能在多个候选里挑最可信的那个。

建议的最小版本：
- 首轮生成 3 到 5 条候选 SQL
- 每条都执行
- 用 verifier 打分
- 选最高分那条
- 如果前两条很接近，再触发 clause repair

适合优先开的题型：
- 值链接歧义
- 多跳 join 歧义
- group-by 粒度歧义
- `NOT IN / EXCEPT`
- top-1 superlative 的多种等价写法

为什么不是更大 beam：
- 你现在还没有一个强 verifier
- 大 beam 会显著增加成本
- 先做 `3~5` 条已经够看出收益了

**5. `P3`：Superlative / Template 扩展**

这一步不是没价值，但现在不该当主线。

从你们的运行结果看，模板路由只覆盖了很小一部分题，绝大多数还是 generic LLM。  
而且 [superlative_solver.py](/D:/VisSQL-Agent/superlative_solver.py:707) 本身就是一个“先判断是否属于某类题，再做模板填槽”的系统，这类系统的天然问题是：

- 覆盖窄
- 边界脆
- 新模板越多，冲突越多
- 调 router 和 slot filling 的时间成本越来越高
- 很容易把研发时间花在少数样本上

什么时候它值得继续做：
- 你已经完成 value grounding 和 verifier
- 你确认某一大类题高频、稳定、结构非常固定
- 模板能一次性吃掉 20+ 个错题，而不是 3~5 个

我更建议保留它，但把它从“主路线”降级为“高 precision 的特种模块”。

**6. `P3`：RAG 只做小修，不再深挖**

这一步优先级最低，不是因为它不重要，而是因为它已经接近“够用”。

现在的 retriever 在 [schema_retriever.py](/D:/VisSQL-Agent/schema_retriever.py:509) 主要是基于词面 overlap 打分。  
它当然还能继续改，比如：
- 提升 table score
- 优化 expand hop
- 优化 path hint
- 让 auto/full 切换更稳

但从结果看，这些不会再带来大涨分。原因是：
- 金表覆盖已经很高
- 真正错误在后面的 SQL 语义构造
- RAG 再精细，也不能自动修 `USA/United States` 这种问题

所以我建议这里只做两件小事：
- 给 schema prompt 补 value sketch
- 给 path hint 增加“候选 join 语义标签”

不要继续把主要时间投在“再提高 1% 表覆盖率”。

**如果按工程落地来排期**

如果给我 1 周，我会这样排：

1. 先做 `value sketch retrieval`
2. 再做 `semantic verifier v1`
3. 再做 `clause-level repair`
4. 最后接一个 `beam=3` 的最小 rerank

如果给我 2 到 3 周，我会这样排：

1. 先把 `252_wrong_cases` 标成 6 到 8 个错误大类
2. 做 value grounding
3. 做 verifier
4. 做 beam+rereank
5. 最后只针对收益最高的一类再补模板

**你可以把这 6 点理解成三层**

- 第一层，`让模型看见更多真实信息`
  这就是 value grounding

- 第二层，`让系统知道第一条答案可能只是“看起来对”`
  这就是 verifier / reranker

- 第三层，`别每次都整句重写，要会局部修`
  这就是 clause-level repair

这三层补完之后，再回头看模板和 RAG，投入产出会高很多。

如果你愿意，我下一条可以直接把这 6 个优先级进一步展开成一份更具体的研发路线图：
- 每个模块改哪些文件
- 先做最小版还是完整版
- 每一步怎么衡量有没有真的涨分