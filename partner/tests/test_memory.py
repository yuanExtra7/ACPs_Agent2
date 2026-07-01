from __future__ import annotations

from partner_agent.memory import ConversationMemoryStore, SessionStateStore


def test_memory_store_keeps_recent_turns() -> None:
    store = ConversationMemoryStore(max_turns=3)
    key = "k1"
    store.append(key, "user", "a")
    store.append(key, "assistant", "b")
    store.append(key, "user", "c")
    store.append(key, "assistant", "d")

    history = store.get_history(key)
    assert len(history) == 3
    assert history[0]["content"] == "b"
    assert history[-1]["content"] == "d"


def test_memory_store_append_exchange() -> None:
    store = ConversationMemoryStore(max_turns=5)
    key = "k2"
    store.append_exchange(key, "你好", "你好，我在")
    assert store.size(key) == 2
    history = store.get_history(key)
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"


def test_session_state_store_update_and_get() -> None:
    store = SessionStateStore()
    state = store.get("session-1")
    assert state.session_id == "session-1"
    assert state.rpc_url == ""
    assert state.active_task_id == ""

    updated = store.update(
        "session-1",
        rpc_url="http://127.0.0.1:5000/rpc",
        active_task_id="task-1",
        last_state="awaiting-input",
    )
    assert updated.rpc_url == "http://127.0.0.1:5000/rpc"
    assert updated.active_task_id == "task-1"
    assert updated.last_state == "awaiting-input"

    again = store.get("session-1")
    assert again.rpc_url == "http://127.0.0.1:5000/rpc"
    assert again.active_task_id == "task-1"
