# -*- coding: utf-8 -*-
"""Extract _pregenerate_next_layers_logic to server/pregeneration.py and fix refs."""
import os

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

with open('game_server.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# Lines 1094-1804 (1-based) -> indices 1093-1804
body_lines = lines[1093:1804]
body = ''.join(body_lines)

# Replace refs for server module
body = body.replace(
    'if _cache_lock_holder:\n                                                        holder_name = _cache_lock_holder.name if hasattr(_cache_lock_holder, \'name\') else str(_cache_lock_holder)\n                                                        holder_time = time.time() - _cache_lock_acquire_time if _cache_lock_acquire_time else 0',
    '_holder = get_cache_lock_holder()\n                                                    if _holder:\n                                                        holder_name = _holder.name if hasattr(_holder, \'name\') else str(_holder)\n                                                        holder_time = time.time() - get_cache_lock_acquire_time() if get_cache_lock_acquire_time() else 0')
body = body.replace(
    'print(f"   🧵 持锁线程实时堆栈（{holder_name}）:\\n{stack}")',
    '_hn = holder_name if \'_holder\' in dir() and _holder else (get_cache_lock_holder().name if get_cache_lock_holder() and hasattr(get_cache_lock_holder(), \'name\') else \'unknown\')\n                                                        print(f"   🧵 持锁线程实时堆栈（{_hn}）:\\n{stack}")')
# Simpler: just use a variable set in same block
body = body.replace(
    'if stack:\n                                                        print(f"   🧵 持锁线程实时堆栈（{holder_name}）:\\n{stack}")',
    'if stack:\n                                                        _hn = (holder_name if _holder else "unknown") if \'holder_name\' in dir() else "unknown"\n                                                        print(f"   🧵 持锁线程实时堆栈（{_hn}）:\\n{stack}")')
# Actually we already have "if _holder:" block setting holder_name, so after that block we have holder_name only when _holder. So the "if stack" branch: if we're in the same "if int(elapsed*2)%2==0" we might not have entered "if _holder". So holder_name could be undefined. Safest: before "stack = cache_lock..." set holder_name = get_cache_lock_holder() and then holder_name = holder_name.name if holder_name... So we need to set a default. Let me use: before "stack = ..." add "holder_name = holder_name if \'holder_name\' in dir() else (\'unknown\')" - no, that's wrong. Simpler: in the block that prints the stack, use: _h = get_cache_lock_holder(); _hn = _h.name if _h and hasattr(_h,\'name\') else (str(_h) if _h else \'unknown\')
body = body.replace(
    'if stack:\n                                                        print(f"   🧵 持锁线程实时堆栈（{holder_name}）:\\n{stack}")',
    'if stack:\n                                                        _h = get_cache_lock_holder(); _hn = _h.name if _h and hasattr(_h, \'name\') else (str(_h) if _h else \'unknown\')\n                                                        print(f"   🧵 持锁线程实时堆栈（{_hn}）:\\n{stack}")')

# First replacement: fix the if _cache_lock_holder block
body = body.replace('_cache_lock_holder', '_holder_orig')
body = body.replace('_cache_lock_acquire_time', 'get_cache_lock_acquire_time()')
body = body.replace('if _holder_orig:', '_holder = get_cache_lock_holder()\n                                                    if _holder:')
body = body.replace('holder_name = _holder_orig.name if hasattr(_holder_orig, \'name\') else str(_holder_orig)', 'holder_name = _holder.name if hasattr(_holder, \'name\') else str(_holder)')
body = body.replace('_holder_orig', '_holder')

out = '''# -*- coding: utf-8 -*-
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


''' + body

os.makedirs('server', exist_ok=True)
with open('server/pregeneration.py', 'w', encoding='utf-8') as f:
    f.write(out)
print('Written server/pregeneration.py')
