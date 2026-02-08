# -*- coding: utf-8 -*-
"""结局预测、基调与内容修改、视频任务状态。"""
import json
from typing import Dict

from src.config import AI_API_CONFIG
from src.llm.api import call_ai_api


def get_video_task_status(task_id: str) -> Dict:
    """获取视频生成任务状态（已禁用）"""
    return None


def modify_ending_tone(global_state: Dict, trigger_event: str) -> bool:
    """
    修改结局主基调，仅在触发深层背景节点时调用
    :param global_state: 全局状态
    :param trigger_event: 触发事件描述
    :return: 主基调是否发生变化
    """
    if not global_state:
        return False

    if 'hidden_ending_prediction' not in global_state:
        global_state['hidden_ending_prediction'] = generate_ending_prediction(global_state)

    current_prediction = global_state['hidden_ending_prediction']
    current_tone = current_prediction.get('main_tone', 'NE')
    core_worldview = global_state.get('core_worldview', {})
    flow_worldline = global_state.get('flow_worldline', {})

    prompt = f"""
    请作为资深游戏编剧，基于以下信息，判断是否需要修改结局主基调，**严格遵守以下要求**：

    ## 【当前信息】
    当前结局主基调：{current_tone}
    触发事件：{trigger_event}
    世界观设定：{json.dumps(core_worldview, ensure_ascii=False)}
    当前游戏状态：{json.dumps(flow_worldline, ensure_ascii=False)}

    ## 【判断要求】
    1. 只有在触发深层背景节点时（如重要人物死亡、主角遭遇生死危机、核心羁绊断裂或稳固等关键剧情），才考虑修改主基调
    2. 结局主基调类型：HE（圆满结局）、BE（悲剧结局）、NE（普通结局）等
    3. 输出格式：仅返回新的主基调类型，如 "HE"、"BE" 或 "NE"，不要返回任何多余的解释说明
    4. 如果不需要修改主基调，直接返回当前主基调

    记住：你的任务是基于触发事件判断是否需要修改结局主基调！
    """

    if AI_API_CONFIG.get("api_key"):
        try:
            request_body = {
                "model": AI_API_CONFIG["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
                "max_tokens": 50,
                "top_p": 0.7,
                "frequency_penalty": 0.5,
                "presence_penalty": 0.2,
            }

            response_data = call_ai_api(request_body)
            choices = response_data.get("choices", [])
            if choices and len(choices) > 0:
                message = choices[0].get("message", {})
                new_tone = message.get("content", "").strip()

                if new_tone != current_tone:
                    current_prediction['main_tone'] = new_tone
                    print(f"🔄 结局主基调已修改：{current_tone} → {new_tone}")
                    return True
        except Exception as e:
            print(f"❌ 修改结局主基调失败：{str(e)}")

    return False


def modify_ending_content(global_state: Dict) -> None:
    """
    修改结局大致内容，用户每完成一次交互选择后调用
    """
    if not global_state:
        return

    if 'hidden_ending_prediction' not in global_state:
        global_state['hidden_ending_prediction'] = generate_ending_prediction(global_state)

    current_prediction = global_state['hidden_ending_prediction']
    core_worldview = global_state.get('core_worldview', {})
    flow_worldline = global_state.get('flow_worldline', {})
    current_tone = current_prediction.get('main_tone', 'NE')
    current_content = current_prediction.get('content', '')

    prompt = f"""
    请作为资深游戏编剧，基于以下信息，对结局大致内容进行小幅度调整，**严格遵守以下要求**：

    ## 【当前信息】
    结局主基调：{current_tone}
    当前结局大致内容：{current_content}
    世界观设定：{json.dumps(core_worldview, ensure_ascii=False)}
    当前游戏进度：{json.dumps(flow_worldline, ensure_ascii=False)}

    ## 【修改要求】
    1. 基于当前的结局主基调和游戏进度，对内容进行小幅度调整
    2. 可以补充细节、微调情节走向，但不颠覆核心框架
    3. 输出格式：仅返回修改后的结局大致内容，不要返回任何多余的解释说明
    4. 所有输出必须使用中文

    记住：你的任务是对结局大致内容进行小幅度调整，不要颠覆核心框架！
    """

    if AI_API_CONFIG.get("api_key"):
        try:
            request_body = {
                "model": AI_API_CONFIG["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
                "max_tokens": 500,
                "top_p": 0.7,
                "frequency_penalty": 0.5,
                "presence_penalty": 0.2,
            }

            response_data = call_ai_api(request_body)
            choices = response_data.get("choices", [])
            if choices and len(choices) > 0:
                message = choices[0].get("message", {})
                new_content = message.get("content", "").strip()
                current_prediction['content'] = new_content
        except Exception as e:
            print(f"❌ 修改结局大致内容失败：{str(e)}")


def generate_ending_prediction(global_state: Dict) -> Dict:
    """
    生成隐藏的结局预测，包含结局主基调和大致内容
    """
    if not global_state:
        return {}

    core_worldview = global_state.get('core_worldview', {})

    prompt = f"""
    请作为资深游戏编剧，基于以下世界观设定，生成一个完整的结局预测，**严格遵守以下要求**：

    ## 【世界观设定】
    {json.dumps(core_worldview, ensure_ascii=False)}

    ## 【生成要求】
    1. 生成内容必须严格符合世界观设定
    2. 结局预测包含两个核心部分：
       - 结局主基调：如HE（圆满结局）、BE（悲剧结局）、NE（普通结局）等
       - 结局大致内容：基于主基调生成的结局核心情节框架，例如 "主角守护羁绊角色达成和解" 这类文本化的情节描述
    3. 输出格式：
       结局主基调：[主基调类型]
       结局大致内容：[内容描述]
    4. 所有输出必须使用中文，不要返回任何多余的解释说明

    记住：你的任务是生成一个合理的结局预测，作为后台调控剧情的依据！
    """

    if AI_API_CONFIG.get("api_key"):
        try:
            request_body = {
                "model": AI_API_CONFIG["model"],
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
                "max_tokens": 500,
                "top_p": 0.7,
                "frequency_penalty": 0.5,
                "presence_penalty": 0.2,
            }

            response_data = call_ai_api(request_body)
            choices = response_data.get("choices", [])
            if choices and len(choices) > 0:
                message = choices[0].get("message", {})
                raw_content = message.get("content", "").strip()

                ending_prediction = {}
                for line in raw_content.split('\n'):
                    line = line.strip()
                    if "结局主基调：" in line:
                        ending_prediction['main_tone'] = line.split("结局主基调：")[1].strip()
                    elif "结局大致内容：" in line:
                        ending_prediction['content'] = line.split("结局大致内容：")[1].strip()

                return ending_prediction
        except Exception as e:
            print(f"❌ 生成结局预测失败：{str(e)}")

    return {
        "main_tone": "NE",
        "content": "主角完成了主要任务，虽然过程中经历了许多困难，但最终达成了预期目标"
    }
