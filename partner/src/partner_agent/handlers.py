"""Direct RPC AIP command handlers for the Partner role."""

from __future__ import annotations

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
    TaskState.AwaitingCompletion,
}
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
    return f"partner:{session}"


async def on_start(command: TaskCommand, task: TaskResult | None) -> TaskResult:
    """Handle start command and move task into AwaitingCompletion when ready."""
    if task:
        return _with_sender(task)

    if _contains_non_text_data(command):
        return _reject_non_text_request(command)

    user_text = _text_from(command).strip()
    if not user_text:
        return _ask_for_text_input(command)

    answer = await build_chat_answer(user_text, conversation_key=_conversation_key(command))

    task = TaskManager.create_task(command, initial_state=TaskState.AwaitingCompletion)
    _set_chat_product(task.taskId, answer)
    return _with_sender(TaskManager.get_task(task.taskId) or task)


async def on_continue(command: TaskCommand, task: TaskResult) -> TaskResult:
    """Handle continue command with idempotent behavior outside allowed states."""
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

    answer = await build_chat_answer(user_text, conversation_key=_conversation_key(command))

    _set_chat_product(task.taskId, answer)
    updated = TaskManager.update_task_status(task.taskId, TaskState.AwaitingCompletion)
    return _with_sender(updated)

