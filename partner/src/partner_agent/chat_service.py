"""Shared chat/orchestration utilities for Partner and Human APIs."""

from __future__ import annotations

import json
import re
from typing import Literal

from .brain import DeepSeekChatBrain
from .memory import MEMORY

_BRAIN = DeepSeekChatBrain()
_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)
ROUTER_TIMEOUT_SECONDS = 8.0
POSTPROCESS_TIMEOUT_SECONDS = 12.0
RELEVANCE_TIMEOUT_SECONDS = 4.0

RouterAction = Literal[
    "call_start",
    "call_continue",
    "call_complete",
    "call_get",
    "need_rpc_url",
    "local_reply",
]


def fallback_answer(user_text: str) -> str:
    """Return a deterministic fallback message when model calls are unavailable."""
    return f"已收到文本咨询：{user_text}"


def _extract_json_object(text: str) -> dict[str, object]:
    """Extract and parse one JSON object from potentially noisy model output."""
    raw = text.strip()
    if raw.startswith("```"):
        lines = [line for line in raw.splitlines() if not line.strip().startswith("```")]
        raw = "\n".join(lines).strip()
    match = _JSON_BLOCK_RE.search(raw)
    if match:
        raw = match.group(0)
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("router output is not a json object")
    return data


async def decide_human_action(
    user_text: str,
    *,
    router_history_key: str,
    candidate_rpc_url: str | None = None,
    has_active_task: bool,
    active_task_state: str,
) -> dict[str, str]:
    """Decide whether to answer locally or call/continue/complete remote tasks."""
    text = user_text.strip()
    if not text:
        return {"action": "local_reply"}

    if not _BRAIN.enabled:
        return {"action": "local_reply"}

    history = MEMORY.get_history(router_history_key)
    url_hint = candidate_rpc_url or ""
    task_hint = active_task_state or "none"
    router_prompt = (
        "你是智能体编排器，只做动作决策，不直接回答用户问题。\n"
        "输出必须是单个 JSON 对象，不要输出任何额外文本。\n"
        'JSON schema: {"action":"call_start|call_continue|call_complete|call_get|need_rpc_url|local_reply","query":"...","reason":"..."}\n'
        "规则：\n"
        "1) 如果当前有 active_task，优先根据状态决定 call_get/call_continue/call_complete。\n"
        "2) 如果需要新发起协作但没有可用RPC地址，返回 need_rpc_url。\n"
        "3) 不需要调用外部智能体时返回 local_reply。\n"
        "4) action=call_start/call_continue 时，可在 query 字段给出要发送给Partner的文本。\n"
        f"可用RPC地址: {url_hint if url_hint else '无'}\n"
        f"是否有active_task: {'yes' if has_active_task else 'no'}\n"
        f"active_task_state: {task_hint}"
    )
    try:
        model_output = await _BRAIN.chat(
            text,
            history=history,
            system_prompt=router_prompt,
            timeout_seconds=ROUTER_TIMEOUT_SECONDS,
        )
        data = _extract_json_object(model_output)
        action = str(data.get("action", "")).strip().lower()
        query = str(data.get("query", "")).strip()
        valid_actions: set[str] = {
            "call_start",
            "call_continue",
            "call_complete",
            "call_get",
            "need_rpc_url",
            "local_reply",
        }
        if action not in valid_actions:
            return {"action": "local_reply"}
        result: dict[str, str] = {"action": action}
        if query:
            result["query"] = query
        MEMORY.append_exchange(router_history_key, text, json.dumps(result, ensure_ascii=False))
        return result
    except Exception:
        return {"action": "local_reply"}


async def postprocess_collaboration_result(
    *,
    user_request: str,
    partner_response: str,
    leader_result: dict[str, object],
    call_proof: dict[str, object] | None = None,
    conversation_key: str | None = None,
) -> str:
    """Transform raw partner output into user-facing delivery text."""
    partner_text = partner_response.strip()
    if not partner_text:
        partner_texts = leader_result.get("product_texts") or leader_result.get("status_texts") or []
        if partner_texts:
            partner_text = str(partner_texts[0]).strip()
    if not partner_text:
        return "远端智能体未返回可处理文本。"

    if not _BRAIN.enabled:
        return partner_text

    history = MEMORY.get_history(conversation_key) if conversation_key else []
    system_prompt = (
        "你是协作结果整合助手。必须严格根据用户要求处理远端智能体返回内容。\n"
        "要求：\n"
        "1) 不编造远端未提供的信息；\n"
        "2) 用户要求原文转发时，保留原文；\n"
        "3) 用户要求精炼、改写、提炼、结构化时，按要求处理；\n"
        "4) 如果调用证明不足（invoked=false或trace_steps为空），不得声称已完成远端提问；\n"
        "5) 无证据时禁止推断远端内部机制，可表达“当前未得到有效远端响应证据”；\n"
        "6) 仅输出最终交付文本，不要解释你的内部过程。"
    )
    payload = json.dumps(
        {
            "user_request": user_request,
            "partner_response": partner_text,
            "leader_final_state": leader_result.get("final_state", ""),
            "call_proof": call_proof or {},
        },
        ensure_ascii=False,
    )
    try:
        return await _BRAIN.chat(
            payload,
            history=history,
            system_prompt=system_prompt,
            timeout_seconds=POSTPROCESS_TIMEOUT_SECONDS,
        )
    except Exception:
        return partner_text


async def is_partner_response_relevant(
    *,
    user_request: str,
    partner_response: str,
    conversation_key: str | None = None,
) -> bool:
    """Judge whether partner output is semantically relevant to the current request."""
    request_text = user_request.strip()
    response_text = partner_response.strip()
    if not request_text or not response_text:
        return False

    if not _BRAIN.enabled:
        request_tokens = {token for token in request_text.split() if len(token) > 1}
        if request_tokens and any(token in response_text for token in request_tokens):
            return True
        request_chars = {ch for ch in request_text if ch.strip()}
        response_chars = {ch for ch in response_text if ch.strip()}
        if not request_chars:
            return False
        overlap = len(request_chars & response_chars) / max(len(request_chars), 1)
        return overlap >= 0.2

    history = MEMORY.get_history(conversation_key) if conversation_key else []
    prompt = (
        "判断远端回答是否与当前用户请求语义相关。\n"
        "只输出JSON：{\"relevant\": true|false}\n"
        "判断标准：主题一致、能直接回应用户请求。"
    )
    payload = json.dumps(
        {"user_request": request_text, "partner_response": response_text},
        ensure_ascii=False,
    )
    try:
        model_output = await _BRAIN.chat(
            payload,
            history=history,
            system_prompt=prompt,
            timeout_seconds=RELEVANCE_TIMEOUT_SECONDS,
        )
        data = _extract_json_object(model_output)
        return bool(data.get("relevant", False))
    except Exception:
        return False


async def build_chat_answer(
    user_text: str,
    *,
    conversation_key: str | None = None,
) -> str:
    """Generate a reply with model-first and fallback-second behavior."""
    text = user_text.strip()
    if not text:
        return "请输入文本内容。"

    history = MEMORY.get_history(conversation_key) if conversation_key else []

    if _BRAIN.enabled:
        try:
            answer = await _BRAIN.chat(text, history=history)
            if conversation_key:
                MEMORY.append_exchange(conversation_key, text, answer)
            return answer
        except Exception:
            answer = fallback_answer(text)
            if conversation_key:
                MEMORY.append_exchange(conversation_key, text, answer)
            return answer

    answer = fallback_answer(text)
    if conversation_key:
        MEMORY.append_exchange(conversation_key, text, answer)
    return answer
