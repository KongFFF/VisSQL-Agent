# GroundedSQL-Agent 系统概览

## 1. 项目定位

GroundedSQL-Agent 是一个面向复杂 Text-to-SQL 任务的数据库智能代理系统。  
它以一个 7B 级开源代码模型的微调版本为基础，在此之上引入：

- schema retrieval
- value grounding
- specialized route
- semantic verification
- explainability

目标不是单纯让模型“猜”一条 SQL，而是让 SQL 生成过程尽量建立在**真实数据库结构、真实值证据和真实执行反馈**之上。

---

## 2. 核心问题

在 Spider 这类跨库 Text-to-SQL 任务中，纯端到端模型常见的错误主要包括：

1. 值落错列  
   例如实体名、代码值、枚举值被错误绑定到不合理列上。

2. schema 子图选择不稳  
   模型可能知道大致要查什么，但会选错表、漏掉关键表，或者多连不必要的表。

3. 结构稳定子任务仍然容易错  
   如 superlative、top-1、count-family、group-by grain 等。

4. SQL 可执行但语义错误  
   即运行不报错，但和题意不一致。

GroundedSQL-Agent 的设计就是围绕这四类问题展开。

---

## 3. 系统总流程

可以把当前正式系统理解为下面这条流水线：

```text
Question
  -> Schema Retrieval
  -> Value Grounding
  -> Optional Specialized Route
  -> Generic SQL Generation
  -> Execution
  -> Semantic Verification / Fallback
  -> Final SQL
```

其中每个阶段的职责如下。

---

## 4. 模块说明

### 4.1 Schema Retrieval

系统不会把整个数据库 schema 原样全量输入模型，而是根据问题动态选出一个**紧凑 schema 子图**。  
当前 retrieval 的核心特点包括：

- 从问题中选择若干 seed tables
- 控制 top-k schema 子图大小
- 保留关键外键关系与 join path
- 在当前版本中采用保守策略，避免无谓扩张

这一步的主要作用是：

- 减少模型注意力被无关表干扰
- 保住真正关键的 schema 上下文

---

### 4.2 Value Grounding

这是当前系统最重要的增强模块之一。

系统不仅要知道“该查哪张表、哪一列”，还要知道：

- 问题中的值最可能落在哪些列
- 值的字面形式和数据库内部取值是否一致
- 某些枚举值、代码值、实体值是否需要额外证据支持

因此当前系统会在 schema retrieval 之后引入 value-level evidence，例如：

- question entities
- candidate value columns
- sampled values
- entity-to-column matches

这一步让系统从单纯的 `schema-aware` 升级为 `schema + value-aware`。

---

### 4.3 Specialized Route

对于少量结构稳定的问题，系统会先尝试一条更高精度的 specialized route。  
目前主要覆盖：

- superlative
- top-1
- count-family

这一部分不是当前系统总增益的主要来源，但它体现了一个重要方向：

- 对局部稳定子任务做 skill-like routing
- 在高置信场景下使用结构化处理，而不是一律依赖 generic SQL generation

在当前公开版本中，这条路线被保留为一个**低覆盖、高精度的专项模块**。

---

### 4.4 Generic SQL Generation

当 specialized route 不适用或不够置信时，系统回退到主路径：

- 用 fine-tuned backbone model
- 基于 retrieval 后的 schema prompt
- 直接生成 SQL

因此，GroundedSQL-Agent 并不是“模板系统”，它的主干依然是：

- 检索增强的通用 SQL 生成模型

---

### 4.5 Execution

生成的 SQL 会在真实 SQLite 数据库上执行。  
这一层的作用包括：

- 判断 SQL 是否可执行
- 捕获 runtime error
- 记录 row count、执行时间等信息

执行器不仅服务于“能不能跑”，也为后续的 semantic verification 与 fallback 提供依据。

---

### 4.6 Semantic Verification / Fallback

即使 SQL 可以执行，也不代表它一定符合题意。  
因此系统还会做一层保守的语义风险识别，例如：

- 某些值是否可能挂错列
- projection / aggregation 是否明显不合理
- 当前结果是否应该触发语义级重试

但当前公开版本采取的是**保守策略**：

- 不让 verifier 过度干预
- 保留 success fallback
- 避免“把本来正确的 SQL 改坏”

这部分体现的是系统的 `risk-controlled` 特征。

---

### 4.7 Explainability Dashboard

每轮实验结束后，系统可以基于日志自动生成静态网页，用于展示：

- 总体实验指标
- route / skill 分布
- retrieval explanation
- value grounding evidence
- verifier / fallback 信息
- 单题案例详情

这一部分不是直接提分模块，但对于：

- 错误分析
- 消融归因
- 项目展示

非常重要。

---

## 5. 当前正式基线的配置特点

当前公开的 formal best 基线具有以下特征：

- 使用 retrieval 驱动的 schema 子图，而非全 schema
- 开启 value grounding
- 保留 specialized route，但不让它主导整个系统
- 使用保守列提示
- 使用 seed-first retrieval 保留策略
- 使用保守 semantic verifier + fallback
- 支持 explainability dashboard 生成

它强调的是：

- 稳定
- 可解释
- 风险可控

而不是盲目叠加复杂模块。

---

## 6. 核心结论

从系统设计角度，GroundedSQL-Agent 最重要的结论有两点：

1. Text-to-SQL 的后期瓶颈会从 schema-level 逐步转向 value-level  
   因此，value grounding 是整个项目中最关键的增强方向。

2. 一个可靠的 Text-to-SQL Agent 不应只有“生成”，还应同时具备：  
   retrieval、routing、execution、verification 和 explanation。

也正因为如此，GroundedSQL-Agent 更适合被理解成：

- 一个 retrieval-enhanced, value-aware, risk-controlled database agent

而不是一个单纯的 SQL 生成模型。

