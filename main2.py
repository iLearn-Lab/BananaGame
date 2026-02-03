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
# 通用输入防护
# ------------------------------
def safe_input(prompt: str, default: str = "", retries: int = 3) -> str:
    """
    包装 input，支持默认值、重试和 Ctrl+C/EOF 兜底，避免阻塞或崩溃。
    :param prompt: 提示文本
    :param default: 默认返回值
    :param retries: 最多重试次数
    """
    for attempt in range(retries):
        try:
            value = input(prompt)
            if value is None:
                return default
            value = value.strip()
            if value:
                return value
            if default:
                return default
            print("⚠️ 输入为空，请重新输入。")
        except (KeyboardInterrupt, EOFError):
            print("\n⏹ 检测到中断，已返回默认值。")
            return default
    print("⚠️ 多次无效输入，已使用默认值。")
    return default

# 加载环境变量
load_dotenv()

# ------------------------------
# 全局常量定义（替换为yunwu.ai配置）
# ------------------------------
AI_API_CONFIG = {
    "api_key": os.getenv("Camera_Analyst_API_KEY"),
    "base_url": os.getenv("Camera_Analyst_BASE_URL"),
    "model": os.getenv("Camera_Analyst_MODEL")
}

# ------------------------------
# 视觉内容生成API配置
# ------------------------------
IMAGE_GENERATION_CONFIG = {
    "provider": os.getenv("IMAGE_GENERATION_PROVIDER", "yunwu"),  # yunwu, replicate, openai, stable_diffusion, comfyui
    "yunwu_api_key": os.getenv("Image_Generation_API_KEY", ""),  # 使用yunwu.ai的图片生成API
    "yunwu_base_url": os.getenv("Image_Generation_BASE_URL", "https://yunwu.ai/v1"),
    "yunwu_model": os.getenv("Image_Generation_MODEL", "sora_image"),
    "replicate_api_token": os.getenv("REPLICATE_API_TOKEN", ""),
    "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
    "stable_diffusion_base_url": os.getenv("STABLE_DIFFUSION_BASE_URL", ""),
    "stable_diffusion_api_key": os.getenv("STABLE_DIFFUSION_API_KEY", ""),
    "comfyui_host": os.getenv("COMFYUI_HOST", ""),
    # 图生图（img2img）：云雾 API，与其他服务一致使用 BASE_URL（https://yunwu.ai/v1）+ PATH
    "img2img_api_key": os.getenv("Img2img_API_KEY", ""),
    "img2img_base_url": os.getenv("Img2img_BASE_URL", "https://yunwu.ai/v1"),
    "img2img_path": os.getenv("Img2img_PATH", "/images/edit"),  # 云雾图生图端点路径，根据文档调整
    "img2img_model": os.getenv("Img2img_MODEL", "stability-ai/stable-diffusion-img2img"),  # 云雾的图生图模型名
}

# ------------------------------
# 现实题材/IP 资料检索（Wikipedia 中/英 + 二次关键词）
# ------------------------------
WIKI_LOOKUP_ENABLED = os.getenv("WIKI_LOOKUP_ENABLED", "true").lower() == "true"
WIKI_LANGS = [x.strip() for x in os.getenv("WIKI_LANGS", "zh,en").split(",") if x.strip()]
WIKI_TIMEOUT_SECONDS = float(os.getenv("WIKI_TIMEOUT_SECONDS", "8"))
WIKI_MAX_SNIPPET_CHARS = int(os.getenv("WIKI_MAX_SNIPPET_CHARS", "1200"))


def _safe_str(v) -> str:
    try:
        return "" if v is None else str(v)
    except Exception:
        return ""


def _clip_text(s: str, max_chars: int) -> str:
    s = _safe_str(s).strip()
    if max_chars <= 0:
        return s
    return s if len(s) <= max_chars else (s[:max_chars] + "…")


def _wiki_api_get(lang: str, params: Dict) -> Dict:
    """Wikipedia action API GET。失败返回 {}"""
    try:
        url = f"https://{lang}.wikipedia.org/w/api.php"
        resp = requests.get(
            url,
            params={"format": "json", "formatversion": 2, **(params or {})},
            timeout=WIKI_TIMEOUT_SECONDS,
            headers={"User-Agent": "DN-main/1.0 (character-lookup; https://example.invalid)"}
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _wiki_search(lang: str, query: str, limit: int = 5) -> List[Dict]:
    query = _safe_str(query).strip()
    if not query:
        return []
    data = _wiki_api_get(lang, {"action": "query", "list": "search", "srsearch": query, "srlimit": max(1, int(limit))})
    items = (data.get("query", {}) or {}).get("search", []) if isinstance(data, dict) else []
    return items if isinstance(items, list) else []


def _wiki_langlink_title(source_lang: str, source_title: str, target_lang: str) -> str:
    """
    尝试通过 Wikipedia langlinks 获取跨语言标题（例如 zh -> en）。
    失败返回空串。
    """
    source_title = _safe_str(source_title).strip()
    if not source_title:
        return ""
    data = _wiki_api_get(
        source_lang,
        {
            "action": "query",
            "prop": "langlinks",
            "titles": source_title,
            "lllang": target_lang,
            "lllimit": 1,
        },
    )
    try:
        pages = (data.get("query", {}) or {}).get("pages", [])
        if not isinstance(pages, list) or not pages:
            return ""
        page0 = pages[0] if isinstance(pages[0], dict) else {}
        lls = page0.get("langlinks", [])
        if not isinstance(lls, list) or not lls:
            return ""
        ll0 = lls[0] if isinstance(lls[0], dict) else {}
        return _safe_str(ll0.get("title")).strip()
    except Exception:
        return ""


def _wiki_summary(lang: str, title: str) -> Dict:
    """Wikipedia REST summary。失败返回 {}"""
    title = _safe_str(title).strip()
    if not title:
        return {}
    try:
        # https://en.wikipedia.org/api/rest_v1/page/summary/Albert_Einstein
        url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{quote(title)}"
        resp = requests.get(
            url,
            timeout=WIKI_TIMEOUT_SECONDS,
            headers={"User-Agent": "DN-main/1.0 (character-lookup; https://example.invalid)"}
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _summary_is_disambiguation(summary: Dict) -> bool:
    try:
        t = (summary or {}).get("type", "")
        return _safe_str(t).lower() == "disambiguation"
    except Exception:
        return False


def _summary_to_compact_evidence(summary: Dict) -> str:
    """把 summary 压缩成可喂给 LLM 的证据文本。"""
    if not isinstance(summary, dict) or not summary:
        return ""
    title = _safe_str(summary.get("title"))
    desc = _safe_str(summary.get("description"))
    extract = _safe_str(summary.get("extract"))
    url = ""
    try:
        url = _safe_str(((summary.get("content_urls") or {}).get("desktop") or {}).get("page"))
    except Exception:
        url = ""
    parts = []
    if title:
        parts.append(f"标题：{title}")
    if desc:
        parts.append(f"简介：{desc}")
    if extract:
        parts.append(f"摘要：{_clip_text(extract, WIKI_MAX_SNIPPET_CHARS)}")
    if url:
        parts.append(f"来源：{url}")
    return "\n".join(parts).strip()


def _extract_image_url_from_summary(summary: Dict) -> str:
    """从 Wikipedia summary 中提取可用的图片URL（优先 originalimage，其次 thumbnail）。"""
    if not isinstance(summary, dict) or not summary:
        return ""
    try:
        oi = summary.get("originalimage") or {}
        if isinstance(oi, dict):
            src = _safe_str(oi.get("source")).strip()
            if src.startswith(("http://", "https://")):
                return src
    except Exception:
        pass
    try:
        th = summary.get("thumbnail") or {}
        if isinstance(th, dict):
            src = _safe_str(th.get("source")).strip()
            if src.startswith(("http://", "https://")):
                return src
    except Exception:
        pass
    return ""


def _infer_gender_from_text(text: str) -> str:
    """从摘要/描述里粗略推断性别。返回 '男性'/'女性'/''"""
    t = _safe_str(text)
    if not t:
        return ""
    # 中文线索
    if re.search(r"\b她\b|女性|女演员|女歌手|女作家|女政治家|女运动员|公主|王后|皇后", t):
        return "女性"
    if re.search(r"\b他\b|男性|男演员|男歌手|男作家|男政治家|男运动员|王子|国王|皇帝", t):
        return "男性"
    # 英文线索（只做很粗的 pronoun）
    if re.search(r"\bshe\b|\bher\b", t, flags=re.I):
        return "女性"
    if re.search(r"\bhe\b|\bhis\b", t, flags=re.I):
        return "男性"
    return ""


def _format_protagonist_canonical_for_prompt(canonical: Dict) -> str:
    """将主角规范信息格式化为可放入 prompt 的文本块（性别、年龄感、外观关键词等）。"""
    if not canonical or not isinstance(canonical, dict):
        return "（无）"
    lines = []
    if canonical.get("gender"):
        lines.append(f"性别：{canonical['gender']}")
    if canonical.get("age_range"):
        lines.append(f"年龄感：{canonical['age_range']}")
    if canonical.get("signature_look_keywords"):
        lines.append(f"标志性外观关键词：{canonical['signature_look_keywords']}")
    if canonical.get("name_zh") or canonical.get("name_en"):
        lines.append(f"主角姓名(中/英)：{canonical.get('name_zh') or '—'} / {canonical.get('name_en') or '—'}")
    return "\n".join(lines) if lines else "（无）"


def _looks_like_real_ip_or_person(text: str) -> bool:
    """
    粗判断：Wikipedia 摘要是否像“现实存在的作品/IP/人物/故事”，用于决定是否启用“还原已有形象”强约束。
    避免把普通概念词（例如“勇气”“城市”）也强行当成需要还原的IP。
    """
    t = _safe_str(text).strip()
    if not t:
        return False
    # 人物
    if re.search(r"演员|歌手|作家|导演|编剧|政治家|运动员|企业家|科学家|哲学家|画家|数学家", t):
        return True
    if re.search(r"\b(actor|singer|writer|director|screenwriter|politician|athlete|entrepreneur|scientist|philosopher|painter|mathematician)\b", t, flags=re.I):
        return True
    # 作品/IP
    if re.search(r"动画|动漫|漫画|轻小说|小说|电影|电视剧|剧集|游戏|系列|作品|角色|人物|主角|主人公", t):
        return True
    if re.search(r"\b(anime|manga|novel|film|movie|television series|tv series|video game|franchise|character|protagonist)\b", t, flags=re.I):
        return True
    # 真实故事/传说（也算需要“参考”）
    if re.search(r"故事|传说|神话|史诗|历史事件|真实事件", t):
        return True
    if re.search(r"\b(story|legend|myth|historical event|true story)\b", t, flags=re.I):
        return True
    return False


@lru_cache(maxsize=256)
def wiki_lookup_theme_and_character(theme: str) -> Dict:
    """
    尝试判断主题是否为现实存在的IP/人物，并检索其资料。
    返回：
      {
        "is_real_world": bool,
        "theme": {...},          # 主题条目（摘要证据）
        "character": {...},      # 二次检索推到的主角/人物条目（摘要证据，可为空）
        "evidence_text": str,    # 合并后的证据文本（喂给提示词LLM）
      }
    """
    theme = _safe_str(theme).strip()
    if not theme or not WIKI_LOOKUP_ENABLED:
        return {"is_real_world": False, "theme": {}, "character": {}, "evidence_text": ""}

    # 先用主题本身做检索（中/英都试），收集尽量多的语言标题（用于“中英文名+作品名”）
    theme_hits_by_lang: Dict[str, Dict] = {}
    for lang in WIKI_LANGS or ["zh", "en"]:
        results = _wiki_search(lang, theme, limit=5)
        if not results:
            continue
        top = results[0] if isinstance(results[0], dict) else {}
        title = _safe_str(top.get("title")).strip()
        if not title:
            continue
        summary = _wiki_summary(lang, title)
        if not summary or _summary_is_disambiguation(summary):
            continue
        theme_hits_by_lang[lang] = {
            "lang": lang,
            "title": title,
            "summary": summary,
            "image_url": _extract_image_url_from_summary(summary),
        }

    # 用 langlinks 尝试补齐另一种语言标题（提高“中英文名+作品名”命中率）
    try:
        langs = (WIKI_LANGS or ["zh", "en"])
        if "zh" in langs and "en" in langs:
            if "zh" in theme_hits_by_lang and "en" not in theme_hits_by_lang:
                en_title = _wiki_langlink_title("zh", theme_hits_by_lang["zh"]["title"], "en")
                if en_title:
                    en_sum = _wiki_summary("en", en_title)
                    if en_sum and not _summary_is_disambiguation(en_sum):
                        theme_hits_by_lang["en"] = {
                            "lang": "en",
                            "title": en_title,
                            "summary": en_sum,
                            "image_url": _extract_image_url_from_summary(en_sum),
                        }
            if "en" in theme_hits_by_lang and "zh" not in theme_hits_by_lang:
                zh_title = _wiki_langlink_title("en", theme_hits_by_lang["en"]["title"], "zh")
                if zh_title:
                    zh_sum = _wiki_summary("zh", zh_title)
                    if zh_sum and not _summary_is_disambiguation(zh_sum):
                        theme_hits_by_lang["zh"] = {
                            "lang": "zh",
                            "title": zh_title,
                            "summary": zh_sum,
                            "image_url": _extract_image_url_from_summary(zh_sum),
                        }
    except Exception:
        pass

    if not theme_hits_by_lang:
        return {"is_real_world": False, "theme": {}, "character": {}, "evidence_text": ""}

    # primary theme：取第一个命中的语言
    primary_lang = None
    for lang in (WIKI_LANGS or ["zh", "en"]):
        if lang in theme_hits_by_lang:
            primary_lang = lang
            break
    if not primary_lang:
        primary_lang = next(iter(theme_hits_by_lang.keys()))
    primary_theme_hit = theme_hits_by_lang.get(primary_lang, next(iter(theme_hits_by_lang.values())))

    # 认为“现实存在”的最低门槛：有可用条目摘要（不是消歧义）
    # 进一步通过二次查询尝试找到“主角/人物”条目（尤其是作品IP）
    combined_theme_text_parts = []
    for hit in theme_hits_by_lang.values():
        s = hit.get("summary") or {}
        combined_theme_text_parts.append(_safe_str(s.get("description")))
        combined_theme_text_parts.append(_safe_str(s.get("extract")))
    combined_theme_text = "\n".join([x for x in combined_theme_text_parts if _safe_str(x).strip()]).strip()
    is_real_world = _looks_like_real_ip_or_person(combined_theme_text)

    # 二次查询关键词（中/英）
    second_queries = [
        f"{theme} 主人公",
        f"{theme} 主角",
        f"{theme} 人物",
        f"{theme} protagonist",
        f"{theme} main character",
    ]

    # 尽量为每种语言找一个“人物/主角”条目
    character_hits_by_lang: Dict[str, Dict] = {}
    for lang in (WIKI_LANGS or ["zh", "en"]):
        theme_title_same_lang = _safe_str((theme_hits_by_lang.get(lang, {}) or {}).get("title")).strip()
        for q in second_queries:
            results = _wiki_search(lang, q, limit=5)
            if not results:
                continue
            candidates = []
            for it in results:
                if not isinstance(it, dict):
                    continue
                title = _safe_str(it.get("title")).strip()
                if not title:
                    continue
                if theme_title_same_lang and title == theme_title_same_lang:
                    continue
                candidates.append(title)
            if not candidates:
                continue
            cand_title = candidates[0]
            summary = _wiki_summary(lang, cand_title)
            if not summary or _summary_is_disambiguation(summary):
                continue
            character_hits_by_lang[lang] = {
                "lang": lang,
                "title": cand_title,
                "summary": summary,
                "query": q,
                "image_url": _extract_image_url_from_summary(summary),
            }
            break

    # 同样尝试用 langlinks 补齐人物条目的另一种语言标题
    try:
        langs = (WIKI_LANGS or ["zh", "en"])
        if "zh" in langs and "en" in langs:
            if "zh" in character_hits_by_lang and "en" not in character_hits_by_lang:
                en_title = _wiki_langlink_title("zh", character_hits_by_lang["zh"]["title"], "en")
                if en_title:
                    en_sum = _wiki_summary("en", en_title)
                    if en_sum and not _summary_is_disambiguation(en_sum):
                        character_hits_by_lang["en"] = {
                            "lang": "en",
                            "title": en_title,
                            "summary": en_sum,
                            "query": _safe_str(character_hits_by_lang["zh"].get("query")),
                            "image_url": _extract_image_url_from_summary(en_sum),
                        }
            if "en" in character_hits_by_lang and "zh" not in character_hits_by_lang:
                zh_title = _wiki_langlink_title("en", character_hits_by_lang["en"]["title"], "zh")
                if zh_title:
                    zh_sum = _wiki_summary("zh", zh_title)
                    if zh_sum and not _summary_is_disambiguation(zh_sum):
                        character_hits_by_lang["zh"] = {
                            "lang": "zh",
                            "title": zh_title,
                            "summary": zh_sum,
                            "query": _safe_str(character_hits_by_lang["en"].get("query")),
                            "image_url": _extract_image_url_from_summary(zh_sum),
                        }
    except Exception:
        pass

    # is_real_world：仅当摘要像“作品/IP/人物/故事”时为 True，用于触发“还原已有形象”的强约束
    evidence_parts = []
    evidence_parts.append("【Wikipedia 主题条目】")
    for lang in (WIKI_LANGS or ["zh", "en"]):
        hit = theme_hits_by_lang.get(lang)
        if not hit:
            continue
        evidence_parts.append(f"[{lang}]")
        evidence_parts.append(_summary_to_compact_evidence(hit.get("summary")) or "")

    if character_hits_by_lang:
        evidence_parts.append("\n【Wikipedia 二次检索（可能的主角/人物）】")
        for lang in (WIKI_LANGS or ["zh", "en"]):
            hit = character_hits_by_lang.get(lang)
            if not hit:
                continue
            evidence_parts.append(f"[{lang}] 检索词：{_safe_str(hit.get('query'))}")
            evidence_parts.append(_summary_to_compact_evidence(hit.get("summary")) or "")

    evidence_text = _clip_text("\n".join([p for p in evidence_parts if _safe_str(p).strip()]).strip(), 2600)

    # 参考图：优先用人物条目图片，其次主题条目图片
    reference_candidates = []
    for lang in (WIKI_LANGS or ["zh", "en"]):
        c = character_hits_by_lang.get(lang, {})
        img = _safe_str(c.get("image_url")).strip()
        if img:
            reference_candidates.append(("character", lang, img))
    for lang in (WIKI_LANGS or ["zh", "en"]):
        t = theme_hits_by_lang.get(lang, {})
        img = _safe_str(t.get("image_url")).strip()
        if img:
            reference_candidates.append(("theme", lang, img))
    reference_image_url = reference_candidates[0][2] if reference_candidates else ""
    reference_from = reference_candidates[0][0] if reference_candidates else ""

    return {
        "is_real_world": bool(is_real_world),
        "theme": {
            "lang": primary_theme_hit.get("lang"),
            "title": primary_theme_hit.get("title"),
            "description": _safe_str(((primary_theme_hit.get("summary") or {}) or {}).get("description")),
            "extract": _safe_str(((primary_theme_hit.get("summary") or {}) or {}).get("extract")),
            "image_url": _safe_str(primary_theme_hit.get("image_url")).strip(),
        },
        "character": (lambda: (
            {
                "lang": next(iter(character_hits_by_lang.values())).get("lang"),
                "title": next(iter(character_hits_by_lang.values())).get("title"),
                "description": _safe_str(((next(iter(character_hits_by_lang.values())).get("summary") or {}) or {}).get("description")),
                "extract": _safe_str(((next(iter(character_hits_by_lang.values())).get("summary") or {}) or {}).get("extract")),
                "image_url": _safe_str(next(iter(character_hits_by_lang.values())).get("image_url")).strip(),
            } if character_hits_by_lang else {}
        ))(),
        "theme_names": {lang: _safe_str(hit.get("title")).strip() for lang, hit in theme_hits_by_lang.items() if _safe_str(hit.get("title")).strip()},
        "character_names": {lang: _safe_str(hit.get("title")).strip() for lang, hit in character_hits_by_lang.items() if _safe_str(hit.get("title")).strip()},
        "evidence_text": evidence_text,
        "reference_image_url": reference_image_url,
        "reference_image_from": reference_from,
        "reference_image_candidates": reference_candidates[:4],
    }

# ------------------------------
# 图片生成：全局限速（避免 429 / 请求过于频繁）
# ------------------------------
# yunwu.ai 图片生成接口通常有更严格的速率限制；项目内又有多线程并行路径（预生成/批量图片），
# 因此需要跨线程的“最小间隔”控制，降低 429 概率与重试等待时间。
_YUNWU_RATE_LOCK = threading.Lock()
_YUNWU_LAST_CALL_TS = 0.0


DIFFICULTY_SETTINGS = {
    "简单": {"剧情容错率": "高", "矛盾解决难度": "低", "提示频率": "高"},
    "中等": {"剧情容错率": "中", "矛盾解决难度": "中", "提示频率": "中"},
    "困难": {"剧情容错率": "低", "矛盾解决难度": "高", "提示频率": "低"}
}

# ------------------------------
# 基调/风格库配置
# ------------------------------
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

# ------------------------------
# 性能优化配置
# ------------------------------
PERFORMANCE_OPTIMIZATION = {
    # 是否启用所有优化（主开关）
    "enabled": os.getenv("PERF_OPT_ENABLED", "true").lower() == "true",
    
    # 方案1：Prompt精简（减少冗余说明）
    "optimize_prompt": os.getenv("PERF_OPT_PROMPT", "true").lower() == "true",
    
    # 方案2：Token优化（降低max_tokens）
    "optimize_tokens": os.getenv("PERF_OPT_TOKENS", "true").lower() == "true",
    "worldview_max_tokens": int(os.getenv("PERF_WORLDVIEW_TOKENS", "3500")),  # 原5000
    "plot_max_tokens_initial": int(os.getenv("PERF_PLOT_TOKENS_INITIAL", "2500")),  # 原3500
    "plot_max_tokens_normal": int(os.getenv("PERF_PLOT_TOKENS_NORMAL", "2000")),  # 原2500
    
    # 方案3：分阶段生成世界观（核心内容优先返回）
    "staged_worldview": os.getenv("PERF_STAGED_WORLDVIEW", "true").lower() == "true",
    
    # 方案4：世界观模板库（已禁用，强制使用AI生成）
    "use_templates": os.getenv("PERF_USE_TEMPLATES", "false").lower() == "true",
    "template_similarity_threshold": float(os.getenv("PERF_TEMPLATE_THRESHOLD", "0.6")),  # 相似度阈值
    
    # 方案5：异步预生成优化
    "async_pregeneration": os.getenv("PERF_ASYNC_PREGEN", "true").lower() == "true",
    "stream_first_option": os.getenv("PERF_STREAM_FIRST", "true").lower() == "true",  # 流式返回第一个完成的选项
    
    # 方案6：重试优化
    "optimize_retry": os.getenv("PERF_OPT_RETRY", "true").lower() == "true",
    "worldview_max_retries": int(os.getenv("PERF_WORLDVIEW_RETRIES", "2")),  # 原3
    "plot_max_retries": int(os.getenv("PERF_PLOT_RETRIES", "2")),  # 原3
    
    # 方案7：文本解析优化
    "optimize_parsing": os.getenv("PERF_OPT_PARSING", "true").lower() == "true",
    
    # 方案8：流式响应支持
    "stream_response": os.getenv("PERF_STREAM_RESPONSE", "false").lower() == "true",  # 需要API支持
}

# 世界观模板库目录
WORLDVIEW_TEMPLATE_DIR = "worldview_templates"
if not os.path.exists(WORLDVIEW_TEMPLATE_DIR):
    os.makedirs(WORLDVIEW_TEMPLATE_DIR)

# 世界观缓存目录
WORLDVIEW_CACHE_DIR = "worldview_cache"
if not os.path.exists(WORLDVIEW_CACHE_DIR):
    os.makedirs(WORLDVIEW_CACHE_DIR)

# ------------------------------
# 世界观模板与缓存辅助函数
# ------------------------------
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


def _load_template_worldview(user_idea: str) -> Dict:
    """从模板库中选择匹配的世界观"""
    if not PERFORMANCE_OPTIMIZATION["use_templates"]:
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
                # 简单相似度：任一关键词出现即视为命中
                if any(k.lower() in idea_lower for k in keywords):
                    print(f"✅ 命中世界观模板：{file}")
                    return tpl.get("worldview", tpl)
            except Exception as e:
                print(f"⚠️ 读取模板失败 {path}：{e}")
    return {}


def _merge_template_with_input(template_view: Dict, protagonist_attr: Dict, difficulty: str, tone_key: str) -> Dict:
    """将模板与用户输入合并，确保必要字段存在"""
    merged = json.loads(json.dumps(template_view, ensure_ascii=False))  # 深拷贝
    merged.setdefault("core_worldview", {}).setdefault("protagonist_ability", "")
    merged.setdefault("flow_worldline", {})
    merged["input_meta"] = {
        "protagonist_attr": protagonist_attr,
        "difficulty": difficulty,
        "tone": tone_key
    }
    return merged


def _background_fill_worldview_details(cache_key: str, user_idea: str, protagonist_attr: Dict, difficulty: str, tone_key: str):
    """后台补全世界观细节"""
    try:
        print("🧵 正在后台补全世界观细节...")
        detailed_state = llm_generate_global(user_idea, protagonist_attr, difficulty, tone_key, force_full=True)
        if detailed_state:
            # 🔑 缓存机制已删除：不再保存缓存
            # _save_worldview_cache(cache_key, detailed_state)
            print("✅ 世界观细节补全完成")
    except Exception as e:
        print(f"⚠️ 后台补全世界观失败：{e}")


# ------------------------------
# 文本解析优化（正则回填缺失字段）
# ------------------------------
# 修改正则表达式以支持多行内容，匹配到下一个字段标签之前
# 使用非贪婪匹配，遇到下一个字段标签或章节标题时停止
_REGEX_GAME_STYLE = re.compile(r"游戏风格[：:]\s*(.+?)(?=\n\s*(?:世界观基础设定|主角核心能力|游戏主线任务|游戏结束触发条件|第\d+章|##\s*【|$))", re.UNICODE | re.DOTALL | re.MULTILINE)
_REGEX_WORLD_BASIC = re.compile(r"世界观基础设定[：:]\s*(.+?)(?=\n\s*(?:主角核心能力|游戏主线任务|游戏结束触发条件|游戏风格|第\d+章|##\s*【|$))", re.UNICODE | re.DOTALL | re.MULTILINE)
_REGEX_PROTAGONIST_ABILITY = re.compile(r"主角核心能力[：:]\s*(.+?)(?=\n\s*(?:游戏主线任务|游戏结束触发条件|世界观基础设定|游戏风格|第\d+章|##\s*【|$))", re.UNICODE | re.DOTALL | re.MULTILINE)
_REGEX_MAIN_QUEST = re.compile(r"游戏主线任务[：:]\s*(.+?)(?=\n\s*(?:游戏结束触发条件|世界观基础设定|主角核心能力|游戏风格|第\d+章|##\s*【|$))", re.UNICODE | re.DOTALL | re.MULTILINE)
_REGEX_END_TRIGGER = re.compile(r"游戏结束触发条件[：:]\s*(.+?)(?=\n\s*(?:游戏主线任务|世界观基础设定|主角核心能力|游戏风格|第\d+章|##\s*【|$))", re.UNICODE | re.DOTALL | re.MULTILINE)
_REGEX_CHAPTER = re.compile(r"第(\d+)章[：:]?", re.UNICODE)
_REGEX_CHAPTER_CONFLICT = re.compile(r"(?:- )?核心矛盾[：:]\s*(.+)", re.UNICODE | re.MULTILINE | re.DOTALL)
_REGEX_CHAPTER_END = re.compile(r"(?:- )?矛盾结束条件[：:]\s*(.+)", re.UNICODE | re.MULTILINE | re.DOTALL)


def _regex_fill_worldview(raw_text: str, core_worldview: Dict, chapters: Dict):
    """使用正则回填缺失的核心字段，避免因格式偏差导致解析失败"""
    if not core_worldview.get("game_style"):
        m = _REGEX_GAME_STYLE.search(raw_text)
        if m:
            content = m.group(1).strip()
            # 清理Markdown格式和多余空格
            content = content.replace('**', '').replace('*', '').strip()
            # 合并多行空格
            content = ' '.join(content.split())
            if content:
                core_worldview["game_style"] = content
    if not core_worldview.get("world_basic_setting"):
        m = _REGEX_WORLD_BASIC.search(raw_text)
        if m:
            content = m.group(1).strip()
            content = content.replace('**', '').replace('*', '').strip()
            content = ' '.join(content.split())
            if content:
                core_worldview["world_basic_setting"] = content
    if not core_worldview.get("protagonist_ability"):
        m = _REGEX_PROTAGONIST_ABILITY.search(raw_text)
        if m:
            content = m.group(1).strip()
            content = content.replace('**', '').replace('*', '').strip()
            content = ' '.join(content.split())
            if content:
                core_worldview["protagonist_ability"] = content
    if not core_worldview.get("main_quest"):
        m = _REGEX_MAIN_QUEST.search(raw_text)
        if m:
            content = m.group(1).strip()
            content = content.replace('**', '').replace('*', '').strip()
            content = ' '.join(content.split())
            if content:
                core_worldview["main_quest"] = content
    if not core_worldview.get("end_trigger_condition"):
        m = _REGEX_END_TRIGGER.search(raw_text)
        if m:
            content = m.group(1).strip()
            content = content.replace('**', '').replace('*', '').strip()
            content = ' '.join(content.split())
            if content:
                core_worldview["end_trigger_condition"] = content

    # 回填章节（即使chapters为空字典也要执行，用于创建章节结构）
    if chapters is None:
        chapters = {}
    # 逐章匹配
    chapter_matches = list(_REGEX_CHAPTER.finditer(raw_text))
    if not chapter_matches:
        # 如果没有找到章节，尝试创建默认章节结构
        return
    for idx, match in enumerate(chapter_matches):
        chap_num = match.group(1)
        chap_key = f"chapter{chap_num}"
        start = match.end()
        end = chapter_matches[idx + 1].start() if idx + 1 < len(chapter_matches) else None
        segment = raw_text[start:end]
        # 使用多行模式匹配，支持跨行内容（注意：已编译的正则对象search方法不接受flags参数）
        # 需要在编译时就设置MULTILINE和DOTALL标志
        conflict_match = _REGEX_CHAPTER_CONFLICT.search(segment or "")
        end_cond_match = _REGEX_CHAPTER_END.search(segment or "")
        chap = chapters.setdefault(chap_key, {})
        if conflict_match and not chap.get("main_conflict"):
            conflict_text = conflict_match.group(1).strip()
            # 清理可能的换行和多余空格
            conflict_text = ' '.join(conflict_text.split())
            chap["main_conflict"] = conflict_text
        if end_cond_match and not chap.get("conflict_end_condition"):
            end_cond_text = end_cond_match.group(1).strip()
            # 清理可能的换行和多余空格
            end_cond_text = ' '.join(end_cond_text.split())
            chap["conflict_end_condition"] = end_cond_text

# ------------------------------
# 新增：通用API请求函数（带自动重试）
# ------------------------------
@retry(
    stop=stop_after_attempt(15),  # 重试上限保持不变，保证兼容
    wait=wait_exponential(multiplier=1, min=5, max=30),  # 等待时间：5s → 10s → 20s → 30s → 30s...
    retry=(
        retry_if_exception_type(requests.exceptions.ConnectionError) |  # 网络连接错误重试
        retry_if_exception_type(requests.exceptions.Timeout)            # 超时错误重试
        # 注意：HTTPError不在这里重试，我们在函数内部处理
    ),
    reraise=True  # 最终失败后抛出原异常，方便上层处理
)
def call_ai_api(request_body: Dict) -> Dict:
    """
    调用AI API的通用函数，带自动重试（401/403错误不重试）
    变更：移除全局共享的重试计数，改用固定超时以避免多线程下状态污染。
    """
    # 安全获取API配置
    api_key = AI_API_CONFIG.get('api_key', '')
    base_url = AI_API_CONFIG.get('base_url', '')
    
    # 验证API配置
    if not api_key:
        raise ValueError("API密钥未配置，请在.env文件中设置Camera_Analyst_API_KEY")
    if not base_url:
        raise ValueError("API基础URL未配置，请在.env文件中设置Camera_Analyst_BASE_URL")
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json; charset=utf-8"
    }
    
    try:
        # 固定超时，避免跨线程共享计数导致超时失控
        timeout = 180
        
        # 处理流式开关：当前API若不支持流式，则退化为普通请求
        stream_flag = False
        if request_body.get("stream"):
            stream_flag = True
            request_body = dict(request_body)
            request_body.pop("stream", None)
            print("ℹ️ Stream模式暂不直接支持，已自动降级为普通请求")
        
        print(f"📡 发送API请求... (超时时间: {timeout}秒)")
        response = requests.post(
            url=f"{base_url}/chat/completions",
            headers=headers,
            json=request_body,
            timeout=timeout
        )
        response.raise_for_status()  # 抛出HTTP错误
        print("✅ API请求成功")
        return response.json()
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response else 0
        
        # 401/403是认证错误，不应该重试
        if status_code in [401, 403]:
            print(f"❌ API认证失败（HTTP {status_code}），请检查API密钥和权限配置")
            print(f"   当前API配置：")
            print(f"   - API基础URL: {base_url}")
            print(f"   - API密钥: {'已配置' if api_key else '未配置'} (长度: {len(api_key) if api_key else 0})")
            print(f"   - 请求URL: {base_url}/chat/completions")
            print(f"   提示：请确认.env文件中的Camera_Analyst_API_KEY是否正确")
            print(f"   提示：请确认API密钥是否有访问该端点的权限")
            print(f"   提示：请确认API基础URL（Camera_Analyst_BASE_URL）是否正确，应该是完整的URL，如：https://api.example.com/v1")
            print(f"   提示：如果使用yunwu.ai，请确认API密钥格式和权限是否正确")
            
            # 检查URL格式
            if base_url and not base_url.startswith(('http://', 'https://')):
                print(f"   ⚠️ 警告：API基础URL格式可能不正确，应该以http://或https://开头")
            
            # 创建一个自定义异常，包含更多信息
            error_msg = f"API认证失败（HTTP {status_code}）。请检查：1) .env文件中的Camera_Analyst_API_KEY是否正确 2) API密钥是否有权限 3) Camera_Analyst_BASE_URL格式是否正确（应该是完整URL）"
            raise ValueError(error_msg) from e
        
        # 其他HTTP错误（如500、502、503等）可以重试，但不在装饰器中重试
        # 这里直接抛出，让上层处理
        print(f"⚠️ API请求失败（HTTP错误 {status_code}），错误信息：{str(e)[:100]}")
        raise
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        # 这些错误会被装饰器自动重试
        print(f"⚠️ API请求失败（网络/超时），将自动重试：{str(e)[:100]}")
        raise
    except Exception as e:
        print(f"⚠️ API请求失败（未知错误）：{str(e)[:100]}")
        raise

# ------------------------------
# 新增JSON容错提取函数（核心修复）
# ------------------------------
def extract_and_validate_json(raw_text: str) -> str:
    """
    从原始文本中提取JSON内容并做基础验证
    处理场景：AI返回内容包含多余文字、代码块标记、格式错误等
    """
    if not raw_text:
        return ""
    
    # 1. 找到JSON的开始位置（{或[），忽略所有在这之前的内容
    # 1.1 查找第一个{或[
    first_brace = raw_text.find('{')
    first_bracket = raw_text.find('[')
    
    # 确定JSON的开始位置
    if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
        # JSON以{开头
        start_idx = first_brace
    elif first_bracket != -1:
        # JSON以[开头
        start_idx = first_bracket
    else:
        # 无法找到JSON的开始位置，返回空字符串
        return ""
    
    # 1.2 只保留从JSON开始位置到结束的内容
    cleaned_text = raw_text[start_idx:]
    
    # 2. 找到JSON的结束位置
    if cleaned_text.startswith('{'):
        # JSON以{开头，查找匹配的}
        brace_count = 1
        end_idx = 1
        for i, char in enumerate(cleaned_text[1:], start=1):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    end_idx = i + 1  # +1 因为切片是左闭右开
                    break
    else:
        # JSON以[开头，查找匹配的]
        bracket_count = 1
        end_idx = 1
        for i, char in enumerate(cleaned_text[1:], start=1):
            if char == '[':
                bracket_count += 1
            elif char == ']':
                bracket_count -= 1
                if bracket_count == 0:
                    end_idx = i + 1  # +1 因为切片是左闭右开
                    break
    
    # 3. 提取完整的JSON字符串
    json_str = cleaned_text[:end_idx]
    
    # 4. 基础清理：移除多余的空格和换行符
    json_str = json_str.strip()
    
    # 5. 处理内容截断的情况（移除末尾的省略号）
    json_str = json_str.replace("...", "")
    
    # 6. 移除末尾可能的多余字符（如逗号、分号、句号等）
    while json_str and json_str[-1] in [',', ';', '.', ' ', '\n', '\t', '"', "'"]:
        json_str = json_str[:-1]
    
    # 7. 基础验证：替换中文标点为英文（常见错误）
    json_str = json_str.replace("：", ":").replace("，", ",").replace("“", '"').replace("”", '"')
    
    # 8. 修复常见的JSON格式问题
    # 修复缺少引号的键名
    json_str = re.sub(r'(?<=[{,\s])\s*([a-zA-Z0-9_\u4e00-\u9fa5]+)\s*:', r' "\1":', json_str)
    
    # 9. 修复单引号问题
    # 将所有单引号替换为双引号
    json_str = json_str.replace("'", '"')
    
    # 10. 修复布尔值和空值问题
    json_str = json_str.replace('True', 'true').replace('False', 'false').replace('None', 'null')
    
    # 11. 移除多余的转义字符
    json_str = re.sub(r'\\"', '"', json_str)
    
    # 12. 修复字符串内部的换行符和制表符
    json_str = json_str.replace('\n', '\\n').replace('\t', '\\t')
    
    # 13. 尝试直接解析JSON，如果成功就直接返回
    try:
        json.loads(json_str)
        return json_str
    except json.JSONDecodeError:
        # 如果解析失败，尝试更简单的修复：移除所有空格和换行符
        simple_json = json_str.replace(' ', '').replace('\n', '').replace('\t', '')
        
        # 再次尝试解析
        try:
            json.loads(simple_json)
            return simple_json
        except json.JSONDecodeError:
            # 如果仍然失败，返回原始提取结果
            return json_str
    
    return json_str

# ------------------------------
# LLM提示词优化函数（用于图片生成）
# ------------------------------
def optimize_image_prompt_with_llm(
    scene_description: str,
    global_state: Dict,
    image_style: Dict = None,
    protagonist_reference_images: List[str] = None,
    supporting_role_references: List[Dict] = None,
    available_supporting_roles_for_tagging: List[Dict] = None
) -> str:
    """
    使用LLM（deepseek-v3.2）优化图片生成提示词
    :param scene_description: 当前剧情文本
    :param global_state: 全局状态（包含主角属性、游戏主题、游戏基调等）
    :param image_style: 图片风格选择
    :param protagonist_reference_images: 主角三视图路径列表 [正面, 侧面, 背面]，可选
    :param supporting_role_references: 配角参考图列表 [{"role_name","image_index","core_features","first_appear_scene"}, ...]，可选；为 None 时由代码后续拼接「参考 Image N」
    :param available_supporting_roles_for_tagging: 本局配角列表 [{"role_key","shallow_background"}, ...]，用于 LLM 在描述中使用「名称-配角N」格式
    :return: 优化后的视觉描述提示词
    """
    try:
        # ------------------------------
        # 视觉连续性上下文（新功能）：
        # - 同一场景统一风格/物件
        # - 下一剧情图片参考上一剧情图片（至少在提示词层面；SD可走img2img）
        #
        # 上游可在 global_state['_visual_context'] 注入（可选）：
        # - previousSceneImage / currentSceneImage: {url, prompt, ...}
        # - previous_image_url / previous_image_prompt（拆分字段）
        # - previousSceneText / currentSceneText
        # - sceneId
        # ------------------------------
        visual_context = global_state.get('_visual_context') if isinstance(global_state, dict) else None
        if not isinstance(visual_context, dict):
            visual_context = {}

        prev_img_obj = visual_context.get('previousSceneImage') or visual_context.get('currentSceneImage') or {}
        if not isinstance(prev_img_obj, dict):
            prev_img_obj = {}

        previous_image_prompt = (
            visual_context.get('previous_image_prompt')
            or prev_img_obj.get('prompt')
            or prev_img_obj.get('optimized_prompt')
            or ""
        )
        previous_image_url = (
            visual_context.get('previous_image_url')
            or prev_img_obj.get('url')
            or prev_img_obj.get('image_url')
            or ""
        )
        previous_scene_text = (
            visual_context.get('previousSceneText')
            or visual_context.get('currentSceneText')
            or ""
        )
        scene_id_for_lock = visual_context.get('sceneId') or ""

        continuity_requirements = ""
        if previous_image_prompt or previous_scene_text or previous_image_url or scene_id_for_lock:
            continuity_requirements = f"""【连续性/一致性要求（重要）】
1) 同一场景保持统一画风与物件：角色外观（发型、脸部特征、服装配色/材质）、关键道具/武器/饰品、环境主色调与光线风格要前后一致。
2) 下一剧情的图片需要延续上一剧情的“画面设定”：尽量沿用上一张图的镜头语言、色彩、角色造型与关键物件，不要无故更换造型/服装/装备。
3) 最终提示词中不要包含URL/文件路径/任何可被当作文字的字符串（例如 http://...），避免图片里出现文字。

上一剧情文本（可选）：
{previous_scene_text[:800] if previous_scene_text else '（无）'}

上一张图的提示词（可选，作为画面设定参照）：
{previous_image_prompt[:1200] if previous_image_prompt else '（无）'}
"""

        # 提取游戏背景信息
        core_worldview = global_state.get('core_worldview', {})
        # game_style 往往是“风格描述”，不一定是用户输入主题；检索/还原时优先用 user_theme
        user_theme = _safe_str(global_state.get("user_theme")).strip()
        game_theme = core_worldview.get('game_style', '')
        world_setting = core_worldview.get('world_basic_setting', '')
        protagonist_ability = core_worldview.get('protagonist_ability', '')
        
        # 提取主角信息
        protagonist_info = {}
        if 'characters' in core_worldview and '主角' in core_worldview['characters']:
            protagonist = core_worldview['characters']['主角']
            protagonist_info = {
                'personality': protagonist.get('core_personality', ''),
                'appearance': protagonist.get('shallow_background', '')
            }
        
        # 提取游戏基调
        game_tone = global_state.get('tone', 'normal_ending')
        tone_map = {
            'happy_ending': '圆满结局，积极乐观',
            'bad_ending': '悲剧结局，沉重悲伤',
            'normal_ending': '普通结局，真实平淡',
            'dark_depressing': '黑深残，黑暗压抑',
            'humorous': '幽默，轻松诙谐',
            'abstract': '抽象，象征隐喻',
            'aesthetic': '唯美，优美细腻',
            'logical': '逻辑推理严谨',
            'mysterious': '神秘，悬念丛生',
            'stream_of_consciousness': '意识流，内心描写'
        }
        tone_description = tone_map.get(game_tone, '普通结局')
        
        # 提取图片风格信息
        style_description = ''
        if image_style:
            style_type = image_style.get('type', '')
            if style_type == 'realistic':
                style_description = '写实风格，真实细腻，细节丰富'
            elif style_type == 'anime':
                style_description = '动漫风格，日式动画风格，色彩鲜明'
            elif style_type == 'ink_painting':
                style_description = '水墨画风格，中国传统水墨画，黑白灰调，意境深远'
            elif style_type == 'oil_painting':
                subtype = image_style.get('subtype', 'classic_oil')
                if subtype == 'impressionist':
                    style_description = '印象派油画风格，光影变化丰富，笔触明显'
                elif subtype == 'rococo':
                    style_description = '洛可可风格油画，华丽精致，装饰性强'
                else:
                    style_description = '经典油画风格，厚重质感，色彩丰富'
            elif style_type == 'cyberpunk':
                style_description = '赛博朋克风格，未来科技感，霓虹灯效果，高对比度'
            elif style_type == 'custom':
                style_description = f"自定义风格：{image_style.get('value', '')}"
        
        # 构建主角参考图说明（1张=正面，2张=正+侧，3张=正+侧+背；第一次场景图可能只有正面）
        protagonist_reference_section = ""
        if protagonist_reference_images and len(protagonist_reference_images) >= 1:
            n = len(protagonist_reference_images)
            lines = ["【主角参考图说明（重要）】", f"生图API将接收{n}张主角参考图，编号从 Image 0 起："]
            lines.append("- Image 0：主角正面视图（Front view portrait of the protagonist）")
            if n >= 2:
                lines.append("- Image 1：主角侧面视图（Side view portrait of the protagonist）")
            if n >= 3:
                lines.append("- Image 2：主角背面视图（Back view portrait of the protagonist）")
            lines.append("")
            lines.append("在生成场景图片时，根据剧情中主角的视角明确说明主角使用哪张参考图（仅使用已提供的编号）：")
            lines.append("- 正面朝向镜头 → 主角使用 Image 0")
            if n >= 2:
                lines.append("- 侧面朝向镜头 → 主角使用 Image 1")
            if n >= 3:
                lines.append("- 背面朝向镜头 → 主角使用 Image 2")
            if n >= 2:
                lines.append("- 其他角度可写「主角主要参考 Image 0 和 Image 1」等")
            lines.append("")
            lines.append("请在最终视觉描述中明确说明主角使用哪张参考图，确保主角形象与参考图一致。")
            protagonist_reference_section = "\n".join(lines) + "\n"

        # 构建配角参考图说明（有参考图时说明 Image N；无参考图时仅说明「名称-配角N」标注格式）
        supporting_role_reference_section = ""
        if supporting_role_references and len(supporting_role_references) >= 1:
            lines_sr = ["【配角参考图说明（重要）】", "生图API将接收以下配角参考图（编号续接主角之后，均为初登场场景图，可能含多人）："]
            for sr in supporting_role_references:
                role_name = _safe_str(sr.get("role_name", "")).strip()
                img_idx = sr.get("image_index", 0)
                core_feat = _safe_str(sr.get("core_features", "")).strip()
                first_scene = _safe_str(sr.get("first_appear_scene", "")).strip()
                if not role_name:
                    continue
                desc = f"- Image {img_idx}：{role_name}"
                if first_scene:
                    desc += f"，首次出场于「{first_scene}」"
                if core_feat:
                    desc += "，核心特征（不可修改）：" + (core_feat[:120] + "…" if len(core_feat) > 120 else core_feat)
                else:
                    desc += "，保持五官核心特征不变"
                lines_sr.append(desc)
            lines_sr.append("")
            lines_sr.append("在生成场景图片时：")
            lines_sr.append("1. 根据剧情明确每个配角使用哪张参考图（仅使用已提供的编号）")
            lines_sr.append("2. 参考图为场景图（含多人）时，**必须明确写出该配角对应图中哪个人物**，例如：以图中从左到右第二个人物的形象为准、以图中右侧持剑的少年为准")
            lines_sr.append("3. 必须在描述中写明「XXX 参考 Image N，以图中XX位置/特征的人物为准，保持核心特征不变」")
            lines_sr.append("4. 可变化：服饰细节、动作、表情、所处位置")
            lines_sr.append("5. 不可变化：五官、发型、肤色、体型等核心特征")
            supporting_role_reference_section = "\n".join(lines_sr) + "\n"
        elif available_supporting_roles_for_tagging and len(available_supporting_roles_for_tagging) >= 1:
            # 无配角参考图时：要求 LLM 用「名称-配角N」标注；名称从剧情文本得出；「参考 Image N」由代码后续拼接
            lines_tag = ["【配角标注要求（重要）】"]
            has_existing = any(
                _safe_str(item.get("role_key", "")).strip() == "已有角色"
                for item in available_supporting_roles_for_tagging
            )
            if has_existing:
                lines_tag.append("已建档的配角（再次出场时请使用相同名称或别号）：")
                for item in available_supporting_roles_for_tagging:
                    if _safe_str(item.get("role_key", "")).strip() == "已有角色":
                        names = _safe_str(item.get("names_or_aliases", "")).strip()
                        rn = _safe_str(item.get("role_name", "")).strip()
                        if names or rn:
                            lines_tag.append(f"  - {names or rn}（请使用其名或别号+配角N格式，如 凌川-配角1）")
                lines_tag.append("")
            lines_tag.append("新出场的配角：分析【当前剧情】中是否有**新登场**的非主角人物，若有则用「角色名-配角N」格式标注，角色名必须从剧情文本中得出（如黑衣人-配角1、老者-配角2）。")
            lines_tag.append("只对剧情中实际出场且非主角的配角使用该格式；未出场者不要写。不要写「参考 Image N」，由系统后续自动添加。")
            supporting_role_reference_section = "\n".join(lines_tag) + "\n"
        
        # 主角规范信息（性别/年龄/外貌，与主角形象生成一致）
        protagonist_canonical_block = _format_protagonist_canonical_for_prompt(
            global_state.get("protagonist_canonical") or {}
        )
        canonical = global_state.get("protagonist_canonical") or {}
        protagonist_name = _safe_str(canonical.get("name_zh") or canonical.get("name_en") or "").strip()
        protagonist_identity_warning = f"\n【重要】主角身份：{protagonist_name or '玩家视角主角'}（上述主角规范信息描述的人）。**切勿将主角标注为配角**，只对剧情中出场的**非主角**人物使用「名称-配角N」格式。\n"
        
        # 构建发送给LLM的提示词
        llm_prompt = f"""假设你是一个专业的剧情分析师和视觉设计师，现在需要你将剧情转化为具体的视觉描述，告诉生图AI如何生成图片。

【游戏背景信息】
- 游戏主题：{game_theme}
- 世界观设定：{world_setting}
- 游戏基调：{tone_description}

【主角信息】
- 主角能力：{protagonist_ability}
- 主角性格：{protagonist_info.get('personality', '')}
- 主角外貌特征：{protagonist_info.get('appearance', '')}

【主角规范信息】（描写主角性别/年龄/外貌时必须严格遵循，与主角立绘一致）
{protagonist_canonical_block}
{protagonist_identity_warning}
【当前剧情】（请据此分析是否有新登场配角，并用「名称-配角N」标注）
{scene_description}

【图片风格要求】
{style_description if style_description else '默认风格'}

{protagonist_reference_section if protagonist_reference_section else ''}
{supporting_role_reference_section if supporting_role_reference_section else ''}

{continuity_requirements if continuity_requirements else ''}

请根据以上信息，生成一个详细的视觉描述提示词，要求：
1. 准确反映当前剧情场景
2. 体现主角的外貌特征和能力特点；若有【主角规范信息】，描写主角时必须严格遵循其性别、年龄感与标志性外观关键词
3. 符合游戏主题和世界观设定
4. 匹配游戏基调（如悲剧基调应体现沉重氛围）
5. 符合指定的图片风格
6. 不要包含任何文字、符号、乱码（重要：必须在提示词中明确告诉生图AI不要生成任何文字、符号、乱码）
7. 描述要具体、生动，包含场景、人物、光线、氛围等细节
{('8. 如果提供了主角参考图说明，必须在提示词中明确说明主角使用 Image 0/1/2 中的哪张（根据主角在场景中的视角）' if protagonist_reference_section else '')}
{('9. 如果提供了配角参考图说明，必须在提示词中明确说明每个配角参考 Image N，并强调保持其核心特征不变' if (supporting_role_references and len(supporting_role_references) >= 1) else '')}
{('9. 如果提供了配角标注要求，必须在视觉描述中对出场的配角使用「角色名-配角N」格式（如凌川-配角1），便于系统识别' if (available_supporting_roles_for_tagging and len(available_supporting_roles_for_tagging) >= 1 and not (supporting_role_references and len(supporting_role_references) >= 1)) else '')}

只输出视觉描述，不要输出其他内容。"""

        # 调用LLM API（使用deepseek-v3.2模型）
        api_key = AI_API_CONFIG.get('api_key', '')
        base_url = AI_API_CONFIG.get('base_url', '')
        
        if not api_key or not base_url:
            print("⚠️ LLM API未配置，使用原始提示词")
            return f"{game_theme}, {scene_description[:500]}, cinematic, detailed, high quality, 4k, dramatic lighting, atmospheric"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8"
        }
        
        request_body = {
            "model": "deepseek-v3.2",  # 使用deepseek-v3.2模型
            "messages": [
                {
                    "role": "user",
                    "content": llm_prompt
                }
            ],
            "temperature": 0.7,
            "max_tokens": 2000
        }
        
        print("🔄 正在使用LLM优化图片生成提示词...")
        response = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=request_body,
            timeout=120
        )
        response.raise_for_status()
        
        result = response.json()
        choices = result.get("choices", [])
        if choices and len(choices) > 0:
            optimized_prompt = choices[0].get("message", {}).get("content", "").strip()
            if optimized_prompt:
                # 清理：避免把URL/路径等带入最终提示词（否则容易生成“文字”）
                optimized_prompt = re.sub(r'https?://\S+', '', optimized_prompt).strip()
                optimized_prompt = re.sub(r'data:image/\S+', '', optimized_prompt).strip()
                optimized_prompt = re.sub(r'[/\\]image_cache[/\\]\S+', '', optimized_prompt).strip()
                # 在优化后的提示词末尾添加禁止文字乱码的明确指令
                optimized_prompt = f"{optimized_prompt}, no text, no symbols, no garbled characters, no words"
                # 强制连续性补丁（即使LLM未显式保留，也尽量保持一致性）
                if continuity_requirements:
                    optimized_prompt = f"{optimized_prompt}, consistent character design, consistent outfit and key props, consistent color palette and lighting"
                print(f"✅ LLM提示词优化完成，长度：{len(optimized_prompt)}字符")
                return optimized_prompt
        
        # 如果LLM调用失败，使用原始提示词
        print("⚠️ LLM优化失败，使用原始提示词")
        return f"{game_theme}, {scene_description[:500]}, cinematic, detailed, high quality, 4k, dramatic lighting, atmospheric"
        
    except Exception as e:
        print(f"⚠️ LLM提示词优化出错：{str(e)}，使用原始提示词")
        # 出错时使用原始提示词
        core_worldview = global_state.get('core_worldview', {})
        game_style = core_worldview.get('game_style', '')
        scene_summary = scene_description[:500] if len(scene_description) > 500 else scene_description
        return f"{game_style}, {scene_summary}, cinematic, detailed, high quality, 4k, dramatic lighting, atmospheric"

# ------------------------------
# 主角形象生成函数
# ------------------------------
import time
import random
from pathlib import Path

# ------------------------------
# 主角三视图（白底全身）prompt 模板
# ------------------------------
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

def generate_game_id() -> str:
    """
    生成游戏ID（时间戳+随机数）
    :return: 游戏ID，格式：game_{timestamp}_{random}
    """
    timestamp = int(time.time())
    random_str = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=6))
    return f"game_{timestamp}_{random_str}"

def ensure_main_character_dir(game_id: str) -> Path:
    """
    确保主角形象目录存在
    :param game_id: 游戏ID
    :return: 目录路径
    """
    main_character_dir = Path("initial") / "main_character" / game_id
    main_character_dir.mkdir(parents=True, exist_ok=True)
    return main_character_dir

# ------------------------------
# 配角档案与图片依赖生成
# ------------------------------
SUPPORTING_ROLE_ARCHIVES_FILE = "role_archives.json"

def ensure_character_references_dir(game_id: str) -> Path:
    """确保配角参考图目录存在"""
    ref_dir = Path("initial") / "character_references" / game_id
    ref_dir.mkdir(parents=True, exist_ok=True)
    return ref_dir

def _load_role_archives(game_id: str) -> Dict:
    """加载配角档案。兼容旧格式（key=配角1）与新格式（key=role_001）"""
    ref_dir = ensure_character_references_dir(game_id)
    archive_path = ref_dir / SUPPORTING_ROLE_ARCHIVES_FILE
    if archive_path.exists():
        try:
            with open(archive_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            print(f"⚠️ 加载配角档案失败：{e}")
            return {}
        result = {}
        for i, (k, v) in enumerate(raw.items()):
            if not isinstance(v, dict):
                continue
            role_id = v.get("role_id") or (f"role_{i+1:03d}" if not re.match(r"^role_\d+$", str(k)) else str(k))
            v = dict(v)
            v["role_id"] = role_id
            if "aliases" not in v:
                rn = _safe_str(v.get("role_name", "")).strip()
                v["aliases"] = [rn] if rn else []
            result[role_id] = v
        return result
    return {}

def _save_role_archives(game_id: str, archives: Dict) -> None:
    """保存配角档案"""
    ref_dir = ensure_character_references_dir(game_id)
    archive_path = ref_dir / SUPPORTING_ROLE_ARCHIVES_FILE
    try:
        with open(archive_path, "w", encoding="utf-8") as f:
            json.dump(archives, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 保存配角档案失败：{e}")

def _next_role_id(archives: Dict) -> str:
    """生成下一个角色ID：role_001, role_002... 唯一标识，避免重名"""
    max_num = 0
    for _key, data in archives.items():
        rid = _safe_str(data.get("role_id", "")).strip()
        m = re.match(r"role_(\d+)", rid)
        if m:
            max_num = max(max_num, int(m.group(1)))
    return f"role_{max_num + 1:03d}"


def _find_archive_by_name_or_alias(archives: Dict, display_name: str) -> Optional[Tuple[str, Dict]]:
    """
    根据角色名或别号在档案中查找。
    :return: (role_id, archive) 或 None
    """
    dn = _safe_str(display_name).strip()
    if not dn:
        return None
    for role_id, arch in archives.items():
        if not isinstance(arch, dict):
            continue
        rn = _safe_str(arch.get("role_name", "")).strip()
        if rn == dn:
            return (role_id, arch)
        aliases = arch.get("aliases", [])
        if isinstance(aliases, list) and dn in aliases:
            return (role_id, arch)
    return None

def _sanitize_filename_for_role(s: str) -> str:
    """将角色名转为可安全用于文件名的前缀（去掉 / \\ : * ? \" < > | 等非法字符）"""
    s = _safe_str(s).strip()
    s = re.sub(r'[\s/\\:*?"<>|]+', '_', s)
    return s.strip('_') or "role"

def _next_img_id(ref_dir: Path) -> str:
    """生成首次出场图片ID：img_{YYYYMMDD}_{序号}"""
    date_str = datetime.now().strftime("%Y%m%d")
    prefix = f"img_{date_str}_"
    max_num = 0
    for p in ref_dir.glob(f"{prefix}*.png"):
        name = p.stem
        try:
            n = int(name[len(prefix):])
            max_num = max(max_num, n)
        except (ValueError, IndexError):
            pass
    # 同时考虑「角色名_img_日期_序号」格式的已有文件，避免同日多角色序号冲突
    for p in ref_dir.glob("*_" + prefix + "*.png"):
        stem = p.stem
        idx = stem.rfind("_" + prefix)
        if idx != -1:
            try:
                n = int(stem[idx + len(prefix) + 1:])
                max_num = max(max_num, n)
            except (ValueError, IndexError):
                pass
    return f"{prefix}{max_num + 1:03d}"

def extract_supporting_characters_in_scene(optimized_prompt: str) -> List[str]:
    """
    从优化后的视觉描述提示词中提取出场的配角槽位列表（仅依据 prompt 中是否出现「配角N」）
    :return: 出场配角槽位列表，按编号排序，如 ["配角1", "配角2"]
    """
    text = _safe_str(optimized_prompt).strip()
    if not text:
        return []
    matches = re.findall(r"配角\d+", text)
    seen = set()
    result = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            result.append(m)
    def sort_key(s):
        n = re.search(r"\d+", s)
        return int(n.group()) if n else 0
    result.sort(key=sort_key)
    return result


def extract_supporting_characters_with_names(optimized_prompt: str) -> List[Tuple[str, str]]:
    """
    从优化后的视觉描述提示词中提取出场的配角及角色名。
    :param optimized_prompt: 优化后的视觉描述提示词
    :return: [(display_name, slot), ...]，如 [("凌川", "配角1"), ("李云", "配角2")]
             display_name 从「名称-配角N」解析，无则用 slot 作为 display_name
    """
    text = _safe_str(optimized_prompt).strip()
    if not text:
        return []
    result = []
    seen_slots = set()
    for m in re.finditer(r"([^\s\-]+)\s*[-－]?\s*(配角\d+)(?:\s|$|，|。|、|参考|以|保持)", text):
        name, slot = m.group(1).strip(), m.group(2)
        if slot in seen_slots:
            continue
        seen_slots.add(slot)
        display_name = name if name and not re.match(r"^配角\d+$", name) else slot
        result.append((display_name, slot))
    if not result:
        for slot in extract_supporting_characters_in_scene(text):
            result.append((slot, slot))
    def sort_key(item):
        n = re.search(r"\d+", item[1])
        return int(n.group()) if n else 0
    result.sort(key=sort_key)
    return result

def get_or_create_supporting_role_archive(
    game_id: str,
    display_name: str,
    slot: str,
    role_info: Dict,
    first_appear_scene: str,
) -> Dict:
    """
    获取或返回配角档案。若已有档案（按 role_name / aliases 匹配）则返回；否则返回待建档标记。
    初登场图 = 当前剧情图，在剧情图生成成功后由外部调用 archive_supporting_role_first_appearance 保存。
    :return: 若已有档案：含 first_img_path, core_features 等；若首次出场：含 _pending_first_appearance=True, display_name, slot
    """
    archives = _load_role_archives(game_id)
    found = _find_archive_by_name_or_alias(archives, display_name)
    if found:
        role_id, arch = found
        first_path = _safe_str(arch.get("first_img_path", "")).strip()
        if first_path:
            p = Path(first_path)
            if not p.is_absolute():
                p = Path("initial") / "character_references" / game_id / Path(first_path).name
            if p.exists():
                arch = dict(arch)
                arch["_resolved_first_img_path"] = str(p.resolve())
                arch["_role_id"] = role_id
                return arch
        print(f"⚠️ 配角 {display_name} 档案存在但首图路径无效")
    return {
        "_pending_first_appearance": True,
        "display_name": display_name,
        "slot": slot,
        "role_info": role_info,
        "first_appear_scene": first_appear_scene,
    }


def archive_supporting_role_first_appearance(
    game_id: str,
    pending_item: Dict,
    scene_image_path: str,
    prompt: str,
) -> Optional[Dict]:
    """
    剧情图生成成功后：将当前剧情图保存为配角的初登场图，并建立档案。
    :param pending_item: get_or_create 返回的待建档对象
    :param scene_image_path: 刚生成的剧情图本地路径（如 image_cache/xxx.png）
    :param prompt: 本次生成使用的提示词（用于 first_prompt）
    :return: 新建的 archive，或 None
    """
    if not pending_item.get("_pending_first_appearance"):
        return None
    display_name = _safe_str(pending_item.get("display_name", "")).strip()
    slot = _safe_str(pending_item.get("slot", "")).strip()
    role_info = pending_item.get("role_info") or {}
    first_appear_scene = _safe_str(pending_item.get("first_appear_scene", "")).strip()

    src = Path(scene_image_path)
    if not src.exists():
        print(f"⚠️ 初登场图源文件不存在：{scene_image_path}")
        return None

    ref_dir = ensure_character_references_dir(game_id)
    archives = _load_role_archives(game_id)
    role_id = _next_role_id(archives)
    first_img_id = _next_img_id(ref_dir)
    role_prefix = _sanitize_filename_for_role(display_name)
    first_img_path = ref_dir / f"{role_prefix}_{first_img_id}.png"

    try:
        import shutil
        shutil.copy2(src, first_img_path)
    except Exception as e:
        print(f"⚠️ 保存配角初登场图失败：{e}")
        return None

    first_prompt = _extract_character_core_from_prompt(prompt, display_name) or _clip_text(prompt, 300)
    core_features = _extract_core_features_from_prompt(first_prompt)

    archive = {
        "role_id": role_id,
        "role_name": display_name,
        "aliases": [display_name],
        "story_background": _safe_str(role_info.get("shallow_background", "")),
        "first_appear_scene": first_appear_scene,
        "first_img_id": first_img_id,
        "first_img_path": str(first_img_path.resolve()),
        "first_prompt": first_prompt,
        "img_model": IMAGE_GENERATION_CONFIG.get("yunwu_model", "gemini-2.5-flash-image"),
        "update_log": [],
        "core_features": core_features,
    }
    archives[role_id] = archive
    _save_role_archives(game_id, archives)
    archive["_resolved_first_img_path"] = str(first_img_path.resolve())
    print(f"✅ 配角 {display_name} 初登场图已保存（来自当前剧情图）：{first_img_path}")
    print(f"   📋 新建配角信息：role_id={role_id}, role_name={display_name}, aliases={archive.get('aliases',[])}, first_img={first_img_path.name}")
    return archive


def _extract_character_core_from_prompt(prompt: str, display_name: str) -> str:
    """从提示词中提取与某角色相关的核心描述（简化：取含该名的句子或附近上下文）"""
    text = _safe_str(prompt).strip()
    name = _safe_str(display_name).strip()
    if not name or name not in text:
        return ""
    sentences = re.split(r'[。！？\n]', text)
    for s in sentences:
        if name in s:
            return _clip_text(s.strip(), 200)
    idx = text.find(name)
    if idx >= 0:
        start = max(0, idx - 50)
        end = min(len(text), idx + 150)
        return _clip_text(text[start:end], 200)
    return ""


def update_supporting_role_aliases_from_plot(game_id: str, scene_description: str) -> None:
    """
    每次剧情更新时：从剧情文本中识别身份揭示（如「黑衣人就是艾玛」「A正是B的妹妹」），
    更新对应配角的 aliases。
    """
    if not game_id or not scene_description or len(scene_description.strip()) < 10:
        return
    archives = _load_role_archives(game_id)
    if not archives:
        return
    api_key = AI_API_CONFIG.get("api_key", "")
    base_url = AI_API_CONFIG.get("base_url", "")
    if not api_key or not base_url:
        return
    existing_aliases = []
    for _rid, arch in archives.items():
        if isinstance(arch, dict):
            for a in (arch.get("aliases") or []):
                if a and a not in existing_aliases:
                    existing_aliases.append(a)
    llm_prompt = f"""从以下剧情中提取「身份揭示」：当剧情明确说明某角色A与另一身份B是同一人时（如「黑衣人就是艾玛」「A原来是B」「A正是B的妹妹」「黑衣人摘下兜帽，竟是灵川」），提取出对应关系。
已知配角称呼：{existing_aliases[:20] if existing_aliases else '（暂无）'}
剧情：
{scene_description[:1500]}

要求：每行输出一条，格式为「原名|新身份」，例如：
黑衣人|艾玛
黑衣人|灵川的妹妹
只输出提取到的身份揭示，无则输出「无」。"""
    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json; charset=utf-8"},
            json={"model": "deepseek-v3.2", "messages": [{"role": "user", "content": llm_prompt}], "temperature": 0.2, "max_tokens": 300},
            timeout=30
        )
        resp.raise_for_status()
        content = (resp.json().get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
        if not content or "无" in content[:10]:
            return
        updated_any = False
        for line in content.split("\n"):
            line = line.strip()
            if "|" not in line or len(line) < 3:
                continue
            parts = line.split("|", 1)
            orig, new_id = parts[0].strip(), parts[1].strip()
            if not orig or not new_id:
                continue
            for role_id, arch in archives.items():
                if not isinstance(arch, dict):
                    continue
                aliases = list(arch.get("aliases") or [])
                if orig in aliases or orig == arch.get("role_name"):
                    if new_id not in aliases:
                        aliases.append(new_id)
                        arch["aliases"] = aliases
                        _save_role_archives(game_id, archives)
                        updated_any = True
                        print(f"📋 配角身份更新：{role_id} ({arch.get('role_name')}) 新增别名「{new_id}」")
                        break
        if updated_any:
            print(f"📋 配角档案已更新（身份揭示）")
    except Exception as e:
        print(f"⚠️ 配角身份更新检查失败：{e}")


def _get_style_description(image_style: Dict) -> str:
    """从 image_style 提取风格描述"""
    if not image_style or not isinstance(image_style, dict):
        return "写实风格，8K，细节丰富"
    t = image_style.get("type", "")
    if t == "realistic":
        return "写实风格，真实细腻，8K"
    if t == "anime":
        return "动漫风格，日式动画，色彩鲜明"
    if t == "ink_painting":
        return "水墨画风格，中国传统水墨"
    if t == "oil_painting":
        return "油画风格，光影丰富，8K"
    if t == "cyberpunk":
        return "赛博朋克风格，未来科技感"
    if t == "custom":
        return image_style.get("value", "写实风格，8K") or "写实风格，8K"
    return "写实风格，8K，细节丰富"

def _extract_core_features_from_prompt(prompt: str) -> str:
    """从首次出场提示词中提取核心特征（简化版：取关键句或截取）"""
    s = _safe_str(prompt).strip()
    if "五官核心特征不可修改" in s:
        s = s.split("五官核心特征不可修改")[0].strip().rstrip("，。")
    return _clip_text(s, 200)

def optimize_main_character_prompt_with_llm(
    protagonist_attr: Dict,
    global_state: Dict,
    image_style: Dict = None
) -> str:
    """
    使用LLM生成主角形象提示词
    :param protagonist_attr: 主角属性（颜值、智商、体力、魅力）
    :param global_state: 全局状态（包含游戏主题、世界观、基调等）
    :param image_style: 图片风格选择
    :return: 优化后的主角形象描述提示词
    """
    try:
        # 提取游戏背景信息
        core_worldview = global_state.get('core_worldview', {})
        # game_style 往往是“风格描述”，不一定等同于用户输入主题名；现实题材检索优先用 user_theme
        user_theme = _safe_str(global_state.get("user_theme")).strip()
        game_theme = core_worldview.get('game_style', '')
        world_setting = core_worldview.get('world_basic_setting', '')
        protagonist_ability = core_worldview.get('protagonist_ability', '')
        
        # 提取主角信息
        protagonist_info = {}
        if 'characters' in core_worldview and '主角' in core_worldview['characters']:
            protagonist = core_worldview['characters']['主角']
            protagonist_info = {
                'personality': protagonist.get('core_personality', ''),
                'appearance': protagonist.get('shallow_background', '')
            }
        
        # 提取游戏基调
        game_tone = global_state.get('tone', 'normal_ending')
        tone_map = {
            'happy_ending': '圆满结局，积极乐观',
            'bad_ending': '悲剧结局，沉重悲伤',
            'normal_ending': '普通结局，真实平淡',
            'dark_depressing': '黑深残，黑暗压抑',
            'humorous': '幽默，轻松诙谐',
            'abstract': '抽象，象征隐喻',
            'aesthetic': '唯美，优美细腻',
            'logical': '逻辑推理严谨',
            'mysterious': '神秘，悬念丛生',
            'stream_of_consciousness': '意识流，内心描写'
        }
        tone_description = tone_map.get(game_tone, '普通结局')
        
        # 提取图片风格信息
        style_description = ''
        if image_style:
            style_type = image_style.get('type', '')
            if style_type == 'realistic':
                style_description = '写实风格，真实细腻，细节丰富'
            elif style_type == 'anime':
                style_description = '动漫风格，日式动画风格，色彩鲜明'
            elif style_type == 'ink_painting':
                style_description = '水墨画风格，中国传统水墨画，黑白灰调，意境深远'
            elif style_type == 'oil_painting':
                subtype = image_style.get('subtype', 'classic_oil')
                if subtype == 'impressionist':
                    style_description = '印象派油画风格，光影变化丰富，笔触明显'
                elif subtype == 'rococo':
                    style_description = '洛可可风格油画，华丽精致，装饰性强'
                else:
                    style_description = '经典油画风格，厚重质感，色彩丰富'
            elif style_type == 'cyberpunk':
                style_description = '赛博朋克风格，未来科技感，霓虹灯效果，高对比度'
            elif style_type == 'custom':
                style_description = f"自定义风格：{image_style.get('value', '')}"
        
        # 构建主角属性描述
        attr_description = f"颜值{protagonist_attr.get('颜值', '普通')}，智商{protagonist_attr.get('智商', '普通')}，体力{protagonist_attr.get('体力', '普通')}，魅力{protagonist_attr.get('魅力', '普通')}"

        # 组织“世界观全文/结构化信息”（让提示词LLM更贴合世界观，而不仅是 basic_setting 一句）
        def _build_worldview_context_text() -> str:
            try:
                parts = []
                if core_worldview.get("game_style"):
                    parts.append(f"游戏主题/风格：{_safe_str(core_worldview.get('game_style'))}")
                if core_worldview.get("world_basic_setting"):
                    parts.append(f"世界观基础设定：{_safe_str(core_worldview.get('world_basic_setting'))}")
                if core_worldview.get("main_quest"):
                    parts.append(f"主线任务：{_safe_str(core_worldview.get('main_quest'))}")
                # 章节矛盾（浓缩）
                chapters = core_worldview.get("chapters", {})
                if isinstance(chapters, dict) and chapters:
                    chap_lines = []
                    for k in ["chapter1", "chapter2", "chapter3"]:
                        c = chapters.get(k, {}) if isinstance(chapters.get(k, {}), dict) else {}
                        mc = _safe_str(c.get("main_conflict")).strip()
                        if mc:
                            chap_lines.append(f"{k} 核心矛盾：{mc}")
                    if chap_lines:
                        parts.append("章节矛盾：\n" + "\n".join(chap_lines))
                # 角色（如果存在）
                chars = core_worldview.get("characters", {})
                if isinstance(chars, dict) and chars.get("主角"):
                    p = chars.get("主角", {})
                    if isinstance(p, dict):
                        cp = _safe_str(p.get("core_personality")).strip()
                        sb = _safe_str(p.get("shallow_background")).strip()
                        db = _safe_str(p.get("deep_background")).strip()
                        if cp:
                            parts.append(f"主角核心性格：{cp}")
                        if sb:
                            parts.append(f"主角浅层背景：{sb}")
                        if db:
                            parts.append(f"主角深层背景：{_clip_text(db, 600)}")
                return _clip_text("\n".join([x for x in parts if _safe_str(x).strip()]).strip(), 1800)
            except Exception:
                return _clip_text(_safe_str(world_setting), 800)

        worldview_context_text = _build_worldview_context_text()

        # 主角规范信息（来自世界观 LLM，不展示给用户）：姓名、性别、作品、外观关键词
        canonical = (global_state.get("protagonist_canonical") or {}) if isinstance(global_state.get("protagonist_canonical"), dict) else {}
        name_zh = _safe_str(canonical.get("name_zh")).strip()
        name_en = _safe_str(canonical.get("name_en")).strip()
        work_zh = _safe_str(canonical.get("work_zh")).strip()
        work_en = _safe_str(canonical.get("work_en")).strip()
        canonical_gender = _safe_str(canonical.get("gender")).strip()
        canonical_signature = _safe_str(canonical.get("signature_look_keywords")).strip()

        # Wikipedia/现实IP 检索：补充证据文本与参考图（不用于“决定主角是谁”，仅用于验证/补充/拿参考图）
        wiki_ctx = {}
        wiki_evidence_text = ""
        reference_image_url = ""
        try:
            wiki_query = user_theme or game_theme
            wiki_ctx = wiki_lookup_theme_and_character(wiki_query)
            if isinstance(wiki_ctx, dict) and wiki_ctx.get("is_real_world"):
                wiki_evidence_text = _safe_str((wiki_ctx or {}).get("evidence_text")).strip()
                reference_image_url = _safe_str((wiki_ctx or {}).get("reference_image_url")).strip()
        except Exception:
            wiki_ctx = {}
            wiki_evidence_text = ""
            reference_image_url = ""

        # 身份与名称：优先使用世界观规范信息，缺失时再退回到维基检索结果
        if not (name_zh or name_en) and isinstance(wiki_ctx, dict):
            theme_names = (wiki_ctx.get("theme_names") or {}) if isinstance(wiki_ctx.get("theme_names"), dict) else {}
            char_names = (wiki_ctx.get("character_names") or {}) if isinstance(wiki_ctx.get("character_names"), dict) else {}
            work_zh = work_zh or _safe_str(theme_names.get("zh")).strip()
            work_en = work_en or _safe_str(theme_names.get("en")).strip()
            name_zh = name_zh or _safe_str(char_names.get("zh")).strip()
            name_en = name_en or _safe_str(char_names.get("en")).strip()
            if not (name_zh or name_en):
                name_zh = work_zh
                name_en = work_en

        required_name_tokens: List[str] = []
        for t in [name_zh, name_en, work_zh, work_en]:
            t = _safe_str(t).strip()
            if t and t not in required_name_tokens:
                required_name_tokens.append(t)

        _name_part = "/".join([x for x in [name_zh, name_en] if _safe_str(x).strip()]).strip()
        _work_part = "/".join([x for x in [work_zh, work_en] if _safe_str(x).strip()]).strip()
        if _name_part and _work_part:
            identity_hint = f"{_name_part} from {_work_part}"
        else:
            identity_hint = _name_part or _work_part or ""

        # 将参考图写入 global_state，供主角生图阶段使用（有参考图时优先传给生图模型）
        if isinstance(global_state, dict) and reference_image_url:
            global_state["_main_character_ref_image_url"] = reference_image_url
        if isinstance(global_state, dict):
            global_state["_main_character_required_name_tokens"] = required_name_tokens

        # 主角性别：优先世界观规范信息，其次主角角色描述推断，再次维基摘要推断，最后随机
        protagonist_gender = ""
        if canonical_gender and ("男" in canonical_gender or "女" in canonical_gender):
            protagonist_gender = "男性" if "男" in canonical_gender else "女性"
        if not protagonist_gender:
            char_text = " ".join([
                protagonist_info.get("personality", ""),
                protagonist_info.get("appearance", ""),
                _safe_str(core_worldview.get("characters", {}).get("主角", {}).get("deep_background", ""))
            ])
            if char_text.strip():
                protagonist_gender = _infer_gender_from_text(char_text)
        if not protagonist_gender:
            try:
                if wiki_evidence_text:
                    protagonist_gender = _infer_gender_from_text(wiki_evidence_text)
            except Exception:
                pass
        if not protagonist_gender:
            import random
            protagonist_gender = random.choice(['男性', '女性'])

        # 主角规范信息块（给提示词 LLM）
        canonical_block_lines = []
        if name_zh or name_en:
            canonical_block_lines.append(f"主角姓名(中/英)：{name_zh or '—'} / {name_en or '—'}")
        if work_zh or work_en:
            canonical_block_lines.append(f"所属作品(中/英)：{work_zh or '—'} / {work_en or '—'}")
        if protagonist_gender:
            canonical_block_lines.append(f"性别：{protagonist_gender}")
        if canonical_signature:
            canonical_block_lines.append(f"标志性外观关键词：{canonical_signature}")
        canonical_block = "\n".join(canonical_block_lines) if canonical_block_lines else "（无）"
        
        # 构建发送给LLM的提示词
        llm_prompt = f"""你现在是一个专业的角色设计师，要将具体角色描述给生图ai，让生图ai能够生成准确的主角形象。

【游戏背景信息】
- 游戏主题：{user_theme or game_theme}
- 世界观设定（结构化/节选）：{worldview_context_text}
- 游戏基调：{tone_description}

【主角规范信息】（来自世界观，必须优先使用；姓名、性别、外观关键词须在最终提示词中体现）
{canonical_block}

【Wikipedia 检索补充】（如存在，可补充细节与参考图；有参考图时会传给生图模型）
{wiki_evidence_text if wiki_evidence_text else "（无）"}

【必须保留的名称标识】（必须在最终提示词中原样保留）
{(" / ".join(required_name_tokens)) if required_name_tokens else "（无）"}

【身份提示】（请在最终提示词中显式出现，保持原样）
{identity_hint if identity_hint else "（无）"}

【主角信息】
- 主角性别：{protagonist_gender}
- 主角属性：{attr_description}
- 主角能力：{protagonist_ability}
- 主角性格：{protagonist_info.get('personality', '')}
- 主角背景：{protagonist_info.get('appearance', '')}

【图片风格要求】
{style_description if style_description else '默认风格'}

请根据以上信息，生成一个详细的主角形象描述提示词，要求：
1. 必须优先使用【主角规范信息】中的姓名、性别与标志性外观关键词；若【Wikipedia 检索补充】存在，可补充细节；若有参考图，生图阶段会使用参考图以提高还原度。
2. 主角性别为{protagonist_gender}，请根据性别特征进行描述。
3. 详细描述主角的外貌特征（面部特征、五官、肤色、表情等），并融入【主角规范信息】中的标志性外观关键词（若有）。
4. 若【必须保留的名称标识】不为“（无）”，最终提示词中必须包含这些名称（原样保留，不要用同义词替换）。
5. 详细描述主角的穿着与发型；体现主角属性特点；符合游戏主题、世界观与基调；符合指定图片风格。
6. 强调全身角色设计（full-body），纯白背景，人物居中站立；禁止生成任何文字/符号/乱码（no text, no symbols, no words）。

只输出视觉描述，不要输出其他内容。"""

        # 调用LLM API（使用deepseek-v3.2模型）
        api_key = AI_API_CONFIG.get('api_key', '')
        base_url = AI_API_CONFIG.get('base_url', '')
        
        if not api_key or not base_url:
            print("⚠️ LLM API未配置，使用默认提示词")
            return f"全身，主角形象，纯白背景，人物居中站立，{game_theme}风格，{attr_description}，{style_description if style_description else '写实风格'}，detailed, high quality, 4k, no text, no symbols"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8"
        }
        
        request_body = {
            "model": "deepseek-v3.2",  # 使用deepseek-v3.2模型
            "messages": [
                {
                    "role": "user",
                    "content": llm_prompt
                }
            ],
            "temperature": 0.7,
            "max_tokens": 2000
        }
        
        print("🔄 正在使用LLM生成主角形象提示词...")
        response = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=request_body,
            timeout=120
        )
        response.raise_for_status()
        
        result = response.json()
        choices = result.get("choices", [])
        if choices and len(choices) > 0:
            optimized_prompt = choices[0].get("message", {}).get("content", "").strip()
            if optimized_prompt:
                # 若检测到现实IP/人物：强制把名称标识塞进最终提示词（防止LLM遗漏导致“不像”）
                try:
                    if required_name_tokens:
                        missing = [t for t in required_name_tokens if t and t not in optimized_prompt]
                        if missing:
                            optimized_prompt = f"{' / '.join(required_name_tokens)}, {optimized_prompt}"
                    if identity_hint and identity_hint not in optimized_prompt:
                        optimized_prompt = f"{identity_hint}, {optimized_prompt}"
                except Exception:
                    pass
                # 在优化后的提示词末尾添加“白底全身”的明确指令（避免 LLM 输出半身照指令）
                optimized_prompt = f"{optimized_prompt}, full body, standing pose, arms relaxed at sides, pure white background, character centered, no text, no symbols, no garbled characters, no words"
                print(f"✅ LLM主角形象提示词生成完成，长度：{len(optimized_prompt)}字符")
                return optimized_prompt
        
        # 如果LLM调用失败，使用默认提示词
        print("⚠️ LLM生成失败，使用默认提示词")
        return f"全身，主角形象，纯白背景，人物居中站立，{game_theme}风格，{attr_description}，{style_description if style_description else '写实风格'}，detailed, high quality, 4k, no text, no symbols"
        
    except Exception as e:
        print(f"⚠️ LLM主角形象提示词生成出错：{str(e)}，使用默认提示词")
        # 出错时使用默认提示词
        core_worldview = global_state.get('core_worldview', {})
        game_style = core_worldview.get('game_style', '')
        attr_description = f"颜值{protagonist_attr.get('颜值', '普通')}，智商{protagonist_attr.get('智商', '普通')}，体力{protagonist_attr.get('体力', '普通')}，魅力{protagonist_attr.get('魅力', '普通')}"
        return f"全身，主角形象，纯白背景，人物居中站立，{game_style}风格，{attr_description}，detailed, high quality, 4k, no text, no symbols"

def calculate_image_size_for_viewport(viewport_width: int, viewport_height: int, provider: str = "yunwu") -> tuple:
    """
    根据视口尺寸计算合适的图片生成尺寸（保持宽高比，同时考虑API限制）
    :param viewport_width: 视口宽度
    :param viewport_height: 视口高度
    :param provider: 图片生成服务提供商
    :return: (width, height) 元组
    """
    if not viewport_width or not viewport_height or viewport_width <= 0 or viewport_height <= 0:
        # 如果视口尺寸无效，使用默认尺寸
        return (1024, 1024)
    
    # 计算视口宽高比
    viewport_aspect = viewport_width / viewport_height
    
    # 基础尺寸（保持合理的分辨率，避免过大导致生成慢或失败）
    base_size = 1024
    
    # 根据不同的API提供商，计算合适的尺寸
    if provider == "openai":
        # DALL-E 3支持：1024x1024, 1024x1792, 1792x1024
        if viewport_aspect > 1.5:  # 横屏（宽>高）
            return (1792, 1024)
        elif viewport_aspect < 0.7:  # 竖屏（高>宽）
            return (1024, 1792)
        else:  # 接近正方形
            return (1024, 1024)
    elif provider == "stable_diffusion":
        # Stable Diffusion 通常支持任意尺寸，但建议使用8的倍数
        # 保持视口宽高比，同时确保尺寸合理
        if viewport_aspect > 1:
            # 横屏：以宽度为基准
            width = base_size
            height = int(base_size / viewport_aspect)
            # 确保是8的倍数
            height = (height // 8) * 8
            if height < 512:
                height = 512
            return (width, height)
        else:
            # 竖屏：以高度为基准
            height = base_size
            width = int(base_size * viewport_aspect)
            # 确保是8的倍数
            width = (width // 8) * 8
            if width < 512:
                width = 512
            return (width, height)
    else:
        # 其他API（yunwu, replicate, comfyui等）
        # 保持视口宽高比，使用基础尺寸
        if viewport_aspect > 1:
            # 横屏：以宽度为基准
            width = base_size
            height = int(base_size / viewport_aspect)
            # 确保是8的倍数（大多数模型要求）
            height = (height // 8) * 8
            if height < 512:
                height = 512
            return (width, height)
        else:
            # 竖屏：以高度为基准
            height = base_size
            width = int(base_size * viewport_aspect)
            # 确保是8的倍数
            width = (width // 8) * 8
            if width < 512:
                width = 512
            return (width, height)

def call_image_api_with_custom_size(
    prompt: str,
    width: int = 1024,
    height: int = 1536,
    reference_image_url: str = "",
    sd_denoising_strength: float = None
) -> str:
    """
    调用生图API生成指定尺寸的图片
    :param prompt: 图片生成提示词
    :param width: 图片宽度
    :param height: 图片高度
    :param reference_image_url: 参考图URL/路径（可选；仅部分provider支持，优先走Stable Diffusion img2img）
    :param sd_denoising_strength: 当走 Stable Diffusion img2img 时使用的 denoising_strength（可选）
    :return: 图片URL或base64数据
    """
    provider = IMAGE_GENERATION_CONFIG.get("provider", "yunwu")

    # 若提供了参考图：走图生图。优先用云雾 API 中的 stability-ai/stable-diffusion-img2img（传图片+prompt）
    if reference_image_url:
        img2img_base = (IMAGE_GENERATION_CONFIG.get("img2img_base_url") or "").strip()
        img2img_key = (IMAGE_GENERATION_CONFIG.get("img2img_api_key") or "").strip()
        sd_base = IMAGE_GENERATION_CONFIG.get("stable_diffusion_base_url", "")
        # 1) 若配置了云雾图生图（Img2img_BASE_URL + Img2img_API_KEY），用云雾 API 的图生图模型
        if img2img_base and img2img_key:
            print(f"🧷 主角生图使用参考图（云雾 API stability-ai/stable-diffusion-img2img）：{reference_image_url[:120]}...")
            return call_img2img_via_yunwu(
                prompt,
                width,
                height,
                reference_image_url=reference_image_url,
                denoising_strength=sd_denoising_strength
            )
        # 2) 否则若配置了本地 SD，走 SD img2img
        if sd_base or provider == "stable_diffusion":
            print(f"🧷 主角生图使用参考图（本地 SD img2img）：{reference_image_url[:120]}...")
            return call_stable_diffusion_api_with_size(
                prompt,
                width,
                height,
                style="default",
                reference_image_url=reference_image_url,
                denoising_strength=sd_denoising_strength
            )
        print("⚠️ 检测到参考图，但未配置图生图（Img2img_* 或 Stable Diffusion），将忽略参考图。")
    
    if provider == "yunwu":
        # yunwu.ai可能不支持自定义尺寸，先尝试标准调用
        # 在提示词中添加尺寸要求
        size_prompt = f"{prompt}, aspect ratio {width}:{height}, portrait orientation"
        return call_yunwu_image_api(size_prompt, "default")
    elif provider == "replicate":
        return call_replicate_api(prompt, "default")
    elif provider == "openai":
        # DALL-E 3支持1024x1024, 1024x1792, 1792x1024
        # 1024x1536不在支持列表中，使用最接近的1792x1024或1024x1024
        if height > width:
            # 竖版，使用1024x1792（最接近1024x1536）
            size = "1024x1792"
        else:
            size = "1024x1024"
        return call_dalle_api_with_size(prompt, size)
    elif provider == "stable_diffusion":
        return call_stable_diffusion_api_with_size(
            prompt,
            width,
            height,
            style="default",
            reference_image_url=reference_image_url or "",
            denoising_strength=sd_denoising_strength
        )
    elif provider == "comfyui":
        return call_comfyui_api(prompt, "default")
    else:
        print(f"⚠️ 不支持的图片生成服务：{provider}")
        return None

def call_dalle_api_with_size(prompt: str, size: str) -> str:
    """调用DALL-E API生成指定尺寸的图片"""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=IMAGE_GENERATION_CONFIG.get("openai_api_key"))
        
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt[:1000],  # DALL-E 3限制提示词长度
            size=size,
            quality="standard",
            n=1,
        )
        
        return response.data[0].url
    except Exception as e:
        print(f"❌ DALL-E API调用失败：{str(e)}")
        raise


def _ref_image_to_input(ref: str, max_data_uri_bytes: int = 600000) -> str:
    """
    将参考图（本地路径 / HTTP URL / data URI）转为 Replicate 可接受的 input：
    data URI 或 HTTP URL。本地路径转为 data URI；若超过约 1MB 会压缩以符合 Replicate 建议。
    """
    import base64
    if not ref or not isinstance(ref, str):
        return ""
    ref = ref.strip()
    if not ref:
        return ""
    if ref.startswith("data:image"):
        return ref
    if ref.startswith(("http://", "https://")):
        return ref
    if os.path.exists(ref):
        try:
            with open(ref, "rb") as f:
                raw = f.read()
            # Replicate 建议 data URI 仅用于 <1MB；过大易导致 400
            if len(raw) * 4 // 3 <= max_data_uri_bytes:
                b64 = base64.b64encode(raw).decode("utf-8")
                return f"data:image/png;base64,{b64}"
            try:
                from PIL import Image
                import io
                im = Image.open(io.BytesIO(raw)).convert("RGB")
                w, h = im.size
                # 缩小长边至 640，使压缩后约 <500KB，避免代理/API 拒大 body
                max_side = 640
                if max(w, h) > max_side:
                    if w >= h:
                        im = im.resize((max_side, int(h * max_side / w)), Image.Resampling.LANCZOS)
                    else:
                        im = im.resize((int(w * max_side / h), max_side), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=88, optimize=True)
                buf.seek(0)
                raw = buf.read()
                b64 = base64.b64encode(raw).decode("utf-8")
                return f"data:image/jpeg;base64,{b64}"
            except Exception:
                b64 = base64.b64encode(raw).decode("utf-8")
                return f"data:image/png;base64,{b64}"
        except Exception:
            return ""
    return ""


# Replicate 官方 stability-ai/stable-diffusion-img2img 最新版 version hash（无 width/height 输入）
REPLICATE_IMG2IMG_VERSION = "15a3689ee13b0d2616e98820eca31d4c3abcd36672df6afce5cb6feb1d66087d"


def call_img2img_via_replicate_direct(
    prompt: str,
    width: int,
    height: int,
    reference_image_url: str = "",
    denoising_strength: float = None
) -> str:
    """
    直接调用 Replicate 官方 API，使用 stability-ai/stable-diffusion-img2img 做图生图。
    需在 .env 中设置 REPLICATE_API_TOKEN。绕过云雾代理，避免 400 等问题。
    """
    import time
    token = (IMAGE_GENERATION_CONFIG.get("replicate_api_token") or "").strip()
    if not token:
        raise ValueError("直接 Replicate 图生图未配置：请在 .env 中设置 REPLICATE_API_TOKEN")
    image_input = _ref_image_to_input(reference_image_url)
    if not image_input:
        raise ValueError("无法加载参考图，请检查 reference_image_url 是否为有效路径或 URL")
    ds = 0.5
    if denoising_strength is not None:
        try:
            ds = max(0.0, min(1.0, float(denoising_strength)))
        except Exception:
            pass
    create_url = "https://api.replicate.com/v1/predictions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "version": REPLICATE_IMG2IMG_VERSION,
        "input": {
            "image": image_input,
            "prompt": prompt,
            "prompt_strength": ds,
        },
    }
    try:
        resp = requests.post(create_url, headers=headers, json=payload, timeout=60)
        if resp.status_code >= 400:
            try:
                err_body = resp.json()
            except Exception:
                err_body = resp.text
            print(f"❌ Replicate 图生图 API 错误 {resp.status_code}，响应: {err_body}")
        resp.raise_for_status()
        data = resp.json()
        pred_id = (data.get("id") or "").strip()
        if not pred_id:
            raise RuntimeError("Replicate 未返回 prediction id")
        get_url = f"https://api.replicate.com/v1/predictions/{pred_id}"
        max_wait = int(os.getenv("IMG2IMG_POLL_MAX_SECONDS", "120"))
        interval = float(os.getenv("IMG2IMG_POLL_INTERVAL_SECONDS", "2"))
        deadline = time.time() + max_wait
        while time.time() < deadline:
            r2 = requests.get(get_url, headers=headers, timeout=30)
            r2.raise_for_status()
            p = r2.json()
            status = (p.get("status") or "").lower()
            if status == "succeeded":
                out = p.get("output")
                if isinstance(out, list) and len(out) > 0:
                    url_or_b64 = out[0]
                elif isinstance(out, str):
                    url_or_b64 = out
                else:
                    raise RuntimeError("图生图返回的 output 格式异常")
                if isinstance(url_or_b64, str) and url_or_b64.startswith(("http://", "https://")):
                    return url_or_b64
                if isinstance(url_or_b64, str) and len(url_or_b64) > 100:
                    if not url_or_b64.startswith("data:"):
                        return f"data:image/png;base64,{url_or_b64}"
                    return url_or_b64
                raise RuntimeError("图生图 output 无法解析为 URL 或 base64")
            if status in ("failed", "canceled"):
                err = p.get("error") or status
                raise RuntimeError(f"图生图任务结束：{err}")
            time.sleep(interval)
        raise RuntimeError(f"图生图轮询超时（{max_wait}s）")
    except requests.exceptions.HTTPError as e:
        print(f"❌ Replicate 图生图 API HTTP 错误：{e.response.status_code if e.response else ''} {str(e)}")
        raise
    except Exception as e:
        print(f"❌ Replicate 图生图 API 调用失败：{str(e)}")
        raise


def call_img2img_via_yunwu(
    prompt: str,
    width: int,
    height: int,
    reference_image_url: str = "",
    denoising_strength: float = None
) -> str:
    """
    通过云雾 API 调用图生图模型（stability-ai/stable-diffusion-img2img）。
    配置与其他服务一致：BASE_URL（https://yunwu.ai/v1）+ PATH + MODEL。
    支持两种格式：
    1. Replicate格式：当PATH包含/replicate/时，使用Replicate API格式（version + input）
    2. 云雾API格式：其他情况使用云雾API格式（model + image + prompt）
    """
    import time
    # 重新加载环境变量以确保获取最新值
    from dotenv import load_dotenv
    load_dotenv(override=True)
    
    base_url_raw = (os.getenv("Img2img_BASE_URL") or IMAGE_GENERATION_CONFIG.get("img2img_base_url") or "https://yunwu.ai/v1").strip()
    # 优先从环境变量直接读取
    path_env = os.getenv("Img2img_PATH", "").strip()
    path_config = IMAGE_GENERATION_CONFIG.get("img2img_path", "").strip()
    path = (path_env or path_config or "/images/edit").strip()
    if not path.startswith("/"):
        path = "/" + path
    
    # 如果使用的是默认路径，发出警告
    if path == "/images/edit" and not path_env and not path_config:
        print(f"⚠️ 警告：使用默认路径 /images/edit，请检查 .env 文件中的 Img2img_PATH 配置")
    
    api_key = (os.getenv("Img2img_API_KEY") or IMAGE_GENERATION_CONFIG.get("img2img_api_key") or "").strip()
    model = (os.getenv("Img2img_MODEL") or IMAGE_GENERATION_CONFIG.get("img2img_model") or "stability-ai/stable-diffusion-img2img").strip()
    if not api_key:
        raise ValueError("图生图未配置：请在 .env 中设置 Img2img_API_KEY")
    
    # 构建URL：正确处理base_url和path的拼接
    # 根据.env配置：
    # - Img2img_BASE_URL=https://yunwu.ai（没有/v1）
    # - Img2img_PATH=/replicate/v1/predictions
    # 正确URL应该是：https://yunwu.ai/replicate/v1/predictions
    # 
    # 如果path以/replicate/开头，说明是通过云雾代理调用Replicate
    # 此时path已经包含完整路径，直接拼接base_url和path，不要添加/v1
    base_url_clean = base_url_raw.rstrip("/")
    
    if path.startswith("/replicate/"):
        # Replicate路径：直接拼接，不添加/v1（因为path已经包含完整路径）
        create_url = base_url_clean + path
    else:
        # 其他路径（如/images/edit）：如果base_url没有/v1，添加/v1
        if not base_url_clean.endswith("/v1"):
            base_url_clean = base_url_clean + "/v1"
        create_url = base_url_clean + path
    
    print(f"🔧 图生图配置：base_url_raw='{base_url_raw}', path_env='{path_env}', path_config='{path_config}', 最终path='{path}', create_url='{create_url}'")
    
    # 对于Replicate格式，确保图片格式正确（优先使用JPEG，因为PNG可能不被支持）
    image_input = _ref_image_to_input(reference_image_url)
    if not image_input:
        raise ValueError("无法加载参考图，请检查 reference_image_url 是否为有效路径或 URL")
    
    # 如果是Replicate格式且图片是PNG，尝试转换为JPEG
    if "/replicate/" in path.lower() and image_input.startswith("data:image/png"):
        try:
            import base64
            from PIL import Image
            import io
            # 提取base64数据
            b64_data = image_input.split("base64,", 1)[1]
            img_bytes = base64.b64decode(b64_data)
            # 转换为JPEG
            im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=90, optimize=True)
            buf.seek(0)
            jpeg_bytes = buf.read()
            jpeg_b64 = base64.b64encode(jpeg_bytes).decode("utf-8")
            image_input = f"data:image/jpeg;base64,{jpeg_b64}"
            print(f"🔧 已将PNG图片转换为JPEG格式（Replicate兼容性）")
        except Exception as e:
            print(f"⚠️ PNG转JPEG失败，继续使用原格式: {str(e)}")
    ds = 0.5
    if denoising_strength is not None:
        try:
            ds = max(0.0, min(1.0, float(denoising_strength)))
        except Exception:
            pass
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    
    # 判断API格式：如果路径包含/replicate/，使用Replicate格式
    is_replicate_format = "/replicate/" in path.lower()
    
    if is_replicate_format:
        # 通过云雾代理调用Replicate时，使用标准的Replicate API格式
        # 云雾代理只是转发请求，不会改变请求格式，所以应该使用和直接调用Replicate相同的格式
        payload = {
            "version": REPLICATE_IMG2IMG_VERSION,  # 使用version而不是model（标准Replicate格式）
            "input": {
                "image": image_input,
                "prompt": prompt,
                "prompt_strength": ds,  # 使用prompt_strength（标准Replicate格式）而不是strength
                # 注意：stability-ai/stable-diffusion-img2img不支持width/height参数
            }
        }
        print(f"🔧 使用Replicate API格式调用图生图（通过云雾代理），version={REPLICATE_IMG2IMG_VERSION[:20]}..., prompt_strength={ds}")
    else:
        # 云雾 API 格式：model + image + prompt
        payload = {
            "model": model,
            "image": image_input,
            "prompt": prompt,
            "strength": ds,  # 云雾可能用 strength 而不是 prompt_strength
        }
        print(f"🔧 使用云雾API格式调用图生图")
    try:
        # 打印请求详情用于调试
        import json
        print(f"🔍 请求详情：URL={create_url}, payload_keys={list(payload.keys())}")
        if "input" in payload:
            print(f"🔍 input keys: {list(payload['input'].keys())}")
        
        resp = requests.post(create_url, headers=headers, json=payload, timeout=60)
        if resp.status_code >= 400:
            try:
                err_body = resp.json()
            except Exception:
                err_body = resp.text
            print(f"❌ 云雾图生图 API 错误 {resp.status_code}，响应: {err_body}")
            print(f"🔍 请求URL: {create_url}")
            # 打印payload但不包含图片数据（可能很大）
            payload_debug = {k: (v[:100] + "..." if isinstance(v, str) and len(v) > 100 else v) for k, v in payload.items()}
            if "input" in payload_debug and isinstance(payload_debug["input"], dict):
                input_debug = {}
                for k, v in payload_debug["input"].items():
                    if k == "image" and isinstance(v, str):
                        # 显示图片格式和大小信息
                        if v.startswith("data:image"):
                            img_type = v.split(";")[0].split("/")[-1] if "/" in v.split(";")[0] else "unknown"
                            img_size = len(v) if len(v) < 200 else "..." + str(len(v))
                            input_debug[k] = f"data:image/{img_type};base64,... (size: {img_size} chars)"
                        else:
                            input_debug[k] = v[:100] + "..." if len(str(v)) > 100 else v
                    else:
                        input_debug[k] = v[:100] + "..." if isinstance(v, str) and len(v) > 100 else v
                payload_debug["input"] = input_debug
            print(f"🔍 请求payload: {json.dumps(payload_debug, indent=2, ensure_ascii=False)}")
            
            # 如果是400错误，尝试打印更详细的错误信息
            if resp.status_code == 400:
                print(f"🔍 完整错误响应: {resp.text[:1000]}")
                # 尝试从响应中提取更详细的错误信息
                try:
                    error_detail = resp.json()
                    if isinstance(error_detail, dict):
                        error_msg = error_detail.get('message', '')
                        error_data = error_detail.get('data', '')
                        if error_msg:
                            print(f"🔍 错误消息: {error_msg}")
                        if error_data:
                            print(f"🔍 错误数据: {error_data}")
                except:
                    pass
        resp.raise_for_status()
        data = resp.json()
        # 云雾 API 可能直接返回图片 URL，也可能返回异步任务（类似 Replicate）
        # 先尝试直接返回格式
        if "url" in data:
            return data["url"]
        if "image" in data:
            img = data["image"]
            if isinstance(img, str) and img.startswith(("http://", "https://", "data:image")):
                return img
        if "data" in data and isinstance(data["data"], list) and len(data["data"]) > 0:
            img = data["data"][0]
            if isinstance(img, dict) and "url" in img:
                return img["url"]
            if isinstance(img, str) and img.startswith(("http://", "https://", "data:image")):
                return img
        # 如果是异步任务格式（Replicate 兼容），进行轮询
        pred_id = (data.get("id") or "").strip()
        if pred_id:
            get_url = create_url.rstrip("/") + "/" + pred_id
            max_wait = int(os.getenv("IMG2IMG_POLL_MAX_SECONDS", "120"))
            interval = float(os.getenv("IMG2IMG_POLL_INTERVAL_SECONDS", "2"))
            deadline = time.time() + max_wait
            while time.time() < deadline:
                r2 = requests.get(get_url, headers=headers, timeout=30)
                r2.raise_for_status()
                p = r2.json()
                status = (p.get("status") or "").lower()
                if status == "succeeded":
                    out = p.get("output")
                    if isinstance(out, list) and len(out) > 0:
                        url_or_b64 = out[0]
                    elif isinstance(out, str):
                        url_or_b64 = out
                    else:
                        raise RuntimeError("图生图返回的 output 格式异常")
                    if isinstance(url_or_b64, str) and url_or_b64.startswith(("http://", "https://")):
                        return url_or_b64
                    if isinstance(url_or_b64, str) and len(url_or_b64) > 100:
                        if not url_or_b64.startswith("data:"):
                            return f"data:image/png;base64,{url_or_b64}"
                        return url_or_b64
                    raise RuntimeError("图生图 output 无法解析为 URL 或 base64")
                if status in ("failed", "canceled"):
                    err = p.get("error") or status
                    raise RuntimeError(f"图生图任务结束：{err}")
                time.sleep(interval)
            raise RuntimeError(f"图生图轮询超时（{max_wait}s）")
        # 无法解析响应格式
        raise RuntimeError(f"云雾图生图返回格式无法解析：{data}")
    except requests.exceptions.HTTPError as e:
        print(f"❌ 云雾图生图 API HTTP 错误：{e.response.status_code if e.response else ''} {str(e)}")
        raise
    except Exception as e:
        print(f"❌ 云雾图生图 API 调用失败：{str(e)}")
        raise


def call_stable_diffusion_api_with_size(
    prompt: str,
    width: int,
    height: int,
    style: str = "default",
    reference_image_url: str = "",
    denoising_strength: float = None
) -> str:
    """调用本地Stable Diffusion API生成指定尺寸的图片（支持img2img参考图）"""
    try:
        import base64
        from pathlib import Path

        base_url = IMAGE_GENERATION_CONFIG.get("stable_diffusion_base_url", "http://localhost:7860")
        api_key = IMAGE_GENERATION_CONFIG.get("stable_diffusion_api_key", "")

        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        def _load_ref_image_b64(ref: str) -> str:
            """把参考图读成 base64（不带 data:image 前缀），失败返回空串。"""
            if not ref or not isinstance(ref, str):
                return ""
            ref = ref.strip()
            if not ref:
                return ""

            # data URL
            if ref.startswith("data:image"):
                try:
                    b64_part = ref.split("base64,", 1)[1]
                    b64_part = re.sub(r"\s+", "", b64_part)
                    base64.b64decode(b64_part, validate=False)
                    return b64_part
                except Exception:
                    return ""

            # HTTP/HTTPS URL
            if ref.startswith(("http://", "https://")):
                try:
                    resp = requests.get(ref, timeout=30, stream=True)
                    resp.raise_for_status()
                    img_bytes = resp.content
                    return base64.b64encode(img_bytes).decode("utf-8")
                except Exception:
                    return ""

            # 本地路径
            if os.path.exists(ref):
                try:
                    with open(ref, "rb") as f:
                        img_bytes = f.read()
                    return base64.b64encode(img_bytes).decode("utf-8")
                except Exception:
                    return ""

            return ""

        ref_b64 = _load_ref_image_b64(reference_image_url) if reference_image_url else ""
        # 关键诊断：确认参考图是否真的被读入（否则会退回 txt2img，侧/背会“看起来毫无关系”）
        try:
            if reference_image_url:
                exists_flag = os.path.exists(reference_image_url) if isinstance(reference_image_url, str) else False
                print(
                    f"🔎 [SD] reference_image_url provided, exists={exists_flag}, "
                    f"ref_b64_len={len(ref_b64) if ref_b64 else 0}"
                )
        except Exception:
            pass

        # 如果有参考图，使用img2img，否则使用txt2img
        if ref_b64:
            # 允许外部传入 denoising_strength；默认保持历史行为 0.7
            try:
                ds = float(denoising_strength) if denoising_strength is not None else 0.7
            except Exception:
                ds = 0.7
            # 合法范围兜底
            if ds < 0.0:
                ds = 0.0
            if ds > 1.0:
                ds = 1.0
            # img2img模式
            request_payload = {
                "prompt": prompt,
                "width": width,
                "height": height,
                "steps": 20,
                "cfg_scale": 7,
                "init_images": [ref_b64],
                "denoising_strength": ds  # 控制参考图的影响程度
            }
            api_endpoint = f"{base_url}/sdapi/v1/img2img"
        else:
            # txt2img模式
            request_payload = {
                "prompt": prompt,
                "width": width,
                "height": height,
                "steps": 20,
                "cfg_scale": 7
            }
            api_endpoint = f"{base_url}/sdapi/v1/txt2img"

        response = requests.post(
            api_endpoint,
            headers=headers,
            json=request_payload,
            timeout=120
        )
        response.raise_for_status()
        
        result = response.json()
        if "images" in result and len(result["images"]) > 0:
            # 返回base64数据
            return result["images"][0]
        return None
    except Exception as e:
        print(f"❌ Stable Diffusion API调用失败：{str(e)}")
        raise

def generate_main_character_image(
    protagonist_attr: Dict,
    global_state: Dict,
    image_style: Dict = None,
    game_id: str = None
) -> Dict:
    """
    生成主角形象图片
    :param protagonist_attr: 主角属性
    :param global_state: 全局状态
    :param image_style: 图片风格
    :param game_id: 游戏ID（如果为None，会自动生成）
    :return: 包含图片路径和元数据的字典，如果失败返回None
    """
    try:
        import threading

        # 侧/背生成已改用 gemini-2.5-flash-image 图生图，不再使用 denoising_strength

        # metadata 并发写保护（侧/背线程会更新 metadata.json）
        _metadata_lock = threading.Lock()

        def _style_label(style_obj: Dict) -> str:
            if not isinstance(style_obj, dict):
                return "default"
            t = _safe_str(style_obj.get("type")).strip()
            if t:
                if t == "custom":
                    v = _safe_str(style_obj.get("value")).strip()
                    return v or "custom"
                return t
            return "default"

        def _pick_identifier(req_tokens: list) -> str:
            try:
                if isinstance(req_tokens, list) and req_tokens:
                    cand = _safe_str(req_tokens[0]).strip()
                    if cand:
                        return cand
            except Exception:
                pass
            return "protagonist"

        def _save_image_any(image_url_or_data_obj, out_path: Path) -> bool:
            """复用现有保存逻辑，但可写到任意文件名。"""
            try:
                image_url_str_local = str(image_url_or_data_obj or "")
                if not image_url_str_local:
                    return False

                out_path.parent.mkdir(parents=True, exist_ok=True)

                if image_url_str_local.startswith("data:image"):
                    import base64
                    base64_data = image_url_str_local.split(",", 1)[1] if "," in image_url_str_local else image_url_str_local
                    image_data = base64.b64decode(base64_data)
                    with open(out_path, "wb") as f:
                        f.write(image_data)
                    return out_path.exists()

                if image_url_str_local.startswith(("http://", "https://")):
                    resp = requests.get(image_url_str_local, timeout=60, stream=True)
                    resp.raise_for_status()
                    with open(out_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                    return out_path.exists()

                if image_url_str_local.startswith("/image_cache/") or image_url_str_local.startswith("image_cache/"):
                    import shutil
                    if image_url_str_local.startswith("image_cache/"):
                        source_path = Path("image_cache") / image_url_str_local.replace("image_cache/", "")
                    else:
                        source_path = Path("image_cache") / image_url_str_local.replace("/image_cache/", "")
                    if source_path.exists():
                        shutil.copy2(source_path, out_path)
                        return out_path.exists()
                    return False

                # 可能是纯 base64（无 data:image 前缀）
                if isinstance(image_url_or_data_obj, str) and len(image_url_str_local) > 100:
                    try:
                        import base64
                        image_data = base64.b64decode(image_url_str_local)
                        with open(out_path, "wb") as f:
                            f.write(image_data)
                        return out_path.exists()
                    except Exception:
                        return False

                # 最后兜底：若是本地文件路径，尝试复制
                try:
                    if os.path.exists(image_url_str_local):
                        import shutil
                        shutil.copy2(image_url_str_local, out_path)
                        return out_path.exists()
                except Exception:
                    pass

                return False
            except Exception:
                return False

        def _update_metadata_file(metadata_path: Path, updater_fn):
            """线程安全更新 metadata.json。"""
            with _metadata_lock:
                current = {}
                if metadata_path.exists():
                    try:
                        with open(metadata_path, "r", encoding="utf-8") as f:
                            current = json.load(f) or {}
                    except Exception:
                        current = {}
                try:
                    updated = updater_fn(current if isinstance(current, dict) else {})
                except Exception:
                    updated = current if isinstance(current, dict) else {}
                try:
                    with open(metadata_path, "w", encoding="utf-8") as f:
                        json.dump(updated, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

        def _async_generate_view(
            view_name: str,
            out_filename: str,
            prompt_text: str,
            reference_front_path: str
        ):
            try:
                out_path = main_character_dir / out_filename
                print(f"🎨 [侧/背图] 开始任务 view={view_name} game_id={game_id} 输出路径={out_path}")
                # 记录本任务开始时正面图的 mtime，写入前校验：若正面已被重新生成则不再写入，避免旧线程覆盖新图
                front_mtime_at_start = 0.0
                if reference_front_path and os.path.isfile(reference_front_path):
                    try:
                        front_mtime_at_start = os.path.getmtime(reference_front_path)
                    except Exception:
                        pass
                print(
                    f"🔎 主角{view_name}图参考正面: {reference_front_path} exists={os.path.exists(reference_front_path) if isinstance(reference_front_path, str) else False} front_mtime_at_start={front_mtime_at_start}"
                )
                
                # 优先使用 gemini-2.5-flash-image 图生图
                img = None
                use_img2img = False
                model = IMAGE_GENERATION_CONFIG.get("yunwu_model", "gemini-2.5-flash-image")
                if "gemini" in model.lower() and "image" in model.lower():
                    print(f"🔄 尝试使用 gemini-2.5-flash-image 图生图生成{view_name}视图...")
                    img = call_gemini_img2img(prompt_text, reference_front_path, cache_key_suffix=reference_front_path)
                    use_img2img = True
                    if img:
                        print(f"✅ gemini-2.5-flash-image 图生图成功")
                    else:
                        print(f"⚠️ gemini-2.5-flash-image 图生图失败，回退到文生图")
                
                # 如果图生图失败，回退到文生图
                if not img:
                    print(f"🔄 使用文生图生成{view_name}视图...")
                    img = call_image_api_with_custom_size(
                        prompt_text,
                        width=1024,
                        height=1536,
                        reference_image_url=None  # 文生图不使用参考图
                    )
                
                if not img:
                    print(f"⚠️ 主角{view_name}图生成失败：生图返回空 game_id={game_id} out_path={out_path}")
                    return
                
                # 🔧 防竞态：若正面图在本任务期间被重新生成（新一次游戏），则不要用“基于旧正面”的侧/背覆盖
                if front_mtime_at_start > 0 and reference_front_path and os.path.isfile(reference_front_path):
                    try:
                        current_front_mtime = os.path.getmtime(reference_front_path)
                        if current_front_mtime > front_mtime_at_start:
                            print(f"⚠️ 主角{view_name}图跳过写入：正面图已在本任务期间被重新生成（current_mtime={current_front_mtime} > start={front_mtime_at_start}），避免用旧参考生成的图覆盖 game_id={game_id}")
                            return
                    except Exception as e:
                        print(f"⚠️ 主角{view_name}图 mtime 校验异常：{e}，继续写入")
                    
                print(f"📁 [侧/背图] 即将写入 game_id={game_id} path={out_path}")
                ok = _save_image_any(img, out_path)
                if ok:
                    print(f"✅ 主角{view_name}图已保存 game_id={game_id} path={out_path}")
                    metadata_path_local = main_character_dir / "metadata.json"
                    _update_metadata_file(
                        metadata_path_local,
                        lambda m: {
                            **m,
                            "views": {
                                **(m.get("views") if isinstance(m.get("views"), dict) else {}),
                                view_name: {
                                    "filename": out_filename,
                                    "image_url": f"/initial/main_character/{game_id}/{out_filename}",
                                    "prompt": prompt_text,
                                    "reference_front_path": reference_front_path,
                                    "generation_method": "img2img" if use_img2img else "text2img",
                                    "generated_at": datetime.now().isoformat()
                                }
                            }
                        }
                    )
                else:
                    print(f"⚠️ 主角{view_name}图保存失败 game_id={game_id} path={out_path}")
            except Exception as e:
                print(f"❌ 主角{view_name}图生成异常 game_id={game_id} out_path={out_path} error={e}")
                import traceback
                traceback.print_exc()

        # 生成游戏ID（如果未提供）
        if not game_id:
            game_id = generate_game_id()
        
        # 确保目录存在
        main_character_dir = ensure_main_character_dir(game_id)
        
        # 检查是否已存在主角正面图（正面仍命名 main_character.png 以兼容前端）
        front_path = main_character_dir / "main_character.png"
        side_path = main_character_dir / "main_character_side.png"
        back_path = main_character_dir / "main_character_back.png"

        # 1.5 若为现实IP/人物且拿到了参考图：传给生图以提高“还原度”
        reference_image_url = ""
        required_tokens = []
        if isinstance(global_state, dict):
            reference_image_url = _safe_str(global_state.get("_main_character_ref_image_url")).strip()
            required_tokens = global_state.get("_main_character_required_name_tokens") or []

        identifier = _pick_identifier(required_tokens)
        style_label = _style_label(image_style)

        # 🔧 修复：每次新游戏都强制重新生成主角形象，不复用旧文件
        # 原因：即使主角属性相同，但世界观、游戏主题、图片风格等都可能不同，主角形象应该不同
        metadata_path = main_character_dir / "metadata.json"
        any_existed = front_path.exists() or side_path.exists() or back_path.exists() or metadata_path.exists()
        if any_existed:
            print(f"🔄 检测到已存在的主角形象文件（game_id={game_id}），将删除并重新生成")
        for label, p in [("正面图", front_path), ("侧面图", side_path), ("背面图", back_path), ("元数据", metadata_path)]:
            if p.exists():
                try:
                    p.unlink()
                    print(f"   ✅ 已删除旧{label}：{p}")
                except Exception as e:
                    print(f"   ⚠️ 删除旧{label}失败 path={p} error={e}")
        if front_path.exists() or side_path.exists() or back_path.exists():
            print(f"🔄 仍有残留文件（game_id={game_id}），再次尝试删除")
            for label, p in [("正面图", front_path), ("侧面图", side_path), ("背面图", back_path), ("元数据", metadata_path)]:
                if p.exists():
                    try:
                        p.unlink()
                        print(f"   ✅ 再次删除成功：{p}")
                    except Exception as e:
                        print(f"   ❌ 再次删除失败 path={p} error={e}，侧/背图可能仍为旧图")
        
        # 1. 使用LLM生成“人物特征描述”（后续套入三视图模板）
        features = optimize_main_character_prompt_with_llm(protagonist_attr, global_state, image_style)
        front_prompt = prompt_template_front.format(
            identifier=identifier,
            features=features,
            style=style_label
        )
        
        # 2. 调用生图API生成图片（1024x1536）
        # 获取使用的模型信息（用于日志）
        provider = IMAGE_GENERATION_CONFIG.get("provider", "yunwu")
        model = IMAGE_GENERATION_CONFIG.get("yunwu_model", "sora_image") if provider == "yunwu" else "N/A"
        print(f"🎨 正在生成主角形象图片（1024x1536），使用模型：{model}...")
        if reference_image_url:
            print(f"🧷 主角参考图已就绪，将用于生图：{reference_image_url[:120]}...")
        image_url_or_data = call_image_api_with_custom_size(
            front_prompt,
            width=1024,
            height=1536,
            reference_image_url=reference_image_url
        )
        
        print(f"🔍 call_image_api_with_custom_size 返回结果:")
        print(f"   - 类型: {type(image_url_or_data)}")
        print(f"   - 是否为None: {image_url_or_data is None}")
        if image_url_or_data:
            print(f"   - 长度: {len(str(image_url_or_data))} 字符")
            print(f"   - 前100字符: {str(image_url_or_data)[:100]}")
            print(f"   - 是否以'data:image'开头: {str(image_url_or_data).startswith('data:image')}")
            print(f"   - 是否以'http'开头: {str(image_url_or_data).startswith('http')}")
            print(f"   - 是否以'/image_cache'开头: {str(image_url_or_data).startswith('/image_cache')}")
            print(f"   - 是否以'image_cache'开头: {str(image_url_or_data).startswith('image_cache')}")
        
        if not image_url_or_data:
            print("❌ 主角形象图片生成失败：生图API返回空结果")
            return None
        
        # 3. 下载并保存正面图
        image_path = front_path
        print(f"📁 准备保存主角正面图到: {image_path}")
        print(f"📁 目录是否存在: {main_character_dir.exists()}")
        saved_ok = _save_image_any(image_url_or_data, image_path)
        if not saved_ok:
            print("❌ 主角正面图保存失败")
            return None
        print(f"✅ 主角正面图已保存：{image_path}")
        
        # 4. 保存元数据
        metadata = {
            "game_id": game_id,
            "generated_at": datetime.now().isoformat(),
            "prompt": front_prompt,
            "features": features,
            "reference_image_url": reference_image_url,
            "required_name_tokens": required_tokens,
            "protagonist_attr": protagonist_attr,
            "image_style": image_style,
            "width": 1024,
            "height": 1536
        }
        metadata["views"] = {
            "front": {
                "filename": "main_character.png",
                "image_url": f"/initial/main_character/{game_id}/main_character.png",
                "prompt": front_prompt,
                "generated_at": metadata["generated_at"]
            }
        }
        metadata_path = main_character_dir / "metadata.json"
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
        
        print(f"✅ 主角形象生成完成：{image_path}")

        # 5. 正面生成完成后：后台并行生成侧面/背面（基于正面参考图，不阻塞返回）
        try:
            # 启动前再次删除侧/背图，避免旧会话残留导致“正面新、侧背旧”
            for label, p in [("侧面图", side_path), ("背面图", back_path)]:
                if p.exists():
                    try:
                        p.unlink()
                        print(f"   ✅ 启动侧/背图前再次删除旧{label}：{p}")
                    except Exception as e:
                        print(f"   ⚠️ 启动前删除旧{label}失败 path={p} error={e}")
            front_ref_path = str(front_path.resolve())
            side_prompt = prompt_template_side.format(identifier=identifier)
            back_prompt = prompt_template_back.format(identifier=identifier)

            threading.Thread(
                target=_async_generate_view,
                args=("side", "main_character_side.png", side_prompt, front_ref_path),
                daemon=True
            ).start()
            threading.Thread(
                target=_async_generate_view,
                args=("back", "main_character_back.png", back_prompt, front_ref_path),
                daemon=True
            ).start()
            print("✅ 已启动主角侧面/背面生成任务（后台并行）")
        except Exception as e:
            print(f"⚠️ 启动主角侧面/背面生成任务失败：{str(e)}")
        
        return {
            "game_id": game_id,
            "image_path": str(image_path),
            "image_url": f"/initial/main_character/{game_id}/main_character.png",
            "width": 1024,
            "height": 1536,
            "metadata": metadata
        }
        
    except Exception as e:
        print(f"❌ 主角形象生成失败：{str(e)}")
        print(f"❌ 异常类型：{type(e).__name__}")
        import traceback
        print(f"❌ 完整错误堆栈：")
        traceback.print_exc()
        return None

# ------------------------------
# 视觉内容生成函数
# ------------------------------
import hashlib
import uuid
import random

def generate_scene_image(
    scene_description: str,
    global_state: Dict,
    style: str = "default",
    use_cache: bool = True,
    viewport_width: int = None,
    viewport_height: int = None
) -> Dict:
    """
    生成场景图片（支持本地缓存）
    :param scene_description: 场景描述文本
    :param global_state: 全局状态（用于提取世界观风格）
    :param style: 图片风格
    :param use_cache: 是否使用本地缓存（默认True，下载图片到本地避免OSS URL失效）
    :param viewport_width: 视口宽度（可选，用于按视口宽高比生成图片）
    :param viewport_height: 视口高度（可选，用于按视口宽高比生成图片）
    :return: 包含图片URL和元数据的字典
    """
    # 检查是否配置了图片生成API
    provider = IMAGE_GENERATION_CONFIG.get("provider", "yunwu")
    
    if provider == "yunwu" and not IMAGE_GENERATION_CONFIG.get("yunwu_api_key"):
        print("⚠️ yunwu.ai API Key未配置，跳过图片生成")
        return None
    elif provider == "replicate" and not IMAGE_GENERATION_CONFIG.get("replicate_api_token"):
        print("⚠️ Replicate API Token未配置，跳过图片生成")
        return None
    elif provider == "openai" and not IMAGE_GENERATION_CONFIG.get("openai_api_key"):
        print("⚠️ OpenAI API Key未配置，跳过图片生成")
        return None
    
    # 计算图片生成尺寸（基于视口宽高比）
    if viewport_width and viewport_height:
        image_width, image_height = calculate_image_size_for_viewport(viewport_width, viewport_height, provider)
        print(f"📐 根据视口尺寸 {viewport_width}x{viewport_height} 计算生成尺寸：{image_width}x{image_height}")
    else:
        # 如果没有提供视口尺寸，使用默认尺寸
        image_width, image_height = 1024, 1024
        print(f"📐 使用默认生成尺寸：{image_width}x{image_height}")
    
    # 1. 提取图片风格信息
    image_style = global_state.get('image_style', None)

    # 1.5 视觉连续性上下文（用于同场景统一风格/物件 & 参考上一剧情）
    visual_context = global_state.get('_visual_context') if isinstance(global_state, dict) else None
    if not isinstance(visual_context, dict):
        visual_context = {}
    prev_img_obj = visual_context.get('previousSceneImage') or visual_context.get('currentSceneImage') or {}
    if not isinstance(prev_img_obj, dict):
        prev_img_obj = {}
    reference_image_url = (
        visual_context.get('previous_image_url')
        or prev_img_obj.get('url')
        or prev_img_obj.get('image_url')
        or ""
    )
    reference_image_prompt = (
        visual_context.get('previous_image_prompt')
        or prev_img_obj.get('prompt')
        or prev_img_obj.get('optimized_prompt')
        or ""
    )
    
    # 1.6 获取主角参考图路径（用于保持主角形象一致性）
    # 放宽条件：只要有正面图就使用（第一次场景图与主角生成并行，侧/背可能尚未就绪）
    protagonist_reference_images = []
    game_id = global_state.get('game_id') if isinstance(global_state, dict) else None
    if game_id:
        from pathlib import Path
        main_character_dir = Path("initial") / "main_character" / game_id
        front_path = main_character_dir / "main_character.png"
        side_path = main_character_dir / "main_character_side.png"
        back_path = main_character_dir / "main_character_back.png"
        
        # 至少正面存在即加入参考；三张齐全时用三张，否则用已有视图（保证第一次场景图也能用上主角）
        if front_path.exists():
            protagonist_reference_images.append(str(front_path.resolve()))  # Image 0: 正面
            if side_path.exists():
                protagonist_reference_images.append(str(side_path.resolve()))  # Image 1: 侧面
            if back_path.exists():
                protagonist_reference_images.append(str(back_path.resolve()))  # Image 2: 背面
            if len(protagonist_reference_images) >= 3:
                print(f"✅ 找到主角三视图，将作为参考图传递：{game_id}")
            else:
                print(f"✅ 找到主角参考图（{len(protagonist_reference_images)}张），将作为参考图传递：{game_id}")
        else:
            print(f"⚠️ 主角正面图尚未就绪，将不使用主角参考图")
    
    # 1.6b 每次剧情更新时检查身份揭示，更新配角 aliases
    if game_id and scene_description:
        update_supporting_role_aliases_from_plot(game_id, scene_description)

    # 1.7 先由提示词优化 LLM 生成带「名称-配角N」的视觉描述，再根据优化后的 prompt 识别出场配角
    core_worldview = global_state.get("core_worldview", {}) or {}
    chars = (core_worldview.get("characters", {}) or {}) if isinstance(core_worldview, dict) else {}
    # 已有档案的配角（供 LLM 复用同一角色名/别号）；+ 占位供新角色
    available_supporting_roles_for_tagging = []
    if game_id:
        archives = _load_role_archives(game_id)
        for _rid, arch in archives.items():
            if isinstance(arch, dict):
                rn = _safe_str(arch.get("role_name", "")).strip()
                aliases = arch.get("aliases", [])
                if isinstance(aliases, list) and aliases:
                    names_str = "、".join(aliases[:5])
                else:
                    names_str = rn or ""
                available_supporting_roles_for_tagging.append({
                    "role_key": "已有角色",
                    "role_name": rn,
                    "names_or_aliases": names_str,
                    "shallow_background": _safe_str(arch.get("story_background", ""))[:80] or "（根据剧情）",
                })
    available_supporting_roles_for_tagging.extend([
        {"role_key": "配角1", "shallow_background": "（根据剧情描述，名称从文本中得出）"},
        {"role_key": "配角2", "shallow_background": "（根据剧情描述，名称从文本中得出）"},
    ])

    # 2. 第一次调用 LLM：只负责「名称-配角N」和场景描述，不传配角参考图
    prompt = optimize_image_prompt_with_llm(
        scene_description,
        global_state,
        image_style,
        protagonist_reference_images=protagonist_reference_images if protagonist_reference_images else None,
        supporting_role_references=None,
        available_supporting_roles_for_tagging=available_supporting_roles_for_tagging
    )
    # 打印剧情图提示词 LLM 生成内容（便于确认主角/配角与 Image 编号是否写对）
    if prompt and isinstance(prompt, str):
        _preview_len = 800
        _preview = prompt.strip()[: _preview_len]
        if len(prompt.strip()) > _preview_len:
            _preview += "..."
        print(f"📝 [剧情图提示词] LLM 生成内容（前{min(_preview_len, len(prompt))}字）：\n{_preview}")
    
    # 3. 从优化后的提示词中识别出场配角（名称-配角N），区分已有档案（有参考图）与首次出场（待建档）
    supporting_role_references = []
    supporting_role_images = []
    first_appearance_pending = []
    if game_id:
        char_tuples = extract_supporting_characters_with_names(prompt)
        image_index = 3  # Image 0,1,2 为主角；从 3 起为配角
        for display_name, slot in char_tuples:
            role_info = chars.get(slot, {}) or chars.get(display_name, {}) or {}
            if not isinstance(role_info, dict):
                role_info = {}
            arch = get_or_create_supporting_role_archive(
                game_id,
                display_name=display_name,
                slot=slot,
                role_info=role_info,
                first_appear_scene=_clip_text(scene_description, 60),
            )
            if arch.get("_pending_first_appearance"):
                first_appearance_pending.append(arch)
                print(f"📌 配角 {display_name}-{slot} 首次出场，将在剧情图生成后建档")
                print(f"   📋 待建档信息：display_name={display_name}, slot={slot}, first_appear_scene={_clip_text(arch.get('first_appear_scene',''),40)}…")
            else:
                img_path = arch.get("_resolved_first_img_path") or arch.get("first_img_path", "")
                if img_path:
                    supporting_role_images.append(img_path)
                    supporting_role_references.append({
                        "role_name": slot,
                        "display_name": display_name,
                        "image_index": image_index,
                        "core_features": arch.get("core_features", ""),
                        "first_appear_scene": arch.get("first_appear_scene", ""),
                    })
                    image_index += 1
                    print(f"✅ 配角 {display_name}-{slot} 将作为参考图 Image {image_index - 1} 传递")
                    print(f"   📋 配角信息：role_id={arch.get('_role_id','')}, role_name={arch.get('role_name','')}, aliases={arch.get('aliases',[])}, first_img={Path(img_path).name if img_path else ''}")
    
    # 打印当前游戏所有配角档案摘要
    if game_id:
        _archives = _load_role_archives(game_id)
        if _archives:
            print(f"📋 当前配角档案（共{len(_archives)}个）：")
            for _rid, _a in _archives.items():
                if isinstance(_a, dict):
                    _aliases = _a.get("aliases", [])
                    _rn = _a.get("role_name", "")
                    print(f"   - {_rid}: role_name={_rn}, aliases={_aliases}")
    
    # 4. 由代码将「参考 Image N」拼接到提示词末尾（若 LLM 已写位置指引则保留）
    if supporting_role_references:
        append_parts = []
        for sr in supporting_role_references:
            slot = _safe_str(sr.get("role_name", "")).strip()
            display_name = _safe_str(sr.get("display_name", "")).strip()
            img_idx = sr.get("image_index", 3)
            dn = display_name or slot
            append_parts.append(f"{dn}-{slot} 参考 Image {img_idx}，以图中对应人物的形象为准，保持核心特征不变")
        if append_parts:
            prompt = (prompt.rstrip() + "。" + "。".join(append_parts))
        # 打印拼接「参考 Image N」后的提示词尾部
        _tail_len = 350
        if prompt and len(prompt) > _tail_len:
            print(f"📝 [剧情图提示词] 拼接配角参考后，末尾{_tail_len}字：...{prompt[-_tail_len:]}")
    
    # 5. 调用AI图片生成API（传递尺寸参数和参考图）
    # 若有上一张剧情图，解析为可加载路径并作为最后一张参考图（用于视觉延续）
    previous_scene_image_path = None
    if reference_image_url and isinstance(reference_image_url, str):
        ref_url = reference_image_url.strip()
        if ref_url.startswith("/image_cache/") or ref_url.startswith("image_cache/"):
            prev_local = Path(__file__).resolve().parent / "image_cache" / Path(ref_url).name
            if prev_local.exists():
                previous_scene_image_path = str(prev_local)
        elif ref_url.startswith("http://") or ref_url.startswith("https://") or os.path.exists(ref_url):
            previous_scene_image_path = ref_url

    try:
        if provider == "yunwu":
            # yunwu.ai 易受 429 / 返回格式波动影响：失败时可选用本地 SD 兜底
            image_url = None
            try:
                # yunwu.ai可能不支持自定义尺寸，在提示词中添加尺寸要求
                size_prompt = f"{prompt}, aspect ratio {image_width}:{image_height}"
                
                # 参考图：主角 + 配角 + 上一张剧情图（若有）
                model = IMAGE_GENERATION_CONFIG.get("yunwu_model", "gemini-2.5-flash-image")
                all_reference_images = list(protagonist_reference_images) if protagonist_reference_images else []
                all_reference_images.extend(supporting_role_images if supporting_role_images else [])
                if previous_scene_image_path:
                    all_reference_images.append(previous_scene_image_path)
                    print(f"🖼️ 已将上一张剧情图加入参考图（共{len(all_reference_images)}张）")
                if all_reference_images and len(all_reference_images) >= 1:
                    if "gemini" in model.lower() and "image" in model.lower():
                        n_prev = 1 if previous_scene_image_path else 0
                        print(f"🎨 使用 gemini-2.5-flash-image 图生图，传递{len(all_reference_images)}张参考图（主角{len(protagonist_reference_images or [])}张+配角{len(supporting_role_images or [])}张+上一张剧情图{n_prev}张）")
                        # 构建参考图说明：主角 Image 0/1/2 + 配角 Image 3/4/... + 上一张剧情图 Image N
                        prefix_lines = []
                        n_prot = len(protagonist_reference_images or [])
                        if n_prot >= 1:
                            prefix_lines.append("Image 0: Front view portrait of the protagonist")
                        if n_prot >= 2:
                            prefix_lines.append("Image 1: Side view portrait of the protagonist")
                        if n_prot >= 3:
                            prefix_lines.append("Image 2: Back view portrait of the protagonist")
                        for sr in (supporting_role_references or []):
                            idx = sr.get("image_index", len(prefix_lines))
                            rn = sr.get("display_name", "") or sr.get("role_name", "")
                            cf = _clip_text(sr.get("core_features", ""), 80)
                            prefix_lines.append(f"Image {idx}: {rn} first appearance scene (may contain multiple characters). Identify this character by position in image. Core features (DO NOT MODIFY): {cf}")
                        if previous_scene_image_path:
                            prev_idx = len(prefix_lines)
                            prefix_lines.append(f"Image {prev_idx}: Previous scene image (for visual continuity - maintain consistent style, lighting, and character appearance).")
                        prefix_prompt = "\n".join(prefix_lines) + "\n\n"
                        full_prompt = prefix_prompt + prompt + f", aspect ratio {image_width}:{image_height}"
                        image_url = call_gemini_img2img(full_prompt, all_reference_images)
                    else:
                        print(f"⚠️ 当前模型 {model} 不支持多张参考图，使用文生图")
                        image_url = call_yunwu_image_api(size_prompt, style)
                else:
                    # 没有参考图，使用普通文生图
                    image_url = call_yunwu_image_api(size_prompt, style)
            except Exception as e:
                print(f"⚠️ yunwu.ai 生图失败，将尝试兜底（如已配置）：{str(e)}")
                image_url = None

            if not image_url:
                sd_base = IMAGE_GENERATION_CONFIG.get("stable_diffusion_base_url", "")
                if sd_base:
                    try:
                        print("🛟 使用 Stable Diffusion 作为兜底生图（yunwu 失败/无返回）")
                        # SD 兜底时，如果有主角参考图，使用第一张（正面）作为参考
                        sd_ref = protagonist_reference_images[0] if protagonist_reference_images else reference_image_url
                        image_url = call_stable_diffusion_api_with_size(prompt, image_width, image_height, style, reference_image_url=sd_ref)
                    except Exception as e:
                        print(f"⚠️ Stable Diffusion 兜底失败：{str(e)}")
        elif provider == "replicate":
            image_url = call_replicate_api(prompt, style)
        elif provider == "openai":
            image_url = call_dalle_api_with_size(prompt, f"{image_width}x{image_height}")
        elif provider == "stable_diffusion":
            image_url = call_stable_diffusion_api_with_size(prompt, image_width, image_height, style, reference_image_url=reference_image_url)
        elif provider == "comfyui":
            image_url = call_comfyui_api(prompt, style)
        else:
            print(f"⚠️ 不支持的图片生成服务：{provider}")
            return None
        
        if not image_url:
            return None
        
        # 如果启用缓存，下载图片到本地
        if use_cache and image_url:
            try:
                import hashlib
                from pathlib import Path
                
                MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024  # 10MB 防止超大文件拖垮内存/磁盘
                VALID_IMAGE_PREFIX = "image/"

                # 创建缓存目录
                IMAGE_CACHE_DIR = "image_cache"
                os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)
                
                # 生成缓存键（包含尺寸信息，避免不同尺寸的图片互相覆盖）
                # 新增：当存在“参考上一剧情图片/提示词”时，把参考信息纳入缓存键，避免误用旧缓存。
                ref_sig = (reference_image_prompt or reference_image_url or "").strip()
                if ref_sig:
                    ref_hash = hashlib.md5(ref_sig.encode("utf-8")).hexdigest()[:10]
                    cache_key_seed = f"{provider}_{style}_{scene_description}_{ref_hash}_{image_width}x{image_height}"
                else:
                    cache_key_seed = f"{provider}_{style}_{scene_description}_{image_width}x{image_height}"
                prompt_hash = hashlib.md5(cache_key_seed.encode()).hexdigest()
                cache_path = Path(IMAGE_CACHE_DIR) / f"{prompt_hash}.png"
                
                # 检查是否已缓存
                if cache_path.exists():
                    print(f"✅ 使用本地缓存的图片：{cache_path}")
                    if first_appearance_pending and game_id:
                        try:
                            for p in first_appearance_pending:
                                archive_supporting_role_first_appearance(game_id, p, str(cache_path), prompt)
                        except Exception as ar_err:
                            print(f"⚠️ 配角初登场建档失败：{ar_err}")
                    return {
                        "url": f"/image_cache/{prompt_hash}.png",
                        "prompt": prompt,
                        "style": style,
                        "width": image_width,
                        "height": image_height,
                        "cached": True
                    }
                
                # 检查image_url是否是相对路径（本地缓存路径）
                if image_url.startswith('/image_cache/') or image_url.startswith('image_cache/'):
                    # 如果image_url已经是相对路径，说明可能是从其他地方传入的缓存路径
                    # 检查对应的文件是否存在
                    import re
                    hash_match = re.search(r'([a-f0-9]{32})\.png', image_url)
                    if hash_match:
                        existing_hash = hash_match.group(1)
                        existing_path = Path(IMAGE_CACHE_DIR) / f"{existing_hash}.png"
                        if existing_path.exists():
                            # 如果文件存在，使用现有的hash，或者复制到新的hash
                            if existing_hash == prompt_hash:
                                print(f"✅ 使用现有的本地缓存图片：{existing_path}")
                                if first_appearance_pending and game_id:
                                    try:
                                        for p in first_appearance_pending:
                                            archive_supporting_role_first_appearance(game_id, p, str(existing_path), prompt)
                                    except Exception as ar_err:
                                        print(f"⚠️ 配角初登场建档失败：{ar_err}")
                                return {
                                    "url": f"/image_cache/{prompt_hash}.png",
                                    "prompt": prompt,
                                    "style": style,
                                    "width": image_width,
                                    "height": image_height,
                                    "cached": True
                                }
                            else:
                                # 复制到新的hash名称
                                import shutil
                                try:
                                    print(f"🔄 开始复制图片：{existing_path} -> {cache_path}")
                                    shutil.copy2(existing_path, cache_path)
                                    print(f"✅ 从现有缓存复制图片到新hash：{cache_path}")
                                    
                                    # 验证文件是否成功复制
                                    if not cache_path.exists():
                                        raise FileNotFoundError(f"复制后的文件不存在：{cache_path}")
                                    
                                    print(f"✅ 图片复制完成，文件大小：{cache_path.stat().st_size} 字节")
                                    if first_appearance_pending and game_id:
                                        try:
                                            for p in first_appearance_pending:
                                                archive_supporting_role_first_appearance(game_id, p, str(cache_path), prompt)
                                        except Exception as ar_err:
                                            print(f"⚠️ 配角初登场建档失败：{ar_err}")
                                    return {
                                        "url": f"/image_cache/{prompt_hash}.png",
                                        "prompt": prompt,
                                        "style": style,
                                        "width": image_width,
                                        "height": image_height,
                                        "cached": True
                                    }
                                except Exception as copy_err:
                                    print(f"❌ 复制图片时发生错误：{copy_err}")
                                    import traceback
                                    traceback.print_exc()
                                    print(f"⚠️ 复制失败，尝试使用原始缓存路径")
                                    if first_appearance_pending and game_id:
                                        try:
                                            for p in first_appearance_pending:
                                                archive_supporting_role_first_appearance(game_id, p, str(existing_path), prompt)
                                        except Exception as ar_err:
                                            print(f"⚠️ 配角初登场建档失败：{ar_err}")
                                    return {
                                        "url": f"/image_cache/{existing_hash}.png",
                                        "prompt": prompt,
                                        "style": style,
                                        "width": image_width,
                                        "height": image_height,
                                        "cached": True
                                    }
                    # 如果相对路径对应的文件不存在，抛出错误
                    raise ValueError(f"本地缓存路径对应的文件不存在：{image_url}")
                
                # 检查是否是完整的URL
                if not (image_url.startswith('http://') or image_url.startswith('https://')):
                    raise ValueError(f"无效的图片URL格式：{image_url}（需要完整的HTTP/HTTPS URL或本地缓存路径）")
                
                # 检查是否是私有Azure Blob Storage URL（无法直接下载）
                is_private_blob = 'blob.core.windows.net/private' in image_url or '/private/' in image_url
                if is_private_blob:
                    print(f"⚠️ 检测到私有Azure Blob Storage URL，无法直接下载")
                    print(f"   将直接返回URL，由前端处理：{image_url[:80]}...")
                    # 对于私有URL，直接返回URL，不尝试下载
                    return {
                        "url": image_url,
                        "prompt": prompt,
                        "style": style,
                        "width": image_width,
                        "height": image_height,
                        "cached": False  # 私有URL无法缓存
                    }
                
                # 下载图片到本地（带重试 + 流式写入，降低 image.pollinations.ai 等站点超时概率）
                print(f"📥 正在下载图片到本地缓存：{image_url[:80]}...")
                import time
                download_retries = int(os.getenv("IMAGE_DOWNLOAD_MAX_RETRIES", "3"))
                connect_timeout = float(os.getenv("IMAGE_DOWNLOAD_CONNECT_TIMEOUT", "10"))
                read_timeout = float(os.getenv("IMAGE_DOWNLOAD_READ_TIMEOUT", "60"))
                ua = os.getenv("IMAGE_DOWNLOAD_USER_AGENT", "DN-GameServer/1.0")

                response = None
                last_err = None
                for dl_attempt in range(download_retries):
                    try:
                        response = requests.get(
                            image_url,
                            timeout=(connect_timeout, read_timeout),
                            stream=True,
                            headers={"User-Agent": ua}
                        )
                        response.raise_for_status()
                        break
                    except requests.exceptions.HTTPError as e:
                        if e.response and e.response.status_code == 409:
                            # 409错误表示私有存储，无法公开访问
                            print(f"⚠️ 图片URL是私有存储，无法直接下载（409错误）")
                            print(f"   将直接返回URL，由前端处理：{image_url[:80]}...")
                            return {
                                "url": image_url,
                                "prompt": prompt,
                                "style": style,
                                "width": image_width,
                                "height": image_height,
                                "cached": False
                            }
                        raise
                    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                        last_err = e
                        if dl_attempt < download_retries - 1:
                            backoff = (1.5 * (2 ** dl_attempt)) + random.random()
                            print(f"⚠️ 图片下载超时/连接失败，{backoff:.1f}s 后重试（{dl_attempt+1}/{download_retries}）: {e}")
                            time.sleep(backoff)
                            continue
                        raise

                # 基础类型校验
                content_type = response.headers.get("Content-Type", "")
                if VALID_IMAGE_PREFIX not in content_type:
                    raise ValueError(f"响应类型异常：{content_type}")

                downloaded = 0
                with open(cache_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > MAX_DOWNLOAD_BYTES:
                            raise ValueError("图片过大，已终止下载（>10MB）")
                        f.write(chunk)
                
                print(f"✅ 图片已缓存到本地：{cache_path}")
                if first_appearance_pending and game_id:
                    try:
                        for p in first_appearance_pending:
                            archive_supporting_role_first_appearance(game_id, p, str(cache_path), prompt)
                    except Exception as ar_err:
                        print(f"⚠️ 配角初登场建档失败：{ar_err}")
                return {
                    "url": f"/image_cache/{prompt_hash}.png",
                    "prompt": prompt,
                    "style": style,
                    "width": image_width,
                    "height": image_height,
                    "cached": True
                }
            except Exception as cache_error:
                # 如果缓存过程中写入失败，确保不留空文件
                try:
                    if 'cache_path' in locals() and cache_path.exists():
                        cache_path.unlink()
                except Exception:
                    pass
                print(f"⚠️ 图片缓存失败，使用原始URL：{str(cache_error)}")
                # 缓存失败时返回原始URL
                return {
                    "url": image_url,
                    "prompt": prompt,
                    "style": style,
                    "width": image_width,
                    "height": image_height,
                    "cached": False
                }
        
        # 不使用缓存，直接返回OSS URL
        return {
            "url": image_url,
            "prompt": prompt,
            "style": style,
            "width": image_width,
            "height": image_height
        }
    except Exception as e:
        print(f"❌ 图片生成失败：{str(e)}")
        import traceback
        traceback.print_exc()
        return None

def validate_image_url(url: str) -> bool:
    """
    验证图片URL是否完整有效
    :param url: 待验证的URL
    :return: True if valid, False otherwise
    """
    if not url or not isinstance(url, str):
        return False
    
    # 基本格式检查：必须包含协议和域名
    if not url.startswith(('http://', 'https://')):
        return False
    
    # 检查是否包含域名（至少有一个点）
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if not parsed.netloc or '.' not in parsed.netloc:
            return False
        # 检查路径是否为空或至少包含一个字符
        if not parsed.path or len(parsed.path) < 1:
            # 对于OSS URL，路径可能很短，但至少应该有一些内容
            return False
        return True
    except Exception:
        return False

def fix_incomplete_url(url: str) -> str:
    """
    尝试修复不完整的URL
    :param url: 可能不完整的URL
    :return: 修复后的URL，如果无法修复则返回None
    """
    if not url:
        return None
    
    # 如果URL被截断（例如缺少文件扩展名），尝试添加
    # 但这种情况很难自动修复，因为不知道原始文件名
    
    # 检查URL是否以常见分隔符结尾（可能是被截断的）
    if url.endswith(('-', '_', '.')):
        # 移除末尾的分隔符
        url = url.rstrip('-_')
    
    # 如果URL看起来不完整（没有文件扩展名但应该有），尝试添加.png
    if url and '.' not in url.split('/')[-1] and '?' not in url.split('/')[-1]:
        # 对于OSS URL，如果最后一部分没有扩展名，可能是被截断了
        # 这种情况下我们无法修复，返回None
        pass
    
    return url if validate_image_url(url) else None

def validate_image_url(url: str) -> bool:
    """
    验证图片URL是否完整有效
    :param url: 待验证的URL
    :return: True if valid, False otherwise
    """
    if not url or not isinstance(url, str):
        return False
    
    # 基本格式检查
    if not url.startswith(('http://', 'https://')):
        return False
    
    # 检查是否包含域名和路径
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        if not parsed.netloc:  # 没有域名
            return False
        if not parsed.path or parsed.path == '/':  # 没有路径或只有根路径
            # 对于OSS URL，路径可能包含文件名，检查是否有文件扩展名
            if '.' not in url.split('/')[-1]:
                return False
        return True
    except Exception:
        return False

def fix_incomplete_url(url: str) -> str:
    """
    尝试修复不完整的URL
    :param url: 可能不完整的URL
    :return: 修复后的URL，如果无法修复则返回None
    """
    if not url:
        return None
    
    # 如果URL被截断（以不完整的方式结束），尝试修复
    # 常见问题：URL末尾缺少文件扩展名
    if url.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif')):
        return url  # 已经有扩展名
    
    # 如果URL看起来被截断（以UUID或ID结尾但没有扩展名）
    # 对于OSS URL，通常格式是：https://bucket.oss-region.aliyuncs.com/path/to/file.png
    if 'aliyuncs.com' in url or 'oss-' in url:
        # 尝试添加.png扩展名（最常见的图片格式）
        if not url.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif')):
            # 检查URL末尾是否有查询参数
            if '?' in url:
                # 有查询参数，在?之前添加扩展名
                base_url, query = url.split('?', 1)
                if not base_url.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif')):
                    return f"{base_url}.png?{query}"
            else:
                # 没有查询参数，直接添加扩展名
                return f"{url}.png"
    
    return url

def save_base64_image(data_uri: str, prompt: str, cache_key_suffix: str = None) -> str:
    """
    将base64 data URI保存为图片文件
    :param data_uri: base64 data URI，格式如 data:image/png;base64,iVBORw0KGgo...
    :param prompt: 提示词，用于生成文件名
    :param cache_key_suffix: 可选，参与缓存 key（如 reference 路径），避免不同游戏复用同一缓存
    :return: 保存的文件路径（相对路径），失败返回None
    """
    try:
        import base64
        from pathlib import Path
        
        # 清理可能的空白/引号包装
        data_uri = (data_uri or "").strip()
        if (data_uri.startswith('"') and data_uri.endswith('"')) or (data_uri.startswith("'") and data_uri.endswith("'")):
            data_uri = data_uri[1:-1].strip()
        
        # 解析data URI格式：data:image/png;base64,<base64_data>
        if not data_uri.startswith("data:image"):
            return None
        
        # 提取MIME类型和base64数据
        header, encoded = data_uri.split(',', 1)
        mime_match = re.search(r'data:image/([^;]+)', header)
        if not mime_match:
            return None
        
        image_format = mime_match.group(1)  # png, jpeg, webp等
        if image_format == 'jpeg':
            image_format = 'jpg'
        
        # 兼容多行/带空白的base64（模型输出可能自动换行）
        encoded = re.sub(r'\s+', '', encoded)
        
        # 解码base64数据
        try:
            image_data = base64.b64decode(encoded)
        except Exception as e:
            print(f"❌ base64解码失败：{str(e)}")
            return None

        # 过滤“空白/占位符”图片（常见：1x1 PNG base64 占位）
        # 例如：iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC...
        def _is_tiny_png_placeholder(data: bytes) -> bool:
            try:
                if not data or len(data) < 33:
                    return True
                if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                    return False
                # IHDR: length(4) + type(4) + data(13)
                ihdr_pos = 8
                if data[ihdr_pos + 4:ihdr_pos + 8] != b'IHDR':
                    return False
                width = int.from_bytes(data[ihdr_pos + 8:ihdr_pos + 12], "big", signed=False)
                height = int.from_bytes(data[ihdr_pos + 12:ihdr_pos + 16], "big", signed=False)
                if width <= 2 and height <= 2 and len(data) < 2048:
                    return True
                return False
            except Exception:
                return False

        if _is_tiny_png_placeholder(image_data):
            print("⚠️ 检测到 1x1/2x2 PNG 占位 base64，已丢弃该图片数据")
            return None
        
        # 创建缓存目录
        IMAGE_CACHE_DIR = "image_cache"
        os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)
        
        # 生成文件名（基于提示词+可选后缀的 hash，suffix 用于主角侧/背图按游戏区分缓存）
        key_str = f"{prompt}_{data_uri[:100]}"
        if cache_key_suffix:
            key_str += f"_{cache_key_suffix}"
        prompt_hash = hashlib.md5(key_str.encode()).hexdigest()
        cache_path = Path(IMAGE_CACHE_DIR) / f"{prompt_hash}.{image_format}"
        
        # 检查是否已存在
        if cache_path.exists():
            print(f"✅ 使用已存在的base64图片缓存：{cache_path}")
            return f"/image_cache/{prompt_hash}.{image_format}"
        
        # 保存图片
        with open(cache_path, 'wb') as f:
            f.write(image_data)
        
        print(f"✅ base64图片已保存到：{cache_path}")
        return f"/image_cache/{prompt_hash}.{image_format}"
        
    except Exception as e:
        print(f"❌ 保存base64图片失败：{str(e)}")
        import traceback
        traceback.print_exc()
        return None

def call_gemini_img2img(prompt: str, reference_image_path, additional_reference_images: List[str] = None, cache_key_suffix: str = None) -> str:
    """
    使用 gemini-2.5-flash-image 进行图生图，支持多张参考图
    :param prompt: 文本提示词
    :param reference_image_path: 参考图片路径（本地路径或 data URI），可以是字符串或字符串列表
    :param additional_reference_images: 额外的参考图片路径列表（可选）
    :param cache_key_suffix: 可选，参与 base64 缓存 key（如参考图路径），避免不同游戏复用同一缓存
    :return: 生成的图片 URL 或 base64 数据，失败返回 None
    """
    import time
    import base64
    
    api_key = IMAGE_GENERATION_CONFIG.get("yunwu_api_key")
    base_url = IMAGE_GENERATION_CONFIG.get("yunwu_base_url", "https://yunwu.ai/v1")
    model = IMAGE_GENERATION_CONFIG.get("yunwu_model", "gemini-2.5-flash-image")
    
    if not api_key:
        print("⚠️ gemini-2.5-flash-image 图生图：API Key未配置")
        return None
    
    # 检查模型是否为 gemini-2.5-flash-image
    if "gemini" not in model.lower() or "image" not in model.lower():
        print(f"⚠️ 当前模型 {model} 不是 gemini-2.5-flash-image，跳过图生图")
        return None
    
    # 处理参考图片：支持单个路径或路径列表
    reference_paths = []
    if isinstance(reference_image_path, (list, tuple)):
        reference_paths.extend(reference_image_path)
    elif reference_image_path:
        reference_paths.append(reference_image_path)
    
    # 添加额外的参考图片
    if additional_reference_images:
        if isinstance(additional_reference_images, (list, tuple)):
            reference_paths.extend(additional_reference_images)
        else:
            reference_paths.append(additional_reference_images)
    
    if not reference_paths:
        print("⚠️ 未提供参考图片")
        return None
    
    # 将所有参考图片转换为 base64 data URI
    image_data_uris = []
    for ref_path in reference_paths:
        image_data_uri = _ref_image_to_input(ref_path)
        if image_data_uri:
            image_data_uris.append(image_data_uri)
        else:
            print(f"⚠️ 无法加载参考图片：{ref_path}")
    
    if not image_data_uris:
        print("⚠️ 所有参考图片加载失败")
        return None
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # Gemini API 格式：multimodal content with multiple images
    # 根据 Gemini API 文档，支持多张参考图进行图生图
    # 构建 content 数组：先添加所有图片，最后添加文本提示
    content_items = []
    for image_data_uri in image_data_uris:
        content_items.append({
            "type": "image_url",
            "image_url": {
                "url": image_data_uri
            }
        })
    
    # 根据参考图数量调整提示词
    if len(image_data_uris) == 1:
        prompt_text = f"Edit this image: {prompt}\n\nReturn only the edited image as base64 data (data:image/png;base64,...) or image URL (https://...). Do not include any text, code blocks, or explanations."
    else:
        prompt_text = f"Based on these {len(image_data_uris)} reference images, generate a new image: {prompt}\n\nReturn only the generated image as base64 data (data:image/png;base64,...) or image URL (https://...). Do not include any text, code blocks, or explanations."
    
    content_items.append({
        "type": "text",
        "text": prompt_text
    })
    
    request_body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": content_items
            }
        ],
        "temperature": 0.1,
        "max_tokens": 4000
    }
    
    request_timeout = int(os.getenv("YUNWU_IMAGE_TIMEOUT_SECONDS", "180"))
    min_interval = float(os.getenv("YUNWU_MIN_INTERVAL_SECONDS", "12"))
    
    try:
        # 跨线程限速
        global _YUNWU_LAST_CALL_TS
        with _YUNWU_RATE_LOCK:
            now = time.time()
            delta = now - _YUNWU_LAST_CALL_TS
            if delta < min_interval:
                sleep_s = (min_interval - delta) + random.random() * 0.5
                print(f"⏳ gemini 图生图限速：等待 {sleep_s:.1f}s")
                time.sleep(sleep_s)
            _YUNWU_LAST_CALL_TS = time.time()
        
        print(f"🔄 调用 gemini-2.5-flash-image 图生图 API（{len(image_data_uris)}张参考图）...")
        print(f"   提示词: {prompt[:100]}...")
        ref_paths_str = ", ".join([ref[:50] + "..." if len(ref) > 50 else ref for ref in reference_paths])
        print(f"   参考图: {ref_paths_str}")
        
        response = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=request_body,
            timeout=request_timeout
        )
        
        if response.status_code != 200:
            error_msg = ""
            try:
                error_body = response.json()
                if isinstance(error_body, dict):
                    error_obj = error_body.get("error", {})
                    if isinstance(error_obj, dict):
                        error_msg = error_obj.get("message", "")
                    else:
                        error_msg = str(error_obj)
                else:
                    error_msg = str(error_body)
            except:
                error_msg = response.text[:200]
            
            print(f"❌ gemini-2.5-flash-image 图生图 API 错误 {response.status_code}: {error_msg}")
            return None
        
        # 解析响应（复用 call_yunwu_image_api 的解析逻辑）
        result = response.json()
        
        # 尝试从响应中提取图片
        # 使用与 call_yunwu_image_api 相同的解析策略
        def _extract_image_from_response(obj) -> str:
            try:
                if not isinstance(obj, dict):
                    return ""
                # 顶层直接给 url
                for k in ("image_url", "url"):
                    v = obj.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
                # choices[0].message.content
                choices = obj.get("choices", [])
                if choices and len(choices) > 0:
                    message = choices[0].get("message", {})
                    content = message.get("content", "")
                    if isinstance(content, str) and content.strip():
                        # 检查是否是 base64 或 URL
                        if content.startswith("data:image") or content.startswith("http"):
                            return content.strip()
                        # 尝试从文本中提取 base64
                        import re
                        base64_match = re.search(r'data:image/[^;]+;base64,([A-Za-z0-9+/=\s]+)', content)
                        if base64_match:
                            return f"data:image/png;base64,{base64_match.group(1).strip()}"
                return ""
            except Exception as e:
                print(f"⚠️ 解析响应时出错: {str(e)}")
                return ""
        
        image_result = _extract_image_from_response(result)
        if image_result:
            # 如果是 base64，保存到本地缓存（cache_key_suffix 用于主角侧/背图按游戏区分）
            if image_result.startswith("data:image"):
                saved_path = save_base64_image(image_result, prompt, cache_key_suffix=cache_key_suffix)
                if saved_path:
                    return saved_path
            return image_result
        
        print(f"⚠️ gemini-2.5-flash-image 图生图响应中未找到图片数据")
        return None
        
    except Exception as e:
        print(f"❌ gemini-2.5-flash-image 图生图调用异常: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def call_yunwu_image_api(prompt: str, style: str) -> str:
    """调用yunwu.ai图片生成API（带重试机制处理速率限制）"""
    import time
    
    api_key = IMAGE_GENERATION_CONFIG.get("yunwu_api_key")
    base_url = IMAGE_GENERATION_CONFIG.get("yunwu_base_url", "https://yunwu.ai/v1")
    model = IMAGE_GENERATION_CONFIG.get("yunwu_model", "sora_image")
    
    if not api_key:
        raise ValueError("yunwu.ai API Key未配置")
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # 调用yunwu.ai的图片生成API（使用chat/completions接口）
    # 注意：gemini-2.5-flash-image 模型可能不支持 response_format 参数
    # 注意：不同模型可能有不同的返回格式，需要兼容处理
    
    # 根据模型类型调整提示词
    if "gemini" in model.lower() and "image" in model.lower():
        # Gemini 图片生成模型：尝试使用英文提示词（模型可能是英文训练的）
        # 尝试不使用 system message，只使用 user message，更简洁直接
        request_body = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": f"Generate an image based on this description: {prompt}\n\nReturn only the image as base64 data (data:image/png;base64,...) or image URL (https://...). Do not include any text, code blocks, or explanations."
                }
            ],
            "temperature": 0.1,
            "max_tokens": 4000
        }
    elif "gemini" in model.lower():
        # 其他 Gemini 模型
        system_content = "你是一个图片生成模型。直接生成图片并返回base64数据或URL，不要任何文字说明或代码块。"
        user_content = f"生成图片：{prompt}\n\n返回格式：data:image/png;base64,<base64数据> 或 https://图片URL"
        request_body = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": system_content
                },
                {
                    "role": "user",
                    "content": user_content
                }
            ],
            "temperature": 0.1,
            "max_tokens": 4000
        }
    else:
        # 其他模型的提示词
        system_content = "你是一个图片生成API。用户会提供图片描述，你必须生成图片并返回图片URL或base64数据。优先返回base64格式的图片数据（data:image/png;base64,...），如果没有则返回图片URL。"
        user_content = f"请生成一张图片，描述：{prompt}\n\n请返回图片URL或base64格式的图片数据。"
        request_body = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": system_content
                },
                {
                    "role": "user",
                    "content": user_content
                }
            ],
            "temperature": 0.3,
            "max_tokens": 2000
        }
    
    # 注意：gemini-2.5-flash-image 模型不支持 response_format 参数，不要添加
    # 如果模型是 sora_image 或其他支持JSON模式的模型，可以尝试添加
    # 但 gemini-2.5-flash-image 不支持，会导致400错误
    
    # 可配置：超时/最小间隔/重试次数（避免长时间卡住 + 降低 429 概率）
    # 🔧 修复：增加默认超时时间到180秒，因为图片生成通常需要较长时间
    request_timeout = int(os.getenv("YUNWU_IMAGE_TIMEOUT_SECONDS", "180"))  # 从90秒增加到180秒
    min_interval = float(os.getenv("YUNWU_MIN_INTERVAL_SECONDS", "12"))
    max_retries = int(os.getenv("YUNWU_IMAGE_MAX_RETRIES", "3"))
    for attempt in range(max_retries):
        try:
            # 跨线程限速：保证相邻请求之间至少间隔 min_interval 秒
            global _YUNWU_LAST_CALL_TS
            with _YUNWU_RATE_LOCK:
                now = time.time()
                delta = now - _YUNWU_LAST_CALL_TS
                if delta < min_interval:
                    sleep_s = (min_interval - delta) + random.random() * 0.5
                    print(f"⏳ yunwu.ai 限速：等待 {sleep_s:.1f}s（最小间隔 {min_interval}s）")
                    time.sleep(sleep_s)
                _YUNWU_LAST_CALL_TS = time.time()

            # 🔍 调试：打印实际发送的请求内容
            print(f"🔍 ========== 发送给API的请求内容 ==========")
            print(f"🔍 API端点: {base_url}/chat/completions")
            print(f"🔍 模型: {model}")
            try:
                import json
                request_str = json.dumps(request_body, ensure_ascii=False, indent=2)
                # 如果请求太长，只打印前2000字符
                if len(request_str) > 2000:
                    print(f"📤 请求内容（前2000字符）:\n{request_str[:2000]}")
                    print(f"\n📤 请求内容（后500字符）:\n{request_str[-500:]}")
                else:
                    print(f"📤 请求内容:\n{request_str}")
            except Exception as e:
                print(f"⚠️ 无法序列化请求内容: {str(e)}")
                print(f"📤 请求内容: {str(request_body)[:1000]}")
            print(f"🔍 ==========================================")
            
            # 图片生成可能耗时，但不应无限期阻塞
            # 🔧 修复：添加超时日志，方便调试
            print(f"⏱️ 发送图片生成请求（超时时间：{request_timeout}秒）...")
            start_request_time = time.time()
            response = requests.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=request_body,
                timeout=request_timeout
            )
            elapsed_time = time.time() - start_request_time
            print(f"✅ API请求完成，耗时：{elapsed_time:.2f}秒")
            
            # 先检查HTTP状态码，区分不同类型的错误
            if response.status_code == 400:
                # 400错误：请求格式错误
                try:
                    error_body = response.json()
                    error_message = ""
                    if isinstance(error_body, dict):
                        error_obj = error_body.get("error", {})
                        if isinstance(error_obj, dict):
                            error_message = error_obj.get("message", "")
                        else:
                            error_message = str(error_obj)
                    else:
                        error_message = str(error_body)
                    
                    print(f"❌ yunwu.ai图片生成API请求格式错误（400）：{error_message}")
                    
                    # 检查是否是JSON mode不支持的错误
                    if "JSON mode is not enabled" in error_message or "response_format" in error_message:
                        print(f"💡 提示：模型 {model} 不支持 response_format 参数")
                        # 移除 response_format 参数后重试（如果还有重试机会）
                        if attempt < max_retries - 1:
                            # 确保 request_body 中没有 response_format
                            if "response_format" in request_body:
                                request_body.pop("response_format")
                                print(f"   移除 response_format 参数后重试（尝试 {attempt + 2}/{max_retries}）...")
                                time.sleep(2)  # 等待2秒后重试
                                continue
                    
                    # 检查是否是API格式错误（messages字段不存在）
                    if "Unknown name" in error_message or "Cannot find field" in error_message or "messages" in error_message:
                        print(f"💡 提示：API请求格式可能不正确，模型 {model} 可能使用不同的API格式")
                        print(f"💡 当前使用的格式：chat/completions（标准OpenAI格式）")
                        print(f"💡 建议：")
                        print(f"   1. 检查 yunwu.ai API 文档，确认 {model} 模型的正确调用方式")
                        print(f"   2. 确认模型名称是否正确：{model}")
                        print(f"   3. 可能需要使用不同的API端点或请求格式")
                        # 400错误不应该重试（格式错误重试也没用），直接抛出
                        response.raise_for_status()
                    
                    # 其他400错误直接抛出
                    response.raise_for_status()
                except Exception as parse_error:
                    print(f"❌ 无法解析400错误响应：{str(parse_error)}")
                    response.raise_for_status()
            
            elif response.status_code == 429:
                # 尝试从响应头获取重试时间和详细信息
                retry_after = response.headers.get('Retry-After')
                rate_limit_info = {}
                
                # 尝试解析响应体获取更多信息
                try:
                    error_body = response.json()
                    if isinstance(error_body, dict):
                        rate_limit_info = error_body
                        print(f"🔍 速率限制详细信息：{json.dumps(rate_limit_info, ensure_ascii=False)}")
                except:
                    error_text = response.text[:200] if hasattr(response, 'text') else ""
                    if error_text:
                        print(f"🔍 速率限制响应内容：{error_text}")
                
                # 检查响应头中的速率限制信息
                rate_limit_headers = {
                    'X-RateLimit-Limit': response.headers.get('X-RateLimit-Limit'),
                    'X-RateLimit-Remaining': response.headers.get('X-RateLimit-Remaining'),
                    'X-RateLimit-Reset': response.headers.get('X-RateLimit-Reset'),
                    'Retry-After': retry_after
                }
                if any(rate_limit_headers.values()):
                    print(f"🔍 速率限制响应头：{json.dumps({k: v for k, v in rate_limit_headers.items() if v}, ensure_ascii=False)}")
                
                # Retry-After 可能是秒数（整数）或 HTTP-date（如 RFC 7231 指定）
                wait_time = None
                if retry_after:
                    retry_after_raw = str(retry_after).strip()
                    # 先尝试按“秒数”解析
                    try:
                        wait_time = int(retry_after_raw)
                        if wait_time < 0:
                            wait_time = 0
                        print(f"⚠️ 遇到速率限制（429），API建议等待 {wait_time} 秒后重试（尝试 {attempt + 1}/{max_retries}）")
                    except (TypeError, ValueError):
                        # 再尝试按 HTTP-date 解析
                        try:
                            from email.utils import parsedate_to_datetime
                            from datetime import datetime, timezone
                            dt = parsedate_to_datetime(retry_after_raw)
                            if dt is not None:
                                if dt.tzinfo is None:
                                    dt = dt.replace(tzinfo=timezone.utc)
                                now = datetime.now(timezone.utc)
                                wait_seconds = int((dt.astimezone(timezone.utc) - now).total_seconds())
                                wait_time = max(0, wait_seconds)
                                print(f"⚠️ 遇到速率限制（429），API建议等待 {wait_time} 秒后重试（尝试 {attempt + 1}/{max_retries}）")
                        except Exception:
                            wait_time = None
                
                if wait_time is None:
                    # 如果 Retry-After 不存在或无法解析，使用指数退避：10s, 20s, 40s
                    wait_time = 10 * (2 ** attempt)
                    if retry_after:
                        print(f"⚠️ 遇到速率限制（429），但 Retry-After 无法解析（{retry_after!r}），改用指数退避等待 {wait_time} 秒后重试（尝试 {attempt + 1}/{max_retries}）")
                    else:
                        print(f"⚠️ 遇到速率限制（429），等待 {wait_time} 秒后重试（尝试 {attempt + 1}/{max_retries}）")
                
                print(f"💡 可能的原因：")
                print(f"   1. yunwu.ai 最近调整了速率限制策略")
                print(f"   2. API配额已用完（免费额度用尽）")
                print(f"   3. 账户级别变化（可能降级到免费版）")
                print(f"   4. 使用量增加导致触发限制")
                print(f"   5. 图片生成API的限制比文本生成更严格")
                print(f"💡 建议：")
                print(f"   - 检查 yunwu.ai 账户状态和配额")
                print(f"   - 考虑切换到其他图片生成服务（ComfyUI、Replicate等）")
                print(f"   - 增加请求间隔时间")
                
                # 如果还有重试机会，等待后继续
                if attempt < max_retries - 1:
                    time.sleep(wait_time)
                    continue
                else:
                    # 最后一次尝试也失败，抛出异常
                    response.raise_for_status()
            
            # 其他HTTP错误直接抛出
            response.raise_for_status()
            
            # 如果成功，解析响应（兼容：返回体不是 JSON / 结构变化）
            try:
                result = response.json()
                # 打印响应状态码和基本信息
                print(f"✅ yunwu.ai API响应成功（状态码: {response.status_code}）")
            except Exception as e:
                text_preview = (response.text or "")[:500]
                print(f"⚠️ yunwu.ai 返回非JSON内容，无法解析：{text_preview}")
                print(f"⚠️ 解析错误：{str(e)}")
                return None

            # 解析策略0：优先从“结构化字段”提取（避免只依赖 choices[0].message.content）
            def _extract_from_structured(obj) -> str:
                try:
                    if not isinstance(obj, dict):
                        return ""
                    # 顶层直接给 url
                    for k in ("image_url", "url"):
                        v = obj.get(k)
                        if isinstance(v, str) and v.strip():
                            return v.strip()
                    # 常见：images: [<base64>, ...]
                    images = obj.get("images")
                    if isinstance(images, list) and images:
                        first = images[0]
                        if isinstance(first, str) and first.strip():
                            s = first.strip()
                            if s.startswith("data:image"):
                                return save_base64_image(s, prompt) or ""
                            return save_base64_image(f"data:image/png;base64,{s}", prompt) or ""
                    # 常见：data: {url:...} 或 data: [{url:...}]
                    data = obj.get("data")
                    if isinstance(data, dict):
                        for k in ("url", "image_url"):
                            v = data.get(k)
                            if isinstance(v, str) and v.strip():
                                return v.strip()
                        for k in ("b64_json", "base64", "image_base64"):
                            v = data.get(k)
                            if isinstance(v, str) and v.strip():
                                return save_base64_image(f"data:image/png;base64,{v.strip()}", prompt) or ""
                    if isinstance(data, list) and data:
                        for item in data:
                            if not isinstance(item, dict):
                                continue
                            for k in ("url", "image_url"):
                                v = item.get(k)
                                if isinstance(v, str) and v.strip():
                                    return v.strip()
                            for k in ("b64_json", "base64", "image_base64"):
                                v = item.get(k)
                                if isinstance(v, str) and v.strip():
                                    return save_base64_image(f"data:image/png;base64,{v.strip()}", prompt) or ""
                    return ""
                except Exception:
                    return ""

            structured = _extract_from_structured(result)
            if structured:
                return structured

            # 打印完整的响应结构用于调试
            print(f"🔍 yunwu.ai API完整响应结构：")
            print(f"   - 响应类型: {type(result)}")
            print(f"   - 响应键: {list(result.keys()) if isinstance(result, dict) else 'N/A'}")
            
            # 🔍 检查响应中的其他顶层字段（可能包含图片数据）
            print(f"🔍 检查响应中的其他顶层字段...")
            for key in ["data", "image", "image_url", "url", "images", "output", "result"]:
                if key in result:
                    value = result[key]
                    value_type = type(value).__name__
                    if isinstance(value, str):
                        print(f"   - result['{key}']: {value_type}, 长度={len(value)}, 前200字符={value[:200]}")
                        if value.startswith("data:image") or value.startswith("http://") or value.startswith("https://"):
                            print(f"💡 在result['{key}']中发现可能的图片数据！")
                            if value.startswith("data:image"):
                                saved_path = save_base64_image(value, prompt)
                                if saved_path:
                                    return saved_path
                            elif value.startswith("http://") or value.startswith("https://"):
                                return value
                    else:
                        print(f"   - result['{key}']: {value_type} = {str(value)[:200]}")
            
            # 🔍 检查 usage 字段（可能包含 token 信息，用于确认API确实返回了内容）
            if "usage" in result:
                usage = result["usage"]
                print(f"🔍 API使用情况: {usage}")
                if isinstance(usage, dict):
                    total_tokens = usage.get("total_tokens", 0)
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)
                    print(f"   - 总tokens: {total_tokens}, 输入tokens: {prompt_tokens}, 输出tokens: {completion_tokens}")
                    if completion_tokens > 0:
                        print(f"💡 API确实返回了 {completion_tokens} 个输出tokens，说明有内容返回！")
            
            choices = result.get("choices", [])
            print(f"   - choices数量: {len(choices) if choices else 0}")
            
            if not choices or len(choices) == 0:
                print(f"⚠️ yunwu.ai返回的响应中没有choices字段或choices为空")
                try:
                    import json
                    print(f"📄 完整响应内容: {json.dumps(result, ensure_ascii=False, indent=2)[:1000]}")
                except:
                    print(f"📄 完整响应内容: {str(result)[:1000]}")
                return None
            
            message = choices[0].get("message", {})
            print(f"   - message类型: {type(message)}")
            print(f"   - message键: {list(message.keys()) if isinstance(message, dict) else 'N/A'}")
            
            # 🔍 检查 choices[0] 中的 finish_reason 字段
            if "finish_reason" in choices[0]:
                finish_reason = choices[0]["finish_reason"]
                print(f"🔍 finish_reason: {finish_reason}")
                if finish_reason and finish_reason != "stop":
                    print(f"⚠️ finish_reason 不是 'stop'，可能是 '{finish_reason}'")
                    if finish_reason == "length":
                        print(f"💡 可能原因：输出被截断（max_tokens 限制）")
                    elif finish_reason == "content_filter":
                        print(f"💡 可能原因：内容被过滤")
                    elif finish_reason == "function_call":
                        print(f"💡 可能原因：触发了函数调用")
            
            if not message:
                print(f"⚠️ yunwu.ai返回的choices[0]中没有message字段")
                print(f"📄 choices[0]内容: {json.dumps(choices[0], ensure_ascii=False, indent=2)[:1000]}")
                return None
            
            content = message.get("content", "")
            print(f"   - content类型: {type(content)}")
            print(f"   - content长度: {len(content) if content else 0}")
            print(f"   - content前100字符: {str(content)[:100] if content else '(空)'}")
            
            # 🔍 详细调试：如果content很短，打印完整内容（包括不可见字符）
            if content and len(content) < 100:
                print(f"🔍 content完整内容（repr格式，显示所有字符）: {repr(content)}")
                print(f"🔍 content完整内容（原始格式）: {content}")
            
            # 🔍 检查message中的所有字段（可能有其他字段包含图片数据）
            print(f"🔍 检查message中的所有字段...")
            if isinstance(message, dict):
                for key, value in message.items():
                    if key == "content":
                        continue  # content已经处理过了
                    value_type = type(value).__name__
                    if isinstance(value, str):
                        value_preview = value[:200] if len(value) > 200 else value
                        print(f"   - message['{key}']: {value_type}, 长度={len(value)}, 内容={repr(value_preview)}")
                        # 如果这个字段看起来像图片数据，尝试提取
                        if value.startswith("data:image") or value.startswith("http://") or value.startswith("https://"):
                            print(f"💡 在message['{key}']中发现可能的图片数据！")
                            if value.startswith("data:image"):
                                saved_path = save_base64_image(value, prompt)
                                if saved_path:
                                    return saved_path
                            elif value.startswith("http://") or value.startswith("https://"):
                                return value
                    elif isinstance(value, (dict, list)):
                        print(f"   - message['{key}']: {value_type}, 内容={str(value)[:200]}")
                        # 递归检查嵌套结构
                        if isinstance(value, dict):
                            for sub_key, sub_value in value.items():
                                if isinstance(sub_value, str) and (sub_value.startswith("data:image") or sub_value.startswith("http")):
                                    print(f"💡 在message['{key}']['{sub_key}']中发现可能的图片数据！")
                                    if sub_value.startswith("data:image"):
                                        saved_path = save_base64_image(sub_value, prompt)
                                        if saved_path:
                                            return saved_path
                                    elif sub_value.startswith("http://") or sub_value.startswith("https://"):
                                        return sub_value
                    else:
                        print(f"   - message['{key}']: {value_type} = {value}")
            
            # 🔍 检查choices[0]中的所有字段（可能有其他字段包含图片数据）
            print(f"🔍 检查choices[0]中的所有字段...")
            if isinstance(choices[0], dict):
                for key, value in choices[0].items():
                    if key in ["index", "message", "finish_reason"]:
                        continue  # 这些字段已经处理过了
                    value_type = type(value).__name__
                    if isinstance(value, str):
                        value_preview = value[:200] if len(value) > 200 else value
                        print(f"   - choices[0]['{key}']: {value_type}, 长度={len(value)}, 内容={repr(value_preview)}")
                        if value.startswith("data:image") or value.startswith("http://") or value.startswith("https://"):
                            print(f"💡 在choices[0]['{key}']中发现可能的图片数据！")
                            if value.startswith("data:image"):
                                saved_path = save_base64_image(value, prompt)
                                if saved_path:
                                    return saved_path
                            elif value.startswith("http://") or value.startswith("https://"):
                                return value
                    else:
                        print(f"   - choices[0]['{key}']: {value_type} = {str(value)[:200]}")
            
            # 兼容模型把结果包在代码块/引号里（尤其是 data:image/... 或 JSON）
            content_clean = (content or "").strip()
            
            # 记录原始内容用于调试
            original_content = content_clean
            
            if not content_clean:
                print(f"⚠️ yunwu.ai返回的content字段为空")
                try:
                    import json
                    print(f"📄 完整message内容: {json.dumps(message, ensure_ascii=False, indent=2)[:1000]}")
                    print(f"📄 完整choices[0]内容: {json.dumps(choices[0], ensure_ascii=False, indent=2)[:1000]}")
                except:
                    print(f"📄 完整message内容: {str(message)[:1000]}")
                    print(f"📄 完整choices[0]内容: {str(choices[0])[:1000]}")
                # 检查是否有其他字段包含图片数据
                if isinstance(message, dict):
                    for key, value in message.items():
                        if key != "content" and isinstance(value, str) and len(value) > 50:
                            print(f"💡 发现message中的其他字段 '{key}'，长度: {len(value)}，前100字符: {value[:100]}")
                # 检查是否有 finish_reason 字段，可能说明为什么没有内容
                if isinstance(message, dict) and "finish_reason" in message:
                    finish_reason = message.get("finish_reason")
                    print(f"💡 finish_reason: {finish_reason}")
                    if finish_reason and finish_reason != "stop":
                        print(f"⚠️ 注意：finish_reason 不是 'stop'，可能是 '{finish_reason}'，这可能导致内容为空")
                return None
            
            # 保守地去除引号和代码块，避免误删有效内容
            # 先记录去除前的状态
            before_cleaning = content_clean
            print(f"🔍 开始清理content，原始长度: {len(content_clean)} 字符")
            if len(content_clean) <= 200:
                print(f"🔍 原始content内容: {repr(content_clean)}")
            
            # 策略1：先去掉最外层引号（但要确保去除后还有内容）
            for i in range(2):
                if len(content_clean) >= 2:
                    if (content_clean.startswith('"') and content_clean.endswith('"')) or (content_clean.startswith("'") and content_clean.endswith("'")):
                        # 检查去除引号后是否还有内容（至少1个字符）
                        temp_clean = content_clean[1:-1].strip()
                        if len(temp_clean) > 0:  # 只有去除后还有内容才执行
                            print(f"🔍 步骤{i+1}: 去除引号，长度从 {len(content_clean)} 变为 {len(temp_clean)}")
                            content_clean = temp_clean
                        else:
                            # 去除后为空，说明可能是空引号，保留原内容
                            print(f"🔍 步骤{i+1}: 去除引号后为空，保留原内容")
                            break
            
            # 策略2：剥离 ``` fenced code block（但要确保去除后还有内容）
            if content_clean.startswith("```"):
                print(f"🔍 检测到代码块标记，开始提取内容...")
                fence_match = re.match(r"^```(?:[a-zA-Z0-9_-]+)?\s*([\s\S]*?)\s*```$", content_clean, re.DOTALL)
                if fence_match:
                    extracted = (fence_match.group(1) or "").strip()
                    if len(extracted) > 0:  # 只有提取到内容才使用
                        print(f"🔍 从代码块中提取内容，长度从 {len(content_clean)} 变为 {len(extracted)}")
                        content_clean = extracted
                    else:
                        # 如果提取为空，说明代码块是空的，保留原内容
                        print(f"🔍 代码块提取后为空，保留原内容")
                else:
                    # 退化处理：按行移除首尾 fence（但要确保去除后还有内容）
                    lines = content_clean.splitlines()
                    if len(lines) >= 2 and lines[0].strip().startswith("```"):
                        if lines[-1].strip().startswith("```"):
                            # 移除首尾两行
                            remaining_lines = lines[1:-1]
                            temp_clean = "\n".join(remaining_lines).strip()
                            if len(temp_clean) > 0:  # 只有去除后还有内容才使用
                                print(f"🔍 按行移除代码块标记，长度从 {len(content_clean)} 变为 {len(temp_clean)}")
                                content_clean = temp_clean
                            else:
                                print(f"🔍 按行移除代码块标记后为空，保留原内容")
                        else:
                            # 只移除第一行
                            remaining_lines = lines[1:]
                            temp_clean = "\n".join(remaining_lines).strip()
                            if len(temp_clean) > 0:  # 只有去除后还有内容才使用
                                print(f"🔍 移除第一行代码块标记，长度从 {len(content_clean)} 变为 {len(temp_clean)}")
                                content_clean = temp_clean
                            else:
                                print(f"🔍 移除第一行代码块标记后为空，保留原内容")
            
            # 策略3：fence 解包后再做一次引号去除（但要确保去除后还有内容）
            for i in range(2):
                if len(content_clean) >= 2:
                    if (content_clean.startswith('"') and content_clean.endswith('"')) or (content_clean.startswith("'") and content_clean.endswith("'")):
                        temp_clean = content_clean[1:-1].strip()
                        if len(temp_clean) > 0:  # 只有去除后还有内容才执行
                            print(f"🔍 代码块解包后再次去除引号，长度从 {len(content_clean)} 变为 {len(temp_clean)}")
                            content_clean = temp_clean
                        else:
                            print(f"🔍 代码块解包后去除引号为空，停止处理")
                            break
            
            print(f"🔍 清理完成，最终长度: {len(content_clean)} 字符")
            
            # 检查去除引号和代码块后是否变成空字符串
            if not content_clean:
                print(f"⚠️ yunwu.ai返回的content字段在去除引号/代码块后为空")
                print(f"📄 原始content内容: {repr(original_content[:200])}")
                print(f"📄 原始content长度: {len(original_content)} 字符")
                
                # 检查是否是空的代码块（说明API没有生成图片）
                # 使用正则表达式匹配各种形式的空代码块
                empty_code_block_pattern = re.match(r'^```(?:\w+)?\s*\n?\s*```$', original_content.strip(), re.MULTILINE)
                is_empty_code_block = (
                    empty_code_block_pattern is not None or
                    original_content.strip() in ["```", "```\n```", "```\n\n```", "```json\n```", "```json\n\n```"] or
                    (original_content.strip().startswith("```") and 
                     original_content.strip().endswith("```") and 
                     len(original_content.strip().replace("```", "").strip()) == 0)
                )
                
                if is_empty_code_block:
                    print(f"⚠️ 检测到空的代码块，说明yunwu.ai API没有生成图片数据")
                    print(f"💡 可能的原因：")
                    print(f"   1. gemini-2.5-flash-image 模型可能不支持图片生成，或需要不同的调用方式")
                    print(f"   2. API密钥权限不足，无法使用图片生成功能")
                    print(f"   3. 提示词格式不符合模型要求")
                    print(f"   4. 模型可能返回了错误信息，但被包装在空代码块中")
                    
                    # 检查finish_reason字段
                    if isinstance(message, dict) and "finish_reason" in message:
                        finish_reason = message.get("finish_reason")
                        print(f"💡 finish_reason: {finish_reason}")
                        if finish_reason and finish_reason != "stop":
                            print(f"⚠️ finish_reason 不是 'stop'，可能是 '{finish_reason}'，这可能导致内容为空")
                    
                    # 检查choices[0]中是否有其他字段包含图片数据
                    print(f"🔍 检查choices[0]中的其他字段...")
                    if isinstance(choices[0], dict):
                        for key, value in choices[0].items():
                            if key not in ["index", "message", "finish_reason"]:
                                print(f"   - {key}: {type(value)} = {str(value)[:100] if isinstance(value, str) else value}")
                    
                    # 检查message中是否有其他字段包含图片数据
                    print(f"🔍 检查message中的其他字段...")
                    if isinstance(message, dict):
                        for key, value in message.items():
                            if key not in ["role", "content"]:
                                print(f"   - {key}: {type(value)} = {str(value)[:100] if isinstance(value, str) else value}")
                                # 如果找到可能的图片URL或base64数据
                                if isinstance(value, str) and (value.startswith("http") or value.startswith("data:image")):
                                    print(f"💡 在message['{key}']中发现可能的图片数据！")
                                    return value
                    
                    # 检查完整响应中是否有其他字段包含图片数据
                    print(f"🔍 检查完整响应中的其他字段...")
                    for key in ["data", "image", "image_url", "url", "images"]:
                        if key in result:
                            value = result[key]
                            print(f"   - {key}: {type(value)} = {str(value)[:200] if isinstance(value, str) else value}")
                            if isinstance(value, str) and (value.startswith("http") or value.startswith("data:image")):
                                print(f"💡 在result['{key}']中发现可能的图片数据！")
                                return value
                    
                    print(f"💡 建议：")
                    print(f"   - 检查.env文件中的yunwu_model配置，尝试切换到其他模型（如 sora_image）")
                    print(f"   - 检查yunwu.ai API文档，确认gemini-2.5-flash-image模型是否支持图片生成")
                    print(f"   - 如果API不支持图片生成，可以切换到其他图片生成服务")
                    return None
                
                print(f"💡 可能的原因：")
                print(f"   1. API返回的内容被错误地包装在引号或代码块中，去除后内容丢失")
                print(f"   2. API返回的content字段本身就是空字符串或只包含空白字符")
                print(f"   3. 代码块解析逻辑可能过于激进，误删了有效内容")
                print(f"💡 建议：")
                print(f"   - 检查原始content内容（见上方日志）")
                print(f"   - 如果原始content不为空，可能需要调整引号/代码块去除逻辑")
                print(f"   - 检查yunwu.ai API返回的完整响应结构")
                # 如果原始内容不为空，尝试直接使用原始内容（可能包含有效的图片数据）
                if original_content and len(original_content) > 10:
                    print(f"💡 尝试直接使用原始content内容进行解析...")
                    content_clean = original_content
                else:
                    return None
            
            print(f"🔍 yunwu.ai返回的原始内容（前500字符）：{content_clean[:500]}")
            if len(content_clean) > 500:
                print(f"🔍 yunwu.ai返回的原始内容（完整长度：{len(content_clean)}字符）")
            
            # 解析策略1：尝试解析JSON格式
            try:
                import json
                content_json = json.loads(content_clean)
                if "image_url" in content_json:
                    print(f"✅ 从JSON中提取到image_url：{content_json['image_url']}")
                    return content_json["image_url"]
                elif "url" in content_json:
                    print(f"✅ 从JSON中提取到url：{content_json['url']}")
                    return content_json["url"]
            except json.JSONDecodeError:
                pass  # 不是JSON格式，继续其他解析方式
            
            # 解析策略2：从markdown格式中提取图片URL或base64数据
            # 匹配格式：![image](https://...) 或 ![alt text](url) 或 ![image](data:image/...)
            # 改进正则：支持HTTP/HTTPS URL和data URI，base64数据可能很长，需要匹配到最后的右括号
            # 对于base64，匹配所有非右括号的字符（包括换行符等），直到遇到右括号
            markdown_image_pattern = r'!\[.*?\]\((https?://[^\s\)]+|data:image/[^\)]+)\)'
            markdown_matches = re.findall(markdown_image_pattern, content_clean, re.DOTALL)
            if markdown_matches:
                image_data = markdown_matches[0]  # 取第一个匹配的内容
                
                # 检查是否是base64 data URI
                if image_data.startswith("data:image"):
                    print(f"✅ 从markdown格式中提取到base64图片数据（长度：{len(image_data)}字符）")
                    # 处理base64图片
                    saved_path = save_base64_image(image_data, prompt)
                    if saved_path:
                        return saved_path
                    else:
                        print(f"⚠️ base64图片保存失败")
                else:
                    # 是HTTP/HTTPS URL
                    image_url = image_data
                    # 验证URL是否完整（至少包含协议、域名和路径）
                    if validate_image_url(image_url):
                        print(f"✅ 从markdown格式中提取到图片URL：{image_url}")
                        return image_url
                    else:
                        print(f"⚠️ 提取的URL格式不完整，尝试修复：{image_url}")
                        # 尝试修复不完整的URL
                        fixed_url = fix_incomplete_url(image_url)
                        if fixed_url and validate_image_url(fixed_url):
                            print(f"✅ URL修复成功：{fixed_url}")
                            return fixed_url
                        else:
                            print(f"❌ URL修复失败，跳过此URL")
            
            # 解析策略3：直接查找HTTP/HTTPS URL
            # 改进正则：更精确地匹配完整URL
            url_pattern = r'https?://[^\s\)\]\<\>"]+'
            url_matches = re.findall(url_pattern, content_clean)
            if url_matches:
                # 过滤掉明显不是图片的URL（如API端点）
                for url in url_matches:
                    # 验证URL完整性
                    if not validate_image_url(url):
                        continue
                    # 优先选择包含图片相关关键词的URL
                    if any(keyword in url.lower() for keyword in ['image', 'img', 'photo', 'picture', 'oss', 'cdn', 'aliyuncs', 'jpg', 'jpeg', 'png', 'webp']):
                        print(f"✅ 从文本中提取到图片URL：{url}")
                        return url
                # 如果没有找到明显的图片URL，验证第一个URL后返回
                if url_matches:
                    first_url = url_matches[0]
                    if validate_image_url(first_url):
                        print(f"✅ 从文本中提取到URL：{first_url}")
                        return first_url
                    else:
                        print(f"⚠️ 提取的URL格式不完整：{first_url}")
            
            # 解析策略4：检查是否是直接的URL
            if content_clean.startswith("http://") or content_clean.startswith("https://"):
                if validate_image_url(content_clean):
                    print(f"✅ 内容本身就是URL：{content_clean}")
                    return content_clean
                else:
                    print(f"⚠️ 内容看起来像URL但格式不完整：{content_clean}")
                    fixed = fix_incomplete_url(content_clean)
                    if fixed:
                        return fixed
            
            # 解析策略5：检查是否是base64编码的图片（直接格式，非markdown / 非JSON / 非markdown图片）
            # 兼容前后空白、代码块包装等情况（已在 content_clean 中处理）
            if content_clean.startswith("data:image"):
                print(f"✅ 检测到base64图片数据（直接格式）")
                # 处理base64图片
                saved_path = save_base64_image(content_clean, prompt)
                if saved_path:
                    return saved_path
                else:
                    print(f"⚠️ base64图片保存失败")
            
            # 解析策略6：尝试从文本中提取base64 data URI（非markdown格式）
            # 允许base64内容换行/包含空白，使用非贪婪匹配但确保匹配完整
            # 改进：匹配完整的data URI，包括可能很长的base64数据
            base64_pattern = r'data:image/[^;]+;base64,[A-Za-z0-9+/=\s\n\r]+'
            base64_matches = re.findall(base64_pattern, content_clean, re.DOTALL)
            if base64_matches:
                # 选择最长的匹配（通常是完整的base64数据）
                longest_match = max(base64_matches, key=len)
                print(f"✅ 从文本中提取到base64图片数据（长度：{len(longest_match)}字符）")
                # 处理base64图片
                saved_path = save_base64_image(longest_match, prompt)
                if saved_path:
                    return saved_path
                else:
                    print(f"⚠️ base64图片保存失败")
            
            # 如果所有解析方式都失败，打印详细内容用于调试
            print(f"⚠️ yunwu.ai返回格式无法解析")
            # 如果内容太长（可能是base64数据），只打印前1000字符和后100字符
            if len(content_clean) > 2000:
                print(f"📄 原始内容（前1000字符）：{content_clean[:1000]}")
                print(f"📄 原始内容（后100字符）：{content_clean[-100:]}")
                print(f"📊 内容长度：{len(content_clean)} 字符（已截断显示）")
            else:
                print(f"📄 原始内容（完整）：{content_clean}")
                print(f"📊 内容长度：{len(content_clean)} 字符")
            print(f"📊 内容类型检查：")
            print(f"   - 包含 'http': {'http' in content_clean.lower()}")
            print(f"   - 包含 'data:image': {'data:image' in content_clean.lower()}")
            print(f"   - 包含 'base64': {'base64' in content_clean.lower()}")
            print(f"   - 包含 'url': {'url' in content_clean.lower()}")
            print(f"   - 包含 'image': {'image' in content_clean.lower()}")
            print(f"   - 以'data:image'开头: {content_clean.startswith('data:image')}")
            
            # 检查返回内容是否是文本描述（而非图片数据）
            if len(content_clean) > 100 and not any(keyword in content_clean.lower() for keyword in ['http', 'data:image', 'base64', 'url', 'image']):
                print(f"💡 提示：yunwu.ai返回的是文本描述而非图片数据，可能是API生成失败或返回格式异常")
                print(f"💡 可能的原因：")
                print(f"   1. yunwu.ai API模型配置不正确（当前模型：{model}）")
                print(f"   2. gemini-2.5-flash-image 模型可能不支持图片生成，或返回格式不同")
                print(f"   3. API返回格式不符合预期，需要检查yunwu.ai API文档")
                print(f"   4. API密钥权限不足或配置错误")
                print(f"   5. 提示词格式不符合模型要求")
                print(f"💡 建议：")
                print(f"   - 检查.env文件中的yunwu_api_key和yunwu_model配置")
                print(f"   - 尝试切换到其他支持图片生成的模型（如 sora_image）")
                print(f"   - 确认yunwu.ai API是否支持图片生成功能")
                print(f"   - 查看yunwu.ai API文档确认正确的调用方式")
                print(f"   - 如果API不支持图片生成，可以切换到其他图片生成服务（如ComfyUI、Replicate、Stable Diffusion等）")
            else:
                print(f"💡 提示：返回内容包含图片相关关键词，但解析失败")
                print(f"💡 可能的原因：")
                print(f"   1. 返回格式不在预期的解析策略中")
                print(f"   2. URL或base64数据格式不完整")
                print(f"   3. 需要添加新的解析策略")
            return None
                
        except requests.exceptions.Timeout as e:
            # 超时错误：图片生成可能需要更长时间，重试
            print(f"⚠️ yunwu.ai图片生成API请求超时（尝试 {attempt + 1}/{max_retries}，超时时间：{request_timeout}秒）")
            print(f"   图片生成通常需要较长时间，可能是API响应慢或网络问题")
            print(f"   💡 提示：如果经常超时，可以增加 YUNWU_IMAGE_TIMEOUT_SECONDS 环境变量（当前：{request_timeout}秒）")
            if attempt < max_retries - 1:
                # 超时后等待更长时间再重试
                wait_time = 10 * (attempt + 1)  # 10s, 20s, 30s
                print(f"   等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
                continue
            else:
                # 最后一次尝试也超时，抛出异常
                print(f"❌ 达到最大重试次数（{max_retries}），图片生成超时")
                print(f"   💡 建议：增加 YUNWU_IMAGE_TIMEOUT_SECONDS 环境变量到更大的值（例如：300秒）")
                raise
        except requests.exceptions.HTTPError as e:
            # 429错误已经在上面处理，这里处理其他HTTP错误
            if e.response and e.response.status_code == 429:
                # 如果429错误没有被上面的逻辑处理（理论上不应该发生），抛出异常
                raise
            else:
                # 其他HTTP错误直接抛出
                print(f"❌ yunwu.ai图片生成API调用失败（HTTP错误）：{str(e)}")
                raise
        except Exception as e:
            # 其他错误（如网络错误等）
            error_msg = str(e)
            if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
                # 超时错误，重试
                print(f"⚠️ yunwu.ai图片生成API请求超时（尝试 {attempt + 1}/{max_retries}）")
                if attempt < max_retries - 1:
                    wait_time = 10 * (attempt + 1)
                    print(f"   等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                    continue
            # 其他错误直接抛出
            print(f"❌ yunwu.ai图片生成API调用失败：{error_msg}")
            raise

def call_comfyui_api(prompt: str, style: str) -> str:
    """调用ComfyUI API生成图片"""
    try:
        comfyui_host = IMAGE_GENERATION_CONFIG.get("comfyui_host", "")
        if not comfyui_host:
            raise ValueError("ComfyUI Host未配置")
        
        # ComfyUI API调用需要先提交任务，然后轮询结果
        # 这里提供基础框架，需要根据实际ComfyUI API调整
        print(f"⚠️ ComfyUI API调用需要根据实际API文档实现")
        return None
    except Exception as e:
        print(f"❌ ComfyUI API调用失败：{str(e)}")
        raise

def call_replicate_api(prompt: str, style: str) -> str:
    """调用Replicate API生成图片"""
    try:
        # import replicate
        replicate_client = replicate.Client(api_token=IMAGE_GENERATION_CONFIG.get("replicate_api_token"))
        
        # 使用Stable Diffusion模型
        output = replicate_client.run(
            "stability-ai/stable-diffusion:db21e45d3f7023abc2a46ee38a23973f6dce16bb082a930b0c49861f96d1e5bf",
            input={
                "prompt": prompt,
                "width": 1024,
                "height": 1024,
                "num_outputs": 1
            }
        )
        
        # Replicate返回的是列表
        if isinstance(output, list) and len(output) > 0:
            return output[0]
        elif isinstance(output, str):
            return output
        else:
            print(f"⚠️ Replicate返回格式异常：{output}")
            return None
    except Exception as e:
        print(f"❌ Replicate API调用失败：{str(e)}")
        raise

def call_dalle_api(prompt: str) -> str:
    """调用DALL-E API生成图片"""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=IMAGE_GENERATION_CONFIG.get("openai_api_key"))
        
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt[:1000],  # DALL-E 3限制提示词长度
            size="1024x1024",
            quality="standard",
            n=1,
        )
        
        return response.data[0].url
    except Exception as e:
        print(f"❌ DALL-E API调用失败：{str(e)}")
        raise

def call_stable_diffusion_api(prompt: str, style: str, reference_image_url: str = "") -> str:
    """调用本地Stable Diffusion API生成图片（支持img2img参考图）"""
    try:
        import base64
        from pathlib import Path

        base_url = IMAGE_GENERATION_CONFIG.get("stable_diffusion_base_url", "http://localhost:7860")
        api_key = IMAGE_GENERATION_CONFIG.get("stable_diffusion_api_key", "")

        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        def _load_ref_image_b64(ref: str) -> str:
            """把参考图读成 base64（不带 data:image 前缀），失败返回空串。"""
            if not ref or not isinstance(ref, str):
                return ""
            ref = ref.strip()
            if not ref:
                return ""

            # data URL
            if ref.startswith("data:image"):
                try:
                    b64_part = ref.split("base64,", 1)[1]
                    b64_part = re.sub(r"\s+", "", b64_part)
                    base64.b64decode(b64_part, validate=False)
                    return b64_part
                except Exception:
                    return ""

            # 本地缓存路径（前端常传 /image_cache/...）
            if ref.startswith("/image_cache/") or ref.startswith("image_cache/"):
                rel = ref[1:] if ref.startswith("/") else ref
                # 以项目目录为基准，避免工作目录变化导致找不到文件
                base_dir = Path(__file__).resolve().parent
                local_path = (base_dir / rel).resolve()
                if local_path.exists():
                    data = local_path.read_bytes()
                    return base64.b64encode(data).decode("utf-8")
                return ""

            # HTTP/HTTPS
            if ref.startswith("http://") or ref.startswith("https://"):
                try:
                    r = requests.get(ref, timeout=30)
                    r.raise_for_status()
                    return base64.b64encode(r.content).decode("utf-8")
                except Exception:
                    return ""

            return ""

        # 参数：可通过环境变量调节（给“同一场景统一风格/物件”留调参口）
        denoising_strength = float(os.getenv("STABLE_DIFFUSION_DENOISING_STRENGTH", "0.55"))
        steps = int(os.getenv("STABLE_DIFFUSION_STEPS", "20"))
        cfg_scale = float(os.getenv("STABLE_DIFFUSION_CFG_SCALE", "7"))

        ref_b64 = _load_ref_image_b64(reference_image_url)
        if ref_b64:
            # img2img：参考上一剧情图片，保持人物/物件一致性更强
            response = requests.post(
                f"{base_url}/sdapi/v1/img2img",
                headers=headers,
                json={
                    "init_images": [ref_b64],
                    "prompt": prompt,
                    "denoising_strength": max(0.0, min(1.0, denoising_strength)),
                    "width": 1024,
                    "height": 1024,
                    "steps": steps,
                    "cfg_scale": cfg_scale
                },
                timeout=180
            )
        else:
            # txt2img
            response = requests.post(
                f"{base_url}/sdapi/v1/txt2img",
                headers=headers,
                json={
                    "prompt": prompt,
                    "width": 1024,
                    "height": 1024,
                    "steps": steps,
                    "cfg_scale": cfg_scale
                },
                timeout=180
            )

        response.raise_for_status()
        result = response.json()

        if "images" in result and isinstance(result["images"], list) and len(result["images"]) > 0:
            b64 = result["images"][0]
            if isinstance(b64, str) and b64.strip():
                # SD WebUI 返回的是纯base64，这里转为 data URI 保存到本地缓存
                data_uri = f"data:image/png;base64,{b64.strip()}"
                saved_path = save_base64_image(data_uri, prompt)
                return saved_path
        return None
    except Exception as e:
        print(f"❌ Stable Diffusion API调用失败：{str(e)}")
        raise

# ==================== 视频生成功能已禁用（性能优化） ====================
# 视频生成任务存储（用于状态查询）
# video_tasks = {}
# video_tasks_lock = threading.Lock()

# def generate_scene_video(
#     scene_description: str,
#     image_url: str = None,
#     duration: int = None
# ) -> Dict:
#     """
#     生成场景视频片段（5-10秒）
#     :param scene_description: 场景描述
#     :param image_url: 基于图片生成视频（推荐，质量更好）
#     :param duration: 视频时长（5-10秒）
#     :return: 包含任务ID和状态的字典
#     """
#     # 检查是否配置了视频生成API
#     provider = VIDEO_GENERATION_CONFIG.get("provider", "yunwu")
#     
#     if provider == "yunwu" and not VIDEO_GENERATION_CONFIG.get("yunwu_api_key"):
#         print("⚠️ yunwu.ai API Key未配置，跳过视频生成")
#         return None
#     elif provider == "runway" and not VIDEO_GENERATION_CONFIG.get("runway_api_key"):
#         print("⚠️ Runway API Key未配置，跳过视频生成")
#         return None
#     elif provider == "pika" and not VIDEO_GENERATION_CONFIG.get("pika_api_key"):
#         print("⚠️ Pika API Key未配置，跳过视频生成")
#         return None
#     
#     # 限制视频时长为5-10秒
#     min_duration = VIDEO_GENERATION_CONFIG.get("min_duration", 5)
#     max_duration = VIDEO_GENERATION_CONFIG.get("max_duration", 10)
#     
#     if duration is None:
#         duration = random.randint(min_duration, max_duration)
#     else:
#         duration = max(min_duration, min(max_duration, duration))
#     
#     # 生成任务ID
#     task_id = str(uuid.uuid4())
#     
#     # 启动后台任务
#     thread = threading.Thread(
#         target=async_generate_video_task,
#         args=(task_id, scene_description, image_url, duration, provider),
#         daemon=True
#     )
#     thread.start()
#     
#     return {
#         "task_id": task_id,
#         "status": "processing",
#         "duration": duration,
#         "estimated_time": 60  # 预计生成时间（秒）
#     }

# def async_generate_video_task(
#     task_id: str,
#     scene_description: str,
#     image_url: str,
#     duration: int,
#     provider: str
# ):
#     """异步生成视频任务"""
#     try:
#         if provider == "yunwu":
#             video_url = call_yunwu_video_api(scene_description, image_url, duration)
#         elif provider == "runway":
#             video_url = call_runway_gen2_api(scene_description, image_url, duration)
#         elif provider == "pika":
#             video_url = call_pika_api(scene_description, image_url, duration)
#         else:
#             print(f"⚠️ 不支持的视频生成服务：{provider}")
#             with video_tasks_lock:
#                 video_tasks[task_id] = {
#                     "status": "failed",
#                     "error": f"不支持的视频生成服务：{provider}"
#                 }
#             return
#         
#         # 更新任务状态
#         with video_tasks_lock:
#             video_tasks[task_id] = {
#                 "status": "completed",
#                 "url": video_url,
#                 "duration": duration
#             }
#         print(f"✅ 视频生成完成，任务ID：{task_id}")
#     except Exception as e:
#         print(f"❌ 视频生成失败，任务ID：{task_id}，错误：{str(e)}")
#         import traceback
#         traceback.print_exc()
#         with video_tasks_lock:
#             video_tasks[task_id] = {
#                 "status": "failed",
#                 "error": str(e)
#             }

# # ==================== 以下视频生成函数已禁用（性能优化） ====================
# def call_yunwu_video_api(prompt: str, image_url: str = None, duration: int = 5) -> str:
#     """调用yunwu.ai视频生成API（使用sora模型）"""
#     ... (已注释)

# def call_runway_gen2_api(prompt: str, image_url: str = None, duration: int = 5) -> str:
#     """调用Runway Gen-2 API生成视频"""
#     ... (已注释)

# def call_pika_api(prompt: str, image_url: str = None, duration: int = 5) -> str:
#     """调用Pika Labs API生成视频"""
#     ... (已注释)

# def get_video_task_status(task_id: str) -> Dict:
#     """获取视频生成任务状态"""
#     with video_tasks_lock:
#         return video_tasks.get(task_id, None)

# 提供一个空的占位函数，避免导入错误
def get_video_task_status(task_id: str) -> Dict:
    """获取视频生成任务状态（已禁用）"""
    return None

# ------------------------------
# 新增：结局预测生成函数
# ------------------------------
def modify_ending_tone(global_state: Dict, trigger_event: str) -> bool:
    """
    修改结局主基调，仅在触发深层背景节点时调用
    :param global_state: 全局状态
    :param trigger_event: 触发事件描述
    :return: 主基调是否发生变化
    """
    if not global_state:
        return False
    
    # 确保隐藏结局预测存在
    if 'hidden_ending_prediction' not in global_state:
        global_state['hidden_ending_prediction'] = generate_ending_prediction(global_state)
    
    current_prediction = global_state['hidden_ending_prediction']
    current_tone = current_prediction.get('main_tone', 'NE')
    core_worldview = global_state.get('core_worldview', {})
    flow_worldline = global_state.get('flow_worldline', {})
    
    # 构建Prompt，修改结局主基调
    prompt = f"""
    请作为资深游戏编剧，基于以下信息，判断是否需要修改结局主基调，**严格遵守以下要求**：
    
    ## 【当前信息】
    当前结局主基调：{current_tone}
    触发事件：{trigger_event}
    世界观设定：{json.dumps(core_worldview, ensure_ascii=False)}
    当前游戏状态：{json.dumps(flow_worldline, ensure_ascii=False)}
    
    ## 【判断要求】
    1. 只有在触发深层背景节点时（如重要人物死亡、主角遭遇生死危机、核心羁绊断裂或稳固等关键剧情），才考虑修改主基调
    2. 结局主基调类型：HE（圆满结局）、BE（悲剧结局）、NE（普通结局）等
    3. 输出格式：仅返回新的主基调类型，如 "HE"、"BE" 或 "NE"，不要返回任何多余的解释说明
    4. 如果不需要修改主基调，直接返回当前主基调
    
    记住：你的任务是基于触发事件判断是否需要修改结局主基调！
    """
    
    if AI_API_CONFIG.get("api_key"):
        try:
            request_body = {
                "model": AI_API_CONFIG["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
                "max_tokens": 50,
                "top_p": 0.7,
                "frequency_penalty": 0.5,
                "presence_penalty": 0.2,
                "timeout": 100
            }
            
            response_data = call_ai_api(request_body)
            choices = response_data.get("choices", [])
            if choices and len(choices) > 0:
                message = choices[0].get("message", {})
                new_tone = message.get("content", "").strip()
                
                # 如果主基调发生变化，更新全局状态
                if new_tone != current_tone:
                    current_prediction['main_tone'] = new_tone
                    print(f"🔄 结局主基调已修改：{current_tone} → {new_tone}")
                    return True
        except Exception as e:
            print(f"❌ 修改结局主基调失败：{str(e)}")
    
    return False

def modify_ending_content(global_state: Dict) -> None:
    """
    修改结局大致内容，用户每完成一次交互选择后调用
    """
    if not global_state:
        return
    
    # 确保隐藏结局预测存在
    if 'hidden_ending_prediction' not in global_state:
        global_state['hidden_ending_prediction'] = generate_ending_prediction(global_state)
    
    current_prediction = global_state['hidden_ending_prediction']
    core_worldview = global_state.get('core_worldview', {})
    flow_worldline = global_state.get('flow_worldline', {})
    current_tone = current_prediction.get('main_tone', 'NE')
    current_content = current_prediction.get('content', '')
    
    # 构建Prompt，修改结局大致内容
    prompt = f"""
    请作为资深游戏编剧，基于以下信息，对结局大致内容进行小幅度调整，**严格遵守以下要求**：
    
    ## 【当前信息】
    结局主基调：{current_tone}
    当前结局大致内容：{current_content}
    世界观设定：{json.dumps(core_worldview, ensure_ascii=False)}
    当前游戏进度：{json.dumps(flow_worldline, ensure_ascii=False)}
    
    ## 【修改要求】
    1. 基于当前的结局主基调和游戏进度，对内容进行小幅度调整
    2. 可以补充细节、微调情节走向，但不颠覆核心框架
    3. 输出格式：仅返回修改后的结局大致内容，不要返回任何多余的解释说明
    4. 所有输出必须使用中文
    
    记住：你的任务是对结局大致内容进行小幅度调整，不要颠覆核心框架！
    """
    
    if AI_API_CONFIG.get("api_key"):
        try:
            request_body = {
                "model": AI_API_CONFIG["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
                "max_tokens": 500,
                "top_p": 0.7,
                "frequency_penalty": 0.5,
                "presence_penalty": 0.2,
                "timeout": 100
            }
            
            response_data = call_ai_api(request_body)
            choices = response_data.get("choices", [])
            if choices and len(choices) > 0:
                message = choices[0].get("message", {})
                new_content = message.get("content", "").strip()
                
                # 更新结局大致内容
                current_prediction['content'] = new_content
        except Exception as e:
            print(f"❌ 修改结局大致内容失败：{str(e)}")


def generate_ending_prediction(global_state: Dict) -> Dict:
    """
    生成隐藏的结局预测，包含结局主基调和大致内容
    """
    if not global_state:
        return {}
    
    core_worldview = global_state.get('core_worldview', {})
    
    # 构建Prompt，生成结局预测
    prompt = f"""
    请作为资深游戏编剧，基于以下世界观设定，生成一个完整的结局预测，**严格遵守以下要求**：
    
    ## 【世界观设定】
    {json.dumps(core_worldview, ensure_ascii=False)}
    
    ## 【生成要求】
    1. 生成内容必须严格符合世界观设定
    2. 结局预测包含两个核心部分：
       - 结局主基调：如HE（圆满结局）、BE（悲剧结局）、NE（普通结局）等
       - 结局大致内容：基于主基调生成的结局核心情节框架，例如 "主角守护羁绊角色达成和解" 这类文本化的情节描述
    3. 输出格式：
       结局主基调：[主基调类型]
       结局大致内容：[内容描述]
    4. 所有输出必须使用中文，不要返回任何多余的解释说明
    
    记住：你的任务是生成一个合理的结局预测，作为后台调控剧情的依据！
    """
    
    if AI_API_CONFIG.get("api_key"):
        try:
            request_body = {
                "model": AI_API_CONFIG["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
                "max_tokens": 500,
                "top_p": 0.7,
                "frequency_penalty": 0.5,
                "presence_penalty": 0.2,
                "timeout": 100
            }
            
            response_data = call_ai_api(request_body)
            choices = response_data.get("choices", [])
            if choices and len(choices) > 0:
                message = choices[0].get("message", {})
                raw_content = message.get("content", "").strip()
                
                # 解析生成的内容
                ending_prediction = {}
                for line in raw_content.split('\n'):
                    line = line.strip()
                    if "结局主基调：" in line:
                        ending_prediction['main_tone'] = line.split("结局主基调：")[1].strip()
                    elif "结局大致内容：" in line:
                        ending_prediction['content'] = line.split("结局大致内容：")[1].strip()
                
                return ending_prediction
        except Exception as e:
            print(f"❌ 生成结局预测失败：{str(e)}")
    
    # 如果AI API不可用或生成失败，返回默认结局预测
    return {
        "main_tone": "NE",
        "content": "主角完成了主要任务，虽然过程中经历了许多困难，但最终达成了预期目标"
    }

# ------------------------------
# LLM生成函数（修复JSON解析+强制贴合用户选择+自动重试）
# ------------------------------
def llm_generate_global(user_idea: str, protagonist_attr: Dict, difficulty: str, tone_key: str = "normal_ending", force_full: bool = False) -> Dict:
    """调用yunwu.ai生成包含章节矛盾、适配主角属性/难度的Global世界观
    
    force_full: True 时跳过分阶段/模板/缓存，加速生成完整版本（用于后台补全）
    """
    if not user_idea.strip():
        raise ValueError("游戏主题idea不能为空")
    
    perf = PERFORMANCE_OPTIMIZATION
    perf_enabled = perf.get("enabled", True)
    staged_mode = perf_enabled and perf.get("staged_worldview", True) and not force_full

    
    # 环境变量验证：检查必填字段是否齐全
    required_configs = ["api_key", "base_url", "model"]
    missing_configs = [config for config in required_configs if not AI_API_CONFIG.get(config)]
    if missing_configs:
        config_names = {
            "api_key": "Camera_Analyst_API_KEY",
            "base_url": "Camera_Analyst_BASE_URL",
            "model": "Camera_Analyst_MODEL"
        }
        missing_env_names = [config_names.get(c, c) for c in missing_configs]
        print(f"❌ 错误：缺少必要的API配置，请在.env文件中设置：{', '.join(missing_env_names)}")
        print("💡 提示：将使用默认世界观继续游戏（非AI生成）")
        print("💡 如需使用AI生成，请配置.env文件中的以下环境变量：")
        for env_name in missing_env_names:
            print(f"   - {env_name}")
        # 抛出异常，让后端能够返回错误信息给前端
        raise ValueError(f"缺少必要的API配置：{', '.join(missing_env_names)}。请在.env文件中配置这些环境变量以启用AI生成功能。")
    
    # 模板加速：若开启且命中模板，直接返回，并可后台补全（已禁用，强制使用AI生成）
    # 注释掉模板机制，确保每次都通过AI生成
    # if perf_enabled and perf.get("use_templates", True) and not force_full:
    #     template_view = _load_template_worldview(user_idea)
    #     if template_view:
    #         merged = _merge_template_with_input(template_view, protagonist_attr, difficulty, tone_key)
    #         _save_worldview_cache(cache_key, merged)
    #         print("✅ 使用模板世界观返回")
    #         if staged_mode:
    #             threading.Thread(
    #                 target=_background_fill_worldview_details,
    #                 args=(cache_key, user_idea, protagonist_attr, difficulty, tone_key),
    #                 daemon=True
    #             ).start()
    #             merged.setdefault("meta", {})["detail_async"] = True
    #         return merged
    
    # 获取基调配置
    tone = TONE_CONFIGS.get(tone_key, TONE_CONFIGS["normal_ending"])
    
    # 修改Prompt：根据配置选择核心版或完整版
    if staged_mode:
        prompt = f"""
        你是资深游戏编剧，请生成【核心世界观速写】，简洁但覆盖关键要素。
        要求：中文输出，无代码块，无多余解释；严格贴合基调：{tone['name']}（{tone['description']}），语言特征：{tone['language_features']}，禁忌：{tone['taboo_content']}

        ## 【核心世界观】
        游戏风格：至少60字
        世界观基础设定：至少250字，包含背景/历史/地理/社会/文化/关键事件
        主角核心能力：至少80字，包含来源、使用方式、限制

        ### 【主线任务】
        游戏主线任务：至少150字，说明目标、步骤、挑战

        ### 【章节设定】
        第1章：
        - 核心矛盾：≥80字
        - 矛盾结束条件：≥60字
        第2章：
        - 核心矛盾：≥80字
        - 矛盾结束条件：≥60字
        第3章：
        - 核心矛盾：≥80字
        - 矛盾结束条件：≥60字

        ### 【主角规范信息】（仅内部使用，不展示给玩家；必填，用于后续主角形象生成）
        主角姓名(中)：（如碇真嗣）
        主角姓名(英)：（如Shinji Ikari）
        性别：男性或女性
        年龄感：少年/青年/中年/其他
        所属作品(中)：（如新世纪福音战士）
        所属作品(英)：（如Neon Genesis Evangelion）
        标志性外观关键词：（6–12条逗号分隔，如黑色短发、校服、瘦削、忧郁气质）

        ## 【初始世界线】
        当前章节：chapter1
        主线进度：初始主线进度
        章节矛盾：未解决

        ## 【输入数据】
        - 主题：{user_idea}
        - 主角属性：{json.dumps(protagonist_attr, ensure_ascii=False)}
        - 难度：{difficulty}
        - 基调：{tone['name']}
        """
    else:
        prompt = f"""
        你是资深游戏编剧，请生成完整的文本冒险游戏世界观。
        规则：中文输出；无代码块/解释；按分隔符输出且字段齐全；必须贴合基调：{tone['name']}（{tone['description']}），语言特征：{tone['language_features']}，禁忌：{tone['taboo_content']}

        ## 【核心世界观】
        游戏风格：≥80字
        世界观基础设定：≥320字，包含背景/历史/地理/社会/文化/关键事件，为首轮选项提供足够信息
        主角核心能力：≥100字
        
        ### 【角色设定】
        主角：核心性格≥70字；浅层背景≥120字；深层背景≥250字（含主线相关秘密）
        配角1：核心性格≥70字；浅层背景≥120字；深层背景≥250字
        
        ### 【势力设定】
        正派势力：每个≥50字；反派势力：每个≥50字；中立势力：每个≥50字
        
        ### 【主线任务】
        游戏主线任务：≥180字
        
        ### 【章节设定】
        第1章：
        - 核心矛盾：≥90字
        - 矛盾结束条件：≥70字
        第2章：
        - 核心矛盾：≥90字
        - 矛盾结束条件：≥70字
        第3章：
        - 核心矛盾：≥90字
        - 矛盾结束条件：≥70字
        
        ### 【游戏结束触发条件】
        游戏结束触发条件：≥90字

        ### 【主角规范信息】（仅内部使用，不展示给玩家；必填，用于后续主角形象生成）
        主角姓名(中)：（如碇真嗣）
        主角姓名(英)：（如Shinji Ikari）
        性别：男性或女性
        年龄感：少年/青年/中年/其他
        所属作品(中)：（如新世纪福音战士）
        所属作品(英)：（如Neon Genesis Evangelion）
        标志性外观关键词：（6–12条逗号分隔，如黑色短发、校服、瘦削、忧郁气质）

        ## 【初始世界线】
        当前章节：chapter1
        角色初始状态：主角/配角1的想法、身体状态、深层背景解锁、深度
        环境初始状态：天气/位置/势力关系
        主线进度：初始主线进度
        章节矛盾：已解决/未解决

        ## 【输入数据】
        - 主题：{user_idea}
        - 主角属性：{json.dumps(protagonist_attr, ensure_ascii=False)}
        - 难度：{difficulty}
        - 基调：{tone['name']}
        - 任务：为首轮2个选项提供充足背景信息
        """

    # 构建请求体，不强制要求JSON格式
    worldview_tokens = 5000
    if perf_enabled and perf.get("optimize_tokens", True):
        worldview_tokens = perf.get("worldview_max_tokens", 3500)
        if staged_mode:
            worldview_tokens = min(worldview_tokens, 2200)
    request_body = {
        "model": AI_API_CONFIG["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.32 if staged_mode else 0.3,
        "max_tokens": worldview_tokens,
        "top_p": 0.6,
        "frequency_penalty": 0.4,
        "presence_penalty": 0.15,
        "timeout": 160 if staged_mode else 200
    }

    # 内部重试机制，最多尝试3次生成和解析
    max_retries = 3
    if perf_enabled and perf.get("optimize_retry", True):
        max_retries = perf.get("worldview_max_retries", 2)
    for attempt in range(max_retries):
        try:
            print(f"📝 尝试生成世界观（第{attempt+1}/{max_retries}次）...")
            # 调用带重试的API函数
            response_data = call_ai_api(request_body)
            # 安全访问嵌套键
            choices = response_data.get("choices", [])
            if not choices or len(choices) == 0:
                print("❌ 错误：AI返回内容格式异常，缺少choices字段，将重试...")
                continue
            
            message = choices[0].get("message", {})
            if not message:
                print("❌ 错误：AI返回内容格式异常，缺少message字段，将重试...")
                continue
            
            raw_content = message.get("content", "").strip()
            if not raw_content:
                print("❌ 错误：AI返回内容为空，将重试...")
                continue
            
            # 直接从文本中提取信息，不依赖JSON解析
            global_state = {}
            
            # 初始化核心世界观和世界线
            global_state['core_worldview'] = {}
            global_state['flow_worldline'] = {}
            
            # 处理原始文本
            lines = raw_content.split('\n')
            
            # 提取核心世界观
            core_section = False
            core_worldview = {}
            characters = {}
            forces = {}
            chapters = {}
            
            current_section = ""
            current_character = ""
            current_chapter = ""
            current_field = None  # 当前正在收集的字段
            current_field_content = []  # 当前字段的内容（支持多行）
            current_conflict_content = []  # 当前章节核心矛盾的内容（支持多行）
            current_end_condition_content = []  # 当前章节矛盾结束条件的内容（支持多行）
            # 主角规范信息（仅内部使用，不展示给玩家）
            in_canonical_section = False
            protagonist_canonical = {}
            _canonical_key_map = [
                ("主角姓名(中)", "name_zh"), ("主角姓名(英)", "name_en"), ("性别", "gender"),
                ("年龄感", "age_range"), ("所属作品(中)", "work_zh"), ("所属作品(英)", "work_en"),
                ("标志性外观关键词", "signature_look_keywords")
            ]
            current_canonical_key = None
            current_canonical_content = []
            
            # print(f"🔍 [调试] 开始解析AI返回文本，总行数: {len(lines)}")
            for line_idx, line in enumerate(lines):
                original_line = line
                line = line.strip()
                if not line:
                    # 空行：如果正在收集字段内容，继续收集（可能是多行内容的一部分）
                    if current_field and current_field_content:
                        continue
                    else:
                        continue
                
                # 检测章节
                if line.startswith('## 【核心世界观】'):
                    core_section = True
                    continue
                elif line.startswith('## 【初始世界线】'):
                    # 退出主角规范信息前先保存未刷新的字段
                    if in_canonical_section and current_canonical_key and current_canonical_content:
                        protagonist_canonical[current_canonical_key] = ' '.join(current_canonical_content).strip().replace('**', '').replace('*', '')
                    # 保存最后一个字段的内容
                    if current_field and current_field_content:
                        content = ' '.join(current_field_content).strip()
                        content = content.replace('**', '').replace('*', '')
                        if content:  # 只有非空内容才保存
                            core_worldview[current_field] = content
                    core_section = False
                    break
                # 主角规范信息区块（仅内部使用）
                if core_section and line.startswith('### 【主角规范信息】'):
                    in_canonical_section = True
                    current_canonical_key = None
                    current_canonical_content = []
                    continue
                if in_canonical_section:
                    if line.startswith('### 【') and '主角规范信息' not in line:
                        in_canonical_section = False
                        if current_canonical_key and current_canonical_content:
                            protagonist_canonical[current_canonical_key] = ' '.join(current_canonical_content).strip().replace('**', '').replace('*', '')
                        current_canonical_key = None
                        current_canonical_content = []
                    else:
                        matched = False
                        for cn_key, en_key in _canonical_key_map:
                            if line.startswith(cn_key + "："):
                                if current_canonical_key and current_canonical_content:
                                    protagonist_canonical[current_canonical_key] = ' '.join(current_canonical_content).strip().replace('**', '').replace('*', '')
                                val = line[len(cn_key) + 1:].strip()
                                if val:
                                    protagonist_canonical[en_key] = val.replace('**', '').replace('*', '')
                                    current_canonical_key = None
                                    current_canonical_content = []
                                else:
                                    current_canonical_key = en_key
                                    current_canonical_content = []
                                matched = True
                                break
                        if not matched and current_canonical_key and line and not line.startswith('##'):
                            current_canonical_content.append(line)
                    if in_canonical_section:
                        continue
                
                if core_section:
                    # 检测子章节
                    if line.startswith('### 【'):
                        # 保存上一个字段的内容
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            core_worldview[current_field] = content
                            current_field_content = []
                        current_section = line
                        current_field = None
                        continue
                    
                    # 提取基本信息（支持多行内容）
                    if "游戏风格：" in line:
                        # 保存上一个字段
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:  # 只有非空内容才保存
                                core_worldview[current_field] = content
                        # 开始新字段
                        current_field = 'game_style'
                        part = line.split("游戏风格：")[1].strip()
                        current_field_content = [part] if part else []
                    elif "世界观基础设定：" in line:
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:
                                core_worldview[current_field] = content
                        current_field = 'world_basic_setting'
                        part = line.split("世界观基础设定：")[1].strip()
                        current_field_content = [part] if part else []
                    elif "主角核心能力：" in line:
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:
                                core_worldview[current_field] = content
                        current_field = 'protagonist_ability'
                        part = line.split("主角核心能力：")[1].strip()
                        current_field_content = [part] if part else []
                    # 先检查是否是其他字段的开始（需要先保存当前字段）
                    elif "游戏主线任务：" in line:
                        # 保存当前字段
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:
                                core_worldview[current_field] = content
                            current_field = None
                            current_field_content = []
                        core_worldview['main_quest'] = line.split("游戏主线任务：")[1].strip()
                    elif "游戏结束触发条件：" in line:
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:
                                core_worldview[current_field] = content
                            current_field = None
                            current_field_content = []
                        core_worldview['end_trigger_condition'] = line.split("游戏结束触发条件：")[1].strip()
                    # 提取势力设定
                    elif "正派势力：" in line:
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:
                                core_worldview[current_field] = content
                            current_field = None
                            current_field_content = []
                        forces['positive'] = [f.strip() for f in line.split("正派势力：")[1].split(',')]
                    elif "反派势力：" in line:
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:
                                core_worldview[current_field] = content
                            current_field = None
                            current_field_content = []
                        forces['negative'] = [f.strip() for f in line.split("反派势力：")[1].split(',')]
                    elif "中立势力：" in line:
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:
                                core_worldview[current_field] = content
                            current_field = None
                            current_field_content = []
                        forces['neutral'] = [f.strip() for f in line.split("中立势力：")[1].split(',')]
                    # 角色设定
                    elif line in ["主角：", "配角1："]:
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:
                                core_worldview[current_field] = content
                            current_field = None
                            current_field_content = []
                        current_character = line[:-1]  # 去掉冒号
                        characters[current_character] = {}
                    elif current_character and line.startswith('- 核心性格：'):
                        characters[current_character]['core_personality'] = line.split('- 核心性格：')[1].strip()
                    elif current_character and line.startswith('- 浅层背景：'):
                        characters[current_character]['shallow_background'] = line.split('- 浅层背景：')[1].strip()
                    elif current_character and line.startswith('- 深层背景：'):
                        characters[current_character]['deep_background'] = line.split('- 深层背景：')[1].strip()
                    # 章节设定（优先检查，避免被其他条件拦截）
                    if line.startswith('第') and ('章：' in line or '章' in line):
                        # 保存当前字段和章节矛盾内容
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:
                                core_worldview[current_field] = content
                            current_field = None
                            current_field_content = []
                        # 保存上一个章节的矛盾信息
                        if current_chapter:
                            if current_conflict_content:
                                conflict_text = ' '.join(current_conflict_content).strip()
                                conflict_text = conflict_text.replace('**', '').replace('*', '').strip()
                                if conflict_text:
                                    chapters[current_chapter]['main_conflict'] = conflict_text
                            if current_end_condition_content:
                                end_condition_text = ' '.join(current_end_condition_content).strip()
                                end_condition_text = end_condition_text.replace('**', '').replace('*', '').strip()
                                if end_condition_text:
                                    chapters[current_chapter]['conflict_end_condition'] = end_condition_text
                        # 提取章节号（支持"第1章："或"第1章"格式）
                        if '章：' in line:
                            chapter_num = line.split('章：')[0].replace('第', '').strip()
                        else:
                            # 处理"第1章"格式
                            match = re.search(r'第(\d+)章', line)
                            chapter_num = match.group(1) if match else line.replace('第', '').replace('章', '').strip()
                        current_chapter = f"chapter{chapter_num}"
                        chapters[current_chapter] = {}
                        current_conflict_content = []
                        current_end_condition_content = []
                        
                        # 检查同一行是否包含矛盾信息（容错处理）
                        remaining_line = line.split('章：', 1)[1] if '章：' in line else ''
                        if remaining_line and ('核心矛盾' in remaining_line or '矛盾：' in remaining_line):
                            # 尝试提取同一行的矛盾信息
                            if '- 核心矛盾：' in remaining_line:
                                conflict_part = remaining_line.split('- 核心矛盾：', 1)[1].strip()
                                if conflict_part:
                                    current_conflict_content.append(conflict_part)
                            elif '核心矛盾：' in remaining_line:
                                conflict_part = remaining_line.split('核心矛盾：', 1)[1].strip()
                                if conflict_part:
                                    current_conflict_content.append(conflict_part)
                            if '- 矛盾结束条件：' in remaining_line:
                                end_part = remaining_line.split('- 矛盾结束条件：', 1)[1].strip()
                                if end_part:
                                    current_end_condition_content.append(end_part)
                            elif '矛盾结束条件：' in remaining_line:
                                end_part = remaining_line.split('矛盾结束条件：', 1)[1].strip()
                                if end_part:
                                    current_end_condition_content.append(end_part)
                    elif current_chapter and ('核心矛盾' in line or '矛盾：' in line):
                        # 支持多种格式：- 核心矛盾： 或 核心矛盾： 或 核心矛盾
                        conflict_text = None
                        if '- 核心矛盾：' in line:
                            conflict_text = line.split('- 核心矛盾：', 1)[1].strip()
                        elif '核心矛盾：' in line:
                            conflict_text = line.split('核心矛盾：', 1)[1].strip()
                        elif line.strip().startswith('核心矛盾') and '：' not in line:
                            # 如果没有冒号，整行作为内容
                            conflict_text = line.replace('核心矛盾', '').strip()
                        
                        if conflict_text:
                            # 移除Markdown格式标记
                            conflict_text = conflict_text.replace('**', '').replace('*', '').strip()
                            if conflict_text:
                                current_conflict_content.append(conflict_text)
                        elif current_conflict_content:
                            # 如果当前行没有冒号分隔，可能是多行内容的延续
                            stripped_line = line.strip()
                            if stripped_line and not stripped_line.startswith('-') and not stripped_line.startswith('第') and '：' not in stripped_line:
                                current_conflict_content.append(stripped_line)
                    elif current_chapter and ('矛盾结束条件' in line or '结束条件' in line):
                        # 支持多种格式
                        end_condition_text = None
                        if '- 矛盾结束条件：' in line:
                            end_condition_text = line.split('- 矛盾结束条件：', 1)[1].strip()
                        elif '矛盾结束条件：' in line:
                            end_condition_text = line.split('矛盾结束条件：', 1)[1].strip()
                        elif '- 结束条件：' in line:
                            end_condition_text = line.split('- 结束条件：', 1)[1].strip()
                        elif '结束条件：' in line:
                            end_condition_text = line.split('结束条件：', 1)[1].strip()
                        elif line.strip().startswith('矛盾结束条件') or line.strip().startswith('结束条件'):
                            end_condition_text = line.replace('矛盾结束条件', '').replace('结束条件', '').strip()
                        
                        if end_condition_text:
                            # 移除Markdown格式标记
                            end_condition_text = end_condition_text.replace('**', '').replace('*', '').strip()
                            if end_condition_text:
                                current_end_condition_content.append(end_condition_text)
                        elif current_end_condition_content:
                            # 如果当前行没有冒号分隔，可能是多行内容的延续
                            stripped_line = line.strip()
                            if stripped_line and not stripped_line.startswith('-') and not stripped_line.startswith('第') and '：' not in stripped_line:
                                current_end_condition_content.append(stripped_line)
                    elif current_field and not line.startswith('-') and not line.startswith('第') and '：' not in line:
                        # 如果当前正在收集字段内容，且这行不是新字段的开始，则追加到当前字段
                        # 但排除以"-"开头的列表项、章节标题、和其他带冒号的字段
                        if line and not line.startswith('###'):
                            current_field_content.append(line)
            
            # 保存最后一个字段（如果还在收集）
            if current_field and current_field_content:
                content = ' '.join(current_field_content).strip()
                content = content.replace('**', '').replace('*', '')
                if content:
                    core_worldview[current_field] = content
            
            # 保存最后一个章节的矛盾信息（如果还在收集）
            if current_chapter:
                if current_conflict_content:
                    conflict_text = ' '.join(current_conflict_content).strip()
                    conflict_text = conflict_text.replace('**', '').replace('*', '').strip()
                    if conflict_text:
                        chapters[current_chapter]['main_conflict'] = conflict_text
                if current_end_condition_content:
                    end_condition_text = ' '.join(current_end_condition_content).strip()
                    end_condition_text = end_condition_text.replace('**', '').replace('*', '').strip()
                    if end_condition_text:
                        chapters[current_chapter]['conflict_end_condition'] = end_condition_text
            
            # 填充核心世界观
            core_worldview['characters'] = characters
            core_worldview['forces'] = forces
            core_worldview['chapters'] = chapters
            
            # 使用正则表达式回填缺失的章节矛盾信息（作为备用方案）
            _regex_fill_worldview(raw_content, core_worldview, chapters)
            
            # 如果字段仍然缺失，设置默认值（避免完全为空）
            if not core_worldview.get('game_style'):
                core_worldview['game_style'] = f"基于主题'{user_idea}'的文本冒险游戏"
                print(f"⚠️ [警告] game_style缺失，已设置默认值")
            if not core_worldview.get('world_basic_setting'):
                core_worldview['world_basic_setting'] = f"游戏世界设定待完善，主题：{user_idea}"
                print(f"⚠️ [警告] world_basic_setting缺失，已设置默认值")
            if not core_worldview.get('protagonist_ability'):
                core_worldview['protagonist_ability'] = "主角能力待定义"
                print(f"⚠️ [警告] protagonist_ability缺失，已设置默认值")
            
            # 确保chapters结构完整，如果缺失则填充默认值
            if not chapters or len(chapters) == 0:
                chapters = {}
            # 确保至少有三个章节
            for i in range(1, 4):
                chapter_key = f"chapter{i}"
                if chapter_key not in chapters:
                    chapters[chapter_key] = {}
                if 'main_conflict' not in chapters[chapter_key] or not chapters[chapter_key]['main_conflict']:
                    chapters[chapter_key]['main_conflict'] = f"第{i}章的核心矛盾待定义"
                if 'conflict_end_condition' not in chapters[chapter_key] or not chapters[chapter_key]['conflict_end_condition']:
                    chapters[chapter_key]['conflict_end_condition'] = f"第{i}章的矛盾结束条件待定义"
            
            core_worldview['chapters'] = chapters
            global_state['core_worldview'] = core_worldview
            global_state['protagonist_canonical'] = protagonist_canonical
            # 后端仅打印「主角规范信息」解析结果
            if protagonist_canonical:
                print("✅ 主角规范信息解析结果：", json.dumps(protagonist_canonical, ensure_ascii=False, indent=2))
            
            # 提取初始世界线
            flow_section = False
            flow_worldline = {}
            characters_state = {}
            environment = {}
            
            current_character = ""
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                
                # 检测章节
                if line.startswith('## 【初始世界线】'):
                    flow_section = True
                    continue
                elif flow_section and line.startswith('## 【'):
                    flow_section = False
                    break
                
                if flow_section:
                    # 检测子章节
                    if line.startswith('### 【'):
                        continue
                    
                    # 提取基本信息
                    if "当前章节：" in line:
                        flow_worldline['current_chapter'] = line.split("当前章节：")[1].strip()
                    elif "初始主线进度：" in line:
                        flow_worldline['quest_progress'] = line.split("初始主线进度：")[1].strip()
                    elif "章节矛盾已解决：" in line:
                        status = line.split("章节矛盾已解决：")[1].strip()
                        flow_worldline['chapter_conflict_solved'] = status == "是"
                    
                    # 环境状态
                    elif "天气：" in line:
                        environment['weather'] = line.split("天气：")[1].strip()
                    elif "位置：" in line:
                        environment['location'] = line.split("位置：")[1].strip()
                    elif "势力关系：" in line:
                        environment['force_relationship'] = line.split("势力关系：")[1].strip()
                    
                    # 角色初始状态
                    elif line in ["主角：", "配角1："]:
                        current_character = line[:-1]  # 去掉冒号
                        characters_state[current_character] = {}
                    elif current_character and line.startswith('- 想法：'):
                        characters_state[current_character]['thought'] = line.split('- 想法：')[1].strip()
                    elif current_character and line.startswith('- 身体状态：'):
                        characters_state[current_character]['physiology'] = line.split('- 身体状态：')[1].strip()
                    elif current_character and line.startswith('- 深层背景解锁：'):
                        status = line.split('- 深层背景解锁：')[1].strip()
                        characters_state[current_character]['deep_background_unlocked'] = status == "是"
            
            # 填充世界线
            flow_worldline['characters'] = characters_state
            flow_worldline['environment'] = environment
            global_state['flow_worldline'] = flow_worldline
            
            # 正则回填缺失字段（解析优化）
            # 说明：上方已执行过一次正则回填（备用方案），这里不再重复执行，避免重复计算与日志刷屏。
            
            # 验证世界观完整性并填充缺失字段
            core_wv = global_state.get('core_worldview', {})
            
            # 确保必要字段存在
            if not core_wv.get('game_style'):
                core_wv['game_style'] = f"{user_idea}主题的冒险游戏"
            if not core_wv.get('world_basic_setting'):
                core_wv['world_basic_setting'] = f"在一个充满奇幻色彩的{user_idea}世界中，你将踏上一段改变命运的旅程"
            if not core_wv.get('protagonist_ability'):
                core_wv['protagonist_ability'] = f"你的能力取决于你的属性：颜值{protagonist_attr.get('颜值', '普通')}，智商{protagonist_attr.get('智商', '普通')}，体力{protagonist_attr.get('体力', '普通')}，魅力{protagonist_attr.get('魅力', '普通')}"
            
            # 确保chapters存在且完整
            if 'chapters' not in core_wv or not core_wv['chapters']:
                core_wv['chapters'] = {}
            chapters = core_wv['chapters']
            for i in range(1, 4):
                chapter_key = f"chapter{i}"
                if chapter_key not in chapters:
                    chapters[chapter_key] = {}
                if 'main_conflict' not in chapters[chapter_key] or not chapters[chapter_key]['main_conflict']:
                    chapters[chapter_key]['main_conflict'] = f"第{i}章：你需要完成重要的任务，面对各种挑战"
                if 'conflict_end_condition' not in chapters[chapter_key] or not chapters[chapter_key]['conflict_end_condition']:
                    chapters[chapter_key]['conflict_end_condition'] = f"完成第{i}章的主要目标"
            
            # 确保characters存在
            if 'characters' not in core_wv:
                core_wv['characters'] = {}
            if 'forces' not in core_wv:
                core_wv['forces'] = {'positive': [], 'negative': [], 'neutral': []}
            if 'main_quest' not in core_wv:
                core_wv['main_quest'] = f"完成{user_idea}的任务，达成游戏目标"
            
            global_state['core_worldview'] = core_wv
            
            # 🔑 重要：保存基调信息到global_state，确保后续生成时能正确获取
            global_state['tone'] = tone_key
            print(f"✅ 基调已保存到global_state: {tone_key} ({TONE_CONFIGS.get(tone_key, {}).get('name', '未知')})")
            
            # 验证基本完整性
            if core_wv.get('game_style') and core_wv.get('world_basic_setting') and core_wv.get('chapters'):
                # 🔑 缓存机制已删除：不再保存缓存
                # if perf_enabled and not force_full:
                #     _save_worldview_cache(cache_key, global_state)
                if staged_mode:
                    # cache_key 不再生成，传入空字符串（后台补全函数不再使用它）
                    threading.Thread(
                        target=_background_fill_worldview_details,
                        args=("", user_idea, protagonist_attr, difficulty, tone_key),
                        daemon=True
                    ).start()
                    global_state.setdefault("meta", {})["detail_async"] = True
                return global_state
            else:
                print("❌ 错误：生成的世界观不完整，将重试...")
                continue
        
        except Exception as e:
            print(f"❌ 错误：世界观生成失败（第{attempt+1}/{max_retries}次）：{str(e)}")
            if attempt < max_retries - 1:
                print("🔄 将重试生成世界观...")
                continue
    
    # 所有尝试都失败后，才返回默认世界观
    print("💡 提示：所有尝试均失败，将使用默认世界观继续游戏")
    return _get_default_worldview(user_idea, protagonist_attr, difficulty)

def _get_default_worldview(user_idea: str, protagonist_attr: Dict, difficulty: str, tone_key: str = "normal_ending") -> Dict:
    """
    获取默认世界观，当AI生成失败时使用
    """
    try:
        # 获取基调配置
        tone = TONE_CONFIGS.get(tone_key, TONE_CONFIGS["normal_ending"])
        
        default_worldview = {
            "core_worldview": {
                "game_style": "奇幻冒险",
                "world_basic_setting": f"在一个充满魔法的世界中，你是一名冒险者，踏上了{user_idea}的旅程",
                "protagonist_ability": f"你的能力取决于你的属性：颜值{protagonist_attr.get('颜值', '普通')}，智商{protagonist_attr.get('智商', '普通')}，体力{protagonist_attr.get('体力', '普通')}，魅力{protagonist_attr.get('魅力', '普通')}",
                "characters": {
                    "主角": {
                        "core_personality": "勇敢果断，充满好奇心",
                        "shallow_background": "你是一名普通的冒险者，渴望探索未知的世界",
                        "deep_background": "你有着不平凡的身世，注定要拯救这个世界。你的祖先曾是守护世界的勇者，拥有强大的魔法力量，但家族因被背叛而没落。你体内流淌着勇者的血液，这是你在冒险中逐渐觉醒的力量源泉。"
                    },
                    "配角1": {
                        "core_personality": "聪明机智，善于谋划",
                        "shallow_background": "你遇到的第一个伙伴，是一名经验丰富的向导",
                        "deep_background": "他有着自己的秘密，正在寻找失落的宝藏。实际上，他是一个古老神秘组织的成员，这个组织一直在暗中守护着世界的平衡。他寻找宝藏的真正目的是为了阻止一个即将到来的灾难。"
                    }
                },
                "forces": {
                    "positive": ["光明势力", "冒险者公会"],
                    "negative": ["黑暗军团", "邪恶巫师"],
                    "neutral": ["商人联盟", "流浪部落"]
                },
                "main_quest": f"完成{user_idea}的任务，拯救这个世界",
                "chapters": {
                    "chapter1": {
                        "main_conflict": "你需要通过森林，但是森林中充满了危险",
                        "conflict_end_condition": "找到森林中的古老神庙"
                    },
                    "chapter2": {
                        "main_conflict": "你需要获得法师公会的认可，才能继续前进",
                        "conflict_end_condition": "通过法师公会的考验"
                    },
                    "chapter3": {
                        "main_conflict": "最终决战，你需要面对邪恶巫师",
                        "conflict_end_condition": "击败邪恶巫师，拯救世界"
                    }
                },
                "end_trigger_condition": "选择结束游戏选项"
            },
            "flow_worldline": {
                "current_chapter": "chapter1",
                "tone": tone_key,  # 保存基调信息
                "characters": {
                    "主角": {
                        "thought": "我必须勇敢地面对挑战",
                        "physiology": "健康",
                        "deep_background_unlocked": False,
                        "deep_background_depth": 0
                    },
                    "配角1": {
                        "thought": "这个年轻人看起来很有潜力",
                        "physiology": "健康",
                        "deep_background_unlocked": False,
                        "deep_background_depth": 0
                    }
                },
                "environment": {
                    "weather": "晴朗",
                    "location": "森林入口",
                    "force_relationship": "各势力之间保持着微妙的平衡"
                },
                "quest_progress": "刚刚开始你的冒险",
                "chapter_conflict_solved": False,
                "info_gap_record": {
                    "entries": [],  # 存储玩家未知的隐藏信息条目
                    "current_super_choice": None,  # 当前生成的爽点剧情选项
                    "pending_super_plot": None  # 等待触发的爽点剧情
                }
            },
            # 🔑 重要：保存基调信息到顶层，确保后续生成时能正确获取
            "tone": tone_key
        }
        print(f"✅ 默认世界观已创建，基调: {tone_key} ({TONE_CONFIGS.get(tone_key, {}).get('name', '未知')})")
        return default_worldview
    except Exception as e:
        # 如果构建默认世界观失败，返回一个最基本的世界观
        return {
            "core_worldview": {
                "game_style": "奇幻冒险",
                "world_basic_setting": f"在一个充满魔法的世界中，你是一名冒险者，踏上了{user_idea}的旅程",
                "protagonist_ability": "你的能力取决于你的属性",
                "characters": {
                    "主角": {
                        "core_personality": "勇敢果断，充满好奇心",
                        "shallow_background": "你是一名普通的冒险者",
                        "deep_background": "你有着不平凡的身世，体内隐藏着强大的力量，这将在你的冒险中逐渐显现"
                    }
                },
                "forces": {
                    "positive": ["光明势力"],
                    "negative": ["黑暗势力"],
                    "neutral": ["中立势力"]
                },
                "main_quest": f"完成{user_idea}的任务",
                "chapters": {
                    "chapter1": {
                        "main_conflict": "你需要完成第一个任务",
                        "conflict_end_condition": "完成任务"
                    }
                },
                "end_trigger_condition": "选择结束游戏选项"
            },
            "flow_worldline": {
                "current_chapter": "chapter1",
                "tone": tone_key,  # 保存基调信息
                "characters": {
                    "主角": {
                        "thought": "我必须勇敢地面对挑战",
                        "physiology": "健康",
                        "deep_background_unlocked": False,
                        "deep_background_depth": 0
                    }
                },
                "environment": {
                    "weather": "晴朗",
                    "location": "森林入口",
                    "force_relationship": "各势力之间保持着微妙的平衡"
                },
                "quest_progress": "刚刚开始你的冒险",
                "chapter_conflict_solved": False,
                "info_gap_record": {
                    "entries": [],  # 存储玩家未知的隐藏信息条目
                    "current_super_choice": None,  # 当前生成的爽点剧情选项
                    "pending_super_plot": None  # 等待触发的爽点剧情
                }
            }
        }

#---------------------------------------------------------------------------------------

# 选项剪枝函数：过滤不合理、重复或过于相似的选项
def prune_options(options: List[str]) -> List[str]:
    """过滤和优化选项列表，移除不合理、重复或过于相似的选项"""
    if not options:
        return []
    
    pruned = []
    seen_keywords = []  # 使用列表存储关键词集合，因为set不能包含set
    
    for option in options:
        option = option.strip()
        if not option:
            continue
        
        # 过滤太短或太长的选项
        if len(option) < 3 or len(option) > 30:
            continue
        
        # 提取关键词（去除常见词）
        keywords = set(re.findall(r'[\u4e00-\u9fff]+', option))
        common_words = {'的', '了', '在', '是', '我', '你', '他', '她', '它', '这', '那', '一个', '可以', '应该', '需要', '继续', '查看', '返回', '选择'}
        keywords = keywords - common_words
        
        # 检查是否与已有选项过于相似（关键词重叠率>70%）
        is_similar = False
        for seen_keyword_set in seen_keywords:
            if keywords and seen_keyword_set:
                overlap = len(keywords & seen_keyword_set)
                union = len(keywords | seen_keyword_set)
                similarity = overlap / union if union > 0 else 0
                if similarity > 0.7:
                    is_similar = True
                    break
        
        if not is_similar:
            pruned.append(option)
            seen_keywords.append(keywords)  # 使用append而不是add
    
    # 如果剪枝后选项太少，至少保留前几个
    if len(pruned) < 2 and len(options) >= 2:
        pruned = options[:2]  # 保留前2个
    
    return pruned[:2]  # 最多保留2个选项

# 重构：生成单个选项剧情的独立函数
def _generate_single_option(i: int, option: str, global_state: Dict) -> Dict:
    """
    生成单个选项对应的剧情+下一层选项
    :param i: 选项索引
    :param option: 选项内容
    :param global_state: 全局状态
    :return: 包含选项索引和剧情数据的字典
    """
    perf = PERFORMANCE_OPTIMIZATION
    perf_enabled = perf.get("enabled", True)
    print(f"📝 正在生成选项 {i+1} 的剧情...")
    
    # 构建Prompt，生成当前选项对应的剧情和下一层选项
    # 获取当前基调（从global_state或默认normal_ending）
    tone_key = global_state.get('tone', 'normal_ending')
    tone = TONE_CONFIGS.get(tone_key, TONE_CONFIGS['normal_ending'])
    
    # 检查是否有已解锁的深层背景
    flow = global_state.get('flow_worldline', {})
    deep_background_unlocked_flag = flow.get('deep_background_unlocked_flag', [])
    
    # 构建已解锁深层背景的提示
    deep_bg_prompt = ""
    if deep_background_unlocked_flag:
        core = global_state.get('core_worldview', {})
        characters = core.get('characters', {})
        unlocked_deep_bgs = []
        for char_name in deep_background_unlocked_flag:
            if char_name in characters:
                deep_bg = characters[char_name].get('deep_background', '')
                unlocked_deep_bgs.append(f"{char_name}的深层背景：{deep_bg}")
        if unlocked_deep_bgs:
            deep_bg_prompt = f"\n## 【已解锁深层背景】：\n{chr(10).join(unlocked_deep_bgs)}\n### 【重要要求】：后续剧情必须围绕已解锁的深层背景展开，将深层背景信息自然融入主线剧情中，不要直接向玩家显示深层背景内容！"
    
    # 添加调试信息：打印输入数据
    print(f"🔍 调试信息：输入参数")
    print(f"   选项索引：{i+1}")
    print(f"   用户选择：{option}")
    print(f"   global_state keys：{list(global_state.keys())}")
    print(f"   core_worldview是否存在：{'core_worldview' in global_state}")
    print(f"   flow_worldline是否存在：{'flow_worldline' in global_state}")
    
    # 确保core_worldview和flow_worldline存在
    core_worldview = global_state.get('core_worldview', {})
    flow_worldline = global_state.get('flow_worldline', {})
    protagonist_canonical_block = _format_protagonist_canonical_for_prompt(global_state.get("protagonist_canonical") or {})
    
    # 判断是否是第一次生成（"开始游戏"选项）
    is_initial_scene = (option == "开始游戏" or option == "开始游戏")
    
    # 根据是否是第一次生成，调整场景描述要求
    if is_initial_scene:
        scene_requirement = """【场景】：场景描述（这是游戏的第一个场景，必须极其吸引人，要求：至少400字，必须包含以下元素：
       1. **引人入胜的开场**：必须立即抓住玩家的注意力，包含悬念、冲突或引人注目的元素
       2. **详细的环境描写**：至少100字，详细描述场景的视觉、听觉、嗅觉、触觉等感官细节，让玩家仿佛身临其境
       3. **角色反应和内心活动**：至少80字，描述主角的内心想法、情绪反应、身体感受等
       4. **对话或互动**：至少80字，包含至少2-3句对话，对话必须使用引号，对话要推动剧情或展现角色性格
       5. **悬念或冲突**：至少80字，引入一个引人好奇的问题、冲突或悬念，让玩家想要继续探索
       6. **世界观融入**：自然融入世界观设定，展现世界特色、文化背景或关键信息
       7. **主线任务暗示**：至少60字，暗示或提及主线任务，但不要直接说明，保持神秘感
       场景描述必须流畅自然，有画面感，能够立刻吸引玩家继续游戏！）"""
    else:
        scene_requirement = """【场景】：场景描述（必须是用户操作的直接结果，贴合难度和主角属性，要求：至少150字，包含环境描写、角色反应、对话等，对话必须使用引号）"""
    
    prompt = f"""
    请基于以下设定生成后续1层剧情，**严格遵守以下要求，违反任何一条都将导致任务失败**（优先级：执行用户选择 > 主线推进 > 剧情连贯 > 格式完整）：
    
    ## 【故事基调要求】：
    1. **必须严格遵循以下故事基调要求**：
       - 基调名称：{tone['name']}
       - 基调描述：{tone['description']}
       - 语言特征：{tone['language_features']}
       - 结局导向：{tone['ending_orientation']}
       - 禁忌内容：{tone['taboo_content']}
       - 所有生成内容必须严格贴合上述基调要求！
    
    ## 【最高优先级要求】：绝对执行用户选择，100%服从用户指令
    1. 用户选择了选项：{option}
    2. 必须**完全按照字面意思**执行，**绝对不能**偏离或修改用户指令
    3. 必须**立即执行**用户的指令，不能延迟或跳过
    4. 场景描述必须是：
       - **执行用户选择后**的**直接、即时结果**
       - 不能跳脱到其他场景，不能提前执行未选择的操作
       - 必须紧密贴合用户的选择，体现选择的直接影响
    5. 新生成的选项必须是：
       - **执行当前用户选择后**的**合理后续操作**
       - 必须与当前场景和状态紧密相关
       - 必须**明确推进主线任务**，每个选项都应该让主角离主线目标更近一步
       - **部分选项必须关联角色深层背景**：生成2个选项，其中0-1个选项应直接关联到某个角色的深层背景，选择这类选项会触发该角色深层背景的解锁
    {deep_bg_prompt}
    
    ## 【主线推进要求】：
    1. 必须**明确推进主线任务**，每个选择都应该带来主线进度的实质性变化
    2. 必须**保持主线的连贯性**，后续剧情必须与之前的主线进度紧密相关
    3. 必须**体现用户选择对主线的影响**，不同的选择应该导致不同的主线进展
    4. 必须**明确更新主线进度**，在【世界线更新】中的"主线进度"字段必须清晰描述当前主线的推进情况
    
    ## 【格式要求】：使用清晰的分隔符，方便提取信息
    1. 所有输出内容（包括场景描述、选项、更新日志）必须使用**中文**
    2. 不要返回任何代码块标记（如```json、```）和多余的解释说明
    3. 严格按照以下格式生成，**不要遗漏任何字段**，**不要改变分隔符**：
    4. **重要：必须正确使用标点符号和数字（这是硬性要求，违反将导致任务失败）**：
       - **对话必须使用引号**：所有人物对话必须用引号包裹，如"你好"或"你好"，绝对不能省略引号
       - **句子结尾必须使用标点**：每个句子结尾必须使用句号（。）、问号（？）或感叹号（！），绝对不能省略
       - **数字必须完整显示**：所有数字必须正常显示，如：3、10、第1章、50%、100年、第3次等，绝对不能省略、替换或写成文字
       - **列表项必须使用标点**：列表项必须使用顿号（、）或逗号（，）分隔，如：苹果、香蕉、橙子
       - **特别注意**：生成内容中绝对不能出现缺少标点符号或数字被替换的情况，这是严重错误！
    5. **对话质量要求（这是硬性要求，违反将导致任务失败）**：
       - **语言必须自然流畅**：人物对话必须符合角色性格，语言自然流畅，符合中文表达习惯
       - **避免病句和语法错误**：绝对不能出现病句、语法错误、表达不清、语序混乱等问题
       - **符合人物身份**：对话要符合人物身份、年龄、教育背景和场景氛围
       - **长度适中**：对话长度适中，不要过于冗长或过于简短，每句话控制在20-50字为宜
       - **对话要有意义**：对话必须推动剧情发展或展现角色性格，避免无意义的废话
       - **特别注意**：生成内容中绝对不能出现病句、语法错误或表达不清的情况，这是严重错误！
    
    {scene_requirement}
    【选项】：
    1. 选项1（要求：简洁明确，10-20字）
    2. 选项2（要求：简洁明确，10-20字）
    【世界线更新】：
    角色变化：简要描述角色状态变化（要求：至少50字）
    环境变化：简要描述环境变化（要求：至少50字）
    主线进度：简要描述主线任务进度的具体推进情况（要求：至少80字，必须明确说明推进了什么）
    章节矛盾：已解决/未解决
    【深层背景关联】：
    - 选项X：角色名称（如：选项2：主角）
    
    ## 【生成约束】：必须符合世界观和当前状态
    1. 生成内容必须**完全符合**核心世界观设定
    2. 必须**严格遵循**当前世界线状态
    3. 必须**考虑**主角属性和游戏难度
    4. 必须**体现**用户选择对剧情的影响
    5. 必须**确保主线任务不断推进**，不能让剧情停滞不前
    6. 必须**严格遵循选定的故事基调**，所有生成内容都必须符合基调要求
    7. **描写主角时**必须严格遵循【主角规范信息】中的性别、年龄感与外观，使用一致的人称（他/她）与外貌描述
    
    ## 【主角规范信息】（描写主角性别/年龄/外貌时必须严格遵循，与主角立绘一致）
    {protagonist_canonical_block}
    
    ## 【输入数据】：
    - 【核心世界观】：{json.dumps(core_worldview, ensure_ascii=False)}
    - 【当前状态】：{json.dumps(flow_worldline, ensure_ascii=False)}
    - 【用户选择】：{option}  # 必须100%执行此操作
    - 【故事基调】：{tone['name']}
    
    记住：
    1. 你的任务是**100%服从用户指令**，**明确推进主线任务**，生成符合要求的剧情！
    2. 必须生成部分关联角色深层背景的选项，并在【深层背景关联】中明确标记
    3. 深层背景关联的选项应自然融入剧情，不要显得突兀
    4. 所有生成内容必须严格贴合选定的故事基调！
    5. 如果有已解锁的深层背景，后续剧情必须围绕这些深层背景展开，将深层背景信息自然融入主线剧情中，不要直接向玩家显示深层背景内容！
    6. 描写主角时必须与【主角规范信息】一致（性别、年龄、外貌、人称）。
    """
    
    # 添加调试信息：打印生成的Prompt前500字符
    print(f"📝 调试信息：生成的Prompt前500字符")
    print(prompt[:500])
    
    # 构建请求体，如果是第一次生成，增加max_tokens以确保生成足够长的内容
    if perf_enabled and perf.get("optimize_tokens", True):
        initial_tokens = perf.get("plot_max_tokens_initial", 2500)
        normal_tokens = perf.get("plot_max_tokens_normal", 2000)
    else:
        initial_tokens = 3500
        normal_tokens = 2500
    max_tokens = initial_tokens if is_initial_scene else normal_tokens
    
    request_body = {
        "model": AI_API_CONFIG.get("model", ""),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,  # 适度提高温度，改善标点符号和数字生成
        "max_tokens": max_tokens,  # 根据是否是第一次生成调整token数
        "top_p": 0.7,  # 适度提高多样性，改善对话自然度
        "frequency_penalty": 0.3,  # 降低惩罚，避免过度抑制标点符号
        "presence_penalty": 0.1,  # 降低惩罚，改善对话流畅度
        "timeout": 200  # 适度降低超时时间
    }
    
    option_data = None
    
    # 内部重试机制
    max_retries = 3
    if perf_enabled and perf.get("optimize_retry", True):
        max_retries = perf.get("plot_max_retries", 2)
    for attempt in range(max_retries):
        try:
            # 调用带重试的API函数
            try:
                response_data = call_ai_api(request_body)
            except ValueError as e:
                # 如果是403/401认证错误，立即停止重试，使用默认剧情
                error_str = str(e)
                if "API认证失败" in error_str or "HTTP 403" in error_str or "HTTP 401" in error_str:
                    print(f"❌ 选项 {i+1} API认证失败，停止重试，使用默认剧情")
                    # 直接跳出循环，使用默认剧情
                    option_data = None
                    break
                else:
                    raise  # 其他ValueError也抛出
            except Exception as api_error:
                # 检查是否是认证错误
                error_str = str(api_error)
                if "403" in error_str or "401" in error_str or "Forbidden" in error_str:
                    print(f"❌ 选项 {i+1} API认证失败，停止重试，使用默认剧情")
                    option_data = None
                    break
                raise  # 其他异常继续抛出
            # 安全访问嵌套键
            choices = response_data.get("choices", [])
            if not choices or len(choices) == 0:
                print(f"❌ 错误：选项 {i+1} 的AI返回内容格式异常，缺少choices字段，将重试...")
                continue
            
            message = choices[0].get("message", {})
            if not message:
                print(f"❌ 错误：选项 {i+1} 的AI返回内容格式异常，缺少message字段，将重试...")
                continue
            
            raw_content = message.get("content", "").strip()
            if not raw_content:
                print(f"❌ 错误：选项 {i+1} 的AI返回内容为空，将重试...")
                continue
            
            # 直接从文本中提取信息，不依赖JSON解析
            # 提取场景描述
            scene = ""
            next_options = []
            flow_update = {
                "characters": {},
                "environment": {},
                "quest_progress": "",
                "chapter_conflict_solved": False
            }
            # 新增：深层背景关联信息
            deep_background_links = {}
            
            # 0. 清理AI返回的内容，移除无关文字
            cleaned_content = raw_content
            
            # 移除常见的错误提示文字 - 修复：使用更精确的正则表达式，避免匹配整个字符串
            error_patterns = [
                r'(请求.*?失败|申请.*?失败|请.*?重试|侧向请求|生化或者失败联盟|出让角1|遣代表试)',
            ]
            
            for pattern in error_patterns:
                # 修复：移除re.DOTALL标志，避免跨行匹配导致的问题
                cleaned_content = re.sub(pattern, '', cleaned_content, flags=re.IGNORECASE)
            
            # 1. 提取场景描述 - 尝试多种匹配方式，使用清理后的内容
            scene_match1 = re.search(r'【场景】：([\s\S]*?)【选项】：', cleaned_content, re.DOTALL)
            scene_match2 = re.search(r'【场景】：([\s\S]*?)$', cleaned_content, re.DOTALL)
            scene_match3 = re.search(r'【场景】：([^\n]*)', cleaned_content)
            
            if scene_match1:
                scene = scene_match1.group(1).strip()
                print(f"✅ 选项 {i+1} 场景提取成功（方式1）：{scene[:50]}...")
            elif scene_match2:
                scene = scene_match2.group(1).strip()
                print(f"✅ 选项 {i+1} 场景提取成功（方式2）：{scene[:50]}...")
            elif scene_match3:
                scene = scene_match3.group(1).strip()
                print(f"✅ 选项 {i+1} 场景提取成功（方式3）：{scene[:50]}...")
            else:
                print(f"❌ 选项 {i+1} 场景提取失败，原始内容中未找到【场景】标签")
            
            # 2. 进一步清理提取到的场景描述
            if scene:
                # 修复：清理场景描述中的错误信息，使用更精确的正则表达式
                error_patterns = [
                    r'请求.*?失败|申请.*?失败|请.*?重试|侧向请求|生化或者失败联盟|出让角1|遣代表试',
                    # 保留：中文/英文/数字（含全角）+ 常用中文标点（含省略号）+ 引号
                    r"[^一-龥a-zA-Z0-9０-９\s，。！？、：；“”‘’（）《》【】…\"']+",  # 移除其余“非法字符”，避免误删数字
                ]
                
                for pattern in error_patterns:
                    # 移除错误信息，不使用re.DOTALL避免匹配整个字符串
                    scene = re.sub(pattern, '', scene, flags=re.IGNORECASE)
                
                # 移除多余的空格和换行
                scene = scene.strip()
                
                # 确保场景描述符合预期，没有奇怪的前缀
                if len(scene) > 0:
                    # 尝试找到第一个中文字符或英文单词的位置
                    first_valid_char = re.search(r'[\u4e00-\u9fa5a-zA-Z"""“‘「【(]', scene)
                    if first_valid_char:
                        scene = scene[first_valid_char.start():]
                
                # 验证场景描述长度
                if len(scene) < 10:
                    print(f"⚠️ 选项 {i+1} 场景描述过短，可能提取不完整：{scene}")
                    # 修复：如果场景描述过短，使用默认描述，避免显示错误信息
                    scene = "你仔细观察周围的环境，准备采取行动。"
            
            # 2. 提取选项 - 尝试多种匹配方式，使用清理后的内容
            options_match1 = re.search(r'【选项】：([\s\S]*?)【世界线更新】：', cleaned_content, re.DOTALL)
            options_match2 = re.search(r'【选项】：([\s\S]*?)【深层背景关联】：', cleaned_content, re.DOTALL)
            options_match3 = re.search(r'【选项】：([\s\S]*?)$', cleaned_content, re.DOTALL)
            
            if options_match1:
                options_text = options_match1.group(1).strip()
            elif options_match2:
                options_text = options_match2.group(1).strip()
            elif options_match3:
                options_text = options_match3.group(1).strip()
            else:
                options_text = ""
            
            if options_text:
                # 解析选项行
                option_lines = options_text.split('\n')
                for line in option_lines:
                    stripped_line = line.strip()
                    if stripped_line:
                        # 移除序号和可能的点号
                        next_option = re.sub(r'^\s*\d+\.?\s*', '', stripped_line)
                        if next_option:
                            next_options.append(next_option)
            
            # 3. 提取世界线更新 - 使用正则表达式
            worldline_match = re.search(r'【世界线更新】：([\s\S]*?)(?:【深层背景关联】：|$)', raw_content, re.DOTALL)
            if worldline_match:
                worldline_text = worldline_match.group(1).strip()
                
                # 提取主线进度
                quest_progress_match = re.search(r'主线进度：([^\n]*)', worldline_text)
                if quest_progress_match:
                    flow_update["quest_progress"] = quest_progress_match.group(1).strip()
                
                # 提取章节矛盾
                chapter_conflict_match = re.search(r'章节矛盾：([^\n]*)', worldline_text)
                if chapter_conflict_match:
                    chapter_status = chapter_conflict_match.group(1).strip()
                    flow_update["chapter_conflict_solved"] = chapter_status == "已解决"
            
            # 4. 提取深层背景关联信息
            deep_bg_match = re.search(r'【深层背景关联】：([\s\S]*?)$', raw_content, re.DOTALL)
            if deep_bg_match:
                deep_bg_text = deep_bg_match.group(1).strip()
                deep_bg_lines = deep_bg_text.split('\n')
                
                for line in deep_bg_lines:
                    stripped_line = line.strip()
                    if stripped_line and "：" in stripped_line:
                        parts = stripped_line.split("：")
                        if len(parts) >= 2:
                            option_part = parts[0].strip()
                            char_name = parts[1].strip()
                            
                            # 提取选项序号
                            option_num_match = re.search(r'选项(\d+)', option_part)
                            if option_num_match:
                                option_idx = int(option_num_match.group(1)) - 1  # 转换为0-based索引
                                deep_background_links[option_idx] = char_name
            
            # 选项剪枝：过滤不合理、重复或过于相似的选项
            original_options_count = len(next_options)
            original_options = next_options.copy()  # 保存原始选项
            next_options = prune_options(next_options)
            pruned_count = len(next_options)
            
            # 如果剪枝后选项太少，使用原始选项（至少保留2个）
            if pruned_count < 2 and original_options_count >= 2:
                print(f"⚠️ 选项 {i+1} 剪枝后选项过少（{pruned_count}个），使用原始选项")
                # 使用原始选项，但确保至少有2个
                next_options = original_options[:2] if len(original_options) >= 2 else original_options
            
            # 限制选项数量为2个（确保只保留2个选项）
            if len(next_options) > 2:
                print(f"📊 选项 {i+1} 数量超过2个，限制为前2个")
                next_options = next_options[:2]
            elif len(next_options) < 2:
                # 如果选项少于2个，尝试从原始选项补充
                if original_options_count >= 2:
                    print(f"⚠️ 选项 {i+1} 剪枝后选项过少（{len(next_options)}个），使用原始选项的前2个")
                    next_options = original_options[:2]
            
            print(f"📊 选项 {i+1} 剪枝统计：原始{original_options_count}个 -> 剪枝后{len(next_options)}个")
            
            # 构建选项数据
            option_data = {
                "scene": scene,
                "next_options": next_options,
                "flow_update": flow_update,
                "deep_background_links": deep_background_links
            }
            
            # 新增：生成场景图片（使用本地缓存，避免OSS URL失效问题）
            # 修复：移除“线程 join 6分钟后丢结果”的逻辑，改为同步调用 + 可控的网络超时/重试。
            scene_image = None
            if scene:
                try:
                    # 若是初始场景且主角正面图尚未就绪：等待主角正面图生成后再生成场景图（保证场景中主角形象一致）
                    if is_initial_scene:
                        game_id = global_state.get("game_id") if isinstance(global_state, dict) else None
                        if game_id:
                            from pathlib import Path
                            import time
                            main_character_dir = Path("initial") / "main_character" / game_id
                            front_path = main_character_dir / "main_character.png"
                            wait_timeout = 120  # 最多等待 120 秒
                            poll_interval = 2
                            waited = 0
                            while not front_path.exists() and waited < wait_timeout:
                                time.sleep(poll_interval)
                                waited += poll_interval
                            if front_path.exists():
                                print(f"✅ 主角正面图已就绪，开始生成初始场景图片（等待 {waited} 秒）")
                            else:
                                print(f"⚠️ 等待主角正面图超时（{wait_timeout} 秒），将不使用主角参考图生成场景")
                    print(f"🎨 正在为选项 {i+1} 生成场景图片（启用本地缓存）...")
                    scene_image = generate_scene_image(scene, global_state, "default", use_cache=True)
                    if scene_image and scene_image.get('url'):
                        # 验证图片URL是否有效，确保返回格式正确
                        image_url = scene_image.get('url')
                        
                        # 确保URL是字符串
                        if not isinstance(image_url, str):
                            print(f"⚠️ 选项 {i+1} 图片URL不是字符串类型: {type(image_url)}")
                            image_url = str(image_url)
                            scene_image['url'] = image_url
                        
                        # 检查是否为本地路径格式（/image_cache/开头）
                        is_local_path = image_url.startswith('/image_cache/') or image_url.startswith('image_cache/')
                        
                        # 验证URL格式：本地路径或有效的HTTP(S) URL
                        if is_local_path or validate_image_url(image_url):
                            # 确保本地路径格式统一为 /image_cache/{filename}
                            if image_url.startswith('image_cache/'):
                                image_url = '/' + image_url
                                scene_image['url'] = image_url
                            
                            # 确保返回的数据格式正确（含 scene_text_hash，避免 /generate-option 误判文本变化而重复生成）
                            scene_text_hash = hashlib.md5(scene.encode("utf-8")).hexdigest() if (scene and scene.strip()) else None
                            option_data["scene_image"] = {
                                "url": image_url,
                                "prompt": scene_image.get("prompt", ""),
                                "style": scene_image.get("style", "default"),
                                "width": scene_image.get("width", 1024),
                                "height": scene_image.get("height", 1024),
                                # 本地路径表示已缓存；远程URL默认视为未缓存（除非上游明确标记）
                                "cached": True if is_local_path else scene_image.get("cached", False),
                                "scene_text_hash": scene_text_hash,
                            }
                            if is_local_path:
                                print(f"✅ 选项 {i+1} 场景图片生成成功并已保存到本地")
                                print(f"   本地路径: {image_url}")
                            else:
                                print(f"✅ 选项 {i+1} 场景图片生成成功（远程URL）")
                                print(f"   图片URL: {image_url[:80]}...")
                        else:
                            # URL无效，尝试修复（仅对HTTP(S) URL）
                            if not is_local_path:
                                fixed_url = fix_incomplete_url(image_url)
                                if fixed_url and validate_image_url(fixed_url):
                                    scene_text_hash = hashlib.md5(scene.encode("utf-8")).hexdigest() if (scene and scene.strip()) else None
                                    option_data["scene_image"] = {
                                        "url": fixed_url,
                                        "prompt": scene_image.get("prompt", ""),
                                        "style": scene_image.get("style", "default"),
                                        "width": scene_image.get("width", 1024),
                                        "height": scene_image.get("height", 1024),
                                        "cached": scene_image.get("cached", False),
                                        "scene_text_hash": scene_text_hash,
                                    }
                                    print(f"✅ 选项 {i+1} 场景图片URL修复成功: {fixed_url[:80]}...")
                                else:
                                    print(f"⚠️ 选项 {i+1} 场景图片URL无效，跳过图片: {image_url[:80]}...")
                                    scene_image = None
                            else:
                                print(f"⚠️ 选项 {i+1} 场景图片本地路径格式异常: {image_url}")
                                scene_image = None
                    else:
                        print(f"⚠️ 选项 {i+1} 场景图片生成失败，继续使用文本模式")
                except Exception as e:
                    print(f"⚠️ 选项 {i+1} 图片生成异常，继续使用文本模式：{str(e)}")
                    import traceback
                    traceback.print_exc()
            
            # ==================== 视频生成功能已禁用（性能优化） ====================
            # 新增：生成场景视频（5-10秒）
            # scene_video = None
            # if scene_image and scene_image.get('url'):  # 基于生成的图片生成视频
            #     try:
            #         print(f"🎬 正在为选项 {i+1} 生成场景视频（5-10秒）...")
            #         # 异步生成视频，返回任务ID
            #         scene_video = generate_scene_video(
            #             scene_description=scene,
            #             image_url=scene_image.get('url'),
            #             duration=random.randint(5, 10)  # 随机5-10秒
            #         )
            #         if scene_video:
            #             option_data["scene_video"] = scene_video
            #             print(f"✅ 选项 {i+1} 场景视频生成任务已启动，任务ID：{scene_video.get('task_id')}")
            #         else:
            #             print(f"⚠️ 选项 {i+1} 场景视频生成失败，继续使用图片模式")
            #     except Exception as e:
            #         print(f"⚠️ 选项 {i+1} 视频生成异常，继续使用图片模式：{str(e)}")
            scene_video = None  # 视频功能已禁用
            
            # 只有当场景描述和选项都有内容时，才返回结果
            if scene and next_options and len(next_options) >= 2:  # 至少保留2个选项
                print(f"✅ 选项 {i+1} 剧情生成成功，共{len(next_options)}个选项：{next_options}")
                break
            else:
                # 如果提取失败，继续重试
                print(f"❌ 错误：无法从选项 {i+1} 的AI返回内容中提取有效剧情信息，将重试...")
                if attempt < max_retries - 1:
                    continue
        
        except Exception as e:
            error_str = str(e)
            # 如果是认证错误（403/401），立即停止重试
            if "403" in error_str or "401" in error_str or "Forbidden" in error_str or "API认证失败" in error_str:
                print(f"❌ 选项 {i+1} API认证失败，停止重试，使用默认剧情")
                option_data = None
                break  # 立即跳出循环，不再重试
            else:
                print(f"❌ 选项 {i+1} 剧情生成失败（第{attempt+1}/{max_retries}次）：{error_str}")
                if attempt < max_retries - 1:
                    print(f"🔄 将重试生成选项 {i+1} 的剧情...")
                    continue
    
    # 如果所有尝试都失败，返回默认剧情
    if not option_data or not option_data.get("scene") or not option_data.get("next_options"):
        print(f"💡 提示：选项 {i+1} 的所有生成尝试均失败，将使用默认剧情")
        option_data = {
            "scene": f"你选择了：{option}。在你的努力下，你取得了一些进展。",
            "next_options": ["继续前进", "查看当前状态", "返回上一步"],
            "flow_update": {
                "characters": {},
                "environment": {},
                "quest_progress": f"你正在执行任务：{option}",
                "chapter_conflict_solved": False
            },
            "deep_background_links": {}
        }
        # 默认剧情不生成图片和视频
    
    return {"index": i, "data": option_data}

# 优化：只生成文本内容的版本（用于并行优化）
def _generate_single_option_text_only(i: int, option: str, global_state: Dict) -> Dict:
    """
    生成单个选项对应的剧情+下一层选项（仅文本，不含图片）
    这是优化版本，用于并行生成文本后再批量生成图片
    :param i: 选项索引
    :param option: 选项内容
    :param global_state: 全局状态
    :return: 包含选项索引、剧情数据和场景描述的字典
    """
    print(f"📝 正在生成选项 {i+1} 的剧情（文本模式）...")
    perf = PERFORMANCE_OPTIMIZATION
    perf_enabled = perf.get("enabled", True)
    
    # 构建Prompt，生成当前选项对应的剧情和下一层选项
    # 获取当前基调（从global_state或默认normal_ending）
    tone_key = global_state.get('tone', 'normal_ending')
    tone = TONE_CONFIGS.get(tone_key, TONE_CONFIGS['normal_ending'])
    
    # 检查是否有已解锁的深层背景
    flow = global_state.get('flow_worldline', {})
    deep_background_unlocked_flag = flow.get('deep_background_unlocked_flag', [])
    
    # 构建已解锁深层背景的提示
    deep_bg_prompt = ""
    if deep_background_unlocked_flag:
        core = global_state.get('core_worldview', {})
        characters = core.get('characters', {})
        unlocked_deep_bgs = []
        for char_name in deep_background_unlocked_flag:
            if char_name in characters:
                deep_bg = characters[char_name].get('deep_background', '')
                unlocked_deep_bgs.append(f"{char_name}的深层背景：{deep_bg}")
        if unlocked_deep_bgs:
            deep_bg_prompt = f"\n## 【已解锁深层背景】：\n{chr(10).join(unlocked_deep_bgs)}\n### 【重要要求】：后续剧情必须围绕已解锁的深层背景展开，将深层背景信息自然融入主线剧情中，不要直接向玩家显示深层背景内容！"
    
    # 确保core_worldview和flow_worldline存在
    core_worldview = global_state.get('core_worldview', {})
    flow_worldline = global_state.get('flow_worldline', {})
    protagonist_canonical_block = _format_protagonist_canonical_for_prompt(global_state.get("protagonist_canonical") or {})
    
    # 判断是否是第一次生成（"开始游戏"选项）
    is_initial_scene = (option == "开始游戏" or option == "开始游戏")
    
    # 根据是否是第一次生成，调整场景描述要求
    if is_initial_scene:
        scene_requirement = """【场景】：场景描述（这是游戏的第一个场景，必须极其吸引人，要求：至少400字，必须包含以下元素：
       1. **引人入胜的开场**：必须立即抓住玩家的注意力，包含悬念、冲突或引人注目的元素
       2. **详细的环境描写**：至少100字，详细描述场景的视觉、听觉、嗅觉、触觉等感官细节，让玩家仿佛身临其境
       3. **角色反应和内心活动**：至少80字，描述主角的内心想法、情绪反应、身体感受等
       4. **对话或互动**：至少80字，包含至少2-3句对话，对话必须使用引号，对话要推动剧情或展现角色性格
       5. **悬念或冲突**：至少80字，引入一个引人好奇的问题、冲突或悬念，让玩家想要继续探索
       6. **世界观融入**：自然融入世界观设定，展现世界特色、文化背景或关键信息
       7. **主线任务暗示**：至少60字，暗示或提及主线任务，但不要直接说明，保持神秘感
       场景描述必须流畅自然，有画面感，能够立刻吸引玩家继续游戏！）"""
    else:
        scene_requirement = """【场景】：场景描述（必须是用户操作的直接结果，贴合难度和主角属性，要求：至少150字，包含环境描写、角色反应、对话等，对话必须使用引号）"""
    
    prompt = f"""
    请基于以下设定生成后续1层剧情，**严格遵守以下要求，违反任何一条都将导致任务失败**（优先级：执行用户选择 > 主线推进 > 剧情连贯 > 格式完整）：
    
    ## 【故事基调要求】：
    1. **必须严格遵循以下故事基调要求**：
       - 基调名称：{tone['name']}
       - 基调描述：{tone['description']}
       - 语言特征：{tone['language_features']}
       - 结局导向：{tone['ending_orientation']}
       - 禁忌内容：{tone['taboo_content']}
       - 所有生成内容必须严格贴合上述基调要求！
    
    ## 【最高优先级要求】：绝对执行用户选择，100%服从用户指令
    1. 用户选择了选项：{option}
    2. 必须**完全按照字面意思**执行，**绝对不能**偏离或修改用户指令
    3. 必须**立即执行**用户的指令，不能延迟或跳过
    4. 场景描述必须是：
       - **执行用户选择后**的**直接、即时结果**
       - 不能跳脱到其他场景，不能提前执行未选择的操作
       - 必须紧密贴合用户的选择，体现选择的直接影响
    5. 新生成的选项必须是：
       - **执行当前用户选择后**的**合理后续操作**
       - 必须与当前场景和状态紧密相关
       - 必须**明确推进主线任务**，每个选项都应该让主角离主线目标更近一步
       - **部分选项必须关联角色深层背景**：生成2个选项，其中0-1个选项应直接关联到某个角色的深层背景，选择这类选项会触发该角色深层背景的解锁
    {deep_bg_prompt}
    
    ## 【主线推进要求】：
    1. 必须**明确推进主线任务**，每个选择都应该带来主线进度的实质性变化
    2. 必须**保持主线的连贯性**，后续剧情必须与之前的主线进度紧密相关
    3. 必须**体现用户选择对主线的影响**，不同的选择应该导致不同的主线进展
    4. 必须**明确更新主线进度**，在【世界线更新】中的"主线进度"字段必须清晰描述当前主线的推进情况
    
    ## 【格式要求】：使用清晰的分隔符，方便提取信息
    1. 所有输出内容（包括场景描述、选项、更新日志）必须使用**中文**
    2. 不要返回任何代码块标记（如```json、```）和多余的解释说明
    3. 严格按照以下格式生成，**不要遗漏任何字段**，**不要改变分隔符**：
    4. **重要：必须正确使用标点符号和数字（这是硬性要求，违反将导致任务失败）**：
       - **对话必须使用引号**：所有人物对话必须用引号包裹，如"你好"或"你好"，绝对不能省略引号
       - **句子结尾必须使用标点**：每个句子结尾必须使用句号（。）、问号（？）或感叹号（！），绝对不能省略
       - **数字必须完整显示**：所有数字必须正常显示，如：3、10、第1章、50%、100年、第3次等，绝对不能省略、替换或写成文字
       - **列表项必须使用标点**：列表项必须使用顿号（、）或逗号（，）分隔，如：苹果、香蕉、橙子
       - **特别注意**：生成内容中绝对不能出现缺少标点符号或数字被替换的情况，这是严重错误！
    5. **对话质量要求（这是硬性要求，违反将导致任务失败）**：
       - **语言必须自然流畅**：人物对话必须符合角色性格，语言自然流畅，符合中文表达习惯
       - **避免病句和语法错误**：绝对不能出现病句、语法错误、表达不清、语序混乱等问题
       - **符合人物身份**：对话要符合人物身份、年龄、教育背景和场景氛围
       - **长度适中**：对话长度适中，不要过于冗长或过于简短，每句话控制在20-50字为宜
       - **对话要有意义**：对话必须推动剧情发展或展现角色性格，避免无意义的废话
       - **特别注意**：生成内容中绝对不能出现病句、语法错误或表达不清的情况，这是严重错误！
    
    {scene_requirement}
    【选项】：
    1. 选项1（要求：简洁明确，10-20字）
    2. 选项2（要求：简洁明确，10-20字）
    【世界线更新】：
    角色变化：简要描述角色状态变化（要求：至少50字）
    环境变化：简要描述环境变化（要求：至少50字）
    主线进度：简要描述主线任务进度的具体推进情况（要求：至少80字，必须明确说明推进了什么）
    章节矛盾：已解决/未解决
    【深层背景关联】：
    - 选项X：角色名称（如：选项2：主角）
    
    ## 【生成约束】：必须符合世界观和当前状态
    1. 生成内容必须**完全符合**核心世界观设定
    2. 必须**严格遵循**当前世界线状态
    3. 必须**考虑**主角属性和游戏难度
    4. 必须**体现**用户选择对剧情的影响
    5. 必须**确保主线任务不断推进**，不能让剧情停滞不前
    6. 必须**严格遵循选定的故事基调**，所有生成内容都必须符合基调要求
    7. **描写主角时**必须严格遵循【主角规范信息】中的性别、年龄感与外观，使用一致的人称（他/她）与外貌描述
    
    ## 【主角规范信息】（描写主角性别/年龄/外貌时必须严格遵循，与主角立绘一致）
    {protagonist_canonical_block}
    
    ## 【输入数据】：
    - 【核心世界观】：{json.dumps(core_worldview, ensure_ascii=False)}
    - 【当前状态】：{json.dumps(flow_worldline, ensure_ascii=False)}
    - 【用户选择】：{option}  # 必须100%执行此操作
    - 【故事基调】：{tone['name']}
    
    记住：
    1. 你的任务是**100%服从用户指令**，**明确推进主线任务**，生成符合要求的剧情！
    2. 必须生成部分关联角色深层背景的选项，并在【深层背景关联】中明确标记
    3. 深层背景关联的选项应自然融入剧情，不要显得突兀
    4. 所有生成内容必须严格贴合选定的故事基调！
    5. 如果有已解锁的深层背景，后续剧情必须围绕这些深层背景展开，将深层背景信息自然融入主线剧情中，不要直接向玩家显示深层背景内容！
    6. 描写主角时必须与【主角规范信息】一致（性别、年龄、外貌、人称）。
    """
    
    # 构建请求体，如果是第一次生成，增加max_tokens以确保生成足够长的内容
    if perf_enabled and perf.get("optimize_tokens", True):
        initial_tokens = perf.get("plot_max_tokens_initial", 2500)
        normal_tokens = perf.get("plot_max_tokens_normal", 2000)
    else:
        initial_tokens = 3500
        normal_tokens = 2500
    max_tokens = initial_tokens if is_initial_scene else normal_tokens
    
    request_body = {
        "model": AI_API_CONFIG.get("model", ""),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
        "max_tokens": max_tokens,
        "top_p": 0.7,
        "frequency_penalty": 0.3,
        "presence_penalty": 0.1,
        "timeout": 200
    }
    
    option_data = None
    scene = None
    
    # 内部重试机制
    max_retries = 3
    if perf_enabled and perf.get("optimize_retry", True):
        max_retries = perf.get("plot_max_retries", 2)
    for attempt in range(max_retries):
        try:
            # 调用带重试的API函数
            try:
                response_data = call_ai_api(request_body)
            except ValueError as e:
                error_str = str(e)
                if "API认证失败" in error_str or "HTTP 403" in error_str or "HTTP 401" in error_str:
                    print(f"❌ 选项 {i+1} API认证失败，停止重试，使用默认剧情")
                    option_data = None
                    break
                else:
                    raise
            except Exception as api_error:
                error_str = str(api_error)
                if "403" in error_str or "401" in error_str or "Forbidden" in error_str:
                    print(f"❌ 选项 {i+1} API认证失败，停止重试，使用默认剧情")
                    option_data = None
                    break
                raise
            
            # 安全访问嵌套键
            choices = response_data.get("choices", [])
            if not choices or len(choices) == 0:
                print(f"❌ 错误：选项 {i+1} 的AI返回内容格式异常，缺少choices字段，将重试...")
                continue
            
            message = choices[0].get("message", {})
            if not message:
                print(f"❌ 错误：选项 {i+1} 的AI返回内容格式异常，缺少message字段，将重试...")
                continue
            
            raw_content = message.get("content", "").strip()
            if not raw_content:
                print(f"❌ 错误：选项 {i+1} 的AI返回内容为空，将重试...")
                continue
            
            # 直接从文本中提取信息，不依赖JSON解析
            next_options = []
            flow_update = {
                "characters": {},
                "environment": {},
                "quest_progress": "",
                "chapter_conflict_solved": False
            }
            deep_background_links = {}
            
            # 清理AI返回的内容
            cleaned_content = raw_content
            error_patterns = [
                r'(请求.*?失败|申请.*?失败|请.*?重试|侧向请求|生化或者失败联盟|出让角1|遣代表试)',
            ]
            for pattern in error_patterns:
                cleaned_content = re.sub(pattern, '', cleaned_content, flags=re.IGNORECASE)
            
            # 提取场景描述
            scene_match1 = re.search(r'【场景】：([\s\S]*?)【选项】：', cleaned_content, re.DOTALL)
            scene_match2 = re.search(r'【场景】：([\s\S]*?)$', cleaned_content, re.DOTALL)
            scene_match3 = re.search(r'【场景】：([^\n]*)', cleaned_content)
            
            if scene_match1:
                scene = scene_match1.group(1).strip()
            elif scene_match2:
                scene = scene_match2.group(1).strip()
            elif scene_match3:
                scene = scene_match3.group(1).strip()
            
            # 清理场景描述
            if scene:
                error_patterns = [
                    r'请求.*?失败|申请.*?失败|请.*?重试|侧向请求|生化或者失败联盟|出让角1|遣代表试',
                    # 保留：中文/英文/数字（含全角）+ 常用中文标点（含省略号）+ 引号
                    r"[^一-龥a-zA-Z0-9０-９\s，。！？、：；“”‘’（）《》【】…\"']+",
                ]
                for pattern in error_patterns:
                    scene = re.sub(pattern, '', scene, flags=re.IGNORECASE)
                scene = scene.strip()
                
                first_valid_char = re.search(r'[\u4e00-\u9fa5a-zA-Z"""''「【(]', scene)
                if first_valid_char:
                    scene = scene[first_valid_char.start():]
                
                if len(scene) < 10:
                    scene = "你仔细观察周围的环境，准备采取行动。"
            
            # 提取选项
            options_match1 = re.search(r'【选项】：([\s\S]*?)【世界线更新】：', cleaned_content, re.DOTALL)
            options_match2 = re.search(r'【选项】：([\s\S]*?)【深层背景关联】：', cleaned_content, re.DOTALL)
            options_match3 = re.search(r'【选项】：([\s\S]*?)$', cleaned_content, re.DOTALL)
            
            if options_match1:
                options_text = options_match1.group(1).strip()
            elif options_match2:
                options_text = options_match2.group(1).strip()
            elif options_match3:
                options_text = options_match3.group(1).strip()
            else:
                options_text = ""
            
            if options_text:
                option_lines = options_text.split('\n')
                for line in option_lines:
                    stripped_line = line.strip()
                    if stripped_line:
                        next_option = re.sub(r'^\s*\d+\.?\s*', '', stripped_line)
                        if next_option:
                            next_options.append(next_option)
            
            # 提取世界线更新
            worldline_match = re.search(r'【世界线更新】：([\s\S]*?)(?:【深层背景关联】：|$)', raw_content, re.DOTALL)
            if worldline_match:
                worldline_text = worldline_match.group(1).strip()
                
                quest_progress_match = re.search(r'主线进度：([^\n]*)', worldline_text)
                if quest_progress_match:
                    flow_update["quest_progress"] = quest_progress_match.group(1).strip()
                
                chapter_conflict_match = re.search(r'章节矛盾：([^\n]*)', worldline_text)
                if chapter_conflict_match:
                    chapter_status = chapter_conflict_match.group(1).strip()
                    flow_update["chapter_conflict_solved"] = chapter_status == "已解决"
            
            # 提取深层背景关联信息
            deep_bg_match = re.search(r'【深层背景关联】：([\s\S]*?)$', raw_content, re.DOTALL)
            if deep_bg_match:
                deep_bg_text = deep_bg_match.group(1).strip()
                deep_bg_lines = deep_bg_text.split('\n')
                
                for line in deep_bg_lines:
                    stripped_line = line.strip()
                    if stripped_line and "：" in stripped_line:
                        parts = stripped_line.split("：")
                        if len(parts) >= 2:
                            option_part = parts[0].strip()
                            char_name = parts[1].strip()
                            option_num_match = re.search(r'选项(\d+)', option_part)
                            if option_num_match:
                                option_idx = int(option_num_match.group(1)) - 1
                                deep_background_links[option_idx] = char_name
            
            # 选项剪枝
            original_options_count = len(next_options)
            original_options = next_options.copy()
            next_options = prune_options(next_options)
            pruned_count = len(next_options)
            
            # 限制选项数量为2个（确保只保留2个选项）
            if len(next_options) > 2:
                print(f"📊 选项 {i+1} 数量超过2个，限制为前2个")
                next_options = next_options[:2]
            elif len(next_options) < 2:
                # 如果选项少于2个，尝试从原始选项补充
                if original_options_count >= 2:
                    print(f"⚠️ 选项 {i+1} 剪枝后选项过少（{len(next_options)}个），使用原始选项的前2个")
                    next_options = original_options[:2] if len(original_options) >= 2 else original_options
            
            # 构建选项数据（不包含图片）
            option_data = {
                "scene": scene,
                "next_options": next_options,
                "flow_update": flow_update,
                "deep_background_links": deep_background_links
            }
            
            # 只有当场景描述和选项都有内容时，才返回结果（至少2个选项）
            if scene and next_options and len(next_options) >= 2:
                print(f"✅ 选项 {i+1} 剧情生成成功，共{len(next_options)}个选项：{next_options}")
                break
            else:
                print(f"❌ 错误：无法从选项 {i+1} 的AI返回内容中提取有效剧情信息，将重试...")
                if attempt < max_retries - 1:
                    continue
        
        except Exception as e:
            error_str = str(e)
            if "403" in error_str or "401" in error_str or "Forbidden" in error_str or "API认证失败" in error_str:
                print(f"❌ 选项 {i+1} API认证失败，停止重试，使用默认剧情")
                option_data = None
                break
            else:
                print(f"❌ 选项 {i+1} 剧情生成失败（第{attempt+1}/{max_retries}次）：{error_str}")
                if attempt < max_retries - 1:
                    print(f"🔄 将重试生成选项 {i+1} 的剧情...")
                    continue
    
    # 如果所有尝试都失败，返回默认剧情
    if not option_data or not option_data.get("scene") or not option_data.get("next_options"):
        print(f"💡 提示：选项 {i+1} 的所有生成尝试均失败，将使用默认剧情")
        option_data = {
            "scene": f"你选择了：{option}。在你的努力下，你取得了一些进展。",
            "next_options": ["继续前进", "查看当前状态", "返回上一步"],
            "flow_update": {
                "characters": {},
                "environment": {},
                "quest_progress": f"你正在执行任务：{option}",
                "chapter_conflict_solved": False
            },
            "deep_background_links": {}
        }
        scene = option_data["scene"]
    
    # 返回包含场景描述的字典，用于后续图片生成
    return {
        "index": i,
        "data": option_data,
        "scene_for_image": scene  # 保存场景描述，用于后续并行生成图片
    }

# 优化：并行生成多个场景的图片
def _generate_images_parallel(scenes_dict: Dict[int, str], global_state: Dict) -> Dict[int, Dict]:
    """
    并行生成多个场景的图片
    :param scenes_dict: 场景描述字典 {option_index: scene_description}
    :param global_state: 全局状态
    :return: 图片结果字典 {option_index: image_data}
    """
    if not scenes_dict:
        return {}
    
    print(f"🎨 开始并行生成 {len(scenes_dict)} 个场景的图片...")
    
    image_results = {}
    
    # 先检查缓存，避免重复生成
    import hashlib
    from pathlib import Path
    
    IMAGE_CACHE_DIR = "image_cache"
    os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)
    
    # 过滤需要生成的场景（检查缓存）
    scenes_to_generate = {}
    cached_images = {}
    
    for option_index, scene in scenes_dict.items():
        if not scene:
            continue
        
        # 生成缓存键
        prompt_hash = hashlib.md5(f"{scene}_default".encode()).hexdigest()
        cache_path = Path(IMAGE_CACHE_DIR) / f"{prompt_hash}.png"
        
        # 检查缓存
        if cache_path.exists():
            print(f"✅ 选项 {option_index+1} 使用缓存的图片：{cache_path}")
            cached_images[option_index] = {
                "url": f"/image_cache/{prompt_hash}.png",
                "prompt": scene[:100],
                "style": "default",
                "width": 1024,
                "height": 1024,
                "cached": True
            }
        else:
            scenes_to_generate[option_index] = scene
    
    # 如果所有图片都已缓存，直接返回
    if not scenes_to_generate:
        print(f"✅ 所有图片都已缓存，跳过生成")
        return cached_images
    
    # 并行生成图片（限制并发数，避免API限流）
    # - yunwu.ai 速率限制更严格：默认只开 1 并发（再配合全局最小间隔）
    # - 其它 provider 可适当并发
    provider = IMAGE_GENERATION_CONFIG.get("provider", "yunwu")
    default_workers = 1 if provider == "yunwu" else 2
    max_workers_env = int(os.getenv("IMAGE_PARALLEL_MAX_WORKERS", str(default_workers)))
    max_workers = max(1, min(len(scenes_to_generate), max_workers_env))
    print(f"📊 需要生成 {len(scenes_to_generate)} 张图片，使用 {max_workers} 个并发线程（provider={provider}）")
    
    def generate_single_image(option_index: int, scene: str) -> tuple:
        """生成单个图片的包装函数，返回 (option_index, image_data, error)"""
        try:
            print(f"🎨 正在为选项 {option_index+1} 生成场景图片...")
            # 使用带缓存的图片生成，会自动下载到本地
            image_data = generate_scene_image(scene, global_state, "default", use_cache=True)
            
            if image_data and image_data.get('url'):
                # 验证图片URL
                image_url = image_data.get('url')
                if not isinstance(image_url, str):
                    image_url = str(image_url)
                    image_data['url'] = image_url
                
                # 确保本地路径格式统一
                is_local_path = image_url.startswith('/image_cache/') or image_url.startswith('image_cache/')
                if image_url.startswith('image_cache/'):
                    image_url = '/' + image_url
                    image_data['url'] = image_url
                
                # 验证URL格式
                if is_local_path or validate_image_url(image_url):
                    print(f"✅ 选项 {option_index+1} 图片生成成功：{image_url[:80]}...")
                    return (option_index, image_data, None)
                else:
                    # 尝试修复URL
                    fixed_url = fix_incomplete_url(image_url)
                    if fixed_url and validate_image_url(fixed_url):
                        image_data['url'] = fixed_url
                        image_data['cached'] = False
                        print(f"✅ 选项 {option_index+1} 图片URL修复成功：{fixed_url[:80]}...")
                        return (option_index, image_data, None)
                    else:
                        print(f"⚠️ 选项 {option_index+1} 图片URL无效，跳过")
                        return (option_index, None, "URL无效")
            else:
                print(f"⚠️ 选项 {option_index+1} 图片生成失败，无返回数据")
                print(f"💡 提示：yunwu.ai API可能返回了文本描述而非图片数据，这是API行为不一致导致的")
                print(f"💡 前端可能会使用缓存的图片或其他选项的图片作为替代")
                return (option_index, None, "无返回数据")
        
        except Exception as e:
            error_msg = str(e)
            print(f"⚠️ 选项 {option_index+1} 图片生成异常：{error_msg}")
            import traceback
            traceback.print_exc()
            return (option_index, None, error_msg)
    
    # 使用线程池并行生成（添加延迟避免速率限制）
    import time
    per_task_timeout = int(os.getenv("IMAGE_TASK_TIMEOUT_SECONDS", "120"))
    submit_delay = float(os.getenv("IMAGE_SUBMIT_DELAY_SECONDS", "2.0"))
    total_images = len(scenes_to_generate)
    completed_images = 0
    failed_images = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务（添加延迟避免同时发送过多请求）
        futures = {}
        for idx, (option_index, scene) in enumerate(scenes_to_generate.items()):
            # 如果不是第一个任务，添加延迟（避免同时发送过多请求触发速率限制）
            if idx > 0:
                if submit_delay > 0:
                    print(f"⏳ 等待 {submit_delay:.1f} 秒后提交下一个图片生成任务（避免API速率限制）...")
                    time.sleep(submit_delay)
            future = executor.submit(generate_single_image, option_index, scene)
            futures[option_index] = future
        
        # 收集结果（带超时控制，避免单张图卡住整轮）
        for option_index, future in futures.items():
            completed_images += 1
            print(f"🎨 图片生成进度：{completed_images}/{total_images}")
            try:
                result = future.result(timeout=per_task_timeout)
                result_option_index, image_data, error = result
                
                if error:
                    failed_images += 1
                    print(f"⚠️ 选项 {result_option_index+1} 图片生成失败：{error}")
                elif image_data:
                    image_results[result_option_index] = image_data
                else:
                    failed_images += 1
                    print(f"⚠️ 选项 {result_option_index+1} 图片生成失败，无数据返回")
            
            except TimeoutError as e:
                # 🔧 修复：单独处理 TimeoutError，避免被通用异常处理掩盖
                failed_images += 1
                print(f"⚠️ 选项 {option_index+1} 图片生成超时（{per_task_timeout}s），将跳过该图片")
                print(f"💡 提示：图片生成任务可能因为API响应慢或网络问题而超时")
                # 尝试取消该任务，释放资源（但任务可能已经在执行，无法取消）
                try:
                    future.cancel()
                except:
                    pass
            except Exception as e:
                error_msg = str(e)
                failed_images += 1
                # 检查是否是超时相关的异常（包括 concurrent.futures.TimeoutError）
                if isinstance(e, (TimeoutError, type(None))) or "timeout" in error_msg.lower() or "超时" in error_msg or "TimeoutError" in str(type(e)):
                    print(f"⚠️ 选项 {option_index+1} 图片生成超时（{per_task_timeout}s），将跳过该图片")
                    print(f"💡 提示：图片生成任务可能因为API响应慢或网络问题而超时")
                    # 尝试取消任务
                    try:
                        future.cancel()
                    except:
                        pass
                else:
                    print(f"⚠️ 选项 {option_index+1} 图片生成异常：{error_msg}")
                    print(f"   异常类型：{type(e).__name__}")
                import traceback
                traceback.print_exc()
    
    # 合并缓存的结果和生成的结果
    image_results.update(cached_images)
    
    if failed_images:
        print(f"⚠️ 图片生成完成但有 {failed_images} 个失败，可稍后输入保存/退出后再重试或选择跳过图片。")
    print(f"✅ 图片生成完成，成功生成 {len(image_results)} 张图片（包含缓存）")
    return image_results

# 重构：实现并行批量预生成（优化版）
def generate_all_options(global_state: Dict, current_options: List[str], skip_images: bool = False) -> Dict:
    """
    生成当前场景下所有可选选项对应的剧情+下一层选项，并返回完整的剧情数据
    优化版：使用两阶段并行处理，提高生成效率
    阶段1：并行生成所有选项的文本内容（场景描述、选项等）
    阶段2：并行生成所有场景的图片并缓存
    """
    if not global_state or not current_options:
        return {}
    if not AI_API_CONFIG["api_key"]:
        print("❌ 错误：未配置Camera_Analyst_API_KEY，请在.env文件中设置")
        return {}
    
    perf = PERFORMANCE_OPTIMIZATION
    perf_enabled = perf.get("enabled", True)
    stream_first = perf_enabled and perf.get("stream_first_option", True)
    print(f"📝 开始并行生成 {len(current_options)} 个选项的剧情（优化版：两阶段并行）...")
    
    # ========== 阶段1：并行生成所有选项的文本内容 ==========
    print(f"📝 阶段1：并行生成 {len(current_options)} 个选项的文本内容...")
    all_options_data = {}
    scenes_for_images = {}  # 用于收集需要生成图片的场景描述 {option_index: scene_description}
    
    # 使用线程池并行生成文本内容
    text_workers = min(len(current_options), 4)
    with ThreadPoolExecutor(max_workers=text_workers) as executor:
        # 提交所有选项的文本生成任务
        futures = []
        for i, option in enumerate(current_options):
            future = executor.submit(_generate_single_option_text_only, i, option, global_state)
            futures.append(future)
        
        first_ready = None
        completed = 0
        total = len(futures)
        # 收集所有任务结果（支持流式先返回第一个完成的选项）
        for future in as_completed(futures):
            completed += 1
            print(f"📝 文本生成进度：{completed}/{total}")
            try:
                result = future.result()
                option_index = result["index"]
                option_data = result["data"]
                all_options_data[option_index] = option_data
                
                if stream_first and first_ready is None:
                    first_ready = {option_index: option_data}
                    global_state.setdefault("stream_first_option", {}).update(first_ready)
                    print(f"🚀 第一条选项文本已完成并缓存（流式）：{option_index+1}")
                
                # 收集需要生成图片的场景描述
                scene_for_image = result.get("scene_for_image")
                if scene_for_image:
                    scenes_for_images[option_index] = scene_for_image
            except Exception as e:
                print(f"❌ 选项文本生成异常：{str(e)}")
                import traceback
                traceback.print_exc()
    
    print(f"✅ 阶段1完成：所有选项文本内容生成完成，共 {len(all_options_data)} 个选项")
    
    # ========== 阶段2：并行生成所有场景的图片 ==========
    if skip_images:
        print("⏩ 已选择跳过本轮图片生成以加速。")
    elif scenes_for_images:
        print(f"🎨 阶段2：并行生成 {len(scenes_for_images)} 个场景的图片...")
        try:
            # 并行生成所有图片（包含缓存检查和错误处理）
            image_results = _generate_images_parallel(scenes_for_images, global_state)
            
            # 将图片结果合并回选项数据（含 scene_text_hash，确保图片与文本一一对应）
            for option_index, image_data in image_results.items():
                if option_index in all_options_data and image_data:
                    # 验证图片数据格式
                    if image_data.get('url'):
                        scene_text = all_options_data[option_index].get("scene", "") or ""
                        scene_text_hash = hashlib.md5(scene_text.encode("utf-8")).hexdigest() if scene_text.strip() else None
                        all_options_data[option_index]["scene_image"] = {
                            "url": image_data.get("url"),
                            "prompt": image_data.get("prompt", ""),
                            "style": image_data.get("style", "default"),
                            "width": image_data.get("width", 1024),
                            "height": image_data.get("height", 1024),
                            "cached": image_data.get("cached", True),
                            "scene_text_hash": scene_text_hash,
                        }
                        print(f"✅ 选项 {option_index+1} 图片已合并到选项数据")
                    else:
                        print(f"⚠️ 选项 {option_index+1} 图片数据无效，跳过")
                else:
                    print(f"⚠️ 选项 {option_index+1} 图片数据为空，跳过")
            
            print(f"✅ 阶段2完成：图片生成完成，成功合并 {len(image_results)} 张图片")
        except Exception as e:
            print(f"⚠️ 图片生成阶段出现异常：{str(e)}")
            import traceback
            traceback.print_exc()
            # 即使图片生成失败，也返回文本内容
    else:
        print(f"💡 阶段2跳过：没有需要生成图片的场景")
    
    print(f"✅ 所有选项生成完成，共生成 {len(all_options_data)} 个选项的剧情（包含文本和图片）")
    return all_options_data

# 重构：适配新的批量预生成机制
def llm_generate_local(global_state: Dict, user_interaction: str, last_options: List[str]) -> List[Dict]:
    """生成1层递进剧情，适配章节矛盾、难度、主角属性（强制贴合用户选择+自动重试）"""
    if not global_state or not user_interaction.strip():
        return []
    if not AI_API_CONFIG["api_key"]:
        print("❌ 错误：未配置Camera_Analyst_API_KEY，请在.env文件中设置")
        return []
    
    # 解析用户选择
    selected_option_idx = -1
    try:
        selected_option_idx = int(user_interaction) - 1
        if selected_option_idx < 0 or selected_option_idx >= len(last_options):
            print("❌ 错误：无效的选项序号")
            return []
    except ValueError:
        print("❌ 错误：请输入有效的数字序号")
        return []
    
    # 检查缓存中是否有当前选项的剧情
    if "current" in global_state:
        current_scene_data = global_state["current"]
        if "all_options" in current_scene_data and selected_option_idx in current_scene_data["all_options"]:
            option_data = current_scene_data["all_options"][selected_option_idx]
            return [{"scene": option_data["scene"], "options": option_data["next_options"], "flow_update": option_data["flow_update"]}]
    
    # 如果缓存中没有，使用原始方式生成
    print("⚠️ 缓存中未找到对应选项的剧情，使用原始方式生成...")
    
    # 修改Prompt：不再强制JSON格式，改为使用清晰的分隔符，方便文本提取
    # 获取当前基调（从global_state或默认normal_ending）
    tone_key = global_state.get('tone', 'normal_ending')
    tone = TONE_CONFIGS.get(tone_key, TONE_CONFIGS['normal_ending'])
    protagonist_canonical_block = _format_protagonist_canonical_for_prompt(global_state.get("protagonist_canonical") or {})
    
    prompt = f"""
    请基于以下设定生成后续1层剧情，**严格遵守以下要求，违反任何一条都将导致任务失败**（优先级：执行用户选择 > 主线推进 > 剧情连贯 > 格式完整）：
    
    ## 【故事基调要求】：
    1. **必须严格遵循以下故事基调要求**：
       - 基调名称：{tone['name']}
       - 基调描述：{tone['description']}
       - 语言特征：{tone['language_features']}
       - 结局导向：{tone['ending_orientation']}
       - 禁忌内容：{tone['taboo_content']}
       - 所有生成内容必须严格贴合上述基调要求！
    
    ## 【最高优先级要求】：绝对执行用户选择，100%服从用户指令
    1. 若用户输入是数字序号（如1/2/3）：
       - 首先**精确匹配**上一轮的选项列表：{json.dumps(last_options, ensure_ascii=False)}
       - 严格执行对应序号的选项操作，**绝对不能**执行其他选项的操作
       - 例如用户输入"2"，必须执行第2个选项，**绝对不能**执行1或3的操作
    2. 若用户输入是文本指令：
       - 必须**完全按照字面意思**执行，**绝对不能**偏离或修改用户指令
       - 必须**立即执行**用户的指令，不能延迟或跳过
    3. 场景描述必须是：
       - **执行用户选择后**的**直接、即时结果**
       - 不能跳脱到其他场景，不能提前执行未选择的操作
       - 必须紧密贴合用户的选择，体现选择的直接影响
    4. 新生成的选项必须是：
       - **执行当前用户选择后**的**合理后续操作**
       - 不能回到未选择的操作分支
       - 必须与当前场景和状态紧密相关
       - **部分选项必须关联角色深层背景**：生成2个选项，其中0-1个选项应直接关联到某个角色的深层背景，选择这类选项会触发该角色深层背景的解锁
    
    ## 【格式要求】：使用清晰的分隔符，方便提取信息
    1. 所有输出内容（包括场景描述、选项、更新日志）必须使用**中文**
    2. 不要返回任何代码块标记（如```json、```）和多余的解释说明
    3. 严格按照以下格式生成，**不要遗漏任何字段**，**不要改变分隔符**：
    4. **重要：必须正确使用标点符号和数字（这是硬性要求，违反将导致任务失败）**：
       - **对话必须使用引号**：所有人物对话必须用引号包裹，如"你好"或"你好"，绝对不能省略引号
       - **句子结尾必须使用标点**：每个句子结尾必须使用句号（。）、问号（？）或感叹号（！），绝对不能省略
       - **数字必须完整显示**：所有数字必须正常显示，如：3、10、第1章、50%、100年、第3次等，绝对不能省略、替换或写成文字
       - **列表项必须使用标点**：列表项必须使用顿号（、）或逗号（，）分隔，如：苹果、香蕉、橙子
       - **特别注意**：生成内容中绝对不能出现缺少标点符号或数字被替换的情况，这是严重错误！
    5. **对话质量要求（这是硬性要求，违反将导致任务失败）**：
       - **语言必须自然流畅**：人物对话必须符合角色性格，语言自然流畅，符合中文表达习惯
       - **避免病句和语法错误**：绝对不能出现病句、语法错误、表达不清、语序混乱等问题
       - **符合人物身份**：对话要符合人物身份、年龄、教育背景和场景氛围
       - **长度适中**：对话长度适中，不要过于冗长或过于简短，每句话控制在20-50字为宜
       - **对话要有意义**：对话必须推动剧情发展或展现角色性格，避免无意义的废话
       - **特别注意**：生成内容中绝对不能出现病句、语法错误或表达不清的情况，这是严重错误！
    
    【场景】：场景描述（必须是用户操作的直接结果，贴合难度和主角属性，要求：至少150字，包含环境描写、角色反应、对话等，对话必须使用引号）
    【选项】：
    1. 选项1（要求：简洁明确，10-20字）
    2. 选项2（要求：简洁明确，10-20字）
    【世界线更新】：
    角色变化：简要描述角色状态变化（要求：至少50字）
    环境变化：简要描述环境变化（要求：至少50字）
    主线进度：简要描述主线任务进度（要求：至少80字，必须明确说明推进了什么）
    章节矛盾：已解决/未解决
    【深层背景关联】：
    - 选项X：角色名称（如：选项2：主角）
    
    ## 【生成约束】：必须符合世界观和当前状态
    1. 生成内容必须**完全符合**核心世界观设定
    2. 必须**严格遵循**当前世界线状态
    3. 必须**考虑**主角属性和游戏难度
    4. 必须**体现**用户选择对剧情的影响
    5. 必须**严格遵循选定的故事基调**，所有生成内容都必须符合基调要求
    6. **描写主角时**必须严格遵循【主角规范信息】中的性别、年龄感与外观，使用一致的人称（他/她）与外貌描述
    
    ## 【主角规范信息】（描写主角性别/年龄/外貌时必须严格遵循，与主角立绘一致）
    {protagonist_canonical_block}
    
    ## 【输入数据】：
    - 【核心世界观】：{json.dumps(global_state['core_worldview'], ensure_ascii=False)}
    - 【当前状态】：{json.dumps(global_state['flow_worldline'], ensure_ascii=False)}
    - 【用户交互】：{user_interaction}  # 必须100%执行此操作
    - 【上一轮选项】：{json.dumps(last_options, ensure_ascii=False)}  # 用于解析序号对应的操作
    - 【故事基调】：{tone['name']}
    
    记住：
    1. 你的任务是**100%服从用户指令**，生成符合要求的剧情！
    2. 必须生成部分关联角色深层背景的选项，并在【深层背景关联】中明确标记
    3. 深层背景关联的选项应自然融入剧情，不要显得突兀
    4. 所有生成内容必须严格贴合选定的故事基调！
    5. 描写主角时必须与【主角规范信息】一致（性别、年龄、外貌、人称）。
    """
    
    # 构建请求体，不强制要求JSON格式
    request_body = {
        "model": AI_API_CONFIG.get("model", ""),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,  # 适度提高温度，改善标点符号和数字生成
        "max_tokens": 2500,  # 增加最大令牌数，确保生成完整的内容
        "top_p": 0.7,  # 适度提高多样性，改善对话自然度
        "frequency_penalty": 0.3,  # 降低惩罚，避免过度抑制标点符号
        "presence_penalty": 0.1,  # 降低惩罚，改善对话流畅度
        "timeout": 200  # 适度降低超时时间
    }

    # 内部重试机制，最多尝试3次生成和解析
    for attempt in range(3):
        try:
            print(f"📝 尝试生成剧情（第{attempt+1}/3次）...")
            # 调用带重试的API函数
            response_data = call_ai_api(request_body)
            # 安全访问嵌套键
            choices = response_data.get("choices", [])
            if not choices or len(choices) == 0:
                print("❌ 错误：AI返回内容格式异常，缺少choices字段，将重试...")
                continue
            
            message = choices[0].get("message", {})
            if not message:
                print("❌ 错误：AI返回内容格式异常，缺少message字段，将重试...")
                continue
            
            raw_content = message.get("content", "").strip()
            if not raw_content:
                print("❌ 错误：AI返回内容为空，将重试...")
                continue
            
            # 直接从文本中提取信息，不依赖JSON解析
            # 提取场景描述
            scene = ""
            options = []
            flow_update = {
                "characters": {},
                "environment": {},
                "quest_progress": "",
                "chapter_conflict_solved": False
            }
            # 新增：深层背景关联信息
            deep_background_links = {}
            
            # 处理原始文本
            lines = raw_content.split('\n')
            
            # 提取场景描述
            scene_start = False
            for line in lines:
                if "【场景】：" in line:
                    scene = line.split("【场景】：")[1].strip()
                    break
            
            # 提取选项
            options_start = False
            for line in lines:
                if "【选项】：" in line:
                    options_start = True
                    continue
                if options_start and line.startswith("【世界线更新】"):
                    break
                if options_start and line.strip():
                    # 提取选项内容，移除序号
                    if line.strip():
                        # 移除序号和可能的点号
                        option = re.sub(r'^\s*\d+\.?\s*', '', line.strip())
                        options.append(option)
            
            # 提取世界线更新
            update_start = False
            for line in lines:
                if "【世界线更新】：" in line:
                    update_start = True
                    continue
                if update_start and line.startswith("【深层背景关联】"):
                    break
                if update_start:
                    if "角色变化：" in line:
                        # 简单处理，不解析复杂的角色变化
                        pass
                    elif "环境变化：" in line:
                        # 简单处理，不解析复杂的环境变化
                        pass
                    elif "主线进度：" in line:
                        quest_progress = line.split("主线进度：")[1].strip()
                        flow_update["quest_progress"] = quest_progress
                    elif "章节矛盾：" in line:
                        chapter_status = line.split("章节矛盾：")[1].strip()
                        if chapter_status == "已解决":
                            flow_update["chapter_conflict_solved"] = True
            
            # 新增：提取深层背景关联信息
            links_start = False
            for line in lines:
                if "【深层背景关联】：" in line:
                    links_start = True
                    continue
                if links_start and line.strip():
                    # 提取选项与角色的关联
                    if "：" in line:
                        parts = line.split("：")
                        if len(parts) >= 2:
                            option_part = parts[0].strip()
                            char_name = parts[1].strip()
                            # 提取选项序号
                            match = re.search(r'选项(\d+)', option_part)
                            if match:
                                option_idx = int(match.group(1)) - 1  # 转换为0-based索引
                                deep_background_links[option_idx] = char_name
            
            # 构建场景数据，包含深层背景关联信息
            scene_data = {
                "scene": scene,
                "options": options,
                "flow_update": flow_update,
                "deep_background_links": deep_background_links
            }
            
            # 只有当场景描述和选项都有内容时，才返回结果
            if scene and options:
                return [scene_data]
            else:
                # 如果提取失败，继续重试
                print("❌ 错误：无法从AI返回内容中提取有效剧情信息，将重试...")
                if attempt < 2:
                    continue
        
        except Exception as e:
            print(f"❌ 剧情生成失败（第{attempt+1}/3次）：{str(e)}")
            if attempt < 2:
                print("🔄 将重试生成剧情...")
                continue
    
    # 所有尝试都失败后，才返回默认剧情
    print("💡 提示：所有尝试均失败，将使用默认剧情继续游戏")
    return _get_default_scene(user_interaction, global_state)

def _get_default_scene(user_interaction: str, global_state: Dict) -> List[Dict]:
    """
    获取默认剧情，当AI生成失败时使用
    """
    # 构建默认剧情
    default_scene = {
        "scene": f"你选择了：{user_interaction}。在你的努力下，你取得了一些进展。",
        "options": [
            "继续前进",
            "查看当前状态",
            "返回上一步"
        ],
        "flow_update": {
            "characters": {},
            "environment": {},
            "quest_progress": f"你正在执行任务：{user_interaction}",
            "chapter_conflict_solved": False
        }
    }
    return [default_scene]

# ------------------------------
# 游戏核心类（【核心修改2】传递上一轮选项）
# ------------------------------
class TextAdventureGame:
    def __init__(self):
        self.global_state: Dict = {}
        self.is_running: bool = True
        self.ending_triggered: bool = False
        self.protagonist_attr: Dict = {}
        self.difficulty: str = ""
        self.last_options: List[str] = []  # 记录上一轮的选项
        self.save_dir: str = "saves"  # 存档目录
        
        # 新增：缓存相关属性
        self.scene_cache: Dict = {}  # 场景缓存，key为场景ID，value为2个选项的完整剧情数据
        self.current_scene_id: str = "initial"  # 当前场景ID
        self.generating_task = None  # 异步生成任务
        self.generation_cancelled = False  # 生成取消标志
        self.skip_images: bool = False  # 是否跳过图片生成以加速
        self.max_autosaves: int = 5  # 自动存档最多保留数量
        
        # 确保存档目录存在
        if not os.path.exists(self.save_dir):
            os.makedirs(self.save_dir)

    def _select_protagonist_attr(self):
        print("\n🎭 请为你的主角选择属性：")
        for attr_name, options in PROTAGONIST_ATTR_OPTIONS.items():
            print(f"\n{attr_name}选项：")
            for idx, opt in enumerate(options, 1):
                print(f"   {idx}. {opt}")
            while True:
                try:
                    choice_str = safe_input(f"请选择{attr_name}（输入序号1-{len(options)}，默认1）：", default="1")
                    choice = int(choice_str)
                    if 1 <= choice <= len(options):
                        self.protagonist_attr[attr_name] = options[choice-1]
                        break
                    else:
                        print(f"请输入1-{len(options)}之间的数字！")
                except ValueError:
                    print("请输入有效的数字序号！")
        print(f"\n✅ 你的主角属性：{self.protagonist_attr}")

    def _select_difficulty(self):
        print("\n⚔️ 请选择游戏难度：")
        difficulty_list = list(DIFFICULTY_SETTINGS.keys())
        for idx, diff in enumerate(difficulty_list, 1):
            desc = DIFFICULTY_SETTINGS[diff]
            print(f"   {idx}. {diff} - 容错率：{desc['剧情容错率']}，矛盾难度：{desc['矛盾解决难度']}，提示频率：{desc['提示频率']}")
        while True:
            try:
                choice_str = safe_input(f"请选择难度（输入序号1-{len(difficulty_list)}，默认2中等）：", default="2")
                choice = int(choice_str)
                if 1 <= choice <= len(difficulty_list):
                    self.difficulty = difficulty_list[choice-1]
                    break
                else:
                    print(f"请输入1-{len(difficulty_list)}之间的数字！")
            except ValueError:
                print("请输入有效的数字序号！")
        print(f"\n✅ 游戏难度已选择：{self.difficulty}")
    
    def _select_tone(self):
        """
        基调选择环节：可选AI随机/玩家手动选择
        """
        print("\n🎨 请选择故事基调：")
        print("1. AI随机选择")
        print("2. 手动选择")
        
        while True:
            choice = safe_input("请选择操作（输入序号1-2，默认1随机）：", default="1")
            if choice == "1":
                # AI随机选择基调
                import random
                tone_key = random.choice(list(TONE_CONFIGS.keys()))
                tone = TONE_CONFIGS[tone_key]
                print(f"\n🎲 AI随机选择了基调：{tone['name']}")
                print(f"📝 基调描述：{tone['description']}")
                return tone_key
            elif choice == "2":
                # 手动选择基调
                print("\n🎨 可选基调：")
                tone_list = list(TONE_CONFIGS.items())
                for idx, (key, tone) in enumerate(tone_list, 1):
                    print(f"   {idx}. {tone['name']} - {tone['description'][:30]}...")
                
                while True:
                    try:
                        tone_choice_str = safe_input(f"请选择基调（输入序号1-{len(tone_list)}，默认1）：", default="1")
                        tone_choice = int(tone_choice_str)
                        if 1 <= tone_choice <= len(tone_list):
                            tone_key, tone = tone_list[tone_choice-1]
                            print(f"\n✅ 你选择了基调：{tone['name']}")
                            print(f"📝 基调描述：{tone['description']}")
                            return tone_key
                        else:
                            print(f"请输入1-{len(tone_list)}之间的数字！")
                    except ValueError:
                        print("请输入有效的数字序号！")
            else:
                print("请输入1-2之间的数字！")

    def _show_game_settings(self):
        if not self.global_state:
            return
        core = self.global_state.get('core_worldview', {})
        flow = self.global_state.get('flow_worldline', {})
        
        # 安全获取当前章节
        current_chapter_id = flow.get('current_chapter', 'chapter1')
        chapters = core.get('chapters', {})
        current_chapter = chapters.get(current_chapter_id, {})
        
        # 获取章节编号（用于显示）
        chapter_num = 1
        if current_chapter_id.startswith('chapter'):
            try:
                chapter_num = int(current_chapter_id[7:])
            except (ValueError, IndexError):
                chapter_num = 1
        
        print("\n📖 游戏核心设定告知：")
        print(f"1. 游戏风格：{core.get('game_style', '未知')}")
        print(f"2. 世界观基础：{core.get('world_basic_setting', '')[:50]}...")
        print(f"3. 主角核心能力：{core.get('protagonist_ability', '未知')}")
        print(f"4. 当前章节（第{chapter_num}章）核心矛盾：{current_chapter.get('main_conflict', '未知')}")
        print(f"5. 章节结束条件：{current_chapter.get('conflict_end_condition', '未知')}")
        
        # 安全获取难度信息
        difficulty_info = DIFFICULTY_SETTINGS.get(self.difficulty, {})
        print(f"6. 游戏难度：{self.difficulty}（{difficulty_info.get('矛盾解决难度', '未知')}难度）")
        print(f"7. 主线任务：{core.get('main_quest', '')[:50]}...")
        safe_input("\n请按回车键确认并开始游戏...", default="")

    def _check_chapter_conflict(self):
        flow = self.global_state.get('flow_worldline', {})
        if flow.get('chapter_conflict_solved', False):
            current_chapter = flow.get('current_chapter', 'chapter1')
            print(f"\n🎉 本章（{current_chapter}）核心矛盾已解决！章节结束。")
            # 自动快速存档（防止断档丢进度）
            auto_name = f"auto_{current_chapter}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            self.save_game(auto_name)
            self._prune_autosaves()
            
            # 章节深化：每完成一个章节，自动深化角色的深层背景
            self._deepen_character_backgrounds()
            
            while True:
                end_choice = safe_input("是否选择结束游戏？（输入 是/否，默认否）：", default="否")
                if end_choice in ["是", "否"]:
                    if end_choice == "是":
                        self.ending_triggered = True
                    else:
                        core = self.global_state.get('core_worldview', {})
                        chapters = core.get('chapters', {})
                        chapter_list = list(chapters.keys())
                        
                        if current_chapter in chapter_list:
                            current_idx = chapter_list.index(current_chapter)
                            if current_idx + 1 < len(chapter_list):
                                next_chapter = chapter_list[current_idx + 1]
                                # 安全更新世界线状态
                                if 'flow_worldline' not in self.global_state:
                                    self.global_state['flow_worldline'] = {}
                                self.global_state['flow_worldline']['current_chapter'] = next_chapter
                                self.global_state['flow_worldline']['chapter_conflict_solved'] = False
                                print(f"\n🔄 进入下一章：{next_chapter}")
                                
                                # 安全获取下一章核心矛盾
                                next_chapter_data = chapters.get(next_chapter, {})
                                print(f"本章核心矛盾：{next_chapter_data.get('main_conflict', '未知')}")
                            else:
                                print("\n📚 所有章节已完成！")
                                self.ending_triggered = True
                        else:
                            print("\n📚 无法找到当前章节信息，游戏结束！")
                            self.ending_triggered = True
                    break
                else:
                    print("请输入 是 或 否！")
    
    def _check_info_gap_threshold(self):
        """
        检查信息差数量是否达到阈值，若达到则生成隐藏的剧情深化内容
        """
        core = self.global_state.get('core_worldview', {})
        flow = self.global_state.get('flow_worldline', {})
        
        # 确保信息差记录点存在
        if 'info_gap_record' not in flow:
            flow['info_gap_record'] = {
                "entries": [],
                "current_super_choice": None,
                "pending_super_plot": None
            }
        
        info_gap_record = flow['info_gap_record']
        entries = info_gap_record['entries']
        
        # 计算未发现的信息差数量
        undiscovered_entries = [entry for entry in entries if not entry.get('discovered', False)]
        
        # 如果未发现的信息差数量达到5条，生成隐藏的剧情深化内容
        if len(undiscovered_entries) >= 5:
            # 检查是否已有等待触发的隐藏剧情
            if info_gap_record.get('pending_super_plot') is None:
                # 调用AI生成隐藏的剧情深化内容
                if AI_API_CONFIG.get("api_key"):
                    try:
                        # 构建信息差摘要
                        info_gap_summary = "\n".join([f"- {entry['content'][:100]}..." for entry in undiscovered_entries[:5]])
                        
                        # 构建Prompt，生成隐藏的剧情深化内容
                        prompt = f"""
                        请根据以下信息差内容，生成一个自然嵌入到常规剧情中的深化内容，**严格遵守以下要求**：
                        
                        ## 【信息差摘要】
                        {info_gap_summary}
                        
                        ## 【游戏世界观】
                        {json.dumps(core, ensure_ascii=False)}
                        
                        ## 【当前游戏状态】
                        {json.dumps(flow, ensure_ascii=False)}
                        
                        ## 【生成要求】
                        1. 自然嵌入到常规剧情中，不能作为独立模块强行插入
                        2. 深度贴合游戏的核心剧情脉络，是主线情节的有机延伸
                        3. 通过深层背景信息与已有剧情的前后呼应、关键悬念的逐步揭晓，让玩家感受到揭秘、反转带来的惊喜
                        4. 生成内容要符合游戏世界观和当前状态
                        5. 输出格式：
                           - 首先输出剧情触发选项描述（自然融入常规选项中，无特殊标记）
                           - 然后输出完整的剧情内容
                           - 使用### 选项：和### 剧情：作为分隔符
                        6. 剧情应包含多个隐藏信息的自然揭露，形成前后呼应
                        
                        记住：你的任务是生成一个自然融入主线的剧情深化内容，提升玩家的沉浸感和惊喜感！
                        """
                        
                        # 构建请求体
                        request_body = {
                            "model": AI_API_CONFIG["model"],
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.5,
                            "max_tokens": 2000,
                            "top_p": 0.7,
                            "frequency_penalty": 0.5,
                            "presence_penalty": 0.2,
                            "timeout": 150
                        }
                        
                        # 调用AI API
                        response_data = call_ai_api(request_body)
                        
                        # 提取AI响应
                        choices = response_data.get("choices", [])
                        if choices and len(choices) > 0:
                            message = choices[0].get("message", {})
                            raw_content = message.get("content", "").strip()
                            
                            # 解析生成的内容
                            if "### 选项：" in raw_content and "### 剧情：" in raw_content:
                                option_part = raw_content.split("### 选项：")[1].split("### 剧情：")[0].strip()
                                plot_part = raw_content.split("### 剧情：")[1].strip()
                                
                                # 保存隐藏的剧情深化内容
                                info_gap_record['pending_super_plot'] = {
                                    "plot": plot_part,
                                    "used_entries": [entry['id'] for entry in undiscovered_entries[:5]]
                                }
                                info_gap_record['current_super_choice'] = option_part
                    except Exception as e:
                        # 生成失败时不向玩家显示任何信息
                        pass
        
    def _deepen_character_backgrounds(self):
        """
        章节深化：每完成一个章节，自动深化角色的深层背景内容
        """
        print("\n🔍 章节深化：开始深化角色深层背景...")
        
        core = self.global_state.get('core_worldview', {})
        characters = core.get('characters', {})
        flow = self.global_state.get('flow_worldline', {})
        flow_characters = flow.get('characters', {})
        
        # 为每个角色添加深化进度字段（如果不存在）
        for char_name in characters:
            if char_name not in flow_characters:
                flow_characters[char_name] = {
                    "thought": "",
                    "physiology": "健康",
                    "deep_background_unlocked": False
                }
            
            # 确保角色有深化进度字段
            if "deep_background_depth" not in flow_characters[char_name]:
                flow_characters[char_name]["deep_background_depth"] = 0
            
            # 增加深化进度
            flow_characters[char_name]["deep_background_depth"] += 1
            depth = flow_characters[char_name]["deep_background_depth"]
            
            # 如果AI API可用，调用AI深化背景
            if AI_API_CONFIG.get("api_key"):
                try:
                    print(f"📝 正在深化{char_name}的深层背景（深度：{depth}）...")
                    
                    # 构建Prompt，深化角色深层背景
                    prompt = f"""
                    请根据以下信息深化角色的深层背景内容，**严格遵守以下要求**：
                    
                    ## 【角色信息】
                    角色名称：{char_name}
                    当前深层背景：{characters[char_name].get('deep_background', '暂无')}
                    角色核心性格：{characters[char_name].get('core_personality', '未知')}
                    角色浅层背景：{characters[char_name].get('shallow_background', '未知')}
                    当前章节：{flow.get('current_chapter', 'chapter1')}
                    主线进度：{flow.get('quest_progress', '未知')}
                    深化深度：第{depth}次深化
                    
                    ## 【深化要求】
                    1. 补充更多细节，使深层背景更加丰富
                    2. 将深层背景与主线任务更紧密地关联
                    3. 保持原有的核心设定不变
                    4. 深化内容要符合游戏世界观
                    5. 输出格式：直接输出深化后的深层背景内容，不要添加任何前缀或后缀
                    
                    记住：你的任务是深化角色的深层背景，使其更加丰富和关联主线！
                    """
                    
                    # 构建请求体
                    request_body = {
                        "model": AI_API_CONFIG["model"],
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.4,
                        "max_tokens": 1000,
                        "top_p": 0.7,
                        "frequency_penalty": 0.5,
                        "presence_penalty": 0.2,
                        "timeout": 100
                    }
                    
                    # 调用AI API
                    response_data = call_ai_api(request_body)
                    
                    # 提取AI响应
                    choices = response_data.get("choices", [])
                    if choices and len(choices) > 0:
                        message = choices[0].get("message", {})
                        new_background = message.get("content", "").strip()
                        
                        if new_background:
                            # 更新角色的深层背景
                            characters[char_name]['deep_background'] = new_background
                            print(f"✅ {char_name}的深层背景已深化至第{depth}级")
                            
                            # 记录信息差条目
                            if 'info_gap_record' not in flow:
                                flow['info_gap_record'] = {
                                    "entries": [],
                                    "current_super_choice": None,
                                    "pending_super_plot": None
                                }
                            info_gap_record = flow['info_gap_record']
                            
                            info_gap_entry = {
                                "id": f"info_gap_{len(info_gap_record['entries']) + 1}",
                                "type": "deep_background_deepen",
                                "char_name": char_name,
                                "content": new_background,
                                "discovered": False,
                                "timestamp": str(datetime.now())
                            }
                            info_gap_record['entries'].append(info_gap_entry)
                            
                            # 触发深层背景节点，修改结局主基调
                            trigger_event = f"{char_name}的深层背景已深化至第{depth}级"
                            tone_changed = modify_ending_tone(self.global_state, trigger_event)
                            if tone_changed:
                                print("🔄 结局主基调已更新")
                except Exception as e:
                    print(f"❌ 深化{char_name}的深层背景失败：{str(e)}")
            else:
                # AI API不可用，使用默认深化
                old_background = characters[char_name]['deep_background']
                new_background = old_background + f"\n（第{depth}章深化：角色经历更加丰富，与主线的关联更加紧密）"
                characters[char_name]['deep_background'] = new_background
                print(f"✅ {char_name}的深层背景已使用默认方式深化至第{depth}级")
                
                # 记录信息差条目
                if 'info_gap_record' not in flow:
                    flow['info_gap_record'] = {
                        "entries": [],
                        "current_super_choice": None,
                        "pending_super_plot": None
                    }
                info_gap_record = flow['info_gap_record']
                
                info_gap_entry = {
                    "id": f"info_gap_{len(info_gap_record['entries']) + 1}",
                    "type": "deep_background_deepen",
                    "char_name": char_name,
                    "content": new_background,
                    "discovered": False,
                    "timestamp": str(datetime.now())
                }
                info_gap_record['entries'].append(info_gap_entry)
        
        print("\n✅ 所有角色深层背景深化完成！")
        
        # 检查信息差阈值
        self._check_info_gap_threshold()

    def start(self):
        print("🎮 欢迎来到AI驱动的沉浸式文本冒险游戏！")
        while self.is_running:
            # 显示主菜单
            print("\n=== 游戏主菜单 ===")
            print("1. 开始新游戏")
            print("2. 加载游戏")
            print("3. 存档管理")
            print("4. 退出游戏")
            
            menu_choice = safe_input("请选择操作（输入序号1-4，默认4退出）：", default="4")
            
            if menu_choice == "1":
                # 开始新游戏
                self._select_protagonist_attr()
                self._select_difficulty()
                # 新增：基调选择环节
                selected_tone = self._select_tone()
                user_idea = safe_input("\n请输入你的游戏主题（如：玄幻修仙·寻找九转金丹）：")
                if not user_idea:
                    print("⚠️ 主题不能为空，已取消")
                    continue
                
                print("✅ AI正在构建完整游戏世界观，这可能需要1-3分钟，请耐心等待...")
                self.global_state = llm_generate_global(user_idea, self.protagonist_attr, self.difficulty, selected_tone)
                if not self.global_state:
                    print("❌ 世界观生成失败，请重新输入主题！")
                    continue
                
                # 将选定的基调保存到global_state中
                self.global_state['tone'] = selected_tone
                
                # 生成并保存隐藏的结局预测
                ending_prediction = generate_ending_prediction(self.global_state)
                self.global_state['hidden_ending_prediction'] = ending_prediction
                print("✅ 隐藏结局预测已生成")

                self._show_game_settings()
                
                # 进入游戏循环
                self._interaction_loop()
            
            elif menu_choice == "2":
                # 加载游戏
                saves = self.list_saves()
                if not saves:
                    print("\n📭 暂无存档")
                    continue
                
                print("\n📋 现有存档：")
                for idx, save_name in enumerate(saves, 1):
                    print(f"   {idx}. {save_name}")
                
                load_choice = safe_input("请选择要加载的存档序号：")
                try:
                    load_idx = int(load_choice) - 1
                    if 0 <= load_idx < len(saves):
                        if self.load_game(saves[load_idx]):
                            # 生成前情提要
                            self._generate_recap()
                            # 加载成功后直接进入游戏循环
                            self._interaction_loop()
                    else:
                        print("❌ 无效的存档序号")
                except ValueError:
                    print("❌ 请输入有效的数字序号")
            
            elif menu_choice == "3":
                # 存档管理
                if self._manage_saves():
                    # 从存档管理中成功加载了游戏，直接进入游戏循环
                    self._interaction_loop()
            
            elif menu_choice == "4":
                # 退出游戏
                print("\n👋 感谢游玩！游戏已退出。")
                self.is_running = False
                break
            
            else:
                print("❌ 请输入1-4之间的数字")

    def _interaction_loop(self):
        """【核心修改3】记录上一轮选项，传递给llm_generate_local"""
        # 本轮是否跳过图片生成（玩家可选加速）
        skip_choice = safe_input("是否跳过本局图片生成以加速？（是/否，默认否）：", default="否")
        self.skip_images = skip_choice == "是"
        # 初始剧情生成和预生成
        print("✅ 正在生成初始剧情和选项，请稍候...")
        # 使用原始方式生成初始剧情
        initial_scenes = llm_generate_local(self.global_state, "1", ["开始游戏"])
        if not initial_scenes:
            print("❌ 初始剧情生成失败，游戏结束！")
            return
        
        # 展示初始剧情
        for i, scene in enumerate(initial_scenes, 1):
            print(f"\n--- 第 {i} 段剧情 ---")
            print(f"📜 场景：{scene.get('scene', '无场景描述')}")
            
            # 安全获取选项
            options = scene.get("options", [])
            if options:
                print("🔍 可选操作：")
                # 记录当前选项为“下一轮的上一轮选项”
                self.last_options = options
                for idx, opt in enumerate(options, 1):
                    print(f"   {idx}. {opt}")
            else:
                print("🔍 可选操作：无")
                self.last_options = []
                self.current_scene_id = "initial"

            if 'flow_update' in scene:
                # 安全更新世界线状态
                if 'flow_worldline' not in self.global_state:
                    self.global_state['flow_worldline'] = {}
                self.global_state['flow_worldline'].update(scene['flow_update'])
                
                # 检查角色深层背景解锁
                characters_update = scene['flow_update'].get('characters', {})
                for char_name, char_info in characters_update.items():
                    if char_info.get('deep_background_unlocked'):
                        core = self.global_state.get('core_worldview', {})
                        characters = core.get('characters', {})
                        char_data = characters.get(char_name, {})
                        deep_bg = char_data.get('deep_background', '')
                        print(f"\n🔓 解锁角色深层背景：{char_name} → {deep_bg}")
        
        # 生成初始选项对应的剧情（同步生成）
        print("\n✅ 正在生成选项对应的剧情，请稍候...")
        if self.last_options:
            # 同步生成所有选项对应的剧情
            self.current_scene_id = f"scene_{len(self.scene_cache) + 1}"
            all_options_data = generate_all_options(self.global_state, self.last_options, skip_images=self.skip_images)
            self.scene_cache[self.current_scene_id] = all_options_data
            print(f"✅ 所有选项剧情生成完成，场景ID：{self.current_scene_id}")
        
        # 进入游戏循环
        while not self.ending_triggered:
            # 快速提示当前进度，减少玩家迷茫
            self._quick_recap()
            user_input = safe_input("\n请输入你的选择/行动（'quit'退出，'save'存档）：")
            
            # 检查退出命令
            if user_input.lower() in ['quit', 'exit', '退出', '结束']:
                # 提供存档选项
                while True:
                    save_choice = safe_input("\n是否保存当前游戏进度？（输入 是/否，默认否）：", default="否")
                    if save_choice in ["是", "否"]:
                        if save_choice == "是":
                            save_name = safe_input("请输入存档名称（默认auto_quit）：", default="auto_quit")
                            if save_name:
                                self.save_game(save_name)
                        self.ending_triggered = True
                        break
                    else:
                        print("请输入 是 或 否！")
                break
            
            # 检查保存命令
            if user_input.lower() in ['save', '保存']:
                save_name = safe_input("\n请输入存档名称（默认auto_save）：", default="auto_save")
                if save_name:
                    self.save_game(save_name)
                continue
                
            if not user_input:
                print("⏳ 请输入有效的交互内容！")
                continue

            # 解析用户选择
            try:
                selected_option_idx = int(user_input) - 1
                if selected_option_idx < 0 or selected_option_idx >= len(self.last_options):
                    print("❌ 错误：无效的选项序号")
                    continue
            except ValueError:
                print("❌ 错误：请输入有效的数字序号")
                continue

            # 检查是否选择了爽点剧情选项
            flow = self.global_state.get('flow_worldline', {})
            info_gap_record = flow.get('info_gap_record', {})
            current_super_choice = info_gap_record.get('current_super_choice')
            pending_super_plot = info_gap_record.get('pending_super_plot')
            
            selected_option = self.last_options[selected_option_idx]
            
            # 如果选择了爽点剧情选项
            if current_super_choice and current_super_choice == selected_option:
                print("\n" + "="*50)
                
                if pending_super_plot:
                    # 显示爽点剧情（作为常规剧情的一部分，无特殊标记）
                    print(pending_super_plot['plot'])
                    
                    # 清除使用过的信息差条目
                    used_entries = pending_super_plot.get('used_entries', [])
                    entries = info_gap_record.get('entries', [])
                    
                    for entry in entries:
                        if entry['id'] in used_entries:
                            entry['discovered'] = True
                    
                    # 清除当前的爽点剧情选项和等待触发的剧情
                    info_gap_record['current_super_choice'] = None
                    info_gap_record['pending_super_plot'] = None
                    
                    print("="*50)
                    
                    # 检查信息差阈值，生成新的爽点剧情
                    self._check_info_gap_threshold()
                    
                    # 重新显示当前可选操作
                    print("\n🔍 可选操作：")
                    for idx, opt in enumerate(self.last_options, 1):
                        print(f"   {idx}. {opt}")
                    
                    continue

            # 检查当前场景ID对应的缓存是否存在
            if self.current_scene_id in self.scene_cache:
                print("✅ 从缓存中读取剧情数据...")
                # 从缓存中获取剧情数据
                scene_data = self.scene_cache[self.current_scene_id]
                
                if selected_option_idx in scene_data:
                    option_data = scene_data[selected_option_idx]
                    
                    # 检查当前选项是否关联到深层背景
                    if 'deep_background_links' in option_data and selected_option_idx in option_data['deep_background_links']:
                        char_name = option_data['deep_background_links'][selected_option_idx]
                        core = self.global_state.get('core_worldview', {})
                        characters = core.get('characters', {})
                        
                        if char_name in characters:
                            # 解锁该角色的深层背景
                            flow = self.global_state.get('flow_worldline', {})
                            flow_characters = flow.get('characters', {})
                            
                            if char_name not in flow_characters:
                                flow_characters[char_name] = {
                                    "thought": "",
                                    "physiology": "健康",
                                    "deep_background_unlocked": False,
                                    "deep_background_depth": 0
                                }
                            
                            # 只有在未解锁状态下才解锁，同一个深层背景不会被反复解锁
                            if not flow_characters[char_name].get('deep_background_unlocked', False):
                                flow_characters[char_name]['deep_background_unlocked'] = True
                                deep_bg = characters[char_name].get('deep_background', '无')
                                
                                # 获取信息差记录点
                                if 'info_gap_record' not in self.global_state['flow_worldline']:
                                    self.global_state['flow_worldline']['info_gap_record'] = {
                                        "entries": [],
                                        "current_super_choice": None,
                                        "pending_super_plot": None
                                    }
                                info_gap_record = self.global_state['flow_worldline']['info_gap_record']
                                
                                # 记录信息差条目
                                info_gap_entry = {
                                    "id": f"info_gap_{len(info_gap_record['entries']) + 1}",
                                    "type": "deep_background_unlock",
                                    "char_name": char_name,
                                    "content": deep_bg,
                                    "discovered": False,
                                    "timestamp": str(datetime.now())
                                }
                                info_gap_record['entries'].append(info_gap_entry)
                                
                                # 触发深层背景节点，修改结局主基调
                                trigger_event = f"{char_name}的深层背景被解锁"
                                tone_changed = modify_ending_tone(self.global_state, trigger_event)
                                
                                # 后续剧情会因深层剧情的解锁，转而围绕深层剧情展开（通过修改global_state中的相关标志实现）
                                # 这里添加一个标志，让后续剧情生成时围绕已解锁的深层背景展开
                                if 'deep_background_unlocked_flag' not in flow:
                                    flow['deep_background_unlocked_flag'] = []
                                if char_name not in flow['deep_background_unlocked_flag']:
                                    flow['deep_background_unlocked_flag'].append(char_name)
                    
                    # 展示选中的剧情
                    print(f"\n--- 第 {1} 段剧情 ---")
                    print(f"📜 场景：{option_data['scene']}")
                    
                    # 更新世界线
                    if 'flow_update' in option_data:
                        # 安全更新世界线状态
                        if 'flow_worldline' not in self.global_state:
                            self.global_state['flow_worldline'] = {}
                        self.global_state['flow_worldline'].update(option_data['flow_update'])
                        
                        # 检查角色深层背景解锁
                        characters_update = option_data['flow_update'].get('characters', {})
                        for char_name, char_info in characters_update.items():
                            if char_info.get('deep_background_unlocked'):
                                core = self.global_state.get('core_worldview', {})
                                characters = core.get('characters', {})
                                char_data = characters.get(char_name, {})
                                deep_bg = char_data.get('deep_background', '')
                                print(f"\n🔓 解锁角色深层背景：{char_name} → {deep_bg}")
                    
                    # 生成下一轮选项对应的剧情（同步生成）
                    next_options = option_data['next_options']
                    
                    # 检查是否存在等待触发的爽点剧情
                    flow = self.global_state.get('flow_worldline', {})
                    info_gap_record = flow.get('info_gap_record', {})
                    current_super_choice = info_gap_record.get('current_super_choice')
                    
                    # 如果存在爽点剧情选项，添加到当前选项列表中（无明显标记）
                    if current_super_choice:
                        next_options.append(current_super_choice)
                    
                    if next_options:
                        print("🔍 可选操作：")
                        # 记录当前选项为“下一轮的上一轮选项”
                        self.last_options = next_options
                        for idx, opt in enumerate(next_options, 1):
                            print(f"   {idx}. {opt}")
                    
                        # 生成下一轮选项对应的剧情（同步生成）
                        print("\n✅ 生成选项对应的剧情...")
                        # 删除当前场景的缓存，释放内存
                        del self.scene_cache[self.current_scene_id]
                        # 生成新的场景ID
                        self.current_scene_id = f"scene_{len(self.scene_cache) + 1}"
                        # 同步生成所有选项对应的剧情
                        all_options_data = generate_all_options(self.global_state, next_options, skip_images=self.skip_images)
                        self.scene_cache[self.current_scene_id] = all_options_data
                        print(f"✅ 所有选项剧情生成完成，场景ID：{self.current_scene_id}")
                    else:
                        print("🔍 可选操作：无")
                        self.last_options = []
                        self.current_scene_id = "initial"
                    
                    # 检查信息差阈值
                    self._check_info_gap_threshold()
                else:
                    print("❌ 错误：缓存中未找到对应的选项数据")
                    # 使用原始方式生成剧情
                    print("✅ AI正在生成后续剧情...")
                    
                    # 删除当前场景的旧缓存，释放内存
                    if self.current_scene_id in self.scene_cache:
                        del self.scene_cache[self.current_scene_id]
                        print(f"✅ 已删除旧场景缓存：{self.current_scene_id}")
                    
                    local_scenes = llm_generate_local(self.global_state, user_input, self.last_options)
                    
                    if local_scenes:
                        # 展示剧情
                        for i, scene in enumerate(local_scenes, 1):
                            # 检查当前选项是否关联到深层背景（针对当前选择的选项）
                            if 'deep_background_links' in scene and selected_option_idx in scene['deep_background_links']:
                                char_name = scene['deep_background_links'][selected_option_idx]
                                core = self.global_state.get('core_worldview', {})
                                characters = core.get('characters', {})
                                
                                if char_name in characters:
                                    # 解锁该角色的深层背景
                                    flow = self.global_state.get('flow_worldline', {})
                                    flow_characters = flow.get('characters', {})
                                    
                                    if char_name not in flow_characters:
                                        flow_characters[char_name] = {
                                            "thought": "",
                                            "physiology": "健康",
                                            "deep_background_unlocked": False,
                                            "deep_background_depth": 0
                                        }
                                    
                                    # 只有在未解锁状态下才解锁，同一个深层背景不会被反复解锁
                                    if not flow_characters[char_name].get('deep_background_unlocked', False):
                                        flow_characters[char_name]['deep_background_unlocked'] = True
                                        deep_bg = characters[char_name].get('deep_background', '无')
                                        
                                        # 获取信息差记录点
                                        if 'info_gap_record' not in flow:
                                            flow['info_gap_record'] = {
                                                "entries": [],
                                                "current_super_choice": None,
                                                "pending_super_plot": None
                                            }
                                        info_gap_record = flow['info_gap_record']
                                        
                                        # 记录信息差条目
                                        info_gap_entry = {
                                            "id": f"info_gap_{len(info_gap_record['entries']) + 1}",
                                            "type": "deep_background_unlock",
                                            "char_name": char_name,
                                            "content": deep_bg,
                                            "discovered": False,
                                            "timestamp": str(datetime.now())
                                        }
                                        info_gap_record['entries'].append(info_gap_entry)
                                        
                                        # 触发深层背景节点，修改结局主基调
                                        trigger_event = f"{char_name}的深层背景被解锁"
                                        tone_changed = modify_ending_tone(self.global_state, trigger_event)
                                        
                                        # 添加标志，让后续剧情生成时围绕已解锁的深层背景展开
                                        if 'deep_background_unlocked_flag' not in flow:
                                            flow['deep_background_unlocked_flag'] = []
                                        if char_name not in flow['deep_background_unlocked_flag']:
                                            flow['deep_background_unlocked_flag'].append(char_name)
                            
                            print(f"\n--- 第 {i} 段剧情 ---")
                            print(f"📜 场景：{scene.get('scene', '无场景描述')}")
                            
                            # 安全获取选项
                            options = scene.get("options", [])
                            if options:
                                print("🔍 可选操作：")
                                # 记录当前选项为“下一轮的上一轮选项”
                                self.last_options = options
                                for idx, opt in enumerate(options, 1):
                                    print(f"   {idx}. {opt}")
                                
                                # 生成下一轮选项对应的剧情（同步生成）
                                print("\n✅ 生成选项对应的剧情...")
                                # 生成新的场景ID
                                self.current_scene_id = f"scene_{len(self.scene_cache) + 1}"
                                # 同步生成所有选项对应的剧情
                                all_options_data = generate_all_options(self.global_state, options, skip_images=self.skip_images)
                                self.scene_cache[self.current_scene_id] = all_options_data
                                print(f"✅ 所有选项剧情生成完成，场景ID：{self.current_scene_id}")
                            else:
                                print("🔍 可选操作：无")
                                self.last_options = []
                                self.current_scene_id = "initial"

                            if 'flow_update' in scene:
                                # 安全更新世界线状态
                                if 'flow_worldline' not in self.global_state:
                                    self.global_state['flow_worldline'] = {}
                                self.global_state['flow_worldline'].update(scene['flow_update'])
                                
                                # 检查角色深层背景解锁
                                characters_update = scene['flow_update'].get('characters', {})
                                for char_name, char_info in characters_update.items():
                                    if char_info.get('deep_background_unlocked'):
                                        core = self.global_state.get('core_worldview', {})
                                        characters = core.get('characters', {})
                                        char_data = characters.get(char_name, {})
                                        deep_bg = char_data.get('deep_background', '')
                                        print(f"\n🔓 解锁角色深层背景：{char_name} → {deep_bg}")
            else:
                # 使用原始方式生成剧情
                print("✅ AI正在生成后续剧情...")
                
                # 删除当前场景的旧缓存，释放内存
                if self.current_scene_id in self.scene_cache:
                    del self.scene_cache[self.current_scene_id]
                    print(f"✅ 已删除旧场景缓存：{self.current_scene_id}")
                
                local_scenes = llm_generate_local(self.global_state, user_input, self.last_options)
                
                if local_scenes:
                    # 展示剧情
                    for i, scene in enumerate(local_scenes, 1):
                        print(f"\n--- 第 {i} 段剧情 ---")
                        print(f"📜 场景：{scene.get('scene', '无场景描述')}")
                        
                        # 安全获取选项
                        options = scene.get("options", [])
                        if options:
                            print("🔍 可选操作：")
                            # 记录当前选项为“下一轮的上一轮选项”
                            self.last_options = options
                            for idx, opt in enumerate(options, 1):
                                print(f"   {idx}. {opt}")
                            
                            # 生成下一轮选项对应的剧情（同步生成）
                            print("\n✅ 生成选项对应的剧情...")
                            # 生成新的场景ID
                            self.current_scene_id = f"scene_{len(self.scene_cache) + 1}"
                            # 同步生成所有选项对应的剧情
                            all_options_data = generate_all_options(self.global_state, options, skip_images=self.skip_images)
                            self.scene_cache[self.current_scene_id] = all_options_data
                            print(f"✅ 所有选项剧情生成完成，场景ID：{self.current_scene_id}")
                        else:
                            print("🔍 可选操作：无")
                            self.last_options = []
                            self.current_scene_id = "initial"

                        if 'flow_update' in scene:
                            # 安全更新世界线状态
                            if 'flow_worldline' not in self.global_state:
                                self.global_state['flow_worldline'] = {}
                            self.global_state['flow_worldline'].update(scene['flow_update'])
                            
                            # 检查角色深层背景解锁
                            characters_update = scene['flow_update'].get('characters', {})
                            for char_name, char_info in characters_update.items():
                                if char_info.get('deep_background_unlocked'):
                                    core = self.global_state.get('core_worldview', {})
                                    characters = core.get('characters', {})
                                    char_data = characters.get(char_name, {})
                                    deep_bg = char_data.get('deep_background', '')
                                    print(f"\n🔓 解锁角色深层背景：{char_name} → {deep_bg}")
            
            # 用户每完成一次交互选择后，修改结局大致内容
            modify_ending_content(self.global_state)

            self._check_chapter_conflict()
            if self.ending_triggered:
                self._trigger_ending()
                break

    def save_game(self, save_name: str) -> bool:
        """
        保存游戏状态到文件
        :param save_name: 存档名称
        :return: 是否保存成功
        """
        if not self.global_state:
            print("❌ 无法保存：游戏状态为空")
            return False
        
        try:
            # 构造存档数据
            save_data = {
                "global_state": self.global_state,
                "protagonist_attr": self.protagonist_attr,
                "difficulty": self.difficulty,
                "last_options": self.last_options,
                "timestamp": str(datetime.now())
            }
            
            # 生成存档文件名
            save_filename = f"{save_name}.json"
            save_path = os.path.join(self.save_dir, save_filename)
            
            # 保存到文件
            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)
            
            print(f"✅ 游戏已保存到：{save_path}")
            return True
        except Exception as e:
            print(f"❌ 保存游戏失败：{str(e)}")
            return False

    def _prune_autosaves(self):
        """自动存档数量控制，保留最近的N个自动存档"""
        try:
            files = []
            for file in os.listdir(self.save_dir):
                if file.startswith("auto_") and file.endswith(".json"):
                    path = os.path.join(self.save_dir, file)
                    files.append((os.path.getmtime(path), path))
            files.sort(reverse=True)  # 新的在前
            if len(files) > self.max_autosaves:
                for _, path in files[self.max_autosaves:]:
                    try:
                        os.remove(path)
                        print(f"🧹 已清理旧自动存档：{path}")
                    except Exception as clean_err:
                        print(f"⚠️ 清理自动存档失败：{clean_err}")
        except Exception as e:
            print(f"⚠️ 自动存档清理出错：{e}")
    
    def load_game(self, save_name: str) -> bool:
        """
        从文件加载游戏状态
        :param save_name: 存档名称
        :return: 是否加载成功
        """
        try:
            # 生成存档文件名
            save_filename = f"{save_name}.json"
            save_path = os.path.join(self.save_dir, save_filename)
            
            # 检查文件是否存在
            if not os.path.exists(save_path):
                print(f"❌ 存档文件不存在：{save_path}")
                return False
            
            # 读取存档数据
            with open(save_path, 'r', encoding='utf-8') as f:
                save_data = json.load(f)
            
            # 恢复游戏状态
            self.global_state = save_data.get("global_state", {})
            self.protagonist_attr = save_data.get("protagonist_attr", {})
            self.difficulty = save_data.get("difficulty", "")
            self.last_options = save_data.get("last_options", [])
            
            # 重置游戏结束标志
            self.ending_triggered = False
            
            print(f"✅ 游戏已从：{save_path} 加载")
            return True
        except Exception as e:
            print(f"❌ 加载游戏失败：{str(e)}")
            return False
    
    def list_saves(self) -> List[str]:
        """
        列出所有存档
        :return: 存档名称列表
        """
        try:
            # 获取所有json文件
            saves = []
            for file in os.listdir(self.save_dir):
                if file.endswith('.json'):
                    save_name = file[:-5]  # 去掉.json后缀
                    saves.append(save_name)
            return saves
        except Exception as e:
            print(f"❌ 列出存档失败：{str(e)}")
            return []
    
    def _manage_saves(self):
        """
        存档管理界面
        """
        while True:
            print("\n📁 存档管理")
            print("1. 列出所有存档")
            print("2. 查看存档详情")
            print("3. 保存当前游戏")
            print("4. 加载游戏")
            print("5. 返回游戏")
            
            choice = safe_input("请选择操作（输入序号1-5，默认5返回）：", default="5")
            
            if choice == "1":
                # 列出所有存档
                saves = self.list_saves()
                if not saves:
                    print("\n📭 暂无存档")
                else:
                    print("\n📋 现有存档：")
                    for idx, save_name in enumerate(saves, 1):
                        print(f"   {idx}. {save_name}")
            
            elif choice == "2":
                # 查看存档详情
                saves = self.list_saves()
                if not saves:
                    print("\n📭 暂无存档")
                    continue
                
                print("\n📋 现有存档：")
                for idx, save_name in enumerate(saves, 1):
                    print(f"   {idx}. {save_name}")
                
                detail_choice = safe_input("请选择要查看的存档序号：")
                try:
                    detail_idx = int(detail_choice) - 1
                    if 0 <= detail_idx < len(saves):
                        self._show_save_detail(saves[detail_idx])
                    else:
                        print("❌ 无效的存档序号")
                except ValueError:
                    print("❌ 请输入有效的数字序号")
            
            elif choice == "3":
                # 保存当前游戏
                save_name = safe_input("\n请输入存档名称（默认auto_manual）：", default="auto_manual")
                if not save_name:
                    print("❌ 存档名称不能为空")
                    continue
                self.save_game(save_name)
            
            elif choice == "4":
                # 加载游戏
                saves = self.list_saves()
                if not saves:
                    print("\n📭 暂无存档")
                    continue
                
                print("\n📋 现有存档：")
                for idx, save_name in enumerate(saves, 1):
                    print(f"   {idx}. {save_name}")
                
                load_choice = safe_input("请选择要加载的存档序号：")
                try:
                    load_idx = int(load_choice) - 1
                    if 0 <= load_idx < len(saves):
                        if self.load_game(saves[load_idx]):
                            # 生成前情提要
                            self._generate_recap()
                            # 加载成功后返回游戏循环
                            return True
                    else:
                        print("❌ 无效的存档序号")
                except ValueError:
                    print("❌ 请输入有效的数字序号")
            
            elif choice == "5":
                # 返回游戏
                return False
            
            else:
                print("❌ 请输入1-5之间的数字")
    
    def _generate_recap(self):
        """生成游戏前情提要"""
        if not self.global_state:
            return
        
        core = self.global_state.get('core_worldview', {})
        flow = self.global_state.get('flow_worldline', {})
        
        # 获取当前章节信息
        current_chapter_id = flow.get('current_chapter', 'chapter1')
        chapters = core.get('chapters', {})
        current_chapter = chapters.get(current_chapter_id, {})
        
        # 获取章节编号（用于显示）
        chapter_num = 1
        if current_chapter_id.startswith('chapter'):
            try:
                chapter_num = int(current_chapter_id[7:])
            except (ValueError, IndexError):
                chapter_num = 1
        
        # 生成前情提要
        print("\n📋 前情提要：")
        print(f"1. 当前章节：第{chapter_num}章")
        print(f"2. 核心矛盾：{current_chapter.get('main_conflict', '未知')}")
        print(f"3. 主线进度：{flow.get('quest_progress', '未知')}")
        print(f"4. 矛盾状态：{'已解决' if flow.get('chapter_conflict_solved', False) else '未解决'}")
        print(f"5. 当前位置：{flow.get('environment', {}).get('location', '未知')}")
        
        # 显示当前可选操作（如果有）
        if self.last_options:
            print("\n🔍 你当前可以进行的操作：")
            for idx, opt in enumerate(self.last_options, 1):
                print(f"   {idx}. {opt}")
        
        safe_input("\n请按回车键继续游戏...", default="")

    def _quick_recap(self):
        """
        轻量级提示：每轮输入前快速提醒核心信息，减少玩家迷茫
        """
        if not self.global_state:
            return
        core = self.global_state.get('core_worldview', {})
        flow = self.global_state.get('flow_worldline', {})
        current_chapter_id = flow.get('current_chapter', 'chapter1')
        chapter_num = 1
        if current_chapter_id.startswith('chapter'):
            try:
                chapter_num = int(current_chapter_id[7:])
            except (ValueError, IndexError):
                chapter_num = 1
        location = flow.get('environment', {}).get('location', '未知')
        quest_progress = flow.get('quest_progress', '未知')
        print(f"\n📋 当前：第{chapter_num}章 | 位置：{location} | 进度：{quest_progress}")
    
    def _show_save_detail(self, save_name: str):
        """
        显示存档详情，包括主角和已出场人物的状态以及游戏之前发生过的剧情
        :param save_name: 存档名称
        """
        try:
            # 生成存档文件名
            save_filename = f"{save_name}.json"
            save_path = os.path.join(self.save_dir, save_filename)
            
            # 检查文件是否存在
            if not os.path.exists(save_path):
                print(f"❌ 存档文件不存在：{save_path}")
                return
            
            # 读取存档数据
            with open(save_path, 'r', encoding='utf-8') as f:
                save_data = json.load(f)
            
            # 提取存档数据
            global_state = save_data.get("global_state", {})
            protagonist_attr = save_data.get("protagonist_attr", {})
            difficulty = save_data.get("difficulty", "")
            last_options = save_data.get("last_options", [])
            timestamp = save_data.get("timestamp", "")
            
            if not global_state:
                print("❌ 存档数据不完整")
                return
            
            core = global_state.get('core_worldview', {})
            flow = global_state.get('flow_worldline', {})
            
            # 获取当前章节信息
            current_chapter_id = flow.get('current_chapter', 'chapter1')
            chapters = core.get('chapters', {})
            current_chapter = chapters.get(current_chapter_id, {})
            
            # 获取章节编号（用于显示）
            chapter_num = 1
            if current_chapter_id.startswith('chapter'):
                try:
                    chapter_num = int(current_chapter_id[7:])
                except (ValueError, IndexError):
                    chapter_num = 1
            
            # 显示存档基本信息
            print(f"\n📋 存档详情：{save_name}")
            print(f"🔖 存档时间：{timestamp}")
            print(f"🎮 游戏难度：{difficulty}")
            
            # 显示主角属性
            print(f"\n🎭 主角属性：")
            for attr_name, attr_value in protagonist_attr.items():
                print(f"   {attr_name}：{attr_value}")
            
            # 显示角色状态
            print(f"\n👥 角色状态：")
            # 获取核心角色列表
            core_characters = core.get('characters', {})
            # 获取当前世界线中的角色状态
            flow_characters = flow.get('characters', {})
            
            # 合并核心角色和当前世界线角色
            all_characters = {**core_characters}
            for char_name, char_info in flow_characters.items():
                if char_name in all_characters:
                    all_characters[char_name].update(char_info)
                else:
                    all_characters[char_name] = char_info
            
            # 显示每个角色的状态
            for char_name, char_info in all_characters.items():
                print(f"\n   🧑 {char_name}：")
                # 显示核心信息
                if 'core_personality' in char_info:
                    print(f"      核心性格：{char_info['core_personality']}")
                if 'shallow_background' in char_info:
                    print(f"      浅层背景：{char_info['shallow_background'][:30]}...")
                # 显示当前状态
                if 'thought' in char_info:
                    print(f"      当前想法：{char_info['thought']}")
                if 'physiology' in char_info:
                    print(f"      身体状态：{char_info['physiology']}")
                if 'deep_background_unlocked' in char_info:
                    status = "已解锁" if char_info['deep_background_unlocked'] else "未解锁"
                    print(f"      深层背景：{status}")
            
            # 显示游戏剧情进展
            print(f"\n📜 游戏剧情进展：")
            print(f"   当前章节：第{chapter_num}章")
            print(f"   核心矛盾：{current_chapter.get('main_conflict', '未知')}")
            print(f"   主线进度：{flow.get('quest_progress', '未知')}")
            print(f"   矛盾状态：{'已解决' if flow.get('chapter_conflict_solved', False) else '未解决'}")
            
            # 显示环境状态
            environment = flow.get('environment', {})
            print(f"\n🌍 环境状态：")
            print(f"   位置：{environment.get('location', '未知')}")
            print(f"   天气：{environment.get('weather', '未知')}")
            if 'force_relationship' in environment:
                print(f"   势力关系：{environment['force_relationship'][:30]}...")
            
            # 显示当前可选操作（如果有）
            if last_options:
                print(f"\n🔍 当前可选操作：")
                for idx, opt in enumerate(last_options, 1):
                    print(f"   {idx}. {opt}")
            
            safe_input("\n请按回车键返回存档管理...", default="")
            
        except Exception as e:
            print(f"❌ 查看存档详情失败：{str(e)}")
            safe_input("\n请按回车键返回存档管理...", default="")
    
    def _async_pregenerate(self, scene_id: str, options: List[str]):
        """异步预生成指定场景下所有选项的剧情"""
        print(f"🔄 启动异步预生成线程，场景ID：{scene_id}")
        self.generation_cancelled = False
        
        # 生成所有选项的剧情
        all_options_data = generate_all_options(self.global_state, options, skip_images=self.skip_images)
        
        # 如果生成未被取消，将结果缓存
        if not self.generation_cancelled:
            print(f"✅ 异步预生成完成，场景ID：{scene_id}")
            self.scene_cache[scene_id] = all_options_data
        else:
            print(f"⏹️ 异步预生成已取消，场景ID：{scene_id}")
    
    def start_pregeneration(self, options: List[str]):
        """启动预生成线程"""
        # 取消当前正在进行的生成任务
        self.generation_cancelled = True
        
        # 生成新的场景ID
        next_scene_id = f"scene_{len(self.scene_cache) + 1}"
        
        # 启动新的预生成线程
        self.generating_task = threading.Thread(
            target=self._async_pregenerate,
            args=(next_scene_id, options),
            daemon=True
        )
        self.generating_task.start()
        
        return next_scene_id
    
    def cancel_pregeneration(self):
        """取消当前正在进行的预生成任务"""
        self.generation_cancelled = True
        if self.generating_task and self.generating_task.is_alive():
            self.generating_task.join(timeout=1.0)  # 等待最多1秒
        print("⏹️ 已取消正在进行的预生成任务")
    
    def _trigger_ending(self):
        print("\n🏁 === 游戏结束 ===")
        if self.ending_triggered:
            print("你选择结束游戏，感谢游玩！")
        else:
            flow = self.global_state.get('flow_worldline', {})
            quest_progress = flow.get('quest_progress', '未知')
            print(f"你已完成所有章节，主线任务进度：{quest_progress}")
        self.is_running = False

# ------------------------------
# 启动游戏
# ------------------------------
if __name__ == "__main__":
    game = TextAdventureGame()
    game.start()