# -*- coding: utf-8 -*-
"""游戏常量：难度、基调、主角选项、性能与世界观目录。"""
import os
import threading
from typing import Dict

# 图片生成全局限速（避免 429）
_YUNWU_RATE_LOCK = threading.Lock()
_YUNWU_LAST_CALL_TS = 0.0

DIFFICULTY_SETTINGS = {
    "简单": {"剧情容错率": "高", "矛盾解决难度": "低", "提示频率": "高"},
    "中等": {"剧情容错率": "中", "矛盾解决难度": "中", "提示频率": "中"},
    "困难": {"剧情容错率": "低", "矛盾解决难度": "高", "提示频率": "低"}
}

TONE_CONFIGS = {
    "happy_ending": {
        "name": "圆满结局",
        "description": "故事以积极、乐观、圆满的方式结束，主角达成目标，所有矛盾得到解决",
        "language_features": "语言明亮、温暖，充满希望和正能量，避免过于负面的描写",
        "ending_orientation": "积极向上，主角成功达成目标，人际关系和谐",
        "taboo_content": "避免悲剧结局，避免主角或重要角色死亡，避免严重的负面情绪"
    },
    "bad_ending": {
        "name": "悲剧结局",
        "description": "故事以悲惨、绝望的方式结束，主角未能达成目标，或付出惨重代价",
        "language_features": "语言沉重、压抑，充满悲剧色彩，强调命运的无常",
        "ending_orientation": "主角失败，或成功但付出巨大代价，结局令人悲伤",
        "taboo_content": "避免过于乐观的描写，避免圆满的结局"
    },
    "normal_ending": {
        "name": "普通结局",
        "description": "故事以平淡、真实的方式结束，主角达成部分目标，存在遗憾但也有收获",
        "language_features": "语言平实、自然，贴近现实，强调生活的真实性",
        "ending_orientation": "主角部分成功，结局既有收获也有遗憾，符合现实逻辑",
        "taboo_content": "避免过于极端的描写，避免过于完美或过于悲惨的结局"
    },
    "dark_depressing": {
        "name": "黑深残",
        "description": "故事充满黑暗、压抑、残酷的元素，揭示人性的阴暗面",
        "language_features": "语言阴暗、沉重，充满暴力、压抑和绝望的描写",
        "ending_orientation": "结局可能悲惨，强调人性的黑暗和命运的残酷",
        "taboo_content": "避免过于积极的描写，避免圆满的结局"
    },
    "humorous": {
        "name": "幽默",
        "description": "故事充满笑点，语言轻松诙谐，情节有趣",
        "language_features": "语言幽默、诙谐，充满笑点，对话风趣",
        "ending_orientation": "结局轻松愉快，可能带有喜剧元素",
        "taboo_content": "避免过于严肃、沉重的描写，避免悲剧结局"
    },
    "abstract": {
        "name": "抽象",
        "description": "故事结构松散，情节跳跃，充满象征和隐喻",
        "language_features": "语言富有诗意，充满象征和隐喻，结构松散",
        "ending_orientation": "结局可能开放，强调思考和感受，而非明确的结局",
        "taboo_content": "避免过于线性的叙事，避免明确的结局"
    },
    "aesthetic": {
        "name": "唯美",
        "description": "故事充满美感，语言优美，场景描写细腻",
        "language_features": "语言优美、细腻，充满美感，场景描写生动",
        "ending_orientation": "结局可能悲剧但充满美感，强调美的体验",
        "taboo_content": "避免粗俗、暴力的描写，避免破坏美感的内容"
    },
    "logical": {
        "name": "逻辑推理严谨",
        "description": "故事注重逻辑推理，情节严谨，谜题设计合理",
        "language_features": "语言严谨、准确，逻辑清晰，注重细节",
        "ending_orientation": "结局符合逻辑，谜题得到合理解决，真相大白",
        "taboo_content": "避免逻辑漏洞，避免不合理的情节发展"
    },
    "mysterious": {
        "name": "神秘",
        "description": "故事充满神秘色彩，情节扑朔迷离，悬念丛生",
        "language_features": "语言神秘、悬疑，充满悬念，情节扑朔迷离",
        "ending_orientation": "结局可能保留悬念，强调神秘和未知",
        "taboo_content": "避免过早揭示真相，避免过于明确的结局"
    },
    "stream_of_consciousness": {
        "name": "意识流",
        "description": "故事以主角的意识流动为线索，情节跳跃，注重内心描写",
        "language_features": "语言流畅，充满内心独白，情节跳跃",
        "ending_orientation": "结局可能开放，强调主角的内心变化",
        "taboo_content": "避免过于线性的叙事，避免明确的结局"
    }
}

PROTAGONIST_ATTR_OPTIONS = {
    "颜值": ["极低", "低", "普通", "高", "极高"],
    "智商": ["极低", "低", "普通", "高", "极高"],
    "体力": ["极低", "低", "普通", "高", "极高"],
    "魅力": ["极低", "低", "普通", "高", "极高"]
}

PERFORMANCE_OPTIMIZATION = {
    "enabled": os.getenv("PERF_OPT_ENABLED", "true").lower() == "true",
    "optimize_prompt": os.getenv("PERF_OPT_PROMPT", "true").lower() == "true",
    "optimize_tokens": os.getenv("PERF_OPT_TOKENS", "true").lower() == "true",
    "worldview_max_tokens": int(os.getenv("PERF_WORLDVIEW_TOKENS", "3500")),
    "plot_max_tokens_initial": int(os.getenv("PERF_PLOT_TOKENS_INITIAL", "2500")),
    "plot_max_tokens_normal": int(os.getenv("PERF_PLOT_TOKENS_NORMAL", "2000")),
    "staged_worldview": os.getenv("PERF_STAGED_WORLDVIEW", "true").lower() == "true",
    "use_templates": os.getenv("PERF_USE_TEMPLATES", "false").lower() == "true",
    "template_similarity_threshold": float(os.getenv("PERF_TEMPLATE_THRESHOLD", "0.6")),
    "async_pregeneration": os.getenv("PERF_ASYNC_PREGEN", "true").lower() == "true",
    "stream_first_option": os.getenv("PERF_STREAM_FIRST", "true").lower() == "true",
    "optimize_retry": os.getenv("PERF_OPT_RETRY", "true").lower() == "true",
    "worldview_max_retries": int(os.getenv("PERF_WORLDVIEW_RETRIES", "2")),
    "plot_max_retries": int(os.getenv("PERF_PLOT_RETRIES", "2")),
    "optimize_parsing": os.getenv("PERF_OPT_PARSING", "true").lower() == "true",
    "stream_response": os.getenv("PERF_STREAM_RESPONSE", "false").lower() == "true",
}

WORLDVIEW_TEMPLATE_DIR = "worldview_templates"
WORLDVIEW_CACHE_DIR = "worldview_cache"
if not os.path.exists(WORLDVIEW_TEMPLATE_DIR):
    os.makedirs(WORLDVIEW_TEMPLATE_DIR)
if not os.path.exists(WORLDVIEW_CACHE_DIR):
    os.makedirs(WORLDVIEW_CACHE_DIR)
