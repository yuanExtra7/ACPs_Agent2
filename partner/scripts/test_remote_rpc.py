from __future__ import annotations

import argparse
import json
import ssl
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib import request


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_command(
    *,
    command: str,
    task_id: str,
    session_id: str,
    sender_id: str,
    data_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cmd: dict[str, Any] = {
        "type": "task-command",
        "id": f"msg-{uuid.uuid4()}",
        "sentAt": now_iso(),
        "senderRole": "leader",
        "senderId": sender_id,
        "command": command,
        "taskId": task_id,
        "sessionId": session_id,
    }
    if data_items is not None:
        cmd["dataItems"] = data_items
    return cmd


def rpc_call(
    *,
    rpc_url: str,
    command: dict[str, Any],
    timeout: int,
    insecure: bool,
) -> dict[str, Any]:
    body = {
        "jsonrpc": "2.0",
        "method": "rpc",
        "id": str(uuid.uuid4()),
        "params": {"command": command},
    }
    data = json.dumps(body).encode("utf-8")
    req = request.Request(
        rpc_url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    context = ssl._create_unverified_context() if insecure else None
    with request.urlopen(req, timeout=timeout, context=context) as resp:
        raw = resp.read().decode("utf-8")
    parsed = json.loads(raw)
    if "error" in parsed and parsed["error"]:
        raise RuntimeError(f"RPC error: {parsed['error']}")
    if "result" not in parsed:
        raise RuntimeError(f"RPC invalid response: {parsed}")
    return parsed["result"]


def state_of(task_result: dict[str, Any]) -> str:
    status = task_result.get("status") or {}
    return str(status.get("state", "unknown"))


def print_step(title: str, task_result: dict[str, Any]) -> None:
    state = state_of(task_result)
    print(f"[{title}] state={state}")
    products = task_result.get("products") or []
    if products:
        first = products[0]
        print(f"  product={first.get('name') or first.get('id')}")


def ensure_state(task_result: dict[str, Any], expected: set[str], step: str) -> None:
    state = state_of(task_result)
    if state not in expected:
        raise AssertionError(f"{step} expected {expected}, got {state}")


def poll_until_stable(
    *,
    rpc_url: str,
    task_id: str,
    session_id: str,
    sender_id: str,
    timeout: int,
    insecure: bool,
    max_wait_s: int = 20,
) -> dict[str, Any]:
    deadline = time.time() + max_wait_s
    latest: dict[str, Any] | None = None
    while time.time() < deadline:
        result = rpc_call(
            rpc_url=rpc_url,
            timeout=timeout,
            insecure=insecure,
            command=build_command(
                command="get",
                task_id=task_id,
                session_id=session_id,
                sender_id=sender_id,
            ),
        )
        latest = result
        state = state_of(result)
        if state not in {"accepted", "working"}:
            return result
        time.sleep(1)
    if latest is None:
        raise TimeoutError("poll failed: no response")
    return latest


def run_happy_path(rpc_url: str, timeout: int, insecure: bool) -> None:
    print("=== Happy Path: start -> get -> continue -> complete ===")
    sender_id = "local-leader-smoke"
    session_id = f"session-{uuid.uuid4()}"
    task_id = f"task-{uuid.uuid4()}"

    start_result = rpc_call(
        rpc_url=rpc_url,
        timeout=timeout,
        insecure=insecure,
        command=build_command(
            command="start",
            task_id=task_id,
            session_id=session_id,
            sender_id=sender_id,
            data_items=[{"type": "text", "text": "请给我一个简短测试回复"}],
        ),
    )
    print_step("start", start_result)

    stable_result = start_result
    if state_of(start_result) in {"accepted", "working"}:
        stable_result = poll_until_stable(
            rpc_url=rpc_url,
            task_id=task_id,
            session_id=session_id,
            sender_id=sender_id,
            timeout=timeout,
            insecure=insecure,
        )
        print_step("poll/get", stable_result)
    ensure_state(
        stable_result,
        {"awaiting-input", "awaiting-completion", "rejected", "failed"},
        "start->stable",
    )

    get_result = rpc_call(
        rpc_url=rpc_url,
        timeout=timeout,
        insecure=insecure,
        command=build_command(
            command="get",
            task_id=task_id,
            session_id=session_id,
            sender_id=sender_id,
        ),
    )
    print_step("get", get_result)

    continue_result = rpc_call(
        rpc_url=rpc_url,
        timeout=timeout,
        insecure=insecure,
        command=build_command(
            command="continue",
            task_id=task_id,
            session_id=session_id,
            sender_id=sender_id,
            data_items=[{"type": "text", "text": "请补充一句：这是 continue 测试"}],
        ),
    )
    print_step("continue", continue_result)
    ensure_state(continue_result, {"awaiting-completion", "awaiting-input"}, "continue")

    complete_result = rpc_call(
        rpc_url=rpc_url,
        timeout=timeout,
        insecure=insecure,
        command=build_command(
            command="complete",
            task_id=task_id,
            session_id=session_id,
            sender_id=sender_id,
        ),
    )
    print_step("complete", complete_result)
    ensure_state(complete_result, {"completed"}, "complete")


def run_reject_case(rpc_url: str, timeout: int, insecure: bool) -> None:
    print("=== Reject Case: non-text start ===")
    sender_id = "local-leader-smoke"
    session_id = f"session-{uuid.uuid4()}"
    task_id = f"task-{uuid.uuid4()}"

    reject_result = rpc_call(
        rpc_url=rpc_url,
        timeout=timeout,
        insecure=insecure,
        command=build_command(
            command="start",
            task_id=task_id,
            session_id=session_id,
            sender_id=sender_id,
            data_items=[{"type": "data", "data": {"hello": "world"}}],
        ),
    )
    print_step("start(non-text)", reject_result)
    ensure_state(reject_result, {"rejected", "awaiting-input"}, "non-text-start")


def main() -> None:
    parser = argparse.ArgumentParser(description="A2 direct RPC smoke test for remote Partner")
    parser.add_argument("--base-url", default="http://113.47.5.136:5000", help="Partner base URL")
    parser.add_argument("--rpc-path", default="/rpc", help="RPC path")
    parser.add_argument("--timeout", type=int, default=20, help="HTTP timeout seconds")
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verification (for temporary HTTPS self-signed testing)",
    )
    args = parser.parse_args()

    rpc_url = args.base_url.rstrip("/") + args.rpc_path
    print(f"RPC URL: {rpc_url}")
    run_happy_path(rpc_url, args.timeout, args.insecure)
    run_reject_case(rpc_url, args.timeout, args.insecure)
    print("A2 smoke test: OK")


if __name__ == "__main__":
    main()

