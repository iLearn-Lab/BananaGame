# -*- coding: utf-8 -*-
"""世界观文本解析：正则回填缺失字段。"""
import re
from typing import Dict

_LA = r"(?:世界观基础设定|主角核心能力|游戏主线任务|游戏结束触发条件|总章节数|预计主线步数|第\d+章|##\s*【|$)"
_REGEX_GAME_STYLE = re.compile(r"游戏风格[：:]\s*(.+?)(?=\n\s*" + _LA + r")", re.UNICODE | re.DOTALL | re.MULTILINE)
_REGEX_WORLD_BASIC = re.compile(r"世界观基础设定[：:]\s*(.+?)(?=\n\s*" + _LA + r")", re.UNICODE | re.DOTALL | re.MULTILINE)
_REGEX_PROTAGONIST_ABILITY = re.compile(r"主角核心能力[：:]\s*(.+?)(?=\n\s*" + _LA + r")", re.UNICODE | re.DOTALL | re.MULTILINE)
_REGEX_MAIN_QUEST = re.compile(r"游戏主线任务[：:]\s*(.+?)(?=\n\s*" + _LA + r")", re.UNICODE | re.DOTALL | re.MULTILINE)
_REGEX_END_TRIGGER = re.compile(r"游戏结束触发条件[：:]\s*(.+?)(?=\n\s*" + _LA + r")", re.UNICODE | re.DOTALL | re.MULTILINE)
_REGEX_CHAPTER = re.compile(r"第(\d+)章[：:]?", re.UNICODE)
_REGEX_CHAPTER_CONFLICT = re.compile(r"(?:- )?核心矛盾[：:]\s*(.+)", re.UNICODE | re.MULTILINE | re.DOTALL)
_REGEX_CHAPTER_END = re.compile(r"(?:- )?矛盾结束条件[：:]\s*(.+)", re.UNICODE | re.MULTILINE | re.DOTALL)


def _regex_fill_worldview(raw_text: str, core_worldview: Dict, chapters: Dict):
    """使用正则回填缺失的核心字段，避免因格式偏差导致解析失败"""
    if not core_worldview.get("game_style"):
        m = _REGEX_GAME_STYLE.search(raw_text)
        if m:
            content = m.group(1).strip()
            content = content.replace('**', '').replace('*', '').strip()
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

    if chapters is None:
        chapters = {}
    chapter_matches = list(_REGEX_CHAPTER.finditer(raw_text))
    if not chapter_matches:
        return
    for idx, match in enumerate(chapter_matches):
        chap_num = match.group(1)
        chap_key = f"chapter{chap_num}"
        start = match.end()
        end = chapter_matches[idx + 1].start() if idx + 1 < len(chapter_matches) else None
        segment = raw_text[start:end]
        conflict_match = _REGEX_CHAPTER_CONFLICT.search(segment or "")
        end_cond_match = _REGEX_CHAPTER_END.search(segment or "")
        chap = chapters.setdefault(chap_key, {})
        if conflict_match and not chap.get("main_conflict"):
            conflict_text = conflict_match.group(1).strip()
            conflict_text = ' '.join(conflict_text.split())
            chap["main_conflict"] = conflict_text
        if end_cond_match and not chap.get("conflict_end_condition"):
            end_cond_text = end_cond_match.group(1).strip()
            end_cond_text = ' '.join(end_cond_text.split())
            chap["conflict_end_condition"] = end_cond_text
