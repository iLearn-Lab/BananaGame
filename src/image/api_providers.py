# -*- coding: utf-8 -*-
"""图片 API 各供应商实现及主角/场景图生成。"""
import os
import re
import json
import time
import random
import hashlib
import requests
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

from src.config import IMAGE_GENERATION_CONFIG
from src.constants import _YUNWU_RATE_LOCK, _YUNWU_LAST_CALL_TS
from src.utils.text_utils import _safe_str, _clip_text, get_protagonist_names
from src.image.size import calculate_image_size_for_viewport, get_story_image_size
from src.image.api_common import (
    _ref_image_to_input,
    call_dalle_api_with_size,
    REPLICATE_IMG2IMG_VERSION,
)
from src.image.validation import validate_image_url, fix_incomplete_url
from src.image.storage import save_base64_image
from src.image.prompt_optimize import (
    optimize_image_prompt_with_llm,
    optimize_main_character_prompt_with_llm,
)
from src.characters.paths import generate_game_id, ensure_main_character_dir
from src.characters.archives import _load_role_archives
from src.characters.supporting import (
    extract_supporting_characters_with_names,
    get_or_create_supporting_role_archive,
    update_supporting_role_aliases_from_plot,
)

# 主角三视图 prompt 模板
prompt_template_front = """
Generate a full-body, front-view portrait of character {identifier} based on the following description, with a pure white background. The character should be centered in the image, occupying most of the frame. Gazing straight ahead. Standing with arms relaxed at sides. Natural expression.
Features: {features}
Style: {style}
No text, no symbols, no watermark, no garbled characters, no words.
""".strip()

prompt_template_side = """
Generate a full-body, side-view portrait of character {identifier} based on the provided front-view portrait, with a pure white background. The character should be centered in the image, occupying most of the frame. Facing left. Standing with arms relaxed at sides.
No text, no symbols, no watermark, no garbled characters, no words.
""".strip()

prompt_template_back = """
Generate a full-body, back-view portrait of character {identifier} based on the provided front-view portrait, with a pure white background. The character should be centered in the image, occupying most of the frame. No facial features should be visible.
No text, no symbols, no watermark, no garbled characters, no words.
""".strip()

def call_image_api_with_custom_size(
    prompt: str,
    width: int = 1024,
    height: int = 1536,
    reference_image_url: str = "",
    sd_denoising_strength: float = None
) -> str:
    """
    调用生图API生成指定尺寸的图片
    :param prompt: 图片生成提示词
    :param width: 图片宽度
    :param height: 图片高度
    :param reference_image_url: 参考图URL/路径（可选；仅部分provider支持，优先走Stable Diffusion img2img）
    :param sd_denoising_strength: 当走 Stable Diffusion img2img 时使用的 denoising_strength（可选）
    :return: 图片URL或base64数据
    """
    provider = IMAGE_GENERATION_CONFIG.get("provider", "yunwu")

    # 若提供了参考图：走图生图。优先用云雾 API 中的 stability-ai/stable-diffusion-img2img（传图片+prompt）
    if reference_image_url:
        img2img_base = (IMAGE_GENERATION_CONFIG.get("img2img_base_url") or "").strip()
        img2img_key = (IMAGE_GENERATION_CONFIG.get("img2img_api_key") or "").strip()
        sd_base = IMAGE_GENERATION_CONFIG.get("stable_diffusion_base_url", "")
        # 1) 若配置了云雾图生图（Img2img_BASE_URL + Img2img_API_KEY），用云雾 API 的图生图模型
        if img2img_base and img2img_key:
            print(f"🧷 主角生图使用参考图（云雾 API stability-ai/stable-diffusion-img2img）：{reference_image_url[:120]}...")
            return call_img2img_via_yunwu(
                prompt,
                width,
                height,
                reference_image_url=reference_image_url,
                denoising_strength=sd_denoising_strength
            )
        # 2) 否则若配置了本地 SD，走 SD img2img
        if sd_base or provider == "stable_diffusion":
            print(f"🧷 主角生图使用参考图（本地 SD img2img）：{reference_image_url[:120]}...")
            return call_stable_diffusion_api_with_size(
                prompt,
                width,
                height,
                style="default",
                reference_image_url=reference_image_url,
                denoising_strength=sd_denoising_strength
            )
        print("⚠️ 检测到参考图，但未配置图生图（Img2img_* 或 Stable Diffusion），将忽略参考图。")
    
    if provider == "yunwu":
        # yunwu.ai可能不支持自定义尺寸，先尝试标准调用
        # 在提示词中添加尺寸要求
        size_prompt = f"{prompt}, aspect ratio {width}:{height}, portrait orientation"
        return call_yunwu_image_api(size_prompt, "default")
    elif provider == "replicate":
        return call_replicate_api(prompt, "default")
    elif provider == "openai":
        # DALL-E 3支持1024x1024, 1024x1792, 1792x1024
        # 1024x1536不在支持列表中，使用最接近的1792x1024或1024x1024
        if height > width:
            # 竖版，使用1024x1792（最接近1024x1536）
            size = "1024x1792"
        else:
            size = "1024x1024"
        return call_dalle_api_with_size(prompt, size)
    elif provider == "stable_diffusion":
        return call_stable_diffusion_api_with_size(
            prompt,
            width,
            height,
            style="default",
            reference_image_url=reference_image_url or "",
            denoising_strength=sd_denoising_strength
        )
    elif provider == "comfyui":
        return call_comfyui_api(prompt, "default")
    else:
        print(f"⚠️ 不支持的图片生成服务：{provider}")
        return None




def call_img2img_via_replicate_direct(
    prompt: str,
    width: int,
    height: int,
    reference_image_url: str = "",
    denoising_strength: float = None
) -> str:
    """
    直接调用 Replicate 官方 API，使用 stability-ai/stable-diffusion-img2img 做图生图。
    需在 .env 中设置 REPLICATE_API_TOKEN。绕过云雾代理，避免 400 等问题。
    """
    import time
    token = (IMAGE_GENERATION_CONFIG.get("replicate_api_token") or "").strip()
    if not token:
        raise ValueError("直接 Replicate 图生图未配置：请在 .env 中设置 REPLICATE_API_TOKEN")
    image_input = _ref_image_to_input(reference_image_url)
    if not image_input:
        raise ValueError("无法加载参考图，请检查 reference_image_url 是否为有效路径或 URL")
    ds = 0.5
    if denoising_strength is not None:
        try:
            ds = max(0.0, min(1.0, float(denoising_strength)))
        except Exception:
            pass
    create_url = "https://api.replicate.com/v1/predictions"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "version": REPLICATE_IMG2IMG_VERSION,
        "input": {
            "image": image_input,
            "prompt": prompt,
            "prompt_strength": ds,
        },
    }
    try:
        resp = requests.post(create_url, headers=headers, json=payload, timeout=60)
        if resp.status_code >= 400:
            try:
                err_body = resp.json()
            except Exception:
                err_body = resp.text
            print(f"❌ Replicate 图生图 API 错误 {resp.status_code}，响应: {err_body}")
        resp.raise_for_status()
        data = resp.json()
        pred_id = (data.get("id") or "").strip()
        if not pred_id:
            raise RuntimeError("Replicate 未返回 prediction id")
        get_url = f"https://api.replicate.com/v1/predictions/{pred_id}"
        max_wait = int(os.getenv("IMG2IMG_POLL_MAX_SECONDS", "120"))
        interval = float(os.getenv("IMG2IMG_POLL_INTERVAL_SECONDS", "2"))
        deadline = time.time() + max_wait
        while time.time() < deadline:
            r2 = requests.get(get_url, headers=headers, timeout=30)
            r2.raise_for_status()
            p = r2.json()
            status = (p.get("status") or "").lower()
            if status == "succeeded":
                out = p.get("output")
                if isinstance(out, list) and len(out) > 0:
                    url_or_b64 = out[0]
                elif isinstance(out, str):
                    url_or_b64 = out
                else:
                    raise RuntimeError("图生图返回的 output 格式异常")
                if isinstance(url_or_b64, str) and url_or_b64.startswith(("http://", "https://")):
                    return url_or_b64
                if isinstance(url_or_b64, str) and len(url_or_b64) > 100:
                    if not url_or_b64.startswith("data:"):
                        return f"data:image/png;base64,{url_or_b64}"
                    return url_or_b64
                raise RuntimeError("图生图 output 无法解析为 URL 或 base64")
            if status in ("failed", "canceled"):
                err = p.get("error") or status
                raise RuntimeError(f"图生图任务结束：{err}")
            time.sleep(interval)
        raise RuntimeError(f"图生图轮询超时（{max_wait}s）")
    except requests.exceptions.HTTPError as e:
        print(f"❌ Replicate 图生图 API HTTP 错误：{e.response.status_code if e.response else ''} {str(e)}")
        raise
    except Exception as e:
        print(f"❌ Replicate 图生图 API 调用失败：{str(e)}")
        raise


def call_img2img_via_yunwu(
    prompt: str,
    width: int,
    height: int,
    reference_image_url: str = "",
    denoising_strength: float = None
) -> str:
    """
    通过云雾 API 调用图生图模型（stability-ai/stable-diffusion-img2img）。
    配置与其他服务一致：BASE_URL（https://yunwu.ai/v1）+ PATH + MODEL。
    支持两种格式：
    1. Replicate格式：当PATH包含/replicate/时，使用Replicate API格式（version + input）
    2. 云雾API格式：其他情况使用云雾API格式（model + image + prompt）
    """
    import time
    # 重新加载环境变量以确保获取最新值
    from dotenv import load_dotenv
    load_dotenv(override=True)
    
    base_url_raw = (os.getenv("Img2img_BASE_URL") or IMAGE_GENERATION_CONFIG.get("img2img_base_url") or "https://yunwu.ai/v1").strip()
    # 优先从环境变量直接读取
    path_env = os.getenv("Img2img_PATH", "").strip()
    path_config = IMAGE_GENERATION_CONFIG.get("img2img_path", "").strip()
    path = (path_env or path_config or "/images/edit").strip()
    if not path.startswith("/"):
        path = "/" + path
    
    # 如果使用的是默认路径，发出警告
    if path == "/images/edit" and not path_env and not path_config:
        print(f"⚠️ 警告：使用默认路径 /images/edit，请检查 .env 文件中的 Img2img_PATH 配置")
    
    api_key = (os.getenv("Img2img_API_KEY") or IMAGE_GENERATION_CONFIG.get("img2img_api_key") or "").strip()
    model = (os.getenv("Img2img_MODEL") or IMAGE_GENERATION_CONFIG.get("img2img_model") or "stability-ai/stable-diffusion-img2img").strip()
    if not api_key:
        raise ValueError("图生图未配置：请在 .env 中设置 Img2img_API_KEY")
    
    # 构建URL：正确处理base_url和path的拼接
    # 根据.env配置：
    # - Img2img_BASE_URL=https://yunwu.ai（没有/v1）
    # - Img2img_PATH=/replicate/v1/predictions
    # 正确URL应该是：https://yunwu.ai/replicate/v1/predictions
    # 
    # 如果path以/replicate/开头，说明是通过云雾代理调用Replicate
    # 此时path已经包含完整路径，直接拼接base_url和path，不要添加/v1
    base_url_clean = base_url_raw.rstrip("/")
    
    if path.startswith("/replicate/"):
        # Replicate路径：直接拼接，不添加/v1（因为path已经包含完整路径）
        create_url = base_url_clean + path
    else:
        # 其他路径（如/images/edit）：如果base_url没有/v1，添加/v1
        if not base_url_clean.endswith("/v1"):
            base_url_clean = base_url_clean + "/v1"
        create_url = base_url_clean + path
    
    print(f"🔧 图生图配置：base_url_raw='{base_url_raw}', path_env='{path_env}', path_config='{path_config}', 最终path='{path}', create_url='{create_url}'")
    
    # 对于Replicate格式，确保图片格式正确（优先使用JPEG，因为PNG可能不被支持）
    image_input = _ref_image_to_input(reference_image_url)
    if not image_input:
        raise ValueError("无法加载参考图，请检查 reference_image_url 是否为有效路径或 URL")
    
    # 如果是Replicate格式且图片是PNG，尝试转换为JPEG
    if "/replicate/" in path.lower() and image_input.startswith("data:image/png"):
        try:
            import base64
            from PIL import Image
            import io
            # 提取base64数据
            b64_data = image_input.split("base64,", 1)[1]
            img_bytes = base64.b64decode(b64_data)
            # 转换为JPEG
            im = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=90, optimize=True)
            buf.seek(0)
            jpeg_bytes = buf.read()
            jpeg_b64 = base64.b64encode(jpeg_bytes).decode("utf-8")
            image_input = f"data:image/jpeg;base64,{jpeg_b64}"
            print(f"🔧 已将PNG图片转换为JPEG格式（Replicate兼容性）")
        except Exception as e:
            print(f"⚠️ PNG转JPEG失败，继续使用原格式: {str(e)}")
    ds = 0.5
    if denoising_strength is not None:
        try:
            ds = max(0.0, min(1.0, float(denoising_strength)))
        except Exception:
            pass
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    
    # 判断API格式：如果路径包含/replicate/，使用Replicate格式
    is_replicate_format = "/replicate/" in path.lower()
    
    if is_replicate_format:
        # 通过云雾代理调用Replicate时，使用标准的Replicate API格式
        # 云雾代理只是转发请求，不会改变请求格式，所以应该使用和直接调用Replicate相同的格式
        payload = {
            "version": REPLICATE_IMG2IMG_VERSION,  # 使用version而不是model（标准Replicate格式）
            "input": {
                "image": image_input,
                "prompt": prompt,
                "prompt_strength": ds,  # 使用prompt_strength（标准Replicate格式）而不是strength
                # 注意：stability-ai/stable-diffusion-img2img不支持width/height参数
            }
        }
        print(f"🔧 使用Replicate API格式调用图生图（通过云雾代理），version={REPLICATE_IMG2IMG_VERSION[:20]}..., prompt_strength={ds}")
    else:
        # 云雾 API 格式：model + image + prompt
        payload = {
            "model": model,
            "image": image_input,
            "prompt": prompt,
            "strength": ds,  # 云雾可能用 strength 而不是 prompt_strength
        }
        print(f"🔧 使用云雾API格式调用图生图")
    try:
        # 打印请求详情用于调试
        import json
        print(f"🔍 请求详情：URL={create_url}, payload_keys={list(payload.keys())}")
        if "input" in payload:
            print(f"🔍 input keys: {list(payload['input'].keys())}")
        
        resp = requests.post(create_url, headers=headers, json=payload, timeout=60)
        if resp.status_code >= 400:
            try:
                err_body = resp.json()
            except Exception:
                err_body = resp.text
            print(f"❌ 云雾图生图 API 错误 {resp.status_code}，响应: {err_body}")
            print(f"🔍 请求URL: {create_url}")
            # 打印payload但不包含图片数据（可能很大）
            payload_debug = {k: (v[:100] + "..." if isinstance(v, str) and len(v) > 100 else v) for k, v in payload.items()}
            if "input" in payload_debug and isinstance(payload_debug["input"], dict):
                input_debug = {}
                for k, v in payload_debug["input"].items():
                    if k == "image" and isinstance(v, str):
                        # 显示图片格式和大小信息
                        if v.startswith("data:image"):
                            img_type = v.split(";")[0].split("/")[-1] if "/" in v.split(";")[0] else "unknown"
                            img_size = len(v) if len(v) < 200 else "..." + str(len(v))
                            input_debug[k] = f"data:image/{img_type};base64,... (size: {img_size} chars)"
                        else:
                            input_debug[k] = v[:100] + "..." if len(str(v)) > 100 else v
                    else:
                        input_debug[k] = v[:100] + "..." if isinstance(v, str) and len(v) > 100 else v
                payload_debug["input"] = input_debug
            print(f"🔍 请求payload: {json.dumps(payload_debug, indent=2, ensure_ascii=False)}")
            
            # 如果是400错误，尝试打印更详细的错误信息
            if resp.status_code == 400:
                print(f"🔍 完整错误响应: {resp.text[:1000]}")
                # 尝试从响应中提取更详细的错误信息
                try:
                    error_detail = resp.json()
                    if isinstance(error_detail, dict):
                        error_msg = error_detail.get('message', '')
                        error_data = error_detail.get('data', '')
                        if error_msg:
                            print(f"🔍 错误消息: {error_msg}")
                        if error_data:
                            print(f"🔍 错误数据: {error_data}")
                except:
                    pass
        resp.raise_for_status()
        data = resp.json()
        # 云雾 API 可能直接返回图片 URL，也可能返回异步任务（类似 Replicate）
        # 先尝试直接返回格式
        if "url" in data:
            return data["url"]
        if "image" in data:
            img = data["image"]
            if isinstance(img, str) and img.startswith(("http://", "https://", "data:image")):
                return img
        if "data" in data and isinstance(data["data"], list) and len(data["data"]) > 0:
            img = data["data"][0]
            if isinstance(img, dict) and "url" in img:
                return img["url"]
            if isinstance(img, str) and img.startswith(("http://", "https://", "data:image")):
                return img
        # 如果是异步任务格式（Replicate 兼容），进行轮询
        pred_id = (data.get("id") or "").strip()
        if pred_id:
            get_url = create_url.rstrip("/") + "/" + pred_id
            max_wait = int(os.getenv("IMG2IMG_POLL_MAX_SECONDS", "120"))
            interval = float(os.getenv("IMG2IMG_POLL_INTERVAL_SECONDS", "2"))
            deadline = time.time() + max_wait
            while time.time() < deadline:
                r2 = requests.get(get_url, headers=headers, timeout=30)
                r2.raise_for_status()
                p = r2.json()
                status = (p.get("status") or "").lower()
                if status == "succeeded":
                    out = p.get("output")
                    if isinstance(out, list) and len(out) > 0:
                        url_or_b64 = out[0]
                    elif isinstance(out, str):
                        url_or_b64 = out
                    else:
                        raise RuntimeError("图生图返回的 output 格式异常")
                    if isinstance(url_or_b64, str) and url_or_b64.startswith(("http://", "https://")):
                        return url_or_b64
                    if isinstance(url_or_b64, str) and len(url_or_b64) > 100:
                        if not url_or_b64.startswith("data:"):
                            return f"data:image/png;base64,{url_or_b64}"
                        return url_or_b64
                    raise RuntimeError("图生图 output 无法解析为 URL 或 base64")
                if status in ("failed", "canceled"):
                    err = p.get("error") or status
                    raise RuntimeError(f"图生图任务结束：{err}")
                time.sleep(interval)
            raise RuntimeError(f"图生图轮询超时（{max_wait}s）")
        # 无法解析响应格式
        raise RuntimeError(f"云雾图生图返回格式无法解析：{data}")
    except requests.exceptions.HTTPError as e:
        print(f"❌ 云雾图生图 API HTTP 错误：{e.response.status_code if e.response else ''} {str(e)}")
        raise
    except Exception as e:
        print(f"❌ 云雾图生图 API 调用失败：{str(e)}")
        raise


def call_stable_diffusion_api_with_size(
    prompt: str,
    width: int,
    height: int,
    style: str = "default",
    reference_image_url: str = "",
    denoising_strength: float = None
) -> str:
    """调用本地Stable Diffusion API生成指定尺寸的图片（支持img2img参考图）"""
    try:
        import base64
        from pathlib import Path

        base_url = IMAGE_GENERATION_CONFIG.get("stable_diffusion_base_url", "http://localhost:7860")
        api_key = IMAGE_GENERATION_CONFIG.get("stable_diffusion_api_key", "")

        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        def _load_ref_image_b64(ref: str) -> str:
            """把参考图读成 base64（不带 data:image 前缀），失败返回空串。"""
            if not ref or not isinstance(ref, str):
                return ""
            ref = ref.strip()
            if not ref:
                return ""

            # data URL
            if ref.startswith("data:image"):
                try:
                    b64_part = ref.split("base64,", 1)[1]
                    b64_part = re.sub(r"\s+", "", b64_part)
                    base64.b64decode(b64_part, validate=False)
                    return b64_part
                except Exception:
                    return ""

            # HTTP/HTTPS URL
            if ref.startswith(("http://", "https://")):
                try:
                    resp = requests.get(ref, timeout=30, stream=True)
                    resp.raise_for_status()
                    img_bytes = resp.content
                    return base64.b64encode(img_bytes).decode("utf-8")
                except Exception:
                    return ""

            # 本地路径
            if os.path.exists(ref):
                try:
                    with open(ref, "rb") as f:
                        img_bytes = f.read()
                    return base64.b64encode(img_bytes).decode("utf-8")
                except Exception:
                    return ""

            return ""

        ref_b64 = _load_ref_image_b64(reference_image_url) if reference_image_url else ""
        # 关键诊断：确认参考图是否真的被读入（否则会退回 txt2img，侧/背会“看起来毫无关系”）
        try:
            if reference_image_url:
                exists_flag = os.path.exists(reference_image_url) if isinstance(reference_image_url, str) else False
                print(
                    f"🔎 [SD] reference_image_url provided, exists={exists_flag}, "
                    f"ref_b64_len={len(ref_b64) if ref_b64 else 0}"
                )
        except Exception:
            pass

        # 如果有参考图，使用img2img，否则使用txt2img
        if ref_b64:
            # 允许外部传入 denoising_strength；默认保持历史行为 0.7
            try:
                ds = float(denoising_strength) if denoising_strength is not None else 0.7
            except Exception:
                ds = 0.7
            # 合法范围兜底
            if ds < 0.0:
                ds = 0.0
            if ds > 1.0:
                ds = 1.0
            # img2img模式
            request_payload = {
                "prompt": prompt,
                "width": width,
                "height": height,
                "steps": 20,
                "cfg_scale": 7,
                "init_images": [ref_b64],
                "denoising_strength": ds  # 控制参考图的影响程度
            }
            api_endpoint = f"{base_url}/sdapi/v1/img2img"
        else:
            # txt2img模式
            request_payload = {
                "prompt": prompt,
                "width": width,
                "height": height,
                "steps": 20,
                "cfg_scale": 7
            }
            api_endpoint = f"{base_url}/sdapi/v1/txt2img"

        response = requests.post(
            api_endpoint,
            headers=headers,
            json=request_payload,
            timeout=120
        )
        response.raise_for_status()
        
        result = response.json()
        if "images" in result and len(result["images"]) > 0:
            # 返回base64数据
            return result["images"][0]
        return None
    except Exception as e:
        print(f"❌ Stable Diffusion API调用失败：{str(e)}")
        raise

def generate_main_character_image(
    protagonist_attr: Dict,
    global_state: Dict,
    image_style: Dict = None,
    game_id: str = None
) -> Dict:
    """
    生成主角形象图片
    :param protagonist_attr: 主角属性
    :param global_state: 全局状态
    :param image_style: 图片风格
    :param game_id: 游戏ID（如果为None，会自动生成）
    :return: 包含图片路径和元数据的字典，如果失败返回None
    """
    try:
        import threading

        # 侧/背生成已改用 gemini-2.5-flash-image 图生图，不再使用 denoising_strength

        # metadata 并发写保护（侧/背线程会更新 metadata.json）
        _metadata_lock = threading.Lock()

        def _style_label(style_obj: Dict) -> str:
            if not isinstance(style_obj, dict):
                return "default"
            t = _safe_str(style_obj.get("type")).strip()
            if t:
                if t == "custom":
                    v = _safe_str(style_obj.get("value")).strip()
                    return v or "custom"
                return t
            return "default"

        def _pick_identifier(req_tokens: list) -> str:
            try:
                if isinstance(req_tokens, list) and req_tokens:
                    cand = _safe_str(req_tokens[0]).strip()
                    if cand:
                        return cand
            except Exception:
                pass
            return "protagonist"

        def _save_image_any(image_url_or_data_obj, out_path: Path) -> bool:
            """复用现有保存逻辑，但可写到任意文件名。"""
            try:
                image_url_str_local = str(image_url_or_data_obj or "")
                if not image_url_str_local:
                    return False

                out_path.parent.mkdir(parents=True, exist_ok=True)

                if image_url_str_local.startswith("data:image"):
                    import base64
                    base64_data = image_url_str_local.split(",", 1)[1] if "," in image_url_str_local else image_url_str_local
                    image_data = base64.b64decode(base64_data)
                    with open(out_path, "wb") as f:
                        f.write(image_data)
                    return out_path.exists()

                if image_url_str_local.startswith(("http://", "https://")):
                    resp = requests.get(image_url_str_local, timeout=60, stream=True)
                    resp.raise_for_status()
                    with open(out_path, "wb") as f:
                        for chunk in resp.iter_content(chunk_size=8192):
                            f.write(chunk)
                    return out_path.exists()

                if image_url_str_local.startswith("/image_cache/") or image_url_str_local.startswith("image_cache/"):
                    import shutil
                    if image_url_str_local.startswith("image_cache/"):
                        source_path = Path("image_cache") / image_url_str_local.replace("image_cache/", "")
                    else:
                        source_path = Path("image_cache") / image_url_str_local.replace("/image_cache/", "")
                    if source_path.exists():
                        shutil.copy2(source_path, out_path)
                        return out_path.exists()
                    return False

                # 可能是纯 base64（无 data:image 前缀）
                if isinstance(image_url_or_data_obj, str) and len(image_url_str_local) > 100:
                    try:
                        import base64
                        image_data = base64.b64decode(image_url_str_local)
                        with open(out_path, "wb") as f:
                            f.write(image_data)
                        return out_path.exists()
                    except Exception:
                        return False

                # 最后兜底：若是本地文件路径，尝试复制
                try:
                    if os.path.exists(image_url_str_local):
                        import shutil
                        shutil.copy2(image_url_str_local, out_path)
                        return out_path.exists()
                except Exception:
                    pass

                return False
            except Exception:
                return False

        def _update_metadata_file(metadata_path: Path, updater_fn):
            """线程安全更新 metadata.json。"""
            with _metadata_lock:
                current = {}
                if metadata_path.exists():
                    try:
                        with open(metadata_path, "r", encoding="utf-8") as f:
                            current = json.load(f) or {}
                    except Exception:
                        current = {}
                try:
                    updated = updater_fn(current if isinstance(current, dict) else {})
                except Exception:
                    updated = current if isinstance(current, dict) else {}
                try:
                    with open(metadata_path, "w", encoding="utf-8") as f:
                        json.dump(updated, f, ensure_ascii=False, indent=2)
                except Exception:
                    pass

        def _async_generate_view(
            view_name: str,
            out_filename: str,
            prompt_text: str,
            reference_front_path: str
        ):
            try:
                out_path = main_character_dir / out_filename
                print(f"🎨 [侧/背图] 开始任务 view={view_name} game_id={game_id} 输出路径={out_path}")
                # 记录本任务开始时正面图的 mtime，写入前校验：若正面已被重新生成则不再写入，避免旧线程覆盖新图
                front_mtime_at_start = 0.0
                if reference_front_path and os.path.isfile(reference_front_path):
                    try:
                        front_mtime_at_start = os.path.getmtime(reference_front_path)
                    except Exception:
                        pass
                print(
                    f"🔎 主角{view_name}图参考正面: {reference_front_path} exists={os.path.exists(reference_front_path) if isinstance(reference_front_path, str) else False} front_mtime_at_start={front_mtime_at_start}"
                )
                
                # 优先使用 gemini-2.5-flash-image 图生图
                img = None
                use_img2img = False
                model = IMAGE_GENERATION_CONFIG.get("yunwu_model", "gemini-3-pro-image-preview")
                if "gemini" in model.lower() and "image" in model.lower():
                    print(f"🔄 尝试使用 gemini-2.5-flash-image 图生图生成{view_name}视图...")
                    img = call_gemini_img2img(prompt_text, reference_front_path, cache_key_suffix=reference_front_path)
                    use_img2img = True
                    if img:
                        print(f"✅ gemini-2.5-flash-image 图生图成功")
                    else:
                        print(f"⚠️ gemini-2.5-flash-image 图生图失败，回退到文生图")
                
                # 如果图生图失败，回退到文生图
                if not img:
                    print(f"🔄 使用文生图生成{view_name}视图...")
                    img = call_image_api_with_custom_size(
                        prompt_text,
                        width=1024,
                        height=1536,
                        reference_image_url=None  # 文生图不使用参考图
                    )
                
                if not img:
                    print(f"⚠️ 主角{view_name}图生成失败：生图返回空 game_id={game_id} out_path={out_path}")
                    return
                
                # 🔧 防竞态：若正面图在本任务期间被重新生成（新一次游戏），则不要用“基于旧正面”的侧/背覆盖
                if front_mtime_at_start > 0 and reference_front_path and os.path.isfile(reference_front_path):
                    try:
                        current_front_mtime = os.path.getmtime(reference_front_path)
                        if current_front_mtime > front_mtime_at_start:
                            print(f"⚠️ 主角{view_name}图跳过写入：正面图已在本任务期间被重新生成（current_mtime={current_front_mtime} > start={front_mtime_at_start}），避免用旧参考生成的图覆盖 game_id={game_id}")
                            return
                    except Exception as e:
                        print(f"⚠️ 主角{view_name}图 mtime 校验异常：{e}，继续写入")
                    
                print(f"📁 [侧/背图] 即将写入 game_id={game_id} path={out_path}")
                ok = _save_image_any(img, out_path)
                if ok:
                    print(f"✅ 主角{view_name}图已保存 game_id={game_id} path={out_path}")
                    metadata_path_local = main_character_dir / "metadata.json"
                    _update_metadata_file(
                        metadata_path_local,
                        lambda m: {
                            **m,
                            "views": {
                                **(m.get("views") if isinstance(m.get("views"), dict) else {}),
                                view_name: {
                                    "filename": out_filename,
                                    "image_url": f"/initial/main_character/{game_id}/{out_filename}",
                                    "prompt": prompt_text,
                                    "reference_front_path": reference_front_path,
                                    "generation_method": "img2img" if use_img2img else "text2img",
                                    "generated_at": datetime.now().isoformat()
                                }
                            }
                        }
                    )
                else:
                    print(f"⚠️ 主角{view_name}图保存失败 game_id={game_id} path={out_path}")
            except Exception as e:
                print(f"❌ 主角{view_name}图生成异常 game_id={game_id} out_path={out_path} error={e}")
                import traceback
                traceback.print_exc()

        # 生成游戏ID（如果未提供）
        if not game_id:
            game_id = generate_game_id()
        
        # 确保目录存在
        main_character_dir = ensure_main_character_dir(game_id)
        
        # 检查是否已存在主角正面图（正面仍命名 main_character.png 以兼容前端）
        front_path = main_character_dir / "main_character.png"
        side_path = main_character_dir / "main_character_side.png"
        back_path = main_character_dir / "main_character_back.png"

        # 1.5 若为现实IP/人物且拿到了参考图：传给生图以提高“还原度”
        reference_image_url = ""
        required_tokens = []
        if isinstance(global_state, dict):
            reference_image_url = _safe_str(global_state.get("_main_character_ref_image_url")).strip()
            required_tokens = global_state.get("_main_character_required_name_tokens") or []

        identifier = _pick_identifier(required_tokens)
        style_label = _style_label(image_style)

        # 🔧 修复：每次新游戏都强制重新生成主角形象，不复用旧文件
        # 原因：即使主角属性相同，但世界观、游戏主题、图片风格等都可能不同，主角形象应该不同
        metadata_path = main_character_dir / "metadata.json"
        any_existed = front_path.exists() or side_path.exists() or back_path.exists() or metadata_path.exists()
        if any_existed:
            print(f"🔄 检测到已存在的主角形象文件（game_id={game_id}），将删除并重新生成")
        for label, p in [("正面图", front_path), ("侧面图", side_path), ("背面图", back_path), ("元数据", metadata_path)]:
            if p.exists():
                try:
                    p.unlink()
                    print(f"   ✅ 已删除旧{label}：{p}")
                except Exception as e:
                    print(f"   ⚠️ 删除旧{label}失败 path={p} error={e}")
        if front_path.exists() or side_path.exists() or back_path.exists():
            print(f"🔄 仍有残留文件（game_id={game_id}），再次尝试删除")
            for label, p in [("正面图", front_path), ("侧面图", side_path), ("背面图", back_path), ("元数据", metadata_path)]:
                if p.exists():
                    try:
                        p.unlink()
                        print(f"   ✅ 再次删除成功：{p}")
                    except Exception as e:
                        print(f"   ❌ 再次删除失败 path={p} error={e}，侧/背图可能仍为旧图")
        
        # 1. 使用LLM生成“人物特征描述”（后续套入三视图模板）
        features = optimize_main_character_prompt_with_llm(protagonist_attr, global_state, image_style)
        front_prompt = prompt_template_front.format(
            identifier=identifier,
            features=features,
            style=style_label
        )
        
        # 2. 调用生图API生成图片（1024x1536）
        # 获取使用的模型信息（用于日志）
        provider = IMAGE_GENERATION_CONFIG.get("provider", "yunwu")
        model = IMAGE_GENERATION_CONFIG.get("yunwu_model", "sora_image") if provider == "yunwu" else "N/A"
        print(f"🎨 正在生成主角形象图片（1024x1536），使用模型：{model}...")
        if reference_image_url:
            print(f"🧷 主角参考图已就绪，将用于生图：{reference_image_url[:120]}...")
        image_url_or_data = call_image_api_with_custom_size(
            front_prompt,
            width=1024,
            height=1536,
            reference_image_url=reference_image_url
        )
        
        print(f"🔍 call_image_api_with_custom_size 返回结果:")
        print(f"   - 类型: {type(image_url_or_data)}")
        print(f"   - 是否为None: {image_url_or_data is None}")
        if image_url_or_data:
            print(f"   - 长度: {len(str(image_url_or_data))} 字符")
            print(f"   - 前100字符: {str(image_url_or_data)[:100]}")
            print(f"   - 是否以'data:image'开头: {str(image_url_or_data).startswith('data:image')}")
            print(f"   - 是否以'http'开头: {str(image_url_or_data).startswith('http')}")
            print(f"   - 是否以'/image_cache'开头: {str(image_url_or_data).startswith('/image_cache')}")
            print(f"   - 是否以'image_cache'开头: {str(image_url_or_data).startswith('image_cache')}")
        
        if not image_url_or_data:
            print("❌ 主角形象图片生成失败：生图API返回空结果")
            return None
        
        # 3. 下载并保存正面图
        image_path = front_path
        print(f"📁 准备保存主角正面图到: {image_path}")
        print(f"📁 目录是否存在: {main_character_dir.exists()}")
        saved_ok = _save_image_any(image_url_or_data, image_path)
        if not saved_ok:
            print("❌ 主角正面图保存失败")
            return None
        print(f"✅ 主角正面图已保存：{image_path}")
        
        # 4. 保存元数据
        metadata = {
            "game_id": game_id,
            "generated_at": datetime.now().isoformat(),
            "prompt": front_prompt,
            "features": features,
            "reference_image_url": reference_image_url,
            "required_name_tokens": required_tokens,
            "protagonist_attr": protagonist_attr,
            "image_style": image_style,
            "width": 1024,
            "height": 1536
        }
        metadata["views"] = {
            "front": {
                "filename": "main_character.png",
                "image_url": f"/initial/main_character/{game_id}/main_character.png",
                "prompt": front_prompt,
                "generated_at": metadata["generated_at"]
            }
        }
        metadata_path = main_character_dir / "metadata.json"
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)
        
        print(f"✅ 主角形象生成完成：{image_path}")

        # 5. 正面生成完成后：后台并行生成侧面/背面（基于正面参考图，不阻塞返回）
        try:
            # 启动前再次删除侧/背图，避免旧会话残留导致“正面新、侧背旧”
            for label, p in [("侧面图", side_path), ("背面图", back_path)]:
                if p.exists():
                    try:
                        p.unlink()
                        print(f"   ✅ 启动侧/背图前再次删除旧{label}：{p}")
                    except Exception as e:
                        print(f"   ⚠️ 启动前删除旧{label}失败 path={p} error={e}")
            front_ref_path = str(front_path.resolve())
            side_prompt = prompt_template_side.format(identifier=identifier)
            back_prompt = prompt_template_back.format(identifier=identifier)

            threading.Thread(
                target=_async_generate_view,
                args=("side", "main_character_side.png", side_prompt, front_ref_path),
                daemon=True
            ).start()
            threading.Thread(
                target=_async_generate_view,
                args=("back", "main_character_back.png", back_prompt, front_ref_path),
                daemon=True
            ).start()
            print("✅ 已启动主角侧面/背面生成任务（后台并行）")
        except Exception as e:
            print(f"⚠️ 启动主角侧面/背面生成任务失败：{str(e)}")
        
        return {
            "game_id": game_id,
            "image_path": str(image_path),
            "image_url": f"/initial/main_character/{game_id}/main_character.png",
            "width": 1024,
            "height": 1536,
            "metadata": metadata
        }
        
    except Exception as e:
        print(f"❌ 主角形象生成失败：{str(e)}")
        print(f"❌ 异常类型：{type(e).__name__}")
        import traceback
        print(f"❌ 完整错误堆栈：")
        traceback.print_exc()
        return None

# ------------------------------
# 视觉内容生成函数
# ------------------------------
import hashlib
import uuid
import random

def generate_scene_image(
    scene_description: str,
    global_state: Dict,
    style: str = "default",
    use_cache: bool = True,
    viewport_width: int = None,
    viewport_height: int = None,
    cache_key_suffix: str = None,
    skip_cache_lookup: bool = False,
) -> Dict:
    """
    生成场景图片（支持本地缓存）
    :param scene_description: 场景描述文本
    :param global_state: 全局状态（用于提取世界观风格）
    :param style: 图片风格
    :param use_cache: 是否使用本地缓存（默认True，下载图片到本地避免OSS URL失效）
    :param viewport_width: 视口宽度（可选，用于按视口宽高比生成图片）
    :param viewport_height: 视口高度（可选，用于按视口宽高比生成图片）
    :param cache_key_suffix: 可选，参与缓存键（如 scene_id_optionIdx），避免不同选项复用同一缓存导致两张图相同
    :param skip_cache_lookup: 为True时不查本地缓存复用旧图，但仍会下载并保存到本地（用于补图等需要每次新图的场景）
    :return: 包含图片URL和元数据的字典
    """
    # 检查是否配置了图片生成API
    provider = IMAGE_GENERATION_CONFIG.get("provider", "yunwu")
    
    if provider == "yunwu" and not IMAGE_GENERATION_CONFIG.get("yunwu_api_key"):
        print("⚠️ yunwu.ai API Key未配置，跳过图片生成")
        return None
    elif provider == "replicate" and not IMAGE_GENERATION_CONFIG.get("replicate_api_token"):
        print("⚠️ Replicate API Token未配置，跳过图片生成")
        return None
    elif provider == "openai" and not IMAGE_GENERATION_CONFIG.get("openai_api_key"):
        print("⚠️ OpenAI API Key未配置，跳过图片生成")
        return None
    
    # 计算图片生成尺寸（视口优先，否则使用剧情图固定 16:9）
    if viewport_width and viewport_height:
        image_width, image_height = calculate_image_size_for_viewport(viewport_width, viewport_height, provider)
        print(f"📐 根据视口尺寸 {viewport_width}x{viewport_height} 计算生成尺寸：{image_width}x{image_height}")
    else:
        image_width, image_height = get_story_image_size(provider)
        print(f"📐 剧情图 16:9 尺寸：{image_width}x{image_height}（适配 16 寸屏）")
    
    # 1. 提取图片风格信息
    image_style = global_state.get('image_style', None)

    # 1.5 视觉连续性上下文（用于同场景统一风格/物件 & 参考上一剧情）
    visual_context = global_state.get('_visual_context') if isinstance(global_state, dict) else None
    if not isinstance(visual_context, dict):
        visual_context = {}
    prev_img_obj = visual_context.get('previousSceneImage') or visual_context.get('currentSceneImage') or {}
    if not isinstance(prev_img_obj, dict):
        prev_img_obj = {}
    reference_image_url = (
        visual_context.get('previous_image_url')
        or prev_img_obj.get('url')
        or prev_img_obj.get('image_url')
        or ""
    )
    reference_image_prompt = (
        visual_context.get('previous_image_prompt')
        or prev_img_obj.get('prompt')
        or prev_img_obj.get('optimized_prompt')
        or ""
    )
    
    # 1.6 获取主角参考图路径（用于保持主角形象一致性）
    # 放宽条件：只要有正面图就使用（第一次场景图与主角生成并行，侧/背可能尚未就绪）
    protagonist_reference_images = []
    game_id = global_state.get('game_id') if isinstance(global_state, dict) else None
    if game_id:
        from pathlib import Path
        main_character_dir = Path("initial") / "main_character" / game_id
        front_path = main_character_dir / "main_character.png"
        side_path = main_character_dir / "main_character_side.png"
        back_path = main_character_dir / "main_character_back.png"
        
        # 至少正面存在即加入参考；三张齐全时用三张，否则用已有视图（保证第一次场景图也能用上主角）
        if front_path.exists():
            protagonist_reference_images.append(str(front_path.resolve()))  # Image 0: 正面
            if side_path.exists():
                protagonist_reference_images.append(str(side_path.resolve()))  # Image 1: 侧面
            if back_path.exists():
                protagonist_reference_images.append(str(back_path.resolve()))  # Image 2: 背面
            if len(protagonist_reference_images) >= 3:
                print(f"✅ 找到主角三视图，将作为参考图传递：{game_id}")
            else:
                print(f"✅ 找到主角参考图（{len(protagonist_reference_images)}张），将作为参考图传递：{game_id}")
        else:
            print(f"⚠️ 主角正面图尚未就绪，将不使用主角参考图")
    
    # 1.6b 每次剧情更新时检查身份揭示，更新配角 aliases（排除主角称呼）
    if game_id and scene_description:
        protagonist_names = get_protagonist_names(global_state) if global_state else None
        update_supporting_role_aliases_from_plot(game_id, scene_description, protagonist_names=protagonist_names)

    # 1.7 先由提示词优化 LLM 生成带「名称-配角N」的视觉描述，再根据优化后的 prompt 识别出场配角
    core_worldview = global_state.get("core_worldview", {}) or {}
    chars = (core_worldview.get("characters", {}) or {}) if isinstance(core_worldview, dict) else {}
    # 已有档案的配角（供 LLM 复用同一角色名/别号）；+ 占位供新角色
    available_supporting_roles_for_tagging = []
    if game_id:
        archives = _load_role_archives(game_id)
        for _rid, arch in archives.items():
            if isinstance(arch, dict):
                rn = _safe_str(arch.get("role_name", "")).strip()
                aliases = arch.get("aliases", [])
                if isinstance(aliases, list) and aliases:
                    names_str = "、".join(aliases[:5])
                else:
                    names_str = rn or ""
                available_supporting_roles_for_tagging.append({
                    "role_key": "已有角色",
                    "role_name": rn,
                    "names_or_aliases": names_str,
                    "shallow_background": _safe_str(arch.get("story_background", ""))[:80] or "（根据剧情）",
                })
    available_supporting_roles_for_tagging.extend([
        {"role_key": "配角1", "shallow_background": "（根据剧情描述，名称从文本中得出）"},
        {"role_key": "配角2", "shallow_background": "（根据剧情描述，名称从文本中得出）"},
    ])

    # 2. 第一次调用 LLM：只负责「名称-配角N」和场景描述，不传配角参考图
    prompt = optimize_image_prompt_with_llm(
        scene_description,
        global_state,
        image_style,
        protagonist_reference_images=protagonist_reference_images if protagonist_reference_images else None,
        supporting_role_references=None,
        available_supporting_roles_for_tagging=available_supporting_roles_for_tagging
    )
    # 打印剧情图提示词 LLM 生成内容（便于确认主角/配角与 Image 编号是否写对）
    if prompt and isinstance(prompt, str):
        _preview_len = 800
        _preview = prompt.strip()[: _preview_len]
        if len(prompt.strip()) > _preview_len:
            _preview += "..."
        print(f"📝 [剧情图提示词] LLM 生成内容（前{min(_preview_len, len(prompt))}字）：\n{_preview}")
    
    # 3. 从优化后的提示词中识别出场配角（名称-配角N），区分已有档案（有参考图）与首次出场（待建档）
    # 以剧情模型为准：若存在本段出场配角（含空列表），则不再从提示词推断；仅当未传入该字段时才 fallback 推断
    supporting_role_references = []
    supporting_role_images = []
    first_appearance_pending = []
    if game_id:
        has_plot_key = "_plot_supporting_characters" in (global_state or {})
        plot_char_tuples = (global_state or {}).get("_plot_supporting_characters")
        if has_plot_key and isinstance(plot_char_tuples, list):
            char_tuples = [(str(n).strip(), str(s).strip()) for n, s in plot_char_tuples if n and s]
            if char_tuples:
                print(f"📋 使用剧情模型输出的本段出场配角（共{len(char_tuples)}个）")
            else:
                print(f"📋 剧情模型未列出本段出场配角，本段不建档配角图")
        else:
            char_tuples = extract_supporting_characters_with_names(prompt)
        if isinstance(global_state, dict) and "_plot_supporting_characters" in global_state:
            del global_state["_plot_supporting_characters"]
        image_index = 3  # Image 0,1,2 为主角；从 3 起为配角
        for display_name, slot in char_tuples:
            role_info = chars.get(slot, {}) or chars.get(display_name, {}) or {}
            if not isinstance(role_info, dict):
                role_info = {}
            arch = get_or_create_supporting_role_archive(
                game_id,
                display_name=display_name,
                slot=slot,
                role_info=role_info,
                first_appear_scene=_clip_text(scene_description, 60),
            )
            if arch.get("_pending_first_appearance"):
                first_appearance_pending.append(arch)
                # 首次出场判断与建档改为前端展示剧情图后由 /notify-scene-displayed 触发，此处仅不传参考图
            else:
                # 优先使用视觉裁剪的单人全身参考图，避免多人同框时用错人
                img_path = arch.get("_resolved_face_ref_path") or arch.get("_resolved_first_img_path") or arch.get("first_img_path", "")
                if img_path:
                    supporting_role_images.append(img_path)
                    supporting_role_references.append({
                        "role_name": slot,
                        "display_name": display_name,
                        "image_index": image_index,
                        "core_features": arch.get("core_features", ""),
                        "first_appear_scene": arch.get("first_appear_scene", ""),
                    })
                    image_index += 1
                    print(f"✅ 配角 {display_name}-{slot} 将作为参考图 Image {image_index - 1} 传递")
                    print(f"   📋 配角信息：role_id={arch.get('_role_id','')}, role_name={arch.get('role_name','')}, aliases={arch.get('aliases',[])}, first_img={Path(img_path).name if img_path else ''}")
    
    # 打印当前游戏所有配角档案摘要
    if game_id:
        _archives = _load_role_archives(game_id)
        if _archives:
            print(f"📋 当前配角档案（共{len(_archives)}个）：")
            for _rid, _a in _archives.items():
                if isinstance(_a, dict):
                    _aliases = _a.get("aliases", [])
                    _rn = _a.get("role_name", "")
                    print(f"   - {_rid}: role_name={_rn}, aliases={_aliases}")
    
    # 4. 由代码将「参考 Image N」拼接到提示词末尾（若 LLM 已写位置指引则保留）
    if supporting_role_references:
        append_parts = []
        for sr in supporting_role_references:
            slot = _safe_str(sr.get("role_name", "")).strip()
            display_name = _safe_str(sr.get("display_name", "")).strip()
            img_idx = sr.get("image_index", 3)
            dn = display_name or slot
            append_parts.append(f"{dn}-{slot} 参考 Image {img_idx}，以图中对应人物的形象为准，保持核心特征不变（重要：Image {img_idx} 中该配角的五官与体型不可改动）")
        if append_parts:
            prompt = (prompt.rstrip() + "。" + "。".join(append_parts))
        # 打印拼接「参考 Image N」后的提示词尾部
        _tail_len = 350
        if prompt and len(prompt) > _tail_len:
            print(f"📝 [剧情图提示词] 拼接配角参考后，末尾{_tail_len}字：...{prompt[-_tail_len:]}")
    
    # 5. 调用AI图片生成API（传递尺寸参数和参考图）
    # 若有上一张剧情图，解析为可加载路径并作为最后一张参考图（用于视觉延续）
    previous_scene_image_path = None
    if reference_image_url and isinstance(reference_image_url, str):
        ref_url = reference_image_url.strip()
        if ref_url.startswith("/image_cache/") or ref_url.startswith("image_cache/"):
            prev_local = Path(__file__).resolve().parent.parent.parent / "image_cache" / Path(ref_url).name
            if prev_local.exists():
                previous_scene_image_path = str(prev_local)
        elif ref_url.startswith("http://") or ref_url.startswith("https://") or os.path.exists(ref_url):
            previous_scene_image_path = ref_url

    try:
        if provider == "yunwu":
            # yunwu.ai 易受 429 / 返回格式波动影响：失败时可选用本地 SD 兜底
            image_url = None
            try:
                # yunwu.ai可能不支持自定义尺寸，在提示词中添加尺寸要求
                size_prompt = f"{prompt}, aspect ratio {image_width}:{image_height}"
                
                # 参考图：主角 + 配角 + 上一张剧情图（若有）
                model = IMAGE_GENERATION_CONFIG.get("yunwu_model", "gemini-3-pro-image-preview")
                all_reference_images = list(protagonist_reference_images) if protagonist_reference_images else []
                all_reference_images.extend(supporting_role_images if supporting_role_images else [])
                if previous_scene_image_path:
                    all_reference_images.append(previous_scene_image_path)
                    print(f"🖼️ 已将上一张剧情图加入参考图（共{len(all_reference_images)}张）")
                if all_reference_images and len(all_reference_images) >= 1:
                    if "gemini" in model.lower() and "image" in model.lower():
                        n_prev = 1 if previous_scene_image_path else 0
                        print(f"🎨 使用 gemini-2.5-flash-image 图生图，传递{len(all_reference_images)}张参考图（主角{len(protagonist_reference_images or [])}张+配角{len(supporting_role_images or [])}张+上一张剧情图{n_prev}张）")
                        # 构建参考图说明：主角 Image 0/1/2 + 配角 Image 3/4/... + 上一张剧情图 Image N
                        prefix_lines = []
                        n_prot = len(protagonist_reference_images or [])
                        if n_prot >= 1:
                            prefix_lines.append("Image 0: Front view portrait of the protagonist")
                        if n_prot >= 2:
                            prefix_lines.append("Image 1: Side view portrait of the protagonist")
                        if n_prot >= 3:
                            prefix_lines.append("Image 2: Back view portrait of the protagonist")
                        for sr in (supporting_role_references or []):
                            idx = sr.get("image_index", len(prefix_lines))
                            rn = sr.get("display_name", "") or sr.get("role_name", "")
                            cf = _clip_text(sr.get("core_features", ""), 80)
                            prefix_lines.append(f"Image {idx}: {rn} (MUST preserve - face, hair, build). Core features (DO NOT MODIFY): {cf}")
                        if previous_scene_image_path:
                            prev_idx = len(prefix_lines)
                            prefix_lines.append(f"Image {prev_idx}: Previous scene image (for visual continuity - maintain consistent style, lighting, and character appearance).")
                        prefix_prompt = "\n".join(prefix_lines) + "\n\n"
                        full_prompt = prefix_prompt + prompt + f", aspect ratio {image_width}:{image_height}"
                        # 传入 cache_key_suffix，使里层 save_base64_image 也按选项区分，避免“上一次的图当成本次的”
                        image_url = call_gemini_img2img(full_prompt, all_reference_images, cache_key_suffix=cache_key_suffix)
                    else:
                        print(f"⚠️ 当前模型 {model} 不支持多张参考图，使用文生图")
                        image_url = call_yunwu_image_api(size_prompt, style)
                else:
                    # 没有参考图，使用普通文生图
                    image_url = call_yunwu_image_api(size_prompt, style)
            except Exception as e:
                print(f"⚠️ yunwu.ai 生图失败，将尝试兜底（如已配置）：{str(e)}")
                image_url = None

            if not image_url:
                sd_base = IMAGE_GENERATION_CONFIG.get("stable_diffusion_base_url", "")
                if sd_base:
                    try:
                        print("🛟 使用 Stable Diffusion 作为兜底生图（yunwu 失败/无返回）")
                        # SD 兜底时，如果有主角参考图，使用第一张（正面）作为参考
                        sd_ref = protagonist_reference_images[0] if protagonist_reference_images else reference_image_url
                        image_url = call_stable_diffusion_api_with_size(prompt, image_width, image_height, style, reference_image_url=sd_ref)
                    except Exception as e:
                        print(f"⚠️ Stable Diffusion 兜底失败：{str(e)}")
        elif provider == "replicate":
            image_url = call_replicate_api(prompt, style)
        elif provider == "openai":
            image_url = call_dalle_api_with_size(prompt, f"{image_width}x{image_height}")
        elif provider == "stable_diffusion":
            image_url = call_stable_diffusion_api_with_size(prompt, image_width, image_height, style, reference_image_url=reference_image_url)
        elif provider == "comfyui":
            image_url = call_comfyui_api(prompt, style)
        else:
            print(f"⚠️ 不支持的图片生成服务：{provider}")
            return None
        
        if not image_url:
            return None
        
        # 如果启用缓存，下载图片到本地
        if use_cache and image_url:
            try:
                import hashlib
                from pathlib import Path
                
                MAX_DOWNLOAD_BYTES = 10 * 1024 * 1024  # 10MB 防止超大文件拖垮内存/磁盘
                VALID_IMAGE_PREFIX = "image/"

                # 创建缓存目录
                IMAGE_CACHE_DIR = "image_cache"
                os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)
                
                # 生成缓存键（包含尺寸信息，避免不同尺寸的图片互相覆盖）
                # 新增：当存在“参考上一剧情图片/提示词”时，把参考信息纳入缓存键，避免误用旧缓存。
                ref_sig = (reference_image_prompt or reference_image_url or "").strip()
                if ref_sig:
                    ref_hash = hashlib.md5(ref_sig.encode("utf-8")).hexdigest()[:10]
                    cache_key_seed = f"{provider}_{style}_{scene_description}_{ref_hash}_{image_width}x{image_height}"
                else:
                    cache_key_seed = f"{provider}_{style}_{scene_description}_{image_width}x{image_height}"
                # 选项级唯一标识，避免多选项并行时共用同一缓存键导致“前后两张图相同”
                if cache_key_suffix:
                    cache_key_seed = f"{cache_key_seed}_{cache_key_suffix}"
                prompt_hash = hashlib.md5(cache_key_seed.encode()).hexdigest()
                cache_path = Path(IMAGE_CACHE_DIR) / f"{prompt_hash}.png"
                
                # 检查是否已缓存（skip_cache_lookup 时跳过，仍下载并保存本次生成结果）
                # 配角初登场建档改为「展示到前端后」由 game_server 统一执行，此处不再建档
                if not skip_cache_lookup and cache_path.exists():
                    print(f"✅ 使用本地缓存的图片：{cache_path}")
                    return {
                        "url": f"/image_cache/{prompt_hash}.png",
                        "prompt": prompt,
                        "style": style,
                        "width": image_width,
                        "height": image_height,
                        "cached": True
                    }
                
                # 检查image_url是否是相对路径（本地缓存路径）
                if image_url.startswith('/image_cache/') or image_url.startswith('image_cache/'):
                    # 如果image_url已经是相对路径，说明可能是从其他地方传入的缓存路径
                    # 检查对应的文件是否存在
                    import re
                    hash_match = re.search(r'([a-f0-9]{32})\.png', image_url)
                    if hash_match:
                        existing_hash = hash_match.group(1)
                        existing_path = Path(IMAGE_CACHE_DIR) / f"{existing_hash}.png"
                        if existing_path.exists():
                            # 如果文件存在，使用现有的hash，或者复制到新的hash
                            if existing_hash == prompt_hash:
                                print(f"✅ 使用现有的本地缓存图片：{existing_path}")
                                return {
                                    "url": f"/image_cache/{prompt_hash}.png",
                                    "prompt": prompt,
                                    "style": style,
                                    "width": image_width,
                                    "height": image_height,
                                    "cached": True
                                }
                            else:
                                # API 返回的本地路径与当前请求的缓存键不一致（例如本请求用了 cache_key_suffix）。
                                # 不复制到 prompt_hash，避免覆盖其他选项的图片导致“两张图相同”；直接返回本次生成结果的路径。
                                print(f"✅ 使用本次生成结果（与缓存键不同，不复制避免覆盖）：{existing_path}")
                                return {
                                    "url": f"/image_cache/{existing_hash}.png",
                                    "prompt": prompt,
                                    "style": style,
                                    "width": image_width,
                                    "height": image_height,
                                    "cached": True
                                }
                    # 如果相对路径对应的文件不存在，抛出错误
                    raise ValueError(f"本地缓存路径对应的文件不存在：{image_url}")
                
                # 检查是否是完整的URL
                if not (image_url.startswith('http://') or image_url.startswith('https://')):
                    raise ValueError(f"无效的图片URL格式：{image_url}（需要完整的HTTP/HTTPS URL或本地缓存路径）")
                
                # 检查是否是私有Azure Blob Storage URL（无法直接下载）
                is_private_blob = 'blob.core.windows.net/private' in image_url or '/private/' in image_url
                if is_private_blob:
                    print(f"⚠️ 检测到私有Azure Blob Storage URL，无法直接下载")
                    print(f"   将直接返回URL，由前端处理：{image_url[:80]}...")
                    # 对于私有URL，直接返回URL，不尝试下载
                    return {
                        "url": image_url,
                        "prompt": prompt,
                        "style": style,
                        "width": image_width,
                        "height": image_height,
                        "cached": False  # 私有URL无法缓存
                    }
                
                # 下载图片到本地（带重试 + 流式写入，降低 image.pollinations.ai 等站点超时概率）
                print(f"📥 正在下载图片到本地缓存：{image_url[:80]}...")
                import time
                download_retries = int(os.getenv("IMAGE_DOWNLOAD_MAX_RETRIES", "3"))
                connect_timeout = float(os.getenv("IMAGE_DOWNLOAD_CONNECT_TIMEOUT", "10"))
                read_timeout = float(os.getenv("IMAGE_DOWNLOAD_READ_TIMEOUT", "60"))
                ua = os.getenv("IMAGE_DOWNLOAD_USER_AGENT", "DN-GameServer/1.0")

                response = None
                last_err = None
                for dl_attempt in range(download_retries):
                    try:
                        response = requests.get(
                            image_url,
                            timeout=(connect_timeout, read_timeout),
                            stream=True,
                            headers={"User-Agent": ua}
                        )
                        response.raise_for_status()
                        break
                    except requests.exceptions.HTTPError as e:
                        if e.response and e.response.status_code == 409:
                            # 409错误表示私有存储，无法公开访问
                            print(f"⚠️ 图片URL是私有存储，无法直接下载（409错误）")
                            print(f"   将直接返回URL，由前端处理：{image_url[:80]}...")
                            return {
                                "url": image_url,
                                "prompt": prompt,
                                "style": style,
                                "width": image_width,
                                "height": image_height,
                                "cached": False
                            }
                        raise
                    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
                        last_err = e
                        if dl_attempt < download_retries - 1:
                            backoff = (1.5 * (2 ** dl_attempt)) + random.random()
                            print(f"⚠️ 图片下载超时/连接失败，{backoff:.1f}s 后重试（{dl_attempt+1}/{download_retries}）: {e}")
                            time.sleep(backoff)
                            continue
                        raise

                # 基础类型校验
                content_type = response.headers.get("Content-Type", "")
                if VALID_IMAGE_PREFIX not in content_type:
                    raise ValueError(f"响应类型异常：{content_type}")

                downloaded = 0
                with open(cache_path, 'wb') as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > MAX_DOWNLOAD_BYTES:
                            raise ValueError("图片过大，已终止下载（>10MB）")
                        f.write(chunk)
                
                print(f"✅ 图片已缓存到本地：{cache_path}")
                return {
                    "url": f"/image_cache/{prompt_hash}.png",
                    "prompt": prompt,
                    "style": style,
                    "width": image_width,
                    "height": image_height,
                    "cached": True
                }
            except Exception as cache_error:
                # 如果缓存过程中写入失败，确保不留空文件
                try:
                    if 'cache_path' in locals() and cache_path.exists():
                        cache_path.unlink()
                except Exception:
                    pass
                print(f"⚠️ 图片缓存失败，使用原始URL：{str(cache_error)}")
                # 缓存失败时返回原始URL
                return {
                    "url": image_url,
                    "prompt": prompt,
                    "style": style,
                    "width": image_width,
                    "height": image_height,
                    "cached": False
                }
        
        # 不使用缓存，直接返回OSS URL
        return {
            "url": image_url,
            "prompt": prompt,
            "style": style,
            "width": image_width,
            "height": image_height
        }
    except Exception as e:
        print(f"❌ 图片生成失败：{str(e)}")
        import traceback
        traceback.print_exc()
        return None

def call_gemini_img2img(prompt: str, reference_image_path, additional_reference_images: List[str] = None, cache_key_suffix: str = None) -> str:
    """
    使用 gemini-2.5-flash-image 进行图生图，支持多张参考图
    :param prompt: 文本提示词
    :param reference_image_path: 参考图片路径（本地路径或 data URI），可以是字符串或字符串列表
    :param additional_reference_images: 额外的参考图片路径列表（可选）
    :param cache_key_suffix: 可选，参与 base64 缓存 key（如参考图路径），避免不同游戏复用同一缓存
    :return: 生成的图片 URL 或 base64 数据，失败返回 None
    """
    import time
    import base64
    
    api_key = IMAGE_GENERATION_CONFIG.get("yunwu_api_key")
    base_url = IMAGE_GENERATION_CONFIG.get("yunwu_base_url", "https://yunwu.ai/v1")
    model = IMAGE_GENERATION_CONFIG.get("yunwu_model", "gemini-3-pro-image-preview")
    
    if not api_key:
        print("⚠️ gemini-2.5-flash-image 图生图：API Key未配置")
        return None
    
    # 检查模型是否为 gemini-2.5-flash-image
    if "gemini" not in model.lower() or "image" not in model.lower():
        print(f"⚠️ 当前模型 {model} 不是 gemini-2.5-flash-image，跳过图生图")
        return None
    
    # 处理参考图片：支持单个路径或路径列表
    reference_paths = []
    if isinstance(reference_image_path, (list, tuple)):
        reference_paths.extend(reference_image_path)
    elif reference_image_path:
        reference_paths.append(reference_image_path)
    
    # 添加额外的参考图片
    if additional_reference_images:
        if isinstance(additional_reference_images, (list, tuple)):
            reference_paths.extend(additional_reference_images)
        else:
            reference_paths.append(additional_reference_images)
    
    if not reference_paths:
        print("⚠️ 未提供参考图片")
        return None
    
    # 将所有参考图片转换为 base64 data URI
    image_data_uris = []
    for ref_path in reference_paths:
        image_data_uri = _ref_image_to_input(ref_path)
        if image_data_uri:
            image_data_uris.append(image_data_uri)
        else:
            print(f"⚠️ 无法加载参考图片：{ref_path}")
    
    if not image_data_uris:
        print("⚠️ 所有参考图片加载失败")
        return None
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # Gemini API 格式：multimodal content with multiple images
    # 根据 Gemini API 文档，支持多张参考图进行图生图
    # 构建 content 数组：先添加所有图片，最后添加文本提示
    content_items = []
    for image_data_uri in image_data_uris:
        content_items.append({
            "type": "image_url",
            "image_url": {
                "url": image_data_uri
            }
        })
    
    # 根据参考图数量调整提示词
    if len(image_data_uris) == 1:
        prompt_text = f"Edit this image: {prompt}\n\nReturn only the edited image as base64 data (data:image/png;base64,...) or image URL (https://...). Do not include any text, code blocks, or explanations."
    else:
        prompt_text = f"Based on these {len(image_data_uris)} reference images, generate a new image: {prompt}\n\nReturn only the generated image as base64 data (data:image/png;base64,...) or image URL (https://...). Do not include any text, code blocks, or explanations."
    
    content_items.append({
        "type": "text",
        "text": prompt_text
    })
    
    request_body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": content_items
            }
        ],
        "temperature": 0.1,
        "max_tokens": 4000
    }
    
    request_timeout = int(os.getenv("YUNWU_IMAGE_TIMEOUT_SECONDS", "180"))
    min_interval = float(os.getenv("YUNWU_MIN_INTERVAL_SECONDS", "12"))
    
    try:
        # 跨线程限速
        global _YUNWU_LAST_CALL_TS
        with _YUNWU_RATE_LOCK:
            now = time.time()
            delta = now - _YUNWU_LAST_CALL_TS
            if delta < min_interval:
                sleep_s = (min_interval - delta) + random.random() * 0.5
                print(f"⏳ gemini 图生图限速：等待 {sleep_s:.1f}s")
                time.sleep(sleep_s)
            _YUNWU_LAST_CALL_TS = time.time()
        
        print(f"🔄 调用 gemini-2.5-flash-image 图生图 API（{len(image_data_uris)}张参考图）...")
        print(f"   提示词: {prompt[:100]}...")
        ref_paths_str = ", ".join([ref[:50] + "..." if len(ref) > 50 else ref for ref in reference_paths])
        print(f"   参考图: {ref_paths_str}")
        
        response = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=request_body,
            timeout=request_timeout
        )
        
        if response.status_code != 200:
            error_msg = ""
            try:
                error_body = response.json()
                if isinstance(error_body, dict):
                    error_obj = error_body.get("error", {})
                    if isinstance(error_obj, dict):
                        error_msg = error_obj.get("message", "")
                    else:
                        error_msg = str(error_obj)
                else:
                    error_msg = str(error_body)
            except:
                error_msg = response.text[:200]
            
            print(f"❌ gemini-2.5-flash-image 图生图 API 错误 {response.status_code}: {error_msg}")
            return None
        
        # 解析响应（复用 call_yunwu_image_api 的解析逻辑）
        result = response.json()
        
        # 尝试从响应中提取图片
        # 使用与 call_yunwu_image_api 相同的解析策略
        def _extract_image_from_response(obj) -> str:
            try:
                if not isinstance(obj, dict):
                    return ""
                # 顶层直接给 url
                for k in ("image_url", "url"):
                    v = obj.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
                # choices[0].message.content
                choices = obj.get("choices", [])
                if choices and len(choices) > 0:
                    message = choices[0].get("message", {})
                    content = message.get("content", "")
                    if isinstance(content, str) and content.strip():
                        # 检查是否是 base64 或 URL
                        if content.startswith("data:image") or content.startswith("http"):
                            return content.strip()
                        # 尝试从文本中提取 base64
                        import re
                        base64_match = re.search(r'data:image/[^;]+;base64,([A-Za-z0-9+/=\s]+)', content)
                        if base64_match:
                            return f"data:image/png;base64,{base64_match.group(1).strip()}"
                return ""
            except Exception as e:
                print(f"⚠️ 解析响应时出错: {str(e)}")
                return ""
        
        image_result = _extract_image_from_response(result)
        if image_result:
            # 如果是 base64，保存到本地缓存（cache_key_suffix 用于主角侧/背图按游戏区分）
            if image_result.startswith("data:image"):
                saved_path = save_base64_image(image_result, prompt, cache_key_suffix=cache_key_suffix)
                if saved_path:
                    return saved_path
            return image_result
        
        print(f"⚠️ gemini-2.5-flash-image 图生图响应中未找到图片数据")
        return None
        
    except Exception as e:
        print(f"❌ gemini-2.5-flash-image 图生图调用异常: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def call_yunwu_image_api(prompt: str, style: str) -> str:
    """调用yunwu.ai图片生成API（带重试机制处理速率限制）"""
    import time
    
    api_key = IMAGE_GENERATION_CONFIG.get("yunwu_api_key")
    base_url = IMAGE_GENERATION_CONFIG.get("yunwu_base_url", "https://yunwu.ai/v1")
    model = IMAGE_GENERATION_CONFIG.get("yunwu_model", "sora_image")
    
    if not api_key:
        raise ValueError("yunwu.ai API Key未配置")
    
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    # 调用yunwu.ai的图片生成API（使用chat/completions接口）
    # 注意：gemini-2.5-flash-image 模型可能不支持 response_format 参数
    # 注意：不同模型可能有不同的返回格式，需要兼容处理
    
    # 根据模型类型调整提示词
    if "gemini" in model.lower() and "image" in model.lower():
        # Gemini 图片生成模型：尝试使用英文提示词（模型可能是英文训练的）
        # 尝试不使用 system message，只使用 user message，更简洁直接
        request_body = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": f"Generate an image based on this description: {prompt}\n\nReturn only the image as base64 data (data:image/png;base64,...) or image URL (https://...). Do not include any text, code blocks, or explanations."
                }
            ],
            "temperature": 0.1,
            "max_tokens": 4000
        }
    elif "gemini" in model.lower():
        # 其他 Gemini 模型
        system_content = "你是一个图片生成模型。直接生成图片并返回base64数据或URL，不要任何文字说明或代码块。"
        user_content = f"生成图片：{prompt}\n\n返回格式：data:image/png;base64,<base64数据> 或 https://图片URL"
        request_body = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": system_content
                },
                {
                    "role": "user",
                    "content": user_content
                }
            ],
            "temperature": 0.1,
            "max_tokens": 4000
        }
    else:
        # 其他模型的提示词
        system_content = "你是一个图片生成API。用户会提供图片描述，你必须生成图片并返回图片URL或base64数据。优先返回base64格式的图片数据（data:image/png;base64,...），如果没有则返回图片URL。"
        user_content = f"请生成一张图片，描述：{prompt}\n\n请返回图片URL或base64格式的图片数据。"
        request_body = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": system_content
                },
                {
                    "role": "user",
                    "content": user_content
                }
            ],
            "temperature": 0.3,
            "max_tokens": 2000
        }
    
    # 注意：gemini-2.5-flash-image 模型不支持 response_format 参数，不要添加
    # 如果模型是 sora_image 或其他支持JSON模式的模型，可以尝试添加
    # 但 gemini-2.5-flash-image 不支持，会导致400错误
    
    # 可配置：超时/最小间隔/重试次数（避免长时间卡住 + 降低 429 概率）
    # 🔧 修复：增加默认超时时间到180秒，因为图片生成通常需要较长时间
    request_timeout = int(os.getenv("YUNWU_IMAGE_TIMEOUT_SECONDS", "180"))  # 从90秒增加到180秒
    min_interval = float(os.getenv("YUNWU_MIN_INTERVAL_SECONDS", "12"))
    max_retries = int(os.getenv("YUNWU_IMAGE_MAX_RETRIES", "3"))
    for attempt in range(max_retries):
        try:
            # 跨线程限速：保证相邻请求之间至少间隔 min_interval 秒
            global _YUNWU_LAST_CALL_TS
            with _YUNWU_RATE_LOCK:
                now = time.time()
                delta = now - _YUNWU_LAST_CALL_TS
                if delta < min_interval:
                    sleep_s = (min_interval - delta) + random.random() * 0.5
                    print(f"⏳ yunwu.ai 限速：等待 {sleep_s:.1f}s（最小间隔 {min_interval}s）")
                    time.sleep(sleep_s)
                _YUNWU_LAST_CALL_TS = time.time()

            # 🔍 调试：打印实际发送的请求内容
            print(f"🔍 ========== 发送给API的请求内容 ==========")
            print(f"🔍 API端点: {base_url}/chat/completions")
            print(f"🔍 模型: {model}")
            try:
                import json
                request_str = json.dumps(request_body, ensure_ascii=False, indent=2)
                # 如果请求太长，只打印前2000字符
                if len(request_str) > 2000:
                    print(f"📤 请求内容（前2000字符）:\n{request_str[:2000]}")
                    print(f"\n📤 请求内容（后500字符）:\n{request_str[-500:]}")
                else:
                    print(f"📤 请求内容:\n{request_str}")
            except Exception as e:
                print(f"⚠️ 无法序列化请求内容: {str(e)}")
                print(f"📤 请求内容: {str(request_body)[:1000]}")
            print(f"🔍 ==========================================")
            
            # 图片生成可能耗时，但不应无限期阻塞
            # 🔧 修复：添加超时日志，方便调试
            print(f"⏱️ 发送图片生成请求（超时时间：{request_timeout}秒）...")
            start_request_time = time.time()
            response = requests.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=request_body,
                timeout=request_timeout
            )
            elapsed_time = time.time() - start_request_time
            print(f"✅ API请求完成，耗时：{elapsed_time:.2f}秒")
            
            # 先检查HTTP状态码，区分不同类型的错误
            if response.status_code == 400:
                # 400错误：请求格式错误
                try:
                    error_body = response.json()
                    error_message = ""
                    if isinstance(error_body, dict):
                        error_obj = error_body.get("error", {})
                        if isinstance(error_obj, dict):
                            error_message = error_obj.get("message", "")
                        else:
                            error_message = str(error_obj)
                    else:
                        error_message = str(error_body)
                    
                    print(f"❌ yunwu.ai图片生成API请求格式错误（400）：{error_message}")
                    
                    # 检查是否是JSON mode不支持的错误
                    if "JSON mode is not enabled" in error_message or "response_format" in error_message:
                        print(f"💡 提示：模型 {model} 不支持 response_format 参数")
                        # 移除 response_format 参数后重试（如果还有重试机会）
                        if attempt < max_retries - 1:
                            # 确保 request_body 中没有 response_format
                            if "response_format" in request_body:
                                request_body.pop("response_format")
                                print(f"   移除 response_format 参数后重试（尝试 {attempt + 2}/{max_retries}）...")
                                time.sleep(2)  # 等待2秒后重试
                                continue
                    
                    # 检查是否是API格式错误（messages字段不存在）
                    if "Unknown name" in error_message or "Cannot find field" in error_message or "messages" in error_message:
                        print(f"💡 提示：API请求格式可能不正确，模型 {model} 可能使用不同的API格式")
                        print(f"💡 当前使用的格式：chat/completions（标准OpenAI格式）")
                        print(f"💡 建议：")
                        print(f"   1. 检查 yunwu.ai API 文档，确认 {model} 模型的正确调用方式")
                        print(f"   2. 确认模型名称是否正确：{model}")
                        print(f"   3. 可能需要使用不同的API端点或请求格式")
                        # 400错误不应该重试（格式错误重试也没用），直接抛出
                        response.raise_for_status()
                    
                    # 其他400错误直接抛出
                    response.raise_for_status()
                except Exception as parse_error:
                    print(f"❌ 无法解析400错误响应：{str(parse_error)}")
                    response.raise_for_status()
            
            elif response.status_code == 429:
                # 尝试从响应头获取重试时间和详细信息
                retry_after = response.headers.get('Retry-After')
                rate_limit_info = {}
                
                # 尝试解析响应体获取更多信息
                try:
                    error_body = response.json()
                    if isinstance(error_body, dict):
                        rate_limit_info = error_body
                        print(f"🔍 速率限制详细信息：{json.dumps(rate_limit_info, ensure_ascii=False)}")
                except:
                    error_text = response.text[:200] if hasattr(response, 'text') else ""
                    if error_text:
                        print(f"🔍 速率限制响应内容：{error_text}")
                
                # 检查响应头中的速率限制信息
                rate_limit_headers = {
                    'X-RateLimit-Limit': response.headers.get('X-RateLimit-Limit'),
                    'X-RateLimit-Remaining': response.headers.get('X-RateLimit-Remaining'),
                    'X-RateLimit-Reset': response.headers.get('X-RateLimit-Reset'),
                    'Retry-After': retry_after
                }
                if any(rate_limit_headers.values()):
                    print(f"🔍 速率限制响应头：{json.dumps({k: v for k, v in rate_limit_headers.items() if v}, ensure_ascii=False)}")
                
                # Retry-After 可能是秒数（整数）或 HTTP-date（如 RFC 7231 指定）
                wait_time = None
                if retry_after:
                    retry_after_raw = str(retry_after).strip()
                    # 先尝试按“秒数”解析
                    try:
                        wait_time = int(retry_after_raw)
                        if wait_time < 0:
                            wait_time = 0
                        print(f"⚠️ 遇到速率限制（429），API建议等待 {wait_time} 秒后重试（尝试 {attempt + 1}/{max_retries}）")
                    except (TypeError, ValueError):
                        # 再尝试按 HTTP-date 解析
                        try:
                            from email.utils import parsedate_to_datetime
                            from datetime import datetime, timezone
                            dt = parsedate_to_datetime(retry_after_raw)
                            if dt is not None:
                                if dt.tzinfo is None:
                                    dt = dt.replace(tzinfo=timezone.utc)
                                now = datetime.now(timezone.utc)
                                wait_seconds = int((dt.astimezone(timezone.utc) - now).total_seconds())
                                wait_time = max(0, wait_seconds)
                                print(f"⚠️ 遇到速率限制（429），API建议等待 {wait_time} 秒后重试（尝试 {attempt + 1}/{max_retries}）")
                        except Exception:
                            wait_time = None
                
                if wait_time is None:
                    # 如果 Retry-After 不存在或无法解析，使用指数退避：10s, 20s, 40s
                    wait_time = 10 * (2 ** attempt)
                    if retry_after:
                        print(f"⚠️ 遇到速率限制（429），但 Retry-After 无法解析（{retry_after!r}），改用指数退避等待 {wait_time} 秒后重试（尝试 {attempt + 1}/{max_retries}）")
                    else:
                        print(f"⚠️ 遇到速率限制（429），等待 {wait_time} 秒后重试（尝试 {attempt + 1}/{max_retries}）")
                
                print(f"💡 可能的原因：")
                print(f"   1. yunwu.ai 最近调整了速率限制策略")
                print(f"   2. API配额已用完（免费额度用尽）")
                print(f"   3. 账户级别变化（可能降级到免费版）")
                print(f"   4. 使用量增加导致触发限制")
                print(f"   5. 图片生成API的限制比文本生成更严格")
                print(f"💡 建议：")
                print(f"   - 检查 yunwu.ai 账户状态和配额")
                print(f"   - 考虑切换到其他图片生成服务（ComfyUI、Replicate等）")
                print(f"   - 增加请求间隔时间")
                
                # 如果还有重试机会，等待后继续
                if attempt < max_retries - 1:
                    time.sleep(wait_time)
                    continue
                else:
                    # 最后一次尝试也失败，抛出异常
                    response.raise_for_status()
            
            # 其他HTTP错误直接抛出
            response.raise_for_status()
            
            # 如果成功，解析响应（兼容：返回体不是 JSON / 结构变化）
            try:
                result = response.json()
                # 打印响应状态码和基本信息
                print(f"✅ yunwu.ai API响应成功（状态码: {response.status_code}）")
            except Exception as e:
                text_preview = (response.text or "")[:500]
                print(f"⚠️ yunwu.ai 返回非JSON内容，无法解析：{text_preview}")
                print(f"⚠️ 解析错误：{str(e)}")
                return None

            # 解析策略0：优先从“结构化字段”提取（避免只依赖 choices[0].message.content）
            def _extract_from_structured(obj) -> str:
                try:
                    if not isinstance(obj, dict):
                        return ""
                    # 顶层直接给 url
                    for k in ("image_url", "url"):
                        v = obj.get(k)
                        if isinstance(v, str) and v.strip():
                            return v.strip()
                    # 常见：images: [<base64>, ...]
                    images = obj.get("images")
                    if isinstance(images, list) and images:
                        first = images[0]
                        if isinstance(first, str) and first.strip():
                            s = first.strip()
                            if s.startswith("data:image"):
                                return save_base64_image(s, prompt) or ""
                            return save_base64_image(f"data:image/png;base64,{s}", prompt) or ""
                    # 常见：data: {url:...} 或 data: [{url:...}]
                    data = obj.get("data")
                    if isinstance(data, dict):
                        for k in ("url", "image_url"):
                            v = data.get(k)
                            if isinstance(v, str) and v.strip():
                                return v.strip()
                        for k in ("b64_json", "base64", "image_base64"):
                            v = data.get(k)
                            if isinstance(v, str) and v.strip():
                                return save_base64_image(f"data:image/png;base64,{v.strip()}", prompt) or ""
                    if isinstance(data, list) and data:
                        for item in data:
                            if not isinstance(item, dict):
                                continue
                            for k in ("url", "image_url"):
                                v = item.get(k)
                                if isinstance(v, str) and v.strip():
                                    return v.strip()
                            for k in ("b64_json", "base64", "image_base64"):
                                v = item.get(k)
                                if isinstance(v, str) and v.strip():
                                    return save_base64_image(f"data:image/png;base64,{v.strip()}", prompt) or ""
                    return ""
                except Exception:
                    return ""

            structured = _extract_from_structured(result)
            if structured:
                return structured

            # 打印完整的响应结构用于调试
            print(f"🔍 yunwu.ai API完整响应结构：")
            print(f"   - 响应类型: {type(result)}")
            print(f"   - 响应键: {list(result.keys()) if isinstance(result, dict) else 'N/A'}")
            
            # 🔍 检查响应中的其他顶层字段（可能包含图片数据）
            print(f"🔍 检查响应中的其他顶层字段...")
            for key in ["data", "image", "image_url", "url", "images", "output", "result"]:
                if key in result:
                    value = result[key]
                    value_type = type(value).__name__
                    if isinstance(value, str):
                        print(f"   - result['{key}']: {value_type}, 长度={len(value)}, 前200字符={value[:200]}")
                        if value.startswith("data:image") or value.startswith("http://") or value.startswith("https://"):
                            print(f"💡 在result['{key}']中发现可能的图片数据！")
                            if value.startswith("data:image"):
                                saved_path = save_base64_image(value, prompt)
                                if saved_path:
                                    return saved_path
                            elif value.startswith("http://") or value.startswith("https://"):
                                return value
                    else:
                        print(f"   - result['{key}']: {value_type} = {str(value)[:200]}")
            
            # 🔍 检查 usage 字段（可能包含 token 信息，用于确认API确实返回了内容）
            if "usage" in result:
                usage = result["usage"]
                print(f"🔍 API使用情况: {usage}")
                if isinstance(usage, dict):
                    total_tokens = usage.get("total_tokens", 0)
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    completion_tokens = usage.get("completion_tokens", 0)
                    print(f"   - 总tokens: {total_tokens}, 输入tokens: {prompt_tokens}, 输出tokens: {completion_tokens}")
                    if completion_tokens > 0:
                        print(f"💡 API确实返回了 {completion_tokens} 个输出tokens，说明有内容返回！")
            
            choices = result.get("choices", [])
            print(f"   - choices数量: {len(choices) if choices else 0}")
            
            if not choices or len(choices) == 0:
                print(f"⚠️ yunwu.ai返回的响应中没有choices字段或choices为空")
                try:
                    import json
                    print(f"📄 完整响应内容: {json.dumps(result, ensure_ascii=False, indent=2)[:1000]}")
                except:
                    print(f"📄 完整响应内容: {str(result)[:1000]}")
                return None
            
            message = choices[0].get("message", {})
            print(f"   - message类型: {type(message)}")
            print(f"   - message键: {list(message.keys()) if isinstance(message, dict) else 'N/A'}")
            
            # 🔍 检查 choices[0] 中的 finish_reason 字段
            if "finish_reason" in choices[0]:
                finish_reason = choices[0]["finish_reason"]
                print(f"🔍 finish_reason: {finish_reason}")
                if finish_reason and finish_reason != "stop":
                    print(f"⚠️ finish_reason 不是 'stop'，可能是 '{finish_reason}'")
                    if finish_reason == "length":
                        print(f"💡 可能原因：输出被截断（max_tokens 限制）")
                    elif finish_reason == "content_filter":
                        print(f"💡 可能原因：内容被过滤")
                    elif finish_reason == "function_call":
                        print(f"💡 可能原因：触发了函数调用")
            
            if not message:
                print(f"⚠️ yunwu.ai返回的choices[0]中没有message字段")
                print(f"📄 choices[0]内容: {json.dumps(choices[0], ensure_ascii=False, indent=2)[:1000]}")
                return None
            
            content = message.get("content", "")
            print(f"   - content类型: {type(content)}")
            print(f"   - content长度: {len(content) if content else 0}")
            print(f"   - content前100字符: {str(content)[:100] if content else '(空)'}")
            
            # 🔍 详细调试：如果content很短，打印完整内容（包括不可见字符）
            if content and len(content) < 100:
                print(f"🔍 content完整内容（repr格式，显示所有字符）: {repr(content)}")
                print(f"🔍 content完整内容（原始格式）: {content}")
            
            # 🔍 检查message中的所有字段（可能有其他字段包含图片数据）
            print(f"🔍 检查message中的所有字段...")
            if isinstance(message, dict):
                for key, value in message.items():
                    if key == "content":
                        continue  # content已经处理过了
                    value_type = type(value).__name__
                    if isinstance(value, str):
                        value_preview = value[:200] if len(value) > 200 else value
                        print(f"   - message['{key}']: {value_type}, 长度={len(value)}, 内容={repr(value_preview)}")
                        # 如果这个字段看起来像图片数据，尝试提取
                        if value.startswith("data:image") or value.startswith("http://") or value.startswith("https://"):
                            print(f"💡 在message['{key}']中发现可能的图片数据！")
                            if value.startswith("data:image"):
                                saved_path = save_base64_image(value, prompt)
                                if saved_path:
                                    return saved_path
                            elif value.startswith("http://") or value.startswith("https://"):
                                return value
                    elif isinstance(value, (dict, list)):
                        print(f"   - message['{key}']: {value_type}, 内容={str(value)[:200]}")
                        # 递归检查嵌套结构
                        if isinstance(value, dict):
                            for sub_key, sub_value in value.items():
                                if isinstance(sub_value, str) and (sub_value.startswith("data:image") or sub_value.startswith("http")):
                                    print(f"💡 在message['{key}']['{sub_key}']中发现可能的图片数据！")
                                    if sub_value.startswith("data:image"):
                                        saved_path = save_base64_image(sub_value, prompt)
                                        if saved_path:
                                            return saved_path
                                    elif sub_value.startswith("http://") or sub_value.startswith("https://"):
                                        return sub_value
                    else:
                        print(f"   - message['{key}']: {value_type} = {value}")
            
            # 🔍 检查choices[0]中的所有字段（可能有其他字段包含图片数据）
            print(f"🔍 检查choices[0]中的所有字段...")
            if isinstance(choices[0], dict):
                for key, value in choices[0].items():
                    if key in ["index", "message", "finish_reason"]:
                        continue  # 这些字段已经处理过了
                    value_type = type(value).__name__
                    if isinstance(value, str):
                        value_preview = value[:200] if len(value) > 200 else value
                        print(f"   - choices[0]['{key}']: {value_type}, 长度={len(value)}, 内容={repr(value_preview)}")
                        if value.startswith("data:image") or value.startswith("http://") or value.startswith("https://"):
                            print(f"💡 在choices[0]['{key}']中发现可能的图片数据！")
                            if value.startswith("data:image"):
                                saved_path = save_base64_image(value, prompt)
                                if saved_path:
                                    return saved_path
                            elif value.startswith("http://") or value.startswith("https://"):
                                return value
                    else:
                        print(f"   - choices[0]['{key}']: {value_type} = {str(value)[:200]}")
            
            # 兼容模型把结果包在代码块/引号里（尤其是 data:image/... 或 JSON）
            content_clean = (content or "").strip()
            
            # 记录原始内容用于调试
            original_content = content_clean
            
            if not content_clean:
                print(f"⚠️ yunwu.ai返回的content字段为空")
                try:
                    import json
                    print(f"📄 完整message内容: {json.dumps(message, ensure_ascii=False, indent=2)[:1000]}")
                    print(f"📄 完整choices[0]内容: {json.dumps(choices[0], ensure_ascii=False, indent=2)[:1000]}")
                except:
                    print(f"📄 完整message内容: {str(message)[:1000]}")
                    print(f"📄 完整choices[0]内容: {str(choices[0])[:1000]}")
                # 检查是否有其他字段包含图片数据
                if isinstance(message, dict):
                    for key, value in message.items():
                        if key != "content" and isinstance(value, str) and len(value) > 50:
                            print(f"💡 发现message中的其他字段 '{key}'，长度: {len(value)}，前100字符: {value[:100]}")
                # 检查是否有 finish_reason 字段，可能说明为什么没有内容
                if isinstance(message, dict) and "finish_reason" in message:
                    finish_reason = message.get("finish_reason")
                    print(f"💡 finish_reason: {finish_reason}")
                    if finish_reason and finish_reason != "stop":
                        print(f"⚠️ 注意：finish_reason 不是 'stop'，可能是 '{finish_reason}'，这可能导致内容为空")
                return None
            
            # 保守地去除引号和代码块，避免误删有效内容
            # 先记录去除前的状态
            before_cleaning = content_clean
            print(f"🔍 开始清理content，原始长度: {len(content_clean)} 字符")
            if len(content_clean) <= 200:
                print(f"🔍 原始content内容: {repr(content_clean)}")
            
            # 策略1：先去掉最外层引号（但要确保去除后还有内容）
            for i in range(2):
                if len(content_clean) >= 2:
                    if (content_clean.startswith('"') and content_clean.endswith('"')) or (content_clean.startswith("'") and content_clean.endswith("'")):
                        # 检查去除引号后是否还有内容（至少1个字符）
                        temp_clean = content_clean[1:-1].strip()
                        if len(temp_clean) > 0:  # 只有去除后还有内容才执行
                            print(f"🔍 步骤{i+1}: 去除引号，长度从 {len(content_clean)} 变为 {len(temp_clean)}")
                            content_clean = temp_clean
                        else:
                            # 去除后为空，说明可能是空引号，保留原内容
                            print(f"🔍 步骤{i+1}: 去除引号后为空，保留原内容")
                            break
            
            # 策略2：剥离 ``` fenced code block（但要确保去除后还有内容）
            if content_clean.startswith("```"):
                print(f"🔍 检测到代码块标记，开始提取内容...")
                fence_match = re.match(r"^```(?:[a-zA-Z0-9_-]+)?\s*([\s\S]*?)\s*```$", content_clean, re.DOTALL)
                if fence_match:
                    extracted = (fence_match.group(1) or "").strip()
                    if len(extracted) > 0:  # 只有提取到内容才使用
                        print(f"🔍 从代码块中提取内容，长度从 {len(content_clean)} 变为 {len(extracted)}")
                        content_clean = extracted
                    else:
                        # 如果提取为空，说明代码块是空的，保留原内容
                        print(f"🔍 代码块提取后为空，保留原内容")
                else:
                    # 退化处理：按行移除首尾 fence（但要确保去除后还有内容）
                    lines = content_clean.splitlines()
                    if len(lines) >= 2 and lines[0].strip().startswith("```"):
                        if lines[-1].strip().startswith("```"):
                            # 移除首尾两行
                            remaining_lines = lines[1:-1]
                            temp_clean = "\n".join(remaining_lines).strip()
                            if len(temp_clean) > 0:  # 只有去除后还有内容才使用
                                print(f"🔍 按行移除代码块标记，长度从 {len(content_clean)} 变为 {len(temp_clean)}")
                                content_clean = temp_clean
                            else:
                                print(f"🔍 按行移除代码块标记后为空，保留原内容")
                        else:
                            # 只移除第一行
                            remaining_lines = lines[1:]
                            temp_clean = "\n".join(remaining_lines).strip()
                            if len(temp_clean) > 0:  # 只有去除后还有内容才使用
                                print(f"🔍 移除第一行代码块标记，长度从 {len(content_clean)} 变为 {len(temp_clean)}")
                                content_clean = temp_clean
                            else:
                                print(f"🔍 移除第一行代码块标记后为空，保留原内容")
            
            # 策略3：fence 解包后再做一次引号去除（但要确保去除后还有内容）
            for i in range(2):
                if len(content_clean) >= 2:
                    if (content_clean.startswith('"') and content_clean.endswith('"')) or (content_clean.startswith("'") and content_clean.endswith("'")):
                        temp_clean = content_clean[1:-1].strip()
                        if len(temp_clean) > 0:  # 只有去除后还有内容才执行
                            print(f"🔍 代码块解包后再次去除引号，长度从 {len(content_clean)} 变为 {len(temp_clean)}")
                            content_clean = temp_clean
                        else:
                            print(f"🔍 代码块解包后去除引号为空，停止处理")
                            break
            
            print(f"🔍 清理完成，最终长度: {len(content_clean)} 字符")
            
            # 检查去除引号和代码块后是否变成空字符串
            if not content_clean:
                print(f"⚠️ yunwu.ai返回的content字段在去除引号/代码块后为空")
                print(f"📄 原始content内容: {repr(original_content[:200])}")
                print(f"📄 原始content长度: {len(original_content)} 字符")
                
                # 检查是否是空的代码块（说明API没有生成图片）
                # 使用正则表达式匹配各种形式的空代码块
                empty_code_block_pattern = re.match(r'^```(?:\w+)?\s*\n?\s*```$', original_content.strip(), re.MULTILINE)
                is_empty_code_block = (
                    empty_code_block_pattern is not None or
                    original_content.strip() in ["```", "```\n```", "```\n\n```", "```json\n```", "```json\n\n```"] or
                    (original_content.strip().startswith("```") and 
                     original_content.strip().endswith("```") and 
                     len(original_content.strip().replace("```", "").strip()) == 0)
                )
                
                if is_empty_code_block:
                    print(f"⚠️ 检测到空的代码块，说明yunwu.ai API没有生成图片数据")
                    print(f"💡 可能的原因：")
                    print(f"   1. gemini-2.5-flash-image 模型可能不支持图片生成，或需要不同的调用方式")
                    print(f"   2. API密钥权限不足，无法使用图片生成功能")
                    print(f"   3. 提示词格式不符合模型要求")
                    print(f"   4. 模型可能返回了错误信息，但被包装在空代码块中")
                    
                    # 检查finish_reason字段
                    if isinstance(message, dict) and "finish_reason" in message:
                        finish_reason = message.get("finish_reason")
                        print(f"💡 finish_reason: {finish_reason}")
                        if finish_reason and finish_reason != "stop":
                            print(f"⚠️ finish_reason 不是 'stop'，可能是 '{finish_reason}'，这可能导致内容为空")
                    
                    # 检查choices[0]中是否有其他字段包含图片数据
                    print(f"🔍 检查choices[0]中的其他字段...")
                    if isinstance(choices[0], dict):
                        for key, value in choices[0].items():
                            if key not in ["index", "message", "finish_reason"]:
                                print(f"   - {key}: {type(value)} = {str(value)[:100] if isinstance(value, str) else value}")
                    
                    # 检查message中是否有其他字段包含图片数据
                    print(f"🔍 检查message中的其他字段...")
                    if isinstance(message, dict):
                        for key, value in message.items():
                            if key not in ["role", "content"]:
                                print(f"   - {key}: {type(value)} = {str(value)[:100] if isinstance(value, str) else value}")
                                # 如果找到可能的图片URL或base64数据
                                if isinstance(value, str) and (value.startswith("http") or value.startswith("data:image")):
                                    print(f"💡 在message['{key}']中发现可能的图片数据！")
                                    return value
                    
                    # 检查完整响应中是否有其他字段包含图片数据
                    print(f"🔍 检查完整响应中的其他字段...")
                    for key in ["data", "image", "image_url", "url", "images"]:
                        if key in result:
                            value = result[key]
                            print(f"   - {key}: {type(value)} = {str(value)[:200] if isinstance(value, str) else value}")
                            if isinstance(value, str) and (value.startswith("http") or value.startswith("data:image")):
                                print(f"💡 在result['{key}']中发现可能的图片数据！")
                                return value
                    
                    print(f"💡 建议：")
                    print(f"   - 检查.env文件中的yunwu_model配置，尝试切换到其他模型（如 sora_image）")
                    print(f"   - 检查yunwu.ai API文档，确认gemini-2.5-flash-image模型是否支持图片生成")
                    print(f"   - 如果API不支持图片生成，可以切换到其他图片生成服务")
                    return None
                
                print(f"💡 可能的原因：")
                print(f"   1. API返回的内容被错误地包装在引号或代码块中，去除后内容丢失")
                print(f"   2. API返回的content字段本身就是空字符串或只包含空白字符")
                print(f"   3. 代码块解析逻辑可能过于激进，误删了有效内容")
                print(f"💡 建议：")
                print(f"   - 检查原始content内容（见上方日志）")
                print(f"   - 如果原始content不为空，可能需要调整引号/代码块去除逻辑")
                print(f"   - 检查yunwu.ai API返回的完整响应结构")
                # 如果原始内容不为空，尝试直接使用原始内容（可能包含有效的图片数据）
                if original_content and len(original_content) > 10:
                    print(f"💡 尝试直接使用原始content内容进行解析...")
                    content_clean = original_content
                else:
                    return None
            
            print(f"🔍 yunwu.ai返回的原始内容（前500字符）：{content_clean[:500]}")
            if len(content_clean) > 500:
                print(f"🔍 yunwu.ai返回的原始内容（完整长度：{len(content_clean)}字符）")
            
            # 解析策略1：尝试解析JSON格式
            try:
                import json
                content_json = json.loads(content_clean)
                if "image_url" in content_json:
                    print(f"✅ 从JSON中提取到image_url：{content_json['image_url']}")
                    return content_json["image_url"]
                elif "url" in content_json:
                    print(f"✅ 从JSON中提取到url：{content_json['url']}")
                    return content_json["url"]
            except json.JSONDecodeError:
                pass  # 不是JSON格式，继续其他解析方式
            
            # 解析策略2：从markdown格式中提取图片URL或base64数据
            # 匹配格式：![image](https://...) 或 ![alt text](url) 或 ![image](data:image/...)
            # 改进正则：支持HTTP/HTTPS URL和data URI，base64数据可能很长，需要匹配到最后的右括号
            # 对于base64，匹配所有非右括号的字符（包括换行符等），直到遇到右括号
            markdown_image_pattern = r'!\[.*?\]\((https?://[^\s\)]+|data:image/[^\)]+)\)'
            markdown_matches = re.findall(markdown_image_pattern, content_clean, re.DOTALL)
            if markdown_matches:
                image_data = markdown_matches[0]  # 取第一个匹配的内容
                
                # 检查是否是base64 data URI
                if image_data.startswith("data:image"):
                    print(f"✅ 从markdown格式中提取到base64图片数据（长度：{len(image_data)}字符）")
                    # 处理base64图片
                    saved_path = save_base64_image(image_data, prompt)
                    if saved_path:
                        return saved_path
                    else:
                        print(f"⚠️ base64图片保存失败")
                else:
                    # 是HTTP/HTTPS URL
                    image_url = image_data
                    # 验证URL是否完整（至少包含协议、域名和路径）
                    if validate_image_url(image_url):
                        print(f"✅ 从markdown格式中提取到图片URL：{image_url}")
                        return image_url
                    else:
                        print(f"⚠️ 提取的URL格式不完整，尝试修复：{image_url}")
                        # 尝试修复不完整的URL
                        fixed_url = fix_incomplete_url(image_url)
                        if fixed_url and validate_image_url(fixed_url):
                            print(f"✅ URL修复成功：{fixed_url}")
                            return fixed_url
                        else:
                            print(f"❌ URL修复失败，跳过此URL")
            
            # 解析策略3：直接查找HTTP/HTTPS URL
            # 改进正则：更精确地匹配完整URL
            url_pattern = r'https?://[^\s\)\]\<\>"]+'
            url_matches = re.findall(url_pattern, content_clean)
            if url_matches:
                # 过滤掉明显不是图片的URL（如API端点）
                for url in url_matches:
                    # 验证URL完整性
                    if not validate_image_url(url):
                        continue
                    # 优先选择包含图片相关关键词的URL
                    if any(keyword in url.lower() for keyword in ['image', 'img', 'photo', 'picture', 'oss', 'cdn', 'aliyuncs', 'jpg', 'jpeg', 'png', 'webp']):
                        print(f"✅ 从文本中提取到图片URL：{url}")
                        return url
                # 如果没有找到明显的图片URL，验证第一个URL后返回
                if url_matches:
                    first_url = url_matches[0]
                    if validate_image_url(first_url):
                        print(f"✅ 从文本中提取到URL：{first_url}")
                        return first_url
                    else:
                        print(f"⚠️ 提取的URL格式不完整：{first_url}")
            
            # 解析策略4：检查是否是直接的URL
            if content_clean.startswith("http://") or content_clean.startswith("https://"):
                if validate_image_url(content_clean):
                    print(f"✅ 内容本身就是URL：{content_clean}")
                    return content_clean
                else:
                    print(f"⚠️ 内容看起来像URL但格式不完整：{content_clean}")
                    fixed = fix_incomplete_url(content_clean)
                    if fixed:
                        return fixed
            
            # 解析策略5：检查是否是base64编码的图片（直接格式，非markdown / 非JSON / 非markdown图片）
            # 兼容前后空白、代码块包装等情况（已在 content_clean 中处理）
            if content_clean.startswith("data:image"):
                print(f"✅ 检测到base64图片数据（直接格式）")
                # 处理base64图片
                saved_path = save_base64_image(content_clean, prompt)
                if saved_path:
                    return saved_path
                else:
                    print(f"⚠️ base64图片保存失败")
            
            # 解析策略6：尝试从文本中提取base64 data URI（非markdown格式）
            # 允许base64内容换行/包含空白，使用非贪婪匹配但确保匹配完整
            # 改进：匹配完整的data URI，包括可能很长的base64数据
            base64_pattern = r'data:image/[^;]+;base64,[A-Za-z0-9+/=\s\n\r]+'
            base64_matches = re.findall(base64_pattern, content_clean, re.DOTALL)
            if base64_matches:
                # 选择最长的匹配（通常是完整的base64数据）
                longest_match = max(base64_matches, key=len)
                print(f"✅ 从文本中提取到base64图片数据（长度：{len(longest_match)}字符）")
                # 处理base64图片
                saved_path = save_base64_image(longest_match, prompt)
                if saved_path:
                    return saved_path
                else:
                    print(f"⚠️ base64图片保存失败")
            
            # 如果所有解析方式都失败，打印详细内容用于调试
            print(f"⚠️ yunwu.ai返回格式无法解析")
            # 如果内容太长（可能是base64数据），只打印前1000字符和后100字符
            if len(content_clean) > 2000:
                print(f"📄 原始内容（前1000字符）：{content_clean[:1000]}")
                print(f"📄 原始内容（后100字符）：{content_clean[-100:]}")
                print(f"📊 内容长度：{len(content_clean)} 字符（已截断显示）")
            else:
                print(f"📄 原始内容（完整）：{content_clean}")
                print(f"📊 内容长度：{len(content_clean)} 字符")
            print(f"📊 内容类型检查：")
            print(f"   - 包含 'http': {'http' in content_clean.lower()}")
            print(f"   - 包含 'data:image': {'data:image' in content_clean.lower()}")
            print(f"   - 包含 'base64': {'base64' in content_clean.lower()}")
            print(f"   - 包含 'url': {'url' in content_clean.lower()}")
            print(f"   - 包含 'image': {'image' in content_clean.lower()}")
            print(f"   - 以'data:image'开头: {content_clean.startswith('data:image')}")
            
            # 检查返回内容是否是文本描述（而非图片数据）
            if len(content_clean) > 100 and not any(keyword in content_clean.lower() for keyword in ['http', 'data:image', 'base64', 'url', 'image']):
                print(f"💡 提示：yunwu.ai返回的是文本描述而非图片数据，可能是API生成失败或返回格式异常")
                print(f"💡 可能的原因：")
                print(f"   1. yunwu.ai API模型配置不正确（当前模型：{model}）")
                print(f"   2. gemini-2.5-flash-image 模型可能不支持图片生成，或返回格式不同")
                print(f"   3. API返回格式不符合预期，需要检查yunwu.ai API文档")
                print(f"   4. API密钥权限不足或配置错误")
                print(f"   5. 提示词格式不符合模型要求")
                print(f"💡 建议：")
                print(f"   - 检查.env文件中的yunwu_api_key和yunwu_model配置")
                print(f"   - 尝试切换到其他支持图片生成的模型（如 sora_image）")
                print(f"   - 确认yunwu.ai API是否支持图片生成功能")
                print(f"   - 查看yunwu.ai API文档确认正确的调用方式")
                print(f"   - 如果API不支持图片生成，可以切换到其他图片生成服务（如ComfyUI、Replicate、Stable Diffusion等）")
            else:
                print(f"💡 提示：返回内容包含图片相关关键词，但解析失败")
                print(f"💡 可能的原因：")
                print(f"   1. 返回格式不在预期的解析策略中")
                print(f"   2. URL或base64数据格式不完整")
                print(f"   3. 需要添加新的解析策略")
            return None
                
        except requests.exceptions.Timeout as e:
            # 超时错误：图片生成可能需要更长时间，重试
            print(f"⚠️ yunwu.ai图片生成API请求超时（尝试 {attempt + 1}/{max_retries}，超时时间：{request_timeout}秒）")
            print(f"   图片生成通常需要较长时间，可能是API响应慢或网络问题")
            print(f"   💡 提示：如果经常超时，可以增加 YUNWU_IMAGE_TIMEOUT_SECONDS 环境变量（当前：{request_timeout}秒）")
            if attempt < max_retries - 1:
                # 超时后等待更长时间再重试
                wait_time = 10 * (attempt + 1)  # 10s, 20s, 30s
                print(f"   等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
                continue
            else:
                # 最后一次尝试也超时，抛出异常
                print(f"❌ 达到最大重试次数（{max_retries}），图片生成超时")
                print(f"   💡 建议：增加 YUNWU_IMAGE_TIMEOUT_SECONDS 环境变量到更大的值（例如：300秒）")
                raise
        except requests.exceptions.HTTPError as e:
            # 429错误已经在上面处理，这里处理其他HTTP错误
            if e.response and e.response.status_code == 429:
                # 如果429错误没有被上面的逻辑处理（理论上不应该发生），抛出异常
                raise
            else:
                # 其他HTTP错误直接抛出
                print(f"❌ yunwu.ai图片生成API调用失败（HTTP错误）：{str(e)}")
                raise
        except Exception as e:
            # 其他错误（如网络错误等）
            error_msg = str(e)
            if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
                # 超时错误，重试
                print(f"⚠️ yunwu.ai图片生成API请求超时（尝试 {attempt + 1}/{max_retries}）")
                if attempt < max_retries - 1:
                    wait_time = 10 * (attempt + 1)
                    print(f"   等待 {wait_time} 秒后重试...")
                    time.sleep(wait_time)
                    continue
            # 其他错误直接抛出
            print(f"❌ yunwu.ai图片生成API调用失败：{error_msg}")
            raise

def call_comfyui_api(prompt: str, style: str) -> str:
    """调用ComfyUI API生成图片"""
    try:
        comfyui_host = IMAGE_GENERATION_CONFIG.get("comfyui_host", "")
        if not comfyui_host:
            raise ValueError("ComfyUI Host未配置")
        
        # ComfyUI API调用需要先提交任务，然后轮询结果
        # 这里提供基础框架，需要根据实际ComfyUI API调整
        print(f"⚠️ ComfyUI API调用需要根据实际API文档实现")
        return None
    except Exception as e:
        print(f"❌ ComfyUI API调用失败：{str(e)}")
        raise

def call_replicate_api(prompt: str, style: str) -> str:
    """调用Replicate API生成图片"""
    try:
        # import replicate
        replicate_client = replicate.Client(api_token=IMAGE_GENERATION_CONFIG.get("replicate_api_token"))
        
        # 使用Stable Diffusion模型
        output = replicate_client.run(
            "stability-ai/stable-diffusion:db21e45d3f7023abc2a46ee38a23973f6dce16bb082a930b0c49861f96d1e5bf",
            input={
                "prompt": prompt,
                "width": 1024,
                "height": 1024,
                "num_outputs": 1
            }
        )
        
        # Replicate返回的是列表
        if isinstance(output, list) and len(output) > 0:
            return output[0]
        elif isinstance(output, str):
            return output
        else:
            print(f"⚠️ Replicate返回格式异常：{output}")
            return None
    except Exception as e:
        print(f"❌ Replicate API调用失败：{str(e)}")
        raise

def call_dalle_api(prompt: str) -> str:
    """调用DALL-E API生成图片"""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=IMAGE_GENERATION_CONFIG.get("openai_api_key"))
        
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt[:1000],  # DALL-E 3限制提示词长度
            size="1024x1024",
            quality="standard",
            n=1,
        )
        
        return response.data[0].url
    except Exception as e:
        print(f"❌ DALL-E API调用失败：{str(e)}")
        raise

def call_stable_diffusion_api(prompt: str, style: str, reference_image_url: str = "") -> str:
    """调用本地Stable Diffusion API生成图片（支持img2img参考图）"""
    try:
        import base64
        from pathlib import Path

        base_url = IMAGE_GENERATION_CONFIG.get("stable_diffusion_base_url", "http://localhost:7860")
        api_key = IMAGE_GENERATION_CONFIG.get("stable_diffusion_api_key", "")

        headers = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        def _load_ref_image_b64(ref: str) -> str:
            """把参考图读成 base64（不带 data:image 前缀），失败返回空串。"""
            if not ref or not isinstance(ref, str):
                return ""
            ref = ref.strip()
            if not ref:
                return ""

            # data URL
            if ref.startswith("data:image"):
                try:
                    b64_part = ref.split("base64,", 1)[1]
                    b64_part = re.sub(r"\s+", "", b64_part)
                    base64.b64decode(b64_part, validate=False)
                    return b64_part
                except Exception:
                    return ""

            # 本地缓存路径（前端常传 /image_cache/...）
            if ref.startswith("/image_cache/") or ref.startswith("image_cache/"):
                rel = ref[1:] if ref.startswith("/") else ref
                # 以项目目录为基准，避免工作目录变化导致找不到文件
                base_dir = Path(__file__).resolve().parent
                local_path = (base_dir / rel).resolve()
                if local_path.exists():
                    data = local_path.read_bytes()
                    return base64.b64encode(data).decode("utf-8")
                return ""

            # HTTP/HTTPS
            if ref.startswith("http://") or ref.startswith("https://"):
                try:
                    r = requests.get(ref, timeout=30)
                    r.raise_for_status()
                    return base64.b64encode(r.content).decode("utf-8")
                except Exception:
                    return ""

            return ""

        # 参数：可通过环境变量调节（给“同一场景统一风格/物件”留调参口）
        denoising_strength = float(os.getenv("STABLE_DIFFUSION_DENOISING_STRENGTH", "0.55"))
        steps = int(os.getenv("STABLE_DIFFUSION_STEPS", "20"))
        cfg_scale = float(os.getenv("STABLE_DIFFUSION_CFG_SCALE", "7"))

        ref_b64 = _load_ref_image_b64(reference_image_url)
        if ref_b64:
            # img2img：参考上一剧情图片，保持人物/物件一致性更强
            response = requests.post(
                f"{base_url}/sdapi/v1/img2img",
                headers=headers,
                json={
                    "init_images": [ref_b64],
                    "prompt": prompt,
                    "denoising_strength": max(0.0, min(1.0, denoising_strength)),
                    "width": 1024,
                    "height": 1024,
                    "steps": steps,
                    "cfg_scale": cfg_scale
                },
                timeout=180
            )
        else:
            # txt2img
            response = requests.post(
                f"{base_url}/sdapi/v1/txt2img",
                headers=headers,
                json={
                    "prompt": prompt,
                    "width": 1024,
                    "height": 1024,
                    "steps": steps,
                    "cfg_scale": cfg_scale
                },
                timeout=180
            )

        response.raise_for_status()
        result = response.json()

        if "images" in result and isinstance(result["images"], list) and len(result["images"]) > 0:
            b64 = result["images"][0]
            if isinstance(b64, str) and b64.strip():
                # SD WebUI 返回的是纯base64，这里转为 data URI 保存到本地缓存
                data_uri = f"data:image/png;base64,{b64.strip()}"
                saved_path = save_base64_image(data_uri, prompt)
                return saved_path
        return None
    except Exception as e:
        print(f"❌ Stable Diffusion API调用失败：{str(e)}")
        raise

# ==================== 视频生成功能已禁用（性能优化） ====================
# 视频生成任务存储（用于状态查询）
# video_tasks = {}
# video_tasks_lock = threading.Lock()

# def generate_scene_video(
#     scene_description: str,
#     image_url: str = None,
#     duration: int = None
# ) -> Dict:
#     """
#     生成场景视频片段（5-10秒）
#     :param scene_description: 场景描述
#     :param image_url: 基于图片生成视频（推荐，质量更好）
#     :param duration: 视频时长（5-10秒）
#     :return: 包含任务ID和状态的字典
#     """
#     # 检查是否配置了视频生成API
#     provider = VIDEO_GENERATION_CONFIG.get("provider", "yunwu")
#     
#     if provider == "yunwu" and not VIDEO_GENERATION_CONFIG.get("yunwu_api_key"):
#         print("⚠️ yunwu.ai API Key未配置，跳过视频生成")
#         return None
#     elif provider == "runway" and not VIDEO_GENERATION_CONFIG.get("runway_api_key"):
#         print("⚠️ Runway API Key未配置，跳过视频生成")
#         return None
#     elif provider == "pika" and not VIDEO_GENERATION_CONFIG.get("pika_api_key"):
#         print("⚠️ Pika API Key未配置，跳过视频生成")
#         return None
#     
#     # 限制视频时长为5-10秒
#     min_duration = VIDEO_GENERATION_CONFIG.get("min_duration", 5)
#     max_duration = VIDEO_GENERATION_CONFIG.get("max_duration", 10)
#     
#     if duration is None:
#         duration = random.randint(min_duration, max_duration)
#     else:
#         duration = max(min_duration, min(max_duration, duration))
#     
#     # 生成任务ID
#     task_id = str(uuid.uuid4())
#     
#     # 启动后台任务
#     thread = threading.Thread(
#         target=async_generate_video_task,
#         args=(task_id, scene_description, image_url, duration, provider),
#         daemon=True
#     )
#     thread.start()
#     
#     return {
#         "task_id": task_id,
#         "status": "processing",
#         "duration": duration,
#         "estimated_time": 60  # 预计生成时间（秒）
#     }

# def async_generate_video_task(
#     task_id: str,
#     scene_description: str,
#     image_url: str,
#     duration: int,
#     provider: str
# ):
#     """异步生成视频任务"""
#     try:
#         if provider == "yunwu":
#             video_url = call_yunwu_video_api(scene_description, image_url, duration)
#         elif provider == "runway":
#             video_url = call_runway_gen2_api(scene_description, image_url, duration)
#         elif provider == "pika":
#             video_url = call_pika_api(scene_description, image_url, duration)
#         else:
#             print(f"⚠️ 不支持的视频生成服务：{provider}")
#             with video_tasks_lock:
#                 video_tasks[task_id] = {
#                     "status": "failed",
#                     "error": f"不支持的视频生成服务：{provider}"
#                 }
#             return
#         
#         # 更新任务状态
#         with video_tasks_lock:
#             video_tasks[task_id] = {
#                 "status": "completed",
#                 "url": video_url,
#                 "duration": duration
#             }
#         print(f"✅ 视频生成完成，任务ID：{task_id}")
#     except Exception as e:
#         print(f"❌ 视频生成失败，任务ID：{task_id}，错误：{str(e)}")
#         import traceback
#         traceback.print_exc()
#         with video_tasks_lock:
#             video_tasks[task_id] = {
#                 "status": "failed",
#                 "error": str(e)
#             }

# # ==================== 以下视频生成函数已禁用（性能优化） ====================
# def call_yunwu_video_api(prompt: str, image_url: str = None, duration: int = 5) -> str:
#     """调用yunwu.ai视频生成API（使用sora模型）"""
#     ... (已注释)

# def call_runway_gen2_api(prompt: str, image_url: str = None, duration: int = 5) -> str:
#     """调用Runway Gen-2 API生成视频"""
#     ... (已注释)

# def call_pika_api(prompt: str, image_url: str = None, duration: int = 5) -> str:
#     """调用Pika Labs API生成视频"""
#     ... (已注释)

# def get_video_task_status(task_id: str) -> Dict:
#     """获取视频生成任务状态"""
#     with video_tasks_lock:
#         return video_tasks.get(task_id, None)