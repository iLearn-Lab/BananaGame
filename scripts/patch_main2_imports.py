# -*- coding: utf-8 -*-
"""在 main2.py 开头加入从 src 的导入，并删除已迁移到 src 的代码块（约 22–1033 行）。"""
import os

main2_path = os.path.join(os.path.dirname(__file__), "..", "main2.py")
with open(main2_path, "r", encoding="utf-8") as f:
    lines = f.readlines()

# 保留 1–21 行（编码、import、win32）
head = lines[:21]

# 要插入的导入
import_block = """
# ------------------------------
# 已拆分到 src：config / constants / utils / wiki / llm.api / worldview
# ------------------------------
from src.config import (
    AI_API_CONFIG,
    IMAGE_GENERATION_CONFIG,
    WIKI_LOOKUP_ENABLED,
    WIKI_LANGS,
    WIKI_TIMEOUT_SECONDS,
    WIKI_MAX_SNIPPET_CHARS,
)
from src.constants import (
    DIFFICULTY_SETTINGS,
    TONE_CONFIGS,
    PROTAGONIST_ATTR_OPTIONS,
    PERFORMANCE_OPTIMIZATION,
    WORLDVIEW_TEMPLATE_DIR,
    WORLDVIEW_CACHE_DIR,
)
from src.utils.io_utils import safe_input
from src.utils.text_utils import _safe_str, _clip_text
from src.wiki.lookup import (
    wiki_lookup_theme_and_character,
    _format_protagonist_canonical_for_prompt,
)
from src.llm.api import call_ai_api, extract_and_validate_json
from src.worldview.cache import (
    _make_worldview_cache_key,
    _load_worldview_cache,
    _save_worldview_cache,
)
from src.worldview.template import (
    _load_template_worldview,
    _merge_template_with_input,
    _background_fill_worldview_details,
)
from src.worldview.parser import _regex_fill_worldview

"""

# 原文件 1032 行之后是 "# LLM提示词优化函数"，0-based 为 1031
tail = lines[1031:]

new_content = "".join(head) + import_block + "".join(tail)
with open(main2_path, "w", encoding="utf-8") as f:
    f.write(new_content)
print("Patched main2.py: added src imports, removed lines 22-1031.")
