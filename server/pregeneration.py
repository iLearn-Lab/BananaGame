# -*- coding: utf-8 -*-
"""预生成两层内容的核心逻辑。"""
import os
import threading
import hashlib
from concurrent.futures import ThreadPoolExecutor

from server.cache import (
    pregeneration_cache,
    cache_lock,
    get_cache_lock_holder,
    get_cache_lock_acquire_time,
)
from server.utils import generate_scene_id
from main2 import (
    _generate_single_option_text_only,
    generate_all_options,
    generate_scene_image,
)


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
            
            # 流水线：某分支 layer2 文本写完后立即跑该分支 layer2 图片（先文本后图）
            def run_layer2_for_branch(opt_idx, layer1_option_data):
                import time
                next_options = layer1_option_data.get('next_options', [])
                if not next_options:
                    return
                with cache_lock:
                    if scene_id not in pregeneration_cache:
                        return
                    cache_entry = pregeneration_cache[scene_id]
                    if cache_entry.get('layer2_cancel', False):
                        return
                    selected = cache_entry.get('layer2_selected_option')
                    if selected is not None and selected != opt_idx:
                        return
                updated_global_state = global_state.copy()
                if 'flow_worldline' not in updated_global_state:
                    updated_global_state['flow_worldline'] = {}
                flow_update = layer1_option_data.get('flow_update', {})
                if flow_update:
                    updated_global_state['flow_worldline'].update(flow_update)
                next_scene_id = generate_scene_id(str(updated_global_state), str(next_options))
                try:
                    layer2_data = generate_all_options(updated_global_state, next_options, skip_images=True)
                    if not layer2_data:
                        return
                    with cache_lock:
                        if scene_id not in pregeneration_cache:
                            return
                        cache_entry = pregeneration_cache[scene_id]
                        if cache_entry.get('layer2_cancel', False):
                            return
                        if next_scene_id not in pregeneration_cache:
                            pregeneration_cache[next_scene_id] = {
                                'layer1': {}, 'layer2': {}, 'generation_status': {}, 'generation_events': {},
                                'should_cancel': False, 'current_generating_index': None, 'layer2_generating': False,
                                'layer2_cancel': False, 'layer2_selected_option': None, 'layer2_thread': None,
                                'current_layer2_option': None, 'text_only_mode': True
                            }
                        next_cache_entry = pregeneration_cache[next_scene_id]
                        for next_opt_idx, next_option_data in layer2_data.items():
                            if 'layer1' not in next_cache_entry:
                                next_cache_entry['layer1'] = {}
                            next_cache_entry['layer1'][next_opt_idx] = next_option_data
                            next_cache_entry['generation_status'][next_opt_idx] = 'text_only'
                            events = next_cache_entry.setdefault('generation_events', {})
                            if next_opt_idx not in events:
                                events[next_opt_idx] = threading.Event()
                            events[next_opt_idx].set()
                        if 'layer2' not in cache_entry:
                            cache_entry['layer2'] = {}
                        cache_entry['layer2'][opt_idx] = layer2_data
                        print(f"✅ 选项 {opt_idx} 的 layer2 文本已写入下一层场景 {next_scene_id}，共 {len(layer2_data)} 条")
                    # 该分支 layer2 文本完成后，立即生成该分支 layer2 图片（图片对应剧情文本）
                    for next_opt_idx, next_option_data in layer2_data.items():
                        scene_for_image = (next_option_data.get('scene') or '').strip() or None
                        if not scene_for_image:
                            continue
                        with cache_lock:
                            ce = pregeneration_cache.get(scene_id)
                            if ce and ce.get('layer2_cancel', False):
                                return
                        if isinstance(updated_global_state, dict):
                            updated_global_state["_plot_supporting_characters"] = next_option_data.get("plot_supporting_characters", [])
                        try:
                            img = generate_scene_image(
                                scene_for_image, updated_global_state, "default", use_cache=True,
                                cache_key_suffix=f"{next_scene_id}_opt{next_opt_idx}"
                            )
                            if img and isinstance(img, dict) and img.get('url'):
                                scene_text_hash = hashlib.md5(scene_for_image.encode('utf-8')).hexdigest()
                                img_data = {
                                    "url": img.get("url"), "prompt": img.get("prompt", ""), "style": img.get("style", "default"),
                                    "width": img.get("width", 1024), "height": img.get("height", 1024),
                                    "cached": img.get("cached", True), "scene_text_hash": scene_text_hash,
                                }
                                with cache_lock:
                                    if next_scene_id in pregeneration_cache:
                                        ne = pregeneration_cache[next_scene_id]
                                        if next_opt_idx in ne.get('layer1', {}):
                                            ne['layer1'][next_opt_idx]['scene_image'] = img_data
                                            ne['generation_status'][next_opt_idx] = 'completed'
                                            ev = ne.get('generation_events', {}).get(next_opt_idx)
                                            if ev:
                                                ev.set()
                                print(f"✅ 下一层场景 {next_scene_id} 选项 {next_opt_idx} 图片已预生成")
                        except Exception as e:
                            print(f"⚠️ 下一层场景 {next_scene_id} 选项 {next_opt_idx} 图片生成异常: {e}")
                except Exception as e:
                    print(f"❌ 选项 {opt_idx} 的 layer2 预生成失败: {e}")
                    import traceback
                    traceback.print_exc()
            
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
                    
                    # 流水线：本层该选项文本一写完，立即启动该分支的 layer2（先文本后图）
                    if option_data and option_data.get('next_options'):
                        threading.Thread(
                            target=run_layer2_for_branch,
                            args=(opt_idx, option_data.copy()),
                            daemon=True
                        ).start()
                    
                    # 为当前场景生成图片（限速由 yunwu 全局限速锁 + IMAGE_SUBMIT_DELAY 控制）
                    # 图片生成完成后更新缓存
                    if scene_for_image and option_data:
                        try:
                            print(f"🎨 [第一层预生成] 开始为选项 {opt_idx + 1} 生成图片...")
                            # 传入剧情模型输出的本段出场配角（有则名单，无则[]），图片流程以剧情为准不推断
                            if isinstance(global_state, dict):
                                global_state["_plot_supporting_characters"] = option_data.get("plot_supporting_characters", [])
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
                                                    _holder = get_cache_lock_holder()
                                                    if _holder:
                                                        holder_name = _holder.name if hasattr(_holder, 'name') else str(_holder)
                                                        holder_time = time.time() - get_cache_lock_acquire_time() if get_cache_lock_acquire_time() else 0
                                                        print(f"⏳ [第一层预生成] 等待获取缓存锁...（已等待 {elapsed:.1f}秒，最多等待 {max_lock_wait}秒，线程：{current_thread_name}）")
                                                        print(f"   🔍 锁被线程持有：{holder_name}，已持有 {holder_time:.1f}秒")
                                                        if holder_time > 5:
                                                            print(f"   ⚠️ 警告：锁被持有超过5秒，可能存在死锁或耗时操作！")
                                                    # 🔧 关键：打印持锁线程的“实时堆栈”，定位卡在哪一行
                                                    stack = cache_lock.dump_holder_stack()
                                                    if stack:
                                                        _hn = holder_name if '_holder' in dir() and _holder else (get_cache_lock_holder().name if get_cache_lock_holder() and hasattr(get_cache_lock_holder(), 'name') else 'unknown')
                                                        print(f"   🧵 持锁线程实时堆栈（{_hn}）:\n{stack}")
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
                                status = cache_entry.get('generation_status', {}).get(opt_idx, 'unknown')
                                if opt_idx in cache_entry.get('layer1', {}):
                                    print(f"   ✅ 选项 {opt_idx} 的数据已在缓存中")
                                elif status == 'cancelled':
                                    print(f"   ℹ️ 选项 {opt_idx} 已被取消（用户已选择其他选项），数据已清理，属正常情况")
                                else:
                                    print(f"   ⚠️ 选项 {opt_idx} 的数据不在缓存中！")
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
                                layer2_data = generate_all_options(updated_global_state, next_options, skip_images=False)
                                
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
                                    layer2_data = generate_all_options(updated_global_state, next_options, skip_images=False)
                                    
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
            
            # 第二层已由 run_layer2_for_branch 在「第一层某选项文本写完」时按分支流水线触发，此处不再启动统一 layer2 线程
                
        except Exception as e:
            print(f"❌ 预生成过程中发生错误：{str(e)}")
            import traceback
            traceback.print_exc()
    
    # 启动后台线程执行预生成
    thread = threading.Thread(target=async_pregenerate, daemon=True)
    thread.start()
    
    return scene_id

