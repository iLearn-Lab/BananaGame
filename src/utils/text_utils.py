# -*- coding: utf-8 -*-
"""纯文本小工具。"""


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


def _extract_core_features_from_prompt(prompt: str) -> str:
    """从首次出场提示词中提取核心特征（简化版：取关键句或截取）。供角色建档与图片 prompt 使用。"""
    s = _safe_str(prompt).strip()
    if "五官核心特征不可修改" in s:
        s = s.split("五官核心特征不可修改")[0].strip().rstrip("，。")
    return _clip_text(s, 200)
