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


def get_protagonist_names(game_state: dict) -> set:
    """
    从游戏状态中提取主角姓名与别名集合，用于建档时排除主角称呼。
    :param game_state: global_state / gameData（含 protagonist_canonical、core_worldview）
    :return: 主角姓名集合，如 {"沈屿白", "Shinji"}
    """
    names = set()
    if not game_state or not isinstance(game_state, dict):
        return names
    canonical = game_state.get("protagonist_canonical") or {}
    if isinstance(canonical, dict):
        for k in ("name_zh", "name_en"):
            v = _safe_str(canonical.get(k, "")).strip()
            if v and len(v) >= 1:
                names.add(v)
    core = game_state.get("core_worldview") or {}
    if isinstance(core, dict) and "characters" in core:
        proto = (core.get("characters") or {}).get("主角")
        if isinstance(proto, dict) and proto.get("name"):
            n = _safe_str(proto.get("name", "")).strip()
            if n:
                names.add(n)
    return names
