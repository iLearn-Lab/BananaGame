# -*- coding: utf-8 -*-
"""世界观模板加载、合并与后台补全。"""
import json
import os
from typing import Dict

from src.constants import WORLDVIEW_TEMPLATE_DIR, PERFORMANCE_OPTIMIZATION
from src.worldview.cache import _make_worldview_cache_key, _load_worldview_cache


def _load_template_worldview(user_idea: str) -> Dict:
    """从模板库中选择匹配的世界观"""
    if not PERFORMANCE_OPTIMIZATION.get("use_templates"):
        return {}
    idea_lower = user_idea.lower()
    for root, _, files in os.walk(WORLDVIEW_TEMPLATE_DIR):
        for file in files:
            if not file.endswith(".json"):
                continue
            path = os.path.join(root, file)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    tpl = json.load(f)
                keywords = tpl.get("keywords", [])
                if any(k.lower() in idea_lower for k in keywords):
                    print(f"✅ 命中世界观模板：{file}")
                    return tpl.get("worldview", tpl)
            except Exception as e:
                print(f"⚠️ 读取模板失败 {path}：{e}")
    return {}


def _merge_template_with_input(template_view: Dict, protagonist_attr: Dict, difficulty: str, tone_key: str) -> Dict:
    """将模板与用户输入合并，确保必要字段存在"""
    merged = json.loads(json.dumps(template_view, ensure_ascii=False))
    merged.setdefault("core_worldview", {}).setdefault("protagonist_ability", "")
    merged.setdefault("flow_worldline", {})
    merged["input_meta"] = {
        "protagonist_attr": protagonist_attr,
        "difficulty": difficulty,
        "tone": tone_key
    }
    return merged


def _background_fill_worldview_details(cache_key: str, user_idea: str, protagonist_attr: Dict, difficulty: str, tone_key: str):
    """后台补全世界观细节（延迟导入 llm 避免循环依赖）"""
    try:
        from src.llm.global_gen import llm_generate_global
        print("🧵 正在后台补全世界观细节...")
        detailed_state = llm_generate_global(user_idea, protagonist_attr, difficulty, tone_key, force_full=True)
        if detailed_state:
            print("✅ 世界观细节补全完成")
    except Exception as e:
        print(f"⚠️ 后台补全世界观失败：{e}")
