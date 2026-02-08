# -*- coding: utf-8 -*-
"""配角档案读写：加载/保存、role_id、按名称查找、文件名安全、图片ID。"""
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from src.characters.paths import SUPPORTING_ROLE_ARCHIVES_FILE, ensure_character_references_dir
from src.utils.text_utils import _safe_str


def _load_role_archives(game_id: str) -> Dict:
    """加载配角档案。兼容旧格式（key=配角1）与新格式（key=role_001）"""
    ref_dir = ensure_character_references_dir(game_id)
    archive_path = ref_dir / SUPPORTING_ROLE_ARCHIVES_FILE
    if archive_path.exists():
        try:
            with open(archive_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            print(f"⚠️ 加载配角档案失败：{e}")
            return {}
        result = {}
        for i, (k, v) in enumerate(raw.items()):
            if not isinstance(v, dict):
                continue
            role_id = v.get("role_id") or (f"role_{i+1:03d}" if not re.match(r"^role_\d+$", str(k)) else str(k))
            v = dict(v)
            v["role_id"] = role_id
            if "aliases" not in v:
                rn = _safe_str(v.get("role_name", "")).strip()
                v["aliases"] = [rn] if rn else []
            result[role_id] = v
        return result
    return {}


def _save_role_archives(game_id: str, archives: Dict) -> None:
    """保存配角档案"""
    ref_dir = ensure_character_references_dir(game_id)
    archive_path = ref_dir / SUPPORTING_ROLE_ARCHIVES_FILE
    try:
        with open(archive_path, "w", encoding="utf-8") as f:
            json.dump(archives, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 保存配角档案失败：{e}")


def _next_role_id(archives: Dict) -> str:
    """生成下一个角色ID：role_001, role_002... 唯一标识，避免重名"""
    max_num = 0
    for _key, data in archives.items():
        rid = _safe_str(data.get("role_id", "")).strip()
        m = re.match(r"role_(\d+)", rid)
        if m:
            max_num = max(max_num, int(m.group(1)))
    return f"role_{max_num + 1:03d}"


def _find_archive_by_name_or_alias(archives: Dict, display_name: str) -> Optional[Tuple[str, Dict]]:
    """
    根据角色名或别号在档案中查找。
    :return: (role_id, archive) 或 None
    """
    dn = _safe_str(display_name).strip()
    if not dn:
        return None
    for role_id, arch in archives.items():
        if not isinstance(arch, dict):
            continue
        rn = _safe_str(arch.get("role_name", "")).strip()
        if rn == dn:
            return (role_id, arch)
        aliases = arch.get("aliases", [])
        if isinstance(aliases, list) and dn in aliases:
            return (role_id, arch)
    return None


def _sanitize_filename_for_role(s: str) -> str:
    """将角色名转为可安全用于文件名的前缀（去掉 / \\ : * ? \" < > | 等非法字符）"""
    s = _safe_str(s).strip()
    s = re.sub(r'[\s/\\:*?"<>|]+', '_', s)
    return s.strip('_') or "role"


def _next_img_id(ref_dir: Path) -> str:
    """生成首次出场图片ID：img_{YYYYMMDD}_{序号}"""
    date_str = datetime.now().strftime("%Y%m%d")
    prefix = f"img_{date_str}_"
    max_num = 0
    for p in ref_dir.glob(f"{prefix}*.png"):
        name = p.stem
        try:
            n = int(name[len(prefix):])
            max_num = max(max_num, n)
        except (ValueError, IndexError):
            pass
    for p in ref_dir.glob("*_" + prefix + "*.png"):
        stem = p.stem
        idx = stem.rfind("_" + prefix)
        if idx != -1:
            try:
                n = int(stem[idx + len(prefix) + 1:])
                max_num = max(max_num, n)
            except (ValueError, IndexError):
                pass
    return f"{prefix}{max_num + 1:03d}"
