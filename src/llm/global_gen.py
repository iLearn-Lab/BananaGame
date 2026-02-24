# -*- coding: utf-8 -*-
"""LLM 全局世界观生成：llm_generate_global、_get_default_worldview。"""
import json
import re
import threading
from typing import Dict

from src.config import AI_API_CONFIG
from src.constants import PERFORMANCE_OPTIMIZATION, TONE_CONFIGS, get_tone_brief_for_worldview
from src.llm.api import call_ai_api
from src.llm.council_core import run_full_council_sync
from src.wiki.lookup import _infer_gender_from_text
from src.worldview.parser import _regex_fill_worldview
from src.worldview.template import _background_fill_worldview_details


def llm_generate_global(user_idea: str, protagonist_attr: Dict, difficulty: str, tone_key: str = "normal_ending", force_full: bool = False) -> Dict:
    """调用yunwu.ai生成包含章节矛盾、适配主角属性/难度的Global世界观

    force_full: True 时跳过分阶段/模板/缓存，加速生成完整版本（用于后台补全）
    """
    if not user_idea.strip():
        raise ValueError("游戏主题idea不能为空")

    perf = PERFORMANCE_OPTIMIZATION
    perf_enabled = perf.get("enabled", True)
    staged_mode = perf_enabled and perf.get("staged_worldview", True) and not force_full


    # 环境变量验证：检查必填字段是否齐全
    required_configs = ["api_key", "base_url", "model"]
    missing_configs = [config for config in required_configs if not AI_API_CONFIG.get(config)]
    if missing_configs:
        config_names = {
            "api_key": "Camera_Analyst_API_KEY",
            "base_url": "Camera_Analyst_BASE_URL",
            "model": "Camera_Analyst_MODEL"
        }
        missing_env_names = [config_names.get(c, c) for c in missing_configs]
        print(f"❌ 错误：缺少必要的API配置，请在.env文件中设置：{', '.join(missing_env_names)}")
        print("💡 提示：将使用默认世界观继续游戏（非AI生成）")
        print("💡 如需使用AI生成，请配置.env文件中的以下环境变量：")
        for env_name in missing_env_names:
            print(f"   - {env_name}")
        # 抛出异常，让后端能够返回错误信息给前端
        raise ValueError(f"缺少必要的API配置：{', '.join(missing_env_names)}。请在.env文件中配置这些环境变量以启用AI生成功能。")

    # 获取基调配置
    tone = TONE_CONFIGS.get(tone_key, TONE_CONFIGS["normal_ending"])

    # 修改Prompt：根据配置选择核心版或完整版
    if staged_mode:
        prompt = f"""
        你是资深游戏编剧，请严格贴合基调：{get_tone_brief_for_worldview(tone)}，生成【核心世界观速写】。要求：中文输出、无代码块、无多余解释，简洁凝练且覆盖全部关键要素。

        【核心世界观】

        1. 游戏风格：不少于60字，明确视觉风格与玩法调性，贴合基调。

        2. 世界观基础设定：不少于250字，涵盖背景/历史/地理/社会/文化/关键事件，逻辑连贯。

        3. 主角核心能力：不少于80字；可无能力（需写明后期解锁契机与限制），若有能力需写来源、使用方式与限制。

        【主线任务】

        游戏主线任务：不少于150字，说明核心目标、关键步骤与核心挑战，贴合世界观与基调。

        【章节设定】

        第1章：

        - 核心矛盾：不少于80字，贴合章节与主线。

        - 矛盾结束条件：不少于60字，明确可落地。

        第2章：

        - 核心矛盾：不少于80字，承接上章，深化冲突。

        - 矛盾结束条件：不少于60字。

        第3章：

        - 核心矛盾：不少于80字，聚焦主线核心冲突。

        - 矛盾结束条件：不少于60字。

        【主角规范信息】（仅内部使用，必填）

        主角姓名：（如碇真嗣）

        性别：男性/女性

        年龄感：少年/青年/中年/其他

        所属作品(中)：(如新世纪福音战士）

        标志性外观关键词：（6–12条逗号分隔）

        【初始世界线】

        当前章节：chapter1（第一章）

        主线进度：初始阶段（未开启核心主线任务，仅触发入门剧情）

        章节矛盾：第一章核心矛盾未触发/未解决

        ## 【输入数据】
        - 主题：{user_idea}
        - 主角属性：{json.dumps(protagonist_attr, ensure_ascii=False)}
        - 难度：{difficulty}
        - 基调：{tone['name']}
        """
    else:
        prompt = f"""
        你是拥有25年以上游戏核心剧情及世界观创作经验的资深游戏编剧，擅长构建逻辑严谨、情感饱满的游戏世界与主角成长线，精通各类游戏风格的剧情适配，请严格贴合基调：{get_tone_brief_for_worldview(tone)}，生成【完整文本冒险游戏世界观】，要求中文输出、无代码块、无多余解释，按分隔符输出且字段齐全，为首轮选项提供充足背景信息，具体要求如下：

        【核心世界观】

        1. 游戏风格：不少于80字，需明确游戏视觉风格、核心玩法调性，贴合整体基调，体现游戏核心气质。

        2. 世界观基础设定：不少于320字，需完整涵盖世界背景、核心历史脉络、关键地理节点、社会结构、文化特质及推动世界格局变化的关键事件，逻辑连贯，为首轮选项提供足够信息。

        3. 主角核心能力：不少于100字，需明确能力的来源、使用方式与限制，避免设定过于全能。

        【角色设定】

        主角：核心性格不少于70字；浅层背景不少于120字；深层背景不少于250字（含主线相关秘密）。

        配角1：核心性格不少于70字；浅层背景不少于120字；深层背景不少于250字。

        【势力设定】

        正派势力：每个不少于50字；反派势力：每个不少于50字；中立势力：每个不少于50字。

        【主线任务】

        游戏主线任务：不少于180字，清晰说明主线核心目标、关键推进步骤与核心挑战，紧密贴合世界观与基调。

        【章节设定】

        第1章：

        - 核心矛盾：不少于90字，贴合章节场景与主线，符合整体基调。

        - 矛盾结束条件：不少于70字，明确可落地，推动主线进度。

        第2章：

        - 核心矛盾：不少于90字，承接上一章，深化主线冲突。

        - 矛盾结束条件：不少于70字，推动主线向核心目标迈进。

        第3章：

        - 核心矛盾：不少于90字，聚焦主线核心冲突，确保剧情连贯。

        - 矛盾结束条件：不少于70字，为后续铺垫，符合世界观设定。

        【游戏结束触发条件】

        游戏结束触发条件：不少于90字，明确可判定，贴合世界观与主线目标。

        【主角规范信息】（仅内部使用，不展示给玩家；必填，用于后续主角形象生成）

        主角姓名：（如碇真嗣）

        性别：男性/女性

        年龄感：少年/青年/中年/其他

        所属作品(中)：(如新世纪福音战士）

        标志性外观关键词：（6–12条，逗号分隔，贴合世界观及主角身份，如黑色短发、破损披风、银质手钏、清冷眼神、伤疤、工装靴）

        【初始世界线】

        当前章节：chapter1（第一章）

        角色初始状态：主角/配角1的想法、身体状态、深层背景解锁、深度

        环境初始状态：天气/位置/势力关系

        主线进度：初始阶段（未开启核心主线任务，仅触发入门剧情）

        章节矛盾：第一章核心矛盾未触发/未解决

        ## 【输入数据】
        - 主题：{user_idea}
        - 主角属性：{json.dumps(protagonist_attr, ensure_ascii=False)}
        - 难度：{difficulty}
        - 基调：{tone['name']}
        - 任务：为首轮2个选项提供充足背景信息
        """

    # 构建请求体，不强制要求JSON格式
    worldview_tokens = 5000
    if perf_enabled and perf.get("optimize_tokens", True):
        worldview_tokens = perf.get("worldview_max_tokens", 3500)
        if staged_mode:
            worldview_tokens = min(worldview_tokens, 2200)
    request_body = {
        "model": AI_API_CONFIG["model"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.32 if staged_mode else 0.3,
        "max_tokens": worldview_tokens,
        "top_p": 0.6,
        "frequency_penalty": 0.4,
        "presence_penalty": 0.15,
        "timeout": 160 if staged_mode else 200
    }

    # 内部重试机制，最多尝试3次生成和解析
    max_retries = 3
    if perf_enabled and perf.get("optimize_retry", True):
        max_retries = perf.get("worldview_max_retries", 2)
    for attempt in range(max_retries):
        try:
            print(f"📝 尝试生成世界观（第{attempt+1}/{max_retries}次）...")
            # 完整版用 council 群体智能；核心速写仍用单模型
            if staged_mode:
                response_data = call_ai_api(request_body)
                choices = response_data.get("choices", [])
                if not choices or len(choices) == 0:
                    print("❌ 错误：AI返回内容格式异常，缺少choices字段，将重试...")
                    continue
                message = choices[0].get("message", {})
                if not message:
                    print("❌ 错误：AI返回内容格式异常，缺少message字段，将重试...")
                    continue
                raw_content = message.get("content", "").strip()
            else:
                raw_content = run_full_council_sync(prompt)
            if not raw_content:
                print("❌ 错误：AI返回内容为空，将重试...")
                continue

            # 直接从文本中提取信息，不依赖JSON解析
            global_state = {}

            # 初始化核心世界观和世界线
            global_state['core_worldview'] = {}
            global_state['flow_worldline'] = {}

            # 处理原始文本
            lines = raw_content.split('\n')

            # 提取核心世界观
            core_section = False
            core_worldview = {}
            characters = {}
            forces = {}
            chapters = {}

            current_section = ""
            current_character = ""
            current_chapter = ""
            current_field = None  # 当前正在收集的字段
            current_field_content = []  # 当前字段的内容（支持多行）
            current_conflict_content = []  # 当前章节核心矛盾的内容（支持多行）
            current_end_condition_content = []  # 当前章节矛盾结束条件的内容（支持多行）
            # 主角规范信息（仅内部使用，不展示给玩家）
            in_canonical_section = False
            protagonist_canonical = {}
            _canonical_key_map = [
                ("主角姓名", "name_zh"), ("主角姓名(中)", "name_zh"), ("主角姓名(英)", "name_en"), ("性别", "gender"),
                ("年龄感", "age_range"), ("所属作品(中)", "work_zh"), ("所属作品(英)", "work_en"),
                ("标志性外观关键词", "signature_look_keywords")
            ]
            current_canonical_key = None
            current_canonical_content = []

            def _extract_after_key(text: str, key: str) -> str:
                """兼容全角/半角冒号提取键后内容"""
                for sep in ("：", ":"):
                    full = key + sep
                    if full in text:
                        return text.split(full, 1)[1].strip()
                return ""

            for line_idx, line in enumerate(lines):
                original_line = line
                line = line.strip()
                if not line:
                    if current_field and current_field_content:
                        continue
                    else:
                        continue

                # 检测章节（兼容带 ## 或不带 ##、有无【】的标题）
                if (line.startswith('## 【核心世界观】') or line.startswith('【核心世界观】')
                    or line.startswith('## 核心世界观') or line.strip() == '核心世界观'):
                    core_section = True
                    continue
                elif line.startswith('## 【初始世界线】') or line.startswith('【初始世界线】'):
                    # 退出主角规范信息前先保存未刷新的字段
                    if in_canonical_section and current_canonical_key and current_canonical_content:
                        protagonist_canonical[current_canonical_key] = ' '.join(current_canonical_content).strip().replace('**', '').replace('*', '')
                    # 保存最后一个字段的内容
                    if current_field and current_field_content:
                        content = ' '.join(current_field_content).strip()
                        content = content.replace('**', '').replace('*', '')
                        if content:  # 只有非空内容才保存
                            core_worldview[current_field] = content
                    core_section = False
                    break
                # 主角规范信息区块（仅内部使用）
                if core_section and line.startswith('### 【主角规范信息】'):
                    in_canonical_section = True
                    current_canonical_key = None
                    current_canonical_content = []
                    continue
                if in_canonical_section:
                    if line.startswith('### 【') and '主角规范信息' not in line:
                        in_canonical_section = False
                        if current_canonical_key and current_canonical_content:
                            protagonist_canonical[current_canonical_key] = ' '.join(current_canonical_content).strip().replace('**', '').replace('*', '')
                        current_canonical_key = None
                        current_canonical_content = []
                    else:
                        matched = False
                        for cn_key, en_key in _canonical_key_map:
                            # 兼容全角冒号「：」与半角冒号「:」，避免 LLM 输出半角时性别等未被解析
                            if line.startswith(cn_key + "：") or line.startswith(cn_key + ":"):
                                if current_canonical_key and current_canonical_content:
                                    protagonist_canonical[current_canonical_key] = ' '.join(current_canonical_content).strip().replace('**', '').replace('*', '')
                                val = line[len(cn_key) + 1:].strip()
                                if val:
                                    protagonist_canonical[en_key] = val.replace('**', '').replace('*', '')
                                    current_canonical_key = None
                                    current_canonical_content = []
                                else:
                                    current_canonical_key = en_key
                                    current_canonical_content = []
                                matched = True
                                break
                        if not matched and current_canonical_key and line and not line.startswith('##'):
                            current_canonical_content.append(line)
                    if in_canonical_section:
                        continue

                if core_section:
                    # 检测子章节
                    if line.startswith('### 【'):
                        # 保存上一个字段的内容
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            core_worldview[current_field] = content
                            current_field_content = []
                        current_section = line
                        current_field = None
                        continue

                    # 提取基本信息（支持多行内容，兼容全角/半角冒号）
                    if "游戏风格" in line and ("：" in line or ":" in line):
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:
                                core_worldview[current_field] = content
                        current_field = 'game_style'
                        part = _extract_after_key(line, "游戏风格")
                        current_field_content = [part] if part else []
                    elif "世界观基础设定" in line and ("：" in line or ":" in line):
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:
                                core_worldview[current_field] = content
                        current_field = 'world_basic_setting'
                        part = _extract_after_key(line, "世界观基础设定")
                        current_field_content = [part] if part else []
                    elif "主角核心能力" in line and ("：" in line or ":" in line):
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:
                                core_worldview[current_field] = content
                        current_field = 'protagonist_ability'
                        part = _extract_after_key(line, "主角核心能力")
                        current_field_content = [part] if part else []
                    elif "游戏主线任务：" in line:
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:
                                core_worldview[current_field] = content
                            current_field = None
                            current_field_content = []
                        core_worldview['main_quest'] = line.split("游戏主线任务：")[1].strip()
                    elif "游戏结束触发条件：" in line:
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:
                                core_worldview[current_field] = content
                            current_field = None
                            current_field_content = []
                        core_worldview['end_trigger_condition'] = line.split("游戏结束触发条件：")[1].strip()
                    # 提取势力设定
                    elif "正派势力：" in line:
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:
                                core_worldview[current_field] = content
                            current_field = None
                            current_field_content = []
                        forces['positive'] = [f.strip() for f in line.split("正派势力：")[1].split(',')]
                    elif "反派势力：" in line:
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:
                                core_worldview[current_field] = content
                            current_field = None
                            current_field_content = []
                        forces['negative'] = [f.strip() for f in line.split("反派势力：")[1].split(',')]
                    elif "中立势力：" in line:
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:
                                core_worldview[current_field] = content
                            current_field = None
                            current_field_content = []
                        forces['neutral'] = [f.strip() for f in line.split("中立势力：")[1].split(',')]
                    # 角色设定
                    elif line in ["主角：", "配角1："]:
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:
                                core_worldview[current_field] = content
                            current_field = None
                            current_field_content = []
                        current_character = line[:-1]  # 去掉冒号
                        characters[current_character] = {}
                    elif current_character and line.startswith('- 核心性格：'):
                        characters[current_character]['core_personality'] = line.split('- 核心性格：')[1].strip()
                    elif current_character and line.startswith('- 浅层背景：'):
                        characters[current_character]['shallow_background'] = line.split('- 浅层背景：')[1].strip()
                    elif current_character and line.startswith('- 深层背景：'):
                        characters[current_character]['deep_background'] = line.split('- 深层背景：')[1].strip()
                    # 章节设定（优先检查，避免被其他条件拦截）
                    if line.startswith('第') and ('章：' in line or '章' in line):
                        if current_field and current_field_content:
                            content = ' '.join(current_field_content).strip()
                            content = content.replace('**', '').replace('*', '')
                            if content:
                                core_worldview[current_field] = content
                            current_field = None
                            current_field_content = []
                        if current_chapter:
                            if current_conflict_content:
                                conflict_text = ' '.join(current_conflict_content).strip()
                                conflict_text = conflict_text.replace('**', '').replace('*', '').strip()
                                if conflict_text:
                                    chapters[current_chapter]['main_conflict'] = conflict_text
                            if current_end_condition_content:
                                end_condition_text = ' '.join(current_end_condition_content).strip()
                                end_condition_text = end_condition_text.replace('**', '').replace('*', '').strip()
                                if end_condition_text:
                                    chapters[current_chapter]['conflict_end_condition'] = end_condition_text
                        if '章：' in line:
                            chapter_num = line.split('章：')[0].replace('第', '').strip()
                        else:
                            match = re.search(r'第(\d+)章', line)
                            chapter_num = match.group(1) if match else line.replace('第', '').replace('章', '').strip()
                        current_chapter = f"chapter{chapter_num}"
                        chapters[current_chapter] = {}
                        current_conflict_content = []
                        current_end_condition_content = []

                        remaining_line = line.split('章：', 1)[1] if '章：' in line else ''
                        if remaining_line and ('核心矛盾' in remaining_line or '矛盾：' in remaining_line):
                            if '- 核心矛盾：' in remaining_line:
                                conflict_part = remaining_line.split('- 核心矛盾：', 1)[1].strip()
                                if conflict_part:
                                    current_conflict_content.append(conflict_part)
                            elif '核心矛盾：' in remaining_line:
                                conflict_part = remaining_line.split('核心矛盾：', 1)[1].strip()
                                if conflict_part:
                                    current_conflict_content.append(conflict_part)
                            if '- 矛盾结束条件：' in remaining_line:
                                end_part = remaining_line.split('- 矛盾结束条件：', 1)[1].strip()
                                if end_part:
                                    current_end_condition_content.append(end_part)
                            elif '矛盾结束条件：' in remaining_line:
                                end_part = remaining_line.split('矛盾结束条件：', 1)[1].strip()
                                if end_part:
                                    current_end_condition_content.append(end_part)
                    elif current_chapter and ('核心矛盾' in line or '矛盾：' in line):
                        conflict_text = None
                        if '- 核心矛盾：' in line:
                            conflict_text = line.split('- 核心矛盾：', 1)[1].strip()
                        elif '核心矛盾：' in line:
                            conflict_text = line.split('核心矛盾：', 1)[1].strip()
                        elif line.strip().startswith('核心矛盾') and '：' not in line:
                            conflict_text = line.replace('核心矛盾', '').strip()

                        if conflict_text:
                            conflict_text = conflict_text.replace('**', '').replace('*', '').strip()
                            if conflict_text:
                                current_conflict_content.append(conflict_text)
                        elif current_conflict_content:
                            stripped_line = line.strip()
                            if stripped_line and not stripped_line.startswith('-') and not stripped_line.startswith('第') and '：' not in stripped_line:
                                current_conflict_content.append(stripped_line)
                    elif current_chapter and ('矛盾结束条件' in line or '结束条件' in line):
                        end_condition_text = None
                        if '- 矛盾结束条件：' in line:
                            end_condition_text = line.split('- 矛盾结束条件：', 1)[1].strip()
                        elif '矛盾结束条件：' in line:
                            end_condition_text = line.split('矛盾结束条件：', 1)[1].strip()
                        elif '- 结束条件：' in line:
                            end_condition_text = line.split('- 结束条件：', 1)[1].strip()
                        elif '结束条件：' in line:
                            end_condition_text = line.split('结束条件：', 1)[1].strip()
                        elif line.strip().startswith('矛盾结束条件') or line.strip().startswith('结束条件'):
                            end_condition_text = line.replace('矛盾结束条件', '').replace('结束条件', '').strip()

                        if end_condition_text:
                            end_condition_text = end_condition_text.replace('**', '').replace('*', '').strip()
                            if end_condition_text:
                                current_end_condition_content.append(end_condition_text)
                        elif current_end_condition_content:
                            stripped_line = line.strip()
                            if stripped_line and not stripped_line.startswith('-') and not stripped_line.startswith('第') and '：' not in stripped_line:
                                current_end_condition_content.append(stripped_line)
                    elif current_field and not line.startswith('-') and not line.startswith('第') and '：' not in line:
                        if line and not line.startswith('###'):
                            current_field_content.append(line)

            # 保存最后一个字段（如果还在收集）
            if current_field and current_field_content:
                content = ' '.join(current_field_content).strip()
                content = content.replace('**', '').replace('*', '')
                if content:
                    core_worldview[current_field] = content

            # 保存最后一个章节的矛盾信息（如果还在收集）
            if current_chapter:
                if current_conflict_content:
                    conflict_text = ' '.join(current_conflict_content).strip()
                    conflict_text = conflict_text.replace('**', '').replace('*', '').strip()
                    if conflict_text:
                        chapters[current_chapter]['main_conflict'] = conflict_text
                if current_end_condition_content:
                    end_condition_text = ' '.join(current_end_condition_content).strip()
                    end_condition_text = end_condition_text.replace('**', '').replace('*', '').strip()
                    if end_condition_text:
                        chapters[current_chapter]['conflict_end_condition'] = end_condition_text

            # 填充核心世界观
            core_worldview['characters'] = characters
            core_worldview['forces'] = forces
            core_worldview['chapters'] = chapters

            # 使用正则表达式回填缺失的章节矛盾信息（作为备用方案）
            _regex_fill_worldview(raw_content, core_worldview, chapters)

            # 如果字段仍然缺失，设置默认值（避免完全为空）
            if not core_worldview.get('game_style'):
                core_worldview['game_style'] = f"基于主题'{user_idea}'的文本冒险游戏"
                print(f"⚠️ [警告] game_style缺失，已设置默认值")
            if not core_worldview.get('world_basic_setting'):
                core_worldview['world_basic_setting'] = f"游戏世界设定待完善，主题：{user_idea}"
                print(f"⚠️ [警告] world_basic_setting缺失，已设置默认值")
            if not core_worldview.get('protagonist_ability'):
                core_worldview['protagonist_ability'] = "主角能力待定义"
                print(f"⚠️ [警告] protagonist_ability缺失，已设置默认值")

            # 确保chapters结构完整，如果缺失则填充默认值
            if not chapters or len(chapters) == 0:
                chapters = {}
            for i in range(1, 4):
                chapter_key = f"chapter{i}"
                if chapter_key not in chapters:
                    chapters[chapter_key] = {}
                if 'main_conflict' not in chapters[chapter_key] or not chapters[chapter_key]['main_conflict']:
                    chapters[chapter_key]['main_conflict'] = f"第{i}章的核心矛盾待定义"
                if 'conflict_end_condition' not in chapters[chapter_key] or not chapters[chapter_key]['conflict_end_condition']:
                    chapters[chapter_key]['conflict_end_condition'] = f"第{i}章的矛盾结束条件待定义"

            core_worldview['chapters'] = chapters
            global_state['core_worldview'] = core_worldview
            # 若解析结果中缺少性别，从主角角色描述中推断并写回，保证剧情与主角参考图使用同一性别
            if not (protagonist_canonical.get("gender") or "").strip() or ("男" not in protagonist_canonical.get("gender", "") and "女" not in protagonist_canonical.get("gender", "")):
                protagonist_char = (core_worldview.get("characters") or {}).get("主角") or {}
                protagonist_text_parts = []
                if isinstance(protagonist_char, dict):
                    for key in ("core_personality", "shallow_background", "deep_background"):
                        protagonist_text_parts.append(str(protagonist_char.get(key, "") or ""))
                protagonist_text = " ".join(protagonist_text_parts)
                inferred = _infer_gender_from_text(protagonist_text)
                if inferred:
                    protagonist_canonical["gender"] = inferred
                    print("✅ 主角规范信息性别缺失，已从主角描述推断并写回：", inferred)
                else:
                    protagonist_canonical["gender"] = "男性"
                    print("✅ 主角规范信息性别缺失且无法推断，已写回默认：男性（与生图兜底一致）")
            global_state['protagonist_canonical'] = protagonist_canonical
            if protagonist_canonical:
                print("✅ 主角规范信息解析结果：", json.dumps(protagonist_canonical, ensure_ascii=False, indent=2))

            # 提取初始世界线
            flow_section = False
            flow_worldline = {}
            characters_state = {}
            environment = {}

            current_character = ""

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                if line.startswith('## 【初始世界线】') or line.startswith('【初始世界线】'):
                    flow_section = True
                    continue
                elif flow_section and line.startswith('## 【'):
                    flow_section = False
                    break

                if flow_section:
                    if line.startswith('### 【'):
                        continue

                    if "当前章节：" in line:
                        raw_chapter = line.split("当前章节：")[1].strip()
                        # 规范为 chapterN，便于与 chapters 的 key 一致（如 "chapter1（第一章）" -> "chapter1"）
                        match = re.search(r'chapter\d+', raw_chapter, re.I)
                        flow_worldline['current_chapter'] = match.group(0) if match else raw_chapter
                    elif "初始主线进度：" in line:
                        flow_worldline['quest_progress'] = line.split("初始主线进度：")[1].strip()
                    elif "主线进度：" in line:
                        flow_worldline['quest_progress'] = line.split("主线进度：")[1].strip()
                    elif "章节矛盾已解决：" in line:
                        status = line.split("章节矛盾已解决：")[1].strip()
                        flow_worldline['chapter_conflict_solved'] = status == "是"
                    elif "章节矛盾：" in line:
                        conflict_val = line.split("章节矛盾：")[1].strip()
                        flow_worldline['chapter_conflict_solved'] = "未解决" not in conflict_val and "未触发" not in conflict_val and "已解决" in conflict_val

                    elif "天气：" in line:
                        environment['weather'] = line.split("天气：")[1].strip()
                    elif "位置：" in line:
                        environment['location'] = line.split("位置：")[1].strip()
                    elif "势力关系：" in line:
                        environment['force_relationship'] = line.split("势力关系：")[1].strip()

                    elif line in ["主角：", "配角1："]:
                        current_character = line[:-1]
                        characters_state[current_character] = {}
                    elif current_character and line.startswith('- 想法：'):
                        characters_state[current_character]['thought'] = line.split('- 想法：')[1].strip()
                    elif current_character and line.startswith('- 身体状态：'):
                        characters_state[current_character]['physiology'] = line.split('- 身体状态：')[1].strip()
                    elif current_character and line.startswith('- 深层背景解锁：'):
                        status = line.split('- 深层背景解锁：')[1].strip()
                        characters_state[current_character]['deep_background_unlocked'] = status == "是"

            flow_worldline['characters'] = characters_state
            flow_worldline['environment'] = environment
            global_state['flow_worldline'] = flow_worldline

            core_wv = global_state.get('core_worldview', {})

            if not core_wv.get('game_style'):
                core_wv['game_style'] = f"{user_idea}主题的冒险游戏"
            if not core_wv.get('world_basic_setting'):
                core_wv['world_basic_setting'] = f"在一个充满奇幻色彩的{user_idea}世界中，你将踏上一段改变命运的旅程"
            if not core_wv.get('protagonist_ability'):
                core_wv['protagonist_ability'] = f"你的能力取决于你的属性：颜值{protagonist_attr.get('颜值', '普通')}，智商{protagonist_attr.get('智商', '普通')}，体力{protagonist_attr.get('体力', '普通')}，魅力{protagonist_attr.get('魅力', '普通')}"

            if 'chapters' not in core_wv or not core_wv['chapters']:
                core_wv['chapters'] = {}
            chapters = core_wv['chapters']
            for i in range(1, 4):
                chapter_key = f"chapter{i}"
                if chapter_key not in chapters:
                    chapters[chapter_key] = {}
                if 'main_conflict' not in chapters[chapter_key] or not chapters[chapter_key]['main_conflict']:
                    chapters[chapter_key]['main_conflict'] = f"第{i}章：你需要完成重要的任务，面对各种挑战"
                if 'conflict_end_condition' not in chapters[chapter_key] or not chapters[chapter_key]['conflict_end_condition']:
                    chapters[chapter_key]['conflict_end_condition'] = f"完成第{i}章的主要目标"

            if 'characters' not in core_wv:
                core_wv['characters'] = {}
            if 'forces' not in core_wv:
                core_wv['forces'] = {'positive': [], 'negative': [], 'neutral': []}
            if 'main_quest' not in core_wv:
                core_wv['main_quest'] = f"完成{user_idea}的任务，达成游戏目标"

            global_state['core_worldview'] = core_wv

            global_state['tone'] = tone_key
            print(f"✅ 基调已保存到global_state: {tone_key} ({TONE_CONFIGS.get(tone_key, {}).get('name', '未知')})")

            if core_wv.get('game_style') and core_wv.get('world_basic_setting') and core_wv.get('chapters'):
                if staged_mode:
                    threading.Thread(
                        target=_background_fill_worldview_details,
                        args=("", user_idea, protagonist_attr, difficulty, tone_key),
                        daemon=True
                    ).start()
                    global_state.setdefault("meta", {})["detail_async"] = True
                return global_state
            else:
                print("❌ 错误：生成的世界观不完整，将重试...")
                continue

        except Exception as e:
            print(f"❌ 错误：世界观生成失败（第{attempt+1}/{max_retries}次）：{str(e)}")
            if attempt < max_retries - 1:
                print("🔄 将重试生成世界观...")
                continue

    # 所有尝试都失败后，才返回默认世界观
    print("💡 提示：所有尝试均失败，将使用默认世界观继续游戏")
    return _get_default_worldview(user_idea, protagonist_attr, difficulty)


def _get_default_worldview(user_idea: str, protagonist_attr: Dict, difficulty: str, tone_key: str = "normal_ending") -> Dict:
    """
    获取默认世界观，当AI生成失败时使用
    """
    try:
        tone = TONE_CONFIGS.get(tone_key, TONE_CONFIGS["normal_ending"])

        default_worldview = {
            "core_worldview": {
                "game_style": "奇幻冒险",
                "world_basic_setting": f"在一个充满魔法的世界中，你是一名冒险者，踏上了{user_idea}的旅程",
                "protagonist_ability": f"你的能力取决于你的属性：颜值{protagonist_attr.get('颜值', '普通')}，智商{protagonist_attr.get('智商', '普通')}，体力{protagonist_attr.get('体力', '普通')}，魅力{protagonist_attr.get('魅力', '普通')}",
                "characters": {
                    "主角": {
                        "core_personality": "勇敢果断，充满好奇心",
                        "shallow_background": "你是一名普通的冒险者，渴望探索未知的世界",
                        "deep_background": "你有着不平凡的身世，注定要拯救这个世界。你的祖先曾是守护世界的勇者，拥有强大的魔法力量，但家族因被背叛而没落。你体内流淌着勇者的血液，这是你在冒险中逐渐觉醒的力量源泉。"
                    },
                    "配角1": {
                        "core_personality": "聪明机智，善于谋划",
                        "shallow_background": "你遇到的第一个伙伴，是一名经验丰富的向导",
                        "deep_background": "他有着自己的秘密，正在寻找失落的宝藏。实际上，他是一个古老神秘组织的成员，这个组织一直在暗中守护着世界的平衡。他寻找宝藏的真正目的是为了阻止一个即将到来的灾难。"
                    }
                },
                "forces": {
                    "positive": ["光明势力", "冒险者公会"],
                    "negative": ["黑暗军团", "邪恶巫师"],
                    "neutral": ["商人联盟", "流浪部落"]
                },
                "main_quest": f"完成{user_idea}的任务，拯救这个世界",
                "chapters": {
                    "chapter1": {
                        "main_conflict": "你需要通过森林，但是森林中充满了危险",
                        "conflict_end_condition": "找到森林中的古老神庙"
                    },
                    "chapter2": {
                        "main_conflict": "你需要获得法师公会的认可，才能继续前进",
                        "conflict_end_condition": "通过法师公会的考验"
                    },
                    "chapter3": {
                        "main_conflict": "最终决战，你需要面对邪恶巫师",
                        "conflict_end_condition": "击败邪恶巫师，拯救世界"
                    }
                },
                "end_trigger_condition": "选择结束游戏选项"
            },
            "flow_worldline": {
                "current_chapter": "chapter1",
                "tone": tone_key,
                "characters": {
                    "主角": {
                        "thought": "我必须勇敢地面对挑战",
                        "physiology": "健康",
                        "deep_background_unlocked": False,
                        "deep_background_depth": 0
                    },
                    "配角1": {
                        "thought": "这个年轻人看起来很有潜力",
                        "physiology": "健康",
                        "deep_background_unlocked": False,
                        "deep_background_depth": 0
                    }
                },
                "environment": {
                    "weather": "晴朗",
                    "location": "森林入口",
                    "force_relationship": "各势力之间保持着微妙的平衡"
                },
                "quest_progress": "刚刚开始你的冒险",
                "chapter_conflict_solved": False,
                "info_gap_record": {
                    "entries": [],
                    "current_super_choice": None,
                    "pending_super_plot": None
                }
            },
            "tone": tone_key
        }
        print(f"✅ 默认世界观已创建，基调: {tone_key} ({TONE_CONFIGS.get(tone_key, {}).get('name', '未知')})")
        return default_worldview
    except Exception as e:
        return {
            "core_worldview": {
                "game_style": "奇幻冒险",
                "world_basic_setting": f"在一个充满魔法的世界中，你是一名冒险者，踏上了{user_idea}的旅程",
                "protagonist_ability": "你的能力取决于你的属性",
                "characters": {
                    "主角": {
                        "core_personality": "勇敢果断，充满好奇心",
                        "shallow_background": "你是一名普通的冒险者",
                        "deep_background": "你有着不平凡的身世，体内隐藏着强大的力量，这将在你的冒险中逐渐显现"
                    }
                },
                "forces": {
                    "positive": ["光明势力"],
                    "negative": ["黑暗势力"],
                    "neutral": ["中立势力"]
                },
                "main_quest": f"完成{user_idea}的任务",
                "chapters": {
                    "chapter1": {
                        "main_conflict": "你需要完成第一个任务",
                        "conflict_end_condition": "完成任务"
                    }
                },
                "end_trigger_condition": "选择结束游戏选项"
            },
            "flow_worldline": {
                "current_chapter": "chapter1",
                "tone": tone_key,
                "characters": {
                    "主角": {
                        "thought": "我必须勇敢地面对挑战",
                        "physiology": "健康",
                        "deep_background_unlocked": False,
                        "deep_background_depth": 0
                    }
                },
                "environment": {
                    "weather": "晴朗",
                    "location": "森林入口",
                    "force_relationship": "各势力之间保持着微妙的平衡"
                },
                "quest_progress": "刚刚开始你的冒险",
                "chapter_conflict_solved": False,
                "info_gap_record": {
                    "entries": [],
                    "current_super_choice": None,
                    "pending_super_plot": None
                }
            }
        }
