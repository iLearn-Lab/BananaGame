# -*- coding: utf-8 -*-
"""图片 URL 校验与修复。"""
import re
from urllib.parse import urlparse
from typing import Optional


def validate_image_url(url: str) -> bool:
    """
    验证图片URL是否完整有效
    :param url: 待验证的URL
    :return: True if valid, False otherwise
    """
    if not url or not isinstance(url, str):
        return False

    if not url.startswith(('http://', 'https://')):
        return False

    try:
        parsed = urlparse(url)
        if not parsed.netloc or '.' not in parsed.netloc:
            return False
        if not parsed.path or len(parsed.path) < 1:
            return False
        return True
    except Exception:
        return False


def fix_incomplete_url(url: str) -> Optional[str]:
    """
    尝试修复不完整的URL
    :param url: 可能不完整的URL
    :return: 修复后的URL，如果无法修复则返回None
    """
    if not url:
        return None

    if url.endswith(('-', '_', '.')):
        url = url.rstrip('-_')

    # 对于 OSS URL，若缺少扩展名则尝试添加
    if 'aliyuncs.com' in url or 'oss-' in url:
        if not url.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif')):
            if '?' in url:
                base_url, query = url.split('?', 1)
                if not base_url.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif')):
                    return f"{base_url}.png?{query}" if validate_image_url(f"{base_url}.png?{query}") else url
            else:
                return f"{url}.png" if validate_image_url(f"{url}.png") else url

    return url if validate_image_url(url) else None
