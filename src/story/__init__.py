# -*- coding: utf-8 -*-
# 剧情生成：选项生成、结局预测、场景与批量图片
from src.story.ending import (
    get_video_task_status,
    modify_ending_tone,
    modify_ending_content,
    generate_ending_prediction,
)
from src.story.options import (
    prune_options,
    _generate_single_option,
    _generate_single_option_text_only,
    generate_all_options,
)

__all__ = [
    "get_video_task_status",
    "modify_ending_tone",
    "modify_ending_content",
    "generate_ending_prediction",
    "prune_options",
    "_generate_single_option",
    "_generate_single_option_text_only",
    "generate_all_options",
]
