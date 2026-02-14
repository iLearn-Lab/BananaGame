# -*- coding: utf-8 -*-
"""LLM 图片提示词优化（场景图、主角形象）。"""
import re
import requests
from typing import Dict, List

from src.config import AI_API_CONFIG
from src.constants import TONE_CONFIGS
from src.utils.text_utils import _safe_str, _clip_text
from src.wiki.lookup import (
    wiki_lookup_theme_and_character,
    _format_protagonist_canonical_for_prompt,
    _infer_gender_from_text,
)


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

        style_description = _get_style_description_from_image_style(image_style) if image_style else ''

        protagonist_reference_section = ""
        if protagonist_reference_images and len(protagonist_reference_images) >= 1:
            n = len(protagonist_reference_images)
            lines = ["【主角参考图说明（重要）】", f"生图API将接收{n}张主角参考图，编号从 Image 0 起："]
            lines.append("- Image 0：主角正面视图（Front view portrait of the protagonist）")
            if n >= 2:
                lines.append("- Image 1：主角侧面视图（Side view portrait of the protagonist）")
            if n >= 3:
                lines.append("- Image 2：主角背面视图（Back view portrait of the protagonist）")
            lines.append("")
            lines.append("在生成场景图片时，根据剧情中主角的视角明确说明主角使用哪张参考图（仅使用已提供的编号）：")
            lines.append("- 正面朝向镜头 → 主角使用 Image 0")
            if n >= 2:
                lines.append("- 侧面朝向镜头 → 主角使用 Image 1")
            if n >= 3:
                lines.append("- 背面朝向镜头 → 主角使用 Image 2")
            if n >= 2:
                lines.append("- 其他角度可写「主角主要参考 Image 0 和 Image 1」等")
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
            supporting_role_reference_section = "\n".join(lines_tag) + "\n"

        protagonist_canonical_block = _format_protagonist_canonical_for_prompt(
            global_state.get("protagonist_canonical") or {}
        )
        canonical = global_state.get("protagonist_canonical") or {}
        protagonist_name = _safe_str(canonical.get("name_zh") or canonical.get("name_en") or "").strip()
        protagonist_identity_warning = f"\n【重要】主角身份：{protagonist_name or '玩家视角主角'}（上述主角规范信息描述的人）。**切勿将主角标注为配角**，只对剧情中出场的**非主角**人物使用「名称-配角N」格式。\n"

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

只输出视觉描述，不要输出其他内容。"""

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
            optimized_prompt = choices[0].get("message", {}).get("content", "").strip()
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


def _get_style_description_from_image_style(image_style: Dict) -> str:
    """从 image_style 提取详细风格描述（含 subtype 支持）"""
    if not image_style or not isinstance(image_style, dict):
        return "写实风格，8K，细节丰富"
    style_type = image_style.get("type", "")
    if style_type == "realistic":
        subtype = image_style.get("subtype", "game_realistic")
        if subtype == "photorealistic":
            return "16K UHD超高清画质，RAW格式无损输出，85mm f1.4定焦镜头，ISO 100，快门1/125s，浅景深虚化（虚化半径2.8px），全局光照（照度1200lux），HDR10+动态范围（峰值亮度1000nit）；面部建模：8K超写实面部拓扑，柔肤滤镜（强度35%），虹膜动态高光追踪（高光直径0.8mm，反射率82%），1.48秒眨眼周期，上眼睑下垂14度，下眼睑微颤0.45HZ；毛发系统：2200+根独立发丝动力学模拟，发根固定系数0.96，发梢飘动幅度7-11cm，发丝摩擦系数0.32；服装材质：棉质面料（弹性模量180GPa，刚性系数9.2），衣物褶皱自然分布（褶皱深度0.3-0.8cm）；环境参数：漫反射柔光（色温5600K），颈部后方点状风源（风速1.1m/s，湍流强度5.5%）；镜头设定：视角固定（水平视角60度），仅头发、衣物随动力学飘动，眼睛按参数完成眨眼动作，无多余动态干扰"
        if subtype == "game_realistic":
            return "UE5.3引擎渲染，光线追踪全局光照（Lumen质量等级超高），Nanite微多边形几何体，4K 120Hz画质，HDR渲染（色域DCI-P3 100%）；场景参数：动态体积雾（密度0.08，散射系数0.12），环境光遮蔽（采样率256x），物理级材质反射（金属反射率92%，玻璃折射率1.52）；人物建模：8K高精度拓扑，皮肤纹理扫描级还原（毛孔直径18-22μm），肌肉动力学模拟（面部52个表情肌独立驱动）；镜头分镜：广角叙事镜头（焦距24mm，水平视角84度），2.39:1宽银幕画幅，电影级运镜（缓慢推镜速度0.8m/s）；动态设定：地面物理摩擦系数0.65，人物行走步幅75cm，手臂摆动幅度30度，衣物随动作自然形变（形变系数0.78）；光影参数：主光色温6500K，补光色温4300K，光比3:1，阴影柔化半径1.2px"
        if subtype == "cinematic_realistic":
            return "IMAX 70mm胶片质感，2.39:1宽银幕画幅，胶片颗粒度ISO 200（颗粒尺寸0.15μm），诺兰式高对比度光影（对比度180:1）；镜头参数：中长焦叙事镜头（焦距50mm，光圈f2.0），黄金分割构图（九宫格参考线开启），镜头畸变校正（畸变系数-0.02）；光影设定：聚光主光（照度1500lux，光斑直径1.2m），环境漫反射（照度350lux），明暗过渡梯度256级；动态参数：人物动作帧率24fps，自然呼吸幅度（胸腔起伏0.8-1.2cm，呼吸频率18次/分钟），面部微表情（嘴角自然上扬0.5度，眼神轻微游动0.3Hz）；材质细节：皮革材质（粗糙度0.25，光泽度75%），织物纹理（纱线密度32根/cm²），金属划痕细节（划痕长度0.5-2cm，深度0.03mm）"
        if subtype == "hyper_realistic":
            return "16K 60Hz超高清，微距级细节渲染（放大400%无模糊），冷军级材质还原；面部细节：皮肤油脂光泽（光泽度68%），毛细血管可见（直径0.02mm，分布密度12根/cm²），唇纹深度0.05mm，唇色渐变（从唇峰到唇角色阶差12级）；毛发细节：眉毛1200+根独立建模，眉毛细度0.08mm，生长方向自然（眉峰向上倾斜12度），胡须生长密度8根/mm²；材质参数：金属材质（镜面反射率98%，划痕反射强度0.7），玻璃材质（透光率92%，折射角1.517），布料材质（纤维直径12μm，编织密度40根/cm²）；光影参数：自然柔光（色温5500K，照度1100lux），无明显阴影（阴影密度0.15），全局反光强度0.2；动态设定：无多余动态，仅面部微表情（眼神眨眼周期1.5秒，鼻翼微颤0.3Hz）"
        return "UE5.3引擎渲染，光线追踪全局光照（Lumen质量等级超高），4K 120Hz画质"
    if style_type == "anime":
        subtype = image_style.get("subtype", "genshin")
        if subtype == "hayao_miyazaki":
            return "4K超清手绘赛璐璐风格，传统手绘笔触（笔刷大小2.5px，笔压强度0.7），水彩晕染效果（晕染半径3.2px，透明度65%）；色彩参数：暖色调（色温4800K），色彩饱和度72%，对比度120:1，色阶过渡256级；线条设定：轮廓线宽度1.8px，线条平滑度95%，内部线条宽度0.8px，线条颜色深棕色（RGB 52,32,18）；分镜参数：中景叙事镜头（焦距35mm，视角75度），平视构图，帧速率24fps，画面抖动幅度0.1px（模拟手绘轻微抖动）；人物细节：眼睛建模（虹膜直径8px，高光点2个，高光大小0.6px），发丝线条（单根宽度0.3px，分组飘动，每组5-8根）；场景细节：草地纹理（草叶长度3-5px，密度15根/cm²），水面波纹（波纹幅度0.5px，频率2Hz），光影设定：漫反射柔光（照度800lux），无明显阴影，氛围光强度0.3"
        if subtype == "makoto_shinkai":
            return "4K HDR超清，极致通透空气感（空气透视强度0.25），丁达尔体积光（光束直径1.5px，强度0.8，色温6200K）；色彩参数：高饱和通透色调（饱和度82%，对比度130:1），天空渐变（从顶部RGB 102,187,255到底部RGB 255,242,224，渐变过渡100级）；镜头参数：广角场景镜头（焦距20mm，视角90度），特写与全景交替（特写镜头焦距85mm，光圈f1.8），镜头光晕（光晕强度0.3，光晕直径5px）；人物细节：面部线条（轮廓线宽度1.5px，线条锐利度90%），眼睛高光（动态高光追踪，随视角移动，高光反射率85%）；粒子特效：樱花粒子（粒子大小0.8-1.2px，下落速度0.5cm/s，旋转速度0.3Hz），光斑粒子（大小0.3-0.5px，亮度0.9）；动态设定：发丝飘动（幅度5-9cm，风源风速0.8m/s），裙摆飘动（形变系数0.8，摆动频率1.2Hz）"
        if subtype == "shonen_jump":
            return "4K高清热血动漫风格，硬朗轮廓线（宽度2.2px，线条锐利度98%，颜色纯黑RGB 0,0,0）；色彩参数：高对比平涂上色（对比度150:1，饱和度85%），阴影平涂（阴影色阶差3级，无渐变）；分镜参数：动态冲击构图（广角镜头焦距18mm，视角95度），速度线特效（速度线宽度1-3px，密度8根/cm，角度45度），动态模糊（模糊强度0.8，模糊方向与动作方向一致）；人物细节：眼睛建模（虹膜直径9px，高光点1个，大小0.8px，眼神锐利），肌肉线条（线条宽度1.2px，阴影加深0.4）；动态参数：战斗动作帧率30fps，肢体摆动幅度（手臂摆动45度，腿部踢动60度），头发飘动（幅度10-15cm，风源风速1.5m/s，湍流强度8%）；特效参数：战斗光效（光效直径5-8px，亮度0.9，颜色渐变RGB 255,0,0到RGB 255,128,0）"
        if subtype == "cyber_anime":
            return "4K赛博朋克动漫，冷峻工业线条（宽度2.0px，线条硬度95%，颜色RGB 0,128,255）；色彩参数：低饱和冷色调（饱和度55%，对比度140:1），霓虹光效点缀（霓虹色RGB 0,255,255、RGB 255,0,255，亮度0.9）；分镜参数：近景特写镜头（焦距60mm，光圈f2.2），俯拍/仰拍交替构图，镜头畸变（畸变系数0.03）；人物细节：机械义体建模（金属线条宽度1.5px，反光强度0.85），皮肤纹理（偏冷色，RGB 220,220,220，粗糙度0.2）；特效参数：故障艺术特效（故障偏移0.5px，频率1.5Hz，颜色分离RGB偏差5级），全息投影（投影分辨率1080p，透明度70%，投影模糊半径0.3px）；动态设定：义体关节转动（转动角度0-180度，转动速度30度/秒），发丝飘动（幅度6-10cm，风源风速1.0m/s）"
        if subtype == "genshin":
            return "4K高精二次元渲染，PBR卡通材质（金属度0.15，粗糙度0.3，高光强度0.8）；色彩参数：通透渐变色调（饱和度78%，对比度125:1），人物肤色渐变（面部RGB 255,230,210到颈部RGB 255,220,200）；镜头参数：中近景人物构图（焦距50mm，视角70度），浅景深虚化（虚化半径2.2px），镜头光晕（强度0.25，颜色RGB 255,255,200）；人物细节：眼睛建模（虹膜直径8.5px，高光点3个，大小0.5-0.7px，动态高光随视角移动），发丝建模（1800+根独立线条，单根宽度0.25px，分组飘动，每组6-9根）；粒子特效：元素粒子（大小0.5-1.0px，亮度0.85，下落速度0.4cm/s），衣物光泽（光泽度70%，反光范围1.5px）；动态设定：呼吸动作（胸腔起伏0.7-1.1cm，频率17次/分钟），发丝飘动（幅度4-8cm，风源风速0.7m/s），裙摆摆动（幅度8-12cm，摆动频率1.0Hz）"
        if subtype == "josei":
            return "4K女性向治愈动漫，纤细柔和线条（宽度1.2px，线条平滑度98%，颜色RGB 80,80,80）；色彩参数：莫兰迪低饱和色调（饱和度60%，对比度100:1），浅色系为主（底色RGB 250,248,245）；分镜参数：抒情慢镜头（帧速率18fps），浅景深氛围（虚化半径3.0px），平视微仰构图（视角5度）；人物细节：面部细节（唇纹深度0.03mm，腮红范围直径1.5cm，颜色RGB 255,180,180，透明度50%），发丝线条（单根宽度0.2px，柔软度0.8，飘动幅度3-6cm）；光影参数：温柔柔光布光（色温4500K，照度700lux），阴影柔化半径1.5px，阴影密度0.1；动态设定：轻微头部晃动（幅度1-2度，频率0.2Hz），眼神缓慢游动（速度0.5px/s），无剧烈动作"
        return "4K高精二次元渲染，PBR卡通材质，通透渐变色调"
    if style_type == "ink_painting":
        subtype = image_style.get("subtype", "shanshui")
        if subtype == "shanshui":
            return "4K高清宣纸水墨，焦浓重淡清五色墨韵（焦墨浓度90%，浓墨75%，重墨60%，淡墨40%，清墨25%）；笔触参数：毛笔笔触（笔刷大小3-8px，笔压强度0.6-0.9，笔触羽化0.5px），晕染效果（晕染半径4-7px，渗透强度0.7）；构图参数：高远+深远结合构图（上半部分高远视角，下半部分深远视角），留白比例35%，画面比例16:9；山水细节：山峰线条（线条宽度2.0-3.5px，苍劲度90%），树木建模（树干线条宽度1.5-2.5px，枝叶笔触大小0.8-1.5px，密度12根/cm²）；光影参数：水墨明暗对比（对比度110:1），无明显色彩，仅墨色层次（256级墨色过渡）；动态设定：无动态，静态水墨，墨色晕染自然，笔触流畅，意境静谧"
        if subtype == "xieyi":
            return "4K大写意水墨，泼墨技法（泼墨范围5-12cm，墨色浓度50-80%，渗透强度0.85）；笔触参数：自由洒脱笔触（笔刷大小4-10px，笔压强度0.4-0.8，笔触无规则羽化0.3-0.6px）；构图参数：极简留白构图（留白比例60%），不对称构图（主体偏左30%）；细节参数：主体笔触（线条宽度3-6px，形神兼备，线条扭曲度15%），墨色层次（焦淡对比强烈，浓墨占比30%，淡墨占比70%）；技法参数：飞白笔触（飞白长度2-5cm，飞白密度0.6），枯笔效果（枯笔占比25%，笔刷干燥度75%）；画面效果：墨色氤氲渗透，笔触自然灵动，无多余细节，气韵生动"
        if subtype == "gongbi":
            return "4K工笔重彩水墨，精细白描线条（线条宽度0.5-1.2px，线条平滑度99%，线条锐利度95%）；墨色参数：淡墨晕染底色（墨色浓度30%，晕染半径2.5px，渗透强度0.6）；色彩参数：淡雅工笔设色（饱和度45%，对比度90:1），肤色RGB 255,240,225，衣纹色彩RGB 180,200,220，透明度80%；细节参数：衣纹线条（每厘米8-10根线条，线条间距0.1-0.2cm），发丝细节（单根线条宽度0.15px，密度10根/mm²），面部细节（眉毛细度0.1px，眼睫毛长度0.8px，密度6根/mm²）；构图参数：对称严谨构图（对称偏差≤1%），主体居中，背景简洁（墨色浓度20%）；画面效果：线条工整流畅，细节精致入微，色彩淡雅高级，古典韵味浓厚"
        if subtype == "wuxia":
            return "4K武侠水墨，飞白笔触（飞白长度3-8cm，飞白密度0.7，笔刷大小2.5-5px）；墨色参数：浓淡对比强烈（浓墨浓度80%，淡墨浓度30%，对比度140:1）；构图参数：侠客剪影构图（主体占画面40%，背景山水占60%），动态倾斜构图（倾斜角度10度）；细节参数：侠客衣袍（线条宽度2.0-3.0px，褶皱深度0.5-1.0cm，飘动幅度10-15cm），刀剑线条（宽度2.5px，锐利度98%，反光强度0.7）；动态参数：刀光剑影特效（线条宽度1.5px，长度5-10cm，亮度0.8，动态模糊强度0.6），侠客动作（身体倾斜30度，衣袖飘动频率1.5Hz）；氛围参数：水墨雾气（密度0.1，模糊半径2.0px），江湖氛围感拉满"
        if subtype == "modern_ink":
            return "4K新中式现代水墨，传统毛笔笔触（笔刷大小2-6px，笔压强度0.5-0.8）+ 现代几何构图（几何图形占比35%）；墨色参数：撞色水墨（墨色浓度60%，撞色RGB 255,100,100、RGB 100,100,255，透明度70%）；线条参数：简约有力线条（宽度1.5-2.5px，线条硬度85%），几何线条（宽度2.0px，直线度99%）；构图参数：几何解构构图（分割比例1:1:2），留白比例25%；细节参数：水墨与撞色融合（融合过渡10级，边缘模糊半径0.8px），现代元素点缀（线条几何图形，大小3-5cm）；光影参数：现代光影（色温5000K，照度900lux），水墨反光强度0.3，撞色区域亮度0.85"
        return "4K高清宣纸水墨，焦浓重淡清五色墨韵，毛笔笔触"
    if style_type == "oil_painting":
        subtype = image_style.get("subtype", "classic_oil")
        if subtype == "impressionist":
            return "4K博物馆级印象派油画，厚涂肌理笔触（笔触厚度0.8-1.5mm，笔触大小3-7px，笔触密度15根/cm²）；颜料参数：莫奈同款颜料（透明度65%，色彩饱和度78%），色彩并置技法（相邻色块色差15级，无明显过渡）；光影参数：自然光漫反射（色温5800K，照度1000lux），光影随时间变幻（光影偏移速度0.1px/s）；构图参数：中景风景构图（主体占画面50%，背景占50%），视角平视，画幅比例4:3；细节参数：水面光影（波纹幅度0.3-0.6cm，光影反射率80%），树叶笔触（大小0.5-1.2px，密度20根/cm²）；画面效果：笔触富有节奏感，色彩朦胧柔和，光影灵动，充满自然诗意，无明显轮廓线"
        if subtype == "rococo":
            return "4K洛可可宫廷油画，细腻光滑笔触（笔触厚度0.3-0.6mm，笔触大小1-3px，笔触密度25根/cm²）；色彩参数：马卡龙粉嫩色调（饱和度70%，对比度100:1），主色调RGB 255,220,230、RGB 240,220,255，背景色RGB 255,250,245；细节参数：繁复卷草纹装饰（花纹大小1-2cm，密度8个/cm²，线条宽度0.5-1.0px），贵族服饰细节（蕾丝纹理密度30根/mm²，珠宝光泽度90%，反光强度0.85）；光影参数：柔光布光（色温4800K，照度850lux），阴影柔化半径2.0px，阴影密度0.15；构图参数：对称优雅构图（对称偏差≤0.5%），主体居中，背景宫廷装饰占比40%；人物细节：肌肤质感（细腻度98%，光泽度75%，毛孔不可见），面部表情柔和（嘴角上扬1.0度，眼神柔和）"
        if subtype == "renaissance":
            return "4K文艺复兴古典油画，多层薄涂技法（涂层厚度0.2-0.4mm，层数8-12层）；色彩参数：沉稳典雅色调（饱和度65%，对比度120:1），主色调RGB 180,150,120、RGB 120,100,80，明暗过渡256级；构图参数：黄金比例构图（比例1:1.618），宗教/古典人物题材，主体占画面55%；人物细节：面部建模（8K精度，肌肉线条自然，表情庄重），衣物纹理（丝绸材质，光泽度85%，褶皱深度0.5-1.2cm）；光影参数：柔和明暗过渡（chiaroscuro明暗对比150:1），主光照度1200lux，补光照度400lux；画面效果：博物馆级质感，庄重典雅，充满古典艺术神圣感，笔触细腻无明显痕迹"
        if subtype == "baroque":
            return "4K巴洛克油画，伦勃朗式强烈明暗对比（对比度200:1），厚重油彩肌理（笔触厚度1.0-2.0mm，笔触大小4-8px）；光影参数：戏剧性聚光光影（主光光斑直径1.5m，照度1800lux，补光照度250lux）；构图参数：动态张力构图（主体倾斜15度，视线引导线指向主体），画幅比例3:4；细节参数：人物衣物（天鹅绒材质，粗糙度0.2，光泽度88%），金属装饰（反射率95%，划痕细节0.3-0.8cm）；动态氛围：画面充满戏剧冲突，人物表情夸张（眼神锐利，嘴角紧绷），光影塑造强烈情绪；笔触效果：笔触浑厚有力，油彩堆积明显，画面立体感极强"
        if subtype == "abstract_oil":
            return "4K立体主义抽象油画，毕加索风格几何解构（几何图形占比80%，图形类型：三角形、圆形、矩形）；色彩参数：强烈色彩碰撞（饱和度90%，对比度180:1），主色调RGB 255,0,0、RGB 0,0,255、RGB 255,255,0，色块边缘锐利；笔触参数：粗犷豪放笔触（笔触厚度0.8-1.8mm，笔触大小5-10px，笔触方向无规则）；构图参数：多维视角构图（俯视+平视结合，视角偏差30度），画面分割比例2:3:5；细节参数：色块叠加（叠加层数5-8层，透明度50-70%），线条交织（线条宽度2.0-3.0px，密度10根/cm）；画面效果：先锋艺术感拉满，视觉冲击力强，无明确主体，自由艺术表达"
        return "4K经典古典油画，厚重油彩肌理（笔触厚度0.7-1.3mm，笔触大小2-6px）；光影参数：伦勃朗光影塑造（明暗对比160:1），主光色温5200K，照度1100lux；色彩参数：浓郁沉稳色调（饱和度70%，对比度130:1），色彩过渡自然（256级色阶）；细节参数：人物面部（8K精度，皮肤纹理细腻，毛孔直径20μm），风景细节（树木纹理密度18根/cm²，岩石纹理深度0.5-1.0mm）；技法参数：多层覆涂技法（涂层厚度0.3-0.5mm，层数6-10层）；画面效果：博物馆收藏级质感，光影立体饱满，构图严谨典雅，艺术厚重感极强"
    if style_type == "cyberpunk":
        subtype = image_style.get("subtype", "night_city")
        if subtype == "night_city":
            return "16K UHD赛博朋克2077夜之城风格，UE5.3光线追踪渲染（Lumen质量超高，光线反弹次数8次）；环境参数：雨夜湿滑地面（反射率90%，反光模糊半径0.3px，积水深度0.2-0.5cm），密集霓虹全息广告（分辨率4K，亮度1200nit，闪烁频率2.5Hz）；色彩参数：高对比荧光撞色（主色RGB 0,255,255、RGB 255,0,255，饱和度95%，对比度200:1）；细节参数：机械义体（金属材质反射率95%，关节转动角度0-180度，纹理划痕0.3-1.0cm）；特效参数：故障艺术特效（故障偏移0.8px，频率2.0Hz，RGB颜色分离8级），体积雾（密度0.12，散射系数0.15，色温6500K）；镜头参数：广角都市分镜（焦距20mm，视角90度），2.39:1宽银幕画幅，镜头畸变系数0.04；动态参数：行人动作（步幅65cm，行走速度1.2m/s），全息投影动态（播放帧率30fps，透明度75%）"
        if subtype == "blade_runner":
            return "4K复古未来赛博朋克，银翼杀手2049风格，低饱和冷灰底色（饱和度45%，对比度170:1）；色彩参数：霓虹蓝橙撞色（RGB 0,128,255、RGB 255,128,0，亮度0.85）；环境参数：潮湿空气体积光（光束直径2.0px，强度0.7，密度0.1），巨型全息投影（分辨率2K，投影尺寸5×8m，透明度80%）；镜头参数：慢镜头叙事分镜（帧速率12fps），超广角城市构图（焦距16mm，视角100度），胶片颗粒感（ISO 400，颗粒尺寸0.2μm）；细节参数：城市肌理（破败墙面划痕3-8cm，墙面粗糙度0.8），地面反光（反射率75%，模糊半径0.5px）；动态参数：缓慢运镜（推镜速度0.3m/s），雨滴下落（速度5m/s，雨滴大小0.3-0.5mm，密度20滴/cm²）；氛围参数：反乌托邦压抑氛围，画面冷峻疏离，未来孤独感拉满"
        if subtype == "anime_cyber":
            return "4K日式赛博朋克动画，阿基拉+攻壳机动队画风，手绘线条+未来科技元素（线条宽度1.8px，锐利度95%）；色彩参数：暗调冷色为主（饱和度50%，对比度150:1），霓虹光条点缀（RGB 0,255,255，宽度1.5px，亮度0.9）；场景细节：东京新宿街头（楼宇高度80-120m，广告牌密度12个/100m²，中式+日文牌匾）；镜头参数：动态街头分镜（帧速率24fps），近景跟拍（焦距35mm，跟拍速度1.5m/s）；人物细节：机甲建模（金属线条宽度1.2px，反光强度0.8），人物服装（皮质材质，粗糙度0.25，褶皱深度0.4-0.9cm）；特效参数：霓虹光效（光晕半径2.0px，闪烁频率1.8Hz），烟雾特效（密度0.08，模糊半径1.5px）；动态参数：机甲关节转动（速度45度/秒），人物行走步幅70cm"
        if subtype == "dystopian":
            return "4K反乌托邦废土赛博朋克，破败未来城市肌理（楼宇破损率40%，墙面裂缝宽度0.5-2.0cm，废墟堆积高度5-10m）；色彩参数：灰调压抑底色（饱和度35%，对比度160:1），少量霓虹破碎光效（RGB 255,0,0，亮度0.7，闪烁频率1.2Hz）；镜头参数：长焦纪实分镜（焦距70mm，光圈f2.8），俯拍构图（视角45度向下）；细节参数：废墟质感（金属锈蚀程度60%，锈蚀纹理密度8根/cm²，布料破损面积20-30%）；环境参数：灰雾氛围（密度0.15，模糊半径2.5px），照度500lux，色温4000K；动态参数：人物动作（缓慢行走，步幅55cm，速度0.8m/s），废墟晃动（幅度0.3px，频率0.5Hz）；画面效果：充满末世苍凉与未来绝望感，阶级对比强烈，细节破败真实"
        if subtype == "neon_asia":
            return "4K东方霓虹赛博朋克，香港九龙寨城+东京涩谷融合，中式牌匾（字体大小3-5cm，字体风格宋体）+ 日文霓虹（字体大小2-4cm）；色彩参数：暖黄与冷紫撞色（RGB 255,200,100、RGB 128,0,255，饱和度85%，对比度180:1）；环境参数：潮湿巷弄（地面反射率85%，积水深度0.1-0.3cm，巷弄宽度2-3m）；细节参数：市井细节（商铺招牌密度15个/m，杂物堆积高度0.5-1.5m，线缆密度8根/m）；镜头参数：近景街头构图（焦距40mm，视角75度），镜头光晕（强度0.3，颜色RGB 255,200,100）；特效参数：霓虹光晕（半径3.0px，透明度70%），蒸汽特效（密度0.1，速度0.5m/s，温度色偏暖黄）；动态参数：行人动作（步幅60cm，速度1.0m/s），霓虹闪烁（频率2.2Hz），东方元素与未来科技完美融合"
        return "16K UHD赛博朋克2077夜之城风格，UE5.3光线追踪渲染"
    if style_type == "custom":
        v = image_style.get("value", "") or ""
        return f"自定义风格：{v}" if v else "写实风格，8K"
    return "写实风格，8K，细节丰富"


def _get_style_description(image_style: Dict) -> str:
    """从 image_style 提取风格描述（兼容旧接口，调用详细版）"""
    return _get_style_description_from_image_style(image_style)


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

        style_description = _get_style_description_from_image_style(image_style) if image_style else ''

        attr_description = f"颜值{protagonist_attr.get('颜值', '普通')}，智商{protagonist_attr.get('智商', '普通')}，体力{protagonist_attr.get('体力', '普通')}，魅力{protagonist_attr.get('魅力', '普通')}"

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
        if not protagonist_gender:
            import random
            protagonist_gender = random.choice(['男性', '女性'])

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

        llm_prompt = f"""你现在是一个专业的角色设计师，要将具体角色描述给生图ai，让生图ai能够生成准确的主角形象。

【游戏背景信息】
- 游戏主题：{user_theme or game_theme}
- 世界观设定（结构化/节选）：{worldview_context_text}
- 游戏基调：{tone_description}

【主角规范信息】（来自世界观，必须优先使用；姓名、性别、外观关键词须在最终提示词中体现）
{canonical_block}

【Wikipedia 检索补充】（如存在，可补充细节与参考图；有参考图时会传给生图模型）
{wiki_evidence_text if wiki_evidence_text else "（无）"}

【必须保留的名称标识】（必须在最终提示词中原样保留）
{(" / ".join(required_name_tokens)) if required_name_tokens else "（无）"}

【身份提示】（请在最终提示词中显式出现，保持原样）
{identity_hint if identity_hint else "（无）"}

【主角信息】
- 主角性别：{protagonist_gender}
- 主角属性：{attr_description}
- 主角能力：{protagonist_ability}
- 主角性格：{protagonist_info.get('personality', '')}
- 主角背景：{protagonist_info.get('appearance', '')}

【图片风格要求】
{style_description if style_description else '默认风格'}

请根据以上信息，生成一个详细的主角形象描述提示词，要求：
1. 必须优先使用【主角规范信息】中的姓名、性别与标志性外观关键词；若【Wikipedia 检索补充】存在，可补充细节；若有参考图，生图阶段会使用参考图以提高还原度。
2. 主角性别为{protagonist_gender}，请根据性别特征进行描述。
3. 详细描述主角的外貌特征（面部特征、五官、肤色、表情等），并融入【主角规范信息】中的标志性外观关键词（若有）。
4. 若【必须保留的名称标识】不为"（无）"，最终提示词中必须包含这些名称（原样保留，不要用同义词替换）。
5. 详细描述主角的穿着与发型；体现主角属性特点；符合游戏主题、世界观与基调；符合指定图片风格。
6. 强调全身角色设计（full-body），纯白背景，人物居中站立；禁止生成任何文字/符号/乱码（no text, no symbols, no words）。

只输出视觉描述，不要输出其他内容。"""

        api_key = AI_API_CONFIG.get('api_key', '')
        base_url = AI_API_CONFIG.get('base_url', '')

        if not api_key or not base_url:
            print("⚠️ LLM API未配置，使用默认提示词")
            return f"全身，主角形象，纯白背景，人物居中站立，{game_theme}风格，{attr_description}，{style_description if style_description else '写实风格'}，detailed, high quality, 4k, no text, no symbols"

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
            optimized_prompt = choices[0].get("message", {}).get("content", "").strip()
            if optimized_prompt:
                try:
                    if required_name_tokens:
                        missing = [t for t in required_name_tokens if t and t not in optimized_prompt]
                        if missing:
                            optimized_prompt = f"{' / '.join(required_name_tokens)}, {optimized_prompt}"
                    if identity_hint and identity_hint not in optimized_prompt:
                        optimized_prompt = f"{identity_hint}, {optimized_prompt}"
                except Exception:
                    pass
                optimized_prompt = f"{optimized_prompt}, full body, standing pose, arms relaxed at sides, pure white background, character centered, no text, no symbols, no garbled characters, no words"
                print(f"✅ LLM主角形象提示词生成完成，长度：{len(optimized_prompt)}字符")
                return optimized_prompt

        print("⚠️ LLM生成失败，使用默认提示词")
        return f"全身，主角形象，纯白背景，人物居中站立，{game_theme}风格，{attr_description}，{style_description if style_description else '写实风格'}，detailed, high quality, 4k, no text, no symbols"

    except Exception as e:
        print(f"⚠️ LLM主角形象提示词生成出错：{str(e)}，使用默认提示词")
        core_worldview = global_state.get('core_worldview', {})
        game_style = core_worldview.get('game_style', '')
        attr_description = f"颜值{protagonist_attr.get('颜值', '普通')}，智商{protagonist_attr.get('智商', '普通')}，体力{protagonist_attr.get('体力', '普通')}，魅力{protagonist_attr.get('魅力', '普通')}"
        return f"全身，主角形象，纯白背景，人物居中站立，{game_style}风格，{attr_description}，detailed, high quality, 4k, no text, no symbols"
