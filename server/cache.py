# -*- coding: utf-8 -*-
"""预生成缓存、带追踪的锁、缓存清理。"""
import threading

from server.config import MAX_CACHE_SIZE

# 全局缓存：存储预生成的两层内容
# 结构：{scene_id: {
#   'layer1': {option_index: option_data},
#   'layer2': {option_index: {option_index: option_data}},
#   'generation_status': {option_index: 'pending'|'generating'|'completed'},
#   'generation_events': {option_index: threading.Event()},
#   'should_cancel': False,
#   'current_generating_index': None,
#   'layer2_generating': False,
#   'layer2_cancel': False,
#   'layer2_selected_option': None,
#   'layer2_thread': None
# }}
pregeneration_cache = {}
_cache_lock_holder = None
_cache_lock_acquire_time = None


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
        """返回当前持锁线程的“实时堆栈”。"""
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


def cleanup_old_cache(current_scene_id=None):
    """清理旧的缓存，保留最近使用的场景。"""
    with cache_lock:
        cache_size = len(pregeneration_cache)
        if cache_size <= MAX_CACHE_SIZE:
            return

        scenes_to_keep = set()
        if current_scene_id:
            scenes_to_keep.add(current_scene_id)
        if 'initial' in pregeneration_cache:
            scenes_to_keep.add('initial')

        to_remove = cache_size - MAX_CACHE_SIZE
        scenes_to_remove = []
        for scene_id in pregeneration_cache:
            if scene_id not in scenes_to_keep:
                scenes_to_remove.append(scene_id)

        if len(scenes_to_remove) > to_remove:
            scenes_to_remove = scenes_to_remove[:to_remove]

        for scene_id in scenes_to_remove:
            cache_entry = pregeneration_cache.get(scene_id)
            if cache_entry:
                if cache_entry.get('layer2_generating', False):
                    cache_entry['layer2_cancel'] = True
                    layer2_thread = cache_entry.get('layer2_thread')
                    if layer2_thread and layer2_thread.is_alive():
                        layer2_thread.join(timeout=0.5)

            del pregeneration_cache[scene_id]
            print(f"🗑️ 已清理旧缓存场景 {scene_id}（内存优化）")

        print(f"📊 当前缓存大小：{len(pregeneration_cache)}/{MAX_CACHE_SIZE}")


def cleanup_used_options(scene_id, used_option_index):
    """清理已使用的选项数据，释放内存。"""
    with cache_lock:
        if scene_id not in pregeneration_cache:
            return

        cache_entry = pregeneration_cache[scene_id]
        if 'layer1' in cache_entry:
            layer1 = cache_entry['layer1']
            if used_option_index in layer1:
                if 'layer2' in cache_entry and used_option_index in cache_entry['layer2']:
                    pass  # 可在此进一步清理第二层未使用项


# 调试用：持锁线程与时间（供 pregeneration 等模块打印堆栈）
def get_cache_lock_holder():
    """返回当前持有 cache_lock 的线程（用于调试）。"""
    return _cache_lock_holder


def get_cache_lock_acquire_time():
    """返回 cache_lock 获取时间（用于调试）。"""
    return _cache_lock_acquire_time
