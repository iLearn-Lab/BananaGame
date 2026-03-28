---
name: frontend-mood-pages
description: >-
  Build ornate, animation-heavy, full-viewport HTML mood pages (game tone / art
  panels): CSS-only layered effects over imagery, literary overlay text, no
  minimal UI. Use for immersive single-page collages, triptychs, grid panels,
  唯美 or dark-gold literary layouts—not for slide decks (use frontend-slides).
  Trigger when the user asks for 基调页、氛围页、拼图格、画意动效、叠字、全屏沉浸 HTML.
---

# Frontend mood pages（沉浸基调页）

单页 HTML 作品：情绪与画意优先，**华丽、强动态、反极简**；默认多层叠效与错频动画，而非大留白或系统字体模板。

## Phase 0（强制）：需求澄清与门禁

**在用户对必问项作出回答，或明确确认你的汇总之前，不得编写或修改 HTML/CSS/JS，不得改图片资源。** 只能提问、归纳、等待。

### 必问（最低集）

用 **AskQuestion**（若可用）或一轮对话尽量合并提问：

1. **主题 / 基调**：情绪关键词（如唯美、黑深残、圆满）、参考系（是否对齐仓库内某页）、文案语言（如英文斜体叠字）、禁忌（如不要 blur 接缝）。
2. **布局与结构**：几格/几块、Grid 还是横联、是否无缝拼图（`gap: 0`）、移动端是堆叠还是保持格、主图数量与比例。

### 按需追问（由你判断）

叠字与否及锚点（角/中）、动效密度、色温与金/蓝强度、是否引入 Google Fonts、图片来源与路径、是否每区独立动效、`prefers-reduced-motion` 策略。

### 示例问题（可改编）

- 这一页想传达的核心情绪是哪一两个词？
- 布局更接近「三列拼图」「上下两块」还是「三联横画」？
- 每张图是否有现成文件？相对路径放在哪？
- 需要像唯美页那样每格一句哲学英文吗？放在中间还是某角？
- 动效希望「更明显」还是「略收一点」？

### 门禁（重申）

**无回答 = 不实现。** 用户已在首轮说全所有必问点时，用一句话复述并请其「确认」；收到确认后再进入实现。

---

## 何时用本 Skill vs frontend-slides

| 本 Skill (`frontend-mood-pages`) | `frontend-slides` |
| -------------------------------- | ----------------- |
| 单场景长驻、全屏沉浸、画意 + 叠层动效 | 多页幻灯、每页 100vh、内容密度与分页规则 |
| 拼图格 / 联画、每区独立氛围 | 标题页、要点页、代码页等模板 |
| 相对路径插图、内联 CSS、无构建链 | 可含 `viewport-base`、拆页防溢出 |

---

## 审美原则（显式）

- **华丽、富动态**：渐变、微粒高光、扫光、焦散、浮动、错频 `animation-delay`。
- **刻意不走极简**：避免大留白、无动效、Inter/Roboto/Arial 作主气质。
- **动效与层次是默认**：宁可分层调透明度，也不要「静态海报 + 一行标题」。

---

## 技术约束

- **单文件或少量 HTML**，样式以 **内联 `<style>`** 为主；**不强制** npm/打包。
- 图片 **相对路径**，`object-fit: cover`，`figure` / `.cell` 使用 `overflow: hidden`。
- 动效优先 **CSS**；装饰层 `pointer-events: none`，重要图保留 `alt`。
- 主气质字体：**Georgia** + 按需 **Google Fonts** 展示衬线（`preconnect` + `display=swap`）。

---

## 布局模式

- 全屏基底：`min-height: 100vh`，Grid 用 `minmax(0, 1fr)` 防止撑破。
- 典型结构：`main` 网格 + 多个 `figure.cell` 或 `section.panel`。
- 无缝拼图：`gap: 0`；需要缝时用实色/渐变间隙，**避免**用 `backdrop-filter` 做模糊接缝（除非用户明确要求）。

---

## 动效配方

### 叠层顺序（约定）

- **`img`**：`z-index: 0`（或 `position: relative; z-index: 0`），保证后续层叠在画面上。
- **氛围层**：`position: absolute; inset: 0; z-index: 2+`，命名建议 `fx-*` 或语义化类名。
- **文学叠字**：最高可读层，如 `z-index: 24`，高于常规 `fx-*`。

### `mix-blend-mode` 速查

| 模式 | 常见用途 |
| ---- | -------- |
| `screen` | 亮光、霞光、高光粒子 |
| `soft-light` / `overlay` | 统一色调、水波、金纹 |
| `multiply` | 压暗、暖色罩、晚霞厚重感 |
| `normal` | 需明确不混合的微粒（慎用透明度） |

### 节奏

- 每区 **不同** `animation-duration` 与 **负** `animation-delay`，避免整页同相位。
- 图片微动：`translateY` + `scale` 略大于 1，避免浮动露边。

---

## 叠字配方（`.cell-verse` 类模式）

- **英文 + `font-style: italic`**；哲学短句，**每格一句**。
- **暗金色**示例：`color: rgba(122, 98, 40, 0.78)`。
- **多层 `text-shadow`**：淡金高光 + 深褐描边 + 轻扩散，保证叠在复杂画上可读。
- **锚点**：用 flex `align-items` / `justify-content` + `clamp()` padding；可按格覆盖（如左上 / 右下 / 正中）。
- **艺术字格**：叠加 `Playfair Display`、`Cormorant Garamond` 等（与 Georgia 分工）。

---

## 可访问性

- 纯装饰层加 `aria-hidden="true"`。
- **`@media (prefers-reduced-motion: reduce)`**：相关 `animation: none !important`，并给静态 `opacity` 以免闪烁残留。

---

## 禁忌

- 模糊接缝（`backdrop-filter` 拼缝）——除非用户明确要求。
- 与画意无关的强几何装饰（如大面积 conic 射线、与内容打架的 mask 条带）——除非用户要该语言。
- 忽略 `reduced-motion`。
- **极简模板风**（大留白、无动效、系统字体当主视觉）。

---

## 参考范例（本仓库）

相对本文件（`.cursor/skills/frontend-mood-pages/SKILL.md`）：

- [唯美/index.html](../../../前端图片/基调/唯美/index.html)：多格拼图、分区色系动效、叠字、Playfair、图片浮动。
- [黑深残/index.html](../../../前端图片/基调/黑深残/index.html)：暗底紫金、玻璃缝、前景 `effects-foreground`、Cormorant、噪点与金纹。
- [圆满/index.html](../../../前端图片/基调/圆满/index.html)：四季/圆满向联画与文案节奏（可对照布局与动效强度）。

---

## 代码片段模板

### 1. 图片垫底 + 浮动（唯美式）

```css
.cell img {
    position: relative;
    z-index: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    transform-origin: center center;
    animation: imgFloat 7s ease-in-out infinite;
}
@keyframes imgFloat {
    0%, 100% { transform: translateY(0) scale(1.07); }
    50% { transform: translateY(-14px) scale(1.09); }
}
```

### 2. 氛围层叠在图上（唯美式 bloom）

```css
.fx-sunset-bloom {
    position: absolute;
    inset: 0;
    z-index: 2;
    pointer-events: none;
    background: radial-gradient(ellipse 120% 70% at 55% 18%, rgba(255, 95, 60, 0.5) 0%, transparent 52%);
    mix-blend-mode: screen;
    animation: sunsetBloom 7.5s ease-in-out infinite;
}
```

### 3. 黑深残式前景 + blend

```css
.effects-foreground {
    position: absolute;
    inset: 0;
    z-index: 2;
    pointer-events: none;
    overflow: hidden;
}
.gold-ripple-field {
    position: absolute;
    inset: 0;
    mix-blend-mode: soft-light;
    opacity: 0.92;
    pointer-events: none;
}
```

### 4. 叠字层（暗金 + 阴影）

```css
.cell-verse {
    position: absolute;
    inset: 0;
    z-index: 24;
    pointer-events: none;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: clamp(0.75rem, 3.5vw, 1.75rem);
    font-family: Georgia, 'Times New Roman', serif;
    font-style: italic;
    font-size: clamp(1.02rem, 2.85vw, 1.48rem);
    color: rgba(122, 98, 40, 0.78);
    text-align: center;
    text-shadow:
        0 0 3px rgba(255, 243, 210, 0.42),
        0 0 1px rgba(40, 32, 12, 0.65),
        0 1px 12px rgba(0, 0, 0, 0.28);
}
```

---

## 子模式提示

- **亮色画意**（唯美、圆满）：奶油/雾蓝底、暖色 bloom、screen/soft-light 为主。
- **暗色文学**（黑深残）：近黑底、紫金渐变、金纹与噪点、`multiply`/`overlay` 慎用对比度。

按用户在 Phase 0 的选择选用，不要两种硬混除非用户要求对比实验。

---


