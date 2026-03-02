# -*- coding: utf-8 -*-
"""LLM 图片提示词优化（场景图、主角形象）。"""
import json
import re
import requests
from typing import Dict, List, Optional

from src.config import AI_API_CONFIG
from src.constants import TONE_CONFIGS
from src.utils.text_utils import _safe_str, _clip_text
from src.wiki.lookup import (
    wiki_lookup_theme_and_character,
    _format_protagonist_canonical_for_prompt,
    _infer_gender_from_text,
)

# 剧情图提示词格式示例（默认）：固定参数勿改，仅首行风格与角色/发色随画风与剧情变化
PROMPT_FORMAT_EXAMPLE = """(masterpiece, best quality, 8K, ultra-detailed), anime close-up, soft watercolor wash techniques (strength 45%), subtle film grain, surrealist aesthetics, rich layering, moderate white space, harmonious contrasting colors, artistic narrative, original aspect ratio, elegant scholarly demeanor.

--面部系统--
8K hyper-realistic anime facial modeling, sharp iris highlights, slight bloodshot texture on sclera, matte translucent lip color, lip peak highlight strength 0.3, lip line depth 0.15.

--头发系统--
intricate hair strands, root fixation coefficient 0.95, tip swing amplitude 10cm,
color layering: main color #4A5568, secondary color #718096, highlight #E2E8F0, shadow #1A202C, 3-5 strands of gradient per cluster,
physical wind simulation: point wind source behind neck (wind speed 1.2m/s, turbulence intensity 5%), wind direction 45°.

--衣物系统--
slight shoulder line folds (depth 0.3), fabric: matte black satin, surface oil-splash texture (density 0.3), local iridescent reflection (angle-dependent strength 0.6).

--视角与构图--
perspective locked: close-up elevation 30°, focal length 50mm, depth of field f/1.8, focal plane locked on face,
action constraints: only hair flutters gently with wind field, all other body parts completely static."""

# 水墨画风格专用示例：同一结构，首行改为水墨/淡彩风格，发色为淡彩；固定参数与默认示例一致
PROMPT_FORMAT_EXAMPLE_INK = """(masterpiece, best quality, 8K, ultra-detailed), traditional Chinese ink wash and soft watercolor, dreamy ethereal aesthetic, delicate paper texture and visible brushstrokes, diffused natural light, soft color gradients, moderate white space, pastel and iridescent color palette, original aspect ratio, refined contemplative presence.

--面部系统--
8K hyper-realistic anime facial modeling, sharp iris highlights, slight bloodshot texture on sclera, matte translucent lip color, lip peak highlight strength 0.3, lip line depth 0.15.

--头发系统--
intricate hair strands, root fixation coefficient 0.95, tip swing amplitude 10cm,
color layering: main color #B8E0F7, secondary color #D8B4FE, highlight #FDE68A, shadow #94A3B8, 3-5 strands of gradient per cluster,
physical wind simulation: point wind source behind neck (wind speed 1.2m/s, turbulence intensity 5%), wind direction 45°.

--衣物系统--
slight shoulder line folds (depth 0.3), fabric: matte black satin, surface oil-splash texture (density 0.3), local iridescent reflection (angle-dependent strength 0.6).

--视角与构图--
perspective locked: close-up elevation 30°, focal length 50mm, depth of field f/1.8, focal plane locked on face,
action constraints: only hair flutters gently with wind field, all other body parts completely static."""

# 动漫风格专用示例：日式漫画、柔和线条、网点阴影、手绘感、含场景与氛围与角色动作
PROMPT_FORMAT_EXAMPLE_MANGA = """(masterpiece, best quality, 8K, ultra-detailed), Japanese manga style, refined ink outlines and moderate line weight, screentone shading, high contrast, dramatic shadows, hand-drawn illustration aesthetic, vibrant colors, intense psychological thriller scene in high-tech NERV laboratory, sterile fluorescent lighting with blue-green monitor glow casting dramatic shadows, tense atmosphere as secrets are revealed, young male protagonist listening intently while female scientist explains EVA mysteries, protagonist uses Image 0, 赤木律子博士-配角1.

--面部系统--
8K hyper-realistic anime facial modeling, refined manga-style line art with soft outlines, defined jawline, intense gaze, soft ink outlines around eyes and lips, subtle iris highlights, slight bloodshot texture on sclera, matte translucent lip color, lip peak highlight strength 0.3, lip line depth 0.15.

--头发系统--
defined hair strands with refined outlines, distinct clumps of hair with screentone shading, intricate hair strands, root fixation coefficient 0.95, tip swing amplitude 10cm,
color layering: main color #2D1B69, secondary color #4A4A4A, highlight #6B7280, shadow #1F2937, 3-5 strands of gradient per cluster, natural color transition,
physical wind simulation: point wind source behind neck (wind speed 1.2m/s, turbulence intensity 5%), wind direction 45°.

--衣物系统--
crisp lab coat with clean folds and soft outlines, NERV logo on collar, black turtleneck underneath, subtle screentone on fabric, slight shoulder line folds (depth 0.3), fabric: matte white lab coat, subtle texture, professional and sterile appearance.

--场景与氛围--
high-tech NERV laboratory, multiple glowing monitors with blue-green light, sterile white walls, metallic surfaces, dramatic shadows from fluorescent lights, hand-drawn background details, tense atmosphere, secrets being revealed, psychological thriller vibe, cinematic composition, close-up on faces to emphasize tension.

--视角与构图--
perspective locked: medium shot, eye-level angle, focal length 50mm, depth of field f/2.8, focal plane locked on the two characters' faces,
action constraints: protagonist leaning forward slightly, scientist gesturing with a data pad, both characters with intense expressions, no unnecessary movements."""

# 主角形象·动漫风格专用示例：全身正面、纯白背景、单角色，分模块格式（线条略柔和）
PROMPT_FORMAT_EXAMPLE_MAIN_CHAR_ANIME = """(masterpiece, best quality, 8K, ultra-detailed), Japanese manga style, refined ink outlines and moderate line weight, screentone shading, high contrast, dramatic shadows, hand-drawn illustration aesthetic, full-body front-view portrait, pure white background, single character, young male protagonist with refined features.

--面部系统--
8K hyper-realistic anime facial modeling, refined manga-style line art with soft outlines, defined jawline, intense gaze, soft ink outlines around eyes and lips, subtle iris highlights, slight bloodshot texture on sclera, matte translucent lip color, lip peak highlight strength 0.3, lip line depth 0.15.

--头发系统--
defined hair strands with refined outlines, distinct clumps of hair with screentone shading, intricate hair strands, root fixation coefficient 0.95, tip swing amplitude 10cm,
color layering: main color #2D1B69, secondary color #4A4A4A, highlight #6B7280, shadow #1F2937, 3-5 strands of gradient per cluster, natural color transition,
physical wind simulation: point wind source behind neck (wind speed 1.2m/s, turbulence intensity 5%), wind direction 45°.

--衣物系统--
crisp clothing with clean folds and soft outlines, subtle screentone on fabric, slight shoulder line folds (depth 0.3), fabric: matte texture, professional appearance.

--视角与构图--
perspective locked: full-body front view, eye-level, focal length 50mm, depth of field f/2.8, focal plane on character, pure white background only, no background elements,
action constraints: standing straight, arms relaxed at sides, completely static, no unnecessary movements."""

# 剧情图「精简 JSON 模板」参考示例：供 LLM 按此结构输出，含 face/hair/clothing 参数与 subject/environment 拆分
PROMPT_JSON_EXAMPLE_SCENE = {
    "label": "scene-6-memory-mirror-star-night-revelation",
    "tags": ["fantasy", "mystical", "ethereal-atmosphere", "impressionist", "dreamlike"],
    "style": ["impressionist oil painting", "rich brushstrokes", "luminous color transitions", "atmospheric perspective", "soft-shading"],
    "subject": {
        "body_traits": [
            "young male protagonist with ethereal presence",
            "淡蓝色眼眸 reflecting mirror's starlight",
            "moon-like luminous skin with silver-blue aura",
            "serene surprised contemplative expression",
            "barefoot on ancient stone platform"
        ],
        "outfit": [
            "flowing white long dress with luminous dream-like fabric",
            "ethereal translucent material catching mirror light"
        ],
        "pose": [
            "standing gracefully beside large ornate memory mirror",
            "body turned slightly toward 晨曦-配角1",
            "arms naturally at sides with gentle questioning gesture",
            "posture showing emotional resonance with mirror's revelation"
        ]
    },
    "face_system": [
        "8K detailed facial features with soft impressionist rendering",
        "淡蓝色眼眸 with starlight reflections from mirror",
        "surprised yet contemplative expression",
        "lip color naturally muted with soft highlights",
        "facial highlights catching silver-blue aura light",
        "soft shadows creating ethereal depth"
    ],
    "hair_system": [
        "long silver-white hair flowing like liquid mercury",
        "root fixation coefficient 0.9",
        "tip swing amplitude 15cm",
        "color layering: main color #F7FAFC, secondary color #E2E8F0, highlight #FFFFFF, shadow #CBD5E0",
        "gentle breeze effect with moonlight highlights",
        "wind speed 0.8m/s",
        "turbulence intensity 3%",
        "wind direction from mirror's magical aura"
    ],
    "clothing_system": [
        "flowing white dress with ethereal fabric movement",
        "slight fabric flutter from magical mirror energy",
        "luminous translucent material with dream-like quality",
        "fabric catching and reflecting mirror's starlight"
    ],
    "environment": {
        "background": [
            "large ornate memory mirror with starry garden reflection",
            "memory mirror showing star night tending flowers",
            "circular stone platform with ancient mystical symbols",
            "seven crystal pillars glowing with different colored magical light",
            "night sky with gentle starlight filtering down",
            "moon-stone pathway extending into distance"
        ],
        "characters": [
            "晨曦-配角1 standing beside mirror in deep blue robes"
        ],
        "effects": [
            "magical light emanating from mirror surface",
            "floating light particles around mirror frame",
            "gentle magical aura surrounding the scene"
        ]
    },
    "color_restriction": [
        "silver-blue moonlight tones",
        "starlight white and crystal blue",
        "deep blue robes for 晨曦-配角1",
        "warm golden light from mirror's garden scene"
    ],
    "lighting": [
        "soft moonlight from above",
        "magical starlight from memory mirror",
        "gentle crystal pillar glow",
        "ethereal silver-blue aura around protagonist"
    ],
    "camera": {
        "type": "medium shot",
        "lens": "50mm",
        "aperture": "f/2.8",
        "depth_of_field": "shallow focus on protagonist and mirror",
        "flash": "none",
        "grain": "subtle impressionist texture",
        "texture": "oil painting brushstrokes"
    },
    "composition": [
        "protagonist side view toward mirror",
        "晨曦-配角1 positioned on right side of frame",
        "memory mirror as central focal element",
        "action constraints: gentle questioning gesture and contemplative stance"
    ],
    "mood": [
        "mystical revelation",
        "gentle surprise and recognition",
        "ethereal contemplation",
        "magical discovery atmosphere",
        "warm emotional resonance between characters and mirror"
    ],
    "output_style": [
        "dreamlike magical realism",
        "no text or symbols in image"
    ]
}

# 主角形象「精简 JSON 模板」参考示例：与剧情图同结构，仅人物、纯白背景、无场景
PROMPT_JSON_EXAMPLE_MAIN_CHAR = {
    "label": "main-character-full-body-portrait",
    "tags": ["fantasy", "ethereal", "portrait", "full-body", "single character"],
    "style": ["impressionist oil painting", "rich brushstrokes", "luminous color transitions", "soft-shading", "clean rendering"],
    "subject": {
        "body_traits": [
            "young male protagonist with ethereal presence",
            "淡蓝色眼眸, gentle gaze",
            "moon-like luminous skin with silver-blue aura",
            "serene contemplative expression",
            "slender build, standing full body"
        ],
        "outfit": [
            "flowing white long dress with luminous dream-like fabric",
            "ethereal translucent material, soft folds"
        ],
        "pose": [
            "standing straight, full-body front view",
            "arms relaxed at sides",
            "centered in frame",
            "pure white background only, no environment"
        ]
    },
    "face_system": [
        "8K detailed facial features with soft impressionist rendering",
        "淡蓝色眼眸, soft and clear",
        "serene contemplative expression",
        "lip color naturally muted with soft highlights",
        "facial highlights with subtle silver-blue aura",
        "soft shadows creating gentle depth"
    ],
    "hair_system": [
        "long silver-white hair flowing like liquid mercury",
        "root fixation coefficient 0.9",
        "tip swing amplitude 15cm",
        "color layering: main color #F7FAFC, secondary color #E2E8F0, highlight #FFFFFF, shadow #CBD5E0",
        "gentle natural fall, no wind",
        "wind speed 0 m/s",
        "turbulence intensity 0%"
    ],
    "clothing_system": [
        "flowing white dress with ethereal fabric",
        "slight soft folds, no motion",
        "luminous translucent material with dream-like quality",
        "clean fabric, no background elements"
    ],
    "environment": {
        "background": [
            "pure white background",
            "no objects, no scenery",
            "even white only, no shadows on background"
        ],
        "characters": [],
        "effects": []
    },
    "color_restriction": [
        "pure white background only",
        "silver-white hair tones",
        "character clothing and skin as described",
        "no environmental colors"
    ],
    "lighting": [
        "soft even front lighting",
        "no dramatic shadows on background",
        "gentle light on face and body"
    ],
    "camera": {
        "type": "full-body portrait",
        "lens": "50mm",
        "aperture": "f/2.8",
        "depth_of_field": "shallow focus on character",
        "flash": "none",
        "grain": "subtle impressionist texture",
        "texture": "oil painting brushstrokes"
    },
    "composition": [
        "single character only",
        "full-body front view, centered",
        "pure white background, no other elements",
        "action constraints: standing still, arms at sides, no gesture"
    ],
    "mood": [
        "serene",
        "ethereal contemplation",
        "calm presence"
    ],
    "output_style": [
        "dreamlike magical realism",
        "no text or symbols in image",
        "full-body character design, pure white background only"
    ]
}

# 颜值等级 → 用于生图的具体外貌描述（供 LLM 参考 + 默认提示词兜底）
# 格式: (给 LLM 的中文说明, 拼进最终提示词的英文关键词，高/极高时用于后处理追加)
APPEARANCE_LEVEL_MAP = {
    "极低": ("相貌很普通，无明显亮点", "plain, unremarkable appearance"),
    "低": ("相貌平平", "plain, average appearance"),
    "普通": ("相貌普通", "average looking"),
    "高": ("英俊或美丽，相貌出众，五官端正", "handsome, beautiful, attractive, good-looking, clear skin"),
    "极高": ("非常英俊或美丽，五官精致，皮肤细腻，气质出众", "very handsome, very beautiful, stunning, delicate features, symmetrical face, clear skin, attractive"),
}


def _get_appearance_hint_for_llm(level: str) -> str:
    """返回给 LLM 的颜值视觉描述要求（中文）。"""
    entry = APPEARANCE_LEVEL_MAP.get(level, APPEARANCE_LEVEL_MAP["普通"])
    return entry[0]


def _get_appearance_english_suffix(level: str) -> str:
    """返回高/极高时追加的英文关键词，其余返回空字符串。"""
    if level not in ("高", "极高"):
        return ""
    return ", " + APPEARANCE_LEVEL_MAP.get(level, APPEARANCE_LEVEL_MAP["普通"])[1]


def _parse_json_from_llm_response(content: str) -> Optional[Dict]:
    """从 LLM 返回文本中解析 JSON，支持裸 JSON 或 ```json ... ``` 代码块。"""
    if not content or not isinstance(content, str):
        return None
    text = content.strip()
    for pattern in (r"```json\s*\n?(.*?)```", r"```\s*\n?(.*?)```"):
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except (json.JSONDecodeError, ValueError):
                pass
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _build_prompt_from_structured_json(data: Dict, include_scene_atmosphere: bool = False) -> str:
    """
    将结构化 JSON 拼成剧情图/主角形象用的完整提示词字符串。
    data 应包含: first_line, face_system, hair_system, clothing_system, view_composition；
    可选 scene_atmosphere（剧情图动漫风格时使用）。
    """
    parts = []
    first = _safe_str(data.get("first_line") or data.get("first_line_en", "")).strip()
    if first:
        parts.append(first)
    face = _safe_str(data.get("face_system", "")).strip()
    if face:
        parts.append("--面部系统--\n" + face)
    hair = _safe_str(data.get("hair_system", "")).strip()
    if hair:
        parts.append("--头发系统--\n" + hair)
    clothing = _safe_str(data.get("clothing_system", "")).strip()
    if clothing:
        parts.append("--衣物系统--\n" + clothing)
    if include_scene_atmosphere:
        scene_atm = _safe_str(data.get("scene_atmosphere", "")).strip()
        if scene_atm:
            parts.append("--场景与氛围--\n" + scene_atm)
    view = _safe_str(data.get("view_composition", "")).strip()
    if view:
        parts.append("--视角与构图--\n" + view)
    return "\n\n".join(parts) if parts else ""


def _is_rich_prompt_json(data: Dict) -> bool:
    """判断是否为「完整 JSON 模板」格式（含 label / subject / environment 等）。"""
    if not data or not isinstance(data, dict):
        return False
    return (
        "label" in data
        or ("subject" in data and isinstance(data.get("subject"), dict))
        or ("environment" in data and isinstance(data.get("environment"), dict))
    )


def _join_list_or_str(val) -> str:
    """将数组或字符串转为逗号分隔的字符串。"""
    if val is None:
        return ""
    if isinstance(val, list):
        return ", ".join(_safe_str(x).strip() for x in val if _safe_str(x).strip())
    return _safe_str(val).strip()


def _build_prompt_from_rich_json(data: Dict) -> str:
    """
    将「精简 JSON 模板」格式拼成一行长提示词。支持：
    subject: body_traits, outfit, pose（或 made_out_of, arrangement）；
    environment: background, characters, effects（或 room_objects, accessories）；
    composition 或 view_composition；可选 edit。
    """
    parts = []
    label = _safe_str(data.get("label", "")).strip()
    if label:
        parts.append(label)
    tags = _join_list_or_str(data.get("tags"))
    if tags:
        parts.append(tags)
    style = _join_list_or_str(data.get("style"))
    if style:
        parts.append(style)
    subject = data.get("subject")
    if isinstance(subject, dict):
        for key in ("body_traits", "outfit", "pose", "made_out_of", "arrangement"):
            v = subject.get(key)
            s = _join_list_or_str(v)
            if s:
                parts.append(s)
    for key in ("face_system", "hair_system", "clothing_system"):
        v = data.get(key)
        s = _join_list_or_str(v)
        if s:
            parts.append(s)
    env = data.get("environment")
    if isinstance(env, dict):
        for key in ("background", "characters", "effects", "room_objects", "accessories"):
            v = env.get(key)
            s = _join_list_or_str(v)
            if s:
                parts.append(s)
    parts.append(_join_list_or_str(data.get("color_restriction")))
    parts.append(_join_list_or_str(data.get("lighting")))
    camera = data.get("camera")
    if isinstance(camera, dict):
        cam_parts = []
        for k in ("type", "lens", "aperture", "depth_of_field", "flash", "grain", "texture"):
            v = camera.get(k)
            if v is not None and _safe_str(v).strip():
                cam_parts.append(f"{k} {_safe_str(v).strip()}")
        if cam_parts:
            parts.append(", ".join(cam_parts))
    # composition 与 view_composition 二选一或都有则都拼入
    parts.append(_join_list_or_str(data.get("composition")))
    parts.append(_join_list_or_str(data.get("view_composition")))
    parts.append(_join_list_or_str(data.get("output_style")))
    parts.append(_join_list_or_str(data.get("mood")))
    edit = data.get("edit")
    if isinstance(edit, dict):
        for k in ("location", "atmosphere"):
            v = edit.get(k)
            s = _join_list_or_str(v)
            if s:
                parts.append(s)
    combined = ", ".join(p for p in parts if p)
    return combined


def optimize_image_prompt_with_llm(
    scene_description: str,
    global_state: Dict,
    image_style: Dict = None,
    protagonist_reference_images: List[str] = None,
    supporting_role_references: List[Dict] = None,
    available_supporting_roles_for_tagging: List[Dict] = None
) -> str:
    """
    使用 LLM（由 AI_API_CONFIG.model 配置，默认 claude-opus-4-6）优化图片生成提示词
    """
    try:
        visual_context = global_state.get('_visual_context') if isinstance(global_state, dict) else None
        if not isinstance(visual_context, dict):
            visual_context = {}

        prev_img_obj = visual_context.get('previousSceneImage') or visual_context.get('currentSceneImage') or {}
        if not isinstance(prev_img_obj, dict):
            prev_img_obj = {}

        previous_image_prompt = (
            visual_context.get('previous_image_prompt')
            or prev_img_obj.get('prompt')
            or prev_img_obj.get('optimized_prompt')
            or ""
        )
        previous_image_url = (
            visual_context.get('previous_image_url')
            or prev_img_obj.get('url')
            or prev_img_obj.get('image_url')
            or ""
        )
        previous_scene_text = (
            visual_context.get('previousSceneText')
            or visual_context.get('currentSceneText')
            or ""
        )
        scene_id_for_lock = visual_context.get('sceneId') or ""

        continuity_requirements = ""
        if previous_image_prompt or previous_scene_text or previous_image_url or scene_id_for_lock:
            continuity_requirements = f"""【连续性/一致性要求（重要）】
- **画风与角色一致**：同一场景内角色外观（发型、脸型、服装配色与材质）、关键道具/武器/饰品、环境主色调与光线风格须与上一张图保持一致，不得无故更换造型、服装或装备。
- **延续画面设定**：沿用上一张图的镜头语言、构图风格、色彩基调与角色造型；仅当【当前剧情】明确要求变化（如转身、换装、换场景）时才在描述中体现变化。
- **优先级**：当前剧情与当前镜头的描述（朝向、动作、站位）优先级最高；若与上一张图或参考图冲突，以当前剧情为准。例如剧情明确为「背对观众」时，必须使用背面参考图并写明背对镜头。
- **禁止文字入图**：最终提示词中不得包含 URL、文件路径或任何可被生图模型渲染成文字的字符串（如 http://、data:image/、本地路径），避免画面中出现乱码或网址。

上一剧情文本（可选）：
{previous_scene_text[:800] if previous_scene_text else '（无）'}

上一张图的提示词（可选，作为画面设定参照）：
{previous_image_prompt[:1200] if previous_image_prompt else '（无）'}
"""

        core_worldview = global_state.get('core_worldview', {})
        user_theme = _safe_str(global_state.get("user_theme")).strip()
        game_theme = core_worldview.get('game_style', '')
        world_setting = core_worldview.get('world_basic_setting', '')
        protagonist_ability = core_worldview.get('protagonist_ability', '')

        protagonist_info = {}
        if 'characters' in core_worldview and '主角' in core_worldview['characters']:
            protagonist = core_worldview['characters']['主角']
            protagonist_info = {
                'personality': protagonist.get('core_personality', ''),
                'appearance': protagonist.get('shallow_background', '')
            }

        game_tone = global_state.get('tone', 'normal_ending')
        tone = TONE_CONFIGS.get(game_tone, TONE_CONFIGS['normal_ending'])
        tone_description = tone.get('name', '普通结局')

        style_description = ''
        if image_style:
            style_type = image_style.get('type', '')
            if style_type == 'realistic':
                style_description = '写实风格，真实细腻，细节丰富'
            elif style_type == 'anime':
                style_description = '动漫风格，日式漫画美学，柔和线条（适中线宽），网点阴影，高对比，戏剧性阴影，含场景与氛围与角色动作'
            elif style_type == 'ink_painting':
                style_description = '水墨画风格，中国传统水墨画，黑白灰调，意境深远'
            elif style_type == 'oil_painting':
                subtype = image_style.get('subtype', 'classic_oil')
                if subtype == 'impressionist':
                    style_description = '印象派油画风格，光影变化丰富，笔触明显'
                elif subtype == 'rococo':
                    style_description = '洛可可风格油画，华丽精致，装饰性强'
                else:
                    style_description = '经典油画风格，厚重质感，色彩丰富'
            elif style_type == 'cyberpunk':
                style_description = '赛博朋克风格，未来科技感，霓虹灯效果，高对比度'
            elif style_type == 'custom':
                style_description = f"自定义风格：{image_style.get('value', '')}"

        protagonist_reference_section = ""
        if protagonist_reference_images and len(protagonist_reference_images) >= 1:
            n = len(protagonist_reference_images)
            lines = ["【主角参考图说明（重要）】", "生图API根据本镜头需要仅传递 1～2 张主角参考图（如只需正面则只传正面），请根据剧情只引用需要的视角："]
            lines.append("- Image 0：主角正面视图（Front view portrait of the protagonist）")
            if n >= 2:
                lines.append("- Image 1：主角侧面视图（Side view portrait of the protagonist）")
            if n >= 3:
                lines.append("- Image 2：主角背面视图（Back view portrait of the protagonist）")
            lines.append("")
            lines.append("在生成场景图片时，根据剧情中主角的视角明确说明主角使用哪张参考图（仅使用已提供的编号，最多引用 2 张）：")
            lines.append("- 正面朝向镜头 → 主角使用 Image 0")
            if n >= 2:
                lines.append("- 侧面朝向镜头 → 主角使用 Image 1")
            if n >= 3:
                lines.append("- 背面朝向镜头 → 主角使用 Image 2")
            if n >= 2:
                lines.append("- 其他角度可写「主角主要参考 Image 0 和 Image 1」等（最多 2 张）")
            lines.append("")
            lines.append("请在最终视觉描述中明确说明主角使用哪张参考图，确保主角形象与参考图一致。")
            protagonist_reference_section = "\n".join(lines) + "\n"

        supporting_role_reference_section = ""
        if supporting_role_references and len(supporting_role_references) >= 1:
            lines_sr = ["【配角参考图说明（重要）】", "生图API将接收以下配角参考图（编号续接主角之后，均为初登场场景图，可能含多人）："]
            for sr in supporting_role_references:
                role_name = _safe_str(sr.get("role_name", "")).strip()
                img_idx = sr.get("image_index", 0)
                core_feat = _safe_str(sr.get("core_features", "")).strip()
                first_scene = _safe_str(sr.get("first_appear_scene", "")).strip()
                if not role_name:
                    continue
                desc = f"- Image {img_idx}：{role_name}"
                if first_scene:
                    desc += f"，首次出场于「{first_scene}」"
                if core_feat:
                    desc += "，核心特征（不可修改）：" + (core_feat[:120] + "…" if len(core_feat) > 120 else core_feat)
                else:
                    desc += "，保持五官核心特征不变"
                lines_sr.append(desc)
            lines_sr.append("")
            lines_sr.append("在生成场景图片时：")
            lines_sr.append("1. 根据剧情明确每个配角使用哪张参考图（仅使用已提供的编号）")
            lines_sr.append("2. 参考图为场景图（含多人）时，**必须明确写出该配角对应图中哪个人物**，例如：以图中从左到右第二个人物的形象为准、以图中右侧持剑的少年为准")
            lines_sr.append("3. 必须在描述中写明「XXX 参考 Image N，以图中XX位置/特征的人物为准，保持核心特征不变」")
            lines_sr.append("4. 可变化：服饰细节、动作、表情、所处位置")
            lines_sr.append("5. 不可变化：五官、发型、肤色、体型等核心特征")
            lines_sr.append("6. **必须严格保持该配角的面部、发型、体型与参考图一致，不可因场景变化而变形**")
            supporting_role_reference_section = "\n".join(lines_sr) + "\n"
        elif available_supporting_roles_for_tagging and len(available_supporting_roles_for_tagging) >= 1:
            lines_tag = ["【配角标注要求（重要）】"]
            has_existing = any(
                _safe_str(item.get("role_key", "")).strip() == "已有角色"
                for item in available_supporting_roles_for_tagging
            )
            if has_existing:
                lines_tag.append("已建档的配角（再次出场时请使用相同名称或别号）：")
                for item in available_supporting_roles_for_tagging:
                    if _safe_str(item.get("role_key", "")).strip() == "已有角色":
                        names = _safe_str(item.get("names_or_aliases", "")).strip()
                        rn = _safe_str(item.get("role_name", "")).strip()
                        if names or rn:
                            lines_tag.append(f"  - {names or rn}（请使用其名或别号+配角N格式，如 凌川-配角1）")
                lines_tag.append("")
            lines_tag.append("新出场的配角：分析【当前剧情】中是否有**新登场**的非主角人物，若有则用「角色名-配角N」格式标注，角色名必须从剧情文本中得出（如黑衣人-配角1、老者-配角2）。")
            lines_tag.append("只对剧情中实际出场且非主角的配角使用该格式；未出场者不要写。不要写「参考 Image N」，由系统后续自动添加。")
            lines_tag.append("**新登场配角构图要求**：若有配角首次出场，请在描述中明确写出该配角在画面中的位置或显著特征（如画面左侧、持相机者、穿深色外套者），便于后续建立单人参考图。")
            supporting_role_reference_section = "\n".join(lines_tag) + "\n"

        protagonist_canonical_block = _format_protagonist_canonical_for_prompt(
            global_state.get("protagonist_canonical") or {}
        )
        canonical = global_state.get("protagonist_canonical") or {}
        protagonist_name = _safe_str(canonical.get("name_zh") or canonical.get("name_en") or "").strip()
        protagonist_identity_warning = f"\n【重要】主角身份：{protagonist_name or '玩家视角主角'}（上述主角规范信息描述的人）。**切勿将主角标注为配角**，只对剧情中出场的**非主角**人物使用「名称-配角N」格式。\n"

        # 按画风选择格式示例：水墨 / 动漫（用漫画式提示词模板）/ 默认
        if image_style and image_style.get("type") == "ink_painting":
            format_example = PROMPT_FORMAT_EXAMPLE_INK
        elif image_style and image_style.get("type") == "anime":
            format_example = PROMPT_FORMAT_EXAMPLE_MANGA
        else:
            format_example = PROMPT_FORMAT_EXAMPLE
        include_scene_atmosphere = bool(image_style and image_style.get("type") == "anime")
        scene_atmosphere_instruction = (
            "- scene_atmosphere：（动漫/漫画风格时必填）场景与氛围内容，对应示例中的 --场景与氛围-- 段落。\n"
            if include_scene_atmosphere else ""
        )
        json_example_str = json.dumps(PROMPT_JSON_EXAMPLE_SCENE, ensure_ascii=False, indent=2)

        llm_prompt = f"""假设你是一个专业的剧情分析师和视觉设计师，现在需要你将剧情转化为具体的视觉描述，告诉生图AI如何生成图片。

【游戏背景信息】
- 游戏主题：{game_theme}
- 世界观设定：{world_setting}
- 游戏基调：{tone_description}

【主角信息】
- 主角能力：{protagonist_ability}
- 主角性格：{protagonist_info.get('personality', '')}
- 主角外貌特征：{protagonist_info.get('appearance', '')}

【主角规范信息】（描写主角性别/年龄/外貌时必须严格遵循，与主角立绘一致）
{protagonist_canonical_block}
{protagonist_identity_warning}
【当前剧情】（请据此分析是否有新登场配角，并用「名称-配角N」标注）
{scene_description}

【图片风格要求】
{style_description if style_description else '默认风格'}

{protagonist_reference_section if protagonist_reference_section else ''}
{supporting_role_reference_section if supporting_role_reference_section else ''}

{continuity_requirements if continuity_requirements else ''}

请根据以上信息，生成一个详细的视觉描述提示词，要求：
1. 准确反映当前剧情场景
2. 体现主角的外貌特征和能力特点；若有【主角规范信息】，描写主角时必须严格遵循其性别、年龄感与标志性外观关键词
3. 符合游戏主题和世界观设定
4. 匹配游戏基调（如悲剧基调应体现沉重氛围）
5. 符合指定的图片风格
6. 不要包含任何文字、符号、乱码（重要：必须在提示词中明确告诉生图AI不要生成任何文字、符号、乱码）
7. 描述要具体、生动，包含场景、人物、光线、氛围等细节
{('8. 如果提供了主角参考图说明，必须在提示词中明确说明主角使用 Image 0/1/2 中的哪张（根据主角在场景中的视角）' if protagonist_reference_section else '')}
{('9. 如果提供了配角参考图说明，必须在提示词中明确说明每个配角参考 Image N，并强调保持其核心特征不变' if (supporting_role_references and len(supporting_role_references) >= 1) else '')}
{('9. 如果提供了配角标注要求，必须在视觉描述中对出场的配角使用「角色名-配角N」格式（如凌川-配角1），便于系统识别' if (available_supporting_roles_for_tagging and len(available_supporting_roles_for_tagging) >= 1 and not (supporting_role_references and len(supporting_role_references) >= 1)) else '')}

【输出格式】请严格按照以下「精简 JSON 模板」输出，且仅输出该 JSON，不要其他解释或 markdown。键名必须为英文。**数组字段请逐条列出，一条一句。**

JSON 结构（所有数组均为字符串数组；嵌套对象见说明）：

- label：本镜头的简短内部名称（英文），如 "scene-6-memory-mirror-star-night-revelation"。
- tags：整体美学标签数组，如 ["fantasy", "mystical", "ethereal-atmosphere"]。
- style：画风数组，如 ["impressionist oil painting", "rich brushstrokes", "soft-shading"]。
- subject：对象。内嵌：
  - body_traits：身体特征数组，每项一句（年龄、皮肤、眼眸、表情、站姿等）。
  - outfit：服装/材质数组，每项一句，如 "flowing white long dress", "ethereal translucent material"。
  - pose：姿势与位置数组，每项一句，如 "standing beside mirror", "arms naturally at sides", "身体朝向XXX-配角1"。
- face_system：面部系统数组，每项一句；可含 "8K detailed facial features", "lip color naturally muted", "soft shadows" 等。
- hair_system：头发系统数组，每项一句；须含 root fixation coefficient、tip swing amplitude、color layering（main/secondary/highlight/shadow）、wind speed、turbulence intensity 等参数。
- clothing_system：衣物系统数组，每项一句；可含 "slight fabric flutter", "luminous translucent material" 等。
- environment：对象。内嵌：
  - background：背景描述数组，每项一句。
  - characters：场景中其他角色描述数组，如 ["晨曦-配角1 standing beside mirror in deep blue robes"]。
  - effects：氛围/特效数组，如 "magical light from mirror", "floating light particles"。
- color_restriction：色彩限制数组，每项一句。
- lighting：光源数组，每项一句。
- camera：对象。内嵌 type, lens, aperture, depth_of_field, flash, grain, texture，值为字符串。
- composition：构图数组，每项一句；须含角色站位、焦点、action constraints。
- mood：氛围数组，如 "mystical revelation", "ethereal contemplation"。
- output_style：成片风格数组，如 "dreamlike magical realism", "no text or symbols in image"。

以上字段均需根据【当前剧情】与【图片风格要求】填写；配角请用「角色名-配角N」格式。参考示例（按此结构输出，仅替换为当前剧情内容）：
{json_example_str}

只输出一个合法的 JSON 对象，不要用 markdown 代码块包裹以外的内容。"""

        api_key = AI_API_CONFIG.get('api_key', '')
        base_url = AI_API_CONFIG.get('base_url', '')

        if not api_key or not base_url:
            print("⚠️ LLM API未配置，使用原始提示词")
            return f"{game_theme}, {scene_description[:500]}, cinematic, detailed, high quality, 4k, dramatic lighting, atmospheric"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8"
        }

        request_body = {
            "model": AI_API_CONFIG.get("model", "claude-opus-4-6"),
            "messages": [{"role": "user", "content": llm_prompt}],
            "temperature": 0.7,
            "max_tokens": 2000
        }

        print("🔄 正在使用LLM优化图片生成提示词...")
        response = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=request_body,
            timeout=120
        )
        response.raise_for_status()

        result = response.json()
        choices = result.get("choices", [])
        if choices and len(choices) > 0:
            raw_content = choices[0].get("message", {}).get("content", "").strip()
            if raw_content:
                structured = _parse_json_from_llm_response(raw_content)
                if structured:
                    if _is_rich_prompt_json(structured):
                        optimized_prompt = _build_prompt_from_rich_json(structured)
                    else:
                        optimized_prompt = _build_prompt_from_structured_json(
                            structured, include_scene_atmosphere=include_scene_atmosphere
                        )
                    # 将解析出的 JSON 写入 global_state 并打印到后端，便于调试/展示
                    if isinstance(global_state, dict):
                        global_state["_last_scene_prompt_json"] = structured
                    print("📋 [剧情图] 提示词 JSON：")
                    print(json.dumps(structured, ensure_ascii=False, indent=2))
                else:
                    optimized_prompt = raw_content
                    if isinstance(global_state, dict) and "_last_scene_prompt_json" in global_state:
                        del global_state["_last_scene_prompt_json"]
                if optimized_prompt:
                    optimized_prompt = re.sub(r'https?://\S+', '', optimized_prompt).strip()
                    optimized_prompt = re.sub(r'data:image/\S+', '', optimized_prompt).strip()
                    optimized_prompt = re.sub(r'[/\\]image_cache[/\\]\S+', '', optimized_prompt).strip()
                    optimized_prompt = f"{optimized_prompt}, no text, no symbols, no garbled characters, no words"
                    if continuity_requirements:
                        optimized_prompt = f"{optimized_prompt}, consistent character design, consistent outfit and key props, consistent color palette and lighting"
                    print(f"✅ LLM提示词优化完成，长度：{len(optimized_prompt)}字符")
                    return optimized_prompt

        print("⚠️ LLM优化失败，使用原始提示词")
        return f"{game_theme}, {scene_description[:500]}, cinematic, detailed, high quality, 4k, dramatic lighting, atmospheric"

    except Exception as e:
        print(f"⚠️ LLM提示词优化出错：{str(e)}，使用原始提示词")
        core_worldview = global_state.get('core_worldview', {})
        game_style = core_worldview.get('game_style', '')
        scene_summary = scene_description[:500] if len(scene_description) > 500 else scene_description
        return f"{game_style}, {scene_summary}, cinematic, detailed, high quality, 4k, dramatic lighting, atmospheric"


def _get_style_description(image_style: Dict) -> str:
    """从 image_style 提取风格描述"""
    if not image_style or not isinstance(image_style, dict):
        return "写实风格，8K，细节丰富"
    t = image_style.get("type", "")
    if t == "realistic":
        return "写实风格，真实细腻，8K"
    if t == "anime":
        return "动漫风格，日式动画，色彩鲜明"
    if t == "ink_painting":
        return "水墨画风格，中国传统水墨"
    if t == "oil_painting":
        return "油画风格，光影丰富，8K"
    if t == "cyberpunk":
        return "赛博朋克风格，未来科技感"
    if t == "custom":
        return image_style.get("value", "写实风格，8K") or "写实风格，8K"
    return "写实风格，8K，细节丰富"


def optimize_main_character_prompt_with_llm(
    protagonist_attr: Dict,
    global_state: Dict,
    image_style: Dict = None
) -> str:
    """
    使用LLM生成主角形象提示词
    """
    try:
        core_worldview = global_state.get('core_worldview', {})
        user_theme = _safe_str(global_state.get("user_theme")).strip()
        game_theme = core_worldview.get('game_style', '')
        world_setting = core_worldview.get('world_basic_setting', '')
        protagonist_ability = core_worldview.get('protagonist_ability', '')

        protagonist_info = {}
        if 'characters' in core_worldview and '主角' in core_worldview['characters']:
            protagonist = core_worldview['characters']['主角']
            protagonist_info = {
                'personality': protagonist.get('core_personality', ''),
                'appearance': protagonist.get('shallow_background', '')
            }

        game_tone = global_state.get('tone', 'normal_ending')
        tone = TONE_CONFIGS.get(game_tone, TONE_CONFIGS['normal_ending'])
        tone_description = tone.get('name', '普通结局')

        style_description = ''
        if image_style:
            style_type = image_style.get('type', '')
            if style_type == 'realistic':
                style_description = '写实风格，真实细腻，细节丰富'
            elif style_type == 'anime':
                style_description = '动漫风格，日式漫画美学，柔和线条（适中线宽），网点阴影，高对比，戏剧性阴影，含场景与氛围与角色动作'
            elif style_type == 'ink_painting':
                style_description = '水墨画风格，中国传统水墨画，黑白灰调，意境深远'
            elif style_type == 'oil_painting':
                subtype = image_style.get('subtype', 'classic_oil')
                if subtype == 'impressionist':
                    style_description = '印象派油画风格，光影变化丰富，笔触明显'
                elif subtype == 'rococo':
                    style_description = '洛可可风格油画，华丽精致，装饰性强'
                else:
                    style_description = '经典油画风格，厚重质感，色彩丰富'
            elif style_type == 'cyberpunk':
                style_description = '赛博朋克风格，未来科技感，霓虹灯效果，高对比度'
            elif style_type == 'custom':
                style_description = f"自定义风格：{image_style.get('value', '')}"

        attr_description = f"颜值{protagonist_attr.get('颜值', '普通')}，智商{protagonist_attr.get('智商', '普通')}，体力{protagonist_attr.get('体力', '普通')}，魅力{protagonist_attr.get('魅力', '普通')}"
        appearance_level = protagonist_attr.get("颜值", "普通")
        appearance_visual_hint = _get_appearance_hint_for_llm(appearance_level)

        def _build_worldview_context_text() -> str:
            try:
                parts = []
                if core_worldview.get("game_style"):
                    parts.append(f"游戏主题/风格：{_safe_str(core_worldview.get('game_style'))}")
                if core_worldview.get("world_basic_setting"):
                    parts.append(f"世界观基础设定：{_safe_str(core_worldview.get('world_basic_setting'))}")
                if core_worldview.get("main_quest"):
                    parts.append(f"主线任务：{_safe_str(core_worldview.get('main_quest'))}")
                chapters = core_worldview.get("chapters", {})
                if isinstance(chapters, dict) and chapters:
                    chap_lines = []
                    for k in ["chapter1", "chapter2", "chapter3"]:
                        c = chapters.get(k, {}) if isinstance(chapters.get(k, {}), dict) else {}
                        mc = _safe_str(c.get("main_conflict")).strip()
                        if mc:
                            chap_lines.append(f"{k} 核心矛盾：{mc}")
                    if chap_lines:
                        parts.append("章节矛盾：\n" + "\n".join(chap_lines))
                chars = core_worldview.get("characters", {})
                if isinstance(chars, dict) and chars.get("主角"):
                    p = chars.get("主角", {})
                    if isinstance(p, dict):
                        cp = _safe_str(p.get("core_personality")).strip()
                        sb = _safe_str(p.get("shallow_background")).strip()
                        db = _safe_str(p.get("deep_background")).strip()
                        if cp:
                            parts.append(f"主角核心性格：{cp}")
                        if sb:
                            parts.append(f"主角浅层背景：{sb}")
                        if db:
                            parts.append(f"主角深层背景：{_clip_text(db, 600)}")
                return _clip_text("\n".join([x for x in parts if _safe_str(x).strip()]).strip(), 1800)
            except Exception:
                return _clip_text(_safe_str(world_setting), 800)

        worldview_context_text = _build_worldview_context_text()

        canonical = (global_state.get("protagonist_canonical") or {}) if isinstance(global_state.get("protagonist_canonical"), dict) else {}
        name_zh = _safe_str(canonical.get("name_zh")).strip()
        name_en = _safe_str(canonical.get("name_en")).strip()
        work_zh = _safe_str(canonical.get("work_zh")).strip()
        work_en = _safe_str(canonical.get("work_en")).strip()
        canonical_gender = _safe_str(canonical.get("gender")).strip()
        canonical_signature = _safe_str(canonical.get("signature_look_keywords")).strip()

        wiki_ctx = {}
        wiki_evidence_text = ""
        reference_image_url = ""
        try:
            wiki_query = user_theme or game_theme
            wiki_ctx = wiki_lookup_theme_and_character(wiki_query)
            if isinstance(wiki_ctx, dict) and wiki_ctx.get("is_real_world"):
                wiki_evidence_text = _safe_str((wiki_ctx or {}).get("evidence_text")).strip()
                reference_image_url = _safe_str((wiki_ctx or {}).get("reference_image_url")).strip()
        except Exception:
            wiki_ctx = {}
            wiki_evidence_text = ""
            reference_image_url = ""

        if not (name_zh or name_en) and isinstance(wiki_ctx, dict):
            theme_names = (wiki_ctx.get("theme_names") or {}) if isinstance(wiki_ctx.get("theme_names"), dict) else {}
            char_names = (wiki_ctx.get("character_names") or {}) if isinstance(wiki_ctx.get("character_names"), dict) else {}
            work_zh = work_zh or _safe_str(theme_names.get("zh")).strip()
            work_en = work_en or _safe_str(theme_names.get("en")).strip()
            name_zh = name_zh or _safe_str(char_names.get("zh")).strip()
            name_en = name_en or _safe_str(char_names.get("en")).strip()
            if not (name_zh or name_en):
                name_zh = work_zh
                name_en = work_en

        required_name_tokens: List[str] = []
        for t in [name_zh, name_en, work_zh, work_en]:
            t = _safe_str(t).strip()
            if t and t not in required_name_tokens:
                required_name_tokens.append(t)

        _name_part = "/".join([x for x in [name_zh, name_en] if _safe_str(x).strip()]).strip()
        _work_part = "/".join([x for x in [work_zh, work_en] if _safe_str(x).strip()]).strip()
        if _name_part and _work_part:
            identity_hint = f"{_name_part} from {_work_part}"
        else:
            identity_hint = _name_part or _work_part or ""

        if isinstance(global_state, dict) and reference_image_url:
            global_state["_main_character_ref_image_url"] = reference_image_url
        if isinstance(global_state, dict):
            global_state["_main_character_required_name_tokens"] = required_name_tokens

        protagonist_gender = ""
        if canonical_gender and ("男" in canonical_gender or "女" in canonical_gender):
            protagonist_gender = "男性" if "男" in canonical_gender else "女性"
        if not protagonist_gender:
            char_text = " ".join([
                protagonist_info.get("personality", ""),
                protagonist_info.get("appearance", ""),
                _safe_str(core_worldview.get("characters", {}).get("主角", {}).get("deep_background", ""))
            ])
            if char_text.strip():
                protagonist_gender = _infer_gender_from_text(char_text)
        if not protagonist_gender:
            try:
                if wiki_evidence_text:
                    protagonist_gender = _infer_gender_from_text(wiki_evidence_text)
            except Exception:
                pass
        # 不再使用随机性别，避免与剧情文本中的主角性别不一致；若仍缺失则使用默认，与世界观解析侧补全逻辑保持一致
        if not protagonist_gender:
            protagonist_gender = "男性"
            print("⚠️ 主角规范信息中无性别且无法从文本推断，生图使用默认「男性」以与剧情保持一致")

        canonical_block_lines = []
        if name_zh or name_en:
            canonical_block_lines.append(f"主角姓名(中/英)：{name_zh or '—'} / {name_en or '—'}")
        if work_zh or work_en:
            canonical_block_lines.append(f"所属作品(中/英)：{work_zh or '—'} / {work_en or '—'}")
        if protagonist_gender:
            canonical_block_lines.append(f"性别：{protagonist_gender}")
        if canonical_signature:
            canonical_block_lines.append(f"标志性外观关键词：{canonical_signature}")
        canonical_block = "\n".join(canonical_block_lines) if canonical_block_lines else "（无）"

        json_example_main_char = json.dumps(PROMPT_JSON_EXAMPLE_MAIN_CHAR, ensure_ascii=False, indent=2)
        llm_prompt = f"""假设你是一个专业的角色设计师，需要为「主角形象立绘」生成视觉描述提示词。输出与剧情图相同的「精简 JSON 模板」结构，但仅描述人物本身，背景固定为纯白，无任何场景、道具或其它角色。

【游戏背景信息】
- 游戏主题：{user_theme or game_theme}
- 世界观设定（结构化/节选）：{worldview_context_text}
- 游戏基调：{tone_description}

【主角规范信息】（必须优先使用；姓名、性别、标志性外观须在描述中体现）
{canonical_block}

【必须保留的名称标识】{(" / ".join(required_name_tokens)) if required_name_tokens else "（无）"}
【身份提示】{identity_hint if identity_hint else "（无）"}

【主角信息】性别：{protagonist_gender}，属性：{attr_description}，能力：{protagonist_ability}，性格与外貌：{protagonist_info.get('personality', '')}；{protagonist_info.get('appearance', '')}
【颜值视觉要求】等级：{appearance_level}；{appearance_visual_hint}
【Wikipedia 补充】{wiki_evidence_text if wiki_evidence_text else "（无）"}

【图片风格要求】{style_description if style_description else '默认风格'}

要求：
1. 只描述主角一人：外貌、发型、服装、姿势；背景必须为纯白（environment.background 仅纯白，environment.characters 与 effects 为空）。
2. 严格遵循【主角规范信息】的性别、年龄感与标志性外观；【颜值视觉要求】必须在 subject/face 等中体现。
3. 若【必须保留的名称标识】不为"（无）"，在描述中保留这些名称。
4. 符合指定图片风格；禁止任何文字/符号入图。

【输出格式】请严格按照以下「精简 JSON 模板」输出，且仅输出该 JSON，不要其他解释或 markdown。键名必须为英文。数组字段请逐条列出。

JSON 结构：label, tags, style, subject（body_traits, outfit, pose）, face_system, hair_system, clothing_system, environment（background 仅纯白、characters 空数组、effects 空数组）, color_restriction, lighting, camera, composition, mood, output_style。参考示例（按此结构输出，替换为当前主角内容）：
{json_example_main_char}

只输出一个合法的 JSON 对象，不要用 markdown 代码块包裹以外的内容。"""

        api_key = AI_API_CONFIG.get('api_key', '')
        base_url = AI_API_CONFIG.get('base_url', '')

        if not api_key or not base_url:
            print("⚠️ LLM API未配置，使用默认提示词")
            appearance_extra = _get_appearance_english_suffix(protagonist_attr.get("颜值", "普通"))
            return f"全身，主角形象，纯白背景，人物居中站立，{game_theme}风格，{attr_description}{appearance_extra}，{style_description if style_description else '写实风格'}，detailed, high quality, 4k, no text, no symbols"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8"
        }

        request_body = {
            "model": AI_API_CONFIG.get("model", "claude-opus-4-6"),
            "messages": [{"role": "user", "content": llm_prompt}],
            "temperature": 0.7,
            "max_tokens": 2000
        }

        print("🔄 正在使用LLM生成主角形象提示词...")
        response = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=request_body,
            timeout=120
        )
        response.raise_for_status()

        result = response.json()
        choices = result.get("choices", [])
        if choices and len(choices) > 0:
            raw_content = choices[0].get("message", {}).get("content", "").strip()
            if raw_content:
                structured = _parse_json_from_llm_response(raw_content)
                if (
                    structured
                    and isinstance(structured.get("subject"), dict)
                    and isinstance(structured.get("environment"), dict)
                ):
                    optimized_prompt = _build_prompt_from_rich_json(structured)
                    if isinstance(global_state, dict):
                        global_state["_last_main_char_prompt_json"] = structured
                    print("📋 [主角形象] 提示词 JSON：")
                    print(json.dumps(structured, ensure_ascii=False, indent=2))
                else:
                    optimized_prompt = raw_content
                    if isinstance(global_state, dict) and "_last_main_char_prompt_json" in global_state:
                        del global_state["_last_main_char_prompt_json"]
                if optimized_prompt:
                    optimized_prompt = re.sub(r'https?://\S+', '', optimized_prompt).strip()
                    optimized_prompt = re.sub(r'data:image/\S+', '', optimized_prompt).strip()
                    try:
                        if required_name_tokens:
                            missing = [t for t in required_name_tokens if t and t not in optimized_prompt]
                            if missing:
                                optimized_prompt = f"{' / '.join(required_name_tokens)}, {optimized_prompt}"
                        if identity_hint and identity_hint not in optimized_prompt:
                            optimized_prompt = f"{identity_hint}, {optimized_prompt}"
                    except Exception:
                        pass
                    appearance_suffix = _get_appearance_english_suffix(protagonist_attr.get("颜值", "普通"))
                    if appearance_suffix:
                        optimized_prompt = optimized_prompt.rstrip() + appearance_suffix
                    optimized_prompt = f"{optimized_prompt}, no text, no symbols, no garbled characters, no words"
                    print(f"✅ LLM主角形象提示词生成完成，长度：{len(optimized_prompt)}字符")
                    return optimized_prompt

        print("⚠️ LLM生成失败，使用默认提示词")
        appearance_extra = _get_appearance_english_suffix(protagonist_attr.get("颜值", "普通"))
        return f"全身，主角形象，纯白背景，人物居中站立，{game_theme}风格，{attr_description}{appearance_extra}，{style_description if style_description else '写实风格'}，detailed, high quality, 4k, no text, no symbols"

    except Exception as e:
        print(f"⚠️ LLM主角形象提示词生成出错：{str(e)}，使用默认提示词")
        core_worldview = global_state.get('core_worldview', {})
        game_style = core_worldview.get('game_style', '')
        attr_description = f"颜值{protagonist_attr.get('颜值', '普通')}，智商{protagonist_attr.get('智商', '普通')}，体力{protagonist_attr.get('体力', '普通')}，魅力{protagonist_attr.get('魅力', '普通')}"
        appearance_extra = _get_appearance_english_suffix(protagonist_attr.get("颜值", "普通"))
        return f"全身，主角形象，纯白背景，人物居中站立，{game_style}风格，{attr_description}{appearance_extra}，detailed, high quality, 4k, no text, no symbols"
