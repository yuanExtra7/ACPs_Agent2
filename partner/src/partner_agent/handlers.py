"""Direct RPC AIP command handlers for the Partner role."""

from __future__ import annotations

import asyncio

from acps_sdk.aip.aip_base_model import Product, TaskCommand, TaskResult, TaskState, TextDataItem
from acps_sdk.aip.aip_rpc_server import TaskManager

from .chat_service import build_chat_answer
from .settings import PARTNER_AIC

TERMINAL_STATES = {
    TaskState.Completed,
    TaskState.Failed,
    TaskState.Rejected,
    TaskState.Canceled,
}
CONTINUE_ALLOWED_STATES = {
    TaskState.AwaitingInput,
}
_TASK_LOCKS: dict[str, asyncio.Lock] = {}


def _task_lock(task_id: str) -> asyncio.Lock:
    """Return a per-task lock to serialize in-memory task mutations."""
    lock = _TASK_LOCKS.get(task_id)
    if lock is None:
        lock = asyncio.Lock()
        _TASK_LOCKS[task_id] = lock
    return lock
def _text_from(command: TaskCommand) -> str:
    """Extract the first text data item from a command."""
    for item in command.dataItems or []:
        if isinstance(item, TextDataItem):
            return item.text
    return ""


def _with_sender(task: TaskResult) -> TaskResult:
    """Inject the current Partner sender ID into task responses."""
    task.senderId = PARTNER_AIC
    return task


def _ask_for_text_input(command: TaskCommand) -> TaskResult:
    """Create an AwaitingInput task when required text is missing."""
    task = TaskManager.create_task(
        command,
        initial_state=TaskState.AwaitingInput,
        data_items=[TextDataItem(text="仅支持文本输入。请提供文本内容。")],
    )
    return _with_sender(task)


def _reject_non_text_request(command: TaskCommand) -> TaskResult:
    """Reject commands containing non-text data items."""
    task = TaskManager.create_task(
        command,
        initial_state=TaskState.Rejected,
        data_items=[TextDataItem(text="当前仅支持文本聊天咨询，不支持图像、语音等输入。")],
    )
    return _with_sender(task)


def _set_chat_product(task_id: str, answer_text: str) -> None:
    """Write generated text output into the task product list."""
    TaskManager.set_products(
        task_id,
        [
            Product(
                id=f"product-{task_id}",
                name="text-chat-response",
                dataItems=[TextDataItem(text=answer_text)],
            )
        ],
    )


def _contains_non_text_data(command: TaskCommand) -> bool:
    """Return True when any incoming data item is not text."""
    for item in command.dataItems or []:
        if not isinstance(item, TextDataItem):
            return True
    return False


def _is_terminal(task: TaskResult) -> bool:
    """Check whether a task already reached a terminal state."""
    return task.status.state in TERMINAL_STATES


def _conversation_key(command: TaskCommand) -> str:
    """Build the chat-memory key used by the model layer."""
    session = command.sessionId or command.taskId
    task = command.taskId or "unknown-task"
    return f"partner:{session}:{task}"


async def on_start(command: TaskCommand, task: TaskResult | None) -> TaskResult:
    """Handle start command and move task into AwaitingCompletion when ready."""
    async with _task_lock(command.taskId):
        if task:
            return _with_sender(task)

        if _contains_non_text_data(command):
            return _reject_non_text_request(command)

        user_text = _text_from(command).strip()
        if not user_text:
            return _ask_for_text_input(command)

        task = TaskManager.create_task(
            command,
            initial_state=TaskState.Working,
            data_items=[TextDataItem(text="正在处理你的请求，请稍候。")],
        )
        try:
            answer = await build_chat_answer(user_text, conversation_key=_conversation_key(command))
        except Exception as exc:
            failed = TaskManager.update_task_status(
                task.taskId,
                TaskState.Failed,
                data_items=[TextDataItem(text=f"处理失败：{exc}")],
            )
            return _with_sender(failed or TaskManager.get_task(task.taskId) or task)

        _set_chat_product(task.taskId, answer)
        updated = TaskManager.update_task_status(task.taskId, TaskState.AwaitingCompletion)
        return _with_sender(updated or TaskManager.get_task(task.taskId) or task)


async def on_continue(command: TaskCommand, task: TaskResult) -> TaskResult:
    """Handle continue command with idempotent behavior outside allowed states."""
    async with _task_lock(task.taskId):
        # Keep continue idempotent outside allowed states.
        if _is_terminal(task) or task.status.state not in CONTINUE_ALLOWED_STATES:
            TaskManager.add_command_to_history(task.taskId, command)
            return _with_sender(task)

        TaskManager.add_command_to_history(task.taskId, command)

        if _contains_non_text_data(command):
            TaskManager.update_task_status(
                task.taskId,
                TaskState.AwaitingInput,
                data_items=[TextDataItem(text="仅支持文本补充信息，请改为纯文本。")],
            )
            return _with_sender(TaskManager.get_task(task.taskId) or task)

        user_text = _text_from(command).strip()
        if not user_text:
            return _with_sender(task)

        TaskManager.update_task_status(
            task.taskId,
            TaskState.Working,
            data_items=[TextDataItem(text="正在处理补充信息，请稍候。")],
        )
        try:
            answer = await build_chat_answer(user_text, conversation_key=_conversation_key(command))
        except Exception as exc:
            failed = TaskManager.update_task_status(
                task.taskId,
                TaskState.Failed,
                data_items=[TextDataItem(text=f"补充处理失败：{exc}")],
            )
            return _with_sender(failed or TaskManager.get_task(task.taskId) or task)

        _set_chat_product(task.taskId, answer)
        updated = TaskManager.update_task_status(task.taskId, TaskState.AwaitingCompletion)
        return _with_sender(updated or TaskManager.get_task(task.taskId) or task)


async def on_complete(command: TaskCommand, task: TaskResult) -> TaskResult:
    """Handle complete command with strict state boundary and idempotency."""
    async with _task_lock(task.taskId):
        TaskManager.add_command_to_history(task.taskId, command)
        if _is_terminal(task):
            return _with_sender(task)

        if task.status.state != TaskState.AwaitingCompletion:
            # Only AwaitingCompletion can transition to Completed.
            return _with_sender(TaskManager.get_task(task.taskId) or task)

        updated = TaskManager.update_task_status(task.taskId, TaskState.Completed)
        return _with_sender(updated or TaskManager.get_task(task.taskId) or task)

