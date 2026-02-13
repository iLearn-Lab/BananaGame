# 项目 Prompt 清单

本文档列出项目中所有与 LLM/生图 相关的 prompt，按文件与行号定位，并说明各自作用。

---

## 一、`src/image/prompt_optimize.py`

| 行号 | 变量/类型 | 作用说明 |
|------|-----------|----------|
| **56-66** | `continuity_requirements`（f 字符串） | **连续性/一致性要求**：当存在上一剧情/上一张图时拼接进主 prompt。要求同一场景画风统一、下一张图延续上一张的镜头/色彩/造型，且提示词中不得包含 URL/路径。内含「上一剧情文本」「上一张图的提示词」占位。 |
| **119-139** | `protagonist_reference_section`（多行 `lines`） | **主角参考图说明**：说明生图 API 收到的多张主角参考图编号（Image 0 正面/Image 1 侧面/Image 2 背面），以及「根据剧情视角明确写出主角使用哪张参考图」的规则，确保主角形象与参考图一致。 |
| **143-167** | `supporting_role_reference_section`（多行） | **配角参考图说明**：列出配角参考图编号、首次出场场景、核心特征；要求生成时明确每个配角对应哪张图、图中哪个人物，并写明「XXX 参考 Image N，以图中XX位置/特征的人物为准」。 |
| **169-184** | `supporting_role_reference_section`（配角标注分支） | **配角标注要求**：无参考图时使用。要求对「已有角色」使用相同名称或别号+配角N；对「新登场」非主角用「角色名-配角N」格式标注，且不写「参考 Image N」。 |
| **187-231** | `llm_prompt`（主 prompt） | **场景图提示词优化（LLM 用户消息）**：角色为「专业剧情分析师和视觉设计师」，将剧情转为视觉描述给生图 AI。包含：游戏背景、主角信息、主角规范信息、主角身份警告、当前剧情、图片风格、主角/配角参考说明、连续性要求；输出要求 1–9 条（准确反映剧情、主角外貌、世界观、基调、风格、无文字、具体生动、主角/配角参考图说明等）。只输出视觉描述。 |
| **248** | `messages` 中的 `llm_prompt` | 将上述 `llm_prompt` 作为 `user` 消息发给 Chat Completions API（场景图提示词优化）。 |
| **265-272** | `optimized_prompt` 后处理 | 对 LLM 返回的提示词做后处理：去 URL/data URI/本地路径，末尾追加 `no text, no symbols...`；若有连续性要求则再追加 `consistent character design, consistent outfit...`。 |
| **278** | 兜底返回值 | LLM 未配置或失败时，用「游戏主题 + 剧情前 500 字 + cinematic, detailed...」作为原始提示词。 |
| **284-285** | 异常兜底 | 异常时用 `core_worldview.game_style + scene_description[:500] + ...` 作为提示词。 |
| **290-304** | `_get_style_description()` 返回值 | 根据 `image_style.type` 返回风格描述：写实/动漫/水墨/油画/赛博朋克/自定义等，默认「写实风格，8K，细节丰富」。用于风格相关 prompt 片段。 |
| **490-528** | `llm_prompt`（主角形象） | **主角形象提示词生成（LLM 用户消息）**：角色为「专业角色设计师」，将角色描述给生图 AI。包含：游戏背景、主角规范信息、Wikipedia 检索补充、必须保留的名称、身份提示、主角性别/属性/能力/性格/背景、图片风格；要求优先用主角规范信息、性别一致、外貌与标志性关键词、保留名称、全身纯白背景无文字等。只输出视觉描述。 |
| **534** | 兜底返回值（无 API） | LLM 未配置时返回默认主角提示词（全身、主角形象、纯白背景、游戏主题风格、属性、风格词、no text...）。 |
| **543** | `messages` 中的 `llm_prompt` | 将主角形象 `llm_prompt` 作为 `user` 消息发给 API。 |
| **560-571** | `optimized_prompt` 后处理 | 补全缺失的 `required_name_tokens`、`identity_hint`，末尾追加 `full body, standing pose, arms relaxed at sides, pure white background...`。 |
| **576** | 默认提示词（LLM 失败） | 与 534 相同的默认主角提示词。 |
| **579** | 异常兜底 | 异常时用 `core_worldview.game_style` + 属性 + 通用后缀。 |

---

## 二、`src/image/api_providers.py`

| 行号 | 变量/类型 | 作用说明 |
|------|-----------|----------|
| **40-45** | `prompt_template_front` | **主角正面立绘模板**：英文，要求 full-body front-view、纯白背景、居中、直视、自然站姿；占位符 `{identifier}`、`{features}`、`{style}`；结尾 no text/no symbols。 |
| **47-50** | `prompt_template_side` | **主角侧面立绘模板**：基于正面图生成侧面、朝左、站姿，纯白背景，no text。 |
| **52-55** | `prompt_template_back` | **主角背面立绘模板**：基于正面图生成背面、不露面部，纯白背景，no text。 |
| **106** | `size_prompt` | 文生图时在用户 `prompt` 后追加 `aspect ratio {width}:{height}, portrait orientation`。 |
| **1167** | `size_prompt` | 场景图生成时同样追加宽高比与竖版。 |
| **1181-1196** | `prefix_lines` / `prefix_prompt` | **Gemini 多图图生图前缀**：按顺序说明 Image 0/1/2 为主角正/侧/背，Image N 为配角首登场场景（含核心特征不可改），最后一图为「上一张剧情图」用于视觉连续性。拼成 `prefix_prompt` 再与 `prompt` 拼接。 |
| **1550-1554** | `prompt_text`（Gemini 图生图） | 单图：`Edit this image: {prompt}\n\nReturn only the edited image as base64...`；多图：`Based on these N reference images, generate a new image: {prompt}\n\nReturn only the generated image...`。要求只返回图片 base64 或 URL，无文字/代码块。 |
| **1563-1566** | `content`（Gemini 多模态） | 将多图 + 上述 `prompt_text` 作为 `user` 的 `content` 数组传给 Gemini 图生图 API。 |
| **1696-1699** | user message（Gemini 图片模型） | 当模型名含 `gemini` 且含 `image` 时：单条 user 消息，英文「Generate an image based on this description: {prompt}\n\nReturn only the image as base64...」。 |
| **1707** | `system_content`（Gemini 非 image） | 中文：「你是一个图片生成模型。直接生成图片并返回 base64 数据或 URL，不要任何文字说明或代码块。」 |
| **1708** | `user_content`（同上） | 「生成图片：{prompt}\n\n返回格式：data:image/png;base64,... 或 https://图片URL」。 |
| **1726** | `system_content`（其他模型） | 「你是一个图片生成 API。用户会提供图片描述，你必须生成图片并返回图片 URL 或 base64 数据。优先返回 base64...」 |
| **1727** | `user_content`（同上） | 「请生成一张图片，描述：{prompt}\n\n请返回图片 URL 或 base64 格式的图片数据。」 |

---

## 三、`src/characters/supporting.py`

| 行号 | 变量/类型 | 作用说明 |
|------|-----------|----------|
| **209-218** | `llm_prompt` | **配角身份揭示提取**：从剧情中提取「角色 A 与身份 B 为同一人」的揭示（如「黑衣人就是艾玛」「A原来是B」）。输入：已知配角称呼列表、剧情文本；输出格式「原名\|新身份」每行一条，无则输出「无」。用于更新配角档案别名。 |
| **221** | `messages` | 将上述 `llm_prompt` 作为 `user` 消息调用 `call_ai_api`（模型来自 `AI_API_CONFIG`）。 |

---

## 四、`src/llm/global_gen.py`

| 行号 | 变量/类型 | 作用说明 |
|------|-----------|----------|
| **51-93** | `prompt`（分阶段核心版） | **世界观核心速写**：角色「资深游戏编剧」，生成【核心世界观速写】。含：核心世界观（游戏风格、世界观基础设定、主角核心能力）、主线任务、三章节设定（核心矛盾+结束条件）、主角规范信息（姓名中英、性别、年龄感、作品、标志性外观）、初始世界线。输入：主题、主角属性、难度、基调。贴合基调要求，中文无代码块。 |
| **95-152** | `prompt`（完整版） | **完整世界观**：同上角色，生成完整文本冒险游戏世界观。在核心版基础上增加：角色设定（主角+配角1 性格/浅层/深层背景）、势力设定、主线任务更长、章节设定更详、游戏结束触发条件、初始世界线更细。输入多「任务：为首轮 2 个选项提供充足背景信息」。 |
| **161** | `messages` | 将上述 `prompt` 作为 `user` 消息调用 `call_ai_api` 生成世界观。 |

---

## 五、`src/llm/local_gen.py`

| 行号 | 变量/类型 | 作用说明 |
|------|-----------|----------|
| **46-129** | `prompt` | **单层递进剧情（本地/冒险模式）**：基于设定生成后续 1 层剧情。含：故事基调要求、最高优先级（100% 执行用户选择/数字序号对应上一轮选项）、格式要求（中文、无代码块、标点与数字规范、对话质量）、【场景】/【选项】/【世界线更新】/【深层背景关联】结构、生成约束、主角规范信息、输入数据（核心世界观、当前状态、用户交互、上一轮选项、故事基调）。强调用户选择对应选项与深层背景关联。 |
| **133** | `messages` | 将上述 `prompt` 作为 `user` 消息调用 `call_ai_api`。 |

---

## 六、`src/story/options.py`

| 行号 | 变量/类型 | 作用说明 |
|------|-----------|----------|
| **96** | `deep_bg_prompt` | **已解锁深层背景片段**：当存在已解锁深层背景时，拼入主 prompt，要求后续剧情围绕这些深层背景展开并自然融入，不直接向玩家显示深层背景内容。 |
| **116-127** | `scene_requirement`（首场景） | 第一次「开始游戏」：要求【场景】至少 400 字，含开场、环境、角色反应、对话、悬念、世界观、主线暗示等 7 点。 |
| **127** | `scene_requirement`（普通） | 非首场景：【场景】至少 150 字，为用户选择的直接结果，含环境、角色反应、对话（引号）。 |
| **128-218** | `prompt`（`_generate_single_option`） | **单选项剧情生成（含图流程）**：与 local_gen 结构类似。强调用户选择、主线推进、格式与标点/对话质量、主角规范；含 `deep_bg_prompt`、`scene_requirement`、输入数据。用于生成单个选项的剧情+下一层选项。 |
| **233** | `messages` | 将上述 `prompt` 作为 `user` 消息调用 `call_ai_api`。 |
| **637** | `deep_bg_prompt` | 与 96 相同逻辑，用于「仅文本」生成分支。 |
| **648-657** | `scene_requirement` | 与 116-127 相同，区分首场景/普通场景。 |
| **661-751** | `prompt`（`_generate_single_option_text_only`） | **单选项剧情生成（仅文本）**：与 128-218 内容一致，用于并行先生成文本再批量生成图片的优化路径。 |
| **757** | `messages` | 将上述 `prompt` 作为 `user` 消息调用 `call_ai_api`。 |

---

## 七、`src/story/ending.py`

| 行号 | 变量/类型 | 作用说明 |
|------|-----------|----------|
| **33-47** | `prompt`（`modify_ending_tone`） | **结局主基调是否修改**：根据当前主基调、触发事件、世界观、游戏状态，判断是否在深层背景节点触发时修改基调。输出仅返回新基调类型（HE/BE/NE）或保持当前，无多余说明。 |
| **54** | `messages` | 将上述 `prompt` 作为 `user` 消息调用 `call_ai_api`。 |
| **95-110** | `prompt`（`modify_ending_content`） | **结局大致内容微调**：基于当前基调、当前结局内容、世界观、游戏进度，对结局内容做小幅调整；可补充细节、微调走向，不颠覆核心框架。输出仅返回修改后的结局内容，中文。 |
| **115** | `messages` | 将上述 `prompt` 作为 `user` 消息调用 `call_ai_api`。 |
| **144-162** | `prompt`（`generate_ending_prediction`） | **初始结局预测**：根据世界观生成完整结局预测。含结局主基调（HE/BE/NE 等）与结局大致内容（核心情节框架）。输出格式「结局主基调：…」「结局大致内容：…」，中文无多余说明。 |
| **167** | `messages` | 将上述 `prompt` 作为 `user` 消息调用 `call_ai_api`。 |

---

## 八、`src/game/adventure.py`

| 行号 | 变量/类型 | 作用说明 |
|------|-----------|----------|
| **215-239** | `prompt`（信息差剧情深化） | **隐藏剧情深化**：当未发现的信息差≥5 条时，根据信息差摘要、游戏世界观、当前状态，生成自然嵌入常规剧情的深化内容。要求：自然嵌入、贴合主线、深层背景与悬念揭晓、符合世界观；输出含「### 选项：」「### 剧情：」分隔。 |
| **244** | `messages` | 将上述 `prompt` 作为 `user` 消息调用 `call_ai_api`。 |
| **311-332** | `prompt`（角色深层背景深化） | **章节深化-角色深层背景**：按角色与当前章节深化「深层背景」。输入：角色名、当前深层背景、核心性格、浅层背景、当前章节、主线进度、深化深度。要求：补充细节、与主线更紧密、保持核心设定、符合世界观；输出仅深化后的深层背景正文，无前缀后缀。 |
| **336** | `messages` | 将上述 `prompt` 作为 `user` 消息调用 `call_ai_api`。 |

---

## 九、配置与数据中的 Prompt（非代码拼接）

| 文件 | 说明 |
|------|------|
| `initial/character_references/.../role_archives.json` | 字段 `first_prompt`：该配角首次出场时用于建档的视觉描述片段（来自当次剧情图优化结果）。 |
| `initial/main_character/.../metadata.json` | 字段 `prompt`：主角立绘生成时使用的完整提示词（可由 LLM 生成后写入）。侧/背视图也有独立 `prompt` 模板。 |

---

## 十、汇总表（按用途）

| 用途 | 文件 | 行号 | 调用模型 |
|------|------|------|----------|
| 场景图提示词优化 | `prompt_optimize.py` | 194-231, 248 | AI_API_CONFIG（默认 claude-opus-4-6） |
| 主角形象提示词生成 | `prompt_optimize.py` | 490-528, 543 | 同上 |
| 配角身份揭示提取 | `supporting.py` | 209-218, 221 | 同上 |
| 世界观生成（核心/完整） | `global_gen.py` | 51-93 或 95-152, 161 | 同上 |
| 单层剧情生成（本地） | `local_gen.py` | 46-129, 133 | 同上 |
| 单选项剧情生成（含图/仅文） | `options.py` | 128-218/661-751, 233/757 | 同上 |
| 结局主基调修改 | `ending.py` | 33-47, 54 | 同上 |
| 结局内容微调 | `ending.py` | 95-110, 115 | 同上 |
| 结局预测生成 | `ending.py` | 144-162, 167 | 同上 |
| 信息差剧情深化 | `adventure.py` | 215-239, 244 | 同上 |
| 角色深层背景深化 | `adventure.py` | 311-332, 336 | 同上 |
| 主角三视图生图模板 | `api_providers.py` | 40-55 | 生图 API（与参考图一起） |
| 云雾/文生图尺寸与说明 | `api_providers.py` | 106, 1167, 1181-1197 | 云雾/Replicate/SD 等 |
| Gemini 图生图/文生图 | `api_providers.py` | 1550-1558, 1696-1737 | 云雾 Gemini 模型 |

---

*文档生成后可根据后续新增 prompt 继续在本清单中补充文件、行号与作用说明。*
