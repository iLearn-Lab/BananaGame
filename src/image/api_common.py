# -*- coding: utf-8 -*-
"""图片 API 通用函数（参考图处理、DALL-E 尺寸、Replicate 常量）。"""
import os
import base64
import requests
from typing import Optional

from src.config import IMAGE_GENERATION_CONFIG

# Replicate 官方 stability-ai/stable-diffusion-img2img 最新版 version hash
REPLICATE_IMG2IMG_VERSION = "15a3689ee13b0d2616e98820eca31d4c3abcd36672df6afce5cb6feb1d66087d"


def _ref_image_to_input(ref: str, max_data_uri_bytes: int = 600000) -> str:
    """
    将参考图（本地路径 / HTTP URL / data URI）转为 Replicate 可接受的 input：
    data URI 或 HTTP URL。
    """
    if not ref or not isinstance(ref, str):
        return ""
    ref = ref.strip()
    if not ref:
        return ""
    if ref.startswith("data:image"):
        return ref
    if ref.startswith(("http://", "https://")):
        return ref
    if os.path.exists(ref):
        try:
            with open(ref, "rb") as f:
                raw = f.read()
            if len(raw) * 4 // 3 <= max_data_uri_bytes:
                b64 = base64.b64encode(raw).decode("utf-8")
                return f"data:image/png;base64,{b64}"
            try:
                from PIL import Image
                import io
                im = Image.open(io.BytesIO(raw)).convert("RGB")
                w, h = im.size
                max_side = 640
                if max(w, h) > max_side:
                    if w >= h:
                        im = im.resize((max_side, int(h * max_side / w)), Image.Resampling.LANCZOS)
                    else:
                        im = im.resize((int(w * max_side / h), max_side), Image.Resampling.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=88, optimize=True)
                buf.seek(0)
                raw = buf.read()
                b64 = base64.b64encode(raw).decode("utf-8")
                return f"data:image/jpeg;base64,{b64}"
            except Exception:
                b64 = base64.b64encode(raw).decode("utf-8")
                return f"data:image/png;base64,{b64}"
        except Exception:
            return ""
    return ""


def call_dalle_api_with_size(prompt: str, size: str) -> str:
    """调用DALL-E API生成指定尺寸的图片"""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=IMAGE_GENERATION_CONFIG.get("openai_api_key"))

        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt[:1000],
            size=size,
            quality="standard",
            n=1,
        )

        return response.data[0].url
    except Exception as e:
        print(f"❌ DALL-E API调用失败：{str(e)}")
        raise
