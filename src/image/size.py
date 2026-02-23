# -*- coding: utf-8 -*-
"""图片尺寸计算。"""

# 16:9 剧情图：针对 16 寸笔记本屏幕优化，保证在 1920x1080 等常见分辨率下显示清晰
STORY_IMAGE_ASPECT = (16, 9)
STORY_IMAGE_WIDTH_16INCH = 1920  # Full HD 宽，适配 16 寸屏
STORY_IMAGE_HEIGHT_16INCH = 1080  # Full HD 高


def get_story_image_size(provider: str = "yunwu") -> tuple:
    """
    剧情图固定 16:9 比例，在 16 寸屏幕上显示清晰。
    按 provider 的 API 限制做适配。
    """
    if provider == "openai":
        # DALL-E 3 支持 1792x1024 横向，用 1792x1008 近似 16:9
        return (1792, 1008)
    elif provider == "stable_diffusion":
        # SD 常用 8 的倍数，1920/8=240, 1080/8=135
        w, h = STORY_IMAGE_WIDTH_16INCH, STORY_IMAGE_HEIGHT_16INCH
        h = (h // 8) * 8
        w = (w // 8) * 8
        return (w, h)
    else:
        # yunwu/gemini 等：1920x1080
        return (STORY_IMAGE_WIDTH_16INCH, STORY_IMAGE_HEIGHT_16INCH)


def calculate_image_size_for_viewport(viewport_width: int, viewport_height: int, provider: str = "yunwu") -> tuple:
    """
    根据视口尺寸计算合适的图片生成尺寸（保持宽高比，同时考虑API限制）
    :param viewport_width: 视口宽度
    :param viewport_height: 视口高度
    :param provider: 图片生成服务提供商
    :return: (width, height) 元组
    """
    if not viewport_width or not viewport_height or viewport_width <= 0 or viewport_height <= 0:
        return (1024, 1024)

    viewport_aspect = viewport_width / viewport_height
    base_size = 1024

    if provider == "openai":
        if viewport_aspect > 1.5:
            return (1792, 1024)
        elif viewport_aspect < 0.7:
            return (1024, 1792)
        else:
            return (1024, 1024)
    elif provider == "stable_diffusion":
        if viewport_aspect > 1:
            width = base_size
            height = int(base_size / viewport_aspect)
            height = (height // 8) * 8
            if height < 512:
                height = 512
            return (width, height)
        else:
            height = base_size
            width = int(base_size * viewport_aspect)
            width = (width // 8) * 8
            if width < 512:
                width = 512
            return (width, height)
    else:
        if viewport_aspect > 1:
            width = base_size
            height = int(base_size / viewport_aspect)
            height = (height // 8) * 8
            if height < 512:
                height = 512
            return (width, height)
        else:
            height = base_size
            width = int(base_size * viewport_aspect)
            width = (width // 8) * 8
            if width < 512:
                width = 512
            return (width, height)
