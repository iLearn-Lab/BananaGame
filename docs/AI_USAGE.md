# AI 使用说明文档

本文档列出项目中**所有使用 AI 的位置**，按文件与行号标注，并说明每处使用的 **AI 类型、具体使用的 LLM/模型** 与用途。

---

## LLM 模型使用一览（先看这里）

项目中 LLM 的**模型来源**只有两类：

| 来源 | 含义 | 使用位置 |
|------|------|----------|
| **环境变量 `Camera_Analyst_MODEL`** | 在 `.env` 中配置，代码中默认 `claude-opus-4-6`。 | 世界观生成、剧情生成、选项剧情、结局预测/修改、冒险模式中的剧情深化与角色深化、**以及图像提示词优化**（prompt_optimize.py 已改为读该配置）。 |
| **配置 + 默认 `claude-opus-4-6`** | `AI_API_CONFIG.get("model", "claude-opus-4-6")`，未配置时用 claude-opus-4-6。 | `src/characters/supporting.py`：配角身份揭示；`src/image/prompt_optimize.py`：场景图/主角形象提示词。 |

---

## 当前 .env 下的实际模型与图像配置（以你本地为准）

根据项目中的 `.env` 与 `src/config.py` 的读取关系，当前**实际使用的模型与图像配置**如下（仅写配置项与模型名，不涉及密钥等敏感信息）。

### Camera_Analyst_* → 主剧情用 LLM（AI_API_CONFIG）

| 环境变量 | 当前取值 | 说明 |
|----------|----------|------|
| `Camera_Analyst_API_KEY` | 已配置 | 与 `Camera_Analyst_BASE_URL` 一起用于所有「主剧情/世界观/结局」等 LLM 请求。 |
| `Camera_Analyst_BASE_URL` | `https://yunwu.ai/v1` | 请求发往云雾 API。 |
| **`Camera_Analyst_MODEL`** | **`claude-opus-4-6`** | 即主剧情、世界观、选项剧情、结局预测与修改、角色深化等使用的都是 **Claude Opus 4.6**，经云雾(yunwu.ai) 转发。 |

因此：文档中所有写「**Camera_Analyst_MODEL**」的地方，在你当前环境下**实际调用的都是 Claude Opus 4.6**。

### IMAGE_GENERATION_CONFIG（图像生成）

`src/config.py` 中的 `IMAGE_GENERATION_CONFIG` 由以下环境变量填充（未在 .env 中写的项使用代码内默认值）：

| 配置键（代码中） | 环境变量 | 当前取值 | 说明 |
|------------------|----------|----------|------|
| `provider` | `IMAGE_GENERATION_PROVIDER` | **yunwu**（默认） | 未在 .env 中设置，故为云雾。 |
| `yunwu_api_key` | `Image_Generation_API_KEY` | 已配置 | 云雾图像 API 密钥。 |
| `yunwu_base_url` | `Image_Generation_BASE_URL` | `https://yunwu.ai/v1` | 云雾图像接口基础 URL。 |
| **`yunwu_model`** | **`Image_Generation_MODEL`** | **`gemini-3-pro-image-preview`** | 云雾侧用于**文生图/图像生成**的模型为 **Gemini 3 Pro Image Preview**。 |
| `replicate_api_token` | `REPLICATE_API_TOKEN` | 未配置 | 未设置则不走 Replicate。 |
| `openai_api_key` | `OPENAI_API_KEY` | 未配置 | 未设置则不走 OpenAI 生图。 |
| `stable_diffusion_*` | `STABLE_DIFFUSION_*` | 未配置 | 未设置则不走本地/远程 SD。 |
| `comfyui_host` | `COMFYUI_HOST` | `http://vimaxai.com:58188` | 若代码中走 ComfyUI 流程会连该主机。 |
| `img2img_*` | `Img2img_*` | 未在 .env 中设置 | 使用代码默认（如 Img2img_PATH 默认 `/images/edit` 等）；若未配 `Img2img_API_KEY` 则图生图可能不可用。 |

### 图像生成 AI 实际是什么

- **提供商**：**云雾（yunwu）**（`IMAGE_GENERATION_PROVIDER` 默认，且你未覆盖）。
- **主模型**：**Gemini 3 Pro Image Preview**（`Image_Generation_MODEL=gemini-3-pro-image-preview`），用于项目中的**场景图、主角立绘**等文生图/图生图（在走云雾分支时）。
- **图生图（img2img）**：依赖 `Img2img_API_KEY`、`Img2img_BASE_URL` 等；当前 .env 未配置这些，若代码走到图生图且未用同一 key，可能需补全或使用其他已配置渠道（如 ComfyUI）。

总结：**主剧情 LLM = Claude Opus 4.6（Camera_Analyst_MODEL）**；**图像生成 = 云雾 + Gemini 3 Pro Image Preview**；**图片提示词优化**与主剧情共用 **Camera_Analyst_MODEL**（当前为 claude-opus-4-6），未配置时默认 claude-opus-4-6。

---

## 一、配置与通用入口

### 1. `src/llm/api.py`

| 行号 | 内容 | AI 类型 | 使用模型 | 说明 |
|------|------|---------|----------|------|
| 21-55 | `call_ai_api(request_body)` | **LLM** | 由调用方传入的 `request_body["model"]` 决定，通常来自 `AI_API_CONFIG["model"]`（即 **Camera_Analyst_MODEL**） | 通用 AI 文本 API 调用入口。向配置的 `base_url/chat/completions` 发送 POST 请求。带连接/超时重试，401/403 不重试。 |
| 80-146 | `extract_and_validate_json(raw_text)` | 无 | - | 从 AI 返回的原始文本中提取并校验 JSON，不直接调用 AI。 |

---

## 二、LLM 文本生成（世界观与剧情）

### 2. `src/llm/global_gen.py`

| 行号 | 内容 | AI 类型 | 使用模型 | 说明 |
|------|------|---------|----------|------|
| 15-44 | `llm_generate_global(...)` 配置校验 | 无 | - | 检查 `AI_API_CONFIG` 是否齐全，缺失时抛出 ValueError。 |
| **177** | `response_data = call_ai_api(request_body)` | **LLM** | **Camera_Analyst_MODEL** | 调用 LLM 生成**完整世界观**：章节矛盾、主角/配角设定、难度与基调等，返回结构化 `global_state`。 |

### 3. `src/llm/local_gen.py`

| 行号 | 内容 | AI 类型 | 使用模型 | 说明 |
|------|------|---------|----------|------|
| 13-18 | `llm_generate_local(...)` 配置检查 | 无 | - | 未配置 `api_key` 时直接返回空列表。 |
| **146** | `response_data = call_ai_api(request_body)` | **LLM** | **Camera_Analyst_MODEL**（`AI_API_CONFIG.get("model", "")`） | 根据用户选择的选项生成**单层后续剧情**：场景描述、下一层选项、flow 更新等。 |

### 4. `src/worldview/template.py`

| 行号 | 内容 | AI 类型 | 使用模型 | 说明 |
|------|------|---------|----------|------|
| 49-51 | `llm_generate_global(..., force_full=True)` | **LLM** | **Camera_Analyst_MODEL**（同上，经 global_gen 调用） | **后台补全世界观细节**：在缓存命中简化版世界观后，异步调用 `llm_generate_global` 生成完整版并回填。 |

---

## 三、选项剧情与结局（LLM）

### 5. `src/story/options.py`

| 行号 | 内容 | AI 类型 | 使用模型 | 说明 |
|------|------|---------|----------|------|
| **252** | `response_data = call_ai_api(request_body)` | **LLM** | **Camera_Analyst_MODEL** | 在 `_generate_single_option()` 中：为**单个选项**生成剧情文本（场景 + 下一层选项 + flow 更新）。 |
| **473** | `scene_image = generate_scene_image(...)` | **图像生成 AI** | 见「图像相关」小节（场景图内部用 LLM 优化提示词时为 **Camera_Analyst_MODEL**，当前 claude-opus-4-6） | 在 `_generate_single_option()` 中：根据场景描述生成**场景图**（内部会先经 LLM 优化提示词再调生图 API）。 |
| **782** | `response_data = call_ai_api(request_body)` | **LLM** | **Camera_Analyst_MODEL** | 在 `_generate_single_option_text_only()` 中：仅生成**选项剧情文本**（不含图片），用于并行优化流程。 |
| **1050** | `image_data = generate_scene_image(...)` | **图像生成 AI** | 同上 | 在 `_generate_images_parallel()` 的 `generate_single_image()` 中：批量生成多选项的**场景图**。 |

### 6. `src/story/ending.py`

| 行号 | 内容 | AI 类型 | 使用模型 | 说明 |
|------|------|---------|----------|------|
| **63** | `response_data = call_ai_api(request_body)` | **LLM** | **Camera_Analyst_MODEL** | 在 `modify_ending_tone()` 中：根据触发事件**判断是否修改结局主基调**（HE/BE/NE 等）。 |
| **125** | `response_data = call_ai_api(request_body)` | **LLM** | **Camera_Analyst_MODEL** | 在 `modify_ending_content()` 中：根据当前进度**小幅调整结局大致内容**。 |
| **175** | `response_data = call_ai_api(request_body)` | **LLM** | **Camera_Analyst_MODEL** | 在 `generate_ending_prediction()` 中：根据世界观**生成隐藏的结局预测**（主基调 + 结局大致内容）。 |

---

## 四、角色与剧情深化（LLM）

### 7. `src/game/adventure.py`

| 行号 | 内容 | AI 类型 | 使用模型 | 说明 |
|------|------|---------|----------|------|
| **254** | `response_data = call_ai_api(request_body)` | **LLM** | **Camera_Analyst_MODEL** | 在信息差达到 5 条时：生成**隐藏的剧情深化内容**（自然嵌入主线的揭秘/反转剧情）。 |
| **346** | `response_data = call_ai_api(request_body)` | **LLM** | **Camera_Analyst_MODEL** | 章节深化时：**深化角色深层背景**，使角色设定更丰富并更贴合主线。 |
| 440 | `self.global_state = llm_generate_global(...)` | **LLM** | **Camera_Analyst_MODEL** | 开局时调用 `llm_generate_global` 构建**完整游戏世界观**。 |
| 506 | `initial_scenes = llm_generate_local(...)` | **LLM** | **Camera_Analyst_MODEL** | 开局第一层剧情：生成「开始游戏」的**初始场景**。 |
| 768, 876 | `local_scenes = llm_generate_local(...)` | **LLM** | **Camera_Analyst_MODEL** | 根据用户选择生成**后续剧情**。 |
| 550, 748, 844, 898, 1297 | `generate_all_options(...)` | **LLM + 图像 AI** | 剧情与场景图内提示词：**Camera_Analyst_MODEL**（当前 claude-opus-4-6） | 内部会调用选项剧情生成（LLM）与场景图生成（图像 AI），见 `src/story/options.py`。 |

### 8. `src/characters/supporting.py`

| 行号 | 内容 | AI 类型 | 使用模型 | 说明 |
|------|------|---------|----------|------|
| **219** | `resp = call_ai_api({...})` | **LLM** | **Camera_Analyst_MODEL**（`AI_API_CONFIG.get("model", "claude-opus-4-6")`） | 在 `update_supporting_role_aliases_from_plot()` 中：从剧情文本中**提取身份揭示**（如「黑衣人就是艾玛」），更新配角别名。 |

---

## 五、图像相关：提示词优化（LLM）与生图（图像 AI）

### 9. `src/image/prompt_optimize.py`

| 行号 | 内容 | AI 类型 | 使用模型 | 说明 |
|------|------|---------|----------|------|
| **257-262** | `requests.post(f"{base_url}/chat/completions", ...)` | **LLM** | **Camera_Analyst_MODEL**（`AI_API_CONFIG.get("model", "claude-opus-4-6")`） | 在 `optimize_image_prompt_with_llm()` 中：将**剧情文本转为场景图视觉描述提示词**，保证风格/主角/配角一致性。 |
| **396-401** | `requests.post(f"{base_url}/chat/completions", ...)` | **LLM** | **Camera_Analyst_MODEL**（同上） | 在 `optimize_main_character_prompt_with_llm()` 中：生成**主角形象描述提示词**（含姓名、性别、外观等），供主角立绘生图使用。 |

### 10. `src/image/api_providers.py`

| 行号 | 内容 | AI 类型 | 使用模型 | 说明 |
|------|------|---------|----------|------|
| 57-129 | `call_image_api_with_custom_size(...)` | **图像生成 AI** | 由 IMAGE_GENERATION_CONFIG 决定（云雾/Replicate/DALL·E/SD/ComfyUI 等） | 根据配置调用不同**生图 API**，生成指定尺寸图片（支持文生图/图生图）。 |
| 141-220 | Replicate 图生图 `requests.post(create_url, ...)` | **图像生成 AI** | stability-ai/stable-diffusion-img2img（Replicate） | 直接调用 Replicate API 做图生图。 |
| 228-444 | 云雾图生图 `requests.post(create_url, ...)` | **图像生成 AI** | 云雾配置的图生图模型（如 stability-ai/stable-diffusion-img2img） | 通过云雾 API 调用图生图模型。 |
| 448-567 | `call_stable_diffusion_api_with_size(...)` | **图像生成 AI** | 本地/远程 Stable Diffusion | 调用 SD txt2img 或 img2img 接口。 |
| 570-896 | `generate_main_character_image(...)` | **LLM + 图像 AI** | LLM：**Camera_Analyst_MODEL**（提示词）；生图：见上 | 生成**主角形象**：先 `optimize_main_character_prompt_with_llm`（约 825 行）得到提示词，再 `call_image_api_with_custom_size` 生正面图；侧/背视图可用 gemini 图生图或文生图。 |
| **825** | `optimize_main_character_prompt_with_llm(...)` | **LLM** | **Camera_Analyst_MODEL** | 生成主角立绘用提示词。 |
| 950-约 1152+ | `generate_scene_image(...)` | **LLM + 图像 AI** | LLM：**Camera_Analyst_MODEL**（提示词）；生图：见上 | 生成**场景图**：先 `optimize_image_prompt_with_llm`（约 1069 行）优化提示词，再根据 provider 调用云雾/Replicate/OpenAI DALL·E/SD 等生图。 |
| **1069** | `optimize_image_prompt_with_llm(...)` | **LLM** | **Camera_Analyst_MODEL** | 将剧情转为场景图提示词。 |

---

## 六、游戏服务层（调用上述模块）

### 11. `game_server.py`

| 行号 | 内容 | AI 类型 | 使用模型 | 说明 |
|------|------|---------|----------|------|
| 16-26 | 从 `main2` 导入 | - | - | 导入 `llm_generate_global`、`generate_scene_image`、`generate_main_character_image` 等。 |
| **74** | `global_state = llm_generate_global(...)` | **LLM** | **Camera_Analyst_MODEL** | 创建游戏时生成**全局世界观**。 |
| 102-123 | `generate_main_character_after_worldview_async` | **图像 AI** | 见 api_providers（LLM：Camera_Analyst_MODEL） | 世界观生成后异步调用 `generate_main_character_image` 生成**主角形象图**。 |
| **106** | `result = generate_main_character_image(...)` | **LLM + 图像 AI** | 同上 | 同上，具体调用主角生图（内部含 LLM 提示词优化 + 生图 API）。 |
| **859** | `img = generate_scene_image(...)` | **LLM + 图像 AI** | LLM：**Camera_Analyst_MODEL**；生图：配置决定 | 在选项/剧情返回时，按需生成**当前场景图**。 |
| **1275** | `image_data = generate_scene_image(...)` | **LLM + 图像 AI** | 同上 | 在 `generate_scene_image_api()` 接口中：根据请求参数生成**场景图**并返回。 |

### 12. `server/pregeneration.py`

| 行号 | 内容 | AI 类型 | 使用模型 | 说明 |
|------|------|---------|----------|------|
| 15-18 | 从 main2 导入 | - | - | 导入 `generate_scene_image`、`generate_all_options` 等。 |
| **238** | `img = generate_scene_image(...)` | **LLM + 图像 AI** | LLM：**Camera_Analyst_MODEL**；生图：配置决定 | 第一层预生成：为选项生成**场景图**。 |
| 557, 645 | `generate_all_options(..., skip_images=True/False)` | **LLM（+ 图像 AI）** | 剧情与场景图内 LLM：**Camera_Analyst_MODEL** | 第二层预生成时调用选项生成；若未 skip_images，内部会调用 `generate_scene_image`。 |

### 13. `server/image_cache.py`

| 行号 | 内容 | AI 类型 | 使用模型 | 说明 |
|------|------|---------|----------|------|
| 9 | `from main2 import generate_scene_image` | - | - | 导入。 |
| **74** | `image_data = generate_scene_image(...)` | **LLM + 图像 AI** | LLM：**Camera_Analyst_MODEL**；生图：配置决定 | 缓存未命中时调用生图，得到**场景图**。 |

---

## 七、main2.py（兼容层）

### 14. `main2.py`

| 行号 | 内容 | AI 类型 | 使用模型 | 说明 |
|------|------|---------|----------|------|
| 50, 62-63, 81-82 | 从 `src.llm` / `src.image` 导入并 re-export | - | - | 将 `call_ai_api`、`llm_generate_global`、`llm_generate_local`、`generate_scene_image`、`generate_main_character_image` 等导出给 `game_server`、`server` 等使用，**不在此文件内直接调用 AI**。 |

---

## 八、汇总表：按 AI 类型与模型

| AI 类型 | 使用模型 | 主要用途 | 涉及文件（行号见上文） |
|---------|----------|----------|------------------------|
| **LLM（对话/补全 API）** | **Camera_Analyst_MODEL**（.env 配置） | 世界观生成、剧情生成、选项剧情、结局预测与修改、角色深化、冒险模式剧情深化 | `src/llm/`、`worldview/template.py`、`story/options.py`、`story/ending.py`、`game/adventure.py` |
| **LLM** | **Camera_Analyst_MODEL**（默认 **claude-opus-4-6**） | 场景图提示词优化、主角形象提示词优化、配角身份揭示 | `src/image/prompt_optimize.py`、`src/characters/supporting.py` |
| **图像生成 AI** | 由 IMAGE_GENERATION_CONFIG / .env 决定（云雾、Replicate、DALL·E、SD、ComfyUI 等） | 主角立绘、场景图、图生图（侧/背视图等） | `src/image/api_providers.py`、`story/options.py`、`game_server.py`、`server/pregeneration.py`、`server/image_cache.py` |

---

## 九、环境变量与配置

- **LLM（主剧情/世界观/结局等 + 图片提示词优化）**：`Camera_Analyst_API_KEY`、`Camera_Analyst_BASE_URL`、**`Camera_Analyst_MODEL`**（见 `src/config.py` 中 `AI_API_CONFIG`）。**当前 .env 下为 `claude-opus-4-6`（Claude Opus 4.6）**；未配置时代码默认 `claude-opus-4-6`。图片提示词优化已改为读取该配置，与主剧情共用同一模型。
- **图像生成**：由 `IMAGE_GENERATION_CONFIG` 决定（见 `src/config.py`）。**当前 .env 下**：provider 为 **yunwu**，模型为 **`Image_Generation_MODEL`=gemini-3-pro-image-preview**（Gemini 3 Pro Image Preview）。其他如 `IMAGE_GENERATION_PROVIDER`、`REPLICATE_API_TOKEN`、`Img2img_*` 等见上文表格。

以上即项目中所有「用到 AI」的位置说明，便于排查、优化或做合规/成本统计。
