# -*- coding: utf-8 -*-
"""从 .env 读取的配置（API、图片、Wiki 等）。"""
import os
from dotenv import load_dotenv

load_dotenv()

# ------------------------------
# 全局常量定义（替换为yunwu.ai配置）
# ------------------------------
AI_API_CONFIG = {
    "api_key": os.getenv("Camera_Analyst_API_KEY"),
    "base_url": os.getenv("Camera_Analyst_BASE_URL"),
    "model": os.getenv("Camera_Analyst_MODEL")
}

# ------------------------------
# Council 群体智能（多模型讨论）
# 用于世界观完整版、剧情每 2 轮整合
# ------------------------------
_council_env = os.getenv("COUNCIL_MODELS")
if _council_env:
    COUNCIL_MODELS = [m.strip() for m in _council_env.split(",") if m.strip()]
else:
    COUNCIL_MODELS = [
        os.getenv("Camera_Analyst_MODEL", "gpt-4o"),
    ]
CHAIRMAN_MODEL = os.getenv("CHAIRMAN_MODEL") or os.getenv("Camera_Analyst_MODEL", "gpt-4o")

# ------------------------------
# 视觉内容生成API配置
# ------------------------------
IMAGE_GENERATION_CONFIG = {
    "provider": os.getenv("IMAGE_GENERATION_PROVIDER", "yunwu"),
    "yunwu_api_key": os.getenv("Image_Generation_API_KEY", ""),
    "yunwu_base_url": os.getenv("Image_Generation_BASE_URL", "https://yunwu.ai/v1"),
    "yunwu_model": os.getenv("Image_Generation_MODEL", "sora_image"),
    "replicate_api_token": os.getenv("REPLICATE_API_TOKEN", ""),
    "openai_api_key": os.getenv("OPENAI_API_KEY", ""),
    "stable_diffusion_base_url": os.getenv("STABLE_DIFFUSION_BASE_URL", ""),
    "stable_diffusion_api_key": os.getenv("STABLE_DIFFUSION_API_KEY", ""),
    "comfyui_host": os.getenv("COMFYUI_HOST", ""),
    "img2img_api_key": os.getenv("Img2img_API_KEY", ""),
    "img2img_base_url": os.getenv("Img2img_BASE_URL", "https://yunwu.ai/v1"),
    "img2img_path": os.getenv("Img2img_PATH", "/images/edit"),
    "img2img_model": os.getenv("Img2img_MODEL", "stability-ai/stable-diffusion-img2img"),
}

# ------------------------------
# 现实题材/IP 资料检索（Wikipedia）
# ------------------------------
WIKI_LOOKUP_ENABLED = os.getenv("WIKI_LOOKUP_ENABLED", "true").lower() == "true"
WIKI_LANGS = [x.strip() for x in os.getenv("WIKI_LANGS", "zh,en").split(",") if x.strip()]
WIKI_TIMEOUT_SECONDS = float(os.getenv("WIKI_TIMEOUT_SECONDS", "8"))
WIKI_MAX_SNIPPET_CHARS = int(os.getenv("WIKI_MAX_SNIPPET_CHARS", "1200"))
