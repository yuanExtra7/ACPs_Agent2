from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from acps_sdk.aip.aip_base_model import (
    StructuredDataItem,
    TaskCommand,
    TaskCommandType,
    TaskState,
    TextDataItem,
)
from acps_sdk.aip.aip_rpc_server import TaskManager

import partner_agent.handlers as handlers
from partner_agent.handlers import on_complete, on_continue, on_start


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cmd(
    command: TaskCommandType,
    task_id: str,
    data_items=None,
    session_id: str | None = None,
) -> TaskCommand:
    return TaskCommand(
        id=f"msg-{uuid4()}",
        sentAt=_now(),
        senderRole="leader",
        senderId="test-leader",
        command=command,
        taskId=task_id,
        sessionId=session_id or f"session-{uuid4()}",
        dataItems=data_items,
    )


def _reset_tasks() -> None:
    TaskManager._tasks.clear()  # noqa: SLF001 - test reset for in-memory store


def test_start_rejects_non_text_data() -> None:
    _reset_tasks()
    command = _cmd(
        TaskCommandType.Start,
        task_id=f"task-{uuid4()}",
        data_items=[StructuredDataItem(data={"k": "v"})],
    )

    result = asyncio.run(on_start(command, None))

    assert result.status.state == TaskState.Rejected
    assert result.status.dataItems
    assert isinstance(result.status.dataItems[0], TextDataItem)


def test_start_without_text_goes_awaiting_input() -> None:
    _reset_tasks()
    command = _cmd(TaskCommandType.Start, task_id=f"task-{uuid4()}", data_items=[TextDataItem(text="  ")])

    result = asyncio.run(on_start(command, None))

    assert result.status.state == TaskState.AwaitingInput


def test_start_with_text_produces_awaiting_completion() -> None:
    _reset_tasks()
    command = _cmd(
        TaskCommandType.Start,
        task_id=f"task-{uuid4()}",
        data_items=[TextDataItem(text="你好")],
    )

    result = asyncio.run(on_start(command, None))

    assert result.status.state == TaskState.AwaitingCompletion
    assert result.products
    assert result.products[0].dataItems
    assert isinstance(result.products[0].dataItems[0], TextDataItem)


def test_continue_ignored_for_terminal_state() -> None:
    _reset_tasks()
    task_id = f"task-{uuid4()}"
    start_cmd = _cmd(TaskCommandType.Start, task_id=task_id, data_items=[TextDataItem(text="hello")])
    task = asyncio.run(on_start(start_cmd, None))
    TaskManager.update_task_status(task_id, TaskState.Completed)
    completed_task = TaskManager.get_task(task_id)
    assert completed_task is not None

    continue_cmd = _cmd(TaskCommandType.Continue, task_id=task_id, data_items=[TextDataItem(text="more")])
    result = asyncio.run(on_continue(continue_cmd, completed_task))

    assert result.status.state == TaskState.Completed


def test_continue_text_from_awaiting_input_to_awaiting_completion() -> None:
    _reset_tasks()
    task_id = f"task-{uuid4()}"
    start_cmd = _cmd(TaskCommandType.Start, task_id=task_id, data_items=[TextDataItem(text=" ")])
    task = asyncio.run(on_start(start_cmd, None))
    assert task.status.state == TaskState.AwaitingInput

    continue_cmd = _cmd(TaskCommandType.Continue, task_id=task_id, data_items=[TextDataItem(text="补充信息")])
    result = asyncio.run(on_continue(continue_cmd, task))

    assert result.status.state == TaskState.AwaitingCompletion
    assert result.products


def test_continue_is_idempotent_when_task_is_awaiting_completion() -> None:
    _reset_tasks()
    task_id = f"task-{uuid4()}"
    start_cmd = _cmd(TaskCommandType.Start, task_id=task_id, data_items=[TextDataItem(text="你好")], session_id="s-continue-idem")
    task = asyncio.run(on_start(start_cmd, None))
    assert task.status.state == TaskState.AwaitingCompletion
    original_product = task.products[0].dataItems[0]
    assert isinstance(original_product, TextDataItem)

    continue_cmd = _cmd(
        TaskCommandType.Continue,
        task_id=task_id,
        data_items=[TextDataItem(text="这条不应改变结果")],
        session_id="s-continue-idem",
    )
    result = asyncio.run(on_continue(continue_cmd, task))

    assert result.status.state == TaskState.AwaitingCompletion
    assert result.products
    same_product = result.products[0].dataItems[0]
    assert isinstance(same_product, TextDataItem)
    assert same_product.text == original_product.text


def test_start_failure_transitions_to_failed(monkeypatch) -> None:
    _reset_tasks()
    task_id = f"task-{uuid4()}"
    command = _cmd(
        TaskCommandType.Start,
        task_id=task_id,
        data_items=[TextDataItem(text="触发异常")],
        session_id="session-fail",
    )

    async def _boom(*args, **kwargs):
        raise RuntimeError("llm-down")

    monkeypatch.setattr(handlers, "build_chat_answer", _boom)

    result = asyncio.run(on_start(command, None))

    assert result.status.state == TaskState.Failed
    assert result.status.dataItems
    assert isinstance(result.status.dataItems[0], TextDataItem)
    assert "llm-down" in result.status.dataItems[0].text


def test_complete_only_transitions_from_awaiting_completion() -> None:
    _reset_tasks()
    task_id = f"task-{uuid4()}"
    start_cmd = _cmd(
        TaskCommandType.Start,
        task_id=task_id,
        data_items=[TextDataItem(text=" ")],
        session_id="s-complete-boundary",
    )
    awaiting_input_task = asyncio.run(on_start(start_cmd, None))
    assert awaiting_input_task.status.state == TaskState.AwaitingInput

    complete_cmd = _cmd(TaskCommandType.Complete, task_id=task_id, session_id="s-complete-boundary")
    unchanged = asyncio.run(on_complete(complete_cmd, awaiting_input_task))
    assert unchanged.status.state == TaskState.AwaitingInput

    continue_cmd = _cmd(
        TaskCommandType.Continue,
        task_id=task_id,
        data_items=[TextDataItem(text="补充后可完成")],
        session_id="s-complete-boundary",
    )
    awaiting_completion_task = asyncio.run(on_continue(continue_cmd, unchanged))
    assert awaiting_completion_task.status.state == TaskState.AwaitingCompletion

    completed = asyncio.run(on_complete(complete_cmd, awaiting_completion_task))
    assert completed.status.state == TaskState.Completed

