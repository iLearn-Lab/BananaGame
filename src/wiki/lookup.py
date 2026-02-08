# -*- coding: utf-8 -*-
"""现实题材/IP 资料检索（Wikipedia 中/英 + 二次关键词）。"""
import re
import requests
from functools import lru_cache
from urllib.parse import quote
from typing import Dict, List

from src.config import (
    WIKI_LOOKUP_ENABLED,
    WIKI_LANGS,
    WIKI_TIMEOUT_SECONDS,
    WIKI_MAX_SNIPPET_CHARS,
)
from src.utils.text_utils import _safe_str, _clip_text


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
    """尝试通过 Wikipedia langlinks 获取跨语言标题（例如 zh -> en）。失败返回空串。"""
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
    if re.search(r"\b她\b|女性|女演员|女歌手|女作家|女政治家|女运动员|公主|王后|皇后", t):
        return "女性"
    if re.search(r"\b他\b|男性|男演员|男歌手|男作家|男政治家|男运动员|王子|国王|皇帝", t):
        return "男性"
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
    """粗判断：Wikipedia 摘要是否像“现实存在的作品/IP/人物/故事”。"""
    t = _safe_str(text).strip()
    if not t:
        return False
    if re.search(r"演员|歌手|作家|导演|编剧|政治家|运动员|企业家|科学家|哲学家|画家|数学家", t):
        return True
    if re.search(r"\b(actor|singer|writer|director|screenwriter|politician|athlete|entrepreneur|scientist|philosopher|painter|mathematician)\b", t, flags=re.I):
        return True
    if re.search(r"动画|动漫|漫画|轻小说|小说|电影|电视剧|剧集|游戏|系列|作品|角色|人物|主角|主人公", t):
        return True
    if re.search(r"\b(anime|manga|novel|film|movie|television series|tv series|video game|franchise|character|protagonist)\b", t, flags=re.I):
        return True
    if re.search(r"故事|传说|神话|史诗|历史事件|真实事件", t):
        return True
    if re.search(r"\b(story|legend|myth|historical event|true story)\b", t, flags=re.I):
        return True
    return False


@lru_cache(maxsize=256)
def wiki_lookup_theme_and_character(theme: str) -> Dict:
    """
    尝试判断主题是否为现实存在的IP/人物，并检索其资料。
    返回：is_real_world, theme, character, evidence_text 等。
    """
    theme = _safe_str(theme).strip()
    if not theme or not WIKI_LOOKUP_ENABLED:
        return {"is_real_world": False, "theme": {}, "character": {}, "evidence_text": ""}

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

    primary_lang = None
    for lang in (WIKI_LANGS or ["zh", "en"]):
        if lang in theme_hits_by_lang:
            primary_lang = lang
            break
    if not primary_lang:
        primary_lang = next(iter(theme_hits_by_lang.keys()))
    primary_theme_hit = theme_hits_by_lang.get(primary_lang, next(iter(theme_hits_by_lang.values())))

    combined_theme_text_parts = []
    for hit in theme_hits_by_lang.values():
        s = hit.get("summary") or {}
        combined_theme_text_parts.append(_safe_str(s.get("description")))
        combined_theme_text_parts.append(_safe_str(s.get("extract")))
    combined_theme_text = "\n".join([x for x in combined_theme_text_parts if _safe_str(x).strip()]).strip()
    is_real_world = _looks_like_real_ip_or_person(combined_theme_text)

    second_queries = [
        f"{theme} 主人公",
        f"{theme} 主角",
        f"{theme} 人物",
        f"{theme} protagonist",
        f"{theme} main character",
    ]

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
