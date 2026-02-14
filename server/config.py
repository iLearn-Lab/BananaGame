# -*- coding: utf-8 -*-
"""Web 服务目录与缓存常量，启动时确保目录存在。"""
import os

# 存档目录配置
SAVE_DIR = "saves"

# 图片和视频缓存目录配置
IMAGE_CACHE_DIR = "image_cache"
VIDEO_CACHE_DIR = "video_cache"

# 最大缓存场景数量，超过此数量将清理最旧的缓存（降低内存占用）
MAX_CACHE_SIZE = 3


def ensure_dirs():
    """确保存档与缓存目录存在。"""
    if not os.path.exists(SAVE_DIR):
        os.makedirs(SAVE_DIR)
    if not os.path.exists(IMAGE_CACHE_DIR):
        os.makedirs(IMAGE_CACHE_DIR)
    if not os.path.exists(VIDEO_CACHE_DIR):
        os.makedirs(VIDEO_CACHE_DIR)
