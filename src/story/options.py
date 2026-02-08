# -*- coding: utf-8 -*-
"""选项剪枝、单选项剧情生成、批量图生成。"""
import hashlib
import json
import os
import re
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple, Optional

from src.config import AI_API_CONFIG, IMAGE_GENERATION_CONFIG
from src.constants import TONE_CONFIGS, PERFORMANCE_OPTIMIZATION
from src.llm.api import call_ai_api
from src.wiki.lookup import _format_protagonist_canonical_for_prompt
from src.image.api_providers import generate_scene_image
from src.image.validation import validate_image_url, fix_incomplete_url

# 选项剪枝函数：过滤不合理、重复或过于相似的选项
def prune_options(options: List[str]) -> List[str]:
    """过滤和优化选项列表，移除不合理、重复或过于相似的选项"""
    if not options:
        return []
    
    pruned = []
    seen_keywords = []  # 使用列表存储关键词集合，因为set不能包含set
    
    for option in options:
        option = option.strip()
        if not option:
            continue
        
        # 过滤太短或太长的选项
        if len(option) < 3 or len(option) > 30:
            continue
        
        # 提取关键词（去除常见词）
        keywords = set(re.findall(r'[\u4e00-\u9fff]+', option))
        common_words = {'的', '了', '在', '是', '我', '你', '他', '她', '它', '这', '那', '一个', '可以', '应该', '需要', '继续', '查看', '返回', '选择'}
        keywords = keywords - common_words
        
        # 检查是否与已有选项过于相似（关键词重叠率>70%）
        is_similar = False
        for seen_keyword_set in seen_keywords:
            if keywords and seen_keyword_set:
                overlap = len(keywords & seen_keyword_set)
                union = len(keywords | seen_keyword_set)
                similarity = overlap / union if union > 0 else 0
                if similarity > 0.7:
                    is_similar = True
                    break
        
        if not is_similar:
            pruned.append(option)
            seen_keywords.append(keywords)  # 使用append而不是add
    
    # 如果剪枝后选项太少，至少保留前几个
    if len(pruned) < 2 and len(options) >= 2:
        pruned = options[:2]  # 保留前2个
    
    return pruned[:2]  # 最多保留2个选项

# 重构：生成单个选项剧情的独立函数
def _generate_single_option(i: int, option: str, global_state: Dict) -> Dict:
    """
    生成单个选项对应的剧情+下一层选项
    :param i: 选项索引
    :param option: 选项内容
    :param global_state: 全局状态
    :return: 包含选项索引和剧情数据的字典
    """
    perf = PERFORMANCE_OPTIMIZATION
    perf_enabled = perf.get("enabled", True)
    print(f"📝 正在生成选项 {i+1} 的剧情...")
    
    # 构建Prompt，生成当前选项对应的剧情和下一层选项
    # 获取当前基调（从global_state或默认normal_ending）
    tone_key = global_state.get('tone', 'normal_ending')
    tone = TONE_CONFIGS.get(tone_key, TONE_CONFIGS['normal_ending'])
    
    # 检查是否有已解锁的深层背景
    flow = global_state.get('flow_worldline', {})
    deep_background_unlocked_flag = flow.get('deep_background_unlocked_flag', [])
    
    # 构建已解锁深层背景的提示
    deep_bg_prompt = ""
    if deep_background_unlocked_flag:
        core = global_state.get('core_worldview', {})
        characters = core.get('characters', {})
        unlocked_deep_bgs = []
        for char_name in deep_background_unlocked_flag:
            if char_name in characters:
                deep_bg = characters[char_name].get('deep_background', '')
                unlocked_deep_bgs.append(f"{char_name}的深层背景：{deep_bg}")
        if unlocked_deep_bgs:
            deep_bg_prompt = f"\n## 【已解锁深层背景】：\n{chr(10).join(unlocked_deep_bgs)}\n### 【重要要求】：后续剧情必须围绕已解锁的深层背景展开，将深层背景信息自然融入主线剧情中，不要直接向玩家显示深层背景内容！"
    
    # 添加调试信息：打印输入数据
    print(f"🔍 调试信息：输入参数")
    print(f"   选项索引：{i+1}")
    print(f"   用户选择：{option}")
    print(f"   global_state keys：{list(global_state.keys())}")
    print(f"   core_worldview是否存在：{'core_worldview' in global_state}")
    print(f"   flow_worldline是否存在：{'flow_worldline' in global_state}")
    
    # 确保core_worldview和flow_worldline存在
    core_worldview = global_state.get('core_worldview', {})
    flow_worldline = global_state.get('flow_worldline', {})
    protagonist_canonical_block = _format_protagonist_canonical_for_prompt(global_state.get("protagonist_canonical") or {})
    
    # 判断是否是第一次生成（"开始游戏"选项）
    is_initial_scene = (option == "开始游戏" or option == "开始游戏")
    
    # 根据是否是第一次生成，调整场景描述要求
    if is_initial_scene:
        scene_requirement = """【场景】：场景描述（这是游戏的第一个场景，必须极其吸引人，要求：至少400字，必须包含以下元素：
       1. **引人入胜的开场**：必须立即抓住玩家的注意力，包含悬念、冲突或引人注目的元素
       2. **详细的环境描写**：至少100字，详细描述场景的视觉、听觉、嗅觉、触觉等感官细节，让玩家仿佛身临其境
       3. **角色反应和内心活动**：至少80字，描述主角的内心想法、情绪反应、身体感受等
       4. **对话或互动**：至少80字，包含至少2-3句对话，对话必须使用引号，对话要推动剧情或展现角色性格
       5. **悬念或冲突**：至少80字，引入一个引人好奇的问题、冲突或悬念，让玩家想要继续探索
       6. **世界观融入**：自然融入世界观设定，展现世界特色、文化背景或关键信息
       7. **主线任务暗示**：至少60字，暗示或提及主线任务，但不要直接说明，保持神秘感
       场景描述必须流畅自然，有画面感，能够立刻吸引玩家继续游戏！）"""
    else:
        scene_requirement = """【场景】：场景描述（必须是用户操作的直接结果，贴合难度和主角属性，要求：至少150字，包含环境描写、角色反应、对话等，对话必须使用引号）"""
    
    prompt = f"""
    请基于以下设定生成后续1层剧情，**严格遵守以下要求，违反任何一条都将导致任务失败**（优先级：执行用户选择 > 主线推进 > 剧情连贯 > 格式完整）：
    
    ## 【故事基调要求】：
    1. **必须严格遵循以下故事基调要求**：
       - 基调名称：{tone['name']}
       - 基调描述：{tone['description']}
       - 语言特征：{tone['language_features']}
       - 结局导向：{tone['ending_orientation']}
       - 禁忌内容：{tone['taboo_content']}
       - 所有生成内容必须严格贴合上述基调要求！
    
    ## 【最高优先级要求】：绝对执行用户选择，100%服从用户指令
    1. 用户选择了选项：{option}
    2. 必须**完全按照字面意思**执行，**绝对不能**偏离或修改用户指令
    3. 必须**立即执行**用户的指令，不能延迟或跳过
    4. 场景描述必须是：
       - **执行用户选择后**的**直接、即时结果**
       - 不能跳脱到其他场景，不能提前执行未选择的操作
       - 必须紧密贴合用户的选择，体现选择的直接影响
    5. 新生成的选项必须是：
       - **执行当前用户选择后**的**合理后续操作**
       - 必须与当前场景和状态紧密相关
       - 必须**明确推进主线任务**，每个选项都应该让主角离主线目标更近一步
       - **部分选项必须关联角色深层背景**：生成2个选项，其中0-1个选项应直接关联到某个角色的深层背景，选择这类选项会触发该角色深层背景的解锁
    {deep_bg_prompt}
    
    ## 【主线推进要求】：
    1. 必须**明确推进主线任务**，每个选择都应该带来主线进度的实质性变化
    2. 必须**保持主线的连贯性**，后续剧情必须与之前的主线进度紧密相关
    3. 必须**体现用户选择对主线的影响**，不同的选择应该导致不同的主线进展
    4. 必须**明确更新主线进度**，在【世界线更新】中的"主线进度"字段必须清晰描述当前主线的推进情况
    
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
    
    {scene_requirement}
    【选项】：
    1. 选项1（要求：简洁明确，10-20字）
    2. 选项2（要求：简洁明确，10-20字）
    【世界线更新】：
    角色变化：简要描述角色状态变化（要求：至少50字）
    环境变化：简要描述环境变化（要求：至少50字）
    主线进度：简要描述主线任务进度的具体推进情况（要求：至少80字，必须明确说明推进了什么）
    章节矛盾：已解决/未解决
    【深层背景关联】：
    - 选项X：角色名称（如：选项2：主角）
    
    ## 【生成约束】：必须符合世界观和当前状态
    1. 生成内容必须**完全符合**核心世界观设定
    2. 必须**严格遵循**当前世界线状态
    3. 必须**考虑**主角属性和游戏难度
    4. 必须**体现**用户选择对剧情的影响
    5. 必须**确保主线任务不断推进**，不能让剧情停滞不前
    6. 必须**严格遵循选定的故事基调**，所有生成内容都必须符合基调要求
    7. **描写主角时**必须严格遵循【主角规范信息】中的性别、年龄感与外观，使用一致的人称（他/她）与外貌描述
    
    ## 【主角规范信息】（描写主角性别/年龄/外貌时必须严格遵循，与主角立绘一致）
    {protagonist_canonical_block}
    
    ## 【输入数据】：
    - 【核心世界观】：{json.dumps(core_worldview, ensure_ascii=False)}
    - 【当前状态】：{json.dumps(flow_worldline, ensure_ascii=False)}
    - 【用户选择】：{option}  # 必须100%执行此操作
    - 【故事基调】：{tone['name']}
    
    记住：
    1. 你的任务是**100%服从用户指令**，**明确推进主线任务**，生成符合要求的剧情！
    2. 必须生成部分关联角色深层背景的选项，并在【深层背景关联】中明确标记
    3. 深层背景关联的选项应自然融入剧情，不要显得突兀
    4. 所有生成内容必须严格贴合选定的故事基调！
    5. 如果有已解锁的深层背景，后续剧情必须围绕这些深层背景展开，将深层背景信息自然融入主线剧情中，不要直接向玩家显示深层背景内容！
    6. 描写主角时必须与【主角规范信息】一致（性别、年龄、外貌、人称）。
    """
    
    # 添加调试信息：打印生成的Prompt前500字符
    print(f"📝 调试信息：生成的Prompt前500字符")
    print(prompt[:500])
    
    # 构建请求体，如果是第一次生成，增加max_tokens以确保生成足够长的内容
    if perf_enabled and perf.get("optimize_tokens", True):
        initial_tokens = perf.get("plot_max_tokens_initial", 2500)
        normal_tokens = perf.get("plot_max_tokens_normal", 2000)
    else:
        initial_tokens = 3500
        normal_tokens = 2500
    max_tokens = initial_tokens if is_initial_scene else normal_tokens
    
    request_body = {
        "model": AI_API_CONFIG.get("model", ""),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,  # 适度提高温度，改善标点符号和数字生成
        "max_tokens": max_tokens,  # 根据是否是第一次生成调整token数
        "top_p": 0.7,  # 适度提高多样性，改善对话自然度
        "frequency_penalty": 0.3,  # 降低惩罚，避免过度抑制标点符号
        "presence_penalty": 0.1,  # 降低惩罚，改善对话流畅度
        "timeout": 200  # 适度降低超时时间
    }
    
    option_data = None
    
    # 内部重试机制
    max_retries = 3
    if perf_enabled and perf.get("optimize_retry", True):
        max_retries = perf.get("plot_max_retries", 2)
    for attempt in range(max_retries):
        try:
            # 调用带重试的API函数
            try:
                response_data = call_ai_api(request_body)
            except ValueError as e:
                # 如果是403/401认证错误，立即停止重试，使用默认剧情
                error_str = str(e)
                if "API认证失败" in error_str or "HTTP 403" in error_str or "HTTP 401" in error_str:
                    print(f"❌ 选项 {i+1} API认证失败，停止重试，使用默认剧情")
                    # 直接跳出循环，使用默认剧情
                    option_data = None
                    break
                else:
                    raise  # 其他ValueError也抛出
            except Exception as api_error:
                # 检查是否是认证错误
                error_str = str(api_error)
                if "403" in error_str or "401" in error_str or "Forbidden" in error_str:
                    print(f"❌ 选项 {i+1} API认证失败，停止重试，使用默认剧情")
                    option_data = None
                    break
                raise  # 其他异常继续抛出
            # 安全访问嵌套键
            choices = response_data.get("choices", [])
            if not choices or len(choices) == 0:
                print(f"❌ 错误：选项 {i+1} 的AI返回内容格式异常，缺少choices字段，将重试...")
                continue
            
            message = choices[0].get("message", {})
            if not message:
                print(f"❌ 错误：选项 {i+1} 的AI返回内容格式异常，缺少message字段，将重试...")
                continue
            
            raw_content = message.get("content", "").strip()
            if not raw_content:
                print(f"❌ 错误：选项 {i+1} 的AI返回内容为空，将重试...")
                continue
            
            # 直接从文本中提取信息，不依赖JSON解析
            # 提取场景描述
            scene = ""
            next_options = []
            flow_update = {
                "characters": {},
                "environment": {},
                "quest_progress": "",
                "chapter_conflict_solved": False
            }
            # 新增：深层背景关联信息
            deep_background_links = {}
            
            # 0. 清理AI返回的内容，移除无关文字
            cleaned_content = raw_content
            
            # 移除常见的错误提示文字 - 修复：使用更精确的正则表达式，避免匹配整个字符串
            error_patterns = [
                r'(请求.*?失败|申请.*?失败|请.*?重试|侧向请求|生化或者失败联盟|出让角1|遣代表试)',
            ]
            
            for pattern in error_patterns:
                # 修复：移除re.DOTALL标志，避免跨行匹配导致的问题
                cleaned_content = re.sub(pattern, '', cleaned_content, flags=re.IGNORECASE)
            
            # 1. 提取场景描述 - 尝试多种匹配方式，使用清理后的内容
            scene_match1 = re.search(r'【场景】：([\s\S]*?)【选项】：', cleaned_content, re.DOTALL)
            scene_match2 = re.search(r'【场景】：([\s\S]*?)$', cleaned_content, re.DOTALL)
            scene_match3 = re.search(r'【场景】：([^\n]*)', cleaned_content)
            
            if scene_match1:
                scene = scene_match1.group(1).strip()
                print(f"✅ 选项 {i+1} 场景提取成功（方式1）：{scene[:50]}...")
            elif scene_match2:
                scene = scene_match2.group(1).strip()
                print(f"✅ 选项 {i+1} 场景提取成功（方式2）：{scene[:50]}...")
            elif scene_match3:
                scene = scene_match3.group(1).strip()
                print(f"✅ 选项 {i+1} 场景提取成功（方式3）：{scene[:50]}...")
            else:
                print(f"❌ 选项 {i+1} 场景提取失败，原始内容中未找到【场景】标签")
            
            # 2. 进一步清理提取到的场景描述
            if scene:
                # 修复：清理场景描述中的错误信息，使用更精确的正则表达式
                error_patterns = [
                    r'请求.*?失败|申请.*?失败|请.*?重试|侧向请求|生化或者失败联盟|出让角1|遣代表试',
                    # 保留：中文/英文/数字（含全角）+ 常用中文标点（含省略号）+ 引号
                    r"[^一-龥a-zA-Z0-9０-９\s，。！？、：；“”‘’（）《》【】…\"']+",  # 移除其余“非法字符”，避免误删数字
                ]
                
                for pattern in error_patterns:
                    # 移除错误信息，不使用re.DOTALL避免匹配整个字符串
                    scene = re.sub(pattern, '', scene, flags=re.IGNORECASE)
                
                # 移除多余的空格和换行
                scene = scene.strip()
                
                # 确保场景描述符合预期，没有奇怪的前缀
                if len(scene) > 0:
                    # 尝试找到第一个中文字符或英文单词的位置
                    first_valid_char = re.search(r'[\u4e00-\u9fa5a-zA-Z"""“‘「【(]', scene)
                    if first_valid_char:
                        scene = scene[first_valid_char.start():]
                
                # 验证场景描述长度
                if len(scene) < 10:
                    print(f"⚠️ 选项 {i+1} 场景描述过短，可能提取不完整：{scene}")
                    # 修复：如果场景描述过短，使用默认描述，避免显示错误信息
                    scene = "你仔细观察周围的环境，准备采取行动。"
            
            # 2. 提取选项 - 尝试多种匹配方式，使用清理后的内容
            options_match1 = re.search(r'【选项】：([\s\S]*?)【世界线更新】：', cleaned_content, re.DOTALL)
            options_match2 = re.search(r'【选项】：([\s\S]*?)【深层背景关联】：', cleaned_content, re.DOTALL)
            options_match3 = re.search(r'【选项】：([\s\S]*?)$', cleaned_content, re.DOTALL)
            
            if options_match1:
                options_text = options_match1.group(1).strip()
            elif options_match2:
                options_text = options_match2.group(1).strip()
            elif options_match3:
                options_text = options_match3.group(1).strip()
            else:
                options_text = ""
            
            if options_text:
                # 解析选项行
                option_lines = options_text.split('\n')
                for line in option_lines:
                    stripped_line = line.strip()
                    if stripped_line:
                        # 移除序号和可能的点号
                        next_option = re.sub(r'^\s*\d+\.?\s*', '', stripped_line)
                        if next_option:
                            next_options.append(next_option)
            
            # 3. 提取世界线更新 - 使用正则表达式
            worldline_match = re.search(r'【世界线更新】：([\s\S]*?)(?:【深层背景关联】：|$)', raw_content, re.DOTALL)
            if worldline_match:
                worldline_text = worldline_match.group(1).strip()
                
                # 提取主线进度
                quest_progress_match = re.search(r'主线进度：([^\n]*)', worldline_text)
                if quest_progress_match:
                    flow_update["quest_progress"] = quest_progress_match.group(1).strip()
                
                # 提取章节矛盾
                chapter_conflict_match = re.search(r'章节矛盾：([^\n]*)', worldline_text)
                if chapter_conflict_match:
                    chapter_status = chapter_conflict_match.group(1).strip()
                    flow_update["chapter_conflict_solved"] = chapter_status == "已解决"
            
            # 4. 提取深层背景关联信息
            deep_bg_match = re.search(r'【深层背景关联】：([\s\S]*?)$', raw_content, re.DOTALL)
            if deep_bg_match:
                deep_bg_text = deep_bg_match.group(1).strip()
                deep_bg_lines = deep_bg_text.split('\n')
                
                for line in deep_bg_lines:
                    stripped_line = line.strip()
                    if stripped_line and "：" in stripped_line:
                        parts = stripped_line.split("：")
                        if len(parts) >= 2:
                            option_part = parts[0].strip()
                            char_name = parts[1].strip()
                            
                            # 提取选项序号
                            option_num_match = re.search(r'选项(\d+)', option_part)
                            if option_num_match:
                                option_idx = int(option_num_match.group(1)) - 1  # 转换为0-based索引
                                deep_background_links[option_idx] = char_name
            
            # 选项剪枝：过滤不合理、重复或过于相似的选项
            original_options_count = len(next_options)
            original_options = next_options.copy()  # 保存原始选项
            next_options = prune_options(next_options)
            pruned_count = len(next_options)
            
            # 如果剪枝后选项太少，使用原始选项（至少保留2个）
            if pruned_count < 2 and original_options_count >= 2:
                print(f"⚠️ 选项 {i+1} 剪枝后选项过少（{pruned_count}个），使用原始选项")
                # 使用原始选项，但确保至少有2个
                next_options = original_options[:2] if len(original_options) >= 2 else original_options
            
            # 限制选项数量为2个（确保只保留2个选项）
            if len(next_options) > 2:
                print(f"📊 选项 {i+1} 数量超过2个，限制为前2个")
                next_options = next_options[:2]
            elif len(next_options) < 2:
                # 如果选项少于2个，尝试从原始选项补充
                if original_options_count >= 2:
                    print(f"⚠️ 选项 {i+1} 剪枝后选项过少（{len(next_options)}个），使用原始选项的前2个")
                    next_options = original_options[:2]
            
            print(f"📊 选项 {i+1} 剪枝统计：原始{original_options_count}个 -> 剪枝后{len(next_options)}个")
            
            # 构建选项数据
            option_data = {
                "scene": scene,
                "next_options": next_options,
                "flow_update": flow_update,
                "deep_background_links": deep_background_links
            }
            
            # 新增：生成场景图片（使用本地缓存，避免OSS URL失效问题）
            # 修复：移除“线程 join 6分钟后丢结果”的逻辑，改为同步调用 + 可控的网络超时/重试。
            scene_image = None
            if scene:
                try:
                    # 若是初始场景且主角正面图尚未就绪：等待主角正面图生成后再生成场景图（保证场景中主角形象一致）
                    if is_initial_scene:
                        game_id = global_state.get("game_id") if isinstance(global_state, dict) else None
                        if game_id:
                            main_character_dir = Path("initial") / "main_character" / game_id
                            front_path = main_character_dir / "main_character.png"
                            wait_timeout = 120  # 最多等待 120 秒
                            poll_interval = 2
                            waited = 0
                            while not front_path.exists() and waited < wait_timeout:
                                time.sleep(poll_interval)
                                waited += poll_interval
                            if front_path.exists():
                                print(f"✅ 主角正面图已就绪，开始生成初始场景图片（等待 {waited} 秒）")
                            else:
                                print(f"⚠️ 等待主角正面图超时（{wait_timeout} 秒），将不使用主角参考图生成场景")
                    print(f"🎨 正在为选项 {i+1} 生成场景图片（启用本地缓存）...")
                    scene_image = generate_scene_image(scene, global_state, "default", use_cache=True)
                    if scene_image and scene_image.get('url'):
                        # 验证图片URL是否有效，确保返回格式正确
                        image_url = scene_image.get('url')
                        
                        # 确保URL是字符串
                        if not isinstance(image_url, str):
                            print(f"⚠️ 选项 {i+1} 图片URL不是字符串类型: {type(image_url)}")
                            image_url = str(image_url)
                            scene_image['url'] = image_url
                        
                        # 检查是否为本地路径格式（/image_cache/开头）
                        is_local_path = image_url.startswith('/image_cache/') or image_url.startswith('image_cache/')
                        
                        # 验证URL格式：本地路径或有效的HTTP(S) URL
                        if is_local_path or validate_image_url(image_url):
                            # 确保本地路径格式统一为 /image_cache/{filename}
                            if image_url.startswith('image_cache/'):
                                image_url = '/' + image_url
                                scene_image['url'] = image_url
                            
                            # 确保返回的数据格式正确（含 scene_text_hash，避免 /generate-option 误判文本变化而重复生成）
                            scene_text_hash = hashlib.md5(scene.encode("utf-8")).hexdigest() if (scene and scene.strip()) else None
                            option_data["scene_image"] = {
                                "url": image_url,
                                "prompt": scene_image.get("prompt", ""),
                                "style": scene_image.get("style", "default"),
                                "width": scene_image.get("width", 1024),
                                "height": scene_image.get("height", 1024),
                                # 本地路径表示已缓存；远程URL默认视为未缓存（除非上游明确标记）
                                "cached": True if is_local_path else scene_image.get("cached", False),
                                "scene_text_hash": scene_text_hash,
                            }
                            if is_local_path:
                                print(f"✅ 选项 {i+1} 场景图片生成成功并已保存到本地")
                                print(f"   本地路径: {image_url}")
                            else:
                                print(f"✅ 选项 {i+1} 场景图片生成成功（远程URL）")
                                print(f"   图片URL: {image_url[:80]}...")
                        else:
                            # URL无效，尝试修复（仅对HTTP(S) URL）
                            if not is_local_path:
                                fixed_url = fix_incomplete_url(image_url)
                                if fixed_url and validate_image_url(fixed_url):
                                    scene_text_hash = hashlib.md5(scene.encode("utf-8")).hexdigest() if (scene and scene.strip()) else None
                                    option_data["scene_image"] = {
                                        "url": fixed_url,
                                        "prompt": scene_image.get("prompt", ""),
                                        "style": scene_image.get("style", "default"),
                                        "width": scene_image.get("width", 1024),
                                        "height": scene_image.get("height", 1024),
                                        "cached": scene_image.get("cached", False),
                                        "scene_text_hash": scene_text_hash,
                                    }
                                    print(f"✅ 选项 {i+1} 场景图片URL修复成功: {fixed_url[:80]}...")
                                else:
                                    print(f"⚠️ 选项 {i+1} 场景图片URL无效，跳过图片: {image_url[:80]}...")
                                    scene_image = None
                            else:
                                print(f"⚠️ 选项 {i+1} 场景图片本地路径格式异常: {image_url}")
                                scene_image = None
                    else:
                        print(f"⚠️ 选项 {i+1} 场景图片生成失败，继续使用文本模式")
                except Exception as e:
                    print(f"⚠️ 选项 {i+1} 图片生成异常，继续使用文本模式：{str(e)}")
                    import traceback
                    traceback.print_exc()
            
            # ==================== 视频生成功能已禁用（性能优化） ====================
            # 新增：生成场景视频（5-10秒）
            # scene_video = None
            # if scene_image and scene_image.get('url'):  # 基于生成的图片生成视频
            #     try:
            #         print(f"🎬 正在为选项 {i+1} 生成场景视频（5-10秒）...")
            #         # 异步生成视频，返回任务ID
            #         scene_video = generate_scene_video(
            #             scene_description=scene,
            #             image_url=scene_image.get('url'),
            #             duration=random.randint(5, 10)  # 随机5-10秒
            #         )
            #         if scene_video:
            #             option_data["scene_video"] = scene_video
            #             print(f"✅ 选项 {i+1} 场景视频生成任务已启动，任务ID：{scene_video.get('task_id')}")
            #         else:
            #             print(f"⚠️ 选项 {i+1} 场景视频生成失败，继续使用图片模式")
            #     except Exception as e:
            #         print(f"⚠️ 选项 {i+1} 视频生成异常，继续使用图片模式：{str(e)}")
            scene_video = None  # 视频功能已禁用
            
            # 只有当场景描述和选项都有内容时，才返回结果
            if scene and next_options and len(next_options) >= 2:  # 至少保留2个选项
                print(f"✅ 选项 {i+1} 剧情生成成功，共{len(next_options)}个选项：{next_options}")
                break
            else:
                # 如果提取失败，继续重试
                print(f"❌ 错误：无法从选项 {i+1} 的AI返回内容中提取有效剧情信息，将重试...")
                if attempt < max_retries - 1:
                    continue
        
        except Exception as e:
            error_str = str(e)
            # 如果是认证错误（403/401），立即停止重试
            if "403" in error_str or "401" in error_str or "Forbidden" in error_str or "API认证失败" in error_str:
                print(f"❌ 选项 {i+1} API认证失败，停止重试，使用默认剧情")
                option_data = None
                break  # 立即跳出循环，不再重试
            else:
                print(f"❌ 选项 {i+1} 剧情生成失败（第{attempt+1}/{max_retries}次）：{error_str}")
                if attempt < max_retries - 1:
                    print(f"🔄 将重试生成选项 {i+1} 的剧情...")
                    continue
    
    # 如果所有尝试都失败，返回默认剧情
    if not option_data or not option_data.get("scene") or not option_data.get("next_options"):
        print(f"💡 提示：选项 {i+1} 的所有生成尝试均失败，将使用默认剧情")
        option_data = {
            "scene": f"你选择了：{option}。在你的努力下，你取得了一些进展。",
            "next_options": ["继续前进", "查看当前状态", "返回上一步"],
            "flow_update": {
                "characters": {},
                "environment": {},
                "quest_progress": f"你正在执行任务：{option}",
                "chapter_conflict_solved": False
            },
            "deep_background_links": {}
        }
        # 默认剧情不生成图片和视频
    
    return {"index": i, "data": option_data}

# 优化：只生成文本内容的版本（用于并行优化）
def _generate_single_option_text_only(i: int, option: str, global_state: Dict) -> Dict:
    """
    生成单个选项对应的剧情+下一层选项（仅文本，不含图片）
    这是优化版本，用于并行生成文本后再批量生成图片
    :param i: 选项索引
    :param option: 选项内容
    :param global_state: 全局状态
    :return: 包含选项索引、剧情数据和场景描述的字典
    """
    print(f"📝 正在生成选项 {i+1} 的剧情（文本模式）...")
    perf = PERFORMANCE_OPTIMIZATION
    perf_enabled = perf.get("enabled", True)
    
    # 构建Prompt，生成当前选项对应的剧情和下一层选项
    # 获取当前基调（从global_state或默认normal_ending）
    tone_key = global_state.get('tone', 'normal_ending')
    tone = TONE_CONFIGS.get(tone_key, TONE_CONFIGS['normal_ending'])
    
    # 检查是否有已解锁的深层背景
    flow = global_state.get('flow_worldline', {})
    deep_background_unlocked_flag = flow.get('deep_background_unlocked_flag', [])
    
    # 构建已解锁深层背景的提示
    deep_bg_prompt = ""
    if deep_background_unlocked_flag:
        core = global_state.get('core_worldview', {})
        characters = core.get('characters', {})
        unlocked_deep_bgs = []
        for char_name in deep_background_unlocked_flag:
            if char_name in characters:
                deep_bg = characters[char_name].get('deep_background', '')
                unlocked_deep_bgs.append(f"{char_name}的深层背景：{deep_bg}")
        if unlocked_deep_bgs:
            deep_bg_prompt = f"\n## 【已解锁深层背景】：\n{chr(10).join(unlocked_deep_bgs)}\n### 【重要要求】：后续剧情必须围绕已解锁的深层背景展开，将深层背景信息自然融入主线剧情中，不要直接向玩家显示深层背景内容！"
    
    # 确保core_worldview和flow_worldline存在
    core_worldview = global_state.get('core_worldview', {})
    flow_worldline = global_state.get('flow_worldline', {})
    protagonist_canonical_block = _format_protagonist_canonical_for_prompt(global_state.get("protagonist_canonical") or {})
    
    # 判断是否是第一次生成（"开始游戏"选项）
    is_initial_scene = (option == "开始游戏" or option == "开始游戏")
    
    # 根据是否是第一次生成，调整场景描述要求
    if is_initial_scene:
        scene_requirement = """【场景】：场景描述（这是游戏的第一个场景，必须极其吸引人，要求：至少400字，必须包含以下元素：
       1. **引人入胜的开场**：必须立即抓住玩家的注意力，包含悬念、冲突或引人注目的元素
       2. **详细的环境描写**：至少100字，详细描述场景的视觉、听觉、嗅觉、触觉等感官细节，让玩家仿佛身临其境
       3. **角色反应和内心活动**：至少80字，描述主角的内心想法、情绪反应、身体感受等
       4. **对话或互动**：至少80字，包含至少2-3句对话，对话必须使用引号，对话要推动剧情或展现角色性格
       5. **悬念或冲突**：至少80字，引入一个引人好奇的问题、冲突或悬念，让玩家想要继续探索
       6. **世界观融入**：自然融入世界观设定，展现世界特色、文化背景或关键信息
       7. **主线任务暗示**：至少60字，暗示或提及主线任务，但不要直接说明，保持神秘感
       场景描述必须流畅自然，有画面感，能够立刻吸引玩家继续游戏！）"""
    else:
        scene_requirement = """【场景】：场景描述（必须是用户操作的直接结果，贴合难度和主角属性，要求：至少150字，包含环境描写、角色反应、对话等，对话必须使用引号）"""
    
    prompt = f"""
    请基于以下设定生成后续1层剧情，**严格遵守以下要求，违反任何一条都将导致任务失败**（优先级：执行用户选择 > 主线推进 > 剧情连贯 > 格式完整）：
    
    ## 【故事基调要求】：
    1. **必须严格遵循以下故事基调要求**：
       - 基调名称：{tone['name']}
       - 基调描述：{tone['description']}
       - 语言特征：{tone['language_features']}
       - 结局导向：{tone['ending_orientation']}
       - 禁忌内容：{tone['taboo_content']}
       - 所有生成内容必须严格贴合上述基调要求！
    
    ## 【最高优先级要求】：绝对执行用户选择，100%服从用户指令
    1. 用户选择了选项：{option}
    2. 必须**完全按照字面意思**执行，**绝对不能**偏离或修改用户指令
    3. 必须**立即执行**用户的指令，不能延迟或跳过
    4. 场景描述必须是：
       - **执行用户选择后**的**直接、即时结果**
       - 不能跳脱到其他场景，不能提前执行未选择的操作
       - 必须紧密贴合用户的选择，体现选择的直接影响
    5. 新生成的选项必须是：
       - **执行当前用户选择后**的**合理后续操作**
       - 必须与当前场景和状态紧密相关
       - 必须**明确推进主线任务**，每个选项都应该让主角离主线目标更近一步
       - **部分选项必须关联角色深层背景**：生成2个选项，其中0-1个选项应直接关联到某个角色的深层背景，选择这类选项会触发该角色深层背景的解锁
    {deep_bg_prompt}
    
    ## 【主线推进要求】：
    1. 必须**明确推进主线任务**，每个选择都应该带来主线进度的实质性变化
    2. 必须**保持主线的连贯性**，后续剧情必须与之前的主线进度紧密相关
    3. 必须**体现用户选择对主线的影响**，不同的选择应该导致不同的主线进展
    4. 必须**明确更新主线进度**，在【世界线更新】中的"主线进度"字段必须清晰描述当前主线的推进情况
    
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
    
    {scene_requirement}
    【选项】：
    1. 选项1（要求：简洁明确，10-20字）
    2. 选项2（要求：简洁明确，10-20字）
    【世界线更新】：
    角色变化：简要描述角色状态变化（要求：至少50字）
    环境变化：简要描述环境变化（要求：至少50字）
    主线进度：简要描述主线任务进度的具体推进情况（要求：至少80字，必须明确说明推进了什么）
    章节矛盾：已解决/未解决
    【深层背景关联】：
    - 选项X：角色名称（如：选项2：主角）
    
    ## 【生成约束】：必须符合世界观和当前状态
    1. 生成内容必须**完全符合**核心世界观设定
    2. 必须**严格遵循**当前世界线状态
    3. 必须**考虑**主角属性和游戏难度
    4. 必须**体现**用户选择对剧情的影响
    5. 必须**确保主线任务不断推进**，不能让剧情停滞不前
    6. 必须**严格遵循选定的故事基调**，所有生成内容都必须符合基调要求
    7. **描写主角时**必须严格遵循【主角规范信息】中的性别、年龄感与外观，使用一致的人称（他/她）与外貌描述
    
    ## 【主角规范信息】（描写主角性别/年龄/外貌时必须严格遵循，与主角立绘一致）
    {protagonist_canonical_block}
    
    ## 【输入数据】：
    - 【核心世界观】：{json.dumps(core_worldview, ensure_ascii=False)}
    - 【当前状态】：{json.dumps(flow_worldline, ensure_ascii=False)}
    - 【用户选择】：{option}  # 必须100%执行此操作
    - 【故事基调】：{tone['name']}
    
    记住：
    1. 你的任务是**100%服从用户指令**，**明确推进主线任务**，生成符合要求的剧情！
    2. 必须生成部分关联角色深层背景的选项，并在【深层背景关联】中明确标记
    3. 深层背景关联的选项应自然融入剧情，不要显得突兀
    4. 所有生成内容必须严格贴合选定的故事基调！
    5. 如果有已解锁的深层背景，后续剧情必须围绕这些深层背景展开，将深层背景信息自然融入主线剧情中，不要直接向玩家显示深层背景内容！
    6. 描写主角时必须与【主角规范信息】一致（性别、年龄、外貌、人称）。
    """
    
    # 构建请求体，如果是第一次生成，增加max_tokens以确保生成足够长的内容
    if perf_enabled and perf.get("optimize_tokens", True):
        initial_tokens = perf.get("plot_max_tokens_initial", 2500)
        normal_tokens = perf.get("plot_max_tokens_normal", 2000)
    else:
        initial_tokens = 3500
        normal_tokens = 2500
    max_tokens = initial_tokens if is_initial_scene else normal_tokens
    
    request_body = {
        "model": AI_API_CONFIG.get("model", ""),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.4,
        "max_tokens": max_tokens,
        "top_p": 0.7,
        "frequency_penalty": 0.3,
        "presence_penalty": 0.1,
        "timeout": 200
    }
    
    option_data = None
    scene = None
    
    # 内部重试机制
    max_retries = 3
    if perf_enabled and perf.get("optimize_retry", True):
        max_retries = perf.get("plot_max_retries", 2)
    for attempt in range(max_retries):
        try:
            # 调用带重试的API函数
            try:
                response_data = call_ai_api(request_body)
            except ValueError as e:
                error_str = str(e)
                if "API认证失败" in error_str or "HTTP 403" in error_str or "HTTP 401" in error_str:
                    print(f"❌ 选项 {i+1} API认证失败，停止重试，使用默认剧情")
                    option_data = None
                    break
                else:
                    raise
            except Exception as api_error:
                error_str = str(api_error)
                if "403" in error_str or "401" in error_str or "Forbidden" in error_str:
                    print(f"❌ 选项 {i+1} API认证失败，停止重试，使用默认剧情")
                    option_data = None
                    break
                raise
            
            # 安全访问嵌套键
            choices = response_data.get("choices", [])
            if not choices or len(choices) == 0:
                print(f"❌ 错误：选项 {i+1} 的AI返回内容格式异常，缺少choices字段，将重试...")
                continue
            
            message = choices[0].get("message", {})
            if not message:
                print(f"❌ 错误：选项 {i+1} 的AI返回内容格式异常，缺少message字段，将重试...")
                continue
            
            raw_content = message.get("content", "").strip()
            if not raw_content:
                print(f"❌ 错误：选项 {i+1} 的AI返回内容为空，将重试...")
                continue
            
            # 直接从文本中提取信息，不依赖JSON解析
            next_options = []
            flow_update = {
                "characters": {},
                "environment": {},
                "quest_progress": "",
                "chapter_conflict_solved": False
            }
            deep_background_links = {}
            
            # 清理AI返回的内容
            cleaned_content = raw_content
            error_patterns = [
                r'(请求.*?失败|申请.*?失败|请.*?重试|侧向请求|生化或者失败联盟|出让角1|遣代表试)',
            ]
            for pattern in error_patterns:
                cleaned_content = re.sub(pattern, '', cleaned_content, flags=re.IGNORECASE)
            
            # 提取场景描述
            scene_match1 = re.search(r'【场景】：([\s\S]*?)【选项】：', cleaned_content, re.DOTALL)
            scene_match2 = re.search(r'【场景】：([\s\S]*?)$', cleaned_content, re.DOTALL)
            scene_match3 = re.search(r'【场景】：([^\n]*)', cleaned_content)
            
            if scene_match1:
                scene = scene_match1.group(1).strip()
            elif scene_match2:
                scene = scene_match2.group(1).strip()
            elif scene_match3:
                scene = scene_match3.group(1).strip()
            
            # 清理场景描述
            if scene:
                error_patterns = [
                    r'请求.*?失败|申请.*?失败|请.*?重试|侧向请求|生化或者失败联盟|出让角1|遣代表试',
                    # 保留：中文/英文/数字（含全角）+ 常用中文标点（含省略号）+ 引号
                    r"[^一-龥a-zA-Z0-9０-９\s，。！？、：；“”‘’（）《》【】…\"']+",
                ]
                for pattern in error_patterns:
                    scene = re.sub(pattern, '', scene, flags=re.IGNORECASE)
                scene = scene.strip()
                
                first_valid_char = re.search(r'[\u4e00-\u9fa5a-zA-Z"""''「【(]', scene)
                if first_valid_char:
                    scene = scene[first_valid_char.start():]
                
                if len(scene) < 10:
                    scene = "你仔细观察周围的环境，准备采取行动。"
            
            # 提取选项
            options_match1 = re.search(r'【选项】：([\s\S]*?)【世界线更新】：', cleaned_content, re.DOTALL)
            options_match2 = re.search(r'【选项】：([\s\S]*?)【深层背景关联】：', cleaned_content, re.DOTALL)
            options_match3 = re.search(r'【选项】：([\s\S]*?)$', cleaned_content, re.DOTALL)
            
            if options_match1:
                options_text = options_match1.group(1).strip()
            elif options_match2:
                options_text = options_match2.group(1).strip()
            elif options_match3:
                options_text = options_match3.group(1).strip()
            else:
                options_text = ""
            
            if options_text:
                option_lines = options_text.split('\n')
                for line in option_lines:
                    stripped_line = line.strip()
                    if stripped_line:
                        next_option = re.sub(r'^\s*\d+\.?\s*', '', stripped_line)
                        if next_option:
                            next_options.append(next_option)
            
            # 提取世界线更新
            worldline_match = re.search(r'【世界线更新】：([\s\S]*?)(?:【深层背景关联】：|$)', raw_content, re.DOTALL)
            if worldline_match:
                worldline_text = worldline_match.group(1).strip()
                
                quest_progress_match = re.search(r'主线进度：([^\n]*)', worldline_text)
                if quest_progress_match:
                    flow_update["quest_progress"] = quest_progress_match.group(1).strip()
                
                chapter_conflict_match = re.search(r'章节矛盾：([^\n]*)', worldline_text)
                if chapter_conflict_match:
                    chapter_status = chapter_conflict_match.group(1).strip()
                    flow_update["chapter_conflict_solved"] = chapter_status == "已解决"
            
            # 提取深层背景关联信息
            deep_bg_match = re.search(r'【深层背景关联】：([\s\S]*?)$', raw_content, re.DOTALL)
            if deep_bg_match:
                deep_bg_text = deep_bg_match.group(1).strip()
                deep_bg_lines = deep_bg_text.split('\n')
                
                for line in deep_bg_lines:
                    stripped_line = line.strip()
                    if stripped_line and "：" in stripped_line:
                        parts = stripped_line.split("：")
                        if len(parts) >= 2:
                            option_part = parts[0].strip()
                            char_name = parts[1].strip()
                            option_num_match = re.search(r'选项(\d+)', option_part)
                            if option_num_match:
                                option_idx = int(option_num_match.group(1)) - 1
                                deep_background_links[option_idx] = char_name
            
            # 选项剪枝
            original_options_count = len(next_options)
            original_options = next_options.copy()
            next_options = prune_options(next_options)
            pruned_count = len(next_options)
            
            # 限制选项数量为2个（确保只保留2个选项）
            if len(next_options) > 2:
                print(f"📊 选项 {i+1} 数量超过2个，限制为前2个")
                next_options = next_options[:2]
            elif len(next_options) < 2:
                # 如果选项少于2个，尝试从原始选项补充
                if original_options_count >= 2:
                    print(f"⚠️ 选项 {i+1} 剪枝后选项过少（{len(next_options)}个），使用原始选项的前2个")
                    next_options = original_options[:2] if len(original_options) >= 2 else original_options
            
            # 构建选项数据（不包含图片）
            option_data = {
                "scene": scene,
                "next_options": next_options,
                "flow_update": flow_update,
                "deep_background_links": deep_background_links
            }
            
            # 只有当场景描述和选项都有内容时，才返回结果（至少2个选项）
            if scene and next_options and len(next_options) >= 2:
                print(f"✅ 选项 {i+1} 剧情生成成功，共{len(next_options)}个选项：{next_options}")
                break
            else:
                print(f"❌ 错误：无法从选项 {i+1} 的AI返回内容中提取有效剧情信息，将重试...")
                if attempt < max_retries - 1:
                    continue
        
        except Exception as e:
            error_str = str(e)
            if "403" in error_str or "401" in error_str or "Forbidden" in error_str or "API认证失败" in error_str:
                print(f"❌ 选项 {i+1} API认证失败，停止重试，使用默认剧情")
                option_data = None
                break
            else:
                print(f"❌ 选项 {i+1} 剧情生成失败（第{attempt+1}/{max_retries}次）：{error_str}")
                if attempt < max_retries - 1:
                    print(f"🔄 将重试生成选项 {i+1} 的剧情...")
                    continue
    
    # 如果所有尝试都失败，返回默认剧情
    if not option_data or not option_data.get("scene") or not option_data.get("next_options"):
        print(f"💡 提示：选项 {i+1} 的所有生成尝试均失败，将使用默认剧情")
        option_data = {
            "scene": f"你选择了：{option}。在你的努力下，你取得了一些进展。",
            "next_options": ["继续前进", "查看当前状态", "返回上一步"],
            "flow_update": {
                "characters": {},
                "environment": {},
                "quest_progress": f"你正在执行任务：{option}",
                "chapter_conflict_solved": False
            },
            "deep_background_links": {}
        }
        scene = option_data["scene"]
    
    # 返回包含场景描述的字典，用于后续图片生成
    return {
        "index": i,
        "data": option_data,
        "scene_for_image": scene  # 保存场景描述，用于后续并行生成图片
    }

# 优化：并行生成多个场景的图片
def _generate_images_parallel(scenes_dict: Dict[int, str], global_state: Dict) -> Dict[int, Dict]:
    """
    并行生成多个场景的图片
    :param scenes_dict: 场景描述字典 {option_index: scene_description}
    :param global_state: 全局状态
    :return: 图片结果字典 {option_index: image_data}
    """
    if not scenes_dict:
        return {}
    
    print(f"🎨 开始并行生成 {len(scenes_dict)} 个场景的图片...")
    
    image_results = {}
    
    # 先检查缓存，避免重复生成
    IMAGE_CACHE_DIR = "image_cache"
    os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)
    
    # 过滤需要生成的场景（检查缓存）
    scenes_to_generate = {}
    cached_images = {}
    
    for option_index, scene in scenes_dict.items():
        if not scene:
            continue
        
        # 生成缓存键
        prompt_hash = hashlib.md5(f"{scene}_default".encode()).hexdigest()
        cache_path = Path(IMAGE_CACHE_DIR) / f"{prompt_hash}.png"
        
        # 检查缓存
        if cache_path.exists():
            print(f"✅ 选项 {option_index+1} 使用缓存的图片：{cache_path}")
            cached_images[option_index] = {
                "url": f"/image_cache/{prompt_hash}.png",
                "prompt": scene[:100],
                "style": "default",
                "width": 1024,
                "height": 1024,
                "cached": True
            }
        else:
            scenes_to_generate[option_index] = scene
    
    # 如果所有图片都已缓存，直接返回
    if not scenes_to_generate:
        print(f"✅ 所有图片都已缓存，跳过生成")
        return cached_images
    
    # 并行生成图片（限制并发数，避免API限流）
    # - yunwu.ai 速率限制更严格：默认只开 1 并发（再配合全局最小间隔）
    # - 其它 provider 可适当并发
    provider = IMAGE_GENERATION_CONFIG.get("provider", "yunwu")
    default_workers = 1 if provider == "yunwu" else 2
    max_workers_env = int(os.getenv("IMAGE_PARALLEL_MAX_WORKERS", str(default_workers)))
    max_workers = max(1, min(len(scenes_to_generate), max_workers_env))
    print(f"📊 需要生成 {len(scenes_to_generate)} 张图片，使用 {max_workers} 个并发线程（provider={provider}）")
    
    def generate_single_image(option_index: int, scene: str) -> tuple:
        """生成单个图片的包装函数，返回 (option_index, image_data, error)"""
        try:
            print(f"🎨 正在为选项 {option_index+1} 生成场景图片...")
            # 使用带缓存的图片生成，会自动下载到本地
            image_data = generate_scene_image(scene, global_state, "default", use_cache=True)
            
            if image_data and image_data.get('url'):
                # 验证图片URL
                image_url = image_data.get('url')
                if not isinstance(image_url, str):
                    image_url = str(image_url)
                    image_data['url'] = image_url
                
                # 确保本地路径格式统一
                is_local_path = image_url.startswith('/image_cache/') or image_url.startswith('image_cache/')
                if image_url.startswith('image_cache/'):
                    image_url = '/' + image_url
                    image_data['url'] = image_url
                
                # 验证URL格式
                if is_local_path or validate_image_url(image_url):
                    print(f"✅ 选项 {option_index+1} 图片生成成功：{image_url[:80]}...")
                    return (option_index, image_data, None)
                else:
                    # 尝试修复URL
                    fixed_url = fix_incomplete_url(image_url)
                    if fixed_url and validate_image_url(fixed_url):
                        image_data['url'] = fixed_url
                        image_data['cached'] = False
                        print(f"✅ 选项 {option_index+1} 图片URL修复成功：{fixed_url[:80]}...")
                        return (option_index, image_data, None)
                    else:
                        print(f"⚠️ 选项 {option_index+1} 图片URL无效，跳过")
                        return (option_index, None, "URL无效")
            else:
                print(f"⚠️ 选项 {option_index+1} 图片生成失败，无返回数据")
                print(f"💡 提示：yunwu.ai API可能返回了文本描述而非图片数据，这是API行为不一致导致的")
                print(f"💡 前端可能会使用缓存的图片或其他选项的图片作为替代")
                return (option_index, None, "无返回数据")
        
        except Exception as e:
            error_msg = str(e)
            print(f"⚠️ 选项 {option_index+1} 图片生成异常：{error_msg}")
            import traceback
            traceback.print_exc()
            return (option_index, None, error_msg)
    
    # 使用线程池并行生成（添加延迟避免速率限制）
    per_task_timeout = int(os.getenv("IMAGE_TASK_TIMEOUT_SECONDS", "120"))
    submit_delay = float(os.getenv("IMAGE_SUBMIT_DELAY_SECONDS", "2.0"))
    total_images = len(scenes_to_generate)
    completed_images = 0
    failed_images = 0
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务（添加延迟避免同时发送过多请求）
        futures = {}
        for idx, (option_index, scene) in enumerate(scenes_to_generate.items()):
            # 如果不是第一个任务，添加延迟（避免同时发送过多请求触发速率限制）
            if idx > 0:
                if submit_delay > 0:
                    print(f"⏳ 等待 {submit_delay:.1f} 秒后提交下一个图片生成任务（避免API速率限制）...")
                    time.sleep(submit_delay)
            future = executor.submit(generate_single_image, option_index, scene)
            futures[option_index] = future
        
        # 收集结果（带超时控制，避免单张图卡住整轮）
        for option_index, future in futures.items():
            completed_images += 1
            print(f"🎨 图片生成进度：{completed_images}/{total_images}")
            try:
                result = future.result(timeout=per_task_timeout)
                result_option_index, image_data, error = result
                
                if error:
                    failed_images += 1
                    print(f"⚠️ 选项 {result_option_index+1} 图片生成失败：{error}")
                elif image_data:
                    image_results[result_option_index] = image_data
                else:
                    failed_images += 1
                    print(f"⚠️ 选项 {result_option_index+1} 图片生成失败，无数据返回")
            
            except TimeoutError as e:
                # 🔧 修复：单独处理 TimeoutError，避免被通用异常处理掩盖
                failed_images += 1
                print(f"⚠️ 选项 {option_index+1} 图片生成超时（{per_task_timeout}s），将跳过该图片")
                print(f"💡 提示：图片生成任务可能因为API响应慢或网络问题而超时")
                # 尝试取消该任务，释放资源（但任务可能已经在执行，无法取消）
                try:
                    future.cancel()
                except:
                    pass
            except Exception as e:
                error_msg = str(e)
                failed_images += 1
                # 检查是否是超时相关的异常（包括 concurrent.futures.TimeoutError）
                if isinstance(e, (TimeoutError, type(None))) or "timeout" in error_msg.lower() or "超时" in error_msg or "TimeoutError" in str(type(e)):
                    print(f"⚠️ 选项 {option_index+1} 图片生成超时（{per_task_timeout}s），将跳过该图片")
                    print(f"💡 提示：图片生成任务可能因为API响应慢或网络问题而超时")
                    # 尝试取消任务
                    try:
                        future.cancel()
                    except:
                        pass
                else:
                    print(f"⚠️ 选项 {option_index+1} 图片生成异常：{error_msg}")
                    print(f"   异常类型：{type(e).__name__}")
                import traceback
                traceback.print_exc()
    
    # 合并缓存的结果和生成的结果
    image_results.update(cached_images)
    
    if failed_images:
        print(f"⚠️ 图片生成完成但有 {failed_images} 个失败，可稍后输入保存/退出后再重试或选择跳过图片。")
    print(f"✅ 图片生成完成，成功生成 {len(image_results)} 张图片（包含缓存）")
    return image_results

# 重构：实现并行批量预生成（优化版）
def generate_all_options(global_state: Dict, current_options: List[str], skip_images: bool = False) -> Dict:
    """
    生成当前场景下所有可选选项对应的剧情+下一层选项，并返回完整的剧情数据
    优化版：使用两阶段并行处理，提高生成效率
    阶段1：并行生成所有选项的文本内容（场景描述、选项等）
    阶段2：并行生成所有场景的图片并缓存
    """
    if not global_state or not current_options:
        return {}
    if not AI_API_CONFIG["api_key"]:
        print("❌ 错误：未配置Camera_Analyst_API_KEY，请在.env文件中设置")
        return {}
    
    perf = PERFORMANCE_OPTIMIZATION
    perf_enabled = perf.get("enabled", True)
    stream_first = perf_enabled and perf.get("stream_first_option", True)
    print(f"📝 开始并行生成 {len(current_options)} 个选项的剧情（优化版：两阶段并行）...")
    
    # ========== 阶段1：并行生成所有选项的文本内容 ==========
    print(f"📝 阶段1：并行生成 {len(current_options)} 个选项的文本内容...")
    all_options_data = {}
    scenes_for_images = {}  # 用于收集需要生成图片的场景描述 {option_index: scene_description}
    
    # 使用线程池并行生成文本内容
    text_workers = min(len(current_options), 4)
    with ThreadPoolExecutor(max_workers=text_workers) as executor:
        # 提交所有选项的文本生成任务
        futures = []
        for i, option in enumerate(current_options):
            future = executor.submit(_generate_single_option_text_only, i, option, global_state)
            futures.append(future)
        
        first_ready = None
        completed = 0
        total = len(futures)
        # 收集所有任务结果（支持流式先返回第一个完成的选项）
        for future in as_completed(futures):
            completed += 1
            print(f"📝 文本生成进度：{completed}/{total}")
            try:
                result = future.result()
                option_index = result["index"]
                option_data = result["data"]
                all_options_data[option_index] = option_data
                
                if stream_first and first_ready is None:
                    first_ready = {option_index: option_data}
                    global_state.setdefault("stream_first_option", {}).update(first_ready)
                    print(f"🚀 第一条选项文本已完成并缓存（流式）：{option_index+1}")
                
                # 收集需要生成图片的场景描述
                scene_for_image = result.get("scene_for_image")
                if scene_for_image:
                    scenes_for_images[option_index] = scene_for_image
            except Exception as e:
                print(f"❌ 选项文本生成异常：{str(e)}")
                import traceback
                traceback.print_exc()
    
    print(f"✅ 阶段1完成：所有选项文本内容生成完成，共 {len(all_options_data)} 个选项")
    
    # ========== 阶段2：并行生成所有场景的图片 ==========
    if skip_images:
        print("⏩ 已选择跳过本轮图片生成以加速。")
    elif scenes_for_images:
        print(f"🎨 阶段2：并行生成 {len(scenes_for_images)} 个场景的图片...")
        try:
            # 并行生成所有图片（包含缓存检查和错误处理）
            image_results = _generate_images_parallel(scenes_for_images, global_state)
            
            # 将图片结果合并回选项数据（含 scene_text_hash，确保图片与文本一一对应）
            for option_index, image_data in image_results.items():
                if option_index in all_options_data and image_data:
                    # 验证图片数据格式
                    if image_data.get('url'):
                        scene_text = all_options_data[option_index].get("scene", "") or ""
                        scene_text_hash = hashlib.md5(scene_text.encode("utf-8")).hexdigest() if scene_text.strip() else None
                        all_options_data[option_index]["scene_image"] = {
                            "url": image_data.get("url"),
                            "prompt": image_data.get("prompt", ""),
                            "style": image_data.get("style", "default"),
                            "width": image_data.get("width", 1024),
                            "height": image_data.get("height", 1024),
                            "cached": image_data.get("cached", True),
                            "scene_text_hash": scene_text_hash,
                        }
                        print(f"✅ 选项 {option_index+1} 图片已合并到选项数据")
                    else:
                        print(f"⚠️ 选项 {option_index+1} 图片数据无效，跳过")
                else:
                    print(f"⚠️ 选项 {option_index+1} 图片数据为空，跳过")
            
            print(f"✅ 阶段2完成：图片生成完成，成功合并 {len(image_results)} 张图片")
        except Exception as e:
            print(f"⚠️ 图片生成阶段出现异常：{str(e)}")
            import traceback
            traceback.print_exc()
            # 即使图片生成失败，也返回文本内容
    else:
        print(f"💡 阶段2跳过：没有需要生成图片的场景")
    
    print(f"✅ 所有选项生成完成，共生成 {len(all_options_data)} 个选项的剧情（包含文本和图片）")
    return all_options_data
