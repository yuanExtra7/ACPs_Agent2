"""Human-facing chat page and API."""

from __future__ import annotations

from datetime import datetime
from time import perf_counter
import re
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .chat_service import (
    build_chat_answer,
    decide_human_action,
    is_partner_response_relevant,
    postprocess_collaboration_result,
)
from .leader import leader_complete_task, leader_continue_task, leader_get_task, leader_start_task
from .memory import MEMORY, SESSION_STATES, SessionRuntimeState
from .settings import HUMAN_TOTAL_BUDGET_SECONDS, LEADER_AIC, LEADER_CALL_TIMEOUT_SECONDS

router = APIRouter(prefix="/human", tags=["human-chat"])


class HumanChatRequest(BaseModel):
    text: str = Field(..., min_length=1, description="User input text")
    session_id: str | None = Field(default=None, description="Conversation id for memory continuity")
    rpc_url: str | None = Field(default=None, description="Optional target Partner RPC URL")


_RPC_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
TERMINAL_STATES = {"completed", "failed", "rejected", "canceled"}
ACTIVE_STATES = {"accepted", "working", "awaiting-input", "awaiting-completion"}
ALL_STATES = TERMINAL_STATES | ACTIVE_STATES


def _extract_rpc_url(text: str) -> str:
    match = _RPC_URL_RE.search(text)
    if not match:
        return ""
    return match.group(0).strip().rstrip(".,;)]}")


def _remove_url(text: str, url: str) -> str:
    if not url:
        return text
    return text.replace(url, " ").strip()


def _resolve_answer_from_leader(result: dict[str, object]) -> str:
    product_texts = result.get("product_texts") or []
    status_texts = result.get("status_texts") or []
    if product_texts:
        return str(product_texts[0])
    if status_texts:
        return str(status_texts[0])
    final_state = str(result.get("final_state", "unknown"))
    return f"协作已执行，当前任务状态：{final_state}"


def _normalize_state(state: str) -> str:
    return state.strip().lower().replace("_", "-")


def _session_valid_for_active_task(state: SessionRuntimeState) -> bool:
    if not state.active_task_id:
        return True
    normalized = _normalize_state(state.last_state)
    if normalized not in ALL_STATES:
        return False
    if normalized in TERMINAL_STATES:
        return False
    if not state.aip_session_id:
        return False
    return True


def _collaboration_payload(
    state: SessionRuntimeState,
    *,
    trace: list[dict[str, object]] | None = None,
    recovery_hint: str = "",
    error: str = "",
    phase: str = "",
    timings_ms: dict[str, int] | None = None,
    retryable: bool = False,
) -> dict[str, object]:
    return {
        "state": state.last_state,
        "taskId": state.active_task_id,
        "effectiveRpcUrl": state.rpc_url,
        "aipSessionId": state.aip_session_id,
        "partnerSenderId": state.partner_sender_id,
        "trace": trace or [],
        "recoveryHint": recovery_hint,
        "error": error,
        "phase": phase,
        "timingsMs": timings_ms or {},
        "retryable": retryable,
    }


def _leader_binding_ok(
    *,
    state: SessionRuntimeState,
    leader_result: dict[str, object],
) -> bool:
    if not bool(leader_result.get("binding_ok", True)):
        return False
    actual_task = str(leader_result.get("actual_task_id", "")).strip()
    actual_session = str(leader_result.get("actual_session_id", "")).strip()
    expected_task = str(leader_result.get("task_id", "")).strip()
    if state.active_task_id and expected_task and state.active_task_id != expected_task:
        return False
    if actual_task and expected_task and actual_task != expected_task:
        return False
    if actual_session and state.aip_session_id and actual_session != state.aip_session_id:
        return False
    return True


def _rebuild_session_state(state: SessionRuntimeState) -> SessionRuntimeState:
    state.active_task_id = ""
    state.last_state = ""
    if not state.aip_session_id:
        state.aip_session_id = f"aip-{uuid4()}"
    return state


def _leader_error_response(
    *,
    state: SessionRuntimeState,
    session_id: str,
    answer: str,
    memory_turns: int,
    recovery_hint: str,
    error: str = "",
    phase: str = "error",
    timings_ms: dict[str, int] | None = None,
    retryable: bool = True,
) -> dict[str, object]:
    return {
        "answer": answer,
        "mode": "leader-error",
        "sessionId": session_id,
        "memoryTurns": memory_turns,
        "receivedAt": datetime.now().isoformat(),
        "collaboration": _collaboration_payload(
            state,
            recovery_hint=recovery_hint,
            error=error,
            phase=phase,
            timings_ms=timings_ms,
            retryable=retryable,
        ),
    }


def _append_chat_exchange_if_new(chat_key: str, user_text: str, answer: str) -> bool:
    history = MEMORY.get_history(chat_key)
    if len(history) >= 2:
        prev_user = history[-2]
        prev_assistant = history[-1]
        if (
            prev_user.get("role") == "user"
            and prev_assistant.get("role") == "assistant"
            and (prev_user.get("content") or "").strip() != user_text.strip()
            and (prev_assistant.get("content") or "").strip() == answer.strip()
        ):
            return False
    MEMORY.append_exchange(chat_key, user_text, answer)
    return True


def _remaining_budget(start_total: float) -> float:
    elapsed = perf_counter() - start_total
    return max(0.0, HUMAN_TOTAL_BUDGET_SECONDS - elapsed)


def _call_proof_for_failure(*, state: SessionRuntimeState, reason: str) -> dict[str, object]:
    return {
        "invoked": False,
        "request_id": f"proof-{uuid4()}",
        "session_id": state.aip_session_id,
        "task_id": state.active_task_id,
        "trace_steps": [],
        "source_text_hash": "",
        "reason": reason,
    }


def _proof_allows_remote_claim(call_proof: dict[str, object]) -> bool:
    if not bool(call_proof.get("invoked", False)):
        return False
    steps = [str(step) for step in call_proof.get("trace_steps", [])]
    if not steps:
        return False
    has_start = "start" in steps
    has_followup = any(step in {"get", "continue", "complete"} for step in steps)
    return has_start or has_followup


def _merge_session_state(
    state: SessionRuntimeState,
    *,
    rpc_url: str,
    aip_session_id: str,
    leader_result: dict[str, object],
) -> SessionRuntimeState:
    final_state = str(leader_result.get("final_state", "")).strip()
    active_task_id = str(leader_result.get("task_id", "")).strip()
    if final_state.lower() in TERMINAL_STATES:
        active_task_id = ""
    state.rpc_url = rpc_url
    state.aip_session_id = aip_session_id
    state.active_task_id = active_task_id
    state.last_state = final_state
    state.partner_sender_id = str(leader_result.get("partner_sender_id", "")).strip()
    return state


@router.get("", response_class=HTMLResponse)
async def human_chat_page() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ACPs 智能体对话</title>
  <style>
    :root {
      --bg: #f3f5f9;
      --panel: #ffffff;
      --line: #dfe4ea;
      --text: #1f2a37;
      --subtext: #637083;
      --brand: #2357d9;
      --brand-soft: #e8efff;
      --user: #2f8cff;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: linear-gradient(180deg, #f8faff 0%, var(--bg) 100%);
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      color: var(--text);
      display: flex;
      justify-content: center;
      padding: 24px;
    }
    .layout {
      width: min(1080px, 100%);
      height: calc(100vh - 48px);
      min-height: 640px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 14px;
      box-shadow: 0 10px 30px rgba(18, 36, 73, 0.08);
      display: grid;
      grid-template-columns: 280px 1fr;
      overflow: hidden;
    }
    .sidebar {
      border-right: 1px solid var(--line);
      padding: 18px 16px;
      background: #fbfcff;
    }
    .brand { font-size: 18px; font-weight: 600; margin: 0 0 16px; }
    .meta {
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 12px;
      background: #fff;
      margin-bottom: 12px;
    }
    .meta-title { font-size: 13px; color: var(--subtext); margin: 0 0 8px; }
    .meta-value { font-size: 14px; margin: 0; }
    .chat-wrap {
      display: grid;
      grid-template-rows: auto 1fr auto;
      height: 100%;
      min-height: 0;
    }
    .chat-header {
      padding: 16px 20px;
      border-bottom: 1px solid var(--line);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .chat-title { margin: 0; font-size: 16px; font-weight: 600; }
    .status {
      color: #0f8a55;
      background: #e9fbf2;
      border: 1px solid #bce9d4;
      border-radius: 999px;
      font-size: 12px;
      padding: 4px 10px;
    }
    .messages {
      padding: 18px 20px;
      overflow-y: auto;
      background: #f8fafc;
      display: flex;
      flex-direction: column;
      gap: 12px;
      min-height: 0;
      overscroll-behavior: contain;
    }
    .msg { display: flex; width: 100%; }
    .msg.user { justify-content: flex-end; }
    .bubble {
      max-width: 84%;
      border-radius: 12px;
      padding: 11px 14px;
      line-height: 1.5;
      font-size: 14px;
      white-space: pre-wrap;
      word-break: normal;
      overflow-wrap: anywhere;
      border: 1px solid transparent;
    }
    .msg.user .bubble { max-width: 88%; background: var(--user); color: #fff; }
    .msg.agent .bubble {
      background: #fff;
      color: var(--text);
      border-color: var(--line);
    }
    .msg-time {
      margin-top: 4px;
      font-size: 11px;
      color: #8a95a7;
      text-align: right;
    }
    .composer {
      border-top: 1px solid var(--line);
      padding: 14px 16px;
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      background: #fff;
    }
    textarea {
      resize: vertical;
      min-height: 120px;
      max-height: 260px;
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 12px 14px;
      font-size: 14px;
      font-family: inherit;
      outline: none;
      width: 100%;
    }
    textarea:focus { border-color: #9cb6ff; box-shadow: 0 0 0 3px #edf2ff; }
    button {
      align-self: end;
      border: none;
      border-radius: 10px;
      background: var(--brand);
      color: #fff;
      height: 42px;
      min-width: 96px;
      font-size: 14px;
      cursor: pointer;
    }
    button:disabled { opacity: 0.6; cursor: not-allowed; }
    .hint { font-size: 12px; color: var(--subtext); margin-top: 6px; }
    .runtime-info {
      margin-top: 8px;
      padding: 8px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f7f9fc;
      font-size: 12px;
      color: var(--subtext);
      line-height: 1.6;
    }
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <h1 class="brand">ACPs 智能体助手</h1>
      <section class="meta">
        <p class="meta-title">模式</p>
        <p class="meta-value">用户 <-> 智能体 直接对话</p>
      </section>
      <section class="meta">
        <p class="meta-title">输入限制</p>
        <p class="meta-value">仅支持文本输入</p>
      </section>
      <section class="meta">
        <p class="meta-title">接口</p>
        <p class="meta-value">POST /human/chat</p>
      </section>
      <section class="meta">
        <p class="meta-title">远端 RPC（可选）</p>
        <input id="rpcUrl" placeholder="http://host:port/rpc" style="width: 100%; border: 1px solid #dfe4ea; border-radius: 8px; padding: 8px 10px; font-size: 13px;" />
        <p class="hint" style="margin: 8px 0 0;">填写后将走 Leader 实际调用远端智能体。</p>
        <div id="runtimeInfo" class="runtime-info">
          mode: local-chat<br/>
          task: -<br/>
          state: -<br/>
          phase: -<br/>
          rpc: -<br/>
          timings: -
        </div>
      </section>
    </aside>
    <main class="chat-wrap">
      <header class="chat-header">
        <h2 class="chat-title">商务咨询对话</h2>
        <span class="status">在线</span>
      </header>
      <section id="messages" class="messages"></section>
      <footer class="composer">
        <div>
          <textarea id="input" placeholder="请输入问题，按 Enter 发送（Shift+Enter 换行）"></textarea>
          <div class="hint">已启用服务端会话记忆（进程内存，服务重启后会清空）。</div>
        </div>
        <button id="sendBtn">发送</button>
      </footer>
    </main>
  </div>
  <script>
    const messagesEl = document.getElementById("messages");
    const inputEl = document.getElementById("input");
    const sendBtn = document.getElementById("sendBtn");
    const rpcUrlEl = document.getElementById("rpcUrl");
    const runtimeInfoEl = document.getElementById("runtimeInfo");
    const sessionId = (window.crypto && crypto.randomUUID)
      ? crypto.randomUUID()
      : `session-${Date.now()}-${Math.random().toString(16).slice(2)}`;

    function nowText() {
      return new Date().toLocaleTimeString("zh-CN", { hour12: false });
    }

    function appendMessage(role, text) {
      const row = document.createElement("div");
      row.className = `msg ${role}`;
      const bubbleWrap = document.createElement("div");
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      bubble.textContent = text;
      const time = document.createElement("div");
      time.className = "msg-time";
      time.textContent = nowText();
      bubbleWrap.appendChild(bubble);
      bubbleWrap.appendChild(time);
      row.appendChild(bubbleWrap);
      messagesEl.appendChild(row);
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function renderRuntimeInfo(data) {
      const collab = data.collaboration || {};
      const mode = data.mode || "-";
      const taskId = collab.taskId || "-";
      const state = collab.state || "-";
      const phase = collab.phase || "-";
      const rpc = collab.effectiveRpcUrl || "-";
      const recoveryHint = collab.recoveryHint || "-";
      const error = collab.error || "-";
      const timings = collab.timingsMs || {};
      const timingText = Object.entries(timings).map(([k, v]) => `${k}:${v}ms`).join(", ") || "-";
      runtimeInfoEl.innerHTML = `mode: ${mode}<br/>task: ${taskId}<br/>state: ${state}<br/>phase: ${phase}<br/>rpc: ${rpc}<br/>timings: ${timingText}<br/>recovery: ${recoveryHint}<br/>error: ${error}`;
      if (collab.effectiveRpcUrl && !rpcUrlEl.value.trim()) {
        rpcUrlEl.value = collab.effectiveRpcUrl;
      }
    }

    async function sendMessage() {
      const text = inputEl.value.trim();
      if (!text) return;
      inputEl.value = "";
      appendMessage("user", text);
      sendBtn.disabled = true;
      runtimeInfoEl.innerHTML = "mode: pending<br/>task: -<br/>state: -<br/>phase: calling<br/>rpc: -<br/>timings: -<br/>recovery: -<br/>error: -";
      try {
        const resp = await fetch("/human/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            text,
            session_id: sessionId,
            rpc_url: (rpcUrlEl.value || "").trim() || null
          })
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        appendMessage("agent", data.answer || "未返回有效回复");
        renderRuntimeInfo(data);
      } catch (err) {
        appendMessage("agent", `请求失败：${err.message}`);
      } finally {
        sendBtn.disabled = false;
        inputEl.focus();
      }
    }

    sendBtn.addEventListener("click", sendMessage);
    inputEl.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    appendMessage("agent", "你好，我是文本咨询智能体。请直接输入你的问题。");
    inputEl.focus();
  </script>
</body>
</html>
"""


@router.post("/chat")
async def human_chat(payload: HumanChatRequest) -> dict[str, object]:
    start_total = perf_counter()
    timings_ms: dict[str, int] = {}
    session_id = (payload.session_id or "").strip() or f"session-{uuid4()}"
    text = payload.text.strip()
    if not text:
        return {
            "answer": "请输入文本内容。",
            "mode": "local-chat",
            "sessionId": session_id,
            "memoryTurns": 0,
            "receivedAt": datetime.now().isoformat(),
            "collaboration": {"state": "", "taskId": "", "effectiveRpcUrl": ""},
        }

    chat_key = f"human-chat:{session_id}"
    router_key = f"human-router:{session_id}"
    state = SESSION_STATES.get(session_id)
    payload_rpc = (payload.rpc_url or "").strip()
    extracted_rpc = _extract_rpc_url(text)
    rpc_url = payload_rpc or extracted_rpc or state.rpc_url
    if rpc_url:
        state.rpc_url = rpc_url
    if not state.aip_session_id:
        state.aip_session_id = f"aip-{uuid4()}"
    if not _session_valid_for_active_task(state):
        state = _rebuild_session_state(state)

    if _remaining_budget(start_total) <= 0:
        SESSION_STATES.save(state)
        return _leader_error_response(
            state=state,
            session_id=session_id,
            answer="请求超出时延预算，请重试。",
            memory_turns=MEMORY.size(chat_key),
            recovery_hint="请简化请求或稍后重试。",
            phase="timeout",
            timings_ms={"total": int((perf_counter() - start_total) * 1000)},
            retryable=True,
        )

    start_router = perf_counter()
    decision = await decide_human_action(
        text,
        router_history_key=router_key,
        candidate_rpc_url=rpc_url or None,
        has_active_task=bool(state.active_task_id),
        active_task_state=state.last_state,
    )
    timings_ms["routing"] = int((perf_counter() - start_router) * 1000)
    action = decision.get("action", "local_reply")
    received_at = datetime.now().isoformat()

    if action == "local_reply":
        answer = await build_chat_answer(text, conversation_key=chat_key)
        SESSION_STATES.save(state)
        return {
            "answer": answer,
            "mode": "local-chat",
            "sessionId": session_id,
            "memoryTurns": MEMORY.size(chat_key),
            "receivedAt": received_at,
            "collaboration": _collaboration_payload(
                state,
                phase="local-reply",
                timings_ms={**timings_ms, "total": int((perf_counter() - start_total) * 1000)},
            ),
        }

    if action == "need_rpc_url":
        answer = "请提供可访问的 RPC 地址（http(s)://.../rpc），我才能发起真实调用。"
        SESSION_STATES.save(state)
        return {
            "answer": answer,
            "mode": "rpc-missing-url",
            "sessionId": session_id,
            "memoryTurns": MEMORY.size(chat_key),
            "receivedAt": received_at,
            "collaboration": _collaboration_payload(
                state,
                phase="need-rpc-url",
                timings_ms={**timings_ms, "total": int((perf_counter() - start_total) * 1000)},
            ),
        }

    if not rpc_url:
        answer = "当前没有可用 RPC 地址，请在左侧填写或在消息中提供 http(s)://.../rpc。"
        SESSION_STATES.save(state)
        return {
            "answer": answer,
            "mode": "rpc-missing-url",
            "sessionId": session_id,
            "memoryTurns": MEMORY.size(chat_key),
            "receivedAt": received_at,
            "collaboration": _collaboration_payload(
                state,
                phase="missing-rpc-url",
                timings_ms={**timings_ms, "total": int((perf_counter() - start_total) * 1000)},
            ),
        }

    start_leader = perf_counter()
    try:
        remaining_before_leader = _remaining_budget(start_total)
        if remaining_before_leader <= 0:
            raise TimeoutError("end-to-end budget exhausted before leader call")
        call_timeout = min(LEADER_CALL_TIMEOUT_SECONDS, remaining_before_leader)

        if action == "call_start":
            leader_query = (decision.get("query") or "").strip() or _remove_url(text, rpc_url).strip() or text
            leader_result = await leader_start_task(
                partner_rpc_url=rpc_url,
                leader_id=LEADER_AIC,
                session_id=state.aip_session_id,
                user_input=leader_query,
                task_id=None,
                timeout_seconds=call_timeout,
            )
        elif action == "call_continue":
            task_id = state.active_task_id
            if not task_id:
                leader_query = (decision.get("query") or "").strip() or text
                leader_result = await leader_start_task(
                    partner_rpc_url=rpc_url,
                    leader_id=LEADER_AIC,
                    session_id=state.aip_session_id,
                    user_input=leader_query,
                    timeout_seconds=call_timeout,
                )
            else:
                try:
                    await leader_get_task(
                        partner_rpc_url=rpc_url,
                        leader_id=LEADER_AIC,
                        session_id=state.aip_session_id,
                        task_id=task_id,
                        max_polls=1,
                        timeout_seconds=call_timeout,
                    )
                except Exception:
                    state = _rebuild_session_state(state)
                    leader_query = (decision.get("query") or "").strip() or text
                    leader_result = await leader_start_task(
                        partner_rpc_url=rpc_url,
                        leader_id=LEADER_AIC,
                        session_id=state.aip_session_id,
                        user_input=leader_query,
                        timeout_seconds=call_timeout,
                    )
                else:
                    leader_query = (decision.get("query") or "").strip() or text
                    leader_result = await leader_continue_task(
                        partner_rpc_url=rpc_url,
                        leader_id=LEADER_AIC,
                        session_id=state.aip_session_id,
                        task_id=task_id,
                        continue_input=leader_query,
                        timeout_seconds=call_timeout,
                    )
        elif action == "call_complete":
            task_id = state.active_task_id
            if not task_id:
                SESSION_STATES.save(state)
                return _leader_error_response(
                    state=state,
                    session_id=session_id,
                    answer="当前没有待完成任务，建议先发起协作或重建任务。",
                    memory_turns=MEMORY.size(chat_key),
                    recovery_hint="请先给出协作问题以创建任务。",
                )
            leader_result = await leader_complete_task(
                partner_rpc_url=rpc_url,
                leader_id=LEADER_AIC,
                session_id=state.aip_session_id,
                task_id=task_id,
                timeout_seconds=call_timeout,
            )
        else:  # call_get
            task_id = state.active_task_id
            if not task_id:
                leader_query = (decision.get("query") or "").strip() or text
                leader_result = await leader_start_task(
                    partner_rpc_url=rpc_url,
                    leader_id=LEADER_AIC,
                    session_id=state.aip_session_id,
                    user_input=leader_query,
                    timeout_seconds=call_timeout,
                )
            else:
                leader_result = await leader_get_task(
                    partner_rpc_url=rpc_url,
                    leader_id=LEADER_AIC,
                    session_id=state.aip_session_id,
                    task_id=task_id,
                    timeout_seconds=call_timeout,
                )
    except Exception as exc:
        timings_ms["leader"] = int((perf_counter() - start_leader) * 1000)
        state = _rebuild_session_state(state)
        SESSION_STATES.save(state)
        phase = "timeout" if isinstance(exc, TimeoutError) else "leader-call"
        hint = "远端较慢，请稍后重试或简化问题。" if isinstance(exc, TimeoutError) else "请检查RPC地址可达性，或再次发送问题触发任务重建。"
        return _leader_error_response(
            state=state,
            session_id=session_id,
            answer="远端协作调用失败，请稍后重试。",
            memory_turns=MEMORY.size(chat_key),
            recovery_hint=hint,
            error=str(exc),
            phase=phase,
            timings_ms={**timings_ms, "total": int((perf_counter() - start_total) * 1000)},
        )
    timings_ms["leader"] = int((perf_counter() - start_leader) * 1000)

    if not _leader_binding_ok(state=state, leader_result=leader_result):
        state = _rebuild_session_state(state)
        SESSION_STATES.save(state)
        return _leader_error_response(
            state=state,
            session_id=session_id,
            answer="检测到远端任务绑定异常，已自动清空当前任务状态。",
            memory_turns=MEMORY.size(chat_key),
            recovery_hint="请重新发送问题以创建新任务。",
            error="leader result binding mismatch",
            phase="binding-check",
            timings_ms={**timings_ms, "total": int((perf_counter() - start_total) * 1000)},
        )

    call_proof = leader_result.get("call_proof") or _call_proof_for_failure(state=state, reason="missing-proof")
    if not _proof_allows_remote_claim(call_proof):
        state = _rebuild_session_state(state)
        SESSION_STATES.save(state)
        return _leader_error_response(
            state=state,
            session_id=session_id,
            answer="当前未取得有效远端调用证据，已阻止不可靠交付。",
            memory_turns=MEMORY.size(chat_key),
            recovery_hint="请重试协作请求，确认远端服务可达。",
            error="call-proof-invalid",
            phase="truth-guard",
            timings_ms={**timings_ms, "total": int((perf_counter() - start_total) * 1000)},
            retryable=True,
        )

    raw_partner_answer = _resolve_answer_from_leader(leader_result)
    remaining_for_relevance = _remaining_budget(start_total)
    if remaining_for_relevance > 1.0:
        relevant = await is_partner_response_relevant(
            user_request=text,
            partner_response=raw_partner_answer,
            conversation_key=chat_key,
        )
    else:
        relevant = True

    if not relevant:
        retry_query = f"请只回答这个问题，不要复述旧主题：{text}"
        remaining_retry = _remaining_budget(start_total)
        if remaining_retry <= 2.0:
            state = _rebuild_session_state(state)
            SESSION_STATES.save(state)
            return _leader_error_response(
                state=state,
                session_id=session_id,
                answer="远端响应与问题不相关，且本次预算不足以重试。",
                memory_turns=MEMORY.size(chat_key),
                recovery_hint="请重试一次，系统将重新发起协作。",
                error="offtopic-response-no-budget",
                phase="offtopic-recovery",
                timings_ms={**timings_ms, "total": int((perf_counter() - start_total) * 1000)},
            )
        retry_timeout = min(LEADER_CALL_TIMEOUT_SECONDS, remaining_retry)
        try:
            retry_result = await leader_start_task(
                partner_rpc_url=rpc_url,
                leader_id=LEADER_AIC,
                session_id=state.aip_session_id,
                user_input=retry_query,
                task_id=None,
                timeout_seconds=retry_timeout,
            )
        except Exception as exc:
            state = _rebuild_session_state(state)
            SESSION_STATES.save(state)
            return _leader_error_response(
                state=state,
                session_id=session_id,
                answer="离题恢复重试失败，已停止当前协作。",
                memory_turns=MEMORY.size(chat_key),
                recovery_hint="请稍后重试或更换远端地址。",
                error=str(exc),
                phase="offtopic-recovery",
                timings_ms={**timings_ms, "total": int((perf_counter() - start_total) * 1000)},
            )
        retry_proof = retry_result.get("call_proof") or _call_proof_for_failure(state=state, reason="retry-missing-proof")
        if not _proof_allows_remote_claim(retry_proof):
            state = _rebuild_session_state(state)
            SESSION_STATES.save(state)
            return _leader_error_response(
                state=state,
                session_id=session_id,
                answer="重试后仍未获得有效远端调用证据。",
                memory_turns=MEMORY.size(chat_key),
                recovery_hint="请检查远端状态后再试。",
                error="offtopic-retry-proof-invalid",
                phase="offtopic-recovery",
                timings_ms={**timings_ms, "total": int((perf_counter() - start_total) * 1000)},
            )
        retry_answer = _resolve_answer_from_leader(retry_result)
        retry_relevant = await is_partner_response_relevant(
            user_request=text,
            partner_response=retry_answer,
            conversation_key=chat_key,
        )
        if not retry_relevant:
            state = _rebuild_session_state(state)
            SESSION_STATES.save(state)
            return _leader_error_response(
                state=state,
                session_id=session_id,
                answer="远端连续两次返回与当前问题不相关内容，已停止协作输出。",
                memory_turns=MEMORY.size(chat_key),
                recovery_hint="建议更换远端智能体或简化提问后重试。",
                error="offtopic-response-repeated",
                phase="offtopic-recovery",
                timings_ms={**timings_ms, "total": int((perf_counter() - start_total) * 1000)},
            )
        leader_result = retry_result
        call_proof = retry_proof
        raw_partner_answer = retry_answer

    state = _merge_session_state(
        state,
        rpc_url=rpc_url,
        aip_session_id=state.aip_session_id,
        leader_result=leader_result,
    )
    SESSION_STATES.save(state)
    start_post = perf_counter()
    if _remaining_budget(start_total) <= 0:
        return _leader_error_response(
            state=state,
            session_id=session_id,
            answer="协作进入后处理前已超时，请重试。",
            memory_turns=MEMORY.size(chat_key),
            recovery_hint="可缩短问题或稍后重试。",
            error="postprocess-budget-timeout",
            phase="timeout",
            timings_ms={**timings_ms, "total": int((perf_counter() - start_total) * 1000)},
        )
    answer = await postprocess_collaboration_result(
        user_request=text,
        partner_response=raw_partner_answer,
        leader_result=leader_result,
        call_proof=call_proof,
        conversation_key=chat_key,
    )
    timings_ms["postprocess"] = int((perf_counter() - start_post) * 1000)
    appended = _append_chat_exchange_if_new(chat_key, text, answer)
    if not appended:
        state = _rebuild_session_state(state)
        SESSION_STATES.save(state)
        return _leader_error_response(
            state=state,
            session_id=session_id,
            answer="检测到重复结果写回，已自动重建任务状态，请重试。",
            memory_turns=MEMORY.size(chat_key),
            recovery_hint="请重发你的请求，系统会创建新任务。",
            error="duplicate-stale-answer-detected",
            timings_ms={**timings_ms, "total": int((perf_counter() - start_total) * 1000)},
        )
    return {
        "answer": answer,
        "mode": "leader-proxy",
        "sessionId": session_id,
        "memoryTurns": MEMORY.size(chat_key),
        "receivedAt": received_at,
        "collaboration": _collaboration_payload(
            state,
            trace=leader_result.get("trace", []),
            phase="post-processed",
            timings_ms={**timings_ms, "total": int((perf_counter() - start_total) * 1000)},
        ),
    }
