# -*- coding: utf-8 -*-
"""Council 群体智能：多模型生成 → 匿名互评排名 → 主席综合。同步版，供世界观/剧情调用。"""
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Tuple, Optional

from src.config import AI_API_CONFIG, COUNCIL_MODELS, CHAIRMAN_MODEL
from src.llm.api import call_ai_api


def _query_model_sync(model: str, messages: List[Dict[str, str]], max_tokens: int = 4000) -> Optional[Dict[str, Any]]:
    """单模型同步请求，返回 {content: str} 或 None。"""
    try:
        body = {
            "model": model,
            "messages": messages,
            "temperature": 0.4,
            "max_tokens": max_tokens,
            "timeout": 200,
        }
        data = call_ai_api(body)
        choices = data.get("choices", [])
        if not choices:
            return None
        msg = choices[0].get("message", {})
        content = msg.get("content")
        if isinstance(content, list):
            parts = [p.get("text", p) if isinstance(p, dict) else str(p) for p in content]
            content = "".join(str(p) for p in parts) if parts else ""
        if content is None:
            content = ""
        return {"content": content}
    except Exception:
        return None


def _stage1_collect_responses(prompt: str) -> List[Dict[str, Any]]:
    """Stage1：多模型并行生成，返回 [{model, response}, ...]。"""
    print(f"🤖 Council Stage1：{len(COUNCIL_MODELS)} 个模型并行生成中...")
    print(f"   参与模型：{', '.join(COUNCIL_MODELS)}")
    messages = [{"role": "user", "content": prompt}]
    results = []
    with ThreadPoolExecutor(max_workers=len(COUNCIL_MODELS)) as ex:
        futures = {ex.submit(_query_model_sync, m, messages): m for m in COUNCIL_MODELS}
        for f in as_completed(futures):
            model = futures[f]
            try:
                resp = f.result()
                if resp and resp.get("content"):
                    results.append({"model": model, "response": resp["content"]})
                    print(f"   ✅ {model} 生成完成（{len(resp['content'])} 字符）")
                else:
                    print(f"   ❌ {model} 生成失败或返回空内容")
            except Exception as e:
                print(f"   ❌ {model} 生成异常：{str(e)}")
    print(f"📊 Stage1 完成：{len(results)}/{len(COUNCIL_MODELS)} 个模型成功生成")
    return results


def _parse_ranking_from_text(ranking_text: str) -> List[str]:
    """从评价文本中解析 FINAL RANKING: 后的 Response A/B/C 顺序。"""
    if "FINAL RANKING:" in ranking_text:
        parts = ranking_text.split("FINAL RANKING:")
        if len(parts) >= 2:
            section = parts[1]
            numbered = re.findall(r"\d+\.\s*Response [A-Z]", section)
            if numbered:
                return [re.search(r"Response [A-Z]", m).group() for m in numbered]
            return re.findall(r"Response [A-Z]", section)
    return re.findall(r"Response [A-Z]", ranking_text)


def _stage2_collect_rankings(
    user_query: str, stage1_results: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """Stage2：匿名互评排名，返回 (rankings_list, label_to_model)。"""
    if not stage1_results:
        return [], {}
    print(f"🔍 Council Stage2：{len(COUNCIL_MODELS)} 个模型匿名互评排名中...")
    labels = [chr(65 + i) for i in range(len(stage1_results))]
    label_to_model = {f"Response {label}": r["model"] for label, r in zip(labels, stage1_results)}
    mapping_parts = [f"{label}→{label_to_model['Response ' + label]}" for label in labels]
    print(f"   匿名标签映射：{', '.join(mapping_parts)}")
    responses_text = "\n\n".join(
        [f"Response {label}:\n{r['response']}" for label, r in zip(labels, stage1_results)]
    )
    ranking_prompt = f"""你正在评价对同一问题的多份回答（匿名）。请从逻辑、趣味性、深度、与主线连贯性等维度评价每份，最后给出综合排名。

【问题/背景】
{user_query}

【匿名回答】
{responses_text}

请先逐份简短评价，然后在回复最后严格按以下格式给出排名（从最佳到最差）：
FINAL RANKING:
1. Response A
2. Response C
3. Response B
（仅包含 Response 标签，不要在此部分写其他内容）"""

    messages = [{"role": "user", "content": ranking_prompt}]
    stage2_results = []
    with ThreadPoolExecutor(max_workers=len(COUNCIL_MODELS)) as ex:
        futures = {ex.submit(_query_model_sync, m, messages, 2000): m for m in COUNCIL_MODELS}
        for f in as_completed(futures):
            model = futures[f]
            try:
                resp = f.result()
                if resp and resp.get("content"):
                    full = resp["content"]
                    parsed = _parse_ranking_from_text(full)
                    stage2_results.append({"model": model, "ranking": full, "parsed_ranking": parsed})
                    print(f"   ✅ {model} 评价完成，排名：{', '.join(parsed) if parsed else '解析失败'}")
                else:
                    print(f"   ❌ {model} 评价失败或返回空内容")
            except Exception as e:
                print(f"   ❌ {model} 评价异常：{str(e)}")
    print(f"📊 Stage2 完成：{len(stage2_results)}/{len(COUNCIL_MODELS)} 个模型完成评价")
    return stage2_results, label_to_model


def _stage3_synthesize(
    user_query: str,
    stage1_results: List[Dict[str, Any]],
    stage2_results: List[Dict[str, Any]],
) -> str:
    """Stage3：主席综合，返回最终文本。"""
    print(f"👑 Council Stage3：主席模型 {CHAIRMAN_MODEL} 正在综合 {len(stage1_results)} 份回答和 {len(stage2_results)} 份评价...")
    stage1_text = "\n\n".join([f"Model: {r['model']}\nResponse: {r['response']}" for r in stage1_results])
    stage2_text = "\n\n".join([f"Model: {r['model']}\nRanking: {r['ranking']}" for r in stage2_results])
    chairman_prompt = f"""你是理事会主席。多份回答已由其他模型匿名评价并排名。请综合所有内容，产出一份更优的最终版本：逻辑更清晰、更有趣、更有深度，且与给定背景/主线连贯。

【原始问题/背景】
{user_query}

【各模型回答】
{stage1_text}

【各模型评价与排名】
{stage2_text}

请直接输出你的综合结果（仅输出最终内容，不要输出评价或说明）："""

    messages = [{"role": "user", "content": chairman_prompt}]
    resp = _query_model_sync(CHAIRMAN_MODEL, messages, max_tokens=4000)
    if resp and resp.get("content"):
        final_content = resp["content"].strip()
        print(f"   ✅ 主席综合完成（{len(final_content)} 字符）")
        return final_content
    print(f"   ❌ 主席综合失败或返回空内容")
    return ""


def run_full_council_sync(prompt: str) -> str:
    """
    跑完三阶段 council，只返回主席综合后的文本。
    若 Stage1 无有效结果则返回空字符串（调用方应做降级）。
    """
    print("=" * 60)
    print("🌟 启动 Council 群体智能流程")
    print("=" * 60)
    
    if len(COUNCIL_MODELS) < 2:
        print(f"⚠️ 警告：COUNCIL_MODELS 只有 {len(COUNCIL_MODELS)} 个模型，无法体现群体智能")
        print(f"   建议配置至少 2-3 个不同模型，当前：{COUNCIL_MODELS}")
    
    stage1_results = _stage1_collect_responses(prompt)
    if not stage1_results:
        print("⚠️ Council Stage1 无有效响应，无法进行讨论")
        print("=" * 60)
        return ""
    
    stage2_results, _ = _stage2_collect_rankings(prompt, stage1_results)
    final = _stage3_synthesize(prompt, stage1_results, stage2_results)
    
    print("=" * 60)
    if final:
        print(f"✅ Council 群体智能流程完成！最终输出 {len(final)} 字符")
    else:
        print("❌ Council 群体智能流程完成，但最终输出为空")
    print("=" * 60)
    
    return final or ""
