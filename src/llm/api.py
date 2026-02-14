# -*- coding: utf-8 -*-
"""LLM 通用调用与 JSON 解析。"""
import json
import re
import requests
from typing import Dict
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config import AI_API_CONFIG


@retry(
    stop=stop_after_attempt(15),
    wait=wait_exponential(multiplier=1, min=5, max=30),
    retry=(
        retry_if_exception_type(requests.exceptions.ConnectionError) |
        retry_if_exception_type(requests.exceptions.Timeout)
    ),
    reraise=True
)
def call_ai_api(request_body: Dict) -> Dict:
    """
    调用AI API的通用函数，带自动重试（401/403错误不重试）
    """
    api_key = AI_API_CONFIG.get('api_key', '')
    base_url = AI_API_CONFIG.get('base_url', '')

    if not api_key:
        raise ValueError("API密钥未配置，请在.env文件中设置Camera_Analyst_API_KEY")
    if not base_url:
        raise ValueError("API基础URL未配置，请在.env文件中设置Camera_Analyst_BASE_URL")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json; charset=utf-8"
    }

    try:
        connect_timeout = 30   # 连接阶段超时（秒）
        read_timeout = 180     # 读取响应超时（秒）
        timeout = (connect_timeout, read_timeout)
        stream_flag = False
        if request_body.get("stream"):
            stream_flag = True
            request_body = dict(request_body)
            request_body.pop("stream", None)
            print("ℹ️ Stream模式暂不直接支持，已自动降级为普通请求")

        print(f"📡 发送API请求... (连接超时{connect_timeout}秒，读取超时{read_timeout}秒)")
        response = requests.post(
            url=f"{base_url}/chat/completions",
            headers=headers,
            json=request_body,
            timeout=timeout
        )
        response.raise_for_status()
        print("✅ API请求成功")
        return response.json()
    except requests.exceptions.HTTPError as e:
        status_code = e.response.status_code if e.response else 0
        if status_code in [401, 403]:
            print(f"❌ API认证失败（HTTP {status_code}），请检查API密钥和权限配置")
            print(f"   当前API配置：")
            print(f"   - API基础URL: {base_url}")
            print(f"   - API密钥: {'已配置' if api_key else '未配置'} (长度: {len(api_key) if api_key else 0})")
            print(f"   - 请求URL: {base_url}/chat/completions")
            print(f"   提示：请确认.env文件中的Camera_Analyst_API_KEY是否正确")
            print(f"   提示：请确认API基础URL（Camera_Analyst_BASE_URL）是否正确，应该是完整的URL，如：https://api.example.com/v1")
            if base_url and not base_url.startswith(('http://', 'https://')):
                print(f"   ⚠️ 警告：API基础URL格式可能不正确，应该以http://或https://开头")
            error_msg = f"API认证失败（HTTP {status_code}）。请检查：1) .env文件中的Camera_Analyst_API_KEY是否正确 2) API密钥是否有权限 3) Camera_Analyst_BASE_URL格式是否正确（应该是完整URL）"
            raise ValueError(error_msg) from e
        print(f"⚠️ API请求失败（HTTP错误 {status_code}），错误信息：{str(e)[:100]}")
        raise
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        print(f"⚠️ API请求失败（网络/超时），将自动重试：{str(e)[:100]}")
        print(f"   提示：请检查 1) 本机能否访问 {base_url}  2) 是否需要代理(HTTP_PROXY/HTTPS_PROXY)  3) 防火墙是否放行 443")
        raise
    except Exception as e:
        print(f"⚠️ API请求失败（未知错误）：{str(e)[:100]}")
        raise


def extract_and_validate_json(raw_text: str) -> str:
    """
    从原始文本中提取JSON内容并做基础验证
    处理场景：AI返回内容包含多余文字、代码块标记、格式错误等
    """
    if not raw_text:
        return ""

    first_brace = raw_text.find('{')
    first_bracket = raw_text.find('[')
    if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
        start_idx = first_brace
    elif first_bracket != -1:
        start_idx = first_bracket
    else:
        return ""

    cleaned_text = raw_text[start_idx:]

    if cleaned_text.startswith('{'):
        brace_count = 1
        end_idx = 1
        for i, char in enumerate(cleaned_text[1:], start=1):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    end_idx = i + 1
                    break
    else:
        bracket_count = 1
        end_idx = 1
        for i, char in enumerate(cleaned_text[1:], start=1):
            if char == '[':
                bracket_count += 1
            elif char == ']':
                bracket_count -= 1
                if bracket_count == 0:
                    end_idx = i + 1
                    break

    json_str = cleaned_text[:end_idx]
    json_str = json_str.strip()
    json_str = json_str.replace("...", "")
    while json_str and json_str[-1] in [',', ';', '.', ' ', '\n', '\t', '"', "'"]:
        json_str = json_str[:-1]
    json_str = json_str.replace("：", ":").replace("，", ",").replace("\u201c", '"').replace("\u201d", '"')
    json_str = re.sub(r'(?<=[{,\s])\s*([a-zA-Z0-9_\u4e00-\u9fa5]+)\s*:', r' "\1":', json_str)
    json_str = json_str.replace("'", '"')
    json_str = json_str.replace('True', 'true').replace('False', 'false').replace('None', 'null')
    json_str = re.sub(r'\\"', '"', json_str)
    json_str = json_str.replace('\n', '\\n').replace('\t', '\\t')

    try:
        json.loads(json_str)
        return json_str
    except json.JSONDecodeError:
        simple_json = json_str.replace(' ', '').replace('\n', '').replace('\t', '')
        try:
            json.loads(simple_json)
            return simple_json
        except json.JSONDecodeError:
            return json_str
    return json_str
