# -*- coding: utf-8 -*-
"""世界观缓存：key 生成、加载、保存。"""
import hashlib
import json
import os
from typing import Dict

from src.constants import WORLDVIEW_CACHE_DIR


def _make_worldview_cache_key(user_idea: str, protagonist_attr: Dict, difficulty: str, tone_key: str) -> str:
    raw = f"{user_idea}|{json.dumps(protagonist_attr, ensure_ascii=False)}|{difficulty}|{tone_key}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _load_worldview_cache(cache_key: str) -> Dict:
    cache_path = os.path.join(WORLDVIEW_CACHE_DIR, f"{cache_key}.json")
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠️ 读取世界观缓存失败：{e}")
    return {}


def _save_worldview_cache(cache_key: str, data: Dict):
    try:
        cache_path = os.path.join(WORLDVIEW_CACHE_DIR, f"{cache_key}.json")
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 保存世界观缓存失败：{e}")
