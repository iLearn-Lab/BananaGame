# -*- coding: utf-8 -*-
"""LLM 本地剧情生成：llm_generate_local、_get_default_scene。"""
import json
import re
from typing import Dict, List

from src.config import AI_API_CONFIG
from src.constants import TONE_CONFIGS, get_tone_prompt_block
from src.llm.api import call_ai_api
from src.wiki.lookup import _format_protagonist_canonical_for_prompt


def llm_generate_local(global_state: Dict, user_interaction: str, last_options: List[str]) -> List[Dict]:
    """生成1层递进剧情，适配章节矛盾、难度、主角属性（强制贴合用户选择+自动重试）"""
    if not global_state or not user_interaction.strip():
        return []
    if not AI_API_CONFIG["api_key"]:
        print("❌ 错误：未配置Camera_Analyst_API_KEY，请在.env文件中设置")
        return []

    # 解析用户选择
    selected_option_idx = -1
    try:
        selected_option_idx = int(user_interaction) - 1
        if selected_option_idx < 0 or selected_option_idx >= len(last_options):
            print("❌ 错误：无效的选项序号")
            return []
    except ValueError:
        print("❌ 错误：请输入有效的数字序号")
        return []

    # 检查缓存中是否有当前选项的剧情
    if "current" in global_state:
        current_scene_data = global_state["current"]
        if "all_options" in current_scene_data and selected_option_idx in current_scene_data["all_options"]:
            option_data = current_scene_data["all_options"][selected_option_idx]
            return [{"scene": option_data["scene"], "options": option_data["next_options"], "flow_update": option_data["flow_update"]}]

    # 如果缓存中没有，使用原始方式生成
    print("⚠️ 缓存中未找到对应选项的剧情，使用原始方式生成...")

    tone_key = global_state.get('tone', 'normal_ending')
    tone = TONE_CONFIGS.get(tone_key, TONE_CONFIGS['normal_ending'])
    protagonist_canonical_block = _format_protagonist_canonical_for_prompt(global_state.get("protagonist_canonical") or {})

    prompt = f"""
    请基于以下设定生成后续1层剧情，**严格遵守以下要求，违反任何一条都将导致任务失败**（优先级：执行用户选择 > 主线推进 > 剧情连贯 > 格式完整）：

    ## 【故事基调要求】：
    1. **必须严格遵循以下故事基调要求**：
       {get_tone_prompt_block(tone)}
       - 所有生成内容必须严格贴合上述基调要求！

    ## 【最高优先级要求】：绝对执行用户选择，100%服从用户指令
    1. 若用户输入是数字序号（如1/2/3）：
       - 首先**精确匹配**上一轮的选项列表：{json.dumps(last_options, ensure_ascii=False)}
       - 严格执行对应序号的选项操作，**绝对不能**执行其他选项的操作
       - 例如用户输入"2"，必须执行第2个选项，**绝对不能**执行1或3的操作
    2. 若用户输入是文本指令：
       - 必须**完全按照字面意思**执行，**绝对不能**偏离或修改用户指令
       - 必须**立即执行**用户的指令，不能延迟或跳过
    3. 场景描述必须是：
       - **执行用户选择后**的**直接、即时结果**
       - 不能跳脱到其他场景，不能提前执行未选择的操作
       - 必须紧密贴合用户的选择，体现选择的直接影响
    4. 新生成的选项必须是：
       - **执行当前用户选择后**的**合理后续操作**
       - 不能回到未选择的操作分支
       - 必须与当前场景和状态紧密相关
       - **部分选项必须关联角色深层背景**：生成2个选项，其中0-1个选项应直接关联到某个角色的深层背景，选择这类选项会触发该角色深层背景的解锁

    ## 【格式要求】：使用清晰的分隔符，方便提取信息
    1. 所有输出内容（包括场景描述、选项、更新日志）必须使用**中文**
    2. 不要返回任何代码块标记（如```json、```）和多余的解释说明
    3. 严格按照以下格式生成，**不要遗漏任何字段**，**不要改变分隔符**：
    4. **重要：必须正确使用标点符号和数字（这是硬性要求，违反将导致任务失败）**：
       - **对话必须使用引号**：所有人物对话必须用引号包裹，如"你好"或"你好"，绝对不能省略引号
       - **句子结尾必须使用标点**：每个句子结尾必须使用句号（。）、问号（？）或感叹号（！），绝对不能省略
       - **数字必须完整显示**：所有数字必须正常显示，如：3、10、第1章、50%、100年、第3次等，绝对不能省略、替换或写成文字
       - **列表项必须使用标点**：列表项必须使用顿号（、）或逗号（，）分隔，如：苹果、香蕉、橙子
       - **特别注意**：生成内容中绝对不能出现缺少标点符号或数字被替换的情况，这是严重错误！
    5. **对话质量要求（这是硬性要求，违反将导致任务失败）**：
       - **语言必须自然流畅**：人物对话必须符合角色性格，语言自然流畅，符合中文表达习惯
       - **避免病句和语法错误**：绝对不能出现病句、语法错误、表达不清、语序混乱等问题
       - **符合人物身份**：对话要符合人物身份、年龄、教育背景和场景氛围
       - **长度适中**：对话长度适中，不要过于冗长或过于简短，每句话控制在20-50字为宜
       - **对话要有意义**：对话必须推动剧情发展或展现角色性格，避免无意义的废话
       - **特别注意**：生成内容中绝对不能出现病句、语法错误或表达不清的情况，这是严重错误！

    【场景】：场景描述（必须是用户操作的直接结果，贴合难度和主角属性，要求：至少150字，包含环境描写、角色反应、对话等，对话必须使用引号）
    【选项】：
    1. 选项1（要求：简洁明确，10-20字）
    2. 选项2（要求：简洁明确，10-20字）
    【世界线更新】：
    角色变化：简要描述角色状态变化（要求：至少50字）
    环境变化：简要描述环境变化（要求：至少50字）
    主线进度：简要描述主线任务进度（要求：至少80字，必须明确说明推进了什么）
    章节矛盾：已解决/未解决
    【深层背景关联】：
    - 选项X：角色名称（如：选项2：主角）

    ## 【生成约束】：必须符合世界观和当前状态
    1. 生成内容必须**完全符合**核心世界观设定
    2. 必须**严格遵循**当前世界线状态
    3. 必须**考虑**主角属性和游戏难度
    4. 必须**体现**用户选择对剧情的影响
    5. 必须**严格遵循选定的故事基调**，所有生成内容都必须符合基调要求
    6. **描写主角时**必须严格遵循【主角规范信息】中的性别、年龄感与外观，使用一致的人称（他/她）与外貌描述

    ## 【主角规范信息】（描写主角性别/年龄/外貌时必须严格遵循，与主角立绘一致）
    {protagonist_canonical_block}

    ## 【输入数据】：
    - 【核心世界观】：{json.dumps(global_state['core_worldview'], ensure_ascii=False)}
    - 【当前状态】：{json.dumps(global_state['flow_worldline'], ensure_ascii=False)}
    - 【用户交互】：{user_interaction}  # 必须100%执行此操作
    - 【上一轮选项】：{json.dumps(last_options, ensure_ascii=False)}  # 用于解析序号对应的操作
    - 【故事基调】：{tone['name']}

    记住：
    1. 你的任务是**100%服从用户指令**，生成符合要求的剧情！
    2. 必须生成部分关联角色深层背景的选项，并在【深层背景关联】中明确标记
    3. 深层背景关联的选项应自然融入剧情，不要显得突兀
    4. 所有生成内容必须严格贴合选定的故事基调！
    5. 描写主角时必须与【主角规范信息】一致（性别、年龄、外貌、人称）。
    """

    request_body = {
        "model": AI_API_CONFIG.get("model", ""),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
        "max_tokens": 2500,
        "top_p": 0.7,
        "frequency_penalty": 0.3,
        "presence_penalty": 0.1,
        "timeout": 200
    }

    for attempt in range(3):
        try:
            print(f"📝 尝试生成剧情（第{attempt+1}/3次）...")
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
            if not raw_content:
                print("❌ 错误：AI返回内容为空，将重试...")
                continue

            scene = ""
            options = []
            flow_update = {
                "characters": {},
                "environment": {},
                "quest_progress": "",
                "chapter_conflict_solved": False
            }
            deep_background_links = {}

            lines = raw_content.split('\n')

            for line in lines:
                if "【场景】：" in line:
                    scene = line.split("【场景】：")[1].strip()
                    break

            options_start = False
            for line in lines:
                if "【选项】：" in line:
                    options_start = True
                    continue
                if options_start and line.startswith("【世界线更新】"):
                    break
                if options_start and line.strip():
                    if line.strip():
                        option = re.sub(r'^\s*\d+\.?\s*', '', line.strip())
                        options.append(option)

            update_start = False
            for line in lines:
                if "【世界线更新】：" in line:
                    update_start = True
                    continue
                if update_start and line.startswith("【深层背景关联】"):
                    break
                if update_start:
                    if "角色变化：" in line:
                        pass
                    elif "环境变化：" in line:
                        pass
                    elif "主线进度：" in line:
                        quest_progress = line.split("主线进度：")[1].strip()
                        flow_update["quest_progress"] = quest_progress
                    elif "章节矛盾：" in line:
                        chapter_status = line.split("章节矛盾：")[1].strip()
                        if chapter_status == "已解决":
                            flow_update["chapter_conflict_solved"] = True

            links_start = False
            for line in lines:
                if "【深层背景关联】：" in line:
                    links_start = True
                    continue
                if links_start and line.strip():
                    if "：" in line:
                        parts = line.split("：")
                        if len(parts) >= 2:
                            option_part = parts[0].strip()
                            char_name = parts[1].strip()
                            match = re.search(r'选项(\d+)', option_part)
                            if match:
                                option_idx = int(match.group(1)) - 1
                                deep_background_links[option_idx] = char_name

            scene_data = {
                "scene": scene,
                "options": options,
                "flow_update": flow_update,
                "deep_background_links": deep_background_links
            }

            if scene and options:
                return [scene_data]
            else:
                print("❌ 错误：无法从AI返回内容中提取有效剧情信息，将重试...")
                if attempt < 2:
                    continue

        except Exception as e:
            print(f"❌ 剧情生成失败（第{attempt+1}/3次）：{str(e)}")
            if attempt < 2:
                print("🔄 将重试生成剧情...")
                continue

    print("💡 提示：所有尝试均失败，将使用默认剧情继续游戏")
    return _get_default_scene(user_interaction, global_state)


def _get_default_scene(user_interaction: str, global_state: Dict) -> List[Dict]:
    """
    获取默认剧情，当AI生成失败时使用
    """
    default_scene = {
        "scene": f"你选择了：{user_interaction}。在你的努力下，你取得了一些进展。",
        "options": [
            "继续前进",
            "查看当前状态",
            "返回上一步"
        ],
        "flow_update": {
            "characters": {},
            "environment": {},
            "quest_progress": f"你正在执行任务：{user_interaction}",
            "chapter_conflict_solved": False
        }
    }
    return [default_scene]
