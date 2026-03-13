长视频 / 图像生成与提示词优化工具
本项目是一个为叙事类/文字冒险类游戏服务的「内容生产引擎」，用多种大模型（LLM + 视觉模型）自动生成世界观、剧情分章、主角设定以及对应的剧情插图。
目录
·环境要求
·安装指南
·配置说明
·快速开始
·贡献指南
·许可证

环境要求
Python 版本：Python 3.12 及以上（详见 pyproject.toml 依赖声明）。
虚拟环境：推荐使用虚拟环境隔离依赖，避免影响全局 Python 环境。
安装指南
方式一：使用 uv 管理依赖（推荐）
项目已配置 pyproject.toml，通过 uv 可快速完成环境搭建：
bash
运行
# 创建虚拟环境（可选，若已有环境可跳过）
uv venv .venv

# 激活虚拟环境后，同步安装依赖
uv sync
方式二：使用 pip 安装依赖
若不使用 uv，可通过 pip 完成安装：
bash
运行
# 创建虚拟环境（可选）
python -m venv .venv

# 激活虚拟环境（Windows 示例）
.\.venv\Scripts\activate

# 方法一：基于 pyproject.toml 安装项目（推荐）
pip install .

# 方法二：通过 requirements.txt 安装
pip install -r requirements.txt
国内用户可通过镜像源加速安装，例如：
bash
运行
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

配置说明
项目通过 python-dotenv 加载环境变量，需在根目录创建 .env 文件并配置以下内容（可根据实际需求删减）。
1. 大语言模型配置
env
# 通用大模型调用（用于文本分析、剧情生成等）
Camera_Analyst_API_KEY=your_api_key
Camera_Analyst_BASE_URL=https://api.yunwu.ai/v1
Camera_Analyst_MODEL=gpt-4o
Camera_Analyst_READ_TIMEOUT=180
2. 群体智能（Council）配置
env
# 多模型列表（逗号分隔），默认使用 Camera_Analyst_MODEL
COUNCIL_MODELS=gpt-4o,gpt-4.1,gpt-4o-mini
# 主持人模型，默认使用 Camera_Analyst_MODEL
CHAIRMAN_MODEL=gpt-4o
3. 图像生成配置
env
# 图像生成服务提供商（默认：yunwu）
IMAGE_GENERATION_PROVIDER=yunwu
Image_Generation_API_KEY=your_image_api_key
Image_Generation_BASE_URL=https://yunwu.ai/v1
Image_Generation_MODEL=sora_image

# 可选：其他图像服务配置
REPLICATE_API_TOKEN=
OPENAI_API_KEY=
STABLE_DIFFUSION_BASE_URL=
STABLE_DIFFUSION_API_KEY=
4. 图像编辑（img2img）配置
env
Img2img_API_KEY=your_img2img_api_key
Img2img_BASE_URL=https://yunwu.ai/v1
Img2img_PATH=/images/edit
Img2img_MODEL=stability-ai/stable-diffusion-img2img
5. 视觉模型配置
env
VISION_REF_MODEL=gpt-4o
VISION_REF_API_KEY=  # 不填则默认使用 OPENAI_API_KEY
VISION_REF_BASE_URL=  # 留空则使用 OpenAI 默认地址
VISION_REF_TIMEOUT=120
VISION_REF_MAX_IMAGE_SIDE=1024
VISION_REF_MAX_TOKENS=512
VISION_REF_USE_GEMINI_ENDPOINT=false
6. Wikipedia 检索配置
env
WIKI_LOOKUP_ENABLED=true
WIKI_LANGS=zh,en
WIKI_TIMEOUT_SECONDS=8
WIKI_MAX_SNIPPET_CHARS=1200
注意：.env 文件包含敏感信息，请勿提交至版本控制系统。请确保 .gitignore 中已添加 .env 规则。
快速开始
确保虚拟环境已激活：
bash
运行
.\.venv\Scripts\activate  # Windows 示例
启动服务（以 Flask 为例，入口文件请根据实际项目调整）：
bash
运行
set FLASK_APP=app.py  # 替换为实际入口文件
flask run