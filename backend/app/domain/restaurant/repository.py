from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class SQLiteOrderRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init()

    def close(self) -> None:
        self.conn.close()

    def _init(self) -> None:
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS order_sessions (order_id TEXT PRIMARY KEY, table_id TEXT NOT NULL, guest_session_id TEXT NOT NULL, response_language TEXT NOT NULL, current_revision INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS order_revisions (order_id TEXT NOT NULL, revision INTEGER NOT NULL, parent_revision INTEGER, transcript TEXT NOT NULL, source_language TEXT NOT NULL, items_json TEXT NOT NULL, allergies_json TEXT NOT NULL, validation_flags_json TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(order_id, revision));
        CREATE TABLE IF NOT EXISTS kitchen_decisions (decision_id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, expected_revision INTEGER NOT NULL, action TEXT NOT NULL, eta_minutes INTEGER, substitute_json TEXT, reason TEXT, actor TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS order_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL, revision INTEGER NOT NULL, event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS dialogue_sessions (guest_session_id TEXT PRIMARY KEY, table_id TEXT NOT NULL, state_json TEXT NOT NULL, updated_at TEXT NOT NULL);
        """)
        self.conn.commit()

    def create_revision(self, order_id: str, table_id: str, guest_session_id: str, language: str, transcript: str, items: list[dict[str, Any]], allergies: list[str], status: str = "pending_kitchen", expected_revision: int | None = None) -> dict[str, Any]:
        now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
        with self._lock, self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute("SELECT current_revision,table_id,guest_session_id FROM order_sessions WHERE order_id=?", (order_id,)).fetchone()
            if row and (row["table_id"] != table_id or row["guest_session_id"] != guest_session_id):
                raise ValueError("order belongs to another guest session")
            current_revision = int(row["current_revision"]) if row else 0
            if expected_revision is not None and current_revision != expected_revision:
                raise ValueError(f"stale revision: current={current_revision}")
            rev = current_revision + 1
            parent = rev - 1 if row else None
            self.conn.execute("INSERT OR IGNORE INTO order_sessions VALUES (?,?,?,?,?,?,?,?)", (order_id, table_id, guest_session_id, language, rev, status, now, now))
            self.conn.execute("UPDATE order_sessions SET current_revision=?, response_language=?, status=?, updated_at=? WHERE order_id=?", (rev, language, status, now, order_id))
            self.conn.execute("INSERT INTO order_revisions VALUES (?,?,?,?,?,?,?,?,?,?)", (order_id, rev, parent, transcript, language, json.dumps(items), json.dumps(allergies), json.dumps([]), status, now))
            self.conn.execute("INSERT INTO order_events(order_id,revision,event_type,payload_json,created_at) VALUES (?,?,?,?,?)", (order_id, rev, "revision_created", json.dumps({"items": items}), now))
        return self.get_order(order_id)

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM order_sessions WHERE order_id=?", (order_id,)).fetchone()
            if not row:
                return None
            out = dict(row)
            out["revisions"] = [dict(r) for r in self.conn.execute("SELECT * FROM order_revisions WHERE order_id=? ORDER BY revision", (order_id,)).fetchall()]
            current = out["revisions"][-1]
            out["items"] = json.loads(current["items_json"])
            out["allergies"] = json.loads(current["allergies_json"])
            # Placing writes a pending_kitchen revision; drafts never do, so this survives kitchen decisions.
            out["placed"] = any(r["status"] == "pending_kitchen" for r in out["revisions"])
            decision = self.conn.execute("SELECT * FROM kitchen_decisions WHERE order_id=? ORDER BY decision_id DESC LIMIT 1", (order_id,)).fetchone()
            if decision:
                out["latest_decision"] = dict(decision)
                out["latest_decision"]["substitutions"] = json.loads(decision["substitute_json"] or "[]")
            else:
                out["latest_decision"] = None
            return out

    def decide(self, order_id: str, decision: dict[str, Any]) -> dict[str, Any]:
        now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
        with self._lock, self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute("SELECT current_revision,status FROM order_sessions WHERE order_id=?", (order_id,)).fetchone()
            if not row:
                raise KeyError(order_id)
            if int(row["current_revision"]) != int(decision["expected_revision"]):
                raise ValueError(f"stale revision: current={row['current_revision']}")
            if row["status"] in {"cancelled", "ready", "rejected"}:
                raise ValueError("this order is closed")
            action = decision["action"]
            status = {"accept": "committed", "reject": "rejected", "request_clarification": "clarification_required", "propose_substitute": "substitution_proposed", "mark_ready": "ready", "set_eta": row["status"]}.get(action, row["status"])
            self.conn.execute("UPDATE order_sessions SET status=?,updated_at=? WHERE order_id=?", (status, now, order_id))
            self.conn.execute("INSERT INTO kitchen_decisions(order_id,expected_revision,action,eta_minutes,substitute_json,reason,actor,created_at) VALUES (?,?,?,?,?,?,?,?)", (order_id, decision["expected_revision"], action, decision.get("eta_minutes"), json.dumps(decision.get("substitutions", [])), decision.get("reason"), decision.get("actor", "kitchen"), now))
            self.conn.execute("INSERT INTO order_events(order_id,revision,event_type,payload_json,created_at) VALUES (?,?,?,?,?)", (order_id, row["current_revision"], "kitchen_decision", json.dumps(decision), now))
        return self.get_order(order_id)

    def list_orders(self) -> list[dict[str, Any]]:
        with self._lock:
            ids = [r[0] for r in self.conn.execute("SELECT order_id FROM order_sessions ORDER BY updated_at DESC").fetchall()]
            return [self.get_order(order_id) for order_id in ids]

    def load_dialogue(self, guest_session_id: str, table_id: str) -> dict[str, Any] | None:
        """The saved dialogue state for one guest session, or None if it belongs to another table."""
        with self._lock:
            row = self.conn.execute("SELECT table_id,state_json FROM dialogue_sessions WHERE guest_session_id=?", (guest_session_id,)).fetchone()
        if not row or row["table_id"] != table_id:
            return None
        return json.loads(row["state_json"])

    def save_dialogue(self, guest_session_id: str, table_id: str, state: dict[str, Any]) -> None:
        now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO dialogue_sessions VALUES (?,?,?,?) ON CONFLICT(guest_session_id) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at WHERE dialogue_sessions.table_id=excluded.table_id",
                (guest_session_id, table_id, json.dumps(state), now),
            )
