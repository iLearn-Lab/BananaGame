# 程序架构说明

本文档描述本项目的整体架构、各代码文件的职责以及它们如何协同工作。

---

## 一、项目概览

本项目是一个 **AI 驱动的沉浸式文本冒险游戏**，具备以下能力：

- **双运行模式**：支持 **命令行（CLI）** 与 **Web 前端 + 后端服务** 两种方式运行。
- **AI 生成**：基于大语言模型（LLM）生成世界观、章节矛盾、剧情与选项；支持多供应商的图片生成（主角形象、场景图）。
- **结构化世界观**：核心世界观（core_worldview）、流动世界线（flow_worldline）、章节矛盾与结局基调。
- **角色系统**：主角属性与形象、配角档案与参考图、深层背景与信息差剧情。
- **剧情与选项**：每步生成 2 个选项，支持预生成两层选项以提升 Web 端响应速度；结局预测与基调修改。

整体上，**后端逻辑** 集中在 `src/` 与 `main2.py` / `game_server.py`，**CLI 入口** 为 `main.py`，**Web 入口** 为 `game_server.py` + `game-frontend/`。

---

## 二、入口与运行方式

### main.py（CLI 精简入口）

- **用途**：在项目根目录执行 `python main.py` 时，启动**命令行版**文本冒险游戏。
- **具体行为**：
  - `_ROOT = os.path.dirname(os.path.abspath(__file__))`：取当前文件所在目录的绝对路径，即**项目根目录**（即包含 `src/`、`main.py` 的那一层）。
  - `if _ROOT not in sys.path: sys.path.insert(0, _ROOT)`：把项目根插入到 **sys.path** 的最前面。  
    **sys.path** 是 Python 解析 `import` 时查找模块的搜索路径列表；只有把项目根加进去，后面的 `from src.game.adventure import ...` 才能正确找到 `src` 包（否则会报 `ModuleNotFoundError: No module named 'src'`）。  
    用 `insert(0, _ROOT)` 是为了优先从本项目根目录解析，避免同名 `src` 包来自其它环境。
  - 然后 `from src.game.adventure import TextAdventureGame`，在 `if __name__ == "__main__"` 里实例化并调用 `game.start()`，进入主菜单与交互循环。

### main2.py（聚合入口，供 CLI 与 Web 复用）

- **用途**：一是可直接运行 `python main2.py` 玩 CLI 版（与 main.py 效果等价）；二是被 **game_server.py** 通过 `from main2 import ...` 引用，把世界观生成、选项生成、结局、场景图、主角图等逻辑复用到 Web 后端。
- **具体行为**：
  - **编码**：`if sys.platform == 'win32': os.environ['PYTHONIOENCODING'] = 'utf-8'`。在 Windows 下把标准输入输出的编码设为 UTF-8，避免在终端里出现 GBK 乱码或 `input()` 报错。
  - **依赖**：文件中大量使用 `from src.xxx import ...`。因此运行或导入 main2 时，**当前工作目录**必须是项目根，或者项目根已在 **sys.path** 里（例如先执行过 main.py、或在项目根执行 `python main2.py`），这样 `src` 才能被找到。
  - **入口**：文件末尾 `if __name__ == "__main__": game = TextAdventureGame(); game.start()`，与 main.py 一样启动同一套游戏主循环。

### game_server.py（Web 后端）

- **用途**：用 `python game_server.py` 启动 **Flask** 服务后，浏览器访问前端页面，通过 REST API 完成世界观生成、选项/预生成、存档、结局、场景图与主角图等，实现 **Web 版**游戏。
- **具体行为**：
  - 同样在 Windows 下设置 `os.environ['PYTHONIOENCODING'] = 'utf-8'`，保证子进程或控制台 I/O 的编码一致。
  - **from main2 import** 一系列函数（如 `llm_generate_global`、`_generate_single_option`、`generate_all_options`、`modify_ending_content`、`generate_scene_image`、`generate_game_id`、`generate_main_character_image` 等）。  
    因此 **game_server 不直接 import src**，而是通过 main2 这一层拿到所有生成能力；启动时必须在**项目根目录**执行 `python game_server.py`，这样 Python 解释器会把当前目录（项目根）加入 sys.path，导入 main2 时 main2 里的 `from src...` 才能成功。
  - 提供路由：`/generate-worldview`、`/generate-option`、`/pregenerate-next-layers`、`/get-pregenerated-layer2`、`/save-game`、`/load-game`、`/list-saves`、`/delete-save`、`/generate-ending`、`/generate-scene-image`、主角图与静态资源等；内部用预生成缓存和线程锁保证并发安全。

### 启动脚本

- **启动游戏.bat**（Windows）、**启动游戏.sh**（Linux/macOS）：在项目根目录一键执行启动命令（通常是启动 Web 服务或 CLI），具体以脚本内容为准。

| 文件 | 作用 | 使用场景 |
|------|------|----------|
| **main.py** | 设置 `sys.path` 保证能 `import src`，然后启动 `TextAdventureGame`。 | `python main.py` → **CLI 游戏**。 |
| **main2.py** | 设置 Windows 下 UTF-8 编码；聚合所有 `src` 能力；可直接启动游戏，或被 game_server 导入。 | 直接运行 → CLI；被 **game_server.py** 引用 → Web 后端逻辑。 |
| **game_server.py** | Flask 服务，从 main2 导入生成与存档等函数，提供 REST API 与静态资源。 | `python game_server.py` → **Web 版**（浏览器访问）。 |
| **启动游戏.bat** / **.sh** | 一键启动（服务或 CLI）。 | 本地快速启动。 |

---

## 三、后端核心：`src/` 包结构

`src/` 是按功能拆分的核心逻辑，被 `main2.py` 与 `game_server.py` 共同使用。

### 3.1 配置与常量（无业务依赖）

| 文件 | 职责 |
|------|------|
| **src/config.py** | 从 `.env` 读取并暴露：`AI_API_CONFIG`（对话模型 API）、`IMAGE_GENERATION_CONFIG`（各图生供应商、云雾/Replicate/OpenAI/SD/ComfyUI/img2img 等）、`WIKI_*`（百科开关、语言、超时、摘要长度）。 |
| **src/constants.py** | 游戏常量：`DIFFICULTY_SETTINGS`（简单/中等/困难）、`TONE_CONFIGS`（各类故事基调）、`PROTAGONIST_ATTR_OPTIONS`（主角属性选项）、`PERFORMANCE_OPTIMIZATION`、`WORLDVIEW_TEMPLATE_DIR`/`WORLDVIEW_CACHE_DIR`；以及图片限速用 `_YUNWU_RATE_LOCK`、`_YUNWU_LAST_CALL_TS`。 |

### 3.2 工具（utils）

| 文件 | 职责 |
|------|------|
| **src/utils/io_utils.py** | `safe_input()`：带默认值、重试与 Ctrl+C/EOF 兜底的输入；Windows 下设置 `PYTHONIOENCODING=utf-8`。 |
| **src/utils/text_utils.py** | `_safe_str`、`_clip_text`、`_extract_core_features_from_prompt` 等纯文本处理，供 Wiki、LLM、图片等模块使用。 |

### 3.3 百科检索（wiki）

| 文件 | 职责 |
|------|------|
| **src/wiki/lookup.py** | 现实题材/IP 资料检索：调用 Wikipedia 中英文 API（搜索、摘要、跨语言标题、图片 URL 等），`wiki_lookup_theme_and_character()` 为世界观/角色提供参考资料；`_format_protagonist_canonical_for_prompt()` 将主角规范信息格式化为 prompt 片段。 |

### 3.4 LLM 调用与生成（llm）

| 文件 | 职责 |
|------|------|
| **src/llm/api.py** | 通用 LLM 调用：`call_ai_api(request_body)`（带重试、超时）；`extract_and_validate_json()` 从模型输出中抽取并校验 JSON。 |
| **src/llm/global_gen.py** | 全局世界观生成：`llm_generate_global(user_idea, protagonist_attr, difficulty, tone_key)`，产出 core_worldview（风格、主线、章节矛盾、主角规范等）；支持分阶段/模板/缓存；`_get_default_worldview()` 为无 API 时的默认世界观。 |
| **src/llm/local_gen.py** | 单步剧情生成：`llm_generate_local(global_state, user_interaction, last_options)`，根据用户选择的选项序号或文本指令生成下一段场景与新的 2 个选项；`_get_default_scene()` 为兜底场景。 |

### 3.5 世界观（worldview）

| 文件 | 职责 |
|------|------|
| **src/worldview/cache.py** | 世界观缓存：`_make_worldview_cache_key()`、`_load_worldview_cache()`、`_save_worldview_cache()`，按主题/属性/难度/基调生成 key 并读写 JSON。 |
| **src/worldview/template.py** | 模板与合并：`_load_template_worldview()`、`_merge_template_with_input()`、`_background_fill_worldview_details()`，与缓存、LLM 配合做分阶段世界观补全。 |
| **src/worldview/parser.py** | 正则解析：`_regex_fill_worldview()`，从 LLM 文本输出中解析出结构化世界观字段。 |

### 3.6 角色（characters）

| 文件 | 职责 |
|------|------|
| **src/characters/paths.py** | 路径与 ID：`generate_game_id()`；`ensure_main_character_dir(game_id)`、`ensure_character_references_dir(game_id)`；常量 `SUPPORTING_ROLE_ARCHIVES_FILE`。 |
| **src/characters/archives.py** | 配角档案 JSON 的读写：`_load_role_archives`、`_save_role_archives`、`_next_role_id`、`_find_archive_by_name_or_alias`、`_sanitize_filename_for_role`、`_next_img_id`。 |
| **src/characters/supporting.py** | 配角识别与建档：从剧情中 `extract_supporting_characters_in_scene` / `extract_supporting_characters_with_names`；`get_or_create_supporting_role_archive`、`archive_supporting_role_first_appearance`、`update_supporting_role_aliases_from_plot`。 |

### 3.7 图片（image）

| 文件 | 职责 |
|------|------|
| **src/image/prompt_optimize.py** | 用 LLM 优化出图 prompt：`optimize_image_prompt_with_llm`（场景）、`optimize_main_character_prompt_with_llm`（主角）；风格与核心特征提取。 |
| **src/image/size.py** | `calculate_image_size_for_viewport()`，根据视口或配置计算生成图片宽高。 |
| **src/image/api_common.py** | 通用出图封装：按尺寸调用接口、Dalle、参考图转输入等；Replicate img2img 版本等常量。 |
| **src/image/api_providers.py** | 多供应商实现与对外接口：云雾/Replicate/SD/ComfyUI/Dalle 等；`generate_scene_image()`、`generate_main_character_image()`；内部会用到 prompt_optimize、size、storage、validation、characters。 |
| **src/image/validation.py** | `validate_image_url()`、`fix_incomplete_url()`，校验与修复图片 URL。 |
| **src/image/storage.py** | `save_base64_image()`，将 base64 图片保存到本地。 |

### 3.8 剧情与结局（story）

| 文件 | 职责 |
|------|------|
| **src/story/ending.py** | 结局与视频占位：`get_video_task_status()`（当前为禁用占位）；`modify_ending_tone()` 根据触发事件调整结局主基调；`modify_ending_content()` 修改结局文本；`generate_ending_prediction()` 生成隐藏结局预测供后续基调使用。 |
| **src/story/options.py** | 选项与批量剧情/图：`prune_options()` 过滤不合理或过相似选项（保留最多 2 个）；`_generate_single_option()` / `_generate_single_option_text_only()` 生成单选项的剧情与下一层选项；`generate_all_options()` 为当前场景生成全部选项（含并行图生成），被 CLI 与 game_server 调用。 |

### 3.9 游戏主循环（game）

| 文件 | 职责 |
|------|------|
| **src/game/adventure.py** | **TextAdventureGame** 类：主菜单（新游戏/加载/存档管理/退出）、主角属性与难度与基调选择、主题输入、调用 `llm_generate_global` 生成世界观、展示核心设定、`_interaction_loop()` 内的剧情循环（生成选项、处理选择、章节矛盾检测、存档/读档）、信息差与角色深层背景深化、结局触发与结局生成等。CLI 下所有交互通过 `safe_input()` 与打印完成。 |

---

## 四、Web 服务：game_server.py

- **框架**：Flask，从 `main2` 导入 `llm_generate_global`、选项生成、结局、场景图、主角图、存档相关等。
- **主要路由**：
  - **POST /generate-worldview**：根据主题、属性、难度、基调生成全局世界观。
  - **POST /generate-option**：生成单个选项剧情（或仅文本），供前端预生成/即时请求。
  - **POST /pregenerate-next-layers**：预生成下一层（两层）选项与剧情，写入内存缓存。
  - **POST /get-pregenerated-layer2**：用户选择第一层选项后，返回已预生成的第二层数据。
  - **POST /save-game**、**GET /list-saves**、**POST /load-game**、**POST /delete-save**：存档的增删改查。
  - **POST /generate-ending**：根据当前状态生成结局内容。
  - **POST /generate-scene-image**：单独生成场景图。
  - **POST /generate-scene-video**、**GET /video-status/<task_id>**：视频相关（当前为禁用占位）。
  - **GET /initial/main_character/<game_id>/<filename>**、**GET /image_cache/<filename>**：提供主角图与缓存图片。
  - **GET /**、**GET /<path:filename>**：前端首页与静态资源（HTML/JS/CSS）。
- **缓存与并发**：`pregeneration_cache` 存预生成的两层数据；`TrackedLock` 用于缓存写入的线程安全与调试。

---

## 五、前端：game-frontend/

| 文件 | 职责 |
|------|------|
| **index.html** | 单页结构：主菜单、主角属性选择、难度选择、基调选择、主题输入、图片风格选择、核心设定告知、加载中、核心玩法（剧情文本 + 选项列表 + 下一段按钮）、角色状态面板、存档管理、结局界面、通用弹窗；引用 Tailwind、Font Awesome、Google Fonts、style.css、script-modular.js。 |
| **style.css** | 全局与各屏幕的样式（背景、按钮、卡片、进度条、字体、动画等）。 |
| **script-modular.js** | 前端唯一业务脚本：**Game** 单例（IIFE），包含状态初始化、DOM 元素引用、事件绑定、与 game_server 的 API 通信（fetch）；流程包括：主菜单 → 属性 → 难度 → 基调 → 主题 → 世界观生成 → 设定展示 → 图片风格 → 进入玩法；玩法中请求/预生成选项、展示剧情与选项、切换段落、更新进度与角色面板、存档、结局展示；内含音效管理、字体管理、无障碍与兼容逻辑。 |

前端通过 **fetch** 调用 `game_server.py` 提供的上述接口，不直接访问 `src/` 或 `main2.py`。

---

## 六、脚本与资源（scripts/、initial/）

| 路径 | 职责 |
|------|------|
| **scripts/extract_game_to_src.py** | 从 main2.py 中抽取 TextAdventureGame 相关代码块，写入 `src/game/adventure.py`（用于历史拆分）。 |
| **scripts/extract_image_to_src.py** | 将图片相关逻辑从 main2 抽到 src/image（拆分用）。 |
| **scripts/extract_story_to_src.py** | 将剧情相关逻辑抽到 src/story（拆分用）。 |
| **scripts/patch_main2_imports.py** | 修补 main2.py 的 import，使其指向 src 模块（拆分用）。 |
| **initial/main_character/<game_id>/** | 每个游戏 ID 下存放主角三视图（main_character.png、main_character_side.png、main_character_back.png）及 metadata.json。 |
| **initial/character_references/<game_id>/** | 配角参考图与 `role_archives.json`。 |

---

## 七、数据与缓存目录（运行时）

- **saves/**：存档 JSON（CLI 与 server 共用，由 `game_server` 或 `TextAdventureGame` 写入）。
- **image_cache/**：场景图等缓存文件，由 `game_server` 或图片模块写入并提供 URL。
- **video_cache/**：视频缓存（当前功能禁用）。
- **世界观缓存**：由 `src.constants` 中的 `WORLDVIEW_CACHE_DIR` 指定，`worldview/cache.py` 读写。

---

## 八、整体数据流简述

1. **新游戏（CLI 或 Web）**  
   用户输入主题、主角属性、难度、基调（Web 还有图片风格） → 调用 `llm_generate_global`（可能走世界观缓存/模板） → 得到 `global_state`（core_worldview、flow_worldline、tone 等） → 生成结局预测 → 展示核心设定。

2. **剧情推进**  
   当前 `global_state` + 用户选择的选项序号（或文本） → `llm_generate_local` 或从预生成缓存取 → 得到新场景描述 + 2 个新选项；可选地生成场景图、更新配角档案与深层背景。

3. **章节与结局**  
   每步或章节结束时检查 `flow_worldline.chapter_conflict_solved`；若章节完成则可能深化角色背景、更新结局基调；全部章节完成或用户选择结束时 → 调用 `generate_ending_prediction` / `modify_ending_content` 生成并展示结局。

4. **Web 特有**  
   前端请求 `generate-worldview`、`generate-option`、`pregenerate-next-layers`、`get-pregenerated-layer2`、`save-game`、`load-game`、`generate-ending`、`generate-scene-image` 等；后端用 `main2` 暴露的生成函数与 `src` 模块完成计算，结果通过 JSON 或静态文件返回。

---

## 九、依赖关系小结（仅逻辑层）

- **config / constants**：被几乎所有模块引用。
- **utils**：被 wiki、llm、worldview、characters、image、story、game 使用。
- **wiki**：被 llm（global_gen 等）与 story 的 prompt 格式化使用。
- **llm.api**：被 global_gen、local_gen、story、image、adventure 调用。
- **worldview**：被 llm.global_gen 使用；cache 被 template 使用。
- **characters**：被 image（生成主角/配角图）、story（选项中的配角）、adventure 使用。
- **image**：被 story.options（场景图）、game_server（场景图/主角图）、adventure（CLI 下图）使用。
- **story**：被 adventure 与 game_server 调用。
- **game.adventure**：依赖 constants、utils、llm、story、以及通过 main2 间接使用的 worldview、characters、image 等。

以上即各代码文件职责与整体架构的完整说明；实现细节以源码为准。
