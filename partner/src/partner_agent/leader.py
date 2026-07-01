"""Leader capability: call Partner agents via AIP RPC with task lifecycle control."""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from uuid import uuid4

from acps_sdk.aip.aip_base_model import TaskState, TextDataItem
from acps_sdk.aip.aip_rpc_client import AipRpcClient

from .memory import MEMORY
from .settings import LEADER_CALL_TIMEOUT_SECONDS, LEADER_MAX_POLLS, LEADER_POLL_SECONDS

POLLING_STATES = {TaskState.Accepted, TaskState.Working}


def _state_name(state: object) -> str:
    value = getattr(state, "value", None)
    if isinstance(value, str):
        return value
    return str(state)


def _extract_text_items(items: object) -> list[str]:
    texts: list[str] = []
    for item in items or []:
        if isinstance(item, TextDataItem):
            if item.text:
                texts.append(item.text)
            continue
        item_type = getattr(item, "type", None)
        item_text = getattr(item, "text", None)
        if item_type == "text" and isinstance(item_text, str) and item_text:
            texts.append(item_text)
            continue
        if isinstance(item, dict) and item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str) and text:
                texts.append(text)
    return texts


def _snapshot_task(step: str, task: object) -> dict[str, object]:
    status = getattr(task, "status", SimpleNamespace(state="unknown", dataItems=[]))
    state = getattr(status, "state", "unknown")
    status_texts = _extract_text_items(getattr(status, "dataItems", []))
    product_texts: list[str] = []
    for product in getattr(task, "products", []) or []:
        product_texts.extend(_extract_text_items(getattr(product, "dataItems", [])))
    return {
        "step": step,
        "state": _state_name(state),
        "status_texts": status_texts,
        "product_texts": product_texts,
    }


async def _poll_until_stable(
    *,
    client: AipRpcClient,
    task_id: str,
    session_id: str,
    poll_seconds: float,
    max_polls: int,
    trace: list[dict[str, object]],
    timeout_seconds: float,
) -> tuple[object, bool]:
    polls = 0
    task = await asyncio.wait_for(
        client.get_task(task_id=task_id, session_id=session_id),
        timeout=timeout_seconds,
    )
    trace.append(_snapshot_task("get", task))
    while task.status.state in POLLING_STATES and polls < max_polls:
        await asyncio.sleep(poll_seconds)
        task = await asyncio.wait_for(
            client.get_task(task_id=task_id, session_id=session_id),
            timeout=timeout_seconds,
        )
        trace.append(_snapshot_task("get", task))
        polls += 1
    return task, task.status.state in POLLING_STATES


def _build_result(
    *,
    task: object,
    trace: list[dict[str, object]],
    leader_id: str,
    partner_rpc_url: str,
    session_id: str,
    task_id: str,
    timed_out: bool,
    memory_key: str,
) -> dict[str, object]:
    product_texts = [
        text
        for product in getattr(task, "products", []) or []
        for text in _extract_text_items(getattr(product, "dataItems", []))
    ]
    status_texts = _extract_text_items(getattr(task.status, "dataItems", []))
    actual_task_id = str(getattr(task, "taskId", "") or "")
    actual_session_id = str(getattr(task, "sessionId", "") or "")
    task_id_match = (not actual_task_id) or actual_task_id == task_id
    session_id_match = (not actual_session_id) or actual_session_id == session_id
    source_text = (product_texts[0] if product_texts else "") or (status_texts[0] if status_texts else "")
    source_hash = hashlib.sha256(source_text.encode("utf-8")).hexdigest() if source_text else ""
    trace_steps = [str(step.get("step", "")) for step in trace if step.get("step")]
    return {
        "leader_id": leader_id,
        "partner_rpc_url": partner_rpc_url,
        "session_id": session_id,
        "task_id": task_id,
        "timed_out": timed_out,
        "final_state": _state_name(task.status.state),
        "partner_sender_id": getattr(task, "senderId", "") or "",
        "status_texts": status_texts,
        "product_texts": product_texts,
        "trace": trace,
        "memory_turns": MEMORY.size(memory_key),
        "actual_task_id": actual_task_id,
        "actual_session_id": actual_session_id,
        "binding_ok": task_id_match and session_id_match,
        "call_proof": {
            "invoked": True,
            "request_id": f"proof-{uuid4()}",
            "session_id": session_id,
            "task_id": task_id,
            "trace_steps": trace_steps,
            "source_text_hash": source_hash,
        },
    }


async def leader_start_task(
    *,
    partner_rpc_url: str,
    leader_id: str,
    session_id: str,
    user_input: str,
    task_id: str | None = None,
    poll_seconds: float = LEADER_POLL_SECONDS,
    max_polls: int = LEADER_MAX_POLLS,
    timeout_seconds: float = LEADER_CALL_TIMEOUT_SECONDS,
) -> dict[str, object]:
    actual_task_id = task_id or f"task-{uuid4()}"
    trace: list[dict[str, object]] = []
    memory_key = f"leader:{leader_id}:{partner_rpc_url}:{session_id}"
    MEMORY.append(memory_key, "user", user_input)
    client = AipRpcClient(partner_url=partner_rpc_url, leader_id=leader_id)
    try:
        task = await asyncio.wait_for(
            client.start_task(session_id=session_id, task_id=actual_task_id, user_input=user_input),
            timeout=timeout_seconds,
        )
        trace.append(_snapshot_task("start", task))
        if task.status.state in POLLING_STATES:
            task, timed_out = await _poll_until_stable(
                client=client,
                task_id=actual_task_id,
                session_id=session_id,
                poll_seconds=poll_seconds,
                max_polls=max_polls,
                trace=trace,
                timeout_seconds=timeout_seconds,
            )
        else:
            timed_out = False
        return _build_result(
            task=task,
            trace=trace,
            leader_id=leader_id,
            partner_rpc_url=partner_rpc_url,
            session_id=session_id,
            task_id=actual_task_id,
            timed_out=timed_out,
            memory_key=memory_key,
        )
    finally:
        await client.close()


async def leader_get_task(
    *,
    partner_rpc_url: str,
    leader_id: str,
    session_id: str,
    task_id: str,
    poll_seconds: float = LEADER_POLL_SECONDS,
    max_polls: int = LEADER_MAX_POLLS,
    timeout_seconds: float = LEADER_CALL_TIMEOUT_SECONDS,
) -> dict[str, object]:
    trace: list[dict[str, object]] = []
    memory_key = f"leader:{leader_id}:{partner_rpc_url}:{session_id}"
    client = AipRpcClient(partner_url=partner_rpc_url, leader_id=leader_id)
    try:
        task = await asyncio.wait_for(
            client.get_task(task_id=task_id, session_id=session_id),
            timeout=timeout_seconds,
        )
        trace.append(_snapshot_task("get", task))
        if task.status.state in POLLING_STATES:
            task, timed_out = await _poll_until_stable(
                client=client,
                task_id=task_id,
                session_id=session_id,
                poll_seconds=poll_seconds,
                max_polls=max_polls,
                trace=trace,
                timeout_seconds=timeout_seconds,
            )
        else:
            timed_out = False
        return _build_result(
            task=task,
            trace=trace,
            leader_id=leader_id,
            partner_rpc_url=partner_rpc_url,
            session_id=session_id,
            task_id=task_id,
            timed_out=timed_out,
            memory_key=memory_key,
        )
    finally:
        await client.close()


async def leader_continue_task(
    *,
    partner_rpc_url: str,
    leader_id: str,
    session_id: str,
    task_id: str,
    continue_input: str,
    poll_seconds: float = LEADER_POLL_SECONDS,
    max_polls: int = LEADER_MAX_POLLS,
    timeout_seconds: float = LEADER_CALL_TIMEOUT_SECONDS,
) -> dict[str, object]:
    trace: list[dict[str, object]] = []
    memory_key = f"leader:{leader_id}:{partner_rpc_url}:{session_id}"
    MEMORY.append(memory_key, "user", continue_input)
    client = AipRpcClient(partner_url=partner_rpc_url, leader_id=leader_id)
    try:
        task = await asyncio.wait_for(
            client.continue_task(task_id=task_id, session_id=session_id, user_input=continue_input),
            timeout=timeout_seconds,
        )
        trace.append(_snapshot_task("continue", task))
        if task.status.state in POLLING_STATES:
            task, timed_out = await _poll_until_stable(
                client=client,
                task_id=task_id,
                session_id=session_id,
                poll_seconds=poll_seconds,
                max_polls=max_polls,
                trace=trace,
                timeout_seconds=timeout_seconds,
            )
        else:
            timed_out = False
        return _build_result(
            task=task,
            trace=trace,
            leader_id=leader_id,
            partner_rpc_url=partner_rpc_url,
            session_id=session_id,
            task_id=task_id,
            timed_out=timed_out,
            memory_key=memory_key,
        )
    finally:
        await client.close()


async def leader_complete_task(
    *,
    partner_rpc_url: str,
    leader_id: str,
    session_id: str,
    task_id: str,
    timeout_seconds: float = LEADER_CALL_TIMEOUT_SECONDS,
) -> dict[str, object]:
    trace: list[dict[str, object]] = []
    memory_key = f"leader:{leader_id}:{partner_rpc_url}:{session_id}"
    client = AipRpcClient(partner_url=partner_rpc_url, leader_id=leader_id)
    try:
        task = await asyncio.wait_for(
            client.complete_task(task_id=task_id, session_id=session_id),
            timeout=timeout_seconds,
        )
        trace.append(_snapshot_task("complete", task))
        return _build_result(
            task=task,
            trace=trace,
            leader_id=leader_id,
            partner_rpc_url=partner_rpc_url,
            session_id=session_id,
            task_id=task_id,
            timed_out=False,
            memory_key=memory_key,
        )
    finally:
        await client.close()


async def run_leader_partner_chat(
    *,
    partner_rpc_url: str,
    leader_id: str,
    user_input: str,
    continue_input: str | None = None,
    conversation_id: str | None = None,
    poll_seconds: float = LEADER_POLL_SECONDS,
    max_polls: int = LEADER_MAX_POLLS,
    auto_complete: bool = True,
) -> dict[str, object]:
    """Backward-compatible wrapper for one-shot workflow."""
    session_id = conversation_id or f"session-{uuid4()}"
    result = await leader_start_task(
        partner_rpc_url=partner_rpc_url,
        leader_id=leader_id,
        session_id=session_id,
        user_input=user_input,
        poll_seconds=poll_seconds,
        max_polls=max_polls,
    )
    task_id = str(result["task_id"])
    state = str(result["final_state"])

    if state == _state_name(TaskState.AwaitingInput) and continue_input:
        result = await leader_continue_task(
            partner_rpc_url=partner_rpc_url,
            leader_id=leader_id,
            session_id=session_id,
            task_id=task_id,
            continue_input=continue_input,
            poll_seconds=poll_seconds,
            max_polls=max_polls,
        )
        state = str(result["final_state"])

    if state == _state_name(TaskState.AwaitingCompletion) and auto_complete:
        result = await leader_complete_task(
            partner_rpc_url=partner_rpc_url,
            leader_id=leader_id,
            session_id=session_id,
            task_id=task_id,
        )

    reply_text = ""
    if result["product_texts"]:
        reply_text = str(result["product_texts"][0])
    elif result["status_texts"]:
        reply_text = str(result["status_texts"][0])
    memory_key = f"leader:{leader_id}:{partner_rpc_url}:{session_id}"
    if reply_text:
        MEMORY.append(memory_key, "assistant", reply_text)
        result["memory_turns"] = MEMORY.size(memory_key)
    return result
