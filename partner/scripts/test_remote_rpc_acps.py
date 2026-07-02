from __future__ import annotations

import argparse
import asyncio
import ssl
import sys
import uuid
from pathlib import Path

# Allow running from workspace without installing wheel globally.
try:
    from acps_sdk.aip.aip_base_model import TaskState, TextDataItem
    from acps_sdk.aip.aip_rpc_client import AipRpcClient
except ModuleNotFoundError:
    sdk_dir = Path(__file__).resolve().parents[2] / "ACPs-community" / "acps-sdk"
    if str(sdk_dir) not in sys.path:
        sys.path.insert(0, str(sdk_dir))
    from acps_sdk.aip.aip_base_model import TaskState, TextDataItem
    from acps_sdk.aip.aip_rpc_client import AipRpcClient


def _extract_text_items(items) -> list[str]:
    texts: list[str] = []
    for item in items or []:
        if isinstance(item, TextDataItem):
            texts.append(item.text)
        elif isinstance(item, dict) and item.get("type") == "text":
            texts.append(str(item.get("text", "")))
    return [t for t in texts if t]


def _print_task(step: str, task) -> None:
    state = task.status.state
    print(f"[{step}] state={state}")

    status_texts = _extract_text_items(task.status.dataItems)
    if status_texts:
        print("  status_text:")
        for t in status_texts:
            print(f"    - {t}")

    if task.products:
        for idx, product in enumerate(task.products, start=1):
            name = product.name or product.id
            print(f"  product[{idx}]={name}")
            for t in _extract_text_items(product.dataItems):
                print(f"    - {t}")


async def _run_once(
    partner_rpc_url: str,
    leader_id: str,
    user_input: str,
    continue_input: str,
    poll_seconds: int,
) -> None:
    session_id = f"session-{uuid.uuid4()}"
    task_id = f"task-{uuid.uuid4()}"
    client = AipRpcClient(partner_url=partner_rpc_url, leader_id=leader_id)

    try:
        task = await client.start_task(session_id=session_id, task_id=task_id, user_input=user_input)
        _print_task("start", task)

        while task.status.state in (TaskState.Accepted, TaskState.Working):
            await asyncio.sleep(poll_seconds)
            task = await client.get_task(task_id=task_id, session_id=session_id)
            _print_task("get(poll)", task)

        if task.status.state == TaskState.AwaitingInput:
            task = await client.continue_task(
                task_id=task_id,
                session_id=session_id,
                user_input=continue_input,
            )
            _print_task("continue", task)

        if task.status.state == TaskState.AwaitingCompletion:
            task = await client.complete_task(task_id=task_id, session_id=session_id)
            _print_task("complete", task)

        print("ACPs RPC协作测试完成。")
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Use ACPs AipRpcClient to call remote Partner")
    parser.add_argument(
        "--partner-rpc-url",
        default="http://113.47.5.136/rpc",
        help="Remote Partner RPC URL",
    )
    parser.add_argument("--leader-id", default="local-leader-acps-test", help="Leader senderId")
    parser.add_argument("--user-input", default="请简单自我介绍并说明你能做什么。", help="Start input text")
    parser.add_argument("--continue-input", default="请补充一条更具体的能力说明。", help="Continue input text")
    parser.add_argument("--poll-seconds", type=int, default=1, help="Polling interval when state is working")
    args = parser.parse_args()

    print(f"partner_rpc_url={args.partner_rpc_url}")
    asyncio.run(
        _run_once(
            partner_rpc_url=args.partner_rpc_url,
            leader_id=args.leader_id,
            user_input=args.user_input,
            continue_input=args.continue_input,
            poll_seconds=args.poll_seconds,
        )
    )


if __name__ == "__main__":
    main()

