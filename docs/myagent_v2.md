# 方法名称

**Template-Routed Selective Generation for Superlative Queries (TRSG-SQL)**

---

# 1. 任务定义

我们关注一类特殊的 Text-to-SQL 问题：

> **Superlative Queries（最高级 / 极值类问题）**

典型形式包括：

- “最小 / 最大 / 最多 / 最少”
- “the smallest / largest / most / least”
- “fewest number of ...”
- “highest / lowest / youngest / oldest / earliest / latest”

这类问题的一个重要特点是：

- 一部分题目具有很稳定的 SQL 结构，可以被模板化处理；
- 另一部分题目虽然表面上也带有 superlative 词汇，但真实 SQL 结构更复杂，如果强行套模板，反而容易比 baseline 更差。

因此，本方法的目标不是“让模板系统接管所有 superlative 问题”，而是：

> **只在高置信、高收益的子集上，让模板路线覆盖 baseline；其余情况默认回退 baseline。**

这一定义非常重要。  
模板模块不是 baseline 的替代品，而是一个**高精度、可放弃（abstain）的专科专家模块**。

---

# 2. 方法目标

我们希望最终系统满足以下目标：

1. 默认行为不弱于 baseline
2. 模板路线只影响 superlative 相关子集，不污染主路径
3. 模板接管必须由“结构证据 + 学习式置信度 + 验证信号”共同决定
4. 最终整体准确率理想状态下 `>= baseline`

换句话说，本方法追求的不是“更高覆盖率”，而是：

> **Selective Gain：只在模板路线预期净收益为正时才接管 baseline。**

---

# 3. 设计原则

为了避免系统退化成“手工工程规则库”，我们给方法施加三条硬约束：

## 3.1 只允许增加“结构类模板”，不允许增加“题面补丁”

好的增强应当对应一个可命名的 SQL 结构类，例如：

- 单表 `ORDER BY ... LIMIT 1`
- 单表 `MIN/MAX` 嵌套极值
- `GROUP BY + COUNT(*) + ORDER BY + LIMIT 1`
- 单跳 `JOIN + ORDER BY + LIMIT 1`

不好的增强则是：

- “看到 by land 就强行选 surfacearea”
- “看到 shop + employees 就硬编码某列”

前者是结构抽象，后者是题面 patch。

## 3.2 判定应尽量基于结构证据，而不是词面记忆

我们真正想判断的是：

- 该题是在求“对象”还是在求“聚合值”
- target 列和 measure 列是否在同表
- 是否存在唯一单跳 join
- 是否本质上是 top-1，而不是 top-k
- 是否要求 count 输出，而不是只返回对象
- 是否是“每组一个极值”，而不是“全局一个极值”

这类判定属于**结构语义 routing**，不是 dev set 记忆。

## 3.3 模块必须有 abstain 能力

模板模块不允许“有 superlative 就强上”。

相反：

- 默认走 baseline
- 模板只有在高置信且高收益预期时才允许接管
- 一旦置信度不足，立即回退 baseline

这也是本方法与“纯规则系统”的根本区别。

---

# 4. 系统总览

整体 pipeline 如下：

```text
Question + Schema
  ->
[1] Superlative Signal Extraction
  ->
[2] Structural Eligibility + High-Risk Exclusion
  ->
[3] Learned Router
      - p(use_template | q, schema)
      - p(template_k | use_template, q, schema)
  ->
[4] Template Slot Filling
  ->
[5] Programmatic SQL Construction
  ->
[6] Structural / Schema / Execution Validation
  ->
[7] Per-template Confidence Threshold
      - if score_k >= tau_k: use template SQL
      - else: fallback to baseline
  ->
Final SQL
```

这里最关键的一点是：

> **Baseline 是默认专家；Template Family 是候选专家；Router 决定是否让候选专家接管。**

---

# 5. 模块组成

## 5.1 Baseline Generator

baseline 是默认 SQL 生成器。

它始终会被保留，并且在以下情况中作为主路线：

- 当前问题不适合模板化
- 没有模板与之高置信匹配
- slot 抽取失败
- 模板 SQL 校验失败
- 模板置信度低于阈值

也就是说，模板模块是一个**可选覆盖层**，不是替代 baseline 的强制路线。

## 5.2 Template Families

模板族负责覆盖一小部分**结构非常稳定**的 superlative 问题。  
初始版本保留并整合现有的 5 个模板族。

### Template A: ORDER BY + LIMIT 1

适用于：

- 单表对象极值检索
- 不需要复杂嵌套
- target 和 measure 可直接在同一主查询中表达

SQL 结构：

```sql
SELECT {target}
FROM {table}
{where_clause}
ORDER BY {measure} {ASC_DESC}
LIMIT 1
```

### Template B: Nested MIN/MAX

适用于：

- 单表极值对象检索
- 更适合写成 `measure = MIN/MAX(measure)` 的嵌套结构

SQL 结构：

```sql
SELECT {target}
FROM {table}
WHERE {measure} = (
    SELECT {AGG_FUNC}({measure})
    FROM {table}
    {where_clause}
)
```

### Template C: GROUP BY + COUNT(*) + ORDER BY + LIMIT 1

适用于：

- “哪个 group 出现次数最多 / 最少”
- “哪一年 / 哪个国家 / 哪种语言 / 哪一类对象数量最多 / 最少”

SQL 结构：

```sql
SELECT {target}
FROM {table}
{join_clause}
{where_clause}
GROUP BY {group_key}
ORDER BY COUNT(*) {ASC_DESC}
LIMIT 1
```

Additional constraint:
- `target` and `group_key` should come from the same grouped source side.
- `join_clause` may be used for filtering/context, but should not be used to switch the final answer onto another entity table.

### Template D: Single-Hop JOIN + ORDER BY + LIMIT 1

适用于：

- 输出字段和排序字段不在同一张表
- 两表之间存在唯一单跳外键关系
- 不需要 `GROUP BY / HAVING / set op / 多层嵌套`

SQL 结构：

```sql
SELECT {target}
FROM {left_table}
JOIN {right_table} ON {join_on}
{where_clause}
ORDER BY {measure} {ASC_DESC}
LIMIT 1
```

### Template E: JOIN + GROUP BY + COUNT(*) + ORDER BY + LIMIT 1

适用于：

- 最终输出字段属于实体表，但被计数的记录属于另一张 fact 表
- 典型问题形式是“哪个 stadium / city / country 拥有最多 related rows”
- 可写成“实体表 JOIN 事实表，再按实体键分组并对事实表计数”

SQL 结构：
```sql
SELECT {target}
FROM {entity_table}
JOIN {fact_table} ON {join_on}
{where_clause}
GROUP BY {group_key}
ORDER BY COUNT(*) {ASC_DESC}
LIMIT 1
```

Additional constraint:
- `target` and `group_key` should both come from `entity_table`.
- `fact_table` only provides counted rows.
- The current version accepts only single-hop, non-`OR` join predicates; harder cases should fallback to baseline.

## 5.3 Structural Eligibility

不是所有包含 superlative 词的题目都适合进入模板系统。

因此，在模板选择之前，先做一层“是否可模板化”的结构判定。  
该判定的目标不是“尽量识别更多模板题”，而是：

> **先把明显高风险、不适合模板化的问题挡掉。**

当前保留两类低风险排除型判定：

### Exclusion 1: 直接求极值本身，而不是求极值对象

典型例子：

- `What is the minimum weight ...`
- `What is the maximum mpg ...`
- `What are the average, minimum, and maximum age ...`

这些题的正确 SQL 通常是：

- `MIN(col)`
- `MAX(col)`
- `AVG(col), MIN(col), MAX(col)`

而不是去找某个对象。

### Exclusion 2: 每组聚合，不是 top-1 group

典型例子：

- `maximum weight for each type of pet`
- `largest percentage of people in each country`

这些题更像：

- `GROUP BY + MAX/MIN/AVG`

而不是：

- `GROUP BY + ORDER BY ... LIMIT 1`

## 5.4 High-Risk Exclusion

在 `v2` 的经验基础上，高风险题型需要优先回退 baseline。  
这些题未来不是不能做，而是需要等有成熟模板后再重新放回程序化路径。

当前高风险排除类包括：

- `top-k` / plural ranking  
  例如：`top 3`, `top 5`, `three youngest`, `five largest`

- `count-output superlative`  
  例如：不仅要对象，还显式要 `how many / number of`

- `multi-aggregation extrema`  
  例如：同时要求 `maximum and minimum`

- `temporal superlative`  
  例如：`earliest / latest`

- `ambiguous popularity`  
  例如：`most popular`

这些类并不是永久排除，而是当前版本中的**安全边界**。

## 5.5 Superlative Signal Extraction

superlative detection 仍然需要，但它的作用不再是“只要命中就套模板”，而是作为 router 的输入信号之一。

保留一个简洁、干净的 superlative 词表：

```python
POSITIVE_PATTERNS = [
    "最小", "最大", "最多", "最少", "最低", "最高",
    "minimum", "maximum", "most", "least",
    "smallest", "largest", "fewest",
    "lowest", "highest",
    "youngest", "oldest",
    "earliest", "latest",
    "longest", "shortest",
    "greatest", "biggest",
]

NEGATIVE_PATTERNS = [
    r"\bat\s+least\b",
    r"\bat\s+most\b",
]
```

注意：

- 这个模块只提供“可能是 superlative”的信号；
- 它不再单独决定是否进入模板路径；
- 最终是否接管仍由 learned router + validator + threshold 决定。

---

# 6. Learned Router

这是本方法最核心的升级点。

我们不再采用“只要命中模板判定就走模板”的强规则模式，而是引入一个**可学习的路由器**。

## 6.1 Router 的目标

router 负责回答两个问题：

### (1) 这道题是否值得进入模板系统？

输出：

```text
p(use_template | question, schema)
```

### (2) 如果进入模板系统，它更像哪个模板族？

输出：

```text
p(template_k | use_template, question, schema)
```

这样可以把问题拆成两层：

1. `templateable vs non-templateable`
2. `template class selection`

这比一步直接选模板更稳，因为很多错误不是“选错模板”，而是“本来就不该进模板系统”。

## 6.2 最终模板路由分数

对每个模板 `k`，定义最终分数：

```text
score_k
= p(use_template | q, s)
  * p(template_k | use_template, q, s)
  * validator_conf_k
```

其中：

- `q` 是 question
- `s` 是 schema
- `validator_conf_k` 是模板构造和校验后的置信信号

最终决策为：

```text
if max_k(score_k) >= tau_k:
    use template k
else:
    fallback to baseline
```

## 6.3 Router 输入

router 不应只看题面词，而应使用“问题语义 + schema 结构证据”的联合输入。

建议输入包括：

- question 文本
- superlative signal
- 是否像 top-1 / top-k
- 是否要求 count 输出
- target table / measure table 候选
- target 和 measure 是否在同表
- 是否存在唯一单跳 join
- 是否像 group-count
- 是否像直接求极值值本身
- schema 中相关表列摘要

这样 router 学到的不是“某个词像某个题”，而是：

> **当前问题在当前 schema 下，是否真的适合某个结构模板。**

---

# 7. Slot Filling

slot filling 仍然由 LLM 承担，但其角色需要重新定义：

- 它不是直接生成最终 SQL
- 它只负责从 question + schema 中抽取模板槽位
- 模板 SQL 由程序拼接生成

这意味着 LLM 的任务被缩小为：

> **受控结构下的字段抽取，而不是自由生成 SQL。**

## 7.1 初始路由提示（slot hint）

先抽取轻量 hint，例如：

```json
{
  "target_table": "",
  "measure_table": "",
  "needs_group_by": false,
  "needs_nested": false
}
```

它用于辅助 template selection / router feature。

## 7.2 模板槽位

保留当前四个模板的槽位设计。

### Template A

```json
{
  "target": "",
  "table": "",
  "measure": "",
  "order": "ASC or DESC",
  "condition": ""
}
```

### Template B

```json
{
  "target": "",
  "table": "",
  "measure": "",
  "agg_func": "MIN or MAX",
  "condition": ""
}
```

### Template C

```json
{
  "target": "",
  "table": "",
  "join_clause": "",
  "group_key": "",
  "order": "ASC or DESC",
  "condition": ""
}
```

### Template D

```json
{
  "target": "",
  "left_table": "",
  "right_table": "",
  "join_on": "",
  "measure": "",
  "order": "ASC or DESC",
  "condition": ""
}
```

---

# 8. Validation

validation 是模板系统能否真正稳定上线的关键。

它的作用不是“锦上添花”，而是：

> **阻止低质量模板输出覆盖 baseline。**

validation 至少应包含三层：

## 8.1 Schema Validation

- 表名是否存在
- 列名是否存在
- join 路径是否有效
- target / measure / group_key 是否可解析

## 8.2 Structural Validation

- 是否满足该模板的前提结构
- 是否是单跳 join
- 是否是 top-1 而不是 top-k
- 是否没有越界到更复杂结构

## 8.3 Execution Validation

- SQL 是否可执行
- 是否非空
- 是否与模板预期一致

这些信号可以进一步汇总为：

```text
validator_conf_k
```

并参与最终路由分数。

---

# 9. Abstain / Fallback Boundary

这部分必须显式写清楚，因为它决定了方法是否会伤 baseline。

## 9.1 默认策略

默认总是采用 baseline 输出。

## 9.2 模板接管条件

只有同时满足以下条件，模板才允许接管：

1. 通过 structural eligibility
2. 未命中 high-risk exclusion
3. learned router 认为该模板置信度足够高
4. slot filling 成功
5. validation 成功
6. `score_k >= tau_k`

否则一律回退 baseline。

## 9.3 设计含义

这意味着模板系统是一个：

- selective
- confidence-aware
- abstention-enabled

的专家模块，而不是硬规则分支。

---

# 10. 阈值校准

模板是否接管，不能凭直觉决定，也不能只看命中率。  
应该按**相对 baseline 的净收益**来选择阈值。

对每个模板 `k`，定义阈值 `tau_k`。  
在某个阈值下，模板 `k` 的净收益可写为：

```text
net_gain_k(tau)
= coverage_k(tau)
  * (
      acc_template_k(tau)
      - acc_baseline_on_same_subset(tau)
    )
```

关键点：

- 看的是同一子集上的 baseline，而不是全局 baseline
- 每个模板可以有自己的阈值
- 如果某个模板在任何阈值下净收益都不为正，则不部署

这使得系统具有非常清晰的工程决策原则：

> **只上线那些“在自己负责的子集上”确实优于 baseline 的模板专家。**

---

# 11. 自动标注与训练数据构造

为了避免方法演化成对 dev set 的手工修补，router 的训练标签不应人工按题面打，而应从 gold SQL 结构自动抽取。

## 11.1 标注来源

使用：

- `train_spider.json`
- `tables.json`

根据 gold SQL 自动构造标签。

## 11.2 标签形式

### 第一阶段标签

```text
templateable / non-templateable
```

### 第二阶段标签

```text
Template A / Template B / Template C / Template D / Template E / None
```

## 11.3 标注原则

不是问“题面像不像某模板”，而是问：

> **gold SQL 的真实结构是否落入某个模板族。**

这使得训练标签来自 SQL 结构，而不是 dev 记忆。

---

# 12. 训练与部署建议

## 12.1 推荐的实现顺序

### Phase 1: 安全壳

- superlative signal
- structural eligibility
- high-risk exclusion
- fallback baseline

这对应当前 `v2` 的主要价值：保证模板系统不会明显伤主路径。

### Phase 2: 二分类 router

训练：

```text
p(use_template | q, schema)
```

目标：

- 高 precision
- 覆盖可以暂时较低

### Phase 3: 模板多分类 router

训练：

```text
p(template_k | use_template, q, schema)
```

### Phase 4: validator-aware routing

把以下信号融合进最终决策：

- slot 完整性
- schema 校验
- execution 结果
- 非空结果

### Phase 5: per-template threshold calibration

为每个模板独立调 `tau_k`，确保净收益为正。

---

# 13. 当前版本的合理定位

当前阶段不应把模板模块理解成“准备替代 baseline 的完整系统”，而应理解为：

> **正在从“规则模板尝试”过渡到“选择性专家路由系统”的中间版本。**

因此，`v2` 的价值不在于它已经超过 baseline，而在于它已经提供了三个重要基础：

1. 模板族边界
2. 高风险排除壳
3. fallback 机制

真正能让系统稳定 `>= baseline` 的关键，不是继续堆更多规则，而是引入：

- learned router
- validator-aware confidence
- per-template threshold calibration

---

# 14. 实验协议

后续实验应按如下 protocol 进行。

## 14.1 核心对照组

- `Baseline`
- `Template v1`：直接模板尝试
- `Template v2`：高风险回退版
- `Template + Router`
- `Template + Router + Threshold`

## 14.2 评估指标

- 全量 EX / EM
- superlative 子集 EX / EM
- 模板接管率
- 接管子集上的模板精度
- 接管子集上的 baseline 精度
- 净收益 `net_gain`

## 14.3 子集评测

除了完整 dev 集，还应维护一个 superlative 回归集：

- baseline 与模板版本有差异的样本
- 当前高风险类样本
- 当前每个模板族的代表样本

这样可以更快迭代。

---

# 15. 成功标准

该方法的成功标准不是“模板覆盖越多越好”，而是：

1. 模板只影响应影响的 superlative 子集
2. 接管样本上的模板精度高于 baseline
3. 阈值可校准出正净收益区间
4. 最终整体准确率 `>= baseline`

如果某个模板族做不到这一点，就不应部署。

---

# 16. 后续路线图

建议按如下顺序推进：

## Step 1

保留当前 5 个模板族与高风险排除壳，不继续无约束加规则。

## Step 2

从训练集 gold SQL 自动标注：

- `templateable / non-templateable`
- 模板类别

## Step 3

训练 learned router：

- 先二分类
- 再多分类

## Step 4

把 validator 信号并入最终模板置信度。

## Step 5

按每个模板的净收益调阈值，只部署正收益模板。

## Step 6

在明确有价值的前提下，再逐步扩展模板族：

- join-group-count 的显示名版
- 更稳定的 top-k 模板
- temporal superlative 模板

前提始终是：

> **新增模板必须对应一个可命名、可验证、可评估净收益的 SQL 结构族。**

---

# 17. 总结

本方法不是“看到最高级词就套规则”的系统，而是一个：

- baseline 默认
- template experts 候选
- learned router 选择
- validator 校验
- threshold 控制接管
- fallback 兜底

的 **selective expert routing** 框架。

它的核心思想可以概括为：

> **宁可少接管，也不错误接管；只有当模板路线的预期成功率足够高时，才允许其覆盖 baseline。**

这也是该方法最终能够稳定做到 `>= baseline` 的关键。


## 路线

#### Phase 0：固定当前模板边界
先把当前版本的“专家集合”定下来，不继续无约束加规则。

保留：

Template A
Template B
Template C
Template D
保留的排除层：

plain extrema value query
group aggregation query
top-k
count-output
multi-agg
temporal
ambiguous popularity
这一步的目标是：

定义清楚“哪些题允许候选模板进入竞争”
明确“哪些题一律 baseline”

#### Phase 1：实现非训练版 router
这一阶段不做标注、不做微调。

router 的输入：

question
schema
规则特征
slot hint
router 的输出不是 hard label，而是一个 JSON 评分，例如：

{
  "use_template_score": 0.82,
  "template_scores": {
    "ORDER_BY": 0.15,
    "NESTED": 0.20,
    "GROUP_COUNT_TOP1": 0.88,
    "JOIN_ORDER_BY": 0.31
  },
  "reason": "count superlative with stable single-hop structure"
}
实现方式：

先用结构规则做 shortlist
再让当前基座模型只做“判别与评分”，不直接写 SQL
输出固定 JSON
这里你可以把它理解成：
LLM-as-router，而不是 trained router

这一步的关键是 prompt 设计，不是训练。

Phase 2：把 validator 接到 router 后面
这一阶段把“模型判别分数”和“程序校验信号”合并。

最终分数可以先写成一个简单版本：

final_score_k
= router_score_k
  * schema_valid_k
  * execution_valid_k
  * nonempty_bonus_k
其中：

schema_valid_k 可以是 0/1
execution_valid_k 可以是 0/1
nonempty_bonus_k 可以是 1 或一个较小加权项
如果你想更稳一点，也可以不用乘法，直接做门控：

先看 router score 是否大于阈值
再要求 validation 全通过
否则 fallback
这一步之后，系统就已经具备完整的 selective routing 能力了。

Phase 3：做阈值实验，先确保不伤 baseline
这一阶段仍然不训练 router。

做法是：

对 dev 集跑一遍
记录每题：
baseline SQL
router score
selected template
template SQL
validation signals
final chosen route
扫不同阈值 tau
你会得到一条很重要的曲线：

阈值低：coverage 高，但风险大
阈值高：coverage 低，但 precision 高
你的目标不是找“覆盖最多”的点，而是找：

全局 EX 不低于 baseline
模板接管子集上精度高于 baseline
净收益为正
这个阶段其实非常关键，因为它会告诉你：

现在的模板系统值不值得继续扩
哪个模板最有价值
哪个模板应该暂时下线
Phase 4：收集训练信号，再决定是否微调
等前 3 步跑通后，再回头看是否需要 learned router。

这时你已经有了很好的训练样本来源：

问题和 schema
候选模板
router 初始评分
最终哪条路线对
某模板接管后是 gain 还是 loss
你可以由此构造两个训练任务：

templateable / non-templateable
template class selection
这时再决定：

直接微调当前基座模型做 router
还是单独训练一个轻量分类器
都会比现在直接上训练更稳。

## Phase 1c Reflection and Phase 1d Adjustment

Phase 1c tried to make slot filling more controlled by asking the LLM to emit
`target_columns` and then repairing incomplete projections. The experiment fixed
some missing-projection failures, but it also introduced new semantic slot errors:

- wrong measure column, for example ranking by `capacity` instead of `average`
- wrong count source table, for example grouping `Documents` instead of `Paragraphs`
- wrong entity/fact join choice

The useful lesson is therefore:

> slot validation is useful; LLM-based slot repair is not stable enough.

Phase 1d changes direction:

- do not use the Phase 1c controlled slot prompt
- do not use LLM repair
- keep projection completeness as validator-only fallback
- add `ENTITY_BY_RELATED_COUNT_TOP1`, a schema-enumerated count template

`ENTITY_BY_RELATED_COUNT_TOP1` is a structural template, not a question patch:

```sql
SELECT {entity_columns}
FROM {entity_table}
JOIN {fact_table} ON {entity_key} = {fact_fk}
{where_clause}
GROUP BY {entity_group_column}
ORDER BY COUNT(*) {ASC_DESC}
LIMIT 1
```

The join plan is generated from the schema foreign-key graph. The LLM selects one
enumerated plan and the output columns, instead of freely inventing `entity_table`,
`fact_table`, `join_on`, and `group_key`.

## Phase 1e: Structured Projection Selection

`phase1_d` already showed that the count-family route can create net gain, but the
largest remaining controllable loss bucket is still `projection_incomplete`.

`phase1_e` keeps the conservative `phase1_d` design:

- keep `validator-only` fallback
- keep the schema-enumerated `ENTITY_BY_RELATED_COUNT_TOP1`
- do not bring back `phase1_c` style LLM repair

The new change is only for count-family templates:

- `GROUP_COUNT_TOP1`
- `JOIN_GROUP_COUNT_TOP1`
- `ENTITY_BY_RELATED_COUNT_TOP1`

After the structural slots are fixed, the program enumerates answer-side projection
candidates from schema:

- for `GROUP_COUNT_TOP1`, enumerate columns from the grouped answer-side table
- for `JOIN_GROUP_COUNT_TOP1`, enumerate columns from `entity_table`
- for `ENTITY_BY_RELATED_COUNT_TOP1`, enumerate columns from the selected entity plan

Then the LLM only selects `target_columns` from those candidates. It is not allowed
to freely invent columns, tables, or joins.

If the selected projection is still incomplete or inconsistent, the system falls back
to baseline directly.

## Phase 2a: Unified Count-Family Planner

`phase2_a` does not add validator-aware scoring. It only upgrades the count-family
 route from several loose templates into one planner:

- `COUNT_FAMILY_TOP1`

The planner enumerates two kinds of schema-grounded plans:

- `same_table`: count rows inside one table and return a grouped answer-side value
- `related_entity`: count fact-side rows through a single FK edge and return entity-side columns

The LLM only chooses:

- `plan_id`
- `target_columns`
- `group_by_column`
- `order`
- `condition`
- `include_count`

The SQL structure, join shape, and answer/count table roles come from the enumerated
plan rather than free-form slot invention.

## Phase 2b: Decomposed Count Slotting

`phase2_b` keeps the same unified `COUNT_FAMILY_TOP1` planner, but reduces slot
 freedom in order to raise coverage:

- prune count-family plans more aggressively from `slot_hint`
- choose `plan_id` first
- choose `target_columns` second
- infer `group_by_column` programmatically from the chosen plan and targets
- infer `order` and `include_count` heuristically from the question
- only ask the LLM for `condition` when needed

This version also allows count-output variants such as `country + count(*)` to stay
 inside `COUNT_FAMILY_TOP1`, while still excluding per-group percentage extrema.

Phase 5：再做 learned router
到了这一步，训练就不是“拍脑袋先训一个分类器”，而是建立在已经跑通的系统之上。

此时 learned router 的价值非常明确：

用来替代初始 prompt-based router
提高判别稳定性
降低额外 LLM 调用成本
做更细粒度的 per-template 置信度预测
我建议的最现实路线

如果只说你现在最该做什么，我会建议按这个顺序：

先不微调，不标注
先实现 LLM-as-router
再把 validation + threshold + fallback 接完整
用 dev 做阈值扫描，确认能否做到接近或不低于 baseline
同时把日志记全，为下一步训练 learned router 做准备
最后再决定是否要标注/微调
也就是说，后续路线不是：

先训 router -> 再想系统怎么接

而应该是：

先把系统接好 -> 再用系统日志反哺 router 训练

一句话总结

当前系统保留 A/B/C/D/E，并在 Phase 1d 增加 schema-enumerated 的 `ENTITY_BY_RELATED_COUNT_TOP1` 专家，在 Phase 1e 为 count-family 模板增加 structured projection selector。
router 在最终形态下可以涉及标注和微调，但现阶段不必
完全可以先假设基座模型具备判别能力，先实现非训练版 router
最合理的路线是：
