# -*- coding: utf-8 -*-
"""Extract TextAdventureGame class from main2.py to src/game/adventure.py"""
import os

with open("main2.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

# TextAdventureGame: lines 98-1435 (0-indexed: 97-1434)
start_idx = 97
end_idx = 1435
block_lines = lines[start_idx:end_idx]
block = "".join(block_lines)

header = '''# -*- coding: utf-8 -*-
"""游戏主循环：TextAdventureGame 类。"""
import json
import os
import random
from datetime import datetime
from typing import Dict, List, Tuple, Optional

from src.constants import DIFFICULTY_SETTINGS, TONE_CONFIGS, PROTAGONIST_ATTR_OPTIONS
from src.utils.io_utils import safe_input
from src.llm.global_gen import llm_generate_global
from src.llm.local_gen import llm_generate_local
from src.story.ending import (
    get_video_task_status,
    modify_ending_tone,
    modify_ending_content,
    generate_ending_prediction,
)
from src.story.options import generate_all_options


'''

out_path = "src/game/adventure.py"
os.makedirs(os.path.dirname(out_path), exist_ok=True)
with open(out_path, "w", encoding="utf-8") as f:
    f.write(header + block)

print(f"Created {out_path}")
print(f"  Extracted {end_idx - start_idx} lines")
