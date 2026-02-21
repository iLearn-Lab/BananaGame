# -*- coding: utf-8 -*-
"""
初登场图归档时：用视觉模型在图中标出指定角色位置（bbox），并裁成单人全身参考图。
让后续生图明确「参考图里哪个人」是该配角，避免多人同框时用错脸。
"""
import base64
import json
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

from src.config import VISION_FOR_REF_CROP
from src.utils.text_utils import _safe_str, _clip_text


def _call_vision_bbox(
    image_path: Path,
    character_name: str,
    appearance_hints: str,
) -> Optional[Dict[str, float]]:
    """
    调用视觉模型：在图中找到指定角色，返回其 bounding box（归一化 0~1），尽量全身。
    通过「角色名 + 外观描述」让模型知道要找的是哪个人。
    :return: {"x": 0-1, "y": 0-1, "width": 0-1, "height": 0-1} 或 None
    """
    cfg = VISION_FOR_REF_CROP or {}
    model = (cfg.get("model") or "").strip()
    api_key = (cfg.get("api_key") or "").strip()
    if not model or not api_key:
        print(f"   📌 [vision] 跳过裁剪：未配置 VISION_REF_MODEL 或 VISION_REF_API_KEY（请在 .env 中配置并重启服务）")
        return None

    use_gemini_ep = cfg.get("use_gemini_endpoint") and (cfg.get("base_url") or "").strip() and "gemini" in (cfg.get("model") or "").lower()
    if use_gemini_ep:
        print(f"   🎯 [vision] 正在调用视觉模型（{model}，Gemini 原生接口）定位角色「{character_name}」…")
    else:
        print(f"   🎯 [vision] 正在调用视觉模型（{model}）定位角色「{character_name}」…")
    if not image_path.exists():
        print(f"⚠️ [vision] 图片不存在：{image_path}")
        return None

    # 上传前缩小图片并转 JPEG，减少请求体、加快上传与模型处理（bbox 为 0~1 比例，裁剪仍用原图）
    max_vision_side = int(cfg.get("max_image_side", 1024)) if isinstance(cfg.get("max_image_side"), (int, float)) else 1024
    try:
        from PIL import Image
        import io
        img = Image.open(image_path).convert("RGB")
        w, h = img.size
        if max(w, h) > max_vision_side:
            scale = max_vision_side / max(w, h)
            new_w, new_h = int(w * scale), int(h * scale)
            img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85, optimize=True)
        raw = buf.getvalue()
        b64 = base64.standard_b64encode(raw).decode("ascii")
        data_uri = f"data:image/jpeg;base64,{b64}"
    except Exception as e:
        try:
            with open(image_path, "rb") as f:
                raw = f.read()
            b64 = base64.standard_b64encode(raw).decode("ascii")
            mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
            data_uri = f"data:{mime};base64,{b64}"
        except Exception as e2:
            print(f"⚠️ [vision] 读取图片失败：{e2}")
            return None

    # 为兼容云雾等对回复长度严格限制的 API：优先让模型只输出 4 个数字（极短），否则再解析 JSON
    hints = _clip_text(_safe_str(appearance_hints), 300)
    user_content = f"""在这张图中找出名为「{character_name}」的角色，尽量框全身（从头到脚）。
{f'外观或位置参考：{hints}' if hints else ''}

坐标用 0～1 比例：左x 上y 宽width 高height。
只输出一行 4 个数字，用空格分隔，不要其他文字、不要 JSON。例如：0.2 0.3 0.25 0.6
若找不到该角色则输出：0 0 1 1"""

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_content},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ],
        }
    ]

    base_url = (cfg.get("base_url") or "").strip().rstrip("/")
    use_gemini_ep = cfg.get("use_gemini_endpoint") and base_url and "gemini" in model.lower()
    if use_gemini_ep:
        # 走 Gemini 原生 /v1beta/models/xxx:generateContent，回复长度可能更宽松
        base_host = base_url.rstrip("/").replace("/v1", "") or base_url
        url = f"{base_host}/v1beta/models/{model}:generateContent"
        b64_img = data_uri.split(",", 1)[1] if "," in data_uri else ""
        max_tok = cfg.get("max_output_tokens") if isinstance(cfg.get("max_output_tokens"), (int, float)) else 512
        body = {
            "contents": [{
                "parts": [
                    {"inlineData": {"mimeType": "image/jpeg", "data": b64_img}},
                    {"text": user_content},
                ]
            }],
            "generationConfig": {"maxOutputTokens": max(32, int(max_tok)), "temperature": 0.1},
        }
    else:
        url = f"{base_url}/chat/completions" if base_url else "https://api.openai.com/v1/chat/completions"
        max_tok = cfg.get("max_output_tokens") if isinstance(cfg.get("max_output_tokens"), (int, float)) else 512
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max(32, int(max_tok)),
            "maxOutputTokens": max(32, int(max_tok)),
            "temperature": 0.1,
        }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # 带图请求较慢，默认 120 秒，可通过 .env VISION_REF_TIMEOUT 调整；超时/503 时自动重试
    timeout_seconds = cfg.get("timeout") if isinstance(cfg.get("timeout"), (int, float)) else 120
    try:
        import requests
        import time
        last_err = None
        for attempt in range(3):
            try:
                r = requests.post(url, headers=headers, json=body, timeout=timeout_seconds)
                r.raise_for_status()
                data = r.json()
                if use_gemini_ep:
                    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts") or []
                    content = (parts[0].get("text", "") if parts else "").strip()
                else:
                    msg = (data.get("choices") or [{}])[0].get("message", {})
                    content = msg.get("content")
                    if isinstance(content, list):
                        content = "".join(
                            (p.get("text", "") if isinstance(p, dict) else str(p))
                            for p in content
                        )
                    content = (content or "").strip()
                break
            except requests.exceptions.HTTPError as e:
                last_err = e
                if e.response is not None and e.response.status_code == 503:
                    if attempt < 2:
                        wait = (attempt + 1) * 5
                        print(f"   ⚠️ [vision] 服务暂时不可用(503)，{wait}秒后重试（{attempt+1}/3）…")
                        time.sleep(wait)
                        continue
                raise
            except requests.exceptions.Timeout as e:
                last_err = e
                if attempt < 2:
                    wait = (attempt + 1) * 5
                    print(f"   ⚠️ [vision] 请求超时（{timeout_seconds}s），{wait}秒后重试（{attempt+1}/3）…")
                    time.sleep(wait)
                    continue
                raise
        else:
            if last_err:
                raise last_err
    except Exception as e:
        print(f"⚠️ [vision] 调用视觉模型失败：{e}")
        if hasattr(e, "response") and e.response is not None and getattr(e.response, "status_code", None) == 503:
            print(f"   💡 若持续 503，多为该 API 不支持「带图」请求。建议改用 OpenAI 直连：.env 中 VISION_REF_BASE_URL 留空，VISION_REF_API_KEY 填 OpenAI 的 key。")
        if "timed out" in str(e).lower() or "timeout" in str(e).lower():
            print(f"   💡 带图请求较慢，若仍超时可在 .env 增加 VISION_REF_TIMEOUT=180（秒）后重启。")
        import traceback
        traceback.print_exc()
        return None

    if not content:
        print(f"   ⚠️ [vision] 视觉模型返回内容为空")
        return None

    # 优先解析「仅 4 个数字」格式（适配云雾等回复长度被严格限制的 API，模型按 prompt 只输出 x y width height）
    text_raw = content.strip()
    numbers = re.findall(r"[0-9]+\.?[0-9]*", text_raw)
    if len(numbers) >= 4:
        try:
            x, y, w, h = float(numbers[0]), float(numbers[1]), float(numbers[2]), float(numbers[3])
            # 云雾常把最后一数截断成 "0"（如 0.5→0），用前三个有效值 + 合理默认 height
            if 0 <= x <= 1 and 0 <= y <= 1 and w > 0 and h <= 0:
                h = min(0.5, 1 - y)  # 典型全身约占画面高度一半以内
            if 0 <= x <= 1 and 0 <= y <= 1 and w > 0 and h > 0:
                w = max(0.01, min(1 - x, w))
                h = max(0.01, min(1 - y, h))
                if h < 0.25:
                    h_new = min(0.5, h * 2)
                    y = max(0, y - (h_new - h) * 0.3)
                    h = h_new
                return {"x": x, "y": y, "width": w, "height": h}
        except (ValueError, TypeError):
            pass

    # 解析 JSON：允许键顺序任意、嵌套 {"bbox": {...}}、被 ``` 包裹（含无闭合 ``` 或截断）
    text = text_raw
    # 去掉 ```json / ``` 外壳：有闭合则取中间，无闭合（截断）则去掉首行与尾部的 ```
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\s*```\s*$", "", text).strip()
    else:
        code_match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
        if code_match:
            text = code_match.group(1).strip()

    def _extract_bbox_from_obj(obj):
        if not isinstance(obj, dict):
            return None
        # 支持嵌套 bbox
        if "x" not in obj and "bbox" in obj and isinstance(obj["bbox"], dict):
            obj = obj["bbox"]
        # 支持多种键名：x/left/x1, y/top/y1, width/w, height/h；或 right/bottom 转成 width/height
        x = obj.get("x") if "x" in obj else obj.get("left") if "left" in obj else obj.get("x1")
        y = obj.get("y") if "y" in obj else obj.get("top") if "top" in obj else obj.get("y1")
        w = obj.get("width") if "width" in obj else obj.get("w")
        h = obj.get("height") if "height" in obj else obj.get("h")
        if "right" in obj and x is not None:
            w = float(obj["right"]) - float(x)
        if "bottom" in obj and y is not None:
            h = float(obj["bottom"]) - float(y)
        try:
            x = float(x) if x is not None else 0
            y = float(y) if y is not None else 0
            w = float(w) if w is not None else 0
            h = float(h) if h is not None else 0
        except (TypeError, ValueError):
            return None
        if w <= 0 or h <= 0:
            return None
        x = max(0, min(1, x))
        y = max(0, min(1, y))
        w = max(0.01, min(1 - x, w))
        h = max(0.01, min(1 - y, h))
        if h < 0.25:
            h_new = min(0.5, h * 2)
            y = max(0, y - (h_new - h) * 0.3)
            h = h_new
        return {"x": x, "y": y, "width": w, "height": h}

    def _find_json_objects(s):
        """从字符串中按括号匹配提取所有顶层 {...} 片段，便于尝试解析。"""
        out = []
        i = 0
        while i < len(s):
            if s[i] == "{":
                start = i
                depth = 1
                i += 1
                while i < len(s) and depth > 0:
                    if s[i] == "{":
                        depth += 1
                    elif s[i] == "}":
                        depth -= 1
                    i += 1
                if depth == 0:
                    out.append(s[start:i])
            else:
                i += 1
        return out

    def _try_load_json(raw: str):
        """尝试解析 JSON，允许尾部逗号等常见问题。"""
        s = re.sub(r",\s*}", "}", raw)  # 去掉尾部逗号
        s = re.sub(r",\s*]", "]", s)
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            return None

    for candidate in _find_json_objects(text):
        obj = _try_load_json(candidate)
        if obj:
            bbox = _extract_bbox_from_obj(obj)
            if bbox:
                return bbox

    # 兼容：无嵌套时用简单正则再试一次（键顺序任意）
    json_match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if json_match:
        obj = _try_load_json(json_match.group(0))
        if obj:
            bbox = _extract_bbox_from_obj(obj)
            if bbox:
                return bbox

    # 兜底：用正则从整段内容里抠出 x/y/width/height（应对 JSON 被截断或格式不标准）
    def _parse_bbox_by_regex(s):
        m_x = re.search(r'"x"\s*:\s*([0-9.]+)', s)
        m_y = re.search(r'"y"\s*:\s*([0-9.]+)', s)
        m_w = re.search(r'"width"\s*:\s*([0-9.]+)', s) or re.search(r'"w"\s*:\s*([0-9.]+)', s)
        m_h = re.search(r'"height"\s*:\s*([0-9.]+)', s) or re.search(r'"h"\s*:\s*([0-9.]+)', s)
        if not all([m_x, m_y, m_w, m_h]):
            return None
        try:
            x = max(0, min(1, float(m_x.group(1))))
            y = max(0, min(1, float(m_y.group(1))))
            w = max(0.01, min(1, float(m_w.group(1))))
            h = max(0.01, min(1, float(m_h.group(1))))
            if w <= 0 or h <= 0:
                return None
            if h < 0.25:
                h_new = min(0.5, h * 2)
                y = max(0, y - (h_new - h) * 0.3)
                h = h_new
            return {"x": x, "y": y, "width": w, "height": h}
        except (ValueError, TypeError):
            return None

    for source in (text, content):
        bbox = _parse_bbox_by_regex(source)
        if bbox:
            return bbox

    # 兜底：prompt 要求「只输出 4 个数字空格分隔」，兼容只返回 3 个数被截断的情况（如云雾长度限制）
    def _parse_bbox_plain_numbers(s):
        # 匹配一行中 3 或 4 个 0~1 的小数（允许前导整数如 0.2）
        nums = re.findall(r"[0-9]+\.?[0-9]*", s)
        nums = [n for n in nums if n.strip()]
        try:
            if len(nums) >= 4:
                x, y, w, h = float(nums[0]), float(nums[1]), float(nums[2]), float(nums[3])
            elif len(nums) == 3:
                x, y, w = float(nums[0]), float(nums[1]), float(nums[2])
                h = 0.45  # 仅 3 个数时用默认高度（全身约 0.4~0.5）
            else:
                return None
            x = max(0, min(1, x))
            y = max(0, min(1, y))
            w = max(0.01, min(1 - x, w))
            h = max(0.01, min(1 - y, h))
            if h < 0.25:
                h_new = min(0.5, h * 2)
                y = max(0, y - (h_new - h) * 0.3)
                h = h_new
            return {"x": x, "y": y, "width": w, "height": h}
        except (ValueError, TypeError):
            return None

    for source in (text, content):
        bbox = _parse_bbox_plain_numbers(source)
        if bbox:
            return bbox

    print(f"   ⚠️ [vision] 未从返回中解析出 bbox（长度 {len(content)} 字），内容前 200 字：{content[:200]}")
    if len(content) < 80:
        max_tok = (VISION_FOR_REF_CROP or {}).get("max_output_tokens", 512)
        print(f"   💡 当前 prompt 要求「只输出 4 个数字」；若返回被截断，可尝试 .env 增加 VISION_REF_MAX_TOKENS=512 或向代理确认放宽视觉回复长度。当前请求 max_output_tokens={max_tok}。")
    return None


def crop_image_by_bbox(
    image_path: Path,
    bbox: Dict[str, float],
    ref_dir: Path,
    out_basename: str,
    padding: float = 1.15,
) -> Optional[Path]:
    """
    按归一化 bbox 裁剪图片，适当留边（padding），保存到 ref_dir。
    :param padding: 框边放大比例，1.15 表示四边各扩约 7.5%
    """
    try:
        from PIL import Image
    except ImportError:
        print("⚠️ [vision] 需要 PIL 才能裁剪，请安装 Pillow")
        return None

    if not image_path.exists():
        return None

    try:
        img = Image.open(image_path).convert("RGB")
        W, H = img.size
    except Exception as e:
        print(f"⚠️ [vision] 打开图片失败：{e}")
        return None

    x, y = bbox.get("x", 0), bbox.get("y", 0)
    w, h = bbox.get("width", 0.2), bbox.get("height", 0.4)
    cx = x + w / 2
    cy = y + h / 2
    half_w = (w * padding) / 2
    half_h = (h * padding) / 2
    x1 = max(0, int((cx - half_w) * W))
    y1 = max(0, int((cy - half_h) * H))
    x2 = min(W, int((cx + half_w) * W))
    y2 = min(H, int((cy + half_h) * H))
    if x2 <= x1 or y2 <= y1:
        return None

    try:
        cropped = img.crop((x1, y1, x2, y2))
        out_path = ref_dir / out_basename
        cropped.save(out_path, "PNG")
        return out_path
    except Exception as e:
        print(f"⚠️ [vision] 裁剪保存失败：{e}")
        return None


def get_character_bbox_and_crop(
    scene_image_path: Path,
    ref_dir: Path,
    character_name: str,
    appearance_hints: str,
    body_ref_filename: str,
) -> Tuple[Optional[Dict[str, float]], Optional[Path]]:
    """
    在场景图中定位指定角色（bbox），并裁成单人全身参考图保存。
    通过 character_name + appearance_hints 让视觉模型知道要找的是哪个人。
    :return: (bbox, 裁剪图路径)；任一步失败则对应为 None
    """
    bbox = _call_vision_bbox(
        Path(scene_image_path),
        character_name,
        appearance_hints,
    )
    if not bbox:
        return None, None

    out_path = crop_image_by_bbox(
        Path(scene_image_path),
        bbox,
        ref_dir,
        body_ref_filename,
        padding=1.15,
    )
    return bbox, out_path
