# GroundedSQL-Agent 实验说明

## 1. 实验目标

本项目的实验并不只关注最终 benchmark 分数，还关注以下几个问题：

1. 纯基础模型与纯微调模型的能力边界分别在哪里？
2. 完整 Agent 系统相比纯模型的主要增益来自哪里？
3. `value grounding` 是否真的是关键模块？
4. 历史结构化路线（如 `phase1_d`、`phase2_a`）在整个系统中处于什么位置？

因此，当前公开实验主要分为三类：

- 纯模型实验
- 正式基线实验
- 关键消融 / 历史路线对照实验

---

## 2. 实验环境与评测方式

### 数据集

- Spider development set
- `data/dev.json`
- `data/dev_gold.sql`
- `data/tables.json`

### 数据库目录

当前仓库示例命令默认使用：

- `data/database`

如果你的数据库实际位于：

- `data/testsuitedatabases/database`

请替换对应参数。

### 评测方式

统一采用 Spider 官方评测工具进行：

- `Execution Accuracy`
- `Exact Match Accuracy`

这意味着最终结果不是主观打分，而是基于真实数据库执行与标准答案比较得到。

---

## 3. 主要实验设置

### 3.1 基础模型 zero-shot（v1 schema）

用于刻画未微调基础模型的起点能力。

```bash
python finetune_tools/main_eval.py \
  --base-model /root/autodl-tmp/qwen2.5-coder-7b-instruct \
  --no-lora \
  --schema-format v1 \
  --output-file predict_base_zero_shot_v1.txt
```

### 3.2 基础模型 zero-shot（v6 schema）

用于观察不同 schema 表达格式对未微调模型的影响。

```bash
python finetune_tools/main_eval.py \
  --base-model /root/autodl-tmp/qwen2.5-coder-7b-instruct \
  --no-lora \
  --output-file predict_base_zero_shot_v6.txt
```

### 3.3 纯微调模型

用于评估 LoRA 微调本身带来的能力提升。

```bash
python finetune_tools/main_eval.py \
  --base-model /root/autodl-tmp/qwen2.5-coder-7b-instruct \
  --lora-path /root/autodl-tmp/LLaMA-Factory/saves/Qwen2.5-7B/lora/qwen_spider_lora_v6 \
  --output-file predict_pure_finetuned.txt
```

### 3.4 正式最优基线（formal final）

这是当前项目公开版的正式最优系统。

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

### 3.5 关闭 value grounding 的消融

这是当前最关键的一组消融实验。

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

### 3.6 历史结构化路线

#### `legacy_phase1_d`

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

#### `legacy_phase2_a`

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

## 4. 核心结果

### 4.1 Execution Accuracy 总表

| 设置 | Execution Accuracy |
|---|---:|
| 基础模型 zero-shot（`v1` schema） | 71.5 |
| 基础模型 zero-shot（`v6` schema） | 70.5 |
| 纯微调模型 | 74.2 |
| 完整 Agent（关闭 value grounding） | 75.4 |
| 历史结构化路线 `phase1_d` | 79.2 |
| 历史统一 count-family 路线 `phase2_a` | 78.4 |
| **正式最优基线** | **79.3** |

### 4.2 正式最优基线分难度结果

| 难度 | Execution Accuracy |
|---|---:|
| Easy | 94.4 |
| Medium | 85.0 |
| Hard | 71.3 |
| Extra | 50.0 |
| **All** | **79.3** |

### 4.3 正式最优基线 Exact Match

| 指标 | 数值 |
|---|---:|
| Exact Match Accuracy | 78.6 |

---

## 5. 如何解读这些结果

### 5.1 微调是必要的，但不是终点

从：

- `71.5`（base zero-shot v1）

到：

- `74.2`（pure fine-tuned）

可以看到监督微调本身是有效的。  
但这也说明：仅靠纯模型直出 SQL，依然无法稳定处理更复杂的问题。

### 5.2 最大单步增益来自 value grounding

从：

- `75.4`（w/o value grounding）

到：

- `79.3`（formal final）

这是当前系统里最关键的一步提升。

因此，本项目的最核心实验结论是：

> 当模型本身已经具备一定 SQL 生成能力后，系统瓶颈会从 schema-level 转向 value-level。

### 5.3 结构化路线是有效支线，但不是主增益来源

`phase1_d` 和 `phase2_a` 的结果表明，结构化专项路线在局部题型上是有效的。  
但从整个项目的演化来看，最终系统的主增益来源仍然是：

- value grounding
- retrieval 收敛
- 风险控制与 fallback

因此它们更适合作为：

- 结构化探索支线

而不是整个项目的唯一主线。

---

## 6. 当前正式基线的定位

当前公开版 formal final 并不是“所有模块都尽量打开”的结果，而是一个经过收敛后的系统：

- retrieval 使用保守子图构建策略
- value grounding 开启
- structured route 保留，但不过度扩张
- verifier 采用保守风险控制
- fallback 机制用于防止误修
- explainability 输出作为分析与展示支撑

因此，formal final 的意义不仅在于它是当前最高公开分数，更在于它是：

- 最稳
- 最易解释
- 最适合公开展示与复现

的一版系统。

---

## 7. 可解释性分析

每轮实验完成后，可以通过以下脚本生成静态解释性网页：

```bash
python scripts/analysis/build_explainability_dashboard.py \
  --experiment-dir experiments/formal_final \
  --title "GroundedSQL-Agent Dashboard · Formal Final"
```

网页中可查看：

- 本轮实验总览
- official metrics（若存在 `metrics.json`）
- route / skill 分布
- retrieval explanation
- value grounding evidence
- verifier / fallback 信息
- 单题 SQL、单题轨迹与单题 explanation

这一部分用于支持：

- 结果复盘
- 消融归因
- 面向保研答辩或老师展示的可解释性材料

---

## 8. 总结

从实验上看，GroundedSQL-Agent 的最重要贡献并不是“又加了一个模板”或“又堆了一个 prompt 技巧”，而是：

1. 明确识别出 value-level grounding 是后期系统的主要瓶颈  
2. 围绕这一瓶颈，将纯微调模型升级为 retrieval-enhanced、value-aware、risk-controlled 的数据库 Agent  
3. 通过 explainability dashboard 把系统行为显式化、可分析化

因此，当前正式基线不仅是一个分数结果，也是一套具有清晰演化逻辑的系统方案。

