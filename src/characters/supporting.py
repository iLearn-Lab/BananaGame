# -*- coding: utf-8 -*-
"""配角提取与建档：从提示词提取配角、获取/创建档案、初登场图归档、身份别名更新。"""
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.config import AI_API_CONFIG, IMAGE_GENERATION_CONFIG
from src.llm.api import call_ai_api
from src.characters.paths import ensure_character_references_dir
from src.characters.archives import (
    _load_role_archives,
    _save_role_archives,
    _next_role_id,
    _find_archive_by_name_or_alias,
    _sanitize_filename_for_role,
    _next_img_id,
)
from src.characters.pending_roles import get_and_consume_pending
from src.characters.vision_ref_crop import get_character_bbox_and_crop
from src.utils.text_utils import _safe_str, _clip_text, _extract_core_features_from_prompt


def extract_supporting_characters_in_scene(optimized_prompt: str) -> List[str]:
    """
    从优化后的视觉描述提示词中提取出场的配角槽位列表（仅依据 prompt 中是否出现「配角N」）
    :return: 出场配角槽位列表，按编号排序，如 ["配角1", "配角2"]
    """
    text = _safe_str(optimized_prompt).strip()
    if not text:
        return []
    matches = re.findall(r"配角\d+", text)
    seen = set()
    result = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            result.append(m)
    def sort_key(s):
        n = re.search(r"\d+", s)
        return int(n.group()) if n else 0
    result.sort(key=sort_key)
    return result


def _trim_phrase_to_character_name(name: str, slot: str) -> str:
    """
    提示词里常出现「主角身后是葛城美里-配角1」等句式，正则会误把整句当名字。
    只保留真实角色名：若有「是」则取最后一「是」之后；若仍含「主角」或过长则退回 slot。
    """
    s = _safe_str(name).strip()
    if not s or re.match(r"^配角\d+$", s):
        return slot
    if "是" in s:
        s = s.split("是")[-1].strip()
    if not s or "主角" in s or len(s) > 12:
        return slot
    return s


def extract_supporting_characters_with_names(optimized_prompt: str) -> List[Tuple[str, str]]:
    """
    从优化后的视觉描述提示词中提取出场的配角及角色名。
    :param optimized_prompt: 优化后的视觉描述提示词
    :return: [(display_name, slot), ...]，如 [("凌川", "配角1"), ("李云", "配角2")]
             display_name 从「名称-配角N」解析，无则用 slot 作为 display_name
    """
    text = _safe_str(optimized_prompt).strip()
    if not text:
        return []
    result = []
    seen_slots = set()
    for m in re.finditer(r"([^\s\-]+)\s*[-－]?\s*(配角\d+)(?:\s|$|，|。|、|参考|以|保持)", text):
        name, slot = m.group(1).strip(), m.group(2)
        if slot in seen_slots:
            continue
        seen_slots.add(slot)
        raw_name = name if name and not re.match(r"^配角\d+$", name) else slot
        display_name = _trim_phrase_to_character_name(raw_name, slot)
        result.append((display_name, slot))
    if not result:
        for slot in extract_supporting_characters_in_scene(text):
            result.append((slot, slot))
    def sort_key(item):
        n = re.search(r"\d+", item[1])
        return int(n.group()) if n else 0
    result.sort(key=sort_key)
    return result


def get_or_create_supporting_role_archive(
    game_id: str,
    display_name: str,
    slot: str,
    role_info: Dict,
    first_appear_scene: str,
) -> Dict:
    """
    获取或返回配角档案。若已有档案（按 role_name / aliases 匹配）则返回；否则返回待建档标记。
    初登场图 = 当前剧情图，在剧情图生成成功后由外部调用 archive_supporting_role_first_appearance 保存。
    :return: 若已有档案：含 first_img_path, core_features 等；若首次出场：含 _pending_first_appearance=True, display_name, slot
    """
    archives = _load_role_archives(game_id)
    found = _find_archive_by_name_or_alias(archives, display_name)
    if found:
        role_id, arch = found
        rn = _safe_str(arch.get("role_name", "")).strip()
        aliases = list(arch.get("aliases") or [])
        if display_name != rn and display_name not in aliases:
            aliases.append(display_name)
            arch = dict(arch)
            arch["aliases"] = aliases
            archives[role_id] = arch
            _save_role_archives(game_id, archives)
        first_path = _safe_str(arch.get("first_img_path", "")).strip()
        if first_path:
            ref_dir = ensure_character_references_dir(game_id)
            p = Path(first_path)
            if not p.is_absolute():
                p = ref_dir / Path(first_path).name
            if p.exists():
                arch = dict(arch)
                arch["_resolved_first_img_path"] = str(p.resolve())
                arch["_role_id"] = role_id
                # 若有单人全身参考图，解析路径供生图使用
                face_ref = _safe_str(arch.get("face_ref_path", "")).strip()
                if face_ref:
                    fp = ref_dir / Path(face_ref).name
                    if fp.exists():
                        arch["_resolved_face_ref_path"] = str(fp.resolve())
                return arch
        print(f"⚠️ 配角 {display_name} 档案存在但首图路径无效")
    # 正式出场时：若该角色曾为预配角，取出积累的碎片化特征并合并进待建档项，后续写入正式档案
    pending_data = get_and_consume_pending(game_id, display_name)
    pending_fragments = []
    if pending_data and isinstance(pending_data.get("fragments"), list):
        pending_fragments = pending_data["fragments"]
        if pending_fragments:
            print(f"📋 配角 {display_name} 由预配角正式出场，已取出 {len(pending_fragments)} 条碎片化特征将合并进档案")
    return {
        "_pending_first_appearance": True,
        "display_name": display_name,
        "slot": slot,
        "role_info": role_info,
        "first_appear_scene": first_appear_scene,
        "_pending_fragments": pending_fragments,
    }


def _extract_position_hint(first_prompt: str, first_appear_scene: str) -> str:
    """
    从 first_prompt、first_appear_scene 抽取位置信息，供 vision 裁剪时辅助定位。
    例如：左侧、右侧、第二个人、持某物等。
    """
    text = f"{_safe_str(first_prompt)}\n{_safe_str(first_appear_scene)}".strip()
    if not text or len(text) < 2:
        return ""
    hints = []
    # 左右位置
    if re.search(r"左侧|左边|左側|左邊|靠左|居左|画面左侧|在左侧", text):
        hints.append("该角色在画面左侧")
    elif re.search(r"右侧|右边|右側|右邊|靠右|居右|画面右侧|在右侧", text):
        hints.append("该角色在画面右侧")
    # 顺序：第 N 个人
    m = re.search(r"从左到右\s*第\s*([一二三四五六七八九十\d]+)\s*个", text)
    if m:
        hints.append(f"从左到右第{m.group(1)}个人")
    else:
        m = re.search(r"第\s*([一二三四五六七八九十\d]+)\s*个\s*人", text)
        if m:
            hints.append(f"第{m.group(1)}个人")
    # 持某物、穿某物
    m = re.search(r"(持[^\s，。]+|穿[^\s，。]+(?:者|的人)?)", text)
    if m and len(m.group(1)) <= 20:
        hints.append(m.group(1).strip())
    if not hints:
        return ""
    return "位置参考：" + "；".join(hints[:2])  # 最多取 2 条


def _extract_character_core_from_prompt(prompt: str, display_name: str) -> str:
    """从提示词中提取与某角色相关的核心描述（简化：取含该名的句子或附近上下文）"""
    text = _safe_str(prompt).strip()
    name = _safe_str(display_name).strip()
    if not name or name not in text:
        return ""
    sentences = re.split(r'[。！？\n]', text)
    for s in sentences:
        if name in s:
            return _clip_text(s.strip(), 200)
    idx = text.find(name)
    if idx >= 0:
        start = max(0, idx - 50)
        end = min(len(text), idx + 150)
        return _clip_text(text[start:end], 200)
    return ""


def archive_supporting_role_first_appearance(
    game_id: str,
    pending_item: Dict,
    scene_image_path: str,
    prompt: str,
) -> Optional[Dict]:
    """
    剧情图生成成功后：将当前剧情图保存为配角的初登场图，并建立档案。
    :param pending_item: get_or_create 返回的待建档对象
    :param scene_image_path: 刚生成的剧情图本地路径（如 image_cache/xxx.png）
    :param prompt: 本次生成使用的提示词（用于 first_prompt）
    :return: 新建的 archive，或 None
    """
    if not pending_item.get("_pending_first_appearance"):
        return None
    display_name = _safe_str(pending_item.get("display_name", "")).strip()
    slot = _safe_str(pending_item.get("slot", "")).strip()
    role_info = pending_item.get("role_info") or {}
    first_appear_scene = _safe_str(pending_item.get("first_appear_scene", "")).strip()

    src = Path(scene_image_path)
    if not src.exists():
        print(f"⚠️ 初登场图源文件不存在：{scene_image_path}")
        return None

    ref_dir = ensure_character_references_dir(game_id)
    archives = _load_role_archives(game_id)
    role_id = _next_role_id(archives)
    first_img_id = _next_img_id(ref_dir)
    role_prefix = _sanitize_filename_for_role(display_name)
    first_img_path = ref_dir / f"{role_prefix}_{first_img_id}.png"

    try:
        shutil.copy2(src, first_img_path)
    except Exception as e:
        print(f"⚠️ 保存配角初登场图失败：{e}")
        return None

    first_prompt = _extract_character_core_from_prompt(prompt, display_name) or _clip_text(prompt, 300)
    core_features = _extract_core_features_from_prompt(first_prompt)

    # 预配角正式出场：将出场前积累的碎片化特征合并进正式档案
    story_bg = _safe_str(role_info.get("shallow_background", "")).strip()
    pending_fragments = pending_item.get("_pending_fragments") or []
    if pending_fragments:
        story_bg = (story_bg + "\n\n【出场前碎片积累】\n" + "\n".join(pending_fragments)).strip()
        print(f"   📋 已合并 {len(pending_fragments)} 条碎片化特征进配角档案")

    archive = {
        "role_id": role_id,
        "role_name": display_name,
        "aliases": [display_name],
        "story_background": story_bg,
        "first_appear_scene": first_appear_scene,
        "first_img_id": first_img_id,
        "first_img_path": str(first_img_path.resolve()),
        "first_prompt": first_prompt,
        "img_model": IMAGE_GENERATION_CONFIG.get("yunwu_model", "gemini-3-pro-image-preview"),
        "update_log": [],
        "core_features": core_features,
    }

    # 视觉模型标出该角色在初登场图中的位置并裁成单人全身参考图，便于后续生图时明确「参考谁」
    # 若存在主角参考图，传入以排除与主角相似的人，避免裁到主角
    protagonist_ref = None
    if game_id:
        main_dir = Path("initial") / "main_character" / game_id
        if (main_dir / "main_character.png").exists():
            protagonist_ref = main_dir / "main_character.png"
    face_ref_path_value = None
    appearance_base = f"{first_appear_scene}\n{first_prompt}\n{core_features}".strip() or ""
    position_hint = _extract_position_hint(first_prompt, first_appear_scene)
    appearance_hints = (appearance_base + ("\n" + position_hint if position_hint else "")).strip()
    body_ref_basename = f"{role_prefix}_body_ref.png"
    try:
        _bbox, _cropped_path = get_character_bbox_and_crop(
            first_img_path,
            ref_dir,
            character_name=display_name,
            appearance_hints=appearance_hints,
            body_ref_filename=body_ref_basename,
            protagonist_ref_path=protagonist_ref,
        )
        # 视觉模型返回整图(0,0,1,1)表示找不到人，不将裁剪图作为 body_ref（避免废图当参考）
        _is_full_image_bbox = (
            _bbox
            and abs((_bbox.get("x") or 0)) < 0.01
            and abs((_bbox.get("y") or 0)) < 0.01
            and abs((_bbox.get("width") or 0) - 1) < 0.01
            and abs((_bbox.get("height") or 0) - 1) < 0.01
        )
        if _cropped_path and _cropped_path.exists() and not _is_full_image_bbox:
            face_ref_path_value = _cropped_path.name
            archive["face_ref_path"] = face_ref_path_value
            archive["_resolved_face_ref_path"] = str(_cropped_path.resolve())
            print(f"✅ 配角 {display_name} 已裁出单人全身参考图：{_cropped_path.name}")
            print(f"   📁 保存位置：{_cropped_path.resolve()}")
        elif _is_full_image_bbox:
            print(f"   ⏭️ 视觉模型返回整图 bbox（未找到该角色），跳过 body_ref，将使用整张初登场图作参考")
        elif _bbox is None:
            print(f"   📌 未配置视觉模型，或调用失败（如 503/超时/不支持的请求），跳过单人参考图裁剪（将使用整张初登场图作参考）")
        else:
            print(f"   ⚠️ 视觉模型未返回有效 bbox 或裁剪失败，将使用整张初登场图作参考")
    except Exception as e:
        print(f"⚠️ 配角 {display_name} 单人参考图裁剪失败（将使用整张初登场图）：{e}")

    archives[role_id] = archive
    _save_role_archives(game_id, archives)
    archive["_resolved_first_img_path"] = str(first_img_path.resolve())
    print(f"✅ 配角 {display_name} 初登场图已保存（来自当前剧情图）：{first_img_path}")
    print(f"   📋 新建配角信息：role_id={role_id}, role_name={display_name}, aliases={archive.get('aliases',[])}, first_img={first_img_path.name}")
    return archive


def update_supporting_role_aliases_from_plot(
    game_id: str, scene_description: str, protagonist_names: Optional[set] = None
) -> None:
    """
    每次剧情更新时：从剧情文本中识别身份揭示（如「黑衣人就是艾玛」「A正是B的妹妹」），
    更新对应配角的 aliases。若 orig 或 new_id 属于主角称呼，则跳过，避免将主角称呼加入配角。
    """
    if not game_id or not scene_description or len(scene_description.strip()) < 10:
        return
    archives = _load_role_archives(game_id)
    if not archives:
        return
    api_key = AI_API_CONFIG.get("api_key", "")
    base_url = AI_API_CONFIG.get("base_url", "")
    if not api_key or not base_url:
        return
    protagonist_names = protagonist_names or set()
    existing_aliases = []
    for _rid, arch in archives.items():
        if isinstance(arch, dict):
            for a in (arch.get("aliases") or []):
                if a and a not in existing_aliases:
                    existing_aliases.append(a)
    llm_prompt = f"""从以下剧情中提取「身份揭示」：当剧情明确说明某角色A与另一身份B是同一人时（如「黑衣人就是艾玛」「A原来是B」「A正是B的妹妹」「黑衣人摘下兜帽，竟是灵川」），提取出对应关系。
已知配角称呼：{existing_aliases[:20] if existing_aliases else '（暂无）'}
剧情：
{scene_description[:1500]}

要求：每行输出一条，格式为「原名|新身份」，例如：
黑衣人|艾玛
黑衣人|灵川的妹妹
只输出提取到的身份揭示，无则输出「无」。"""
    try:
        resp = call_ai_api({
            "model": AI_API_CONFIG.get("model", "claude-opus-4-6"),
            "messages": [{"role": "user", "content": llm_prompt}],
            "temperature": 0.2,
            "max_tokens": 300,
        })
        content = (resp.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
        if not content or "无" in content[:10]:
            return
        updated_any = False
        for line in content.split("\n"):
            line = line.strip()
            if "|" not in line or len(line) < 3:
                continue
            parts = line.split("|", 1)
            orig, new_id = parts[0].strip(), parts[1].strip()
            if not orig or not new_id:
                continue
            if protagonist_names and (orig in protagonist_names or new_id in protagonist_names):
                continue  # 主角称呼不入配角别名
            for role_id, arch in archives.items():
                if not isinstance(arch, dict):
                    continue
                aliases = list(arch.get("aliases") or [])
                if orig in aliases or orig == arch.get("role_name"):
                    if new_id not in aliases:
                        aliases.append(new_id)
                        arch["aliases"] = aliases
                        _save_role_archives(game_id, archives)
                        updated_any = True
                        print(f"📋 配角身份更新：{role_id} ({arch.get('role_name')}) 新增别名「{new_id}」")
                        break
        if updated_any:
            print(f"📋 配角档案已更新（身份揭示）")
    except Exception as e:
        print(f"⚠️ 配角身份更新检查失败：{e}")
