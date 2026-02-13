# 图像生成 AI 模型切换清单：gemini-2.5-flash → gemini-3-pro-image-preview

将图像生成模型从 **gemini-2.5-flash-image-preview** 改为 **gemini-3-pro-image-preview** 时，按下列项逐项修改与验证。

**✅ 以下清单已按项执行完成（代码与文档已切换为 gemini-3-pro-image-preview）。**

---

## 一、必须修改（生效主路径）

### 1. 环境变量 `.env`

| 项 | 修改前 | 修改后 |
|----|--------|--------|
| `Image_Generation_MODEL` | `gemini-2.5-flash-image-preview` | `gemini-3-pro-image-preview` |

- **说明**：`src/config.py` 通过 `os.getenv("Image_Generation_MODEL", "sora_image")` 写入 `IMAGE_GENERATION_CONFIG["yunwu_model"]`，所有图像生成逻辑都读该配置。改 .env 后，**未写死默认值**的调用会直接使用新模型。
- **操作**：在 `.env` 中把 `Image_Generation_MODEL=gemini-2.5-flash-image-preview` 改为 `Image_Generation_MODEL=gemini-3-pro-image-preview`。

---

## 二、建议修改（默认值与文案一致）

以下位置在「未配置环境变量」或「日志/注释」中仍出现 `gemini-2.5-flash-image`，建议一并改为新模型或通用表述，避免误导。

### 2. `src/config.py`

| 行号 | 当前 | 建议 | 说明 |
|------|------|------|------|
| 24 | `"sora_image"` | 可保持不变 | `yunwu_model` 的默认值；你已配置 .env 则不会用到。若希望「无 .env 时也用 Gemini 图像模型」，可改为 `"gemini-3-pro-image-preview"`。 |

### 3. `src/image/api_providers.py`

**默认值（fallback）**：以下 `get("yunwu_model", "xxx")` 的第二个参数建议改为 `"gemini-3-pro-image-preview"`，与 .env 一致。

| 行号 | 当前默认值 | 建议改为 |
|------|------------|----------|
| 716 | `"gemini-2.5-flash-image"` | `"gemini-3-pro-image-preview"` |
| 1170 | `"gemini-2.5-flash-image"` | `"gemini-3-pro-image-preview"` |
| 1491 | `"gemini-2.5-flash-image"` | `"gemini-3-pro-image-preview"` |

**注释与打印文案**：将明确写死的 `gemini-2.5-flash-image` 改为 `gemini-3-pro-image-preview` 或通用表述（如「Gemini 图像模型」），便于日后再换模型时少改文案。

| 行号 | 类型 | 建议修改 |
|------|------|----------|
| 587 | 注释 | `gemini-2.5-flash-image` → `gemini-3-pro-image` 或「Gemini 图像模型」 |
| 713 | 注释 | 同上 |
| 718, 722, 724 | print | 同上 |
| 1179 | print | 同上 |
| 1479 | 函数说明 | 同上 |
| 1494, 1497, 1499 | print/注释 | 同上 |
| 1588, 1615, 1660, 1664 | print | 同上 |
| 1687, 1744, 1746 | 注释 | 同上 |
| 2240, 2281, 2282 | print（错误提示） | 改为「当前图像模型」或 `gemini-3-pro-image-preview` |
| 2437, 2442, 2443 | print（错误提示） | 同上 |

**逻辑说明**：当前用 `"gemini" in model.lower() and "image" in model.lower()` 判断是否走 Gemini 图生图。`gemini-3-pro-image-preview` 仍含 `gemini` 与 `image`，**无需改判断逻辑**。

### 4. `src/characters/supporting.py`

| 行号 | 当前 | 建议 |
|------|------|------|
| 177 | `get("yunwu_model", "gemini-2.5-flash-image")` | `get("yunwu_model", "gemini-3-pro-image-preview")` |

- **说明**：配角档案里记录的 `img_model` 展示用；与 .env 一致即可。

---

## 三、文档与其它

### 5. `docs/AI_USAGE.md`

| 位置 | 修改内容 |
|------|----------|
| 「当前 .env 下的实际模型与图像配置」小节 | 将 `Image_Generation_MODEL` / 图像生成主模型 从 `gemini-2.5-flash-image-preview` 改为 `gemini-3-pro-image-preview`。 |
| 「图像生成 AI 实际是什么」小结 | 主模型改为 **Gemini 3 Pro Image Preview**。 |
| 「九、环境变量与配置」中图像生成一句 | 若写了具体模型名，改为 `gemini-3-pro-image-preview`。 |

---

## 四、汇总表（按文件）

| 文件 | 修改类型 | 简要说明 |
|------|----------|----------|
| **.env** | 必须 | `Image_Generation_MODEL=gemini-3-pro-image-preview` |
| **src/config.py** | 可选 | 默认值可保持 `sora_image` 或改为 `gemini-3-pro-image-preview` |
| **src/image/api_providers.py** | 建议 | 3 处默认值 + 多处注释/print 中的 `gemini-2.5-flash-image` → `gemini-3-pro-image-preview`（或通用表述） |
| **src/characters/supporting.py** | 建议 | 1 处默认值 `gemini-2.5-flash-image` → `gemini-3-pro-image-preview` |
| **docs/AI_USAGE.md** | 建议 | 当前 .env 与实际模型描述改为 gemini-3-pro-image-preview |

---

## 五、验证建议

1. 修改 .env 后重启服务，发起一次「创建游戏 + 生成主角/场景图」流程，确认请求发往云雾且使用 `gemini-3-pro-image-preview`（可从日志或云雾控制台查看）。
2. 若云雾 API 对 `gemini-3-pro-image-preview` 的请求体（如 `response_format`、多图格式）与 `gemini-2.5-flash-image` 不同，需在 `api_providers.py` 中按新模型文档再做适配（本清单仅做配置与文案切换）。

完成以上项即完成从 gemini-2.5-flash 到 gemini-3-pro-image-preview 的改动与记录。
