# game_server.py 拆分清单

**当前状态**：拆分已完成。`game_server.py` 已从约 **2420 行** 精简为约 **1397 行**（路由与入口保留在根目录），配置、缓存、预生成逻辑、工具与图片缓存已迁至 `server/` 包。

**实际采用**：路由仍保留在 `game_server.py` 中，仅将「配置 / 缓存 / 工具 / 预生成 / 图片缓存」拆到 `server/`，避免将 600+ 行的 `generate_option` 再拆到多文件。若后续需要，可再按清单将路由迁入 `server/routes/` 的 Blueprint。

**建议**：每步将对应代码迁到新文件 → 补全 import → 在 `game_server.py` 中改为 `from ... import ...` 或注册 Blueprint，然后跑一遍 Web 流程验证。

---

## 一、目标目录结构（拆分后）

```
项目根/
├── game_server.py          # 仅：环境、Flask app、注册路由、启动
├── server/                  # 新建：Web 服务相关模块
│   ├── __init__.py
│   ├── config.py            # 目录与缓存常量
│   ├── cache.py             # 预生成缓存 + 锁 + 清理
│   ├── utils.py             # clean_error_message 等
│   ├── pregeneration.py     # _pregenerate_next_layers_logic
│   ├── image_cache.py       # 图片缓存读写、generate_image_with_cache
│   └── routes/              # 可选：按领域拆成多个 Blueprint
│       ├── __init__.py
│       ├── worldview.py     # generate_worldview
│       ├── option.py        # generate_option
│       ├── pregen_api.py    # pregenerate_next_layers, get_pregenerated_layer2
│       ├── save_load.py     # save_game, list_saves, load_game, delete_save
│       ├── ending.py        # generate_ending
│       ├── media.py         # 场景图/视频占位/主角图/image_cache 静态
│       └── static.py        # index, frontend_files
```

若不想用 Blueprint，可把 `routes/` 改为单文件 `server/routes.py`，按函数分块即可。

---

## 二、拆分步骤（按执行顺序）

### 第一步：配置与常量（无业务依赖）

| 序号 | 目标文件 | 从 game_server.py 迁出内容 | 行号（约） |
|------|----------|---------------------------|------------|
| 1.1 | `server/config.py` | 目录常量与缓存上限 | 42-56, 154 |
| 1.2 | | `SAVE_DIR`, `IMAGE_CACHE_DIR`, `VIDEO_CACHE_DIR` | 42-50 |
| 1.3 | | `MAX_CACHE_SIZE = 3` | 154 |
| 1.4 | | 目录存在性检查与创建（`os.makedirs`） | 45-56 |

**说明**：仅保留「从字面量/环境读出的配置」，不放 Flask 或 main2 依赖。其他模块通过 `from server.config import SAVE_DIR, IMAGE_CACHE_DIR, ...` 使用。

---

### 第二步：缓存与锁（仅依赖 config + 标准库）

| 序号 | 目标文件 | 从 game_server.py 迁出内容 | 行号（约） |
|------|----------|---------------------------|------------|
| 2.1 | `server/cache.py` | 全局变量与锁 | 58-74, 76-154 |
| 2.2 | | 缓存结构注释（pregeneration_cache 的 schema） | 58-71 |
| 2.3 | | `pregeneration_cache = {}` | 71 |
| 2.4 | | `_cache_lock_holder`, `_cache_lock_acquire_time` | 73-74 |
| 2.5 | | `class TrackedLock`（完整类） | 76-151 |
| 2.6 | | `cache_lock = TrackedLock("cache_lock")` | 153 |
| 2.7 | | `cleanup_old_cache(current_scene_id=None)` | 176-219 |
| 2.8 | | `cleanup_used_options(scene_id, used_option_index)` | 222-241 |

**依赖**：`threading`、`server.config`（若把 `MAX_CACHE_SIZE` 放 config 则从 config 读）。  
**导出**：`pregeneration_cache`、`cache_lock`、`cleanup_old_cache`、`cleanup_used_options`，供预生成与路由使用。

---

### 第三步：通用工具（无 Flask 依赖）

| 序号 | 目标文件 | 从 game_server.py 迁出内容 | 行号（约） |
|------|----------|---------------------------|------------|
| 3.1 | `server/utils.py` | `clean_error_message(error_msg)` | 156-170 |
| 3.2 | | `generate_scene_id(global_state_hash, current_options_hash)` | 172-174 |

**依赖**：仅 `re`（在 `clean_error_message` 内）。  
**说明**：`generate_scene_id` 被预生成与缓存逻辑使用，放在 utils 便于 cache 与 pregeneration 共用。

---

### 第四步：预生成核心逻辑（依赖 cache + utils + main2）

| 序号 | 目标文件 | 从 game_server.py 迁出内容 | 行号（约） |
|------|----------|---------------------------|------------|
| 4.1 | `server/pregeneration.py` | `_pregenerate_next_layers_logic(global_state, current_options, scene_id)` 整函数 | 1094-1804 |

**依赖**：  
- `server.cache`：`pregeneration_cache`、`cache_lock`  
- `server.utils`：`generate_scene_id`  
- `main2`：`_generate_single_option`、`_generate_single_option_text_only`、`generate_all_options`、`generate_scene_image`  
- 标准库：`threading`、`hashlib`、`os`、`concurrent.futures.ThreadPoolExecutor`

**说明**：该函数约 710 行，迁移后可在本文件内再拆成若干子函数（如「第一层生成」「第二层生成」「单选项任务」），便于阅读与单测。

---

### 第五步：图片缓存（依赖 config，被路由与预生成使用）

| 序号 | 目标文件 | 从 game_server.py 迁出内容 | 行号（约） |
|------|----------|---------------------------|------------|
| 5.1 | `server/image_cache.py` | `get_cached_image(prompt_hash)` | 2165-2170 |
| 5.2 | | `cache_image(prompt_hash, image_url)` | 2172-2213 |
| 5.3 | | `generate_image_with_cache(scene_description, style, global_state)` | 2215-2266 |

**依赖**：`pathlib.Path`、`requests`、`hashlib`、`server.config`（`IMAGE_CACHE_DIR`）；`generate_image_with_cache` 内部会调用 main2 的 `generate_scene_image`，需在 image_cache 内 `from main2 import generate_scene_image`。  
**说明**：此处「图片缓存」指按 prompt_hash 存文件并返回本地路径的逻辑，与 `pregeneration_cache`（剧情/选项缓存）分离。

---

### 第六步：路由——世界观

| 序号 | 目标文件 | 从 game_server.py 迁出内容 | 行号（约） |
|------|----------|---------------------------|------------|
| 6.1 | `server/routes/worldview.py` 或 `server/routes.py` 中一段 | `@app.route('/generate-worldview', methods=['POST'])` 与 `def generate_worldview():` 整段 | 252-465 |

**依赖**：  
- main2：`generate_game_id`、`llm_generate_global`、`generate_main_character_image`、`_generate_single_option`  
- server.cache：`pregeneration_cache`、`cache_lock`  
- server.utils：`clean_error_message`  
- Flask：`request`、`jsonify`

**注册方式**：若用 Blueprint，在 `game_server.py` 中 `app.register_blueprint(worldview_bp)`；若单文件 `server/routes.py`，则在该文件中用 `app` 注册（app 需从 game_server 传入或通过 `current_app` 获取）。

---

### 第七步：路由——单选项剧情（体积最大）

| 序号 | 目标文件 | 从 game_server.py 迁出内容 | 行号（约） |
|------|----------|---------------------------|------------|
| 7.1 | `server/routes/option.py` 或 `server/routes.py` 中一段 | `@app.route('/generate-option', methods=['POST'])` 与 `def generate_option():` 整段 | 467-1092 |

**依赖**：  
- server.cache：`pregeneration_cache`、`cache_lock`、`cleanup_old_cache`、`cleanup_used_options`  
- server.utils：`clean_error_message`  
- main2：`_generate_single_option`、`generate_scene_image`  
- Flask：`request`、`jsonify`  
- 标准库：`hashlib`、`threading`、`os`、`time`

**说明**：该处理函数约 626 行，迁移后建议在模块内拆成多个子函数（如「从 initial 取数据」「从 scene_id 缓存取数据」「按需生成并等待」「补图与兜底」「清理上一轮缓存」），再在 `generate_option` 中按顺序调用，便于维护和单测。

---

### 第八步：路由——预生成接口

| 序号 | 目标文件 | 从 game_server.py 迁出内容 | 行号（约） |
|------|----------|---------------------------|------------|
| 8.1 | `server/routes/pregen_api.py` 或 `server/routes.py` | `@app.route('/pregenerate-next-layers', methods=['POST'])` 与 `def pregenerate_next_layers():` | 1806-1866 |
| 8.2 | | `@app.route('/get-pregenerated-layer2', methods=['POST'])` 与 `def get_pregenerated_layer2():` | 1868-1897 |

**依赖**：server.cache、server.utils、server.pregeneration（`_pregenerate_next_layers_logic`）、Flask。

---

### 第九步：路由——存档与读档

| 序号 | 目标文件 | 从 game_server.py 迁出内容 | 行号（约） |
|------|----------|---------------------------|------------|
| 9.1 | `server/routes/save_load.py` 或 `server/routes.py` | `@app.route('/save-game', methods=['POST'])` 与 `def save_game():` | 1899-1959 |
| 9.2 | | `@app.route('/list-saves', methods=['GET'])` 与 `def list_saves():` | 1961-2013 |
| 9.3 | | `@app.route('/load-game', methods=['POST'])` 与 `def load_game():` | 2015-2068 |
| 9.4 | | `@app.route('/delete-save', methods=['POST'])` 与 `def delete_save():` | 2070-2106 |

**依赖**：`server.config`（`SAVE_DIR`）、server.utils（`clean_error_message`）、Flask、`json`、`os`、`datetime`。

---

### 第十步：路由——结局

| 序号 | 目标文件 | 从 game_server.py 迁出内容 | 行号（约） |
|------|----------|---------------------------|------------|
| 10.1 | `server/routes/ending.py` 或 `server/routes.py` | `@app.route('/generate-ending', methods=['POST'])` 与 `def generate_ending():` | 2108-2162 |

**依赖**：main2（`generate_ending_prediction`、`modify_ending_content`）、server.utils、Flask。

---

### 第十一步：路由——场景图与媒体静态

| 序号 | 目标文件 | 从 game_server.py 迁出内容 | 行号（约） |
|------|----------|---------------------------|------------|
| 11.1 | `server/routes/media.py` 或 `server/routes.py` | `@app.route('/generate-scene-image', methods=['POST'])` 与 `def generate_scene_image_api():` | 2272-2320 |
| 11.2 | | `@app.route('/generate-scene-video', methods=['POST'])` 与 `def generate_scene_video_api():`（占位） | 2334-2340 |
| 11.3 | | `@app.route('/video-status/<task_id>', methods=['GET'])` 与 `def get_video_status_api(task_id):`（占位） | 2342-2349 |
| 11.4 | | `@app.route('/initial/main_character/<game_id>/<filename>')` 与 `def serve_main_character_image(...)` | 2350-2368 |
| 11.5 | | `@app.route('/image_cache/<filename>')` 与 `def serve_cached_image(filename)` | 2369-2380 |

**依赖**：main2（`generate_scene_image`）、server.config（`IMAGE_CACHE_DIR`）、server.utils、Flask、`pathlib.Path`、`send_file`。

---

### 第十二步：路由——前端静态

| 序号 | 目标文件 | 从 game_server.py 迁出内容 | 行号（约） |
|------|----------|---------------------------|------------|
| 12.1 | `server/routes/static.py` 或 `server/routes.py` | `@app.route('/')` 与 `def index():` | 2382-2385 |
| 12.2 | | `@app.route('/<path:filename>')` 与 `def frontend_files(filename):` | 2387-2396 |

**依赖**：Flask（`send_from_directory`、`jsonify`）。

---

### 第十三步：入口保留在 game_server.py

| 序号 | 保留在 game_server.py 的内容 | 行号（约） |
|------|------------------------------|------------|
| 13.1 | 文件头、Windows UTF-8 设置 | 1-16 |
| 13.2 | 从 main2 的 import 列表 | 18-33 |
| 13.3 | `app = Flask(__name__)`、`load_dotenv()` | 36-39 |
| 13.4 | 对 server.config 的调用（确保目录存在，若迁到 config 则此处仅 import 并调用一次） | 可收敛到 1 处 |
| 13.5 | `@app.after_request` 与 `def after_request(response):`（CORS） | 244-249 |
| 13.6 | 对 server.routes 的注册（Blueprint 或 import 并注册） | — |
| 13.7 | `if __name__ == "__main__":` 与启动打印、`app.run(...)` | 2399-2420 |

**说明**：拆分完成后，`game_server.py` 仅负责「组装配件」：环境、创建 app、挂 CORS、注册各路由模块、启动。体量目标约 **80～120 行**。

---

## 三、行号与内容对照表（便于复制粘贴）

| 模块/功能 | 起始行 | 结束行 | 说明 |
|-----------|--------|--------|------|
| 环境与 import | 1 | 39 | 含 main2 导入、Flask、load_dotenv |
| 目录与缓存常量 | 42 | 56 | SAVE_DIR, IMAGE_CACHE_DIR, VIDEO_CACHE_DIR, makedirs |
| 预生成缓存结构注释 | 58 | 71 | pregeneration_cache 的 schema + 变量 |
| 锁调试全局变量 | 73 | 74 | _cache_lock_holder, _cache_lock_acquire_time |
| TrackedLock 类 | 76 | 151 | 完整类 |
| cache_lock, MAX_CACHE_SIZE | 153 | 154 | 实例与常量 |
| clean_error_message | 156 | 170 | 工具函数 |
| generate_scene_id | 172 | 174 | 工具函数 |
| cleanup_old_cache | 176 | 219 | 缓存清理 |
| cleanup_used_options | 222 | 241 | 已用选项清理 |
| after_request (CORS) | 245 | 249 | 中间件 |
| generate_worldview | 252 | 465 | 世界观路由 |
| generate_option | 467 | 1092 | 单选项剧情路由 |
| _pregenerate_next_layers_logic | 1094 | 1804 | 预生成核心逻辑 |
| pregenerate_next_layers | 1806 | 1866 | 预生成接口 |
| get_pregenerated_layer2 | 1868 | 1897 | 取第二层接口 |
| save_game | 1899 | 1959 | 存档 |
| list_saves | 1961 | 2013 | 列存档 |
| load_game | 2015 | 2068 | 读档 |
| delete_save | 2070 | 2106 | 删档 |
| generate_ending | 2108 | 2162 | 结局 |
| get_cached_image | 2165 | 2170 | 图片缓存读 |
| cache_image | 2172 | 2213 | 图片缓存写 |
| generate_image_with_cache | 2215 | 2266 | 带缓存的生成 |
| generate_scene_image_api | 2272 | 2320 | 场景图 API |
| 视频占位两个路由 | 2334 | 2349 | 禁用视频的占位 |
| serve_main_character_image | 2350 | 2368 | 主角图静态 |
| serve_cached_image | 2369 | 2380 | 缓存图静态 |
| index | 2382 | 2385 | 首页 |
| frontend_files | 2387 | 2396 | 前端静态 |
| 启动块 | 2399 | 2420 | if __name__ + app.run |

---

## 四、依赖关系简图

```
game_server.py (入口)
  ├── server.config       (SAVE_DIR, IMAGE_CACHE_DIR, VIDEO_CACHE_DIR, MAX_CACHE_SIZE)
  ├── server.cache        (pregeneration_cache, cache_lock, cleanup_old_cache, cleanup_used_options)
  ├── server.utils        (clean_error_message, generate_scene_id)
  ├── server.pregeneration (_pregenerate_next_layers_logic)
  ├── server.image_cache  (get_cached_image, cache_image, generate_image_with_cache)
  └── server.routes.*     (各路由，依赖 cache/utils/main2/image_cache 等)
          ↑
      main2 (llm_generate_global, _generate_single_option, generate_scene_image, ...)
```

---

## 五、注意事项

1. **循环导入**：路由模块不要直接 `import game_server` 取 `app`。建议在 `game_server.py` 中创建 `app` 后，将各 Blueprint 注册到 `app`，或在一个 `server/routes/__init__.py` 中定义 `def register_routes(app)`，由 `game_server.py` 调用并传入 `app`。
2. **main2 依赖**：所有从 main2 的导入仍保留在 `game_server.py` 或在使用处（如 pregeneration、routes）按需 `from main2 import ...`，避免 server 包依赖 src 包造成耦合；若项目已统一从 src 暴露入口，可改为从 src 导入。
3. **全局单例**：`pregeneration_cache` 与 `cache_lock` 必须在整个进程内唯一，只在一个模块（如 `server/cache.py`）中定义，其余处仅引用。
4. **测试**：每拆完一步，建议执行一次 `python game_server.py` 并在浏览器访问首页、生成世界观、选择选项、存档读档，确认无 404 与 500。

按本清单顺序执行，即可将 `game_server.py` 拆分为「配置 / 缓存 / 工具 / 预生成 / 图片缓存 / 路由 / 入口」多模块，并保持行为一致。
