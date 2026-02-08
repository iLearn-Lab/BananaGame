# main2.py 拆分清单

**当前进度**：第一步～第八步已完成（config、constants、utils、wiki、llm.api、worldview、llm 生成、角色、**图片 image**）。main2.py 已改为从 `src` 导入上述模块。剩余：第九步（story）、第十步（game）、第十一步（入口）。

按「依赖从少到多」的顺序拆，避免循环引用。每步：把对应行复制到新文件 → 补上 import → 在 main2.py 里改为从新模块 `from ... import ...`。

---

## 第一步：配置与常量（无依赖）

### 1. `src/config.py`
- **从 main2.py 复制**：约 **1–88 行**（去掉具体函数，只保留）
  - 文件头 `# -*- coding: utf-8 -*-`
  - `import os`、`from dotenv import load_dotenv`、`load_dotenv()`
  - `AI_API_CONFIG`（55–59）
  - `IMAGE_GENERATION_CONFIG`（64–79）
  - `WIKI_LOOKUP_ENABLED`、`WIKI_LANGS`、`WIKI_TIMEOUT_SECONDS`、`WIKI_MAX_SNIPPET_CHARS`（84–87）
- **说明**：只放从 `.env` 读出来的配置字典和开关，不放业务逻辑。

### 2. `src/constants.py`
- **从 main2.py 复制**：约 **520–666 行**
  - `_YUNWU_RATE_LOCK`、`_YUNWU_LAST_CALL_TS`（若后面 image 用得到可改放 image 或保留）
  - `DIFFICULTY_SETTINGS`（529–533）
  - `TONE_CONFIGS`（538–610）
  - `PROTAGONIST_ATTR_OPTIONS`（611–617）
  - `PERFORMANCE_OPTIMIZATION`（621–657）
  - `WORLDVIEW_TEMPLATE_DIR`、`WORLDVIEW_CACHE_DIR`（658–663）
- **依赖**：可只依赖 `os`，或从 `config` 读部分项（若你把部分常量迁到 config 也可）。
- **说明**：游戏难度、基调、主角选项、世界观目录等「不变/少变」的常量。

---

## 第二步：工具（仅标准库/环境）

### 3. `src/utils/io_utils.py`
- **从 main2.py 复制**：约 **17–50 行**
  - 若保留 Windows UTF-8：`import sys`、`import os` 及 `if sys.platform == 'win32': os.environ['PYTHONIOENCODING']='utf-8'`
  - `safe_input`（25–46）
- **依赖**：`sys`、`os`。
- **说明**：输入防护，多处会用到，先拆可减少后续重复复制。

### 4. `src/utils/text_utils.py`
- **从 main2.py 复制**：**90–102 行**
  - `_safe_str`、`_clip_text`
- **依赖**：无（仅 str）。
- **说明**：纯文本小工具，被 wiki 等模块用。

---

## 第三步：Wiki（依赖 config + utils）

### 5. `src/wiki/lookup.py`
- **从 main2.py 复制**：约 **104–669 行中与 wiki 相关的部分**
  - 即：`_wiki_api_get`、`_wiki_search`、`_wiki_langlink_title`、`_wiki_summary`
  - `_summary_is_disambiguation`、`_summary_to_compact_evidence`、`_extract_image_url_from_summary`
  - `_infer_gender_from_text`、`_format_protagonist_canonical_for_prompt`、`_looks_like_real_ip_or_person`
  - `wiki_lookup_theme_and_character`（298–约 518）
- **依赖**：`src.config`（WIKI_*）、`src.utils.text_utils`（`_safe_str`、`_clip_text`）、`requests`、`re`、`functools.lru_cache`、`urllib.parse.quote`。
- **说明**：所有「现实题材/IP 资料检索」逻辑集中在此。

---

## 第四步：LLM 基础（被世界观、剧情、图片等调用）

### 6. `src/llm/api.py`
- **从 main2.py 复制**：约 **848–1033 行**
  - `call_ai_api`（848–923）
  - `extract_and_validate_json`（928–约 1033）
- **依赖**：`src.config`（`AI_API_CONFIG`）、`requests`。
- **说明**：通用 LLM 调用与 JSON 解析，不加业务 prompt。

---

## 第五步：世界观（依赖 LLM + constants）

### 7. `src/worldview/cache.py`
- **从 main2.py 复制**：约 **670–692 行**
  - `_make_worldview_cache_key`、`_load_worldview_cache`、`_save_worldview_cache`
- **依赖**：`src.config` 或 `src.constants`（`WORLDVIEW_CACHE_DIR`）、`hashlib`、`json`、`os`。

### 8. `src/worldview/template.py`
- **从 main2.py 复制**：约 **695–741 行**
  - `_load_template_worldview`、`_merge_template_with_input`、`_background_fill_worldview_details`
- **依赖**：`src.worldview.cache`、`src.constants`、`src.llm.api`（若 `_background_fill_worldview_details` 里调 `llm_generate_global`，则这里改为从 `src.llm.global_gen` 导入，见下）。

### 9. `src/worldview/parser.py`
- **从 main2.py 复制**：约 **744–847 行**
  - 所有 `_LA`、`_REGEX_*` 编译常量（748–758 等）
  - `_regex_fill_worldview`（760–约 847）
- **依赖**：`re`、`typing`。

---

## 第六步：LLM 生成（全局/本地）

### 10. `src/llm/global_gen.py`
- **从 main2.py 复制**：约 **5337–6037 行**
  - `llm_generate_global`、`_get_default_worldview`
- **依赖**：`src.llm.api`、`src.config`、`src.constants`、`src.worldview.cache`、`src.worldview.template`、`src.worldview.parser`、`src.wiki.lookup`（若用到了）。
- **说明**：全局世界观生成，体积大，可再按「分段/模板/缓存」拆成子函数，文件内先保持一块。

### 11. `src/llm/local_gen.py`
- **从 main2.py 复制**：约 **7433–7714 行**
  - `llm_generate_local`、`_get_default_scene`
- **依赖**：`src.llm.api`、`src.config`、`src.constants`、以及可能用到的 worldview/options 等。

---

## 第七步：角色（主角+配角）

### 12. `src/characters/paths.py`
- **从 main2.py 复制**：约 **1371–1398 行**
  - `generate_game_id`、`ensure_main_character_dir`、`ensure_character_references_dir`
  - 常量 `SUPPORTING_ROLE_ARCHIVES_FILE`（1393）
- **依赖**：`time`、`random`、`pathlib.Path`。

### 13. `src/characters/archives.py`
- **从 main2.py 复制**：约 **1401–1469 行**
  - `_load_role_archives`、`_save_role_archives`、`_next_role_id`、`_find_archive_by_name_or_alias`、`_sanitize_filename_for_role`、`_next_img_id`
- **依赖**：`src.characters.paths`、`src.utils.text_utils`（`_safe_str`）、`json`、`re`、`pathlib`。

### 14. `src/characters/supporting.py`
- **从 main2.py 复制**：约 **1496–1728 行**
  - `extract_supporting_characters_in_scene`、`extract_supporting_characters_with_names`
  - `get_or_create_supporting_role_archive`、`archive_supporting_role_first_appearance`
  - `_extract_character_core_from_prompt`、`update_supporting_role_aliases_from_plot`
- **依赖**：`src.characters.paths`、`src.characters.archives`、`src.llm.api`（若内部有调 LLM）、`src.utils.text_utils`。

---

## 第八步：图片（多供应商、尺寸、校验、存储）

### 15. `src/image/prompt_optimize.py`
- **从 main2.py 复制**：约 **1035–1342 行**（以及 1730–2063）
  - `optimize_image_prompt_with_llm`（1035–约 1370）
  - `_get_style_description`、`_extract_core_features_from_prompt`、`optimize_main_character_prompt_with_llm`（1730–2063）
- **依赖**：`src.llm.api`、`src.config`、`src.constants`、`src.utils.text_utils`。

### 16. `src/image/size.py`
- **从 main2.py 复制**：约 **2064–2132 行**
  - `calculate_image_size_for_viewport`
- **依赖**：`src.config`（IMAGE_GENERATION_CONFIG 等）。

### 17. `src/image/api_common.py`
- **从 main2.py 复制**：约 **2134–2280** 以及 **2231** 起
  - `call_image_api_with_custom_size`、`call_dalle_api_with_size`、`_ref_image_to_input`
  - 以及用到的 `REPLICATE_IMG2IMG_VERSION` 等常量
- **依赖**：`src.config`、`src.image.size`、`requests`、`base64`、`pathlib` 等。

### 18. `src/image/api_providers.py`
- **从 main2.py 复制**：约 **2284–5131 行**（按函数切分）
  - `call_img2img_via_replicate_direct`、`call_img2img_via_yunwu`、`call_stable_diffusion_api_with_size`
  - `generate_main_character_image`（2717–3095）
  - `generate_scene_image`（3097–约 3623）
  - `validate_image_url`、`fix_incomplete_url`（保留一份，去掉重复定义）
  - `save_base64_image`、`call_gemini_img2img`
  - `call_yunwu_image_api`、`call_comfyui_api`、`call_replicate_api`、`call_dalle_api`、`call_stable_diffusion_api`
- **依赖**：`src.config`、`src.image.size`、`src.image.api_common`、`src.image.prompt_optimize`、`src.characters.*`（若生成主角/配角图时用档案）、`threading`（限速）等。
- **说明**：文件会很大，可再拆成 `img2img.py`、`main_character_image.py`、`scene_image.py`、`providers/yunwu.py` 等，第一轮先合并到一个或两个文件也可。

### 19. `src/image/validation.py`
- **从 main2.py 复制**：`validate_image_url`、`fix_incomplete_url` 各一份（3624–3650、3651–3675 或 3703 前）
- **依赖**：`requests`、`re`。

### 20. `src/image/storage.py`
- **从 main2.py 复制**：`save_base64_image`（3734–约 3826）
- **依赖**：`pathlib`、`base64`、`hashlib` 等。

---

## 第九步：剧情（选项、结局、批量图）

### 21. `src/story/ending.py`
- **从 main2.py 复制**：约 **5139–5333 行**
  - `get_video_task_status`（5132–5134，可放 story 或单独 video/task.py）
  - `modify_ending_tone`、`modify_ending_content`、`generate_ending_prediction`
- **依赖**：`src.llm.api`、`src.config`、`src.constants`。

### 22. `src/story/options.py`
- **从 main2.py 复制**：约 **6177–7431 行**
  - `prune_options`、`_generate_single_option`、`_generate_single_option_text_only`
  - `_generate_images_parallel`、`generate_all_options`
- **依赖**：`src.llm.api`、`src.llm.global_gen`、`src.llm.local_gen`、`src.image.*`（场景图）、`src.characters.*`（配角）、`src.constants`。

---

## 第十步：游戏主循环（入口）

### 23. `src/game/adventure.py`
- **从 main2.py 复制**：约 **7719–9062 行**
  - `class TextAdventureGame` 及其中所有方法
- **依赖**：上面所有模块（按需导入），以及 `src.utils.io_utils.safe_input`、`src.constants`（DIFFICULTY_SETTINGS、TONE_CONFIGS、PROTAGONIST_ATTR_OPTIONS）等。

---

## 第十一步：入口与旧文件

### 24. `main.py`（项目根目录新建）
- **内容**：仅启动游戏，例如：
  - `# -*- coding: utf-8 -*-`
  - `import sys`、`import os`（如需把项目根加入 `sys.path`）
  - `from src.game.adventure import TextAdventureGame`
  - `if __name__ == "__main__": game = TextAdventureGame(); game.start()`
- **说明**：以后用 `python main.py` 启动，`main2.py` 可在确认无误后废弃或保留作备份。

---

## 拆分顺序小结（按此顺序做可减少返工）

| 顺序 | 文件 | 说明 |
|------|------|------|
| 1 | `src/config.py` | 配置 |
| 2 | `src/constants.py` | 常量 |
| 3 | `src/utils/io_utils.py` | 输入工具 |
| 4 | `src/utils/text_utils.py` | 文本工具 |
| 5 | `src/wiki/lookup.py` | 百科检索 |
| 6 | `src/llm/api.py` | LLM 调用与 JSON |
| 7 | `src/worldview/cache.py` | 世界观缓存 |
| 8 | `src/worldview/parser.py` | 世界观解析 |
| 9 | `src/worldview/template.py` | 世界观模板与后台补全 |
| 10 | `src/llm/global_gen.py` | 全局世界观生成 |
| 11 | `src/llm/local_gen.py` | 本地剧情生成 |
| 12 | `src/characters/paths.py` | 角色目录与 game_id |
| 13 | `src/characters/archives.py` | 配角档案读写 |
| 14 | `src/characters/supporting.py` | 配角提取与建档 |
| 15 | `src/image/prompt_optimize.py` | 图片 prompt 优化 |
| 16 | `src/image/size.py` | 图片尺寸 |
| 17 | `src/image/validation.py` | URL 校验与修复 |
| 18 | `src/image/storage.py` | base64 存图 |
| 19 | `src/image/api_common.py` | 通用出图接口 |
| 20 | `src/image/api_providers.py` | 各供应商 + 主角/场景图 |
| 21 | `src/story/ending.py` | 结局与视频状态 |
| 22 | `src/story/options.py` | 选项与批量图 |
| 23 | `src/game/adventure.py` | TextAdventureGame |
| 24 | `main.py` | 入口 |

---

## 注意事项

1. **每拆一个文件**：在新文件中补全 `import`（包括对 `src.xxx` 的引用），再在 `main2.py` 中删除已迁出代码并改为 `from src.xxx.yyy import ...`，然后跑一遍游戏或关键流程，避免遗漏依赖。
2. **循环依赖**：若出现 A 用 B、B 用 A，可把共用部分抽到第三个模块（如 `llm.api`），或把「调用 LLM」的入口统一放在 `llm`，其他模块只调 `llm` 不互相调。
3. **重复定义**：`validate_image_url`、`fix_incomplete_url` 在 main2.py 里出现两次，合并到一个文件（如 `image/validation.py`）只保留一份。
4. **大文件**：`src/image/api_providers.py`、`src/llm/global_gen.py` 若单文件过长，可在第一轮拆完跑通后再按「子功能」拆成多个文件。

按上述顺序和清单，即可把 `main2.py` 拆成不同文件并保持「前端 + 后端（LLM / 世界观 / 角色生成 / 剧情生成）」的板块划分。
