# -*- coding: utf-8 -*-
"""项目入口：用 python main.py 启动 CLI 游戏。"""
import os
import sys

# 确保项目根在 path 中
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.game.adventure import TextAdventureGame

if __name__ == "__main__":
    game = TextAdventureGame()
    game.start()
