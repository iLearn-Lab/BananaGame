# LLM 选型建议清单（效果优先、不计成本）

本文档基于 `docs/AI_USAGE.md` 中的**全部 LLM 使用场景**，按「效果最佳」原则给出模型选择建议，并附理由、事实数据与实施方式。当前你使用云雾(yunwu.ai) 作为 API 聚合，其支持近 400 款模型（含 Claude、GPT、Gemini、DeepSeek 等），以下推荐均可在云雾或同架构 API 下配置。

**✅ 已按本清单完成修改：主剧情与图像提示词均已切换为云雾支持的 `claude-opus-4-6`（.env 的 Camera_Analyst_MODEL + prompt_optimize.py 改为读配置，默认 claude-opus-4-6）。**

---

## 一、使用场景分组与推荐总表

| 场景分组 | 当前使用 | 推荐模型（效果优先） | 主要理由 |
|----------|----------|----------------------|----------|
| **A. 主剧情 / 世界观 / 选项 / 结局 / 角色深化** | **claude-opus-4-6**（已切换） | **Claude Opus 4.6** | 长文创意与叙事一致性、编剧级判断、复杂指令遵循均属顶级；基准与人类评估中 Opus 领先。 |
| **B. 配角身份揭示（短抽取）** | **claude-opus-4-6**（与 A 同） | **与 A 同模型** | 任务简单但需与主线设定一致；统一用最强模型保证抽取准确、别名不冲突。 |
| **C. 图像提示词优化（场景 + 主角）** | **claude-opus-4-6**（已改为读配置） | **Claude Opus 4.6** | 已与主剧情共用 Camera_Analyst_MODEL，默认 claude-opus-4-6。 |

下面按场景细说理由与数据，再给出具体配置清单。

---

## 二、场景 A：主剧情、世界观、选项剧情、结局、角色深化

### 2.1 包含的用法

- 世界观生成（`llm_generate_global`）
- 单层后续剧情（`llm_generate_local`）
- 选项剧情文本（`_generate_single_option` / `_generate_single_option_text_only`）
- 后台补全世界观（`worldview/template.py`）
- 结局预测与修改（`generate_ending_prediction` / `modify_ending_tone` / `modify_ending_content`）
- 隐藏剧情深化、角色深层背景（`adventure.py`）

特点：**长上下文、强创意、需严格格式（场景/选项/flow）、多轮一致性（角色/基调/主线）**。

### 2.2 推荐：Claude Opus 4（或 Opus 4.5）

**理由与事实：**

1. **创意写作与长叙事基准**
   - **EQ-Bench Longform Creative Writing**（2025）：评估多轮、每轮约 1000 词的 8 轮写作，从构思、规划到成文；评分维度包括角色发展、情感张力、情节连贯、文风问题等 14 项；评委已升级为 Claude Sonnet 4。该基准专门针对「长文创意叙事」，与你的世界观 + 多轮剧情高度相关。
   - **LLM Creative Story-Writing Benchmark V4**（lechmazur/writing）：在「强制 10 个故事要素 + 文学质量」上排名（2024–2025）中，**Claude Opus 4.6 / 4.6 Thinking** 与 **GPT-5.2 / GPT-5 Pro** 居前列（约 8.5+ 分），**Gemini 3 Pro Preview** 约 8.2；Claude Opus 在「要素融入」与整体叙事质量上表现最佳。
   - 结论：在**长叙事、多要素、角色一致性**上，Opus 4 系列处于第一梯队，且与「编剧级」结局判断、角色深化需求匹配。

2. **推理与复杂指令**
   - MMLU 等综合基准：Opus 4.5 约 90.8%，Sonnet 4.5 约 89.1%；Opus 在复杂推理与长程依赖上略优。
   - 你的任务里：结局基调判断（HE/BE/NE）、结局内容微调、隐藏剧情揭秘与角色深化，都依赖**对前文与设定的深度理解**，Opus 的推理深度更利于减少逻辑断裂和 OOC。

3. **上下文与稳定性**
   - Opus 4.5 支持约 200k token 上下文（Sonnet 约 200k 级别），对「世界观 + 当前进度 + 多选项」的长 prompt 更友好。
   - 长文生成中，顶模在「后半段质量衰减」上相对更轻（EQ-Bench 等报告），有利于多章、多选项的连贯性。

**数据来源（可查证）：**

- EQ-Bench Creative Writing Longform: https://eqbench.com/creative_writing_longform.html  
- lechmazur/writing V4 排名: GitHub creative-writing-bench 及多篇 2024–2025 模型对比文章  
- Claude Opus 4.5 vs Sonnet 4.5 能力对比: Anthropic 官方介绍与 MMLU/综合指数（如 Artificial Analysis）

**若云雾未上架 Opus 4.5：** 优先用 **Claude Opus 4**（如 `claude-opus-4` 等，以云雾控制台模型 ID 为准）；其次可考虑 **GPT-4o** 或 **GPT-5 系列**（创意写作基准中同样顶尖）。

---

## 三、场景 B：配角身份揭示（从剧情中抽取「A 就是 B」）

### 3.1 包含的用法

- `update_supporting_role_aliases_from_plot`：从剧情片段中识别「身份揭示」关系，更新配角别名（如「黑衣人就是艾玛」→ 别名表新增）。

特点：**短文本输入、短输出、格式固定（「原名|新身份」）、需与已有配角表一致**。

### 3.2 推荐：与场景 A 同模型（Claude Opus 4）

**理由：**

- 抽取本身不难，但**错误会污染配角档案**，影响后续剧情与选项中的称呼一致性。
- 使用与主剧情**同一套模型**，可避免「主剧情用 Opus、抽取用弱模型」导致的风格/理解偏差；且单次调用 token 少，成本增加有限。
- 在「严格按格式输出 + 不捏造身份」上，强指令遵循模型更稳，Opus 4 足够且与 A 统一运维。

**结论：** 不单独为 B 选模型，**Camera_Analyst_MODEL 设为 Opus 4 即可同时覆盖 A 与 B**。

---

## 四、场景 C：图像提示词优化（场景图 + 主角形象）

### 4.1 包含的用法

- `optimize_image_prompt_with_llm`：把**剧情文本**转成**场景图视觉描述提示词**（风格/主角/配角/连续性一致）。
- `optimize_main_character_prompt_with_llm`：生成**主角立绘用提示词**（姓名、性别、外貌、风格等）。

特点：**输入为剧情/设定（中文为主），输出为给生图模型用的描述（中英混合、关键词明确、无多余解释）**；需强指令遵循与「视觉语言」能力。

### 4.2 当前实现

- 代码中**写死** `deepseek-v3.2`，未读 `Camera_Analyst_MODEL`（见 `src/image/prompt_optimize.py` 约 247、392 行）。

### 4.3 推荐：Claude Opus 4 或 GPT-4o

**理由与事实：**

1. **指令遵循与结构化输出**
   - 研究（如 JSONSchemaBench、Structured Outputs 对比）表明：**GPT-4o** 在复杂 JSON/结构化输出上可靠度最高；**Claude Sonnet/Opus** 通过规范 prompt 也能达到高服从度。
   - 你的需求是「只输出视觉描述、不要解释、不要 URL/代码块」——属于强约束的文本生成，顶模更少出现多余前缀/后缀，便于下游解析。

2. **视觉与描述能力**
   - 多模态/视觉语言评估（如 GenAI-Bench、T2I 相关论文）关注「组合式描述、属性与关系」；能更好理解「主角参考 Image 0、配角参考 Image 3」等说明的，多为具备强语言与推理能力的模型。
   - Claude / GPT 系列在「把抽象设定转成具体视觉关键词」上经验更多，且文档与社区案例丰富，便于你后续微调 prompt。

3. **与生图模型配合**
   - 你当前生图为 **Gemini 3 Pro Image Preview**；提示词模型与生图模型不必同厂，但**描述清晰、关键词准确**更能发挥生图效果。Opus 4 / GPT-4o 在长说明 + 多约束下的表现优于多数开源模型。

**若考虑延迟与调用量：**  
- 图像提示词调用频繁时，若希望略降延迟，可单独为 C 配置 **Claude Sonnet 4** 或 **GPT-4o-mini**，在「效果优先」前提下仍建议至少 **Sonnet 4** 或 **GPT-4o**，不推荐再弱一档。

**结论：**  
- **效果优先**：将场景 C 的模型改为 **Claude Opus 4**（与 A/B 统一）或 **GPT-4o**。  
- **实现方式**：需要改代码——把 `prompt_optimize.py` 里写死的 `deepseek-v3.2` 改为从配置读取（例如与 `AI_API_CONFIG` 一致，或单独增加 `IMAGE_PROMPT_LLM_MODEL` 环境变量），再在 .env 中设为 `claude-opus-4` 或对应 ID。

---

## 五、推荐汇总与实施清单

### 5.1 模型与场景对应表（效果优先）

| 配置项 / 代码位置 | 当前 | 推荐（效果最佳） | 说明 |
|-------------------|------|------------------|------|
| **Camera_Analyst_MODEL**（.env） | ~~claude-sonnet-4-20250514~~ → **claude-opus-4-6**（已改） | **claude-opus-4-6** | 覆盖：世界观、剧情、选项、结局、角色深化、配角身份揭示（A+B） |
| **图像提示词用 LLM**（`prompt_optimize.py`） | ~~写死 deepseek-v3.2~~ → **读 AI_API_CONFIG.model**（已改） | **与上同：claude-opus-4-6**（默认） | 场景图 + 主角形象提示词（C）；已改为读配置 |

### 5.2 理由与数据小结（摆事实、讲道理）

- **主剧情/创意长文**：EQ-Bench Longform、lechmazur writing V4 等将 **Claude Opus 4.x** 与 **GPT-5.x** 排在创意叙事前列；Opus 在角色一致性、情节连贯与编剧级判断上有优势，且上下文与推理深度适合你的多轮、多选项设定。  
- **结构化与指令**：世界观/选项/结局均需严格格式与指令遵循；综合基准与结构化输出评测中，Opus 4 / GPT-4o 处于第一梯队，适合作为「唯一主模型」统一 A+B。  
- **图像提示词**：无专门「剧情→生图提示词」公开基准，但任务本质是「强约束 + 视觉描述」；闭源顶模在类似任务上表现更稳，故推荐 Opus 4 或 GPT-4o，并建议改为配置化以便后续 A/B 测试。

### 5.3 实施步骤（清单）

1. **仅改 .env（立刻生效 A+B）**  
   - 将 `Camera_Analyst_MODEL` 改为云雾支持的 **Claude Opus 4** 模型 ID（如 `claude-opus-4-20250514` 或控制台展示的 ID）。  
   - 主剧情、选项、结局、角色深化、配角身份揭示将全部走 Opus 4。

2. **改代码 + 配置（让 C 也用顶模）**  
   - 在 `src/image/prompt_optimize.py` 中：  
     - 两处 `"model": "deepseek-v3.2"` 改为从 `AI_API_CONFIG.get("model", "claude-opus-4-xxx")` 读取（或新增环境变量 `IMAGE_PROMPT_LLM_MODEL` 单独指定）。  
   - 在 `.env` 中：  
     - 若与主剧情统一，无需新增变量；若希望图像提示词单独用 GPT-4o，则新增 `IMAGE_PROMPT_LLM_MODEL=gpt-4o` 并在代码中读该变量（需同时支持 base_url/api_key 或使用云雾同一 key）。  
   - 更新 `docs/AI_USAGE.md` 中「图片提示词优化」的模型说明为「与 Camera_Analyst_MODEL 一致（或 IMAGE_PROMPT_LLM_MODEL）」。

3. **验证建议**  
   - 创建新游戏，跑通「世界观 → 选项剧情 → 结局预测」至少一轮，检查格式与连贯性。  
   - 生成 1～2 张场景图 + 1 张主角图，检查提示词是否仍被正确解析、生图质量是否提升。

---

## 六、可选：按场景拆模型（若要进一步榨取效果）

若你希望「主剧情用最强、图像提示词用次强以兼顾延迟」，可做如下拆分（仍属效果优先，仅略降 C 的延迟）：

| 场景 | 推荐模型 | 说明 |
|------|----------|------|
| A + B | Claude Opus 4 | 不变，创意与一致性核心。 |
| C（图像提示词） | Claude Sonnet 4 或 GPT-4o | 略快、仍属顶模，提示词质量与 Opus 差距不大。 |

实现方式：为 C 单独增加环境变量与代码读取（如上），A/B 仍只用 `Camera_Analyst_MODEL`。

---

## 七、参考与数据来源

- EQ-Bench Longform Creative Writing（评委 Claude Sonnet 4）：https://eqbench.com/creative_writing_longform.html  
- LLM Creative Story-Writing Benchmark V4（Opus 4.6 / GPT-5.x 排名）：GitHub lechmazur/writing、多篇 2025模型对比  
- Claude Opus 4.5 vs Sonnet 4.5（能力与 MMLU）：Anthropic 官方、Artificial Analysis Intelligence Index  
- 云雾 API 支持模型：官网与社区称支持 Claude、GPT、Gemini、DeepSeek 等近 400 模型，具体 ID 以控制台为准  
- 结构化输出 / 指令遵循：OpenAI Structured Outputs 博客、JSONSchemaBench、StructuredRAG 等论文  

以上建议均以「效果最佳、不计成本」为前提；若后续需要成本与延迟的平衡，可再按场景做降档（例如 Sonnet 4 用于 C、或部分选项用 DeepSeek V3）。
