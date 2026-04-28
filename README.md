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
