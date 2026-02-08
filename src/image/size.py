# -*- coding: utf-8 -*-
"""图片尺寸计算。"""


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
