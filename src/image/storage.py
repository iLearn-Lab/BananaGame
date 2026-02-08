# -*- coding: utf-8 -*-
"""base64 图片保存。"""
import os
import re
import base64
import hashlib
from pathlib import Path
from typing import Optional


def save_base64_image(data_uri: str, prompt: str, cache_key_suffix: str = None) -> Optional[str]:
    """
    将base64 data URI保存为图片文件
    :param data_uri: base64 data URI，格式如 data:image/png;base64,iVBORw0KGgo...
    :param prompt: 提示词，用于生成文件名
    :param cache_key_suffix: 可选，参与缓存 key（如 reference 路径），避免不同游戏复用同一缓存
    :return: 保存的文件路径（相对路径），失败返回None
    """
    try:
        data_uri = (data_uri or "").strip()
        if (data_uri.startswith('"') and data_uri.endswith('"')) or (data_uri.startswith("'") and data_uri.endswith("'")):
            data_uri = data_uri[1:-1].strip()

        if not data_uri.startswith("data:image"):
            return None

        header, encoded = data_uri.split(',', 1)
        mime_match = re.search(r'data:image/([^;]+)', header)
        if not mime_match:
            return None

        image_format = mime_match.group(1)
        if image_format == 'jpeg':
            image_format = 'jpg'

        encoded = re.sub(r'\s+', '', encoded)

        try:
            image_data = base64.b64decode(encoded)
        except Exception as e:
            print(f"❌ base64解码失败：{str(e)}")
            return None

        def _is_tiny_png_placeholder(data: bytes) -> bool:
            try:
                if not data or len(data) < 33:
                    return True
                if not data.startswith(b"\x89PNG\r\n\x1a\n"):
                    return False
                ihdr_pos = 8
                if data[ihdr_pos + 4:ihdr_pos + 8] != b'IHDR':
                    return False
                width = int.from_bytes(data[ihdr_pos + 8:ihdr_pos + 12], "big", signed=False)
                height = int.from_bytes(data[ihdr_pos + 12:ihdr_pos + 16], "big", signed=False)
                if width <= 2 and height <= 2 and len(data) < 2048:
                    return True
                return False
            except Exception:
                return False

        if _is_tiny_png_placeholder(image_data):
            print("⚠️ 检测到 1x1/2x2 PNG 占位 base64，已丢弃该图片数据")
            return None

        IMAGE_CACHE_DIR = "image_cache"
        os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)

        key_str = f"{prompt}_{data_uri[:100]}"
        if cache_key_suffix:
            key_str += f"_{cache_key_suffix}"
        prompt_hash = hashlib.md5(key_str.encode()).hexdigest()
        cache_path = Path(IMAGE_CACHE_DIR) / f"{prompt_hash}.{image_format}"

        if cache_path.exists():
            print(f"✅ 使用已存在的base64图片缓存：{cache_path}")
            return f"/image_cache/{prompt_hash}.{image_format}"

        with open(cache_path, 'wb') as f:
            f.write(image_data)

        print(f"✅ base64图片已保存到：{cache_path}")
        return f"/image_cache/{prompt_hash}.{image_format}"

    except Exception as e:
        print(f"❌ 保存base64图片失败：{str(e)}")
        import traceback
        traceback.print_exc()
        return None
