# -*- coding: utf-8 -*-
"""通用输入防护与编码设置。"""
import os
import sys

if sys.platform == 'win32':
    os.environ['PYTHONIOENCODING'] = 'utf-8'


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
