# -*- coding: utf-8 -*-
"""角色目录与 game_id：生成游戏ID、主角/配角目录。"""
import random
import time
from pathlib import Path


SUPPORTING_ROLE_ARCHIVES_FILE = "role_archives.json"


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


def ensure_character_references_dir(game_id: str) -> Path:
    """确保配角参考图目录存在"""
    ref_dir = Path("initial") / "character_references" / game_id
    ref_dir.mkdir(parents=True, exist_ok=True)
    return ref_dir
