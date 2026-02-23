# 从「生成内容优化」到「C26」的改动说明

> **版本区间**：`b3e5d0d`（生成内容优化）→ `dcc71d1`（C26:万恶的配角生成。。。）  
> 共 4 个提交，用通俗的话解释给你听。

---

## 一、整体概览

这 4 次提交主要做了三件事：

1. **LLM 群体智能（C24）**：剧情和世界观不再只用一个模型写，改成多个模型一起写、互相打分、选最好的
2. **融合两个分支 + 预生成（合并提交）**：把「AI 逻辑优化」分支合并进来，加了预生成、缓存、游戏服务拆分等
3. **解析与兜底加固（C25）**：默认剧情补图、超时拆分、剧情解析更稳（格式约束、代码块剥离、兼容无【】、失败调试）
4. **配角生成大改（C26）**：配角图改用视觉模型裁单人，并加预配角机制

---

## 二、C24：LLM 群体智能

### 白话解释

以前：写剧情、写世界观，只用一个 AI 模型，生成啥就是啥。

现在：多个 AI 模型一起写同一段内容 → 互相匿名打分排名 → 再让一个「主席」模型综合出一个最终版本。相当于几个专家各自写答案，再匿名投票，最后主席拍板。

### 代码层面

- 新增 `src/llm/council_core.py`：实现了「多模型并行 → 匿名互评 → 主席综合」三阶段流程
- `local_gen.py`：剧情生成多了一个 `llm_generate_local_council()`，每 2 轮用一次 council
- `global_gen.py`：世界观生成里，完整版也会用 council
- `src/config.py`：新增 `COUNCIL_MODELS`、`CHAIRMAN_MODEL` 等配置，在 `.env` 里配置多个模型

### 实际效果

- 剧情和世界观的稳定性、质量理论上会更好
- 代价：调用次数多、耗时更长（多模型 + 互评 + 主席综合）

---

## 三、合并提交：融合 AI 逻辑优化分支

### 白话解释

把另一条开发线（AI 逻辑优化）合并进来，引入一套完整的**预生成 + 缓存**机制，并且把 `game_server.py` 拆成更小的模块。

### 主要变化

| 改动 | 通俗解释 |
|------|----------|
| **预生成** | 用户还在看当前剧情时，后台先偷偷生成下一层的选项，等你选的时候直接拿出来用，不用再等 |
| **缓存** | 生成过的选项、图片会存起来，避免重复算 |
| **game_server 拆分** | 原来一个大文件，拆成 `server/pregeneration.py`、`server/cache.py`、`server/image_cache.py` 等，逻辑更清晰 |
| **新增文档** | 加了 AI 使用说明、架构说明、Prompt 清单、模型切换清单等 |

### 新增/改动文件（核心）

- `server/pregeneration.py`：预生成逻辑
- `server/cache.py`：预生成缓存
- `server/image_cache.py`：图片缓存
- `src/constants.py`：限流、并行数等常量
- 若干 `docs/` 下的说明文档

---

## 四、C25：融合形态（详细展开）

### 白话解释

C25 的「融合形态」主要做的是**让系统更稳、解析更准**：把前面 council、预生成合并进来后暴露出的各种小问题修一修，包括默认剧情没图、AI 输出格式乱、解析失败难排查等。核心是「兜底 + 容错 + 易排查」。

---

### 1. game_server.py：默认剧情补图

**问题**：当使用「默认剧情」（AI 没按【场景】格式返回）时，初始场景没有图片，用户第一次进游戏是黑屏/无图。

**改动**：检测到「初始场景没有图片（因使用默认剧情，AI 未返回【场景】格式）」时，自动调用 `generate_scene_image()` 为这段默认剧情补生成一张场景图，并写入 `initial_scene_image`。这样即使走默认剧情，首次进入也有图可看。

```python
# 关键逻辑
if not initial_scene_image and initial_scene and initial_scene.strip():
    img = generate_scene_image(initial_scene, global_state, "default", use_cache=True)
    if img and img.get("url"):
        initial_scene_image = dict(img)
        # ... 保存到 initial_cache
```

---

### 2. src/llm/api.py：超时与错误提示

**问题**：原来只有一个总超时 180 秒，连接慢和读取慢混在一起，出错时也不知道是连不上还是读得慢。

**改动**：
- 把超时拆成 **连接超时 30 秒**、**读取超时 180 秒**
- 连接/超时失败时，打印更具体的排查提示：检查本机能否访问 API、是否需要代理、防火墙是否放行 443

```python
# 改动前
timeout = 180

# 改动后
connect_timeout = 30   # 连接阶段超时
read_timeout = 180     # 读取响应超时
timeout = (connect_timeout, read_timeout)
```

---

### 3. src/story/options.py：剧情解析的加固（核心改动）

这是 C25 改动最多的部分，目标是**减少「AI 返回了但解析失败」的情况**。

#### 3.1 强化输出格式要求

在 `_generate_single_option` 和 `_generate_single_option_text_only` 的 prompt 里，新增 **【硬性输出格式】** 说明：

- 明确要求：**第一行就必须是【场景】：**，不要前缀、解释或代码块
- 列出四块结构：【场景】、【选项】、【世界线更新】、【深层背景关联】
- 强调：不要「好的，以下是…」、不要 \`\`\` 包裹

#### 3.2 新增 system 消息

原来只发一条 user 消息；现在多加一条 **system 消息**，专门强调格式：

```
你是剧情生成器。你必须只输出指定格式的剧情内容：以【场景】：开头，接着【选项】：、【世界线更新】：、【深层背景关联】：。不要输出任何解释、问候语、代码块或前缀文字，第一行就是【场景】：。
```

相当于在「角色设定」里反复强调输出规范，提高模型遵守概率。

#### 3.3 自动去除 Markdown 代码块

有些模型会用 \`\`\`text 或 \`\`\`json 把回复包起来，导致正则匹配不到【场景】等标签。

**改动**：解析前先检测是否以 \`\`\` 开头，若是则 strip 掉外层代码块再解析：

```python
if raw_content.startswith("```"):
    for prefix in ("```text", "```txt", "```json", "```\n"):
        if raw_content.startswith(prefix):
            raw_content = raw_content[len(prefix):].lstrip()
            break
    # ... 再去掉结尾的 ```
```

#### 3.4 兼容「无方括号」的格式

原来只认 `【场景】：`、`【选项】：`；有些模型会输出 `场景：`、`选项：`。

**改动**：新增第四种正则匹配：

- `scene_match4`：`场景[：:]\s*([\s\S]*?)(?=【选项】|选项[：:]|$)`
- `options_match4`：`选项[：:]\s*([\s\S]*?)(?=【世界线更新】|【深层背景关联】|…)`

这样即使模型没用【】也能解析出来。

#### 3.5 解析失败时的调试输出

**问题**：解析失败时只打印「未找到【场景】标签」，看不出模型实际返回了什么。

**改动**：解析失败时，打印 AI 返回内容的前 800 字符，便于排查格式问题：

```python
print(f"📋 选项 {i+1} AI 返回内容预览（前800字）：\n{preview}\n---")
```

---

### C25 改动汇总表

| 文件 | 改动类型 | 具体内容 |
|------|----------|----------|
| `game_server.py` | 兜底逻辑 | 默认剧情时补生成初始场景图 |
| `src/llm/api.py` | 超时与错误 | 拆分为连接/读取超时，增强超时错误提示 |
| `src/story/options.py` | 格式约束 | 硬性输出格式 + system 消息 |
| `src/story/options.py` | 容错解析 | 去掉 Markdown 代码块、兼容无【】的格式 |
| `src/story/options.py` | 排查手段 | 解析失败时打印前 800 字 |

---

## 五、C26：配角生成重构（详细展开）

### 白话解释

之前：配角第一次出现时，直接用整张场景图（多人同框）当参考图，导致后续生图经常搞混人、用错脸；且建档发生在生图时，未选中的选项里的角色也可能被误建档。

现在：

1. **视觉裁剪**：用视觉模型在场景图里找到这个配角，框出单人区域，裁成「单人全身参考图」（`body_ref.png`），后续生图优先用这张单人图
2. **预配角**：只在对话里被提及、尚未登场的角色，先积累描述碎片；正式出场时再合并进档案
3. **建档时机后移**：改由「前端展示剧情图后」调用接口触发建档，只对用户真正看到的选项建档
4. **剧情模型为准**：出场配角名单由剧情模型直接输出，不再从图片提示词推断

---

### 1. 建档时机：从「生图时」改为「展示后」

#### 之前

- 在 `generate_scene_image` 生成或命中缓存后，立刻调用 `archive_supporting_role_first_appearance` 建档
- 问题：预生成会提前为多个选项生图，用户可能只选其中一个，但其他未选选项里的配角也被建档了

#### 现在

- 生图流程**不再建档**
- 前端 `displaySceneImage` 在图片**真正加载完成并绘制到画面**后，调用 `POST /notify-scene-displayed`，把当前选项数据传给后端
- 后端 `_archive_supporting_roles_on_option_shown` 只对**用户看到的这一段**里的首次出场配角建档

**涉及**：`game_server.py`（新增 `_archive_supporting_roles_on_option_shown`、`/notify-scene-displayed`）、`api_providers.py`（删除建档逻辑）、`game-frontend/script-modular.js`（展示后发 notify）

---

### 2. 前端：展示后通知 + 加载竞态防护

#### 2.1 展示后通知建档

- `displaySceneImage(imageData, optionDataForArchive)` 新增第二参数，用于建档
- 图片加载完成后，若 `optionDataForArchive` 存在，则 `fetch('/notify-scene-displayed', { game_id, option_data })`
- `displayScene` 和调用处（初始场景、选择选项后）都传入 `optionData`，保证建档用的是当前展示的选项数据

#### 2.2 加载竞态防护

- 现象：用户快速切换选项时，后发的请求可能先返回，先发的后返回，导致画面和建档错乱
- 改动：用 `gameState._pendingDisplay = { imageUrl, optionData }` 绑定「当前展示请求」
- 图片 onload 时检查：若本次加载的 URL ≠ 当前 `_pendingDisplay.imageUrl`，说明已过时，**不绘制、不 notify**，避免错建档

#### 2.3 文本切分规则调整

- 原来：每段 1～2 句话合并展示
- 现在：**按句切分**，每句单独一段，逐句展示
- 句末引号：句号后紧跟右引号（如 `。"`、`。」`）时，在引号后截断；左引号（`。「`）则在句号处截断

---

### 3. 剧情模型输出：新增【本段出场配角】与【本段提及但未出场】

#### 3.1 格式要求

在 `_generate_single_option` 和 `_generate_single_option_text_only` 的 prompt 中，输出格式从四块扩展为六块：

- **【本段出场配角】**：`角色名-配角1、角色名-配角2`，必须与【场景】一致，凡有对话、自我介绍或明确出场的有名有姓角色都要列出
- **【本段提及但未出场】**：只被提及、未实际登场的角色名，用于预配角

#### 3.2 提取与使用

- 用正则解析 `【本段出场配角】：…`、`【本段提及但未出场】：…`
- 兜底：若未填【本段出场配角】但场景里有「我是XXX」式自我介绍，则补一条 `(name, 配角1)`
- `plot_supporting_characters` 写入 `option_data`，并写入 `global_state["_plot_supporting_characters"]` 传给生图流程
- `plot_mentioned_only` 传给 `add_mentioned_roles()` 写入预配角

---

### 4. 预配角（pending_roles）

#### 4.1 概念

- **预配角**：只在对话里被提及、尚未正式出场的角色
- 每段剧情若提到这些名字，就抽取相关句子作为「碎片」存到 `pending_roles.json`
- 当角色**正式出场**时，从预配角中取出并消费这些碎片，合并进正式配角档案的 `story_background`

#### 4.2 实现

- `pending_roles.py` 新增：`add_mentioned_roles(game_id, role_names, scene_text)`、`get_and_consume_pending(game_id, display_name)`
- 存储：`initial/character_references/<game_id>/pending_roles.json`，结构为 `{ 角色名: { aliases, fragments } }`
- 在 `get_or_create_supporting_role_archive` 中，若档案不存在，先调用 `get_and_consume_pending` 取出预配角数据，写入待建档项的 `_pending_fragments`
- 在 `archive_supporting_role_first_appearance` 中，把 `_pending_fragments` 拼进 `story_background` 的「【出场前碎片积累】」段落

---

### 5. 视觉裁剪单人参考图（vision_ref_crop）

#### 5.1 流程

1. 配角首次出场建档时，拿到初登场场景图路径
2. 调用 `get_character_bbox_and_crop(场景图, 角色名, 外观描述, body_ref_filename)`
3. 内部：`_call_vision_bbox` 调用视觉模型，在图中定位该角色，返回归一化 bbox `{x, y, width, height}`
4. `crop_image_by_bbox` 按 bbox 裁剪（含 1.15 倍 padding），保存为 `xxx_body_ref.png`
5. 档案中写入 `face_ref_path`、`_resolved_face_ref_path`

#### 5.2 视觉模型调用

- 支持 OpenAI 兼容接口和 Gemini 原生 `generateContent`
- 上传前将图缩至长边 ≤ 1024px、转 JPEG，减小请求体积
- prompt 要求只输出一行 4 个数字（x y width height），便于云雾等长度受限 API
- 解析：优先 4 数字，其次 JSON，再次正则抠数字
- 503/超时自动重试 3 次

#### 5.3 配置（src/config.py）

```
VISION_REF_MODEL          # 如 gpt-4o-latest、gemini-3-pro-preview
VISION_REF_API_KEY        # 或沿用 OPENAI_API_KEY
VISION_REF_BASE_URL       # 空则用 OpenAI 默认
VISION_REF_TIMEOUT        # 默认 120 秒
VISION_REF_MAX_IMAGE_SIDE # 默认 1024
VISION_REF_MAX_TOKENS     # 默认 512
VISION_REF_USE_GEMINI_ENDPOINT  # true 时走 Gemini 原生接口
```

未配置则跳过裁剪，仍用整张初登场图作参考。

---

### 6. 生图流程：以剧情为准，优先单人参考图

#### 6.1 出场配角来源

- **之前**：从图片提示词中用正则推断「配角1」「配角2」等
- **现在**：优先使用 `global_state["_plot_supporting_characters"]`（剧情模型输出）
- 若未传入该字段，才回退到从提示词推断
- 补图、预生成时都会把 `plot_supporting_characters` 写入 `global_state` 再调用 `generate_scene_image`

#### 6.2 参考图优先级

- 有档案的配角：优先用 `_resolved_face_ref_path`（裁剪后的单人图），没有再用 `_resolved_first_img_path`（初登场原图）
- 目的：多人同框时明确「参考谁」，减少用错脸

#### 6.3 补图与缓存

- 新增 `skip_cache_lookup`：为 True 时不查本地缓存，但仍会保存本次生成结果（用于补图等需要新图的场景）
- 补图时传 `skip_cache_lookup=True`，避免复用旧图导致和当前剧情不符

---

### 7. 配角名解析与档案匹配

#### 7.1 _trim_phrase_to_character_name

- 问题：提示词里常有「主角身后是葛城美里-配角1」，正则容易把整句当名字
- 处理：有「是」则取最后一个「是」之后；若仍含「主角」或超过 12 字则退回 slot

#### 7.2 _find_archive_by_name_or_alias 包含匹配

- 问题：「美里」和「葛城美里」被当成两人
- 处理：无精确匹配时，按「名字包含」视为同一人，要求较短名至少 2 字，选名字更长的档案

---

### 8. 预生成：第二层也生成图片

- 之前：`generate_all_options(..., skip_images=True)`，第二层不生成图
- 现在：改为 `skip_images=False`，第二层也生成场景图，用户选到第二层时可直接展示

---

### 9. 主角与世界观的小改动

#### 9.1 主角颜值（prompt_optimize.py）

- 新增 `APPEARANCE_LEVEL_MAP`：颜值等级 → 中文描述 + 英文关键词
- 高/极高颜值时，在提示词中显式加入「英俊/美丽、五官精致」等，并追加英文如 `handsome, beautiful, attractive`

#### 9.2 主角性别（global_gen.py、prompt_optimize.py）

- 不再随机性别；若缺失则默认「男性」，与剧情保持一致
- 世界观解析：兼容半角冒号 `:`；性别缺失时从主角描述推断并写回

---

### C26 改动汇总表

| 模块 | 文件 | 改动要点 |
|------|------|----------|
| 建档时机 | game_server.py, api_providers.py | 建档移至展示后，由 /notify-scene-displayed 触发 |
| 前端 | script-modular.js | 展示后 notify、_pendingDisplay 防竞态、按句切分 |
| 剧情格式 | options.py | 新增【本段出场配角】【本段提及但未出场】，提取并传递 |
| 预配角 | pending_roles.py, supporting.py | 提及未出场→积累碎片；正式出场→合并进档案 |
| 视觉裁剪 | vision_ref_crop.py, supporting.py | 视觉模型标 bbox→裁单人图→存 face_ref_path |
| 生图 | api_providers.py | 以剧情为准、优先 face_ref、skip_cache_lookup |
| 名解析 | supporting.py, archives.py | _trim_phrase、包含匹配（美里/葛城美里） |
| 预生成 | pregeneration.py | 第二层 skip_images=False |
| 主角 | prompt_optimize.py, global_gen.py | 颜值映射、性别不随机、解析兼容半角冒号 |
| 配置 | config.py | VISION_FOR_REF_CROP 相关环境变量 |

---

### 配置要求

- 视觉裁剪：需在 `.env` 配置 `VISION_REF_MODEL`、`VISION_REF_API_KEY` 等，否则跳过裁剪，仍用整张初登场图

---

## 六、总结：一句话概括每次提交

| 提交 | 一句话 |
|------|--------|
| **C24** | 剧情和世界观改用多模型投票（Council） |
| **合并** | 引入预生成、缓存，拆分 game_server |
| **C25** | 兜底补图 + 超时拆分 + 剧情解析加固（格式约束、代码块剥离、兼容无【】、失败时打印调试） |
| **C26** | 建档改展示后触发、剧情输出出场/提及配角、预配角、视觉裁单人图、名解析加固、预生成第二层生图 |

---

## 七、和你《问题清单》的关系

| 问题 | 相关改动 |
|------|----------|
| 配角图无效图片过多 | C26 的 vision_ref_crop 可能产生新一类失败（视觉模型找不到人、裁切失败等） |
| 配角图不是单人 / 场景图误归类 | C26 正是为了解决「多人同框用错脸」，用视觉裁单人 |
| 生成耗时长 / 异步异常 | C24 的 council 明显增加调用次数；合并后的预生成理论上能缓解等待，但需看实际并行效果 |
| 人物生成初期大量 404 | 前端在 C26 有改动，可能和 404 处理有关，需结合具体代码排查 |

---

*文档生成：2025-02-22*
