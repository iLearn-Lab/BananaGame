#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
单独测试 yunwu.ai 上的 gemini-2.5-flash-image-preview 图生图模型。

用法示例（在 DN-main 根目录下）：

    python scripts/test_gemini_image.py --prompt "a boy standing in a mysterious forest, anime style"

环境依赖：
- .env / 环境变量中已配置：
    Image_Generation_API_KEY
    Image_Generation_BASE_URL (默认 https://yunwu.ai/v1)
- 本项目的 src 目录可被 Python 识别为包（在仓库根目录执行命令即可）。
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

import requests

# 确保可以导入项目内的 src.*
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import IMAGE_GENERATION_CONFIG
from src.image.storage import save_base64_image


def _extract_image_from_response(obj: dict) -> Optional[str]:
    """
    从 yunwu /chat/completions 风格响应里尽量抽取图片 URL 或 base64 data URI。
    逻辑与现有 call_yunwu_image_api / call_gemini_img2img 保持一致风格。
    """
    if not isinstance(obj, dict):
        return None

    # 顶层直接给 url
    for k in ("image_url", "url"):
        v = obj.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()

    # choices[0].message.content
    choices = obj.get("choices", [])
    if choices:
        message = choices[0].get("message", {}) or {}
        content = message.get("content", "")
        if isinstance(content, str) and content.strip():
            content_str = content.strip()
            # 直接是 data:image... 或 http(s)...
            if content_str.startswith("data:image") or content_str.startswith("http"):
                return content_str
            # 从文本中抓 data:image/...;base64,...
            import re

            m = re.search(r"data:image/[^;]+;base64,[A-Za-z0-9+/=\s]+", content_str)
            if m:
                return m.group(0).strip()

    return None


def run_test(prompt: str) -> None:
    api_key = IMAGE_GENERATION_CONFIG.get("yunwu_api_key")
    base_url = IMAGE_GENERATION_CONFIG.get("yunwu_base_url", "https://yunwu.ai/v1")

    # 只在这个测试脚本里强制使用 gemini-2.5-flash-image-preview，不影响主流程配置
    model = "gemini-2.5-flash-image-preview"

    if not api_key:
        print("❌ Image_Generation_API_KEY 未配置，无法调用 yunwu.ai")
        return

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # 尽量与现有 call_yunwu_image_api 的 Gemini 分支保持一致的提示格式
    user_content = (
        "Generate an image based on this description: "
        f"{prompt}\n\n"
        "Return ONLY the generated image as base64 data "
        "(data:image/png;base64,...) or image URL (https://...). "
        "Do NOT include any text, code blocks, or explanations."
    )

    request_body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": user_content,
            }
        ],
        "temperature": 0.1,
        "max_tokens": 4000,
    }

    timeout = int(os.getenv("YUNWU_IMAGE_TIMEOUT_SECONDS", "180"))

    print("🔍 准备调用 gemini-2.5-flash-image-preview 文生图接口：")
    print(f"   base_url: {base_url}")
    print(f"   model   : {model}")
    print(f"   timeout : {timeout}s")
    print(f"   prompt  : {prompt[:120]}{'...' if len(prompt) > 120 else ''}")

    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=request_body,
            timeout=timeout,
        )
    except Exception as e:
        print(f"❌ 请求发送失败：{e}")
        return

    print(f"🔁 HTTP 状态码: {resp.status_code}")
    if resp.status_code != 200:
        msg = ""
        try:
            body = resp.json()
            if isinstance(body, dict):
                err = body.get("error") or {}
                if isinstance(err, dict):
                    msg = err.get("message", "") or str(err)
                else:
                    msg = str(err)
            else:
                msg = str(body)
        except Exception:
            msg = resp.text[:300]
        print(f"❌ 接口返回错误: {msg}")
        return

    try:
        result = resp.json()
    except Exception as e:
        print(f"❌ 响应 JSON 解析失败：{e}")
        print("📥 原始响应前500字：")
        print(resp.text[:500])
        return

    img = _extract_image_from_response(result)
    if not img:
        print("⚠️ 响应中未找到图片数据（既不是 URL 也不是 base64 data URI）")
        print("📥 响应前800字：")
        print(json.dumps(result, ensure_ascii=False)[:800])
        return

    # 如果是 base64 data URI，落盘；如果是 URL，只打印
    if img.startswith("data:image"):
        saved = save_base64_image(img, prompt)
        if saved:
            print(f"✅ 生成图片已保存到：{saved}")
            print("   可在浏览器中通过本地服务访问，或直接打开对应文件。")
        else:
            print("⚠️ base64 落盘失败，但已成功拿到 data URI：")
            print(img[:200] + ("..." if len(img) > 200 else ""))
    else:
        print("✅ 成功获取图片 URL：")
        print(img)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="测试调用 yunwu.ai 的 gemini-2.5-flash-image-preview 文生图接口"
    )
    parser.add_argument(
        "--prompt",
        type=str,
        required=True,
        help="用来生成图片的英文/中文提示词",
    )
    args = parser.parse_args()
    run_test(args.prompt)


if __name__ == "__main__":
    main()

