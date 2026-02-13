# -*- coding: utf-8 -*-
"""图片缓存：按 prompt_hash 读写本地文件，带缓存的场景图生成。"""
import hashlib
from pathlib import Path
from typing import Dict

import requests

from main2 import generate_scene_image
from server.config import IMAGE_CACHE_DIR


def get_cached_image(prompt_hash: str) -> str:
    """从缓存获取图片路径。"""
    cache_path = Path(IMAGE_CACHE_DIR) / f"{prompt_hash}.png"
    if cache_path.exists():
        return str(cache_path)
    return None


def cache_image(prompt_hash: str, image_url: str) -> str:
    """缓存图片到本地。"""
    try:
        if image_url.startswith('/image_cache/') or image_url.startswith('image_cache/'):
            cache_path = Path(IMAGE_CACHE_DIR) / f"{prompt_hash}.png"
            if cache_path.exists():
                print(f"✅ 图片已在本地缓存：{cache_path}")
                return str(cache_path)
            import re
            hash_match = re.search(r'([a-f0-9]{32})\.png', image_url)
            if hash_match:
                existing_hash = hash_match.group(1)
                existing_path = Path(IMAGE_CACHE_DIR) / f"{existing_hash}.png"
                if existing_path.exists():
                    import shutil
                    shutil.copy2(existing_path, cache_path)
                    print(f"✅ 从现有缓存复制图片：{cache_path}")
                    return str(cache_path)
            raise ValueError(f"本地缓存文件不存在：{image_url}")

        if not (image_url.startswith('http://') or image_url.startswith('https://')):
            raise ValueError(f"无效的图片URL格式：{image_url}（需要完整的HTTP/HTTPS URL或本地缓存路径）")

        response = requests.get(image_url, timeout=30)
        response.raise_for_status()

        cache_path = Path(IMAGE_CACHE_DIR) / f"{prompt_hash}.png"
        with open(cache_path, 'wb') as f:
            f.write(response.content)

        print(f"✅ 图片已缓存：{cache_path}")
        return str(cache_path)
    except Exception as e:
        print(f"❌ 图片缓存失败：{str(e)}")
        raise


def generate_image_with_cache(scene_description: str, style: str, global_state: Dict) -> Dict:
    """带缓存的图片生成。"""
    prompt_hash = hashlib.md5(f"{scene_description}_{style}".encode()).hexdigest()

    cached_path = get_cached_image(prompt_hash)
    if cached_path:
        print(f"✅ 使用缓存的图片：{prompt_hash}")
        return {
            "url": f"/image_cache/{prompt_hash}.png",
            "prompt": scene_description,
            "style": style,
            "width": 1024,
            "height": 1024,
            "cached": True
        }

    image_data = generate_scene_image(scene_description, global_state, style)
    if not image_data or not image_data.get('url'):
        return None

    image_url = image_data['url']

    if image_url.startswith('/image_cache/') or image_url.startswith('image_cache/'):
        print(f"✅ 图片已在main2.py中缓存，使用现有路径：{image_url}")
        return {
            "url": image_url,
            "prompt": scene_description,
            "style": style,
            "width": 1024,
            "height": 1024,
            "cached": True
        }

    try:
        cache_image(prompt_hash, image_url)
        return {
            "url": f"/image_cache/{prompt_hash}.png",
            "prompt": scene_description,
            "style": style,
            "width": 1024,
            "height": 1024,
            "cached": False
        }
    except Exception as e:
        print(f"⚠️ 图片缓存失败，使用原始URL：{str(e)}")
        return image_data
