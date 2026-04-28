# GroundedSQL-Agent

GroundedSQL-Agent 是一个面向复杂 Text-to-SQL 任务的数据库智能代理系统，通过将自然语言问题与真实数据库中的 **schema、value 和 execution evidence** 对齐，把纯微调 SQL 生成模型升级为一个 **retrieval-enhanced、value-aware、risk-controlled、explainable** 的数据库 Agent。

当前公开版本在 Spider 开发集上的正式最优基线达到：

- **Execution Accuracy: 79.3%**
- **Exact Match Accuracy: 78.6%**

---

## 1. 项目简介

在 Text-to-SQL 场景中，纯端到端模型即使经过监督微调，仍然会在以下方面频繁出错：

- 值与列的错误对齐（literal 绑定错误）
- 多表关系下的 schema 选择与 join 路径选择
- superlative / count-family 等结构稳定子任务
- SQL 可执行但语义错误的“隐性失败”

GroundedSQL-Agent 的核心目标，不是继续堆叠更大的模型，而是围绕真实数据库执行过程，把系统从“单次直出 SQL”升级成“带检索、带路由、带验证、带解释”的数据库智能代理。

当前公开版系统主要包含以下能力：

- **Schema Retrieval**：针对每个问题动态选择紧凑的 schema 子图，而不是把整库 schema 全量输入模型
- **Value Grounding**：补充值级证据，使实体值、枚举值、代码值更容易落到正确列上
- **Specialized Route**：为部分结构稳定的 superlative / count-family 问题提供高精度专项路径
- **Semantic Risk Control**：在 SQL 可执行之后继续做语义风险识别与保守 fallback
- **Explainability Dashboard**：为每轮实验自动生成静态网页，展示检索、值匹配、路由、verifier 与单题轨迹

---

## 2. 仓库结构

```text
GroundedSQL-Agent/
|-- agent/                # Agent 核心：coder / executor / memory / main agent class
|-- retrieval/            # schema retrieval 与 schema prompt 构造
|-- superlative/          # 结构化专项路径（superlative / count-family）
|-- verifier/             # semantic verifier
|-- finetune_tools/       # 纯模型 / 纯微调模型评测脚本
|-- spider_eval/          # Spider 官方评测相关工具
|-- scripts/analysis/     # 分析脚本、dashboard 生成器等
|-- experiments/          # 各轮实验输出目录
|-- data/                 # Spider dev 数据、gold SQL、tables、数据库
|-- main_eval_agent.py    # 完整 Agent 系统正式评测入口
`-- README.md
```

---

## 3. 环境与数据

### 推荐环境

- Python 3.10+
- 支持 CUDA 的 GPU 环境
- Spider 开发集相关文件：
  - `data/dev.json`
  - `data/dev_gold.sql`
  - `data/tables.json`
  - 数据库根目录（通常为以下之一）：
    - `data/database`
    - `data/testsuitedatabases/database`

### 关于数据库路径

本仓库当前 README 中的命令默认使用 `data/database`，因为这是当前项目目录下最直接的本地布局。  
如果你的 Spider 数据库存放在 `data/testsuitedatabases/database`，只需要替换命令中的 `--db-root` 或 `--db` 路径即可。

---

### 模型与权重说明

本仓库的正式系统建立在 **Qwen2.5-Coder-7B-Instruct** 基座模型之上，公开推理代码默认加载的最终 LoRA 检查点名称为：

- 基座模型：`/root/autodl-tmp/qwen2.5-coder-7b-instruct`
- 最终推理权重：`/root/autodl-tmp/LLaMA-Factory/saves/Qwen2.5-7B/lora/qwen_spider_lora_v6`

出于仓库体积、分发成本与版本管理的考虑，**本仓库不直接托管 LoRA 权重文件本体**；公开仓库主要提供：

- 完整代码实现
- 数据构造脚本
- 推理与评测命令
- 实验结果与分析文档

如需完整复现，可基于仓库中的数据构造与训练配置重新训练，或在具备权重文件的环境中直接加载推理。

#### 训练数据格式

项目最终公开版本使用的是 `finetune_tools/format_data_v6.py` 所构造的 **ShareGPT 风格 SFT 数据**，输出文件为：

- `data/spider_sharegpt_v6.json`

每条样本由三轮对话组成：

1. `system`：要求模型扮演数据库架构师与 SQL 专家，只输出 SQL
2. `user`：输入内容为 **v6 schema 表示 + 自然语言问题**
3. `assistant`：目标 SQL

其中，`v6 schema` 不是简单的 `CREATE TABLE` 平铺文本，而是由 `build_schema_dict_v6(...)` 构造的 **中文半结构化 schema 表示**，其特点包括：

- 以数据库为单位组织
- 显式写出表与字段
- 用自然语言补充主键（PK）与外键（FK）语义
- 在输入层面对 schema 结构进行压缩与重组，以提升模型对关系结构的理解效率

#### 训练/推理配置（仓库中可确认的部分）

结合 `finetune_tools/main_eval.py`、`finetune_tools/llm_inference_v6.py`、训练记录以及最终 `adapter_config`，本项目最终公开版本可确认的关键配置如下：

**训练范式**

- 任务类型：`CAUSAL_LM`
- 微调方式：**SFT + LoRA**
- PEFT 类型：`LORA`
- PEFT 版本：`0.18.1`
- 基座模板：`qwen`

**LoRA 关键参数**

- `r = 64`
- `lora_alpha = 128`
- `lora_dropout = 0`
- `bias = "none"`
- `use_dora = false`
- `use_qalora = false`
- `use_rslora = false`
- `inference_mode = true`（当前公开权重用于推理加载）

这意味着最终公开版本采用的是一组**较高 rank、无 dropout 的标准 LoRA 适配器配置**，重点追求在 Spider 文本到 SQL 任务上的稳定拟合能力，而不是进一步叠加 DoRA / QALoRA / RSLoRA 等额外变体。

**输入与推理配置**

- 推理输入格式：`system prompt + v6 schema + question`
- `v6 schema`：中文半结构化 schema 表示，显式补充 PK/FK 语义
- 推理解码方式：`do_sample = false`（贪心解码）
- 最大生成长度：`max_new_tokens = 512`
- 推理数值精度：`bfloat16`

**权重依赖关系**

- `base_model_name_or_path = /root/autodl-tmp/qwen2.5-coder-7b-instruct`
- 最终 LoRA 权重在该基座模型上加载
- 当前公开仓库不包含 adapter 权重文件本体，但保留完整的加载路径、推理代码与实验命令

仓库中保留的历史训练记录还显示，项目在多个版本中持续采用 LoRA 监督微调范式，并围绕以下维度做过系统试验：

- schema 表达格式（`v1 / v3 / v5 / v6`）
- LoRA rank / alpha
- 是否使用 DoRA
- 上下文长度（如 `2048 / 3072 / 4096`）
- 数据集版本（如 `spider_sql / spider_golden / spider_v5`）

对外公开时，本项目重点展示的是最终用于正式系统推理的 **`qwen_spider_lora_v6`** 及其对应的数据格式与完整实验结果，而不是将所有历史训练权重一并托管到仓库中。

## 4. 运行方式

### 4.1 微调前基础模型（无 LoRA）

该实验用于评估纯基础模型在 Spider 上的零样本性能。

```bash
python finetune_tools/main_eval.py \
  --base-model /root/autodl-tmp/qwen2.5-coder-7b-instruct \
  --no-lora \
  --schema-format v1 \
  --output-file predict_base_zero_shot_v1.txt
```

官方评测：

```bash
python -m spider_eval.evaluation \
  --gold data/dev_gold.sql \
  --pred predict_base_zero_shot_v1.txt \
  --db data/database \
  --table data/tables.json \
  --etype all
```

---

### 4.2 纯微调模型（不启用 Agent）

该实验用于评估 LoRA 微调模型本身的端到端 SQL 生成能力。

```bash
python finetune_tools/main_eval.py \
  --base-model /root/autodl-tmp/qwen2.5-coder-7b-instruct \
  --lora-path /root/autodl-tmp/LLaMA-Factory/saves/Qwen2.5-7B/lora/qwen_spider_lora_v6 \
  --output-file predict_pure_finetuned.txt
```

官方评测：

```bash
python -m spider_eval.evaluation \
  --gold data/dev_gold.sql \
  --pred predict_pure_finetuned.txt \
  --db data/database \
  --table data/tables.json \
  --etype all
```

---

### 4.3 正式最优基线（完整 Agent 系统）

这是当前公开版本的正式最优配置。

```bash
python main_eval_agent.py \
  --entrypoint formal \
  --base-model /root/autodl-tmp/qwen2.5-coder-7b-instruct \
  --lora-path /root/autodl-tmp/LLaMA-Factory/saves/Qwen2.5-7B/lora/qwen_spider_lora_v6 \
  --dev-path data/dev.json \
  --tables-path data/tables.json \
  --db-root data/database \
  --output-dir experiments/formal_final \
  --max-retries 3
```

该命令会在实验目录下生成：

- `predict_agent.txt`
- `agent_run_summary.jsonl`
- `agent_trajectories.jsonl`
- `metrics.json`（若本地官方评测依赖完整）

如需手动调用官方评测：

```bash
python -m spider_eval.evaluation \
  --gold data/dev_gold.sql \
  --pred experiments/formal_final/predict_agent.txt \
  --db data/database \
  --table data/tables.json \
  --etype all
```

---

### 4.4 关键消融实验

#### (a) 关闭 value grounding

```bash
python main_eval_agent.py \
  --entrypoint experiment \
  --experiment value_hints_off \
  --base-model /root/autodl-tmp/qwen2.5-coder-7b-instruct \
  --lora-path /root/autodl-tmp/LLaMA-Factory/saves/Qwen2.5-7B/lora/qwen_spider_lora_v6 \
  --dev-path data/dev.json \
  --tables-path data/tables.json \
  --db-root data/database \
  --output-dir experiments/ablation_value_hints_off \
  --max-retries 3
```

#### (b) 历史结构化路线 `phase1_d`

```bash
python main_eval_agent.py \
  --entrypoint experiment \
  --experiment legacy_phase1_d \
  --base-model /root/autodl-tmp/qwen2.5-coder-7b-instruct \
  --lora-path /root/autodl-tmp/LLaMA-Factory/saves/Qwen2.5-7B/lora/qwen_spider_lora_v6 \
  --dev-path data/dev.json \
  --tables-path data/tables.json \
  --db-root data/database \
  --output-dir experiments/legacy_phase1_d \
  --max-retries 3
```

#### (c) 历史统一 count-family planner `phase2_a`

```bash
python main_eval_agent.py \
  --entrypoint experiment \
  --experiment legacy_phase2_a \
  --base-model /root/autodl-tmp/qwen2.5-coder-7b-instruct \
  --lora-path /root/autodl-tmp/LLaMA-Factory/saves/Qwen2.5-7B/lora/qwen_spider_lora_v6 \
  --dev-path data/dev.json \
  --tables-path data/tables.json \
  --db-root data/database \
  --output-dir experiments/legacy_phase2_a \
  --max-retries 3
```

---

### 4.5 可解释性网页生成

每轮实验完成后，可以从日志中自动生成一套静态解释性网页：

```bash
python scripts/analysis/build_explainability_dashboard.py \
  --experiment-dir experiments/formal_final \
  --title "GroundedSQL-Agent Dashboard · Formal Final"
```

会生成：

- `experiments/formal_final/dashboard/index.html`
- `experiments/formal_final/dashboard/data.json`

网页中可查看：

- 本轮实验总览
- route / skill 分布
- retrieval explanation
- value grounding 证据
- verifier / fallback 信息
- 单题案例详情与轨迹

---

## 5. 主要实验结果

以下结果来自当前项目最终实验记录。

### Spider 开发集 Execution Accuracy

| 设置 | Execution Accuracy |
|---|---:|
| 基础模型 zero-shot（`v1` schema） | 71.5 |
| 基础模型 zero-shot（`v6` schema） | 70.5 |
| 纯微调模型 | 74.2 |
| 完整 Agent（关闭 value grounding） | 75.4 |
| 历史结构化路线 `phase1_d` | 79.2 |
| 历史统一 count-family 路线 `phase2_a` | 78.4 |
| **正式最优基线** | **79.3** |

### 正式最优基线分难度表现

| 难度 | Execution Accuracy |
|---|---:|
| Easy | 94.4 |
| Medium | 85.0 |
| Hard | 71.3 |
| Extra | 50.0 |
| **All** | **79.3** |

### 正式最优基线 Exact Match

| 指标 | 数值 |
|---|---:|
| Exact Match Accuracy | 78.6 |

---

## 6. 主要结论

### 6.1 项目最关键的增益来自 value-level grounding

本项目最重要的经验结论是：  
当纯微调模型已经具备一定 SQL 生成能力后，系统瓶颈会从 **schema-level understanding** 逐步转向 **value-level grounding**。

这一点由如下对比直接支持：

- 正式最优基线：**79.3**
- 去掉 value grounding：**75.4**

也就是说，本项目的关键提升并不只是“多给一些 prompt 信息”，而是让系统在值层面具备：

- 值到列的更可靠绑定
- 实体名 / 代码值 / 枚举值的更合理解释
- 条件 literal 的更稳定落列

### 6.2 结构化专项路线是有效支线，但不是总增益主来源

项目中保留了一条针对 superlative / count-family 子任务的高精度结构化路线。  
这条路线对局部结构稳定题型有效，但整体最主要的增益仍然来自：

- value grounding
- retrieval 子图构建
- 保守的 semantic risk control

### 6.3 最终公开基线强调“收敛”和“稳健”

在开发过程中，一些看起来更复杂的设计并没有稳定提升结果，例如：

- 过于激进的 semantic verification
- 过于激进的列提示暴露
- 缺少精度约束的复杂 bridge / graph 风格增强

因此最终公开版基线更强调：

- 保守但有效的 retrieval
- 值级证据增强
- 风险控制与 fallback
- 可解释性输出

---

## 7. 项目价值

GroundedSQL-Agent 并不是一个单纯的 prompt engineering 结果，也不是一个只追求 benchmark 数字的脚本集合。  
它更像是一个围绕真实数据库执行闭环展开的系统研究项目，展示了如何从：

- 一个纯微调的 SQL 生成模型

逐步演化到：

- 一个带检索增强、值级 grounding、结构化专项路由、风险控制与解释能力的数据库智能代理

从项目表达上，这个仓库主要体现两层价值：

1. 一个可复现的 7B 级 Text-to-SQL Agent 基线  
2. 一条清晰的系统演化路线：从纯模型到 value-aware agent

---

## 8. 说明

- 项目内部研发笔记与保研材料并未全部公开，公开仓库只保留适合对外展示的代码与文档。
- `docs/` 目录建议用于放置整理后的外部展示材料，而不是原始开发记录。
- 某些官方评测依赖（如 `sqlparse`、NLTK 分词环境）需要在本地 Python 环境中自行准备。

---

## 9. 使用建议

如果保研老师或项目评审希望快速理解本项目，建议优先查看以下入口：

- 纯模型 / 纯微调模型评测：`finetune_tools/main_eval.py`
- 正式 Agent 评测入口：`main_eval_agent.py`
- 可解释性网页生成：`scripts/analysis/build_explainability_dashboard.py`

如果需要展示项目整体能力，最推荐直接提供：

- 本仓库链接
- `formal_final` 对应的正式基线实验结果
- 对应的 explainability dashboard 页面
