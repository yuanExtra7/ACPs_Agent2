"""In-memory conversation storage shared by human, partner, and leader flows."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock
from typing import Deque

from .settings import MEMORY_MAX_TURNS


@dataclass(frozen=True)
class MemoryTurn:
    role: str
    content: str


@dataclass
class SessionRuntimeState:
    session_id: str
    rpc_url: str = ""
    aip_session_id: str = ""
    active_task_id: str = ""
    last_state: str = ""
    partner_sender_id: str = ""


class ConversationMemoryStore:
    def __init__(self, *, max_turns: int) -> None:
        self._max_turns = max(1, max_turns)
        self._data: dict[str, Deque[MemoryTurn]] = defaultdict(lambda: deque(maxlen=self._max_turns))
        self._lock = Lock()

    def append(self, key: str, role: str, content: str) -> None:
        text = content.strip()
        if not key or not text:
            return
        with self._lock:
            self._data[key].append(MemoryTurn(role=role, content=text))

    def append_exchange(self, key: str, user_text: str, assistant_text: str) -> None:
        self.append(key, "user", user_text)
        self.append(key, "assistant", assistant_text)

    def get_history(self, key: str) -> list[dict[str, str]]:
        with self._lock:
            turns = list(self._data.get(key, ()))
        return [{"role": turn.role, "content": turn.content} for turn in turns]

    def size(self, key: str) -> int:
        with self._lock:
            return len(self._data.get(key, ()))


class SessionStateStore:
    def __init__(self) -> None:
        self._states: dict[str, SessionRuntimeState] = {}
        self._lock = Lock()

    def get(self, session_id: str) -> SessionRuntimeState:
        with self._lock:
            state = self._states.get(session_id)
            if state is None:
                state = SessionRuntimeState(session_id=session_id)
                self._states[session_id] = state
            return SessionRuntimeState(**state.__dict__)

    def save(self, state: SessionRuntimeState) -> None:
        with self._lock:
            self._states[state.session_id] = SessionRuntimeState(**state.__dict__)

    def update(self, session_id: str, **changes: str) -> SessionRuntimeState:
        with self._lock:
            state = self._states.get(session_id)
            if state is None:
                state = SessionRuntimeState(session_id=session_id)
            for key, value in changes.items():
                if hasattr(state, key):
                    setattr(state, key, value)
            self._states[session_id] = state
            return SessionRuntimeState(**state.__dict__)

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._states.pop(session_id, None)


MEMORY = ConversationMemoryStore(max_turns=MEMORY_MAX_TURNS)
SESSION_STATES = SessionStateStore()
