# -*- coding: utf-8 -*-
"""Extract image module code from main2.py to src/image/api_providers.py"""
import re

with open("main2.py", "r", encoding="utf-8") as f:
    content = f.read()

# Line ranges (1-indexed in editor): 306-3817 for the main block
# We need: prompt_templates (305-424), then 971-3817 (img2img through generate_scene_image)
# Skip: 82-304 (optimize_image_prompt - in prompt_optimize), 422-749 (part in prompt_optimize)
# Skip: 751-819 (size), 821-964 (api_common), 2311-2513 (validation, storage)

lines = content.split("\n")

# Build header with imports
header = '''# -*- coding: utf-8 -*-
"""图片 API 各供应商实现及主角/场景图生成。"""
import os
import re
import json
import time
import random
import hashlib
import requests
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

from src.config import IMAGE_GENERATION_CONFIG
from src.constants import _YUNWU_RATE_LOCK, _YUNWU_LAST_CALL_TS
from src.utils.text_utils import _safe_str, _clip_text
from src.image.size import calculate_image_size_for_viewport
from src.image.api_common import (
    _ref_image_to_input,
    call_dalle_api_with_size,
    REPLICATE_IMG2IMG_VERSION,
)
from src.image.validation import validate_image_url, fix_incomplete_url
from src.image.storage import save_base64_image
from src.image.prompt_optimize import (
    optimize_image_prompt_with_llm,
    optimize_main_character_prompt_with_llm,
)
from src.characters.paths import generate_game_id, ensure_main_character_dir
from src.characters.archives import _load_role_archives
from src.characters.supporting import (
    extract_supporting_characters_with_names,
    get_or_create_supporting_role_archive,
    archive_supporting_role_first_appearance,
    update_supporting_role_aliases_from_plot,
)

# 主角三视图 prompt 模板
prompt_template_front = """
Generate a full-body, front-view portrait of character {identifier} based on the following description, with a pure white background. The character should be centered in the image, occupying most of the frame. Gazing straight ahead. Standing with arms relaxed at sides. Natural expression.
Features: {features}
Style: {style}
No text, no symbols, no watermark, no garbled characters, no words.
""".strip()

prompt_template_side = """
Generate a full-body, side-view portrait of character {identifier} based on the provided front-view portrait, with a pure white background. The character should be centered in the image, occupying most of the frame. Facing left. Standing with arms relaxed at sides.
No text, no symbols, no watermark, no garbled characters, no words.
""".strip()

prompt_template_back = """
Generate a full-body, back-view portrait of character {identifier} based on the provided front-view portrait, with a pure white background. The character should be centered in the image, occupying most of the frame. No facial features should be visible.
No text, no symbols, no watermark, no garbled characters, no words.
""".strip()

'''

# Extract: call_image_api_with_custom_size (821-896) + img2img through generate_scene (971-3816)
# EXCLUDE: call_dalle_api_with_size (898-915), _ref_image_to_input (918-965), REPLICATE_IMG2IMG (967-968),
#          validate_image_url, fix_incomplete_url, save_base64_image (2311-2513)
part1 = lines[820:897]   # call_image_api_with_custom_size
part2 = lines[969:2310]  # img2img through generate_scene_image, before validate
part3 = lines[2514:3816]  # call_gemini_img2img through end of generate_scene_image
block_lines = part1 + ["", ""] + part2 + part3
block = "\n".join(block_lines)

# Fix Path(__file__).resolve().parent - in api_providers it's src/image/, need to go to project root for image_cache
block = block.replace(
    'Path(__file__).resolve().parent / "image_cache"',
    'Path(__file__).resolve().parent.parent.parent / "image_cache"'
)

with open("src/image/api_providers.py", "w", encoding="utf-8") as f:
    f.write(header + block)

print("Created src/image/api_providers.py")
