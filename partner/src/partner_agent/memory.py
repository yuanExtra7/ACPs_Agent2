"""Thread-safe in-memory stores shared across human, partner, and leader flows."""

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
    discovered_partner_aic: str = ""
    discovered_partner_name: str = ""
    discovery_query: str = ""
    discovery_error: str = ""
    discovery_total_candidates: int = 0


class ConversationMemoryStore:
    def __init__(self, *, max_turns: int) -> None:
        """Initialize bounded per-key chat history storage."""
        self._max_turns = max(1, max_turns)
        self._data: dict[str, Deque[MemoryTurn]] = defaultdict(lambda: deque(maxlen=self._max_turns))
        self._lock = Lock()

    def append(self, key: str, role: str, content: str) -> None:
        """Append one message if key and content are both valid."""
        text = content.strip()
        if not key or not text:
            return
        with self._lock:
            self._data[key].append(MemoryTurn(role=role, content=text))

    def append_exchange(self, key: str, user_text: str, assistant_text: str) -> None:
        """Append a user/assistant pair in order."""
        self.append(key, "user", user_text)
        self.append(key, "assistant", assistant_text)

    def get_history(self, key: str) -> list[dict[str, str]]:
        """Return a snapshot list of role/content records for a key."""
        with self._lock:
            turns = list(self._data.get(key, ()))
        return [{"role": turn.role, "content": turn.content} for turn in turns]

    def size(self, key: str) -> int:
        """Return current turn count for a key."""
        with self._lock:
            return len(self._data.get(key, ()))


class SessionStateStore:
    def __init__(self) -> None:
        """Initialize runtime state map keyed by session ID."""
        self._states: dict[str, SessionRuntimeState] = {}
        self._lock = Lock()

    def get(self, session_id: str) -> SessionRuntimeState:
        """Get session runtime state, creating a default state if missing."""
        with self._lock:
            state = self._states.get(session_id)
            if state is None:
                state = SessionRuntimeState(session_id=session_id)
                self._states[session_id] = state
            return SessionRuntimeState(**state.__dict__)

    def save(self, state: SessionRuntimeState) -> None:
        """Persist a full runtime state snapshot."""
        with self._lock:
            self._states[state.session_id] = SessionRuntimeState(**state.__dict__)

    def update(self, session_id: str, **changes: str) -> SessionRuntimeState:
        """Patch selected state fields and return the updated snapshot."""
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
        """Remove one session state entry."""
        with self._lock:
            self._states.pop(session_id, None)


MEMORY = ConversationMemoryStore(max_turns=MEMORY_MAX_TURNS)
SESSION_STATES = SessionStateStore()
