# -*- coding: utf-8 -*-
"""预配角：只被提及未出场的角色，持续积累碎片化特征；正式出场时合并进配角档案。"""
import json
import re
from pathlib import Path
from typing import Dict, List, Optional

from src.characters.paths import PENDING_ROLES_FILE, ensure_character_references_dir
from src.utils.text_utils import _safe_str, _clip_text


def _load_pending_roles(game_id: str) -> Dict:
    """加载预配角数据。key=角色名, value={"aliases": [], "fragments": []}"""
    ref_dir = ensure_character_references_dir(game_id)
    path = ref_dir / PENDING_ROLES_FILE
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ 加载预配角失败：{e}")
        return {}


def _save_pending_roles(game_id: str, data: Dict) -> None:
    ref_dir = ensure_character_references_dir(game_id)
    path = ref_dir / PENDING_ROLES_FILE
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 保存预配角失败：{e}")


def _extract_fragments_for_name(scene_text: str, name: str, max_len: int = 200) -> List[str]:
    """从场景文本中摘取包含该名字的句子或片段，作为碎片化特征。"""
    text = _safe_str(scene_text).strip()
    name = _safe_str(name).strip()
    if not name or name not in text:
        return []
    fragments = []
    sentences = re.split(r'[。！？\n]', text)
    for s in sentences:
        s = s.strip()
        if name in s and len(s) > 2:
            fragments.append(_clip_text(s, max_len))
    if not fragments:
        idx = text.find(name)
        if idx >= 0:
            start = max(0, idx - 60)
            end = min(len(text), idx + 120)
            fragments.append(_clip_text(text[start:end], max_len))
    return fragments


def add_mentioned_roles(game_id: str, role_names: List[str], scene_text: str) -> None:
    """
    本段剧情中只被提及、未出场的角色：追加本段碎片到预配角存储。
    :param game_id: 游戏ID
    :param role_names: 本段提及但未出场的角色名列表
    :param scene_text: 本段场景文本，用于抽取与各角色相关的句子
    """
    if not game_id or not role_names or not scene_text or not scene_text.strip():
        return
    data = _load_pending_roles(game_id)
    for name in role_names:
        name = _safe_str(name).strip()
        if not name:
            continue
        fragments = _extract_fragments_for_name(scene_text, name)
        if not fragments:
            continue
        if name not in data:
            data[name] = {"aliases": [name], "fragments": []}
        else:
            data[name] = dict(data[name])
            if "fragments" not in data[name]:
                data[name]["fragments"] = []
            if "aliases" not in data[name]:
                data[name]["aliases"] = [name]
        data[name]["fragments"] = (data[name].get("fragments") or []) + fragments
    if data:
        _save_pending_roles(game_id, data)
        print(f"📋 预配角碎片已更新（本段提及未出场）：{list(data.keys())}")


def get_and_consume_pending(game_id: str, display_name: str) -> Optional[Dict]:
    """
    正式出场时：取出该角色在预配角中积累的碎片并从预配角中移除，合并进正式档案。
    :return: {"fragments": [...], "aliases": [...]} 或 None
    """
    if not game_id or not display_name:
        return None
    data = _load_pending_roles(game_id)
    dn = _safe_str(display_name).strip()
    if dn in data:
        out = dict(data[dn])
        del data[dn]
        _save_pending_roles(game_id, data)
        return out
    for key, val in list(data.items()):
        aliases = val.get("aliases") or []
        if dn == key or (isinstance(aliases, list) and dn in aliases):
            out = dict(val)
            del data[key]
            _save_pending_roles(game_id, data)
            return out
    return None
