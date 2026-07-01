from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PROJECT_ROOT.parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(WORKSPACE_ROOT / "ACPs-community" / "acps-sdk"))

from acps_sdk.aip.aip_base_model import (
    StructuredDataItem,
    TaskCommand,
    TaskCommandType,
    TaskState,
    TextDataItem,
)
from acps_sdk.aip.aip_rpc_server import TaskManager

from partner_agent.handlers import on_continue, on_start


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cmd(command: TaskCommandType, task_id: str, data_items=None) -> TaskCommand:
    return TaskCommand(
        id=f"msg-{uuid4()}",
        sentAt=_now(),
        senderRole="leader",
        senderId="verify-script",
        command=command,
        taskId=task_id,
        sessionId=f"session-{uuid4()}",
        dataItems=data_items,
    )


def _reset_tasks() -> None:
    TaskManager._tasks.clear()  # noqa: SLF001


def main() -> None:
    _reset_tasks()
    task_id = f"task-{uuid4()}"
    reject_cmd = _cmd(TaskCommandType.Start, task_id, [StructuredDataItem(data={"x": 1})])
    reject_task = asyncio.run(on_start(reject_cmd, None))
    assert reject_task.status.state == TaskState.Rejected

    _reset_tasks()
    task_id = f"task-{uuid4()}"
    empty_cmd = _cmd(TaskCommandType.Start, task_id, [TextDataItem(text=" ")])
    awaiting_input_task = asyncio.run(on_start(empty_cmd, None))
    assert awaiting_input_task.status.state == TaskState.AwaitingInput

    continue_cmd = _cmd(TaskCommandType.Continue, task_id, [TextDataItem(text="补充说明")])
    after_continue = asyncio.run(on_continue(continue_cmd, awaiting_input_task))
    assert after_continue.status.state == TaskState.AwaitingCompletion
    assert after_continue.products

    TaskManager.update_task_status(task_id, TaskState.Completed)
    completed = TaskManager.get_task(task_id)
    assert completed is not None
    continue_after_terminal = asyncio.run(on_continue(continue_cmd, completed))
    assert continue_after_terminal.status.state == TaskState.Completed

    print("verify_handlers: OK")


if __name__ == "__main__":
    main()

