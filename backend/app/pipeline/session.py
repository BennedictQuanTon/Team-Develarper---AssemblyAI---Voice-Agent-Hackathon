"""In-memory conversation session (3-turn cap for demo)."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


MAX_TURNS = 10


@dataclass
class SessionState:
    session_id: str
    turn_count: int = 0
    ended: bool = False
    history: list[dict[str, str]] = field(default_factory=list)
    profile: str | None = None

    def remaining(self) -> int:
        return max(0, MAX_TURNS - self.turn_count)

    def snapshot(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "turn_count": self.turn_count,
            "max_turns": MAX_TURNS,
            "remaining": self.remaining(),
            "ended": self.ended,
            "history_len": len(self.history),
        }


class SessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, SessionState] = {}

    def get(self, session_id: str) -> SessionState:
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = SessionState(session_id=session_id)
            return self._sessions[session_id]

    def reset(self, session_id: str) -> SessionState:
        with self._lock:
            state = SessionState(session_id=session_id)
            self._sessions[session_id] = state
            return state

    def append_turn(self, session_id: str, user: str, agent: str) -> SessionState:
        with self._lock:
            state = self._sessions.setdefault(session_id, SessionState(session_id=session_id))
            state.history.append({"user": user, "agent": agent})
            # Keep only last 2 for prompt
            if len(state.history) > 4:
                state.history = state.history[-4:]
            state.turn_count += 1
            if state.turn_count >= MAX_TURNS:
                state.ended = True
            return state

    def end(self, session_id: str) -> SessionState:
        with self._lock:
            state = self._sessions.setdefault(session_id, SessionState(session_id=session_id))
            state.ended = True
            return state
