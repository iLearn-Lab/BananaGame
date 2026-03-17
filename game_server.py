# -*- coding: utf-8 -*-
import os
import sys
import json
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import datetime
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
    get_video_task_status,
    generate_game_id,
    generate_main_character_image,
)

from server.config import SAVE_DIR, IMAGE_CACHE_DIR, ensure_dirs
from server.cache import (
    pregeneration_cache,
    cache_lock,
    cleanup_old_cache,
    cleanup_used_options,
)
from server.utils import clean_error_message, generate_scene_id
from server.pregeneration import _pregenerate_next_layers_logic
from server.config import IMAGE_CACHE_DIR
from src.characters.supporting import (
    get_or_create_supporting_role_archive,
    archive_supporting_role_first_appearance,
)
from src.characters.archives import _load_role_archives
from src.utils.text_utils import _clip_text, get_protagonist_names

# 初始化 Flask 应用
app = Flask(__name__)
load_dotenv()
ensure_dirs()


def _archive_supporting_roles_on_option_shown(game_id, option_data, global_state, protagonist_names=None):
    """
    仅当选项即将展示到前端时，为本段首次出场的配角做初登场图建档。
    避免未选中选项中的角色也被建档。
    若 display_name 与主角姓名/别名匹配，则跳过建档，避免将主角误建档为配角。
    """
    if not game_id or not option_data or not isinstance(option_data, dict):
        return
    plot_supporting_characters = option_data.get("plot_supporting_characters") or []
    if not plot_supporting_characters:
        return
    scene_image = option_data.get("scene_image") or {}
    scene_url = (scene_image.get("url") or "").strip()
    prompt = (scene_image.get("prompt") or "").strip()
    if not scene_url or not prompt:
        return
    # 仅支持本地缓存路径（/image_cache/xxx.png 或 image_cache/xxx.png）
    if scene_url.startswith("/image_cache/"):
        name = Path(scene_url).name
    elif scene_url.startswith("image_cache/"):
        name = Path(scene_url).name
    else:
        return
    local_path = Path(IMAGE_CACHE_DIR) / name
    if not local_path.exists():
        return
    # 主角称呼集合：用于排除主角（如「拍短片的」可能是主角别称）
    if protagonist_names is None and global_state:
        protagonist_names = get_protagonist_names(global_state)
    protagonist_names = protagonist_names or set()
    if isinstance(protagonist_names, (list, tuple)):
        protagonist_names = set(str(x).strip() for x in protagonist_names if x)
    chars = (global_state or {}).get("supporting_role_archives") or {}
    if not isinstance(chars, dict) or not chars:
        chars = _load_role_archives(game_id)
    first_appear_scene = _clip_text(option_data.get("scene", ""), 60)
    for display_name, slot in plot_supporting_characters:
        dn = str(display_name).strip()
        if protagonist_names and dn in protagonist_names:
            print(f"⏭️ 跳过建档：{dn} 为主角称呼，不建配角档案")
            continue
        role_info = chars.get(slot, {}) or chars.get(display_name, {}) or {}
        if not isinstance(role_info, dict):
            role_info = {}
        arch = get_or_create_supporting_role_archive(
            game_id, dn, str(slot).strip(), role_info, first_appear_scene
        )
        if arch.get("_pending_first_appearance"):
            try:
                archive_supporting_role_first_appearance(game_id, arch, str(local_path), prompt)
            except Exception as e:
                print(f"⚠️ 配角初登场建档失败：{e}")


# 允许前端跨域访问
@app.after_request
def after_request(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'POST, GET, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response


def _normalize_checkpoint_memory(raw_value):
    if not isinstance(raw_value, list):
        return []
    normalized = []
    for item in raw_value[-30:]:
        if not isinstance(item, dict):
            continue
        normalized.append({
            "id": item.get("id") or f"cp_{int(datetime.now().timestamp() * 1000)}",
            "source": item.get("source", "unknown"),
            "chapter": item.get("chapter", ""),
            "recap": item.get("recap", ""),
            "keywords": item.get("keywords") if isinstance(item.get("keywords"), list) else [],
            "selectedOption": item.get("selectedOption", ""),
            "timestamp": item.get("timestamp") or datetime.now().isoformat(),
        })
    return normalized


def _build_checkpoint_packet(option_data, global_state, selected_option):
    flow = (global_state or {}).get("flow_worldline", {}) if isinstance(global_state, dict) else {}
    flow_update = (option_data or {}).get("flow_update", {}) if isinstance(option_data, dict) else {}
    chapter = flow.get("current_chapter", "chapter1")
    quest_progress = flow_update.get("quest_progress") or flow.get("quest_progress") or "主线正在推进。"
    scene = ((option_data or {}).get("scene") or "").strip()
    recap_scene = scene if scene else "剧情继续推进中。"
    recap_text = f"你选择了“{selected_option}”。当前章节：{chapter}。主线进展：{quest_progress}。最近剧情：{recap_scene}"
    return {
        "chapter": chapter,
        "quest_delta": flow_update.get("quest_progress", ""),
        "chapter_conflict_solved": bool(flow_update.get("chapter_conflict_solved", False)),
        "recap_text": recap_text
    }

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
                        if not initial_scene_image.get("image_type"):
                            initial_scene_image = dict(initial_scene_image)
                            initial_scene_image["image_type"] = "story_scene"
                        if not initial_scene_image.get('scene_text_hash') and initial_scene and initial_scene.strip():
                            initial_scene_image = dict(initial_scene_image)
                            initial_scene_image['scene_text_hash'] = hashlib.md5(initial_scene.encode('utf-8')).hexdigest()
                        print(f"✅ 初始场景图片数据已提取: {initial_scene_image.get('url', 'N/A')[:80]}...")
                    else:
                        print(f"⚠️ 初始场景没有图片数据（因使用默认剧情，AI 未返回【场景】格式）")
                        # 默认剧情时补生成一张场景图，保证首次进入也有图
                        if initial_scene and initial_scene.strip():
                            try:
                                img = generate_scene_image(initial_scene, global_state, "default", use_cache=True)
                                if img and img.get("url"):
                                    initial_scene_image = dict(img)
                                    initial_scene_image["image_type"] = "story_scene"
                                    initial_scene_image["scene_text_hash"] = hashlib.md5(initial_scene.encode("utf-8")).hexdigest()
                                    print(f"✅ 已为默认剧情补生成初始场景图: {initial_scene_image.get('url', '')[:80]}...")
                            except Exception as img_e:
                                print(f"⚠️ 默认剧情补生成场景图失败，继续无图: {img_e}")
                    initial_cache['initial_scene'] = initial_scene
                    initial_cache['initial_scene_image'] = initial_scene_image  # 保存图片数据
                    initial_cache['initial_options'] = initial_options
                    # 保存本段出场配角，供展示初始场景时建档用（与后续选项一致）
                    initial_cache['plot_supporting_characters'] = initial_option_data.get('plot_supporting_characters', [])
                    # 选项剧情未预生成，状态保持 pending（如后续需要可由预生成写入 scene_id 对应缓存）
                    initial_cache['generation_status'] = {i: 'pending' for i in range(len(initial_options))}
                    initial_cache['completed'] = True
                    
                    # 首屏预生成：生成 scene_id 并写入缓存，供前端与预生成共用
                    server_scene_id = generate_scene_id(str(global_state), str(initial_options))
                    initial_cache['pregeneration_scene_id'] = server_scene_id
                    
                    # 触发等待事件（如果有线程在等待）
                    events = initial_cache.get('generation_events', {})
                    if 'main' in events:
                        events['main'].set()

                # 首屏预生成：立即在后台启动首屏选项的预生成（与后续轮同一套逻辑）
                _pregenerate_next_layers_logic(global_state, initial_options, server_scene_id)

                print(f"✅ 第一次选项生成完成（含首屏预生成），选项数：{len(initial_options)}，预生成 scene_id：{server_scene_id}")
                
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
                                "deep_background_links": {},
                                "plot_supporting_characters": initial_cache.get("plot_supporting_characters", []),
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
                    # 补图时传入剧情模型输出的本段出场配角（有则名单，无则[]），图片流程以剧情为准不推断
                    if isinstance(global_state, dict) and option_data is not None:
                        global_state["_plot_supporting_characters"] = option_data.get("plot_supporting_characters", [])
                    # 补图时不查缓存复用旧图，但仍保存到本地（skip_cache_lookup=True）
                    img = generate_scene_image(
                        scene_text, global_state, "default",
                        use_cache=True,
                        skip_cache_lookup=True,
                        cache_key_suffix=f"{scene_id or 'initial'}_opt{option_index}"
                    )
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
                            "image_type": "story_scene",
                            "scene_text_hash": scene_text_hash  # 存储场景文本哈希，用于匹配检查
                        }
                        print("✅ 已生成场景图片（确保图片和文本匹配）")
                    else:
                        print("⚠️ 场景图片生成失败，但继续返回文本")
        except Exception as e:
            print(f"⚠️ 生成场景图片失败，继续返回文本：{str(e)}")
            import traceback
            traceback.print_exc()
        
        # 建档改为前端展示剧情图后由前端调用 /notify-scene-displayed 触发，此处不再建档
        # 与首屏一致：后端在返回时带 sceneId，且由后端触发下一轮预生成（首屏在 generate_initial_options 已触发，此处处理非首屏）
        response = {
            "status": "success",
            "message": "选项剧情生成成功！",
            "optionData": option_data
        }
        if isinstance(option_data, dict):
            option_data["checkpoint_packet"] = _build_checkpoint_packet(option_data, global_state, option)
        if isinstance(option_data, dict):
            if not scene_id or scene_id == 'initial':
                # 首屏：从 initial 缓存取已写入的 pregeneration_scene_id（预生成已在 generate_initial_options 中触发）
                with cache_lock:
                    if 'initial' in pregeneration_cache:
                        sid = pregeneration_cache['initial'].get('pregeneration_scene_id')
                        if sid:
                            response["optionData"] = dict(option_data)
                            response["optionData"]["sceneId"] = sid
            else:
                # 非首屏：与首屏一样由后端在返回时触发下一轮预生成，并带上新 sceneId
                next_options = option_data.get('next_options', [])
                if next_options:
                    updated_global_state = global_state.copy()
                    if 'flow_worldline' not in updated_global_state:
                        updated_global_state['flow_worldline'] = {}
                    flow_update = option_data.get('flow_update') or {}
                    if flow_update:
                        updated_global_state['flow_worldline'].update(flow_update)
                    new_scene_id = generate_scene_id(str(updated_global_state), str(next_options))
                    _pregenerate_next_layers_logic(updated_global_state, next_options, new_scene_id)
                    response["optionData"] = dict(option_data)
                    response["optionData"]["sceneId"] = new_scene_id
                    print(f"✅ [generate-option] 已触发下一轮预生成，sceneId={new_scene_id}，选项数={len(next_options)}")
        return jsonify(response)
    except Exception as e:
        print(f"🔴 服务器错误：{str(e)}")
        import traceback
        traceback.print_exc()
        error_msg = clean_error_message(str(e))
        return jsonify({"status": "error", "message": f"选项剧情生成失败：{error_msg}"})

# 预生成两层内容的核心逻辑（委托 server.pregeneration，流水线版）
def _pregenerate_next_layers_logic(global_state, current_options, scene_id=None):
    """委托 server.pregeneration（流水线版：本层文本完→layer2 文本；layer2 文本完→layer2 图片）。"""
    from server.pregeneration import _pregenerate_next_layers_logic as _impl
    return _impl(global_state, current_options, scene_id)

# 新增接口：预生成两层内容（优先级策略 + 渐进式缓存）
@app.route('/pregenerate-next-layers', methods=['POST'])
def pregenerate_next_layers():
    """
    预生成两层内容（按优先级顺序渐进式生成）：
    - 第一层：按优先级顺序（0→1→2→3）逐个生成，生成一个立即写入缓存
    - 第二层：第一层完成后，继续在后台生成第二层
    """
    try:
        data = request.json
        global_state = data.get('globalState', {})
        current_options = data.get('currentOptions', [])
        scene_id = data.get('sceneId', None)
        current_scene_image = data.get('currentSceneImage', None)
        current_scene_text = data.get('currentSceneText', '')
        if isinstance(global_state, dict) and (current_scene_image or current_scene_text):
            global_state['_visual_context'] = {
                "sceneId": scene_id,
                "currentSceneImage": current_scene_image,
                "currentSceneText": current_scene_text
            }
            if scene_id:
                with cache_lock:
                    if scene_id in pregeneration_cache:
                        pregeneration_cache[scene_id]['visual_context'] = global_state['_visual_context']
        if not global_state:
            return jsonify({"status": "error", "message": "全局状态不能为空！"})
        if not current_options:
            return jsonify({"status": "error", "message": "当前选项列表不能为空！"})
        print(f"🔍 [pregenerate-next-layers] 预生成参数：sceneId={scene_id}, 选项数={len(current_options)}")
        scene_id = _pregenerate_next_layers_logic(global_state, current_options, scene_id)
        return jsonify({"status": "success", "message": "预生成任务已启动！", "sceneId": scene_id})
    except Exception as e:
        print(f"🔴 预生成接口错误：{str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": f"预生成任务启动失败：{clean_error_message(str(e))}"})

@app.route('/get-pregenerated-layer2', methods=['POST'])
def get_pregenerated_layer2():
    """获取预生成的第二层内容"""
    try:
        data = request.json or {}
        scene_id = data.get('sceneId', None)
        layer1_option_index = data.get('layer1OptionIndex', None)
        layer2_option_index = data.get('layer2OptionIndex', None)
        global_state = data.get('globalState', {})
        with cache_lock:
            if not scene_id or scene_id not in pregeneration_cache:
                return jsonify({"status": "error", "message": "未找到预生成的第二层内容！"})
            cache_entry = pregeneration_cache[scene_id]
            layer2_data = cache_entry.get('layer2', {}).get(layer1_option_index) if layer1_option_index is not None else None
            if layer2_data and layer2_option_index is not None:
                option_data = layer2_data.get(layer2_option_index)
                if option_data:
                    return jsonify({"status": "success", "optionData": option_data})
        return jsonify({"status": "error", "message": "未找到预生成的第二层内容！"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"获取失败：{clean_error_message(str(e))}"})

@app.route('/save-game', methods=['POST'])
def save_game():
    try:
        data = request.json or {}
        save_name = (data.get('saveName') or '').strip()
        if not save_name:
            return jsonify({"status": "error", "message": "存档名称不能为空！"})
        game_data = data.get('gameData') or data.get('globalState') or {}
        global_state = data.get('globalState') or data.get('gameData') or {}
        checkpoint_memory = _normalize_checkpoint_memory(
            data.get("checkpointMemory")
            or (game_data.get("flow_worldline", {}) if isinstance(game_data, dict) else {}).get("checkpoint_memory")
            or (global_state.get("flow_worldline", {}) if isinstance(global_state, dict) else {}).get("checkpoint_memory")
        )

        if isinstance(game_data, dict):
            game_data.setdefault("flow_worldline", {})
            game_data["flow_worldline"]["checkpoint_memory"] = checkpoint_memory
            game_data["checkpoint_memory"] = checkpoint_memory
        if isinstance(global_state, dict):
            global_state.setdefault("flow_worldline", {})
            global_state["flow_worldline"]["checkpoint_memory"] = checkpoint_memory
            global_state["checkpoint_memory"] = checkpoint_memory

        save_payload = {
            "schemaVersion": 2,
            "gameData": game_data,
            "globalState": global_state,
            "meta": {
                "protagonistAttr": data.get("protagonistAttr", {}),
                "difficulty": data.get("difficulty", ""),
                "lastOptions": data.get("lastOptions", []),
                "checkpointMemory": checkpoint_memory,
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M")
            }
        }

        # 兼容旧前端读取字段
        save_payload["saveData"] = {
            "global_state": global_state,
            "protagonist_attr": data.get("protagonistAttr", {}),
            "difficulty": data.get("difficulty", ""),
            "last_options": data.get("lastOptions", []),
            "checkpoint_memory": checkpoint_memory,
            "timestamp": save_payload["meta"]["timestamp"]
        }
        from server.config import SAVE_DIR
        import json as _json
        path = Path(SAVE_DIR) / f"{save_name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            _json.dump(save_payload, f, ensure_ascii=False, indent=2)
        return jsonify({"status": "success", "message": "保存成功"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"保存失败，请重试：{clean_error_message(str(e))}"})

@app.route('/list-saves', methods=['GET'])
def list_saves():
    try:
        from server.config import SAVE_DIR
        saves = []
        if Path(SAVE_DIR).exists():
            for f in Path(SAVE_DIR).glob("*.json"):
                saves.append(f.stem)
        return jsonify({"status": "success", "saves": saves})
    except Exception as e:
        return jsonify({"status": "error", "message": f"列出存档失败：{clean_error_message(str(e))}", "saves": []})

@app.route('/load-game', methods=['POST'])
def load_game():
    try:
        data = request.json or {}
        save_name = (data.get('saveName') or '').strip()
        if not save_name:
            return jsonify({"status": "error", "message": "存档名称不能为空！"})
        from server.config import SAVE_DIR
        import json as _json
        path = Path(SAVE_DIR) / f"{save_name}.json"
        if not path.exists():
            return jsonify({"status": "error", "message": "存档文件不存在"})
        with open(path, 'r', encoding='utf-8') as f:
            loaded = _json.load(f)
        game_data = loaded.get("gameData") or loaded.get("globalState") or {}
        global_state = loaded.get("globalState") or loaded.get("gameData") or {}
        meta = loaded.get("meta") or {}
        save_data = loaded.get("saveData") or {}

        # 兼容老存档（没有 meta/saveData）
        if not save_data:
            checkpoint_memory = _normalize_checkpoint_memory(
                (global_state.get("flow_worldline", {}) if isinstance(global_state, dict) else {}).get("checkpoint_memory")
                or (game_data.get("flow_worldline", {}) if isinstance(game_data, dict) else {}).get("checkpoint_memory")
            )
            save_data = {
                "global_state": global_state,
                "protagonist_attr": meta.get("protagonistAttr", {}),
                "difficulty": meta.get("difficulty", ""),
                "last_options": meta.get("lastOptions", []),
                "checkpoint_memory": checkpoint_memory,
                "timestamp": meta.get("timestamp", "")
            }
        return jsonify({
            "status": "success",
            "gameData": game_data,
            "globalState": global_state,
            "meta": {
                "protagonistAttr": meta.get("protagonistAttr", {}),
                "difficulty": meta.get("difficulty", ""),
                "lastOptions": meta.get("lastOptions", []),
                "checkpointMemory": save_data.get("checkpoint_memory", meta.get("checkpointMemory", [])),
                "timestamp": save_data.get("timestamp", meta.get("timestamp", ""))
            },
            "saveData": save_data
        })
    except Exception as e:
        return jsonify({"status": "error", "message": f"加载失败，请重试：{clean_error_message(str(e))}"})

@app.route('/delete-save', methods=['POST'])
def delete_save():
    try:
        data = request.json or {}
        save_name = (data.get('saveName') or '').strip()
        if not save_name:
            return jsonify({"status": "error", "message": "存档名称不能为空！"})
        from server.config import SAVE_DIR
        path = Path(SAVE_DIR) / f"{save_name}.json"
        if not path.exists():
            return jsonify({"status": "error", "message": "存档文件不存在"})
        path.unlink()
        return jsonify({"status": "success", "message": "删除成功"})
    except Exception as e:
        return jsonify({"status": "error", "message": f"删除存档失败：{clean_error_message(str(e))}"})

@app.route('/generate-ending', methods=['POST'])
def generate_ending():
    try:
        data = request.json or {}
        global_state = data.get('globalState', {})
        if not global_state:
            return jsonify({"status": "error", "message": "全局状态不能为空！"})
        ending_content = generate_ending_prediction(global_state)
        return jsonify({"status": "success", "endingContent": ending_content or ""})
    except Exception as e:
        return jsonify({"status": "error", "message": f"生成游戏结局失败：{clean_error_message(str(e))}"})

@app.route('/notify-scene-displayed', methods=['POST'])
def notify_scene_displayed():
    try:
        data = request.json or {}
        game_id = (data.get("game_id") or "").strip()
        option_data = data.get("option_data")
        if not game_id:
            return jsonify({"status": "error", "message": "game_id 不能为空"})
        if not option_data or not isinstance(option_data, dict):
            return jsonify({"status": "error", "message": "option_data 不能为空"})
        _archive_supporting_roles_on_option_shown(game_id, option_data, None, data.get("protagonist_names"))
        return jsonify({"status": "success", "message": "已处理"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

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
            resp = {"status": "success", "image": image_data}
            # 将本次剧情图提示词 JSON 一并返回，便于后端/前端查看
            if isinstance(global_state, dict) and "_last_scene_prompt_json" in global_state:
                resp["prompt_json"] = global_state["_last_scene_prompt_json"]
            return jsonify(resp)
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
    print("请在浏览器地址栏输入（务必包含 http://）：")
    print("  http://127.0.0.1:5001")
    print("不要只输入 127.0.0.1:5001，否则会被当成搜索，无法打开游戏页面。")
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
