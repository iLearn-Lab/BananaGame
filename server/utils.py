# -*- coding: utf-8 -*-
"""通用工具：错误消息清理、场景 ID 生成。"""
import re


def clean_error_message(error_msg):
    """清理错误消息，移除可能导致编码问题的字符。"""
    try:
        msg = str(error_msg)
        msg = re.sub(r'[^\x00-\x7F\u4e00-\u9fff\s\.,;:!?()\[\]{}\-+=]', '', msg)
        return msg
    except Exception:
        return "发生错误，请稍后重试"


def generate_scene_id(global_state_hash, current_options_hash):
    """根据全局状态和当前选项生成唯一的场景ID。"""
    return f"{hash(str(global_state_hash))}_{hash(str(current_options_hash))}"
