# -*- coding: utf-8 -*-
import os
import sys
import json
import requests
import threading
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional
from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_file, send_from_directory

# 设置环境变量以使用 UTF-8 编码（解决 Windows GBK 编码问题）
if sys.platform == 'win32':
    os.environ['PYTHONIOENCODING'] = 'utf-8'

from main2 import (
    llm_generate_global, 
    _generate_single_option, 
    _generate_single_option_text_only,
    generate_all_options, 
    modify_ending_content, 
    generate_ending_prediction,
    generate_scene_image,
    # ==================== 视频生成功能已禁用（性能优化） ====================
    # generate_scene_video,
    # get_video_task_status
    get_video_task_status,  # 保留占位函数，避免导入错误
    # ==================== 主角形象生成功能 ====================
    generate_game_id,
    generate_main_character_image
)

# 初始化Flask应用
app = Flask(__name__)

# 加载环境变量
load_dotenv()

# 存档目录配置
SAVE_DIR = "saves"

# 确保存档目录存在
if not os.path.exists(SAVE_DIR):
    os.makedirs(SAVE_DIR)

# 图片和视频缓存目录配置
IMAGE_CACHE_DIR = "image_cache"
VIDEO_CACHE_DIR = "video_cache"

# 确保缓存目录存在
if not os.path.exists(IMAGE_CACHE_DIR):
    os.makedirs(IMAGE_CACHE_DIR)
if not os.path.exists(VIDEO_CACHE_DIR):
    os.makedirs(VIDEO_CACHE_DIR)

# 全局缓存：存储预生成的两层内容
# 结构：{scene_id: {
#   'layer1': {option_index: option_data},
#   'layer2': {option_index: {option_index: option_data}},
#   'generation_status': {option_index: 'pending'|'generating'|'completed'},
#   'generation_events': {option_index: threading.Event()},
#   'should_cancel': False,
#   'current_generating_index': None,
#   'layer2_generating': False,  # 第二层是否正在生成
#   'layer2_cancel': False,  # 第二层生成取消标志
#   'layer2_selected_option': None,  # 用户选择的选项索引（用于第二层生成控制）
#   'layer2_thread': None  # 第二层生成线程对象
# }}
pregeneration_cache = {}
# 线程锁，保证缓存操作的线程安全（带追踪：定位谁持有锁）
_cache_lock_holder = None  # 🔧 调试：追踪当前持有锁的线程（threading.Thread）
_cache_lock_acquire_time = None  # 🔧 调试：追踪锁的获取时间（time.time）


class TrackedLock:
    """
    为 threading.Lock 增加“谁持有锁/持有多久/当前堆栈”的追踪能力。
    目的：定位 cache_lock 被长时间持有导致的“图片生成后无法写入缓存”问题。
    """

    def __init__(self, name: str = "cache_lock"):
        self._lock = threading.Lock()
        self.name = name
        self.holder_ident = None
        self.holder_name = None
        self.holder_since = None
        self._last_stack_dump_ts = 0.0

    def acquire(self, blocking: bool = True, timeout: float = -1):
        import time
        # 兼容 threading.Lock.acquire(blocking=True, timeout=-1)
        if timeout is None or timeout == -1:
            got = self._lock.acquire(blocking)
        else:
            got = self._lock.acquire(blocking, timeout)

        if got:
            global _cache_lock_holder, _cache_lock_acquire_time
            self.holder_ident = threading.get_ident()
            self.holder_name = threading.current_thread().name
            self.holder_since = time.time()
            _cache_lock_holder = threading.current_thread()
            _cache_lock_acquire_time = self.holder_since

        return got

    def release(self):
        global _cache_lock_holder, _cache_lock_acquire_time
        self.holder_ident = None
        self.holder_name = None
        self.holder_since = None
        _cache_lock_holder = None
        _cache_lock_acquire_time = None
        return self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
        return False

    def dump_holder_stack(self, limit: int = 40, min_interval_seconds: float = 2.0):
        """
        返回当前持锁线程的“实时堆栈”（不是获取锁时的堆栈）。
        为避免刷屏，默认 2 秒最多输出一次（由调用方 print）。
        """
        import sys
        import time
        import traceback

        if not self.holder_ident:
            return None

        now = time.time()
        if now - self._last_stack_dump_ts < min_interval_seconds:
            return None

        frames = sys._current_frames()
        frame = frames.get(self.holder_ident)
        if frame is None:
            self._last_stack_dump_ts = now
            return f"[{self.name}] 无法获取持锁线程堆栈（holder_ident={self.holder_ident})"

        stack = "".join(traceback.format_stack(frame, limit=limit))
        self._last_stack_dump_ts = now
        return stack


cache_lock = TrackedLock("cache_lock")
MAX_CACHE_SIZE = 3  # 最大缓存场景数量，超过此数量将清理最旧的缓存（降低内存占用）

# 辅助函数：清理错误消息中的特殊字符（避免编码问题）
def clean_error_message(error_msg):
    """清理错误消息，移除可能导致编码问题的字符"""
    try:
        # 先尝试编码为 UTF-8
        msg = str(error_msg)
        # 移除 emoji 和特殊 Unicode 字符（保留基本 ASCII 和中文字符）
        import re
        # 保留 ASCII、中文字符、常见标点符号
        msg = re.sub(r'[^\x00-\x7F\u4e00-\u9fff\s\.,;:!?()\[\]{}\-+=]', '', msg)
        return msg
    except:
        # 如果清理失败，返回安全的默认消息
        return "发生错误，请稍后重试"

# 生成场景ID的辅助函数
def generate_scene_id(global_state_hash, current_options_hash):
    """根据全局状态和当前选项生成唯一的场景ID"""
    return f"{hash(str(global_state_hash))}_{hash(str(current_options_hash))}"

# 缓存清理函数：清理旧的、无用的缓存
def cleanup_old_cache(current_scene_id=None):
    """清理旧的缓存，保留最近使用的场景"""
    with cache_lock:
        cache_size = len(pregeneration_cache)
        if cache_size <= MAX_CACHE_SIZE:
            return
        
        # 如果提供了当前场景ID，确保它不被清理
        scenes_to_keep = set()
        if current_scene_id:
            scenes_to_keep.add(current_scene_id)
        if 'initial' in pregeneration_cache:
            scenes_to_keep.add('initial')
        
        # 计算需要清理的数量
        to_remove = cache_size - MAX_CACHE_SIZE
        
        # 找出最旧的缓存（除了要保留的）
        scenes_to_remove = []
        for scene_id in pregeneration_cache:
            if scene_id not in scenes_to_keep:
                scenes_to_remove.append(scene_id)
        
        # 如果场景太多，清理最旧的（这里简化处理，清理除了当前和initial之外的所有）
        if len(scenes_to_remove) > to_remove:
            # 只清理超出限制的部分
            scenes_to_remove = scenes_to_remove[:to_remove]
        
        # 清理选中的场景
        for scene_id in scenes_to_remove:
            cache_entry = pregeneration_cache.get(scene_id)
            if cache_entry:
                # 停止正在进行的生成
                if cache_entry.get('layer2_generating', False):
                    cache_entry['layer2_cancel'] = True
                    layer2_thread = cache_entry.get('layer2_thread')
                    if layer2_thread and layer2_thread.is_alive():
                        layer2_thread.join(timeout=0.5)
            
            del pregeneration_cache[scene_id]
            print(f"🗑️ 已清理旧缓存场景 {scene_id}（内存优化）")
        
        print(f"📊 当前缓存大小：{len(pregeneration_cache)}/{MAX_CACHE_SIZE}")

# 清理已使用选项的缓存数据
def cleanup_used_options(scene_id, used_option_index):
    """清理已使用的选项数据，释放内存"""
    with cache_lock:
        if scene_id not in pregeneration_cache:
            return
        
        cache_entry = pregeneration_cache[scene_id]
        
        # 清理第一层已使用的选项（保留当前使用的，但清理其他未使用的）
        if 'layer1' in cache_entry:
            layer1 = cache_entry['layer1']
            # 只保留当前使用的选项，清理其他未使用的选项
            if used_option_index in layer1:
                # 保留当前使用的选项数据，但可以清理其第二层数据
                if 'layer2' in cache_entry and used_option_index in cache_entry['layer2']:
                    # 清理第二层中未使用的选项
                    layer2_data = cache_entry['layer2'][used_option_index]
                    # 这里可以进一步优化，但为了安全，暂时保留
                    pass

# 允许前端跨域访问
@app.after_request
def after_request(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'POST, GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response

# 核心接口：生成游戏世界观
@app.route('/generate-worldview', methods=['POST'])
def generate_worldview():
    try:
        # 获取前端传的参数
        data = request.json
        game_theme = data.get('gameTheme', '').strip()
        protagonist_attr = data.get('protagonistAttr', {})
        difficulty = data.get('difficulty', '中等')
        tone_key = data.get('toneKey', 'normal_ending')
        image_style = data.get('imageStyle', None)  # 图片风格选择
        
        # 基础校验
        if not game_theme:
            return jsonify({"status": "error", "message": "游戏主题不能为空！"})
        
        # 生成游戏ID
        game_id = generate_game_id()
        print(f"🎮 生成游戏ID: {game_id}")
        
        # 调用后端生成世界观的函数
        try:
            global_state = llm_generate_global(game_theme, protagonist_attr, difficulty, tone_key)
            
            # 保存游戏ID到global_state
            global_state['game_id'] = game_id

            # 🔑 保存用户输入的主题（用于现实题材/IP检索命中率）
            # 注意：core_worldview.game_style 往往是较长的“风格描述”，不一定等同于用户输入主题名。
            global_state['user_theme'] = game_theme
            
            # 保存图片风格到global_state
            if image_style:
                global_state['image_style'] = image_style
                print(f"✅ 图片风格已保存到global_state: {image_style}")
        except ValueError as e:
            # 如果是API配置错误，返回明确的错误信息
            error_msg = str(e)
            if "缺少必要的API配置" in error_msg or "API" in error_msg:
                return jsonify({
                    "status": "error",
                    "message": f"AI生成功能未配置：{error_msg}\n\n请检查.env文件，确保配置了以下环境变量：\n- Camera_Analyst_API_KEY\n- Camera_Analyst_BASE_URL\n- Camera_Analyst_MODEL"
                })
            raise  # 其他ValueError继续抛出

        # ✅ 世界观生成完成后：立刻启动主角形象生成（后台线程，不阻塞响应）
        # 目的：用户正在查看世界观时并行生图；并将完整世界观文本/结构传入提示词LLM。
        try:
            import copy

            def generate_main_character_after_worldview_async(gs_snapshot, game_id_arg):
                """世界观生成完成后触发：主角形象生成（后台线程）。game_id_arg 必须传入，避免闭包读到后续请求覆盖的值。"""
                try:
                    print(f"🎨 开始生成主角形象（游戏ID: {game_id_arg}，世界观已就绪，后台并行）...")
                    result = generate_main_character_image(
                        protagonist_attr=protagonist_attr,
                        global_state=gs_snapshot,
                        image_style=image_style,
                        game_id=game_id_arg
                    )
                    if result:
                        print(f"✅ 主角形象生成完成（游戏ID: {game_id_arg}）")
                    else:
                        print(f"⚠️ 主角形象生成失败（游戏ID: {game_id_arg}），但游戏可以继续")
                except Exception as e:
                    print(f"❌ 主角形象生成出错（游戏ID: {game_id_arg}）：{str(e)}")
                    import traceback
                    traceback.print_exc()

            gs_snapshot = copy.deepcopy(global_state) if isinstance(global_state, dict) else global_state
            threading.Thread(
                target=generate_main_character_after_worldview_async,
                args=(gs_snapshot, game_id),
                daemon=True
            ).start()
            print("✅ 主角形象生成任务已启动（世界观生成完成后触发，后台并行）")
        except Exception as e:
            print(f"⚠️ 启动主角形象生成任务失败：{str(e)}")
        
        # 世界观生成完成后，更新主角形象信息到global_state（如果已生成）
        try:
            # 检查主角形象是否已生成
            main_character_path = f"initial/main_character/{game_id}/main_character.png"
            if os.path.exists(main_character_path):
                global_state['main_character'] = {
                    'game_id': game_id,
                    'image_url': f"/initial/main_character/{game_id}/main_character.png",
                    'image_path': main_character_path,
                    'width': 1024,
                    'height': 1536
                }
                print(f"✅ 主角形象信息已更新到global_state")
        except Exception as e:
            print(f"⚠️ 更新主角形象信息失败：{str(e)}")
        
        # 世界观生成成功后，立即启动第一次选项的生成（后台线程，不使用预生成机制）
        def generate_initial_options():
            """生成第一次选项（根据世界观动态生成）"""
            try:
                print(f"🔄 开始生成第一次选项（根据世界观动态生成）...")
                
                # 根据世界观生成初始场景和选项
                # 使用"开始游戏"作为初始选项，生成第一个场景和后续选项
                initial_option = "开始游戏"
                result = _generate_single_option(0, initial_option, global_state)
                
                if isinstance(result, dict):
                    initial_option_data = result.get('data', result)
                else:
                    initial_option_data = result
                
                # 获取生成的初始选项列表
                initial_options = initial_option_data.get('next_options', [])
                
                if not initial_options:
                    # 如果生成失败，使用默认选项
                    initial_options = ["继续深入探索", "查看周围环境"]
                
                # 限制选项数量为2个
                if len(initial_options) > 2:
                    initial_options = initial_options[:2]

                # ✅ 性能优化：第一次只生成“当前轮（初始场景）的文本+画面+下一步选项”，不再在这里预生成每个选项的剧情/图片。
                # 后续预生成仍由前端触发 /pregenerate-next-layers（用户阅读时间后台生成），逻辑保持一致。

                # 存储到特殊缓存位置（仅初始场景，不预生成选项剧情）
                with cache_lock:
                    if 'initial' not in pregeneration_cache:
                        pregeneration_cache['initial'] = {
                            'generation_events': {}
                        }
                    
                    initial_cache = pregeneration_cache['initial']
                    # 不再填充 layer1（每个选项的剧情），交给后续预生成或按需生成
                    initial_cache['layer1'] = {}
                    # 确保initial_scene不为空，如果为空则使用默认场景
                    initial_scene = initial_option_data.get('scene', '')
                    if not initial_scene or initial_scene.strip() == '':
                        print(f"⚠️ 初始场景为空，使用默认场景")
                        initial_scene = "你开始了你的冒险之旅."
                    # 修复：提取并保存初始场景的图片数据（含 scene_text_hash，避免 /generate-option 误判文本变化而重复生成）
                    initial_scene_image = initial_option_data.get('scene_image', None)
                    if initial_scene_image:
                        if not initial_scene_image.get('scene_text_hash') and initial_scene and initial_scene.strip():
                            initial_scene_image = dict(initial_scene_image)
                            initial_scene_image['scene_text_hash'] = hashlib.md5(initial_scene.encode('utf-8')).hexdigest()
                        print(f"✅ 初始场景图片数据已提取: {initial_scene_image.get('url', 'N/A')[:80]}...")
                    else:
                        print(f"⚠️ 初始场景没有图片数据")
                    initial_cache['initial_scene'] = initial_scene
                    initial_cache['initial_scene_image'] = initial_scene_image  # 保存图片数据
                    initial_cache['initial_options'] = initial_options
                    # 选项剧情未预生成，状态保持 pending（如后续需要可由预生成写入 scene_id 对应缓存）
                    initial_cache['generation_status'] = {i: 'pending' for i in range(len(initial_options))}
                    initial_cache['completed'] = True
                    
                    # 触发等待事件（如果有线程在等待）
                    events = initial_cache.get('generation_events', {})
                    if 'main' in events:
                        events['main'].set()

                print(f"✅ 第一次选项生成完成（仅初始场景+选项，未预生成选项剧情/图片），选项数：{len(initial_options)}")
                
            except Exception as e:
                print(f"❌ 生成第一次选项失败：{str(e)}")
                import traceback
                traceback.print_exc()
                # 即使失败，也设置一个标记，避免前端无限等待
                with cache_lock:
                    if 'initial' not in pregeneration_cache:
                        pregeneration_cache['initial'] = {
                            'generation_events': {}
                        }
                    initial_cache = pregeneration_cache['initial']
                    initial_cache['completed'] = False
                    initial_cache['error'] = str(e)
                    
                    # 触发等待事件（避免前端无限等待）
                    events = initial_cache.get('generation_events', {})
                    if 'main' in events:
                        events['main'].set()
        
        # 启动后台线程生成第一次选项（不阻塞响应）
        thread = threading.Thread(target=generate_initial_options, daemon=True)
        thread.start()
        
        # 验证返回的数据结构
        if not global_state:
            return jsonify({
                "status": "error",
                "message": "世界观生成失败：返回的数据为空"
            })
        
        # 验证核心字段
        if not global_state.get('core_worldview'):
            return jsonify({
                "status": "error",
                "message": "世界观生成失败：缺少核心世界观数据"
            })
        
        print(f"✅ 世界观生成成功，返回数据包含：")
        print(f"   - core_worldview: {bool(global_state.get('core_worldview'))}")
        print(f"   - chapters: {bool(global_state.get('core_worldview', {}).get('chapters'))}")
        print(f"   - chapter1: {bool(global_state.get('core_worldview', {}).get('chapters', {}).get('chapter1'))}")
        
        # 返回结果
        return jsonify({
            "status": "success",
            "message": "世界观生成成功！",
            "globalState": global_state
        })
    except Exception as e:
        error_msg = clean_error_message(str(e))
        return jsonify({"status": "error", "message": f"世界观生成失败：{error_msg}"})

# 核心接口：生成单个选项对应的剧情（支持智能等待，不降级为实时生成）
@app.route('/generate-option', methods=['POST'])
def generate_option():
    try:
        # 获取前端传的参数
        data = request.json
        option = data.get('option', '').strip()
        global_state = data.get('globalState', {})
        option_index = data.get('optionIndex', 0)
        scene_id = data.get('sceneId', None)  # 前端传入的场景ID，用于缓存查找
        current_options = data.get('currentOptions', [])  # 当前选项列表，用于触发优先生成
        
        # 🔍 调试日志：显示前端传入的参数
        print(f"🔍 [generate-option] 收到请求：")
        print(f"   - 选项内容：{option[:50]}...")
        print(f"   - 选项索引：{option_index}")
        print(f"   - 前端传入的 sceneId：{scene_id}")
        print(f"   - 当前缓存中的所有 scene_id：{list(pregeneration_cache.keys())}")
        
        # 新增：图片依赖生成（视觉连续性上下文）
        # - 同一场景统一风格/物件
        # - 下一剧情图片参考上一剧情图片生成
        previous_scene_image = data.get('previousSceneImage', None)  # {url,prompt,...}（可选）
        previous_scene_text = data.get('previousSceneText', '')  # 可选：上一剧情文本（用于提示词连续性）
        if isinstance(global_state, dict) and (previous_scene_image or previous_scene_text):
            global_state['_visual_context'] = {
                "sceneId": scene_id,
                "previousSceneImage": previous_scene_image,
                "previousSceneText": previous_scene_text
            }
            # 也写入缓存（便于后续在该 scene_id 下触发的优先生成/补生成复用）
            if scene_id:
                with cache_lock:
                    if scene_id in pregeneration_cache:
                        pregeneration_cache[scene_id]['visual_context'] = global_state['_visual_context']
        
        # 基础校验
        if not option:
            return jsonify({"status": "error", "message": "选项内容不能为空！"})
        if not global_state:
            return jsonify({"status": "error", "message": "全局状态不能为空！"})
        
        option_data = None
        need_wait = False
        wait_event = None  # 初始化wait_event
        layer2_thread_to_wait = None  # 用于在释放锁后等待第二层线程
        
        # 处理第一次生成的情况（sceneId为null或'initial'）
        if not scene_id or scene_id == 'initial':
            # 第一次生成：从initial缓存读取
            with cache_lock:
                # 如果initial缓存不存在，创建并等待
                if 'initial' not in pregeneration_cache:
                    pregeneration_cache['initial'] = {
                        'generation_events': {},
                        'completed': False
                    }
                    need_wait = True
                else:
                    initial_cache = pregeneration_cache['initial']
                    
                    # 检查是否生成完成
                    if initial_cache.get('completed', False):
                        # 如果用户选择的是"开始游戏"（option_index=0），返回初始场景
                        if option_index == 0 and option == "开始游戏":
                            # 返回初始场景和选项
                            initial_scene = initial_cache.get('initial_scene', '')
                            initial_scene_image = initial_cache.get('initial_scene_image', None)  # 修复：读取图片数据
                            initial_options = initial_cache.get('initial_options', [])
                            
                            # 确保initial_scene不为空
                            if not initial_scene or initial_scene.strip() == '':
                                print(f"⚠️ 从缓存读取的初始场景为空，使用默认场景")
                                initial_scene = "你开始了你的冒险之旅."
                            
                            option_data = {
                                "scene": initial_scene,
                                "scene_image": initial_scene_image,  # 修复：包含图片数据
                                "next_options": initial_options,
                                "flow_update": {},
                                "deep_background_links": {}
                            }
                            if initial_scene_image:
                                print(f"✅ 从initial缓存中读取初始场景和选项，场景长度: {len(initial_scene)}，包含图片数据")
                            else:
                                print(f"✅ 从initial缓存中读取初始场景和选项，场景长度: {len(initial_scene)}，无图片数据")
                        else:
                            # 从layer1中读取对应选项的数据
                            layer1_data = initial_cache.get('layer1', {})
                            if option_index in layer1_data:
                                option_data = layer1_data[option_index]
                                print(f"✅ 从initial缓存中读取选项 {option_index} 的剧情")
                            else:
                                # 如果找不到，等待生成完成
                                need_wait = True
                    else:
                        # 还未生成完成，等待
                        need_wait = True
                
                # 如果需要等待，创建等待事件
                if need_wait:
                    initial_cache = pregeneration_cache['initial']
                    events = initial_cache.setdefault('generation_events', {})
                    if 'main' not in events:
                        events['main'] = threading.Event()
                    wait_event = events['main']
        
        if scene_id and scene_id != 'initial':
            with cache_lock:
                # 🔍 调试日志：检查 scene_id 是否在缓存中
                print(f"🔍 [generate-option] 检查 scene_id 是否在缓存中...")
                print(f"   - 查找的 scene_id：{scene_id}")
                print(f"   - 缓存中的 scene_id 列表：{list(pregeneration_cache.keys())}")
                print(f"   - scene_id 是否在缓存中：{scene_id in pregeneration_cache}")
                
                if scene_id in pregeneration_cache:
                    cache_entry = pregeneration_cache[scene_id]
                    print(f"✅ [generate-option] scene_id 匹配成功，找到缓存条目")
                    print(f"   - 缓存条目中的 layer1 选项索引：{list(cache_entry.get('layer1', {}).keys())}")
                    print(f"   - 缓存条目中的生成状态：{cache_entry.get('generation_status', {})}")
                    
                    # 情况1：缓存中已有该选项的数据
                    if 'layer1' in cache_entry and option_index in cache_entry['layer1']:
                        option_data_temp = cache_entry['layer1'][option_index]
                        generation_status = cache_entry.get('generation_status', {})
                        status = generation_status.get(option_index, 'pending')
                        
                        # 🔧 修复：确保图片和文本一起返回
                        # 如果状态是 'text_completed'，说明图片还在生成，需要等待
                        if status == 'text_completed':
                            # 检查是否有图片
                            scene_image = option_data_temp.get('scene_image')
                            if not scene_image or not scene_image.get('url'):
                                # 图片还在生成中，需要等待
                                print(f"⏳ 选项 {option_index} 文本已就绪，但图片还在生成中，等待图片生成完成...")
                                need_wait = True
                                events = cache_entry.setdefault('generation_events', {})
                                if option_index not in events:
                                    events[option_index] = threading.Event()
                                wait_event = events[option_index]
                            else:
                                # 图片已生成，可以直接返回
                                option_data = option_data_temp
                                print(f"✅ 从缓存中读取场景 {scene_id} 的选项 {option_index} 的剧情（包含图片）")
                        elif status == 'completed':
                            # 完全完成，可以直接返回
                            option_data = option_data_temp
                            print(f"✅ 从缓存中读取场景 {scene_id} 的选项 {option_index} 的剧情（包含图片）")
                        else:
                            # 其他状态，也尝试返回（可能有数据）
                            option_data = option_data_temp
                            print(f"✅ 从缓存中读取场景 {scene_id} 的选项 {option_index} 的剧情")
                        
                        # 如果数据已就绪（有图片），处理第二层生成逻辑
                        if option_data and not need_wait:
                            # 用户选择了选项，需要控制第二层生成
                            # 检查第二层是否已经开始生成
                            layer2_generating = cache_entry.get('layer2_generating', False)
                            
                            if layer2_generating:
                                # 情况1a：第二层已经开始生成
                                # 检查当前正在生成的是哪个选项的第二层
                                current_layer2_option = cache_entry.get('current_layer2_option', None)
                                
                                if current_layer2_option == option_index:
                                    # 正在生成的是用户选择的选项的第二层，继续生成
                                    print(f"✅ 正在生成选项 {option_index} 的第二层，继续生成")
                                else:
                                    # 正在生成的不是用户选择的选项的第二层，停止生成
                                    print(f"⏹️ 停止生成选项 {current_layer2_option} 的第二层（用户选择了选项 {option_index}）")
                                    cache_entry['layer2_cancel'] = True
                                    # 保存线程引用，在释放锁后等待（避免死锁）
                                    layer2_thread_to_wait = cache_entry.get('layer2_thread')
                            else:
                                # 情况1b：第二层还未开始生成
                                # 设置标志，只生成用户选择的选项的第二层
                                print(f"📝 第二层还未开始生成，将只为选项 {option_index} 生成第二层")
                                cache_entry['layer2_selected_option'] = option_index
                                cache_entry['layer2_cancel'] = False
                    
                    # 情况2：缓存中没有该选项的数据，检查生成状态
                    elif 'generation_status' in cache_entry:
                        generation_status = cache_entry.get('generation_status', {})
                        status = generation_status.get(option_index, 'pending')
                        
                        if status == 'generating':
                            # 情况2a：正在生成中，等待生成完成
                            print(f"⏳ 选项 {option_index} 正在生成中，等待完成...")
                            print(f"   - 当前缓存中的 layer1 选项索引：{list(cache_entry.get('layer1', {}).keys())}")
                            print(f"   - 当前生成状态：{generation_status}")
                            need_wait = True
                            # 获取对应的事件对象
                            events = cache_entry.setdefault('generation_events', {})
                            if option_index not in events:
                                events[option_index] = threading.Event()
                                print(f"   - 创建了选项 {option_index} 的等待事件")
                            else:
                                print(f"   - 使用已存在的选项 {option_index} 的等待事件")
                            wait_event = events[option_index]
                        
                        elif status == 'pending':
                            # 情况2b：还未开始生成，优先生成该选项
                            print(f"🚀 选项 {option_index} 还未生成，优先生成...")
                            # 标记需要取消其他未开始的生成
                            cache_entry['should_cancel'] = True
                            # 如果用户选择的选项还未生成，标记为高优先级
                            generation_status[option_index] = 'generating'
                            # 创建事件对象
                            events = cache_entry.setdefault('generation_events', {})
                            if option_index not in events:
                                events[option_index] = threading.Event()
                            wait_event = events[option_index]
                            
                            # 启动单个选项的生成任务（优先生成）
                            def generate_selected_option():
                                try:
                                    result = _generate_single_option(option_index, option, global_state)
                                    if isinstance(result, dict):
                                        opt_data = result.get('data', result)
                                    else:
                                        opt_data = result
                                    
                                    with cache_lock:
                                        if scene_id in pregeneration_cache:
                                            cache_entry = pregeneration_cache[scene_id]
                                            if 'layer1' not in cache_entry:
                                                cache_entry['layer1'] = {}
                                            cache_entry['layer1'][option_index] = opt_data
                                            generation_status = cache_entry.setdefault('generation_status', {})
                                            generation_status[option_index] = 'completed'
                                            
                                            # 触发等待事件
                                            events = cache_entry.get('generation_events', {})
                                            if option_index in events:
                                                events[option_index].set()
                                            print(f"✅ 选项 {option_index} 优先生成完成")
                                except Exception as e:
                                    print(f"❌ 优先生成选项 {option_index} 失败：{str(e)}")
                                    with cache_lock:
                                        if scene_id in pregeneration_cache:
                                            events = pregeneration_cache[scene_id].get('generation_events', {})
                                            if option_index in events:
                                                events[option_index].set()
                            
                            thread = threading.Thread(target=generate_selected_option, daemon=True)
                            thread.start()
                            need_wait = True
                    else:
                        # 情况3：scene_id不在缓存中，可能是第一次选择（前端传入了新生成的sceneId）
                        # 尝试从initial缓存中查找（第一次的选项数据在initial缓存中）
                        print(f"⚠️ [generate-option] 场景 {scene_id} 不在缓存中！")
                        print(f"   - 前端传入的 scene_id：{scene_id}")
                        print(f"   - 缓存中存在的 scene_id：{list(pregeneration_cache.keys())}")
                        print(f"   - 尝试从initial缓存查找...")
                        if 'initial' in pregeneration_cache:
                            initial_cache = pregeneration_cache['initial']
                            if initial_cache.get('completed', False):
                                layer1_data = initial_cache.get('layer1', {})
                                if option_index in layer1_data:
                                    option_data = layer1_data[option_index]
                                    print(f"✅ 从initial缓存中读取选项 {option_index} 的剧情（第一次选择）")
                                else:
                                    print(f"⚠️ initial缓存中也没有选项 {option_index} 的数据")
                            else:
                                print(f"⚠️ initial缓存还未完成生成")

                    # 🔧 容错增强：如果 scene_id 未命中且 initial 也没有该选项数据，则按需启动该选项生成并等待。
                    # 目的：避免因“首次不预生成 layer1”或“前端预生成请求尚未到达”导致返回默认/空数据。
                    if not option_data:
                        print(f"🚀 [generate-option] 缓存未命中，按需生成选项 {option_index}（scene_id={scene_id}）...")
                        # 初始化该 scene_id 的缓存条目（与预生成结构一致）
                        pregeneration_cache[scene_id] = {
                            'layer1': {},
                            'layer2': {},
                            'generation_status': {},
                            'generation_events': {},
                            'should_cancel': False,
                            'current_generating_index': None,
                            'layer2_generating': False,
                            'layer2_cancel': False,
                            'layer2_selected_option': None,
                            'layer2_thread': None,
                            'current_layer2_option': None
                        }
                        cache_entry = pregeneration_cache[scene_id]
                        generation_status = cache_entry['generation_status']
                        generation_status[option_index] = 'generating'
                        events = cache_entry['generation_events']
                        if option_index not in events:
                            events[option_index] = threading.Event()
                        wait_event = events[option_index]

                        def generate_selected_option_for_missing_scene():
                            try:
                                result = _generate_single_option(option_index, option, global_state)
                                if isinstance(result, dict):
                                    opt_data = result.get('data', result)
                                else:
                                    opt_data = result
                                with cache_lock:
                                    if scene_id in pregeneration_cache:
                                        entry = pregeneration_cache[scene_id]
                                        entry.setdefault('layer1', {})[option_index] = opt_data
                                        entry.setdefault('generation_status', {})[option_index] = 'completed'
                                        evs = entry.get('generation_events', {})
                                        if option_index in evs:
                                            evs[option_index].set()
                                print(f"✅ [generate-option] 按需生成完成：scene_id={scene_id}, option_index={option_index}")
                            except Exception as e:
                                print(f"❌ [generate-option] 按需生成失败：scene_id={scene_id}, option_index={option_index}, err={str(e)}")
                                with cache_lock:
                                    if scene_id in pregeneration_cache:
                                        entry = pregeneration_cache[scene_id]
                                        entry.setdefault('generation_status', {})[option_index] = 'failed'
                                        evs = entry.get('generation_events', {})
                                        if option_index in evs:
                                            evs[option_index].set()

                        thread = threading.Thread(target=generate_selected_option_for_missing_scene, daemon=True)
                        thread.start()
                        need_wait = True
        
        # 在释放锁后等待第二层线程退出（避免死锁）
        if layer2_thread_to_wait and layer2_thread_to_wait.is_alive():
            # 等待线程退出（最多等待2秒）
            layer2_thread_to_wait.join(timeout=2.0)
        
        # 如果需要等待，则等待生成完成
        if need_wait and wait_event:
            try:
                # 等待超时（默认300秒，可通过环境变量调节），避免前端卡死太久
                # 说明：前端对 /generate-option 的默认超时为 5 分钟，因此这里默认 300s 与其对齐。
                import time
                wait_timeout = int(os.getenv("OPTION_WAIT_TIMEOUT_SECONDS", "300"))
                start_wait_ts = time.time()
                print(f"⏳ [generate-option] 开始等待选项 {option_index} 生成完成（超时：{wait_timeout}秒）...")
                event_triggered = wait_event.wait(timeout=wait_timeout)
                
                if event_triggered:
                    print(f"✅ [generate-option] 等待事件已触发，选项 {option_index} 生成完成")
                else:
                    print(f"⚠️ [generate-option] 等待超时（{wait_timeout}秒），选项 {option_index} 可能仍在生成中")
                
                # 再次尝试从缓存读取（重要：不要在持锁状态下 sleep/wait，避免阻塞图片线程写回缓存）
                if not scene_id or scene_id == 'initial':
                    with cache_lock:
                        if 'initial' in pregeneration_cache:
                            initial_cache = pregeneration_cache['initial']
                            if initial_cache.get('completed', False):
                                if option_index == 0 and option == "开始游戏":
                                    initial_scene = initial_cache.get('initial_scene', '')
                                    initial_scene_image = initial_cache.get('initial_scene_image', None)
                                    initial_options = initial_cache.get('initial_options', [])
                                    option_data = {
                                        "scene": initial_scene,
                                        "scene_image": initial_scene_image,
                                        "next_options": initial_options,
                                        "flow_update": {},
                                        "deep_background_links": {}
                                    }
                                else:
                                    layer1_data = initial_cache.get('layer1', {})
                                    if option_index in layer1_data:
                                        option_data = layer1_data[option_index]
                else:
                    option_data_temp = None
                    status = 'pending'
                    scene_image = None

                    with cache_lock:
                        if scene_id in pregeneration_cache:
                            cache_entry = pregeneration_cache[scene_id]
                            option_data_temp = cache_entry.get('layer1', {}).get(option_index)
                            status = cache_entry.get('generation_status', {}).get(option_index, 'pending')
                            if isinstance(option_data_temp, dict):
                                scene_image = option_data_temp.get('scene_image')

                    if isinstance(option_data_temp, dict):
                        if status == 'completed' and scene_image and scene_image.get('url'):
                            option_data = option_data_temp
                        elif status == 'text_completed':
                            # 图片还在生成中，继续等待（在锁外 sleep，在锁内短读）
                            max_image_wait = 60
                            start_time = time.time()
                            while time.time() - start_time < max_image_wait:
                                time.sleep(0.5)
                                with cache_lock:
                                    if scene_id in pregeneration_cache:
                                        cache_entry = pregeneration_cache[scene_id]
                                        option_data_temp2 = cache_entry.get('layer1', {}).get(option_index)
                                        status2 = cache_entry.get('generation_status', {}).get(option_index, 'pending')
                                        if isinstance(option_data_temp2, dict):
                                            scene_image2 = option_data_temp2.get('scene_image')
                                            if status2 == 'completed' and scene_image2 and scene_image2.get('url'):
                                                option_data = option_data_temp2
                                                break
                            if not option_data:
                                # 等待超时：保持原逻辑，返回文本
                                option_data = option_data_temp
                        else:
                            option_data = option_data_temp

                # 🆕 关键修复：如果事件触发后仍未拿到 option_data，不要立即“同步再生成”，而是继续等待正在进行的预生成写回缓存
                # - 常见场景：后台线程仍在进行 LLM/图片生成，事件触发/超时后短时间内数据尚未写入
                # - 这里做一个“剩余时间内轮询”，确保优先等待预生成完成再返回
                if not option_data and scene_id and scene_id != 'initial':
                    poll_interval = float(os.getenv("OPTION_WAIT_POLL_SECONDS", "0.5"))
                    while time.time() - start_wait_ts < wait_timeout:
                        with cache_lock:
                            cache_entry = pregeneration_cache.get(scene_id)
                            if not cache_entry:
                                break
                            status = cache_entry.get('generation_status', {}).get(option_index, 'pending')
                            option_data_temp = cache_entry.get('layer1', {}).get(option_index)
                            if isinstance(option_data_temp, dict):
                                option_data = option_data_temp
                                break
                            if status in ['failed', 'cancelled']:
                                break
                        time.sleep(poll_interval)
                
                # 如果等待后仍然没有：
                # 不要返回 error + message（前端会把 message 当作剧情展示，并触发 /generate-scene-image，导致“生成超时”被画进图里）
                # 这里返回一个“安全兜底”的 optionData，让游戏可以继续，同时避免把错误文案喂给生图。
                if not option_data:
                    print(f"⚠️ [generate-option] 等待预生成到期仍未拿到 option_data，返回安全兜底数据（scene_id={scene_id}, option_index={option_index}）")
                    option_data = {
                        "scene": "当前内容生成耗时较长，但你仍可以继续推进剧情。你决定先观察局势并寻找下一步行动方向。",
                        "next_options": ["继续前进", "查看周围环境"],
                        "flow_update": {
                            "characters": {},
                            "environment": {},
                            "quest_progress": "继续推进",
                            "chapter_conflict_solved": False
                        },
                        "deep_background_links": {}
                    }
            except Exception as e:
                print(f"❌ 等待生成时发生错误：{str(e)}")
                return jsonify({
                    "status": "error",
                    "message": f"等待生成失败：{str(e)}"
                })
        
        # 🔧 修复：确保图片和文本一起返回
        # 如果数据存在但图片还没生成，等待图片生成完成
        if option_data and scene_id and scene_id != 'initial':
            scene_image = option_data.get('scene_image')
            if not scene_image or not scene_image.get('url'):
                # 图片还没生成，等待图片生成完成
                print(f"⏳ 文本数据已就绪，但图片还在生成中，等待图片生成完成...")
                import time
                max_image_wait = 60  # 最多等待60秒
                start_time = time.time()
                while time.time() - start_time < max_image_wait:
                    time.sleep(0.5)
                    with cache_lock:
                        if scene_id in pregeneration_cache:
                            cache_entry = pregeneration_cache[scene_id]
                            if option_index in cache_entry.get('layer1', {}):
                                option_data_temp = cache_entry['layer1'][option_index]
                                status = cache_entry.get('generation_status', {}).get(option_index, 'pending')
                                scene_image_temp = option_data_temp.get('scene_image')
                                if status == 'completed' and scene_image_temp and scene_image_temp.get('url'):
                                    option_data = option_data_temp
                                    print(f"✅ 图片生成完成，数据已就绪（包含图片）")
                                    break
                if not option_data.get('scene_image') or not option_data.get('scene_image', {}).get('url'):
                    print(f"⚠️ 图片生成超时，但继续返回文本数据（图片可能稍后生成）")
        
        # 如果仍然没有数据（不应该发生，但做容错处理）
        if not option_data:
            print(f"⚠️ 所有方法都失败，使用默认数据")
            option_data = {
                "scene": f"你选择了：{option}。在你的努力下，你取得了一些进展。",
                "next_options": ["继续前进", "查看当前状态", "返回上一步", "探索周围环境"],
                "flow_update": {
                    "characters": {},
                    "environment": {},
                    "quest_progress": f"你正在执行任务：{option}",
                    "chapter_conflict_solved": False
                },
                "deep_background_links": {}
            }
        
        # 返回结果前，清理上一轮的缓存（如果提供了上一轮的scene_id）
        previous_scene_id = data.get('previousSceneId', None)
        if previous_scene_id and previous_scene_id != scene_id and previous_scene_id != 'initial':
            with cache_lock:
                if previous_scene_id in pregeneration_cache:
                    # 停止该场景的第二层生成（如果正在生成）
                    prev_cache_entry = pregeneration_cache[previous_scene_id]
                    if prev_cache_entry.get('layer2_generating', False):
                        prev_cache_entry['layer2_cancel'] = True
                        layer2_thread = prev_cache_entry.get('layer2_thread')
                        if layer2_thread and layer2_thread.is_alive():
                            # 等待线程退出（最多等待1秒）
                            layer2_thread.join(timeout=1.0)
                    
                    # 删除上一轮的缓存
                    del pregeneration_cache[previous_scene_id]
                    print(f"🗑️ 已清理上一轮场景 {previous_scene_id} 的缓存")
        
        # 清理当前场景中未使用的选项数据（内存优化）
        if scene_id and scene_id != 'initial' and scene_id in pregeneration_cache:
            with cache_lock:
                cache_entry = pregeneration_cache[scene_id]
                # 🆕 先把“未选中的选项”标记为 cancelled，并触发其事件，避免后台线程继续回填导致状态卡死/刷警告
                generation_status = cache_entry.get('generation_status', {})
                events = cache_entry.get('generation_events', {})
                try:
                    for idx, st in list(generation_status.items()):
                        if idx == option_index:
                            continue
                        if st in ['pending', 'generating', 'text_completed', 'text_only']:
                            generation_status[idx] = 'cancelled'
                            ev = events.get(idx)
                            if ev:
                                ev.set()
                except Exception:
                    pass

                # 清理第一层中未使用的选项（保留当前使用的）
                # ✅ 优化：不要清理正在生成中的选项的数据，避免预生成完成后无法回填
                if 'layer1' in cache_entry:
                    layer1 = cache_entry['layer1']
                    generation_status = cache_entry.get('generation_status', {})
                    unused_indices = [idx for idx in layer1.keys() if idx != option_index]
                    for idx in unused_indices:
                        # 检查该选项是否正在生成中
                        status = generation_status.get(idx, 'pending')
                        if status in ['generating', 'text_completed']:
                            # 正在生成中，保留数据，等待预生成完成
                            print(f"⏸️ 选项 {idx} 正在生成中，保留数据等待预生成完成")
                            continue
                        del layer1[idx]
                        print(f"🗑️ 已清理未使用的选项 {idx} 的第一层数据")
                
                # 清理第二层中未使用的选项数据
                if 'layer2' in cache_entry:
                    layer2 = cache_entry['layer2']
                    # 只保留当前使用的选项的第二层数据
                    if option_index in layer2:
                        # 保留当前选项的第二层，但可以清理其他选项的第二层
                        current_layer2 = layer2[option_index]
                        # 清理其他选项的第二层
                        unused_layer2_indices = [idx for idx in layer2.keys() if idx != option_index]
                        for idx in unused_layer2_indices:
                            del layer2[idx]
                            print(f"🗑️ 已清理未使用的选项 {idx} 的第二层数据")
        
        # 定期清理旧缓存
        cleanup_old_cache(scene_id)

        # 如果返回的剧情数据缺少图片：默认不在 /generate-option 阻塞生成（避免长等待）。
        # 如需“选择后立即同步补图”，可设置环境变量：GENERATE_OPTION_ON_DEMAND_IMAGE=1
        # 修复：确保图片和文本匹配
        # 问题：预生成时只生成文本，不生成图片，导致从缓存读取时可能没有图片或图片不匹配
        # 解决方案：在返回数据前，检查并生成图片，确保图片和当前场景文本匹配
        try:
            if isinstance(option_data, dict) and option_data.get("scene"):
                scene_text = option_data.get("scene", "")
                scene_image = option_data.get("scene_image", None)
                
                # 检查是否需要生成图片：确保图片和场景文本匹配
                # 1. 没有图片 -> 生成
                # 2. 有图片但 URL 无效 -> 生成
                # 3. 图片存在且有效，但场景文本已变化 -> 重新生成（确保图片和文本匹配）
                # 4. 图片存在且有效，且场景文本未变化 -> 使用缓存
                need_generate_image = False
                
                if not scene_image:
                    need_generate_image = True
                    print(f"🔄 缓存数据缺少图片，立即生成图片（场景文本长度：{len(scene_text)}）")
                elif not isinstance(scene_image, dict):
                    need_generate_image = True
                    print(f"🔄 缓存数据图片格式无效（非字典类型），立即生成新图片")
                elif not scene_image.get("url"):
                    need_generate_image = True
                    print(f"🔄 缓存数据图片URL无效，立即生成新图片")
                elif isinstance(scene_text, str) and scene_text.strip():
                    # 计算当前场景文本的哈希值
                    current_scene_hash = hashlib.md5(scene_text.encode('utf-8')).hexdigest()
                    # 获取缓存图片关联的场景文本哈希（如果存在）
                    cached_scene_hash = scene_image.get("scene_text_hash", None)
                    # 如果场景文本已变化，需要重新生成图片以确保匹配
                    if cached_scene_hash != current_scene_hash:
                        need_generate_image = True
                        print(f"🔄 场景文本已变化（缓存哈希: {cached_scene_hash[:8] if cached_scene_hash else 'N/A'} vs 当前哈希: {current_scene_hash[:8]}），重新生成图片以确保匹配")
                
                if need_generate_image and isinstance(scene_text, str) and scene_text.strip():
                    print(f"🎨 正在为场景生成图片（确保图片和文本匹配）...")
                    img = generate_scene_image(scene_text, global_state, "default", use_cache=True)
                    if img and isinstance(img, dict) and img.get("url"):
                        # 计算并存储场景文本哈希，用于后续匹配检查
                        scene_text_hash = hashlib.md5(scene_text.encode('utf-8')).hexdigest()
                        option_data["scene_image"] = {
                            "url": img.get("url"),
                            "prompt": img.get("prompt", ""),
                            "style": img.get("style", "default"),
                            "width": img.get("width", 1024),
                            "height": img.get("height", 1024),
                            "cached": img.get("cached", True),
                            "scene_text_hash": scene_text_hash  # 存储场景文本哈希，用于匹配检查
                        }
                        print("✅ 已生成场景图片（确保图片和文本匹配）")
                    else:
                        print("⚠️ 场景图片生成失败，但继续返回文本")
        except Exception as e:
            print(f"⚠️ 生成场景图片失败，继续返回文本：{str(e)}")
            import traceback
            traceback.print_exc()
        
        # 返回结果
        return jsonify({
            "status": "success",
            "message": "选项剧情生成成功！",
            "optionData": option_data
        })
    except Exception as e:
        # 详细记录错误信息
        print(f"🔴 服务器错误：{str(e)}")
        import traceback
        traceback.print_exc()
        error_msg = clean_error_message(str(e))
        return jsonify({"status": "error", "message": f"选项剧情生成失败：{error_msg}"})

# 预生成两层内容的核心逻辑（提取为独立函数，可被其他函数调用）
def _pregenerate_next_layers_logic(global_state, current_options, scene_id):
    """
    预生成两层内容的核心逻辑（优先级策略 + 渐进式缓存）
    可以被接口函数或其他函数调用
    """
    # 🔍 调试日志：显示 scene_id 的处理
    print(f"🔍 [_pregenerate_next_layers_logic] scene_id 处理：")
    print(f"   - 传入的 scene_id：{scene_id}")
    
    # 如果没有提供scene_id，生成一个新的
    if not scene_id:
        scene_id = generate_scene_id(str(global_state), str(current_options))
        print(f"   - 未提供 scene_id，已生成新的：{scene_id}")
    else:
        print(f"   - 使用传入的 scene_id：{scene_id}")
    
    print(f"🔄 开始预生成场景 {scene_id} 的两层内容（优先级策略）...")
    
    # 在后台线程中异步执行预生成，不阻塞响应
    def async_pregenerate():
        try:
            # 初始化缓存条目（需要先加锁检查，避免重复初始化）
            with cache_lock:
                if scene_id not in pregeneration_cache:
                    pregeneration_cache[scene_id] = {
                        'layer1': {},
                        'layer2': {},
                        'generation_status': {},
                        'generation_events': {},
                        'should_cancel': False,
                        'current_generating_index': None,
                        'layer2_generating': False,
                        'layer2_cancel': False,
                        'layer2_selected_option': None,
                        'layer2_thread': None,
                        'current_layer2_option': None
                    }
                
                cache_entry = pregeneration_cache[scene_id]
                
                # 初始化所有选项的状态为 'pending'
                generation_status = cache_entry['generation_status']
                for i in range(len(current_options)):
                    if i not in generation_status:
                        generation_status[i] = 'pending'
                        # 创建事件对象
                        if 'generation_events' not in cache_entry:
                            cache_entry['generation_events'] = {}
                        if i not in cache_entry['generation_events']:
                            cache_entry['generation_events'][i] = threading.Event()
            
            # 第一层：并行生成所有选项（按优先级顺序提交任务），生成一个立即写入缓存
            print(f"📝 预生成第一层：并行生成 {len(current_options)} 个选项的下一轮剧情...")
            
            # 定义单个选项的生成任务函数
            def generate_single_option_task(opt_idx, option):
                """生成单个选项的任务函数"""
                # 在设置状态为 'generating' 之前就检查取消标志和状态
                with cache_lock:
                    if scene_id not in pregeneration_cache:
                        return
                    cache_entry = pregeneration_cache[scene_id]
                    generation_status = cache_entry.get('generation_status', {})
                    current_status = generation_status.get(opt_idx, 'pending')
                    
                    # 🆕 若该选项已被标记为取消（例如用户已做出选择并清理其他选项），直接退出
                    if current_status == 'cancelled':
                        return
                    
                    # 如果已经完成，不需要再生成
                    if current_status == 'completed':
                        return
                    
                    # 如果正在生成中，可能是用户选择的优先生成任务，避免重复生成
                    if current_status == 'generating':
                        # 检查缓存中是否已有数据（可能是优先生成任务已经完成）
                        if 'layer1' in cache_entry and opt_idx in cache_entry['layer1']:
                            return  # 已有数据，不需要重复生成
                        # 否则继续等待或生成（这里选择继续，因为可能是正常的并行生成）
                    
                    # 检查取消标志（只取消 'pending' 状态的选项）
                    if cache_entry.get('should_cancel', False):
                        if current_status == 'pending':
                            # 如果该选项还未开始生成，取消它
                            print(f"⏭️ 选项 {opt_idx} 被取消生成（用户选择了其他选项）")
                            return
                    
                    # 更新状态为 'generating'（只有在 pending 状态时才设置）
                    if current_status == 'pending':
                        generation_status[opt_idx] = 'generating'
                        cache_entry['current_generating_index'] = opt_idx
                
                print(f"📝 开始并行生成选项 {opt_idx + 1}/{len(current_options)}: {option[:30]}...")
                
                # 🆕 优化：检查是否已有文本数据（来自上一层的第二层预生成）
                try:
                    option_data = None
                    scene_for_image = None
                    text_already_exists = False
                    need_wait_for_text = False
                    text_wait_event = None
                    
                    with cache_lock:
                        if scene_id in pregeneration_cache:
                            cache_entry = pregeneration_cache[scene_id]
                            generation_status = cache_entry.get('generation_status', {})
                            status = generation_status.get(opt_idx, 'pending')
                            
                            if 'layer1' in cache_entry and opt_idx in cache_entry['layer1']:
                                existing_data = cache_entry['layer1'][opt_idx]
                                # 检查是否只有文本（没有图片或图片无效）
                                if existing_data and isinstance(existing_data, dict):
                                    existing_image = existing_data.get('scene_image')
                                    if not existing_image or not existing_image.get('url'):
                                        # 已有文本但缺少图片，复用文本，只生成图片
                                        option_data = existing_data.copy()
                                        scene_for_image = (option_data.get('scene') or '').strip() or None
                                        text_already_exists = True
                                        print(f"🔄 选项 {opt_idx} 已有文本数据，将只生成图片")
                            elif status == 'text_only':
                                # 🆕 优化：文本正在生成中（来自上一层的第二层预生成），等待完成
                                print(f"⏳ 选项 {opt_idx} 的文本正在生成中（来自上一层的第二层预生成），等待完成...")
                                need_wait_for_text = True
                                events = cache_entry.setdefault('generation_events', {})
                                if opt_idx not in events:
                                    events[opt_idx] = threading.Event()
                                text_wait_event = events[opt_idx]
                    
                    # 🆕 优化：如果文本正在生成中，等待完成
                    if need_wait_for_text and text_wait_event:
                        wait_timeout = 180  # 最多等待180秒
                        print(f"⏳ [第一层预生成] 等待选项 {opt_idx} 的文本生成完成（超时：{wait_timeout}秒）...")
                        event_triggered = text_wait_event.wait(timeout=wait_timeout)
                        
                        if event_triggered:
                            print(f"✅ [第一层预生成] 选项 {opt_idx} 的文本生成完成")
                            # 再次检查缓存，获取文本数据
                            with cache_lock:
                                if scene_id in pregeneration_cache:
                                    cache_entry = pregeneration_cache[scene_id]
                                    if 'layer1' in cache_entry and opt_idx in cache_entry['layer1']:
                                        existing_data = cache_entry['layer1'][opt_idx]
                                        if existing_data and isinstance(existing_data, dict):
                                            existing_image = existing_data.get('scene_image')
                                            if not existing_image or not existing_image.get('url'):
                                                option_data = existing_data.copy()
                                                scene_for_image = (option_data.get('scene') or '').strip() or None
                                                text_already_exists = True
                                                print(f"🔄 选项 {opt_idx} 文本生成完成，将只生成图片")
                        else:
                            print(f"⚠️ [第一层预生成] 等待选项 {opt_idx} 的文本生成超时，将正常生成（文本+图片）")
                    
                    # 如果没有文本数据，正常生成文本+图片
                    if not text_already_exists:
                        result = _generate_single_option_text_only(opt_idx, option, global_state)
                        if result is None:
                            print(f"⚠️ [第一层预生成] 选项 {opt_idx} 的文本生成返回 None")
                            option_data = None
                            scene_for_image = None
                        elif isinstance(result, dict):
                            option_data = result.get('data', result)
                            scene_for_image = result.get('scene_for_image')
                            if option_data is None:
                                print(f"⚠️ [第一层预生成] 选项 {opt_idx} 的 result['data'] 为 None")
                        else:
                            option_data = result
                            scene_for_image = (option_data.get('scene') if option_data else '').strip() or None if option_data else None
                        
                        # 🔍 调试日志：检查生成结果
                        if option_data:
                            print(f"✅ [第一层预生成] 选项 {opt_idx} 文本生成成功，scene_for_image: {bool(scene_for_image)}")
                        else:
                            print(f"⚠️ [第一层预生成] 选项 {opt_idx} 文本生成失败，option_data 为 None")
                    
                    # 🔧 优化：文本生成完成后立即写入缓存（让第二层预生成可以立即开始）
                    # 然后再生成图片并更新缓存
                    if option_data:  # 确保有数据才写入
                        # 🔧 使用带追踪的 cache_lock（可定位持锁线程）
                        with cache_lock:
                            # 🔍 再次检查 scene_id 是否在缓存中（在锁内）
                            if scene_id in pregeneration_cache:
                                cache_entry = pregeneration_cache[scene_id]
                                # 🆕 若该选项已被取消，则不再写入缓存（避免被清理后又“复活”）
                                if cache_entry.get('generation_status', {}).get(opt_idx) == 'cancelled':
                                    events = cache_entry.get('generation_events', {})
                                    if opt_idx in events:
                                        events[opt_idx].set()
                                    print(f"⏭️ [第一层预生成] 选项 {opt_idx} 已取消，跳过写入缓存")
                                    return
                                if 'layer1' not in cache_entry:
                                    cache_entry['layer1'] = {}
                                
                                # 🔍 写入前检查：是否已经有数据
                                if opt_idx in cache_entry['layer1']:
                                    print(f"⚠️ [第一层预生成] 选项 {opt_idx} 的数据已存在，将被覆盖")
                                
                                # 先写入文本数据（让第二层预生成可以立即开始）
                                cache_entry['layer1'][opt_idx] = option_data.copy()  # 复制，避免后续修改影响
                                cache_entry['generation_status'][opt_idx] = 'text_completed'  # 标记为文本已完成
                                
                                # 🔍 调试日志：显示写入缓存后的状态（简化日志，减少锁持有时间）
                                print(f"✅ 选项 {opt_idx} 文本已写入缓存（等待图片生成），scene_id: {scene_id}")
                                
                                # 触发等待事件（如果有线程在等待文本数据）
                                events = cache_entry.get('generation_events', {})
                                if opt_idx in events:
                                    events[opt_idx].set()
                                    print(f"   - 已触发选项 {opt_idx} 的等待事件（文本数据就绪）")
                            else:
                                print(f"⚠️ [第一层预生成] scene_id {scene_id} 不在缓存中，无法写入选项 {opt_idx} 的数据")
                    
                    # 为当前场景生成图片（限速由 yunwu 全局限速锁 + IMAGE_SUBMIT_DELAY 控制）
                    # 图片生成完成后更新缓存
                    if scene_for_image and option_data:
                        try:
                            print(f"🎨 [第一层预生成] 开始为选项 {opt_idx + 1} 生成图片...")
                            img = generate_scene_image(scene_for_image, global_state, "default", use_cache=True)
                            print(f"🎨 [第一层预生成] generate_scene_image 返回：img={img is not None}, type={type(img)}")
                            
                            if img and isinstance(img, dict) and img.get('url'):
                                print(f"🎨 [第一层预生成] 图片生成成功，URL: {img.get('url', 'N/A')[:80]}...")
                                scene_text_hash = hashlib.md5(scene_for_image.encode('utf-8')).hexdigest()
                                option_data['scene_image'] = {
                                    "url": img.get("url"),
                                    "prompt": img.get("prompt", ""),
                                    "style": img.get("style", "default"),
                                    "width": img.get("width", 1024),
                                    "height": img.get("height", 1024),
                                    "cached": img.get("cached", True),
                                    "scene_text_hash": scene_text_hash,
                                }
                                
                                print(f"🎨 [第一层预生成] 准备更新缓存中的图片数据...")
                                # 🔧 修复：添加超时机制，避免无限等待锁
                                import time
                                import threading as th
                                lock_acquired = False
                                lock_start_time = time.time()
                                max_lock_wait = 10  # 最多等待10秒获取锁
                                current_thread_name = th.current_thread().name
                                
                                print(f"🔍 [第一层预生成] 当前线程：{current_thread_name}，尝试获取缓存锁...")
                                
                                while not lock_acquired and (time.time() - lock_start_time) < max_lock_wait:
                                    try:
                                        # 尝试非阻塞获取锁
                                        if cache_lock.acquire(blocking=False):
                                            lock_acquired = True
                                            elapsed_wait = time.time() - lock_start_time
                                            print(f"🎨 [第一层预生成] 已获取缓存锁，开始更新...（等待时间：{elapsed_wait:.2f}秒，线程：{current_thread_name}）")
                                            try:
                                                if scene_id in pregeneration_cache:
                                                    cache_entry = pregeneration_cache[scene_id]
                                                    if opt_idx in cache_entry.get('layer1', {}):
                                                        cache_entry['layer1'][opt_idx]['scene_image'] = option_data['scene_image']
                                                        cache_entry['generation_status'][opt_idx] = 'completed'  # 标记为完全完成
                                                        print(f"🎨 [第一层预生成] 缓存更新完成，状态已设置为 completed")
                                                    else:
                                                        # ✅ 优化：即使 layer1 被清理，如果图片已生成，也应该写入缓存
                                                        # 原因：图片生成成本高，即使选项被取消，也应该保存以备后用
                                                        generation_status = cache_entry.get('generation_status', {})
                                                        current_status = generation_status.get(opt_idx, 'pending')
                                                        
                                                        # 如果状态是 generating、text_completed 或 cancelled，但图片已生成，都应该写入缓存
                                                        # cancelled 状态可能是因为用户选择了其他选项，但图片可能仍然有用
                                                        if current_status in ['generating', 'text_completed'] or (current_status == 'cancelled' and option_data.get('scene_image')):
                                                            # 重新创建 layer1 数据并写入图片
                                                            if 'layer1' not in cache_entry:
                                                                cache_entry['layer1'] = {}
                                                            cache_entry['layer1'][opt_idx] = option_data
                                                            # 如果之前是 cancelled，现在图片生成了，可以标记为 completed（图片已就绪）
                                                            if current_status == 'cancelled':
                                                                cache_entry['generation_status'][opt_idx] = 'completed'
                                                                print(f"✅ [第一层预生成] 选项 {opt_idx} 的 layer1 被清理且状态为 cancelled，但图片已生成，已重新写入缓存并标记为 completed")
                                                            else:
                                                                cache_entry['generation_status'][opt_idx] = 'completed'
                                                                print(f"✅ [第一层预生成] 选项 {opt_idx} 的 layer1 被清理但正在生成中，已重新写入缓存并完成")
                                                            events = cache_entry.get('generation_events', {})
                                                            if opt_idx in events:
                                                                events[opt_idx].set()
                                                        else:
                                                            # 确实是被取消的选项且没有图片，标记为 cancelled
                                                            cache_entry['generation_status'][opt_idx] = 'cancelled'
                                                            events = cache_entry.get('generation_events', {})
                                                            if opt_idx in events:
                                                                events[opt_idx].set()
                                                            print(f"⏭️ [第一层预生成] 选项 {opt_idx} 的 layer1 已被清理，标记为 cancelled（跳过图片回填）")
                                                else:
                                                    print(f"⚠️ [第一层预生成] 缓存中找不到 scene_id: {scene_id}")
                                            finally:
                                                cache_lock.release()
                                                print(f"🎨 [第一层预生成] 已释放缓存锁（线程：{current_thread_name}）")
                                        else:
                                            # 锁被其他线程持有，等待一小段时间后重试
                                            elapsed = time.time() - lock_start_time
                                            if elapsed < max_lock_wait:
                                                if int(elapsed * 2) % 2 == 0:  # 每0.5秒打印一次
                                                    # 🔧 调试：显示当前持有锁的线程信息
                                                    import threading as th
                                                    if _cache_lock_holder:
                                                        holder_name = _cache_lock_holder.name if hasattr(_cache_lock_holder, 'name') else str(_cache_lock_holder)
                                                        holder_time = time.time() - _cache_lock_acquire_time if _cache_lock_acquire_time else 0
                                                        print(f"⏳ [第一层预生成] 等待获取缓存锁...（已等待 {elapsed:.1f}秒，最多等待 {max_lock_wait}秒，线程：{current_thread_name}）")
                                                        print(f"   🔍 锁被线程持有：{holder_name}，已持有 {holder_time:.1f}秒")
                                                        if holder_time > 5:
                                                            print(f"   ⚠️ 警告：锁被持有超过5秒，可能存在死锁或耗时操作！")
                                                    # 🔧 关键：打印持锁线程的“实时堆栈”，定位卡在哪一行
                                                    stack = cache_lock.dump_holder_stack()
                                                    if stack:
                                                        print(f"   🧵 持锁线程实时堆栈（{holder_name}）:\n{stack}")
                                                    else:
                                                        print(f"⏳ [第一层预生成] 等待获取缓存锁...（已等待 {elapsed:.1f}秒，最多等待 {max_lock_wait}秒，线程：{current_thread_name}）")
                                                time.sleep(0.5)  # 等待0.5秒后重试
                                            else:
                                                break
                                    except Exception as lock_err:
                                        print(f"⚠️ [第一层预生成] 获取缓存锁时发生错误：{lock_err}")
                                        import traceback
                                        traceback.print_exc()
                                        break
                                
                                if not lock_acquired:
                                    print(f"❌ [第一层预生成] 获取缓存锁超时（{max_lock_wait}秒），跳过缓存更新")
                                    print(f"   💡 提示：图片数据已生成，但无法更新缓存，可能稍后会被其他线程更新")
                                    print(f"   🔍 调试：scene_id={scene_id}, opt_idx={opt_idx}, 线程：{current_thread_name}")
                                    # 🔧 修复：即使无法更新缓存，也尝试触发等待事件，避免其他线程无限等待
                                    try:
                                        if cache_lock.acquire(blocking=False):
                                            try:
                                                if scene_id in pregeneration_cache:
                                                    cache_entry = pregeneration_cache[scene_id]
                                                    events = cache_entry.get('generation_events', {})
                                                    if opt_idx in events:
                                                        events[opt_idx].set()
                                                        print(f"   ✅ 已触发等待事件，通知其他线程图片数据已就绪")
                                            finally:
                                                cache_lock.release()
                                    except:
                                        pass
                                
                                if text_already_exists:
                                    print(f"✅ 选项 {opt_idx + 1} 图片已生成（复用文本）")
                                else:
                                    print(f"✅ 选项 {opt_idx + 1} 场景图片已预生成并更新缓存")
                            else:
                                print(f"⚠️ 选项 {opt_idx + 1} 场景图片生成失败，将按需补图（img={img}, url={img.get('url') if img else 'N/A'}）")
                        except Exception as img_err:
                            print(f"⚠️ 选项 {opt_idx + 1} 场景图片生成异常：{img_err}，将按需补图")
                            import traceback
                            traceback.print_exc()
                    elif not option_data:
                        print(f"⚠️ [第一层预生成] 选项 {opt_idx} 的 option_data 为空，无法写入缓存")
                        # 即使 option_data 为空，也要更新状态，避免一直处于 generating
                        with cache_lock:
                            if scene_id in pregeneration_cache:
                                cache_entry = pregeneration_cache[scene_id]
                                cache_entry['generation_status'][opt_idx] = 'failed'
                                events = cache_entry.get('generation_events', {})
                                if opt_idx in events:
                                    events[opt_idx].set()
                                    print(f"   - 已触发选项 {opt_idx} 的等待事件（失败状态）")
                except Exception as e:
                    print(f"❌ 生成选项 {opt_idx} 失败：{str(e)}")
                    print(f"   - scene_id: {scene_id}")
                    import traceback
                    traceback.print_exc()
                    with cache_lock:
                        if scene_id in pregeneration_cache:
                            cache_entry = pregeneration_cache[scene_id]
                            cache_entry['generation_status'][opt_idx] = 'failed'
                            events = cache_entry.get('generation_events', {})
                            if opt_idx in events:
                                events[opt_idx].set()
                        else:
                            print(f"⚠️ [异常处理] scene_id {scene_id} 不在缓存中，无法更新状态")
            
            # 使用线程池并行生成所有选项（按优先级顺序提交任务）
            # 限制并发，避免同时触发过多 LLM/下游调用导致排队或限流
            max_workers = min(len(current_options), int(os.getenv("PREGEN_MAX_WORKERS", "2")))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # 按优先级顺序（0→1→2→3）提交所有任务
                futures = []
                for opt_idx in range(len(current_options)):
                    option = current_options[opt_idx]
                    future = executor.submit(generate_single_option_task, opt_idx, option)
                    futures.append((opt_idx, future))
                
                # 等待所有任务完成（可选，但保留以便跟踪完成状态）
                print(f"🔍 [第一层预生成] 开始等待所有任务完成，共 {len(futures)} 个任务")
                for opt_idx, future in futures:
                    try:
                        future.result()  # 等待任务完成，如果有异常会抛出
                        print(f"✅ [第一层预生成] 选项 {opt_idx} 的任务已完成")
                        # 🔍 立即检查缓存状态
                        with cache_lock:
                            if scene_id in pregeneration_cache:
                                cache_entry = pregeneration_cache[scene_id]
                                if opt_idx in cache_entry.get('layer1', {}):
                                    print(f"   ✅ 选项 {opt_idx} 的数据已在缓存中")
                                else:
                                    print(f"   ⚠️ 选项 {opt_idx} 的数据不在缓存中！")
                                status = cache_entry.get('generation_status', {}).get(opt_idx, 'unknown')
                                print(f"   - 选项 {opt_idx} 的状态: {status}")
                    except Exception as e:
                        print(f"❌ 选项 {opt_idx} 的任务执行异常：{str(e)}")
                        import traceback
                        traceback.print_exc()
            
            # 清理当前生成索引
            with cache_lock:
                if scene_id in pregeneration_cache:
                    pregeneration_cache[scene_id]['current_generating_index'] = None
            
            # 🔍 调试日志：检查第一层预生成完成后的缓存状态
            with cache_lock:
                if scene_id in pregeneration_cache:
                    cache_entry = pregeneration_cache[scene_id]
                    layer1_count = len(cache_entry.get('layer1', {}))
                    generation_status = cache_entry.get('generation_status', {})
                    print(f"✅ 第一层预生成完成，共生成 {layer1_count} 个选项的剧情+图片")
                    print(f"   - scene_id: {scene_id}")
                    print(f"   - layer1 选项索引：{list(cache_entry.get('layer1', {}).keys())}")
                    print(f"   - 生成状态：{generation_status}")
                    if layer1_count == 0:
                        cancelled_count = sum(1 for s in generation_status.values() if s == 'cancelled')
                        if cancelled_count > 0:
                            print(f"ℹ️ 第一层预生成完成但 layer1 为空：可能因为用户已选择选项，未使用的 layer1 被清理（cancelled={cancelled_count}）")
                        else:
                            print(f"⚠️ [警告] 第一层预生成完成，但 layer1 为空！")
                            print(f"   - 可能的原因：所有选项生成失败，或 scene_id 不匹配")
                else:
                    print(f"⚠️ [警告] scene_id {scene_id} 不在缓存中！")
            print("---------------------------------------------- 第一层预生成完成 ----------------------------------------------")
            
            # 第二层：为第一层的每个选项的next_options预生成再下一层剧情（继续在后台异步生成）
            print(f"📝 预生成第二层：为下一轮选项生成再下一层剧情...")
            print("---------------------------------------------- 开始第二层预生成 ----------------------------------------------")
            
            def generate_layer2():
                try:
                    # 🔧 优化：等待第一层文本数据写入缓存（文本生成完成后立即写入，所以等待时间很短）
                    import time
                    max_wait_attempts = 10  # 最多等待10次（文本生成很快）
                    wait_interval = 0.3  # 每次等待0.3秒
                    layer1_data = {}
                    selected_option = None
                    
                    for attempt in range(max_wait_attempts):
                        # 🔧 修复：在锁外检查，避免长时间持有锁
                        should_continue = False
                        with cache_lock:
                            if scene_id not in pregeneration_cache:
                                if attempt < max_wait_attempts - 1:
                                    should_continue = True
                                else:
                                    return
                            
                            if should_continue:
                                # 释放锁后再 sleep
                                pass
                            else:
                                cache_entry = pregeneration_cache[scene_id]
                                layer1_data_temp = cache_entry.get('layer1', {})
                                expected_count = len(current_options)
                                
                                # 检查是否所有选项的文本数据都已写入缓存（只需要文本数据，不需要图片）
                                text_completed_count = 0
                                for opt_idx in range(expected_count):
                                    status = cache_entry.get('generation_status', {}).get(opt_idx, 'pending')
                                    if status in ['text_completed', 'completed'] and opt_idx in layer1_data_temp:
                                        text_completed_count += 1
                                
                                if text_completed_count >= expected_count:
                                    layer1_data = layer1_data_temp.copy()  # 复制数据，避免长时间持有锁
                                    selected_option = cache_entry.get('layer2_selected_option', None)
                                    print(f"✅ [第二层预生成] 第一层文本数据已就绪，共 {text_completed_count} 个选项")
                                    break
                                elif attempt < max_wait_attempts - 1:
                                    print(f"⏳ [第二层预生成] 等待第一层文本数据... ({text_completed_count}/{expected_count}，尝试 {attempt+1}/{max_wait_attempts})")
                                    should_continue = True
                                else:
                                    # 最后一次尝试，即使数据不完整也继续
                                    layer1_data = layer1_data_temp.copy()
                                    selected_option = cache_entry.get('layer2_selected_option', None)
                                    print(f"⚠️ [第二层预生成] 等待超时，当前只有 {text_completed_count}/{expected_count} 个选项的文本数据，继续生成")
                        
                        # 🔧 修复：在锁外 sleep，避免阻塞其他线程
                        if should_continue:
                            time.sleep(wait_interval)
                    
                    need_process_options = []
                    
                    # 检查是否有用户选择的选项（如果用户在选择时设置了）
                    # 如果用户已经选择了选项，只生成该选项的第二层
                    if selected_option is not None:
                        print(f"📝 只为用户选择的选项 {selected_option} 生成第二层")
                        if selected_option not in layer1_data:
                            print(f"⚠️ 用户选择的选项 {selected_option} 不在第一层数据中")
                            return
                        
                        # 只处理用户选择的选项
                        opt_idx = selected_option
                        layer1_option_data = layer1_data[opt_idx]
                        next_options = layer1_option_data.get('next_options', [])
                        
                        if next_options:
                            # 检查取消标志（在锁外快速检查）
                            with cache_lock:
                                if scene_id not in pregeneration_cache:
                                    return
                                cache_entry = pregeneration_cache[scene_id]
                                if cache_entry.get('layer2_cancel', False):
                                    print(f"⏹️ 选项 {opt_idx} 的第二层生成被取消")
                                    return
                                # 标记当前正在生成的选项
                                cache_entry['current_layer2_option'] = opt_idx
                            
                            # 更新global_state（应用第一层的flow_update）
                            updated_global_state = global_state.copy()
                            if 'flow_worldline' not in updated_global_state:
                                updated_global_state['flow_worldline'] = {}
                            flow_update = layer1_option_data.get('flow_update', {})
                            if flow_update:
                                updated_global_state['flow_worldline'].update(flow_update)
                            
                            # 计算下一层场景的 scene_id（用于存储第二层预生成的数据）
                            next_scene_id = generate_scene_id(str(updated_global_state), str(next_options))
                            print(f"🔍 [第二层预生成] 计算下一层场景ID：{next_scene_id}")
                            
                            # 为下一轮的每个选项生成再下一层剧情（在锁外执行，避免长时间持有锁）
                            try:
                                layer2_data = generate_all_options(updated_global_state, next_options, skip_images=True)
                                
                                # 再次检查取消标志并写入缓存（生成过程中可能被取消）
                                with cache_lock:
                                    if scene_id in pregeneration_cache:
                                        cache_entry = pregeneration_cache[scene_id]
                                        if cache_entry.get('layer2_cancel', False):
                                            print(f"⏹️ 选项 {opt_idx} 的第二层生成在生成过程中被取消")
                                            return
                                    
                                    # 🆕 优化：将第二层预生成的数据存储到下一层场景的 layer1（只有文本）
                                    # 初始化下一层场景的缓存结构
                                    if next_scene_id not in pregeneration_cache:
                                        pregeneration_cache[next_scene_id] = {
                                            'layer1': {},
                                            'layer2': {},
                                            'generation_status': {},
                                            'generation_events': {},
                                            'should_cancel': False,
                                            'current_generating_index': None,
                                            'layer2_generating': False,
                                            'layer2_cancel': False,
                                            'layer2_selected_option': None,
                                            'layer2_thread': None,
                                            'current_layer2_option': None,
                                            'text_only_mode': True  # 标记：只有文本，需要后续生成图片
                                        }
                                    
                                    next_cache_entry = pregeneration_cache[next_scene_id]
                                    
                                    # 将第二层预生成的数据存储到下一层场景的 layer1（只有文本）
                                    # layer2_data 格式：{option_index: option_data}
                                    for next_opt_idx, next_option_data in layer2_data.items():
                                        if 'layer1' not in next_cache_entry:
                                            next_cache_entry['layer1'] = {}
                                        next_cache_entry['layer1'][next_opt_idx] = next_option_data
                                        # 标记为只有文本，需要后续生成图片
                                        next_cache_entry['generation_status'][next_opt_idx] = 'text_only'
                                        
                                        # 🆕 创建等待事件，用于通知第一层预生成文本已完成
                                        events = next_cache_entry.setdefault('generation_events', {})
                                        if next_opt_idx not in events:
                                            events[next_opt_idx] = threading.Event()
                                        
                                        # 触发等待事件，通知第一层预生成可以开始生成图片了
                                        events[next_opt_idx].set()
                                        print(f"✅ 选项 {opt_idx} 的第二层数据已存储到下一层场景 {next_scene_id} 的 layer1[{next_opt_idx}]（只有文本），已触发等待事件")
                                    
                                    # 保留原有的 layer2 存储（向后兼容）
                                    if 'layer2' not in cache_entry:
                                        cache_entry['layer2'] = {}
                                    cache_entry['layer2'][opt_idx] = layer2_data
                                    print(f"✅ 选项 {opt_idx} 的第二层生成完成，共生成 {len(layer2_data)} 个选项的剧情（已存储到下一层场景 {next_scene_id}）")
                            except Exception as e:
                                print(f"❌ 生成选项 {opt_idx} 的第二层失败：{str(e)}")
                        
                        print(f"✅ 第二层预生成完成（仅生成用户选择的选项）")
                        print("---------------------------------------------- 第二层预生成完成（用户选择模式） ----------------------------------------------")
                    else:
                        # 用户还未选择，为所有第一层选项生成第二层
                        layer2_count = 0
                        for opt_idx, layer1_option_data in layer1_data.items():
                            # 检查取消标志（在锁外快速检查）
                            with cache_lock:
                                if scene_id not in pregeneration_cache:
                                    return
                                cache_entry = pregeneration_cache[scene_id]
                                if cache_entry.get('layer2_cancel', False):
                                    print(f"⏹️ 第二层生成被取消（用户选择了其他选项）")
                                    return
                                # 标记当前正在生成的选项
                                cache_entry['current_layer2_option'] = opt_idx
                            
                            next_options = layer1_option_data.get('next_options', [])
                            if next_options:
                                # 更新global_state（应用第一层的flow_update）
                                updated_global_state = global_state.copy()
                                if 'flow_worldline' not in updated_global_state:
                                    updated_global_state['flow_worldline'] = {}
                                flow_update = layer1_option_data.get('flow_update', {})
                                if flow_update:
                                    updated_global_state['flow_worldline'].update(flow_update)
                                
                                # 计算下一层场景的 scene_id（用于存储第二层预生成的数据）
                                next_scene_id = generate_scene_id(str(updated_global_state), str(next_options))
                                
                                # 为下一轮的每个选项生成再下一层剧情（在锁外执行，避免长时间持有锁）
                                try:
                                    layer2_data = generate_all_options(updated_global_state, next_options, skip_images=True)
                                    
                                    # 再次检查取消标志并写入缓存（生成过程中可能被取消）
                                    with cache_lock:
                                        if scene_id in pregeneration_cache:
                                            cache_entry = pregeneration_cache[scene_id]
                                            if cache_entry.get('layer2_cancel', False):
                                                print(f"⏹️ 选项 {opt_idx} 的第二层生成在生成过程中被取消")
                                                return
                                        
                                        # 🆕 优化：将第二层预生成的数据存储到下一层场景的 layer1（只有文本）
                                        # 初始化下一层场景的缓存结构
                                        if next_scene_id not in pregeneration_cache:
                                            pregeneration_cache[next_scene_id] = {
                                                'layer1': {},
                                                'layer2': {},
                                                'generation_status': {},
                                                'generation_events': {},
                                                'should_cancel': False,
                                                'current_generating_index': None,
                                                'layer2_generating': False,
                                                'layer2_cancel': False,
                                                'layer2_selected_option': None,
                                                'layer2_thread': None,
                                                'current_layer2_option': None,
                                                'text_only_mode': True  # 标记：只有文本，需要后续生成图片
                                            }
                                        
                                        next_cache_entry = pregeneration_cache[next_scene_id]
                                        
                                        # 将第二层预生成的数据存储到下一层场景的 layer1（只有文本）
                                        # layer2_data 格式：{option_index: option_data}
                                        for next_opt_idx, next_option_data in layer2_data.items():
                                            if 'layer1' not in next_cache_entry:
                                                next_cache_entry['layer1'] = {}
                                            next_cache_entry['layer1'][next_opt_idx] = next_option_data
                                            # 标记为只有文本，需要后续生成图片
                                            next_cache_entry['generation_status'][next_opt_idx] = 'text_only'
                                            
                                            # 🆕 创建等待事件，用于通知第一层预生成文本已完成
                                            events = next_cache_entry.setdefault('generation_events', {})
                                            if next_opt_idx not in events:
                                                events[next_opt_idx] = threading.Event()
                                            
                                            # 触发等待事件，通知第一层预生成可以开始生成图片了
                                            events[next_opt_idx].set()
                                        
                                        # 保留原有的 layer2 存储（向后兼容）
                                        if 'layer2' not in cache_entry:
                                            cache_entry['layer2'] = {}
                                        cache_entry['layer2'][opt_idx] = layer2_data
                                        layer2_count += len(layer2_data)
                                except Exception as e:
                                    print(f"❌ 生成选项 {opt_idx} 的第二层失败：{str(e)}")
                        
                        print(f"✅ 第二层预生成完成，共生成 {layer2_count} 个选项的剧情")
                        print(f"✅ 场景 {scene_id} 的两层内容预生成全部完成")
                        print("---------------------------------------------- 第二层预生成完成（全量模式） ----------------------------------------------")
                except Exception as e:
                    print(f"❌ 生成第二层时发生错误：{str(e)}")
                    import traceback
                    traceback.print_exc()
                finally:
                    # 标记第二层生成完成
                    with cache_lock:
                        if scene_id in pregeneration_cache:
                            pregeneration_cache[scene_id]['layer2_generating'] = False
                            pregeneration_cache[scene_id]['current_layer2_option'] = None
            
            # 第二层在后台线程中继续生成（不阻塞）
            with cache_lock:
                if scene_id in pregeneration_cache:
                    cache_entry = pregeneration_cache[scene_id]
                    cache_entry['layer2_generating'] = True
                    cache_entry['layer2_cancel'] = False
                    layer2_thread = threading.Thread(target=generate_layer2, daemon=True)
                    cache_entry['layer2_thread'] = layer2_thread
                    layer2_thread.start()
                
        except Exception as e:
            print(f"❌ 预生成过程中发生错误：{str(e)}")
            import traceback
            traceback.print_exc()
    
    # 启动后台线程执行预生成
    thread = threading.Thread(target=async_pregenerate, daemon=True)
    thread.start()
    
    return scene_id

# 新增接口：预生成两层内容（优先级策略 + 渐进式缓存）
@app.route('/pregenerate-next-layers', methods=['POST'])
def pregenerate_next_layers():
    """
    预生成两层内容（按优先级顺序渐进式生成）：
    - 第一层：按优先级顺序（0→1→2→3）逐个生成，生成一个立即写入缓存
    - 第二层：第一层完成后，继续在后台生成第二层
    """
    try:
        # 获取前端传的参数
        data = request.json
        global_state = data.get('globalState', {})
        current_options = data.get('currentOptions', [])
        scene_id = data.get('sceneId', None)  # 当前场景ID
        
        # 新增：图片依赖生成（用于预生成时也带上“上一剧情图片参考”）
        current_scene_image = data.get('currentSceneImage', None)  # {url,prompt,...}（可选）
        current_scene_text = data.get('currentSceneText', '')  # 可选：当前剧情文本（作为连续性信息）
        if isinstance(global_state, dict) and (current_scene_image or current_scene_text):
            global_state['_visual_context'] = {
                "sceneId": scene_id,
                "currentSceneImage": current_scene_image,
                "currentSceneText": current_scene_text
            }
            # 尝试写入缓存条目（若已存在）
            if scene_id:
                with cache_lock:
                    if scene_id in pregeneration_cache:
                        pregeneration_cache[scene_id]['visual_context'] = global_state['_visual_context']
        
        # 基础校验
        if not global_state:
            return jsonify({"status": "error", "message": "全局状态不能为空！"})
        if not current_options:
            return jsonify({"status": "error", "message": "当前选项列表不能为空！"})
        
        # 🔍 调试日志：显示预生成使用的 scene_id
        print(f"🔍 [pregenerate-next-layers] 预生成参数：")
        print(f"   - 前端传入的 sceneId：{scene_id}")
        print(f"   - 当前选项数量：{len(current_options)}")
        
        # 调用预生成核心逻辑
        scene_id = _pregenerate_next_layers_logic(global_state, current_options, scene_id)
        
        # 🔍 调试日志：显示预生成返回的 scene_id
        print(f"🔍 [pregenerate-next-layers] 预生成返回的 sceneId：{scene_id}")
        print(f"   - 返回给前端的 sceneId：{scene_id}")
        
        # 立即返回，告知前端预生成已启动
        return jsonify({
            "status": "success",
            "message": "预生成任务已启动！",
            "sceneId": scene_id
        })
        
    except Exception as e:
        print(f"🔴 预生成接口错误：{str(e)}")
        import traceback
        traceback.print_exc()
        error_msg = clean_error_message(str(e))
        return jsonify({"status": "error", "message": f"预生成任务启动失败：{error_msg}"})

# 新增接口：获取预生成的第二层内容
@app.route('/get-pregenerated-layer2', methods=['POST'])
def get_pregenerated_layer2():
    """获取预生成的第二层内容（当用户选择了第一层的某个选项后，可以立即获取第二层）"""
    try:
        data = request.json
        scene_id = data.get('sceneId', None)
        layer1_option_index = data.get('layer1OptionIndex', None)
        layer2_option_index = data.get('layer2OptionIndex', None)
        
        if not scene_id or layer1_option_index is None or layer2_option_index is None:
            return jsonify({"status": "error", "message": "参数不完整！"})
        
        with cache_lock:
            if scene_id in pregeneration_cache:
                cache_entry = pregeneration_cache[scene_id]
                if 'layer2' in cache_entry and layer1_option_index in cache_entry['layer2']:
                    layer2_data = cache_entry['layer2'][layer1_option_index]
                    if layer2_option_index in layer2_data:
                        return jsonify({
                            "status": "success",
                            "optionData": layer2_data[layer2_option_index]
                        })
        
        return jsonify({"status": "error", "message": "未找到预生成的第二层内容！"})
        
    except Exception as e:
        print(f"🔴 获取预生成内容错误：{str(e)}")
        error_msg = clean_error_message(str(e))
        return jsonify({"status": "error", "message": f"获取失败：{error_msg}"})

# 新增接口：保存游戏
@app.route('/save-game', methods=['POST'])
def save_game():
    """
    保存游戏状态到文件
    接收前端传来的游戏状态数据，保存为JSON文件
    """
    try:
        data = request.json
        save_name = data.get('saveName', '').strip()
        global_state = data.get('globalState', {})
        protagonist_attr = data.get('protagonistAttr', {})
        difficulty = data.get('difficulty', '')
        last_options = data.get('lastOptions', [])
        
        # 基础校验
        if not save_name:
            return jsonify({"status": "error", "message": "存档名称不能为空！"})
        # 允许空的global_state（可能是游戏刚开始还没有生成世界观）
        if global_state is None:
            global_state = {}
        
        # 构造存档数据（与main2.py中的格式保持一致）
        save_data = {
            "global_state": global_state,
            "protagonist_attr": protagonist_attr,
            "difficulty": difficulty,
            "last_options": last_options,
            "timestamp": str(datetime.now())
        }
        
        # 生成存档文件名
        save_filename = f"{save_name}.json"
        save_path = os.path.join(SAVE_DIR, save_filename)
        
        # 保存到文件（带重试机制）
        max_retries = 3
        for attempt in range(max_retries):
            try:
                with open(save_path, 'w', encoding='utf-8') as f:
                    json.dump(save_data, f, ensure_ascii=False, indent=2)
                print(f"✅ 游戏已保存到：{save_path}")
                return jsonify({
                    "status": "success",
                    "message": "游戏已成功保存！",
                    "savePath": save_path
                })
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"⚠️ 保存失败（尝试 {attempt + 1}/{max_retries}），重试中...")
                    import time
                    time.sleep(0.5)  # 等待0.5秒后重试
                else:
                    raise e
        
    except Exception as e:
        print(f"🔴 保存游戏错误：{str(e)}")
        import traceback
        traceback.print_exc()
        error_msg = clean_error_message(str(e))
        return jsonify({"status": "error", "message": f"保存失败，请重试：{error_msg}"})

# 新增接口：列出所有存档
@app.route('/list-saves', methods=['GET'])
def list_saves():
    """
    列出所有存档文件
    返回存档名称列表和基本信息
    """
    try:
        saves = []
        if os.path.exists(SAVE_DIR):
            for file in os.listdir(SAVE_DIR):
                if file.endswith('.json'):
                    save_name = file[:-5]  # 去掉.json后缀
                    save_path = os.path.join(SAVE_DIR, file)
                    
                    # 读取存档基本信息（不加载完整数据）
                    try:
                        with open(save_path, 'r', encoding='utf-8') as f:
                            save_data = json.load(f)
                        
                        # 获取存档时间
                        timestamp = save_data.get('timestamp', '')
                        
                        # 计算进度信息
                        global_state = save_data.get('global_state', {})
                        flow_worldline = global_state.get('flow_worldline', {})
                        current_chapter = flow_worldline.get('current_chapter', 'chapter1')
                        chapter_name = '第一章' if current_chapter == 'chapter1' else ('第二章' if current_chapter == 'chapter2' else '第三章')
                        
                        saves.append({
                            "name": save_name,
                            "timestamp": timestamp,
                            "chapter": chapter_name
                        })
                    except Exception as e:
                        print(f"⚠️ 读取存档 {save_name} 信息失败：{str(e)}")
                        saves.append({
                            "name": save_name,
                            "timestamp": "",
                            "chapter": "未知"
                        })
        
        return jsonify({
            "status": "success",
            "saves": saves
        })
        
    except Exception as e:
        print(f"🔴 列出存档错误：{str(e)}")
        import traceback
        traceback.print_exc()
        error_msg = clean_error_message(str(e))
        return jsonify({"status": "error", "message": f"列出存档失败：{error_msg}", "saves": []})

# 新增接口：加载游戏
@app.route('/load-game', methods=['POST'])
def load_game():
    """
    加载指定存档
    接收存档名称，返回完整的游戏状态数据
    """
    try:
        data = request.json
        save_name = data.get('saveName', '').strip()
        
        if not save_name:
            return jsonify({"status": "error", "message": "存档名称不能为空！"})
        
        # 生成存档文件名
        save_filename = f"{save_name}.json"
        save_path = os.path.join(SAVE_DIR, save_filename)
        
        # 检查文件是否存在
        if not os.path.exists(save_path):
            return jsonify({"status": "error", "message": f"存档文件不存在：{save_name}"})
        
        # 读取存档数据（带重试机制）
        max_retries = 3
        save_data = None
        for attempt in range(max_retries):
            try:
                with open(save_path, 'r', encoding='utf-8') as f:
                    save_data = json.load(f)
                break  # 成功读取，退出重试循环
            except Exception as e:
                if attempt < max_retries - 1:
                    print(f"⚠️ 加载失败（尝试 {attempt + 1}/{max_retries}），重试中...")
                    import time
                    time.sleep(0.5)  # 等待0.5秒后重试
                else:
                    raise e
        
        if not save_data:
            return jsonify({"status": "error", "message": "加载失败，请重试"})
        
        print(f"✅ 游戏已从：{save_path} 加载")
        
        # 返回完整的存档数据
        return jsonify({
            "status": "success",
            "message": "游戏加载成功！",
            "saveData": save_data
        })
        
    except Exception as e:
        print(f"🔴 加载游戏错误：{str(e)}")
        import traceback
        traceback.print_exc()
        error_msg = clean_error_message(str(e))
        return jsonify({"status": "error", "message": f"加载失败，请重试：{error_msg}"})

# 新增接口：删除存档
@app.route('/delete-save', methods=['POST'])
def delete_save():
    """
    删除指定存档文件
    """
    try:
        data = request.json
        save_name = data.get('saveName', '').strip()
        
        if not save_name:
            return jsonify({"status": "error", "message": "存档名称不能为空！"})
        
        # 生成存档文件名
        save_filename = f"{save_name}.json"
        save_path = os.path.join(SAVE_DIR, save_filename)
        
        # 检查文件是否存在
        if not os.path.exists(save_path):
            return jsonify({"status": "error", "message": f"存档文件不存在：{save_name}"})
        
        # 删除文件
        os.remove(save_path)
        print(f"✅ 已删除存档：{save_path}")
        
        return jsonify({
            "status": "success",
            "message": "存档已成功删除！"
        })
        
    except Exception as e:
        print(f"🔴 删除存档错误：{str(e)}")
        import traceback
        traceback.print_exc()
        error_msg = clean_error_message(str(e))
        return jsonify({"status": "error", "message": f"删除存档失败：{error_msg}"})

# 新增接口：生成游戏结局
@app.route('/generate-ending', methods=['POST'])
def generate_ending():
    """
    生成游戏结局（基于当前游戏状态）
    当用户主动选择结束游戏时调用此接口
    """
    try:
        # 获取前端传的参数
        data = request.json
        global_state = data.get('globalState', {})
        
        # 基础校验
        if not global_state:
            return jsonify({"status": "error", "message": "全局状态不能为空！"})
        
        print(f"🔄 开始生成游戏结局...")
        
        # 确保隐藏结局预测存在
        if 'hidden_ending_prediction' not in global_state:
            print(f"📝 生成初始结局预测...")
            global_state['hidden_ending_prediction'] = generate_ending_prediction(global_state)
        
        # 基于当前游戏进度修改结局内容（生成最终结局）
        print(f"📝 基于当前游戏进度生成最终结局...")
        modify_ending_content(global_state)
        
        # 获取最终的结局预测
        ending_prediction = global_state.get('hidden_ending_prediction', {})
        main_tone = ending_prediction.get('main_tone', 'NE')
        content = ending_prediction.get('content', '主角完成了主要任务，虽然过程中经历了许多困难，但最终达成了预期目标')
        
        print(f"✅ 游戏结局生成完成，主基调：{main_tone}")
        
        # 返回结果
        return jsonify({
            "status": "success",
            "message": "游戏结局生成成功！",
            "ending": {
                "main_tone": main_tone,
                "content": content
            }
        })
        
    except Exception as e:
        print(f"🔴 生成游戏结局错误：{str(e)}")
        import traceback
        traceback.print_exc()
        error_msg = clean_error_message(str(e))
        return jsonify({"status": "error", "message": f"生成游戏结局失败：{error_msg}"})

# ------------------------------
# 图片缓存管理函数
# ------------------------------
import hashlib

def get_cached_image(prompt_hash: str) -> str:
    """从缓存获取图片路径"""
    cache_path = Path(IMAGE_CACHE_DIR) / f"{prompt_hash}.png"
    if cache_path.exists():
        return str(cache_path)
    return None

def cache_image(prompt_hash: str, image_url: str) -> str:
    """缓存图片到本地"""
    try:
        # 检查是否是相对路径（本地缓存路径）
        if image_url.startswith('/image_cache/') or image_url.startswith('image_cache/'):
            # 已经是本地缓存路径，不需要下载
            cache_path = Path(IMAGE_CACHE_DIR) / f"{prompt_hash}.png"
            if cache_path.exists():
                print(f"✅ 图片已在本地缓存：{cache_path}")
                return str(cache_path)
            else:
                # 如果文件不存在，尝试从相对路径提取hash
                import re
                hash_match = re.search(r'([a-f0-9]{32})\.png', image_url)
                if hash_match:
                    existing_hash = hash_match.group(1)
                    existing_path = Path(IMAGE_CACHE_DIR) / f"{existing_hash}.png"
                    if existing_path.exists():
                        # 复制文件到新的hash名称
                        import shutil
                        shutil.copy2(existing_path, cache_path)
                        print(f"✅ 从现有缓存复制图片：{cache_path}")
                        return str(cache_path)
                raise ValueError(f"本地缓存文件不存在：{image_url}")
        
        # 检查是否是完整的URL
        if not (image_url.startswith('http://') or image_url.startswith('https://')):
            raise ValueError(f"无效的图片URL格式：{image_url}（需要完整的HTTP/HTTPS URL或本地缓存路径）")
        
        # 下载图片
        response = requests.get(image_url, timeout=30)
        response.raise_for_status()
        
        cache_path = Path(IMAGE_CACHE_DIR) / f"{prompt_hash}.png"
        
        with open(cache_path, 'wb') as f:
            f.write(response.content)
        
        print(f"✅ 图片已缓存：{cache_path}")
        return str(cache_path)
    except Exception as e:
        print(f"❌ 图片缓存失败：{str(e)}")
        raise

def generate_image_with_cache(scene_description: str, style: str, global_state: Dict) -> Dict:
    """带缓存的图片生成"""
    # 生成缓存键
    prompt_hash = hashlib.md5(f"{scene_description}_{style}".encode()).hexdigest()
    
    # 检查缓存
    cached_path = get_cached_image(prompt_hash)
    if cached_path:
        print(f"✅ 使用缓存的图片：{prompt_hash}")
        return {
            "url": f"/image_cache/{prompt_hash}.png",
            "prompt": scene_description,
            "style": style,
            "width": 1024,
            "height": 1024,
            "cached": True
        }
    
    # 生成新图片
    image_data = generate_scene_image(scene_description, global_state, style)
    if not image_data or not image_data.get('url'):
        return None
    
    image_url = image_data['url']
    
    # 检查图片URL是否是本地缓存路径（说明已经在main2.py中缓存过了）
    if image_url.startswith('/image_cache/') or image_url.startswith('image_cache/'):
        # 已经是本地缓存路径，直接返回，不需要再次缓存
        print(f"✅ 图片已在main2.py中缓存，使用现有路径：{image_url}")
        return {
            "url": image_url,
            "prompt": scene_description,
            "style": style,
            "width": 1024,
            "height": 1024,
            "cached": True
        }
    
    # 缓存图片（只有当image_url是完整的HTTP/HTTPS URL时才需要下载）
    try:
        local_path = cache_image(prompt_hash, image_url)
        return {
            "url": f"/image_cache/{prompt_hash}.png",
            "prompt": scene_description,
            "style": style,
            "width": 1024,
            "height": 1024,
            "cached": False
        }
    except Exception as e:
        print(f"⚠️ 图片缓存失败，使用原始URL：{str(e)}")
        return image_data

# ------------------------------
# 视觉内容生成API接口
# ------------------------------

@app.route('/generate-scene-image', methods=['POST'])
def generate_scene_image_api():
    """单独生成场景图片的接口"""
    try:
        data = request.json
        scene_description = data.get('sceneDescription', '')
        global_state = data.get('globalState', {})
        style = data.get('style', 'default')
        viewport_width = data.get('viewportWidth', None)
        viewport_height = data.get('viewportHeight', None)
        
        if not scene_description:
            return jsonify({"status": "error", "message": "场景描述不能为空"})
        
        # 转换视口尺寸为整数（如果提供）
        if viewport_width is not None:
            try:
                viewport_width = int(viewport_width)
            except (ValueError, TypeError):
                viewport_width = None
        if viewport_height is not None:
            try:
                viewport_height = int(viewport_height)
            except (ValueError, TypeError):
                viewport_height = None
        
        image_data = generate_scene_image(
            scene_description, 
            global_state, 
            style,
            viewport_width=viewport_width,
            viewport_height=viewport_height
        )
        
        if image_data:
            return jsonify({
                "status": "success",
                "image": image_data
            })
        else:
            return jsonify({
                "status": "error",
                "message": "图片生成失败"
            })
    except Exception as e:
        print(f"🔴 生成场景图片错误：{str(e)}")
        import traceback
        traceback.print_exc()
        error_msg = clean_error_message(str(e))
        return jsonify({"status": "error", "message": f"生成场景图片失败：{error_msg}"})

# ==================== 视频生成API接口已禁用（性能优化） ====================
# @app.route('/generate-scene-video', methods=['POST'])
# def generate_scene_video_api():
#     """异步生成场景视频（5-10秒）"""
#     ... (已注释)

# @app.route('/video-status/<task_id>', methods=['GET'])
# def get_video_status_api(task_id):
#     """查询视频生成状态"""
#     ... (已注释)

# 提供占位接口，返回错误提示
@app.route('/generate-scene-video', methods=['POST'])
def generate_scene_video_api():
    """视频生成功能已禁用"""
    return jsonify({
        "status": "error",
        "message": "视频生成功能已禁用（性能优化）"
    })

@app.route('/video-status/<task_id>', methods=['GET'])
def get_video_status_api(task_id):
    """视频生成功能已禁用"""
    return jsonify({
        "status": "error",
        "message": "视频生成功能已禁用（性能优化）"
    }), 404

@app.route('/initial/main_character/<game_id>/<filename>')
def serve_main_character_image(game_id, filename):
    """提供主角形象图片"""
    try:
        # 安全检查：防止路径遍历攻击（game_id 与 filename 均禁止 .. / \）
        if ('..' in game_id or '..' in filename or '/' in game_id or '\\' in game_id or
                '/' in filename or '\\' in filename):
            return jsonify({"status": "error", "message": "Invalid path"}), 400
        
        image_path = os.path.join("initial", "main_character", game_id, filename)
        
        if not os.path.exists(image_path):
            return jsonify({"status": "error", "message": "Image not found"}), 404
        
        return send_file(image_path)
    except Exception as e:
        print(f"❌ 提供主角形象图片错误：{str(e)}")
        return jsonify({"status": "error", "message": f"Failed to serve image: {str(e)}"}), 500

@app.route('/image_cache/<filename>')
def serve_cached_image(filename):
    """提供缓存的图片文件"""
    try:
        cache_path = Path(IMAGE_CACHE_DIR) / filename
        if cache_path.exists() and cache_path.suffix.lower() in ['.png', '.jpg', '.jpeg']:
            return send_file(cache_path, mimetype='image/png')
        return jsonify({"status": "error", "message": "图片不存在"}), 404
    except Exception as e:
        print(f"🔴 提供缓存图片错误：{str(e)}")
        return jsonify({"status": "error", "message": "无法提供图片"}), 500

# 前端静态文件路由
@app.route('/')
def index():
    """返回前端首页"""
    return send_from_directory('game-frontend', 'index.html')

@app.route('/<path:filename>')
def frontend_files(filename):
    """提供前端静态文件（JS、CSS等）"""
    # 排除API路由和图片缓存路由
    if filename.startswith('api/') or filename.startswith('image_cache/'):
        return jsonify({"status": "error", "message": "路径不存在"}), 404
    try:
        return send_from_directory('game-frontend', filename)
    except:
        return jsonify({"status": "error", "message": "文件不存在"}), 404

# 启动服务
if __name__ == "__main__":
    print("=== 文本冒险游戏API服务器 ===")
    print("前端访问地址：http://127.0.0.1:5001")
    print("API端点：")
    print("  POST /generate-worldview - 生成游戏世界观")
    print("  POST /generate-option - 生成单个选项对应的剧情（支持缓存）")
    print("  POST /pregenerate-next-layers - 预生成两层内容")
    print("  POST /get-pregenerated-layer2 - 获取预生成的第二层内容")
    print("  POST /generate-ending - 生成游戏结局")
    print("  POST /save-game - 保存游戏")
    print("  GET /list-saves - 列出所有存档")
    print("  POST /load-game - 加载游戏")
    print("  POST /delete-save - 删除存档")
    print("  POST /generate-scene-image - 生成场景图片")
    # print("  POST /generate-scene-video - 生成场景视频（5-10秒）")  # 已禁用
    # print("  GET /video-status/<task_id> - 查询视频生成状态")  # 已禁用
    print("  GET /image_cache/<filename> - 获取缓存的图片")
    print("===============================")
    app.run(host='0.0.0.0', port=5001, debug=True)