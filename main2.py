# -*- coding: utf-8 -*-
import json
import os
import sys
import re
import hashlib
import requests
import threading
from functools import lru_cache
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from dotenv import load_dotenv
# 新增：导入重试相关模块
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type, retry_if_result

# 设置环境变量以使用 UTF-8 编码（解决 Windows GBK 编码问题）
if sys.platform == 'win32':
    os.environ['PYTHONIOENCODING'] = 'utf-8'


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
    _YUNWU_RATE_LOCK,
    _YUNWU_LAST_CALL_TS,
)
from src.utils.io_utils import safe_input
from src.utils.text_utils import _safe_str, _clip_text, _extract_core_features_from_prompt
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
from src.llm.global_gen import llm_generate_global, _get_default_worldview
from src.llm.local_gen import llm_generate_local, _get_default_scene
from src.characters.paths import generate_game_id, ensure_main_character_dir, ensure_character_references_dir
from src.characters.archives import (
    _load_role_archives,
    _save_role_archives,
    _next_role_id,
    _find_archive_by_name_or_alias,
    _sanitize_filename_for_role,
    _next_img_id,
)
from src.characters.supporting import (
    extract_supporting_characters_in_scene,
    extract_supporting_characters_with_names,
    get_or_create_supporting_role_archive,
    archive_supporting_role_first_appearance,
    update_supporting_role_aliases_from_plot,
)
from src.image.api_providers import (
    generate_scene_image,
    generate_main_character_image,
)
from src.image.validation import validate_image_url, fix_incomplete_url

# ------------------------------
# 已拆分到 src.image：prompt_optimize / size / api_common / api_providers / validation / storage
# ------------------------------
# ------------------------------
# 已拆分到 src.story：ending / options
# ------------------------------
from src.story.ending import get_video_task_status, modify_ending_tone, modify_ending_content, generate_ending_prediction
from src.story.options import prune_options, _generate_single_option, _generate_single_option_text_only, generate_all_options

# ------------------------------
# 已拆分到 src.game.adventure：TextAdventureGame
# ------------------------------
from src.game.adventure import TextAdventureGame

# ------------------------------
# 启动游戏
# ------------------------------
if __name__ == "__main__":
    game = TextAdventureGame()
    game.start()