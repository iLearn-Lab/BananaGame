# -*- coding: utf-8 -*-
"""Extract story (options) module from main2.py to src/story/options.py"""
import os

with open("main2.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

# Extract lines 296-1556 (0-indexed: 295-1555)
# prune_options through end of generate_all_options, before TextAdventureGame
start_idx = 295
end_idx = 1556
block_lines = lines[start_idx:end_idx]
block = "".join(block_lines)

# Build header with imports
header = '''# -*- coding: utf-8 -*-
"""选项剪枝、单选项剧情生成、批量图生成。"""
import hashlib
import json
import os
import re
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple, Optional

from src.config import AI_API_CONFIG, IMAGE_GENERATION_CONFIG
from src.constants import TONE_CONFIGS, PERFORMANCE_OPTIMIZATION
from src.llm.api import call_ai_api
from src.wiki.lookup import _format_protagonist_canonical_for_prompt
from src.image.api_providers import generate_scene_image
from src.image.validation import validate_image_url, fix_incomplete_url

'''

# Remove inline imports that are now at module level
block = block.replace("    import hashlib\n    from pathlib import Path\n    \n    ", "    ")
block = block.replace("    import time\n    ", "    ")

with open("src/story/options.py", "w", encoding="utf-8") as f:
    f.write(header + block)

print("Created src/story/options.py")
print(f"  Extracted {end_idx - start_idx} lines")
