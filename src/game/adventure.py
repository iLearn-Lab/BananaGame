# -*- coding: utf-8 -*-
"""游戏主循环：TextAdventureGame 类。"""
import json
import os
import random
from datetime import datetime
from typing import Dict, List, Tuple, Optional

from src.constants import DIFFICULTY_SETTINGS, TONE_CONFIGS, PROTAGONIST_ATTR_OPTIONS
from src.utils.io_utils import safe_input
from src.llm.global_gen import llm_generate_global
from src.llm.local_gen import llm_generate_local
from src.story.ending import (
    get_video_task_status,
    modify_ending_tone,
    modify_ending_content,
    generate_ending_prediction,
)
from src.story.options import generate_all_options


class TextAdventureGame:
    def __init__(self):
        self.global_state: Dict = {}
        self.is_running: bool = True
        self.ending_triggered: bool = False
        self.protagonist_attr: Dict = {}
        self.difficulty: str = ""
        self.last_options: List[str] = []  # 记录上一轮的选项
        self.save_dir: str = "saves"  # 存档目录
        
        # 新增：缓存相关属性
        self.scene_cache: Dict = {}  # 场景缓存，key为场景ID，value为2个选项的完整剧情数据
        self.current_scene_id: str = "initial"  # 当前场景ID
        self.generating_task = None  # 异步生成任务
        self.generation_cancelled = False  # 生成取消标志
        self.skip_images: bool = False  # 是否跳过图片生成以加速
        self.max_autosaves: int = 5  # 自动存档最多保留数量
        
        # 确保存档目录存在
        if not os.path.exists(self.save_dir):
            os.makedirs(self.save_dir)

    def _select_protagonist_attr(self):
        print("\n🎭 请为你的主角选择属性：")
        for attr_name, options in PROTAGONIST_ATTR_OPTIONS.items():
            print(f"\n{attr_name}选项：")
            for idx, opt in enumerate(options, 1):
                print(f"   {idx}. {opt}")
            while True:
                try:
                    choice_str = safe_input(f"请选择{attr_name}（输入序号1-{len(options)}，默认1）：", default="1")
                    choice = int(choice_str)
                    if 1 <= choice <= len(options):
                        self.protagonist_attr[attr_name] = options[choice-1]
                        break
                    else:
                        print(f"请输入1-{len(options)}之间的数字！")
                except ValueError:
                    print("请输入有效的数字序号！")
        print(f"\n✅ 你的主角属性：{self.protagonist_attr}")

    def _select_difficulty(self):
        print("\n⚔️ 请选择游戏难度：")
        difficulty_list = list(DIFFICULTY_SETTINGS.keys())
        for idx, diff in enumerate(difficulty_list, 1):
            desc = DIFFICULTY_SETTINGS[diff]
            print(f"   {idx}. {diff} - 容错率：{desc['剧情容错率']}，矛盾难度：{desc['矛盾解决难度']}，提示频率：{desc['提示频率']}")
        while True:
            try:
                choice_str = safe_input(f"请选择难度（输入序号1-{len(difficulty_list)}，默认2中等）：", default="2")
                choice = int(choice_str)
                if 1 <= choice <= len(difficulty_list):
                    self.difficulty = difficulty_list[choice-1]
                    break
                else:
                    print(f"请输入1-{len(difficulty_list)}之间的数字！")
            except ValueError:
                print("请输入有效的数字序号！")
        print(f"\n✅ 游戏难度已选择：{self.difficulty}")
    
    def _select_tone(self):
        """
        基调选择环节：可选AI随机/玩家手动选择
        """
        print("\n🎨 请选择故事基调：")
        print("1. AI随机选择")
        print("2. 手动选择")
        
        while True:
            choice = safe_input("请选择操作（输入序号1-2，默认1随机）：", default="1")
            if choice == "1":
                # AI随机选择基调
                import random
                tone_key = random.choice(list(TONE_CONFIGS.keys()))
                tone = TONE_CONFIGS[tone_key]
                print(f"\n🎲 AI随机选择了基调：{tone['name']}")
                print(f"📝 基调描述：{tone['description']}")
                return tone_key
            elif choice == "2":
                # 手动选择基调
                print("\n🎨 可选基调：")
                tone_list = list(TONE_CONFIGS.items())
                for idx, (key, tone) in enumerate(tone_list, 1):
                    print(f"   {idx}. {tone['name']} - {tone['description'][:30]}...")
                
                while True:
                    try:
                        tone_choice_str = safe_input(f"请选择基调（输入序号1-{len(tone_list)}，默认1）：", default="1")
                        tone_choice = int(tone_choice_str)
                        if 1 <= tone_choice <= len(tone_list):
                            tone_key, tone = tone_list[tone_choice-1]
                            print(f"\n✅ 你选择了基调：{tone['name']}")
                            print(f"📝 基调描述：{tone['description']}")
                            return tone_key
                        else:
                            print(f"请输入1-{len(tone_list)}之间的数字！")
                    except ValueError:
                        print("请输入有效的数字序号！")
            else:
                print("请输入1-2之间的数字！")

    def _show_game_settings(self):
        if not self.global_state:
            return
        core = self.global_state.get('core_worldview', {})
        flow = self.global_state.get('flow_worldline', {})
        
        # 安全获取当前章节
        current_chapter_id = flow.get('current_chapter', 'chapter1')
        chapters = core.get('chapters', {})
        current_chapter = chapters.get(current_chapter_id, {})
        
        # 获取章节编号（用于显示）
        chapter_num = 1
        if current_chapter_id.startswith('chapter'):
            try:
                chapter_num = int(current_chapter_id[7:])
            except (ValueError, IndexError):
                chapter_num = 1
        
        print("\n📖 游戏核心设定告知：")
        print(f"1. 游戏风格：{core.get('game_style', '未知')}")
        print(f"2. 世界观基础：{core.get('world_basic_setting', '')[:50]}...")
        print(f"3. 主角核心能力：{core.get('protagonist_ability', '未知')}")
        print(f"4. 当前章节（第{chapter_num}章）核心矛盾：{current_chapter.get('main_conflict', '未知')}")
        print(f"5. 章节结束条件：{current_chapter.get('conflict_end_condition', '未知')}")
        
        # 安全获取难度信息
        difficulty_info = DIFFICULTY_SETTINGS.get(self.difficulty, {})
        print(f"6. 游戏难度：{self.difficulty}（{difficulty_info.get('矛盾解决难度', '未知')}难度）")
        print(f"7. 主线任务：{core.get('main_quest', '')[:50]}...")
        safe_input("\n请按回车键确认并开始游戏...", default="")

    def _check_chapter_conflict(self):
        flow = self.global_state.get('flow_worldline', {})
        if flow.get('chapter_conflict_solved', False):
            current_chapter = flow.get('current_chapter', 'chapter1')
            print(f"\n🎉 本章（{current_chapter}）核心矛盾已解决！章节结束。")
            # 自动快速存档（防止断档丢进度）
            auto_name = f"auto_{current_chapter}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            self.save_game(auto_name)
            self._prune_autosaves()
            
            # 章节深化：每完成一个章节，自动深化角色的深层背景
            self._deepen_character_backgrounds()
            
            while True:
                end_choice = safe_input("是否选择结束游戏？（输入 是/否，默认否）：", default="否")
                if end_choice in ["是", "否"]:
                    if end_choice == "是":
                        self.ending_triggered = True
                    else:
                        core = self.global_state.get('core_worldview', {})
                        chapters = core.get('chapters', {})
                        chapter_list = list(chapters.keys())
                        
                        if current_chapter in chapter_list:
                            current_idx = chapter_list.index(current_chapter)
                            if current_idx + 1 < len(chapter_list):
                                next_chapter = chapter_list[current_idx + 1]
                                # 安全更新世界线状态
                                if 'flow_worldline' not in self.global_state:
                                    self.global_state['flow_worldline'] = {}
                                self.global_state['flow_worldline']['current_chapter'] = next_chapter
                                self.global_state['flow_worldline']['chapter_conflict_solved'] = False
                                print(f"\n🔄 进入下一章：{next_chapter}")
                                
                                # 安全获取下一章核心矛盾
                                next_chapter_data = chapters.get(next_chapter, {})
                                print(f"本章核心矛盾：{next_chapter_data.get('main_conflict', '未知')}")
                            else:
                                print("\n📚 所有章节已完成！")
                                self.ending_triggered = True
                        else:
                            print("\n📚 无法找到当前章节信息，游戏结束！")
                            self.ending_triggered = True
                    break
                else:
                    print("请输入 是 或 否！")
    
    def _check_info_gap_threshold(self):
        """
        检查信息差数量是否达到阈值，若达到则生成隐藏的剧情深化内容
        """
        core = self.global_state.get('core_worldview', {})
        flow = self.global_state.get('flow_worldline', {})
        
        # 确保信息差记录点存在
        if 'info_gap_record' not in flow:
            flow['info_gap_record'] = {
                "entries": [],
                "current_super_choice": None,
                "pending_super_plot": None
            }
        
        info_gap_record = flow['info_gap_record']
        entries = info_gap_record['entries']
        
        # 计算未发现的信息差数量
        undiscovered_entries = [entry for entry in entries if not entry.get('discovered', False)]
        
        # 如果未发现的信息差数量达到5条，生成隐藏的剧情深化内容
        if len(undiscovered_entries) >= 5:
            # 检查是否已有等待触发的隐藏剧情
            if info_gap_record.get('pending_super_plot') is None:
                # 调用AI生成隐藏的剧情深化内容
                if AI_API_CONFIG.get("api_key"):
                    try:
                        # 构建信息差摘要
                        info_gap_summary = "\n".join([f"- {entry['content'][:100]}..." for entry in undiscovered_entries[:5]])
                        
                        # 构建Prompt，生成隐藏的剧情深化内容
                        prompt = f"""
                        请根据以下信息差内容，生成一个自然嵌入到常规剧情中的深化内容，**严格遵守以下要求**：
                        
                        ## 【信息差摘要】
                        {info_gap_summary}
                        
                        ## 【游戏世界观】
                        {json.dumps(core, ensure_ascii=False)}
                        
                        ## 【当前游戏状态】
                        {json.dumps(flow, ensure_ascii=False)}
                        
                        ## 【生成要求】
                        1. 自然嵌入到常规剧情中，不能作为独立模块强行插入
                        2. 深度贴合游戏的核心剧情脉络，是主线情节的有机延伸
                        3. 通过深层背景信息与已有剧情的前后呼应、关键悬念的逐步揭晓，让玩家感受到揭秘、反转带来的惊喜
                        4. 生成内容要符合游戏世界观和当前状态
                        5. 输出格式：
                           - 首先输出剧情触发选项描述（自然融入常规选项中，无特殊标记）
                           - 然后输出完整的剧情内容
                           - 使用### 选项：和### 剧情：作为分隔符
                        6. 剧情应包含多个隐藏信息的自然揭露，形成前后呼应
                        
                        记住：你的任务是生成一个自然融入主线的剧情深化内容，提升玩家的沉浸感和惊喜感！
                        """
                        
                        # 构建请求体
                        request_body = {
                            "model": AI_API_CONFIG["model"],
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.5,
                            "max_tokens": 2000,
                            "top_p": 0.7,
                            "frequency_penalty": 0.5,
                            "presence_penalty": 0.2,
                            "timeout": 150
                        }
                        
                        # 调用AI API
                        response_data = call_ai_api(request_body)
                        
                        # 提取AI响应
                        choices = response_data.get("choices", [])
                        if choices and len(choices) > 0:
                            message = choices[0].get("message", {})
                            raw_content = message.get("content", "").strip()
                            
                            # 解析生成的内容
                            if "### 选项：" in raw_content and "### 剧情：" in raw_content:
                                option_part = raw_content.split("### 选项：")[1].split("### 剧情：")[0].strip()
                                plot_part = raw_content.split("### 剧情：")[1].strip()
                                
                                # 保存隐藏的剧情深化内容
                                info_gap_record['pending_super_plot'] = {
                                    "plot": plot_part,
                                    "used_entries": [entry['id'] for entry in undiscovered_entries[:5]]
                                }
                                info_gap_record['current_super_choice'] = option_part
                    except Exception as e:
                        # 生成失败时不向玩家显示任何信息
                        pass
        
    def _deepen_character_backgrounds(self):
        """
        章节深化：每完成一个章节，自动深化角色的深层背景内容
        """
        print("\n🔍 章节深化：开始深化角色深层背景...")
        
        core = self.global_state.get('core_worldview', {})
        characters = core.get('characters', {})
        flow = self.global_state.get('flow_worldline', {})
        flow_characters = flow.get('characters', {})
        
        # 为每个角色添加深化进度字段（如果不存在）
        for char_name in characters:
            if char_name not in flow_characters:
                flow_characters[char_name] = {
                    "thought": "",
                    "physiology": "健康",
                    "deep_background_unlocked": False
                }
            
            # 确保角色有深化进度字段
            if "deep_background_depth" not in flow_characters[char_name]:
                flow_characters[char_name]["deep_background_depth"] = 0
            
            # 增加深化进度
            flow_characters[char_name]["deep_background_depth"] += 1
            depth = flow_characters[char_name]["deep_background_depth"]
            
            # 如果AI API可用，调用AI深化背景
            if AI_API_CONFIG.get("api_key"):
                try:
                    print(f"📝 正在深化{char_name}的深层背景（深度：{depth}）...")
                    
                    # 构建Prompt，深化角色深层背景
                    prompt = f"""
                    请根据以下信息深化角色的深层背景内容，**严格遵守以下要求**：
                    
                    ## 【角色信息】
                    角色名称：{char_name}
                    当前深层背景：{characters[char_name].get('deep_background', '暂无')}
                    角色核心性格：{characters[char_name].get('core_personality', '未知')}
                    角色浅层背景：{characters[char_name].get('shallow_background', '未知')}
                    当前章节：{flow.get('current_chapter', 'chapter1')}
                    主线进度：{flow.get('quest_progress', '未知')}
                    深化深度：第{depth}次深化
                    
                    ## 【深化要求】
                    1. 补充更多细节，使深层背景更加丰富
                    2. 将深层背景与主线任务更紧密地关联
                    3. 保持原有的核心设定不变
                    4. 深化内容要符合游戏世界观
                    5. 输出格式：直接输出深化后的深层背景内容，不要添加任何前缀或后缀
                    
                    记住：你的任务是深化角色的深层背景，使其更加丰富和关联主线！
                    """
                    
                    # 构建请求体
                    request_body = {
                        "model": AI_API_CONFIG["model"],
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.4,
                        "max_tokens": 1000,
                        "top_p": 0.7,
                        "frequency_penalty": 0.5,
                        "presence_penalty": 0.2,
                        "timeout": 100
                    }
                    
                    # 调用AI API
                    response_data = call_ai_api(request_body)
                    
                    # 提取AI响应
                    choices = response_data.get("choices", [])
                    if choices and len(choices) > 0:
                        message = choices[0].get("message", {})
                        new_background = message.get("content", "").strip()
                        
                        if new_background:
                            # 更新角色的深层背景
                            characters[char_name]['deep_background'] = new_background
                            print(f"✅ {char_name}的深层背景已深化至第{depth}级")
                            
                            # 记录信息差条目
                            if 'info_gap_record' not in flow:
                                flow['info_gap_record'] = {
                                    "entries": [],
                                    "current_super_choice": None,
                                    "pending_super_plot": None
                                }
                            info_gap_record = flow['info_gap_record']
                            
                            info_gap_entry = {
                                "id": f"info_gap_{len(info_gap_record['entries']) + 1}",
                                "type": "deep_background_deepen",
                                "char_name": char_name,
                                "content": new_background,
                                "discovered": False,
                                "timestamp": str(datetime.now())
                            }
                            info_gap_record['entries'].append(info_gap_entry)
                            
                            # 触发深层背景节点，修改结局主基调
                            trigger_event = f"{char_name}的深层背景已深化至第{depth}级"
                            tone_changed = modify_ending_tone(self.global_state, trigger_event)
                            if tone_changed:
                                print("🔄 结局主基调已更新")
                except Exception as e:
                    print(f"❌ 深化{char_name}的深层背景失败：{str(e)}")
            else:
                # AI API不可用，使用默认深化
                old_background = characters[char_name]['deep_background']
                new_background = old_background + f"\n（第{depth}章深化：角色经历更加丰富，与主线的关联更加紧密）"
                characters[char_name]['deep_background'] = new_background
                print(f"✅ {char_name}的深层背景已使用默认方式深化至第{depth}级")
                
                # 记录信息差条目
                if 'info_gap_record' not in flow:
                    flow['info_gap_record'] = {
                        "entries": [],
                        "current_super_choice": None,
                        "pending_super_plot": None
                    }
                info_gap_record = flow['info_gap_record']
                
                info_gap_entry = {
                    "id": f"info_gap_{len(info_gap_record['entries']) + 1}",
                    "type": "deep_background_deepen",
                    "char_name": char_name,
                    "content": new_background,
                    "discovered": False,
                    "timestamp": str(datetime.now())
                }
                info_gap_record['entries'].append(info_gap_entry)
        
        print("\n✅ 所有角色深层背景深化完成！")
        
        # 检查信息差阈值
        self._check_info_gap_threshold()

    def start(self):
        print("🎮 欢迎来到AI驱动的沉浸式文本冒险游戏！")
        while self.is_running:
            # 显示主菜单
            print("\n=== 游戏主菜单 ===")
            print("1. 开始新游戏")
            print("2. 加载游戏")
            print("3. 存档管理")
            print("4. 退出游戏")
            
            menu_choice = safe_input("请选择操作（输入序号1-4，默认4退出）：", default="4")
            
            if menu_choice == "1":
                # 开始新游戏
                self._select_protagonist_attr()
                self._select_difficulty()
                # 新增：基调选择环节
                selected_tone = self._select_tone()
                user_idea = safe_input("\n请输入你的游戏主题（如：玄幻修仙·寻找九转金丹）：")
                if not user_idea:
                    print("⚠️ 主题不能为空，已取消")
                    continue
                
                print("✅ AI正在构建完整游戏世界观，这可能需要1-3分钟，请耐心等待...")
                self.global_state = llm_generate_global(user_idea, self.protagonist_attr, self.difficulty, selected_tone)
                if not self.global_state:
                    print("❌ 世界观生成失败，请重新输入主题！")
                    continue
                
                # 将选定的基调保存到global_state中
                self.global_state['tone'] = selected_tone
                
                # 生成并保存隐藏的结局预测
                ending_prediction = generate_ending_prediction(self.global_state)
                self.global_state['hidden_ending_prediction'] = ending_prediction
                print("✅ 隐藏结局预测已生成")

                self._show_game_settings()
                
                # 进入游戏循环
                self._interaction_loop()
            
            elif menu_choice == "2":
                # 加载游戏
                saves = self.list_saves()
                if not saves:
                    print("\n📭 暂无存档")
                    continue
                
                print("\n📋 现有存档：")
                for idx, save_name in enumerate(saves, 1):
                    print(f"   {idx}. {save_name}")
                
                load_choice = safe_input("请选择要加载的存档序号：")
                try:
                    load_idx = int(load_choice) - 1
                    if 0 <= load_idx < len(saves):
                        if self.load_game(saves[load_idx]):
                            # 生成前情提要
                            self._generate_recap()
                            # 加载成功后直接进入游戏循环
                            self._interaction_loop()
                    else:
                        print("❌ 无效的存档序号")
                except ValueError:
                    print("❌ 请输入有效的数字序号")
            
            elif menu_choice == "3":
                # 存档管理
                if self._manage_saves():
                    # 从存档管理中成功加载了游戏，直接进入游戏循环
                    self._interaction_loop()
            
            elif menu_choice == "4":
                # 退出游戏
                print("\n👋 感谢游玩！游戏已退出。")
                self.is_running = False
                break
            
            else:
                print("❌ 请输入1-4之间的数字")

    def _interaction_loop(self):
        """【核心修改3】记录上一轮选项，传递给llm_generate_local"""
        # 本轮是否跳过图片生成（玩家可选加速）
        skip_choice = safe_input("是否跳过本局图片生成以加速？（是/否，默认否）：", default="否")
        self.skip_images = skip_choice == "是"
        # 初始剧情生成和预生成
        print("✅ 正在生成初始剧情和选项，请稍候...")
        # 使用原始方式生成初始剧情
        initial_scenes = llm_generate_local(self.global_state, "1", ["开始游戏"])
        if not initial_scenes:
            print("❌ 初始剧情生成失败，游戏结束！")
            return
        
        # 展示初始剧情
        for i, scene in enumerate(initial_scenes, 1):
            print(f"\n--- 第 {i} 段剧情 ---")
            print(f"📜 场景：{scene.get('scene', '无场景描述')}")
            
            # 安全获取选项
            options = scene.get("options", [])
            if options:
                print("🔍 可选操作：")
                # 记录当前选项为“下一轮的上一轮选项”
                self.last_options = options
                for idx, opt in enumerate(options, 1):
                    print(f"   {idx}. {opt}")
            else:
                print("🔍 可选操作：无")
                self.last_options = []
                self.current_scene_id = "initial"

            if 'flow_update' in scene:
                # 安全更新世界线状态
                if 'flow_worldline' not in self.global_state:
                    self.global_state['flow_worldline'] = {}
                self.global_state['flow_worldline'].update(scene['flow_update'])
                
                # 检查角色深层背景解锁
                characters_update = scene['flow_update'].get('characters', {})
                for char_name, char_info in characters_update.items():
                    if char_info.get('deep_background_unlocked'):
                        core = self.global_state.get('core_worldview', {})
                        characters = core.get('characters', {})
                        char_data = characters.get(char_name, {})
                        deep_bg = char_data.get('deep_background', '')
                        print(f"\n🔓 解锁角色深层背景：{char_name} → {deep_bg}")
        
        # 生成初始选项对应的剧情（同步生成）
        print("\n✅ 正在生成选项对应的剧情，请稍候...")
        if self.last_options:
            # 同步生成所有选项对应的剧情
            self.current_scene_id = f"scene_{len(self.scene_cache) + 1}"
            all_options_data = generate_all_options(self.global_state, self.last_options, skip_images=self.skip_images)
            self.scene_cache[self.current_scene_id] = all_options_data
            print(f"✅ 所有选项剧情生成完成，场景ID：{self.current_scene_id}")
        
        # 进入游戏循环
        while not self.ending_triggered:
            # 快速提示当前进度，减少玩家迷茫
            self._quick_recap()
            user_input = safe_input("\n请输入你的选择/行动（'quit'退出，'save'存档）：")
            
            # 检查退出命令
            if user_input.lower() in ['quit', 'exit', '退出', '结束']:
                # 提供存档选项
                while True:
                    save_choice = safe_input("\n是否保存当前游戏进度？（输入 是/否，默认否）：", default="否")
                    if save_choice in ["是", "否"]:
                        if save_choice == "是":
                            save_name = safe_input("请输入存档名称（默认auto_quit）：", default="auto_quit")
                            if save_name:
                                self.save_game(save_name)
                        self.ending_triggered = True
                        break
                    else:
                        print("请输入 是 或 否！")
                break
            
            # 检查保存命令
            if user_input.lower() in ['save', '保存']:
                save_name = safe_input("\n请输入存档名称（默认auto_save）：", default="auto_save")
                if save_name:
                    self.save_game(save_name)
                continue
                
            if not user_input:
                print("⏳ 请输入有效的交互内容！")
                continue

            # 解析用户选择
            try:
                selected_option_idx = int(user_input) - 1
                if selected_option_idx < 0 or selected_option_idx >= len(self.last_options):
                    print("❌ 错误：无效的选项序号")
                    continue
            except ValueError:
                print("❌ 错误：请输入有效的数字序号")
                continue

            # 检查是否选择了爽点剧情选项
            flow = self.global_state.get('flow_worldline', {})
            info_gap_record = flow.get('info_gap_record', {})
            current_super_choice = info_gap_record.get('current_super_choice')
            pending_super_plot = info_gap_record.get('pending_super_plot')
            
            selected_option = self.last_options[selected_option_idx]
            
            # 如果选择了爽点剧情选项
            if current_super_choice and current_super_choice == selected_option:
                print("\n" + "="*50)
                
                if pending_super_plot:
                    # 显示爽点剧情（作为常规剧情的一部分，无特殊标记）
                    print(pending_super_plot['plot'])
                    
                    # 清除使用过的信息差条目
                    used_entries = pending_super_plot.get('used_entries', [])
                    entries = info_gap_record.get('entries', [])
                    
                    for entry in entries:
                        if entry['id'] in used_entries:
                            entry['discovered'] = True
                    
                    # 清除当前的爽点剧情选项和等待触发的剧情
                    info_gap_record['current_super_choice'] = None
                    info_gap_record['pending_super_plot'] = None
                    
                    print("="*50)
                    
                    # 检查信息差阈值，生成新的爽点剧情
                    self._check_info_gap_threshold()
                    
                    # 重新显示当前可选操作
                    print("\n🔍 可选操作：")
                    for idx, opt in enumerate(self.last_options, 1):
                        print(f"   {idx}. {opt}")
                    
                    continue

            # 检查当前场景ID对应的缓存是否存在
            if self.current_scene_id in self.scene_cache:
                print("✅ 从缓存中读取剧情数据...")
                # 从缓存中获取剧情数据
                scene_data = self.scene_cache[self.current_scene_id]
                
                if selected_option_idx in scene_data:
                    option_data = scene_data[selected_option_idx]
                    
                    # 检查当前选项是否关联到深层背景
                    if 'deep_background_links' in option_data and selected_option_idx in option_data['deep_background_links']:
                        char_name = option_data['deep_background_links'][selected_option_idx]
                        core = self.global_state.get('core_worldview', {})
                        characters = core.get('characters', {})
                        
                        if char_name in characters:
                            # 解锁该角色的深层背景
                            flow = self.global_state.get('flow_worldline', {})
                            flow_characters = flow.get('characters', {})
                            
                            if char_name not in flow_characters:
                                flow_characters[char_name] = {
                                    "thought": "",
                                    "physiology": "健康",
                                    "deep_background_unlocked": False,
                                    "deep_background_depth": 0
                                }
                            
                            # 只有在未解锁状态下才解锁，同一个深层背景不会被反复解锁
                            if not flow_characters[char_name].get('deep_background_unlocked', False):
                                flow_characters[char_name]['deep_background_unlocked'] = True
                                deep_bg = characters[char_name].get('deep_background', '无')
                                
                                # 获取信息差记录点
                                if 'info_gap_record' not in self.global_state['flow_worldline']:
                                    self.global_state['flow_worldline']['info_gap_record'] = {
                                        "entries": [],
                                        "current_super_choice": None,
                                        "pending_super_plot": None
                                    }
                                info_gap_record = self.global_state['flow_worldline']['info_gap_record']
                                
                                # 记录信息差条目
                                info_gap_entry = {
                                    "id": f"info_gap_{len(info_gap_record['entries']) + 1}",
                                    "type": "deep_background_unlock",
                                    "char_name": char_name,
                                    "content": deep_bg,
                                    "discovered": False,
                                    "timestamp": str(datetime.now())
                                }
                                info_gap_record['entries'].append(info_gap_entry)
                                
                                # 触发深层背景节点，修改结局主基调
                                trigger_event = f"{char_name}的深层背景被解锁"
                                tone_changed = modify_ending_tone(self.global_state, trigger_event)
                                
                                # 后续剧情会因深层剧情的解锁，转而围绕深层剧情展开（通过修改global_state中的相关标志实现）
                                # 这里添加一个标志，让后续剧情生成时围绕已解锁的深层背景展开
                                if 'deep_background_unlocked_flag' not in flow:
                                    flow['deep_background_unlocked_flag'] = []
                                if char_name not in flow['deep_background_unlocked_flag']:
                                    flow['deep_background_unlocked_flag'].append(char_name)
                    
                    # 展示选中的剧情
                    print(f"\n--- 第 {1} 段剧情 ---")
                    print(f"📜 场景：{option_data['scene']}")
                    
                    # 更新世界线
                    if 'flow_update' in option_data:
                        # 安全更新世界线状态
                        if 'flow_worldline' not in self.global_state:
                            self.global_state['flow_worldline'] = {}
                        self.global_state['flow_worldline'].update(option_data['flow_update'])
                        
                        # 检查角色深层背景解锁
                        characters_update = option_data['flow_update'].get('characters', {})
                        for char_name, char_info in characters_update.items():
                            if char_info.get('deep_background_unlocked'):
                                core = self.global_state.get('core_worldview', {})
                                characters = core.get('characters', {})
                                char_data = characters.get(char_name, {})
                                deep_bg = char_data.get('deep_background', '')
                                print(f"\n🔓 解锁角色深层背景：{char_name} → {deep_bg}")
                    
                    # 生成下一轮选项对应的剧情（同步生成）
                    next_options = option_data['next_options']
                    
                    # 检查是否存在等待触发的爽点剧情
                    flow = self.global_state.get('flow_worldline', {})
                    info_gap_record = flow.get('info_gap_record', {})
                    current_super_choice = info_gap_record.get('current_super_choice')
                    
                    # 如果存在爽点剧情选项，添加到当前选项列表中（无明显标记）
                    if current_super_choice:
                        next_options.append(current_super_choice)
                    
                    if next_options:
                        print("🔍 可选操作：")
                        # 记录当前选项为“下一轮的上一轮选项”
                        self.last_options = next_options
                        for idx, opt in enumerate(next_options, 1):
                            print(f"   {idx}. {opt}")
                    
                        # 生成下一轮选项对应的剧情（同步生成）
                        print("\n✅ 生成选项对应的剧情...")
                        # 删除当前场景的缓存，释放内存
                        del self.scene_cache[self.current_scene_id]
                        # 生成新的场景ID
                        self.current_scene_id = f"scene_{len(self.scene_cache) + 1}"
                        # 同步生成所有选项对应的剧情
                        all_options_data = generate_all_options(self.global_state, next_options, skip_images=self.skip_images)
                        self.scene_cache[self.current_scene_id] = all_options_data
                        print(f"✅ 所有选项剧情生成完成，场景ID：{self.current_scene_id}")
                    else:
                        print("🔍 可选操作：无")
                        self.last_options = []
                        self.current_scene_id = "initial"
                    
                    # 检查信息差阈值
                    self._check_info_gap_threshold()
                else:
                    print("❌ 错误：缓存中未找到对应的选项数据")
                    # 使用原始方式生成剧情
                    print("✅ AI正在生成后续剧情...")
                    
                    # 删除当前场景的旧缓存，释放内存
                    if self.current_scene_id in self.scene_cache:
                        del self.scene_cache[self.current_scene_id]
                        print(f"✅ 已删除旧场景缓存：{self.current_scene_id}")
                    
                    local_scenes = llm_generate_local(self.global_state, user_input, self.last_options)
                    
                    if local_scenes:
                        # 展示剧情
                        for i, scene in enumerate(local_scenes, 1):
                            # 检查当前选项是否关联到深层背景（针对当前选择的选项）
                            if 'deep_background_links' in scene and selected_option_idx in scene['deep_background_links']:
                                char_name = scene['deep_background_links'][selected_option_idx]
                                core = self.global_state.get('core_worldview', {})
                                characters = core.get('characters', {})
                                
                                if char_name in characters:
                                    # 解锁该角色的深层背景
                                    flow = self.global_state.get('flow_worldline', {})
                                    flow_characters = flow.get('characters', {})
                                    
                                    if char_name not in flow_characters:
                                        flow_characters[char_name] = {
                                            "thought": "",
                                            "physiology": "健康",
                                            "deep_background_unlocked": False,
                                            "deep_background_depth": 0
                                        }
                                    
                                    # 只有在未解锁状态下才解锁，同一个深层背景不会被反复解锁
                                    if not flow_characters[char_name].get('deep_background_unlocked', False):
                                        flow_characters[char_name]['deep_background_unlocked'] = True
                                        deep_bg = characters[char_name].get('deep_background', '无')
                                        
                                        # 获取信息差记录点
                                        if 'info_gap_record' not in flow:
                                            flow['info_gap_record'] = {
                                                "entries": [],
                                                "current_super_choice": None,
                                                "pending_super_plot": None
                                            }
                                        info_gap_record = flow['info_gap_record']
                                        
                                        # 记录信息差条目
                                        info_gap_entry = {
                                            "id": f"info_gap_{len(info_gap_record['entries']) + 1}",
                                            "type": "deep_background_unlock",
                                            "char_name": char_name,
                                            "content": deep_bg,
                                            "discovered": False,
                                            "timestamp": str(datetime.now())
                                        }
                                        info_gap_record['entries'].append(info_gap_entry)
                                        
                                        # 触发深层背景节点，修改结局主基调
                                        trigger_event = f"{char_name}的深层背景被解锁"
                                        tone_changed = modify_ending_tone(self.global_state, trigger_event)
                                        
                                        # 添加标志，让后续剧情生成时围绕已解锁的深层背景展开
                                        if 'deep_background_unlocked_flag' not in flow:
                                            flow['deep_background_unlocked_flag'] = []
                                        if char_name not in flow['deep_background_unlocked_flag']:
                                            flow['deep_background_unlocked_flag'].append(char_name)
                            
                            print(f"\n--- 第 {i} 段剧情 ---")
                            print(f"📜 场景：{scene.get('scene', '无场景描述')}")
                            
                            # 安全获取选项
                            options = scene.get("options", [])
                            if options:
                                print("🔍 可选操作：")
                                # 记录当前选项为“下一轮的上一轮选项”
                                self.last_options = options
                                for idx, opt in enumerate(options, 1):
                                    print(f"   {idx}. {opt}")
                                
                                # 生成下一轮选项对应的剧情（同步生成）
                                print("\n✅ 生成选项对应的剧情...")
                                # 生成新的场景ID
                                self.current_scene_id = f"scene_{len(self.scene_cache) + 1}"
                                # 同步生成所有选项对应的剧情
                                all_options_data = generate_all_options(self.global_state, options, skip_images=self.skip_images)
                                self.scene_cache[self.current_scene_id] = all_options_data
                                print(f"✅ 所有选项剧情生成完成，场景ID：{self.current_scene_id}")
                            else:
                                print("🔍 可选操作：无")
                                self.last_options = []
                                self.current_scene_id = "initial"

                            if 'flow_update' in scene:
                                # 安全更新世界线状态
                                if 'flow_worldline' not in self.global_state:
                                    self.global_state['flow_worldline'] = {}
                                self.global_state['flow_worldline'].update(scene['flow_update'])
                                
                                # 检查角色深层背景解锁
                                characters_update = scene['flow_update'].get('characters', {})
                                for char_name, char_info in characters_update.items():
                                    if char_info.get('deep_background_unlocked'):
                                        core = self.global_state.get('core_worldview', {})
                                        characters = core.get('characters', {})
                                        char_data = characters.get(char_name, {})
                                        deep_bg = char_data.get('deep_background', '')
                                        print(f"\n🔓 解锁角色深层背景：{char_name} → {deep_bg}")
            else:
                # 使用原始方式生成剧情
                print("✅ AI正在生成后续剧情...")
                
                # 删除当前场景的旧缓存，释放内存
                if self.current_scene_id in self.scene_cache:
                    del self.scene_cache[self.current_scene_id]
                    print(f"✅ 已删除旧场景缓存：{self.current_scene_id}")
                
                local_scenes = llm_generate_local(self.global_state, user_input, self.last_options)
                
                if local_scenes:
                    # 展示剧情
                    for i, scene in enumerate(local_scenes, 1):
                        print(f"\n--- 第 {i} 段剧情 ---")
                        print(f"📜 场景：{scene.get('scene', '无场景描述')}")
                        
                        # 安全获取选项
                        options = scene.get("options", [])
                        if options:
                            print("🔍 可选操作：")
                            # 记录当前选项为“下一轮的上一轮选项”
                            self.last_options = options
                            for idx, opt in enumerate(options, 1):
                                print(f"   {idx}. {opt}")
                            
                            # 生成下一轮选项对应的剧情（同步生成）
                            print("\n✅ 生成选项对应的剧情...")
                            # 生成新的场景ID
                            self.current_scene_id = f"scene_{len(self.scene_cache) + 1}"
                            # 同步生成所有选项对应的剧情
                            all_options_data = generate_all_options(self.global_state, options, skip_images=self.skip_images)
                            self.scene_cache[self.current_scene_id] = all_options_data
                            print(f"✅ 所有选项剧情生成完成，场景ID：{self.current_scene_id}")
                        else:
                            print("🔍 可选操作：无")
                            self.last_options = []
                            self.current_scene_id = "initial"

                        if 'flow_update' in scene:
                            # 安全更新世界线状态
                            if 'flow_worldline' not in self.global_state:
                                self.global_state['flow_worldline'] = {}
                            self.global_state['flow_worldline'].update(scene['flow_update'])
                            
                            # 检查角色深层背景解锁
                            characters_update = scene['flow_update'].get('characters', {})
                            for char_name, char_info in characters_update.items():
                                if char_info.get('deep_background_unlocked'):
                                    core = self.global_state.get('core_worldview', {})
                                    characters = core.get('characters', {})
                                    char_data = characters.get(char_name, {})
                                    deep_bg = char_data.get('deep_background', '')
                                    print(f"\n🔓 解锁角色深层背景：{char_name} → {deep_bg}")
            
            # 用户每完成一次交互选择后，修改结局大致内容
            modify_ending_content(self.global_state)

            self._check_chapter_conflict()
            if self.ending_triggered:
                self._trigger_ending()
                break

    def save_game(self, save_name: str) -> bool:
        """
        保存游戏状态到文件
        :param save_name: 存档名称
        :return: 是否保存成功
        """
        if not self.global_state:
            print("❌ 无法保存：游戏状态为空")
            return False
        
        try:
            # 构造存档数据
            save_data = {
                "global_state": self.global_state,
                "protagonist_attr": self.protagonist_attr,
                "difficulty": self.difficulty,
                "last_options": self.last_options,
                "timestamp": str(datetime.now())
            }
            
            # 生成存档文件名
            save_filename = f"{save_name}.json"
            save_path = os.path.join(self.save_dir, save_filename)
            
            # 保存到文件
            with open(save_path, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, ensure_ascii=False, indent=2)
            
            print(f"✅ 游戏已保存到：{save_path}")
            return True
        except Exception as e:
            print(f"❌ 保存游戏失败：{str(e)}")
            return False

    def _prune_autosaves(self):
        """自动存档数量控制，保留最近的N个自动存档"""
        try:
            files = []
            for file in os.listdir(self.save_dir):
                if file.startswith("auto_") and file.endswith(".json"):
                    path = os.path.join(self.save_dir, file)
                    files.append((os.path.getmtime(path), path))
            files.sort(reverse=True)  # 新的在前
            if len(files) > self.max_autosaves:
                for _, path in files[self.max_autosaves:]:
                    try:
                        os.remove(path)
                        print(f"🧹 已清理旧自动存档：{path}")
                    except Exception as clean_err:
                        print(f"⚠️ 清理自动存档失败：{clean_err}")
        except Exception as e:
            print(f"⚠️ 自动存档清理出错：{e}")
    
    def load_game(self, save_name: str) -> bool:
        """
        从文件加载游戏状态
        :param save_name: 存档名称
        :return: 是否加载成功
        """
        try:
            # 生成存档文件名
            save_filename = f"{save_name}.json"
            save_path = os.path.join(self.save_dir, save_filename)
            
            # 检查文件是否存在
            if not os.path.exists(save_path):
                print(f"❌ 存档文件不存在：{save_path}")
                return False
            
            # 读取存档数据
            with open(save_path, 'r', encoding='utf-8') as f:
                save_data = json.load(f)
            
            # 恢复游戏状态
            self.global_state = save_data.get("global_state", {})
            self.protagonist_attr = save_data.get("protagonist_attr", {})
            self.difficulty = save_data.get("difficulty", "")
            self.last_options = save_data.get("last_options", [])
            
            # 重置游戏结束标志
            self.ending_triggered = False
            
            print(f"✅ 游戏已从：{save_path} 加载")
            return True
        except Exception as e:
            print(f"❌ 加载游戏失败：{str(e)}")
            return False
    
    def list_saves(self) -> List[str]:
        """
        列出所有存档
        :return: 存档名称列表
        """
        try:
            # 获取所有json文件
            saves = []
            for file in os.listdir(self.save_dir):
                if file.endswith('.json'):
                    save_name = file[:-5]  # 去掉.json后缀
                    saves.append(save_name)
            return saves
        except Exception as e:
            print(f"❌ 列出存档失败：{str(e)}")
            return []
    
    def _manage_saves(self):
        """
        存档管理界面
        """
        while True:
            print("\n📁 存档管理")
            print("1. 列出所有存档")
            print("2. 查看存档详情")
            print("3. 保存当前游戏")
            print("4. 加载游戏")
            print("5. 返回游戏")
            
            choice = safe_input("请选择操作（输入序号1-5，默认5返回）：", default="5")
            
            if choice == "1":
                # 列出所有存档
                saves = self.list_saves()
                if not saves:
                    print("\n📭 暂无存档")
                else:
                    print("\n📋 现有存档：")
                    for idx, save_name in enumerate(saves, 1):
                        print(f"   {idx}. {save_name}")
            
            elif choice == "2":
                # 查看存档详情
                saves = self.list_saves()
                if not saves:
                    print("\n📭 暂无存档")
                    continue
                
                print("\n📋 现有存档：")
                for idx, save_name in enumerate(saves, 1):
                    print(f"   {idx}. {save_name}")
                
                detail_choice = safe_input("请选择要查看的存档序号：")
                try:
                    detail_idx = int(detail_choice) - 1
                    if 0 <= detail_idx < len(saves):
                        self._show_save_detail(saves[detail_idx])
                    else:
                        print("❌ 无效的存档序号")
                except ValueError:
                    print("❌ 请输入有效的数字序号")
            
            elif choice == "3":
                # 保存当前游戏
                save_name = safe_input("\n请输入存档名称（默认auto_manual）：", default="auto_manual")
                if not save_name:
                    print("❌ 存档名称不能为空")
                    continue
                self.save_game(save_name)
            
            elif choice == "4":
                # 加载游戏
                saves = self.list_saves()
                if not saves:
                    print("\n📭 暂无存档")
                    continue
                
                print("\n📋 现有存档：")
                for idx, save_name in enumerate(saves, 1):
                    print(f"   {idx}. {save_name}")
                
                load_choice = safe_input("请选择要加载的存档序号：")
                try:
                    load_idx = int(load_choice) - 1
                    if 0 <= load_idx < len(saves):
                        if self.load_game(saves[load_idx]):
                            # 生成前情提要
                            self._generate_recap()
                            # 加载成功后返回游戏循环
                            return True
                    else:
                        print("❌ 无效的存档序号")
                except ValueError:
                    print("❌ 请输入有效的数字序号")
            
            elif choice == "5":
                # 返回游戏
                return False
            
            else:
                print("❌ 请输入1-5之间的数字")
    
    def _generate_recap(self):
        """生成游戏前情提要"""
        if not self.global_state:
            return
        
        core = self.global_state.get('core_worldview', {})
        flow = self.global_state.get('flow_worldline', {})
        
        # 获取当前章节信息
        current_chapter_id = flow.get('current_chapter', 'chapter1')
        chapters = core.get('chapters', {})
        current_chapter = chapters.get(current_chapter_id, {})
        
        # 获取章节编号（用于显示）
        chapter_num = 1
        if current_chapter_id.startswith('chapter'):
            try:
                chapter_num = int(current_chapter_id[7:])
            except (ValueError, IndexError):
                chapter_num = 1
        
        # 生成前情提要
        print("\n📋 前情提要：")
        print(f"1. 当前章节：第{chapter_num}章")
        print(f"2. 核心矛盾：{current_chapter.get('main_conflict', '未知')}")
        print(f"3. 主线进度：{flow.get('quest_progress', '未知')}")
        print(f"4. 矛盾状态：{'已解决' if flow.get('chapter_conflict_solved', False) else '未解决'}")
        print(f"5. 当前位置：{flow.get('environment', {}).get('location', '未知')}")
        
        # 显示当前可选操作（如果有）
        if self.last_options:
            print("\n🔍 你当前可以进行的操作：")
            for idx, opt in enumerate(self.last_options, 1):
                print(f"   {idx}. {opt}")
        
        safe_input("\n请按回车键继续游戏...", default="")

    def _quick_recap(self):
        """
        轻量级提示：每轮输入前快速提醒核心信息，减少玩家迷茫
        """
        if not self.global_state:
            return
        core = self.global_state.get('core_worldview', {})
        flow = self.global_state.get('flow_worldline', {})
        current_chapter_id = flow.get('current_chapter', 'chapter1')
        chapter_num = 1
        if current_chapter_id.startswith('chapter'):
            try:
                chapter_num = int(current_chapter_id[7:])
            except (ValueError, IndexError):
                chapter_num = 1
        location = flow.get('environment', {}).get('location', '未知')
        quest_progress = flow.get('quest_progress', '未知')
        print(f"\n📋 当前：第{chapter_num}章 | 位置：{location} | 进度：{quest_progress}")
    
    def _show_save_detail(self, save_name: str):
        """
        显示存档详情，包括主角和已出场人物的状态以及游戏之前发生过的剧情
        :param save_name: 存档名称
        """
        try:
            # 生成存档文件名
            save_filename = f"{save_name}.json"
            save_path = os.path.join(self.save_dir, save_filename)
            
            # 检查文件是否存在
            if not os.path.exists(save_path):
                print(f"❌ 存档文件不存在：{save_path}")
                return
            
            # 读取存档数据
            with open(save_path, 'r', encoding='utf-8') as f:
                save_data = json.load(f)
            
            # 提取存档数据
            global_state = save_data.get("global_state", {})
            protagonist_attr = save_data.get("protagonist_attr", {})
            difficulty = save_data.get("difficulty", "")
            last_options = save_data.get("last_options", [])
            timestamp = save_data.get("timestamp", "")
            
            if not global_state:
                print("❌ 存档数据不完整")
                return
            
            core = global_state.get('core_worldview', {})
            flow = global_state.get('flow_worldline', {})
            
            # 获取当前章节信息
            current_chapter_id = flow.get('current_chapter', 'chapter1')
            chapters = core.get('chapters', {})
            current_chapter = chapters.get(current_chapter_id, {})
            
            # 获取章节编号（用于显示）
            chapter_num = 1
            if current_chapter_id.startswith('chapter'):
                try:
                    chapter_num = int(current_chapter_id[7:])
                except (ValueError, IndexError):
                    chapter_num = 1
            
            # 显示存档基本信息
            print(f"\n📋 存档详情：{save_name}")
            print(f"🔖 存档时间：{timestamp}")
            print(f"🎮 游戏难度：{difficulty}")
            
            # 显示主角属性
            print(f"\n🎭 主角属性：")
            for attr_name, attr_value in protagonist_attr.items():
                print(f"   {attr_name}：{attr_value}")
            
            # 显示角色状态
            print(f"\n👥 角色状态：")
            # 获取核心角色列表
            core_characters = core.get('characters', {})
            # 获取当前世界线中的角色状态
            flow_characters = flow.get('characters', {})
            
            # 合并核心角色和当前世界线角色
            all_characters = {**core_characters}
            for char_name, char_info in flow_characters.items():
                if char_name in all_characters:
                    all_characters[char_name].update(char_info)
                else:
                    all_characters[char_name] = char_info
            
            # 显示每个角色的状态
            for char_name, char_info in all_characters.items():
                print(f"\n   🧑 {char_name}：")
                # 显示核心信息
                if 'core_personality' in char_info:
                    print(f"      核心性格：{char_info['core_personality']}")
                if 'shallow_background' in char_info:
                    print(f"      浅层背景：{char_info['shallow_background'][:30]}...")
                # 显示当前状态
                if 'thought' in char_info:
                    print(f"      当前想法：{char_info['thought']}")
                if 'physiology' in char_info:
                    print(f"      身体状态：{char_info['physiology']}")
                if 'deep_background_unlocked' in char_info:
                    status = "已解锁" if char_info['deep_background_unlocked'] else "未解锁"
                    print(f"      深层背景：{status}")
            
            # 显示游戏剧情进展
            print(f"\n📜 游戏剧情进展：")
            print(f"   当前章节：第{chapter_num}章")
            print(f"   核心矛盾：{current_chapter.get('main_conflict', '未知')}")
            print(f"   主线进度：{flow.get('quest_progress', '未知')}")
            print(f"   矛盾状态：{'已解决' if flow.get('chapter_conflict_solved', False) else '未解决'}")
            
            # 显示环境状态
            environment = flow.get('environment', {})
            print(f"\n🌍 环境状态：")
            print(f"   位置：{environment.get('location', '未知')}")
            print(f"   天气：{environment.get('weather', '未知')}")
            if 'force_relationship' in environment:
                print(f"   势力关系：{environment['force_relationship'][:30]}...")
            
            # 显示当前可选操作（如果有）
            if last_options:
                print(f"\n🔍 当前可选操作：")
                for idx, opt in enumerate(last_options, 1):
                    print(f"   {idx}. {opt}")
            
            safe_input("\n请按回车键返回存档管理...", default="")
            
        except Exception as e:
            print(f"❌ 查看存档详情失败：{str(e)}")
            safe_input("\n请按回车键返回存档管理...", default="")
    
    def _async_pregenerate(self, scene_id: str, options: List[str]):
        """异步预生成指定场景下所有选项的剧情"""
        print(f"🔄 启动异步预生成线程，场景ID：{scene_id}")
        self.generation_cancelled = False
        
        # 生成所有选项的剧情
        all_options_data = generate_all_options(self.global_state, options, skip_images=self.skip_images)
        
        # 如果生成未被取消，将结果缓存
        if not self.generation_cancelled:
            print(f"✅ 异步预生成完成，场景ID：{scene_id}")
            self.scene_cache[scene_id] = all_options_data
        else:
            print(f"⏹️ 异步预生成已取消，场景ID：{scene_id}")
    
    def start_pregeneration(self, options: List[str]):
        """启动预生成线程"""
        # 取消当前正在进行的生成任务
        self.generation_cancelled = True
        
        # 生成新的场景ID
        next_scene_id = f"scene_{len(self.scene_cache) + 1}"
        
        # 启动新的预生成线程
        self.generating_task = threading.Thread(
            target=self._async_pregenerate,
            args=(next_scene_id, options),
            daemon=True
        )
        self.generating_task.start()
        
        return next_scene_id
    
    def cancel_pregeneration(self):
        """取消当前正在进行的预生成任务"""
        self.generation_cancelled = True
        if self.generating_task and self.generating_task.is_alive():
            self.generating_task.join(timeout=1.0)  # 等待最多1秒
        print("⏹️ 已取消正在进行的预生成任务")
    
    def _trigger_ending(self):
        print("\n🏁 === 游戏结束 ===")
        if self.ending_triggered:
            print("你选择结束游戏，感谢游玩！")
        else:
            flow = self.global_state.get('flow_worldline', {})
            quest_progress = flow.get('quest_progress', '未知')
            print(f"你已完成所有章节，主线任务进度：{quest_progress}")
        self.is_running = False

