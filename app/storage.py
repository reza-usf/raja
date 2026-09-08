from __future__ import annotations

import sqlite3
import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Watch:
    id: int
    chat_id: int
    origin: str
    destination: str
    date_from: str
    date_to: str
    passengers: int
    passenger_type: str
    interval_seconds: int
    active: bool
    next_check_at: float
    last_fingerprint: str | None
    consecutive_errors: int
    last_error_notified_at: float | None
    dashboard_message_id: int | None


class Storage:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._init()

    def _connect(self):
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def _init(self):
        with self._lock, self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS watches (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    origin TEXT NOT NULL,
                    destination TEXT NOT NULL,
                    date_from TEXT NOT NULL,
                    date_to TEXT NOT NULL,
                    passengers INTEGER NOT NULL,
                    passenger_type TEXT NOT NULL,
                    interval_seconds INTEGER NOT NULL DEFAULT 60,
                    active INTEGER NOT NULL DEFAULT 1,
                    next_check_at REAL NOT NULL,
                    last_fingerprint TEXT,
                    consecutive_errors INTEGER NOT NULL DEFAULT 0,
                    last_error_notified_at REAL,
                    dashboard_message_id INTEGER,
                    created_at REAL NOT NULL
                )
                """
            )
            con.execute("CREATE INDEX IF NOT EXISTS idx_watches_due ON watches(active, next_check_at)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_watches_chat ON watches(chat_id, active)")
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS watch_date_state (
                    watch_id INTEGER NOT NULL,
                    jalali_date TEXT NOT NULL,
                    fingerprint TEXT,
                    snapshot_json TEXT,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (watch_id, jalali_date)
                )
                """
            )
            # Safe schema migration for existing VPS databases.
            watch_cols = {
                row["name"]
                for row in con.execute("PRAGMA table_info(watches)").fetchall()
            }
            if "dashboard_message_id" not in watch_cols:
                con.execute(
                    "ALTER TABLE watches ADD COLUMN dashboard_message_id INTEGER"
                )

            state_cols = {
                row["name"]
                for row in con.execute(
                    "PRAGMA table_info(watch_date_state)"
                ).fetchall()
            }
            if "snapshot_json" not in state_cols:
                con.execute(
                    "ALTER TABLE watch_date_state ADD COLUMN snapshot_json TEXT"
                )

    @staticmethod
    def _row_to_watch(row: sqlite3.Row) -> Watch:
        return Watch(
            id=row["id"],
            chat_id=row["chat_id"],
            origin=row["origin"],
            destination=row["destination"],
            date_from=row["date_from"],
            date_to=row["date_to"],
            passengers=row["passengers"],
            passenger_type=row["passenger_type"],
            interval_seconds=row["interval_seconds"],
            active=bool(row["active"]),
            next_check_at=row["next_check_at"],
            last_fingerprint=row["last_fingerprint"],
            consecutive_errors=row["consecutive_errors"],
            last_error_notified_at=row["last_error_notified_at"],
            dashboard_message_id=row["dashboard_message_id"],
        )

    def add_watch(
        self, chat_id: int, origin: str, destination: str,
        date_from: str, date_to: str, passengers: int,
        passenger_type: str, interval_seconds: int,
    ) -> int:
        now = time.time()
        with self._lock, self._connect() as con:
            cur = con.execute(
                """
                INSERT INTO watches
                (chat_id, origin, destination, date_from, date_to, passengers,
                 passenger_type, interval_seconds, active, next_check_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    chat_id, origin, destination, date_from, date_to, passengers,
                    passenger_type, max(60, interval_seconds), now, now,
                ),
            )
            return int(cur.lastrowid)

    def list_active(self, chat_id: int) -> list[Watch]:
        with self._lock, self._connect() as con:
            rows = con.execute(
                "SELECT * FROM watches WHERE chat_id=? AND active=1 ORDER BY id DESC",
                (chat_id,),
            ).fetchall()
        return [self._row_to_watch(r) for r in rows]

    def due(self, now: float | None = None) -> list[Watch]:
        now = now or time.time()
        with self._lock, self._connect() as con:
            rows = con.execute(
                """
                SELECT * FROM watches
                WHERE active=1 AND next_check_at<=?
                ORDER BY next_check_at ASC
                """,
                (now,),
            ).fetchall()
        return [self._row_to_watch(r) for r in rows]

    def schedule_next(self, watch_id: int, interval_seconds: int):
        with self._lock, self._connect() as con:
            con.execute(
                "UPDATE watches SET next_check_at=? WHERE id=?",
                (time.time() + max(60, interval_seconds), watch_id),
            )

    def set_fingerprint(self, watch_id: int, fingerprint: str | None):
        with self._lock, self._connect() as con:
            con.execute(
                """
                UPDATE watches
                SET last_fingerprint=?, consecutive_errors=0, last_error_notified_at=NULL
                WHERE id=?
                """,
                (fingerprint, watch_id),
            )

    def record_error(self, watch_id: int) -> tuple[int, float | None]:
        with self._lock, self._connect() as con:
            con.execute(
                "UPDATE watches SET consecutive_errors=consecutive_errors+1 WHERE id=?",
                (watch_id,),
            )
            row = con.execute(
                "SELECT consecutive_errors, last_error_notified_at FROM watches WHERE id=?",
                (watch_id,),
            ).fetchone()
            return int(row["consecutive_errors"]), row["last_error_notified_at"]

    def mark_error_notified(self, watch_id: int):
        with self._lock, self._connect() as con:
            con.execute(
                "UPDATE watches SET last_error_notified_at=? WHERE id=?",
                (time.time(), watch_id),
            )

    def schedule_at(self, watch_id: int, timestamp: float):
        with self._lock, self._connect() as con:
            con.execute(
                "UPDATE watches SET next_check_at=? WHERE id=?",
                (timestamp, watch_id),
            )

    def get_date_fingerprint(self, watch_id: int, jalali_date: str) -> str | None:
        with self._lock, self._connect() as con:
            row = con.execute(
                """
                SELECT fingerprint
                FROM watch_date_state
                WHERE watch_id=? AND jalali_date=?
                """,
                (watch_id, jalali_date),
            ).fetchone()
            return row["fingerprint"] if row else None

    def set_date_fingerprint(
        self, watch_id: int, jalali_date: str, fingerprint: str | None
    ):
        with self._lock, self._connect() as con:
            con.execute(
                """
                INSERT INTO watch_date_state
                    (watch_id, jalali_date, fingerprint, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(watch_id, jalali_date)
                DO UPDATE SET
                    fingerprint=excluded.fingerprint,
                    updated_at=excluded.updated_at
                """,
                (watch_id, jalali_date, fingerprint, time.time()),
            )

    def clear_errors(self, watch_id: int):
        with self._lock, self._connect() as con:
            con.execute(
                """
                UPDATE watches
                SET consecutive_errors=0, last_error_notified_at=NULL
                WHERE id=?
                """,
                (watch_id,),
            )

    def set_dashboard_message_id(self, watch_id: int, message_id: int | None):
        with self._lock, self._connect() as con:
            con.execute(
                "UPDATE watches SET dashboard_message_id=? WHERE id=?",
                (message_id, watch_id),
            )

    def get_date_snapshot(self, watch_id: int, jalali_date: str) -> dict | None:
        with self._lock, self._connect() as con:
            row = con.execute(
                """
                SELECT snapshot_json
                FROM watch_date_state
                WHERE watch_id=? AND jalali_date=?
                """,
                (watch_id, jalali_date),
            ).fetchone()
        if not row or not row["snapshot_json"]:
            return None
        try:
            return json.loads(row["snapshot_json"])
        except Exception:
            return None

    def set_date_snapshot(
        self,
        watch_id: int,
        jalali_date: str,
        snapshot: dict | None,
        fingerprint: str | None = None,
    ):
        payload = (
            json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
            if snapshot is not None
            else None
        )
        with self._lock, self._connect() as con:
            con.execute(
                """
                INSERT INTO watch_date_state
                    (watch_id, jalali_date, fingerprint, snapshot_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(watch_id, jalali_date)
                DO UPDATE SET
                    fingerprint=excluded.fingerprint,
                    snapshot_json=excluded.snapshot_json,
                    updated_at=excluded.updated_at
                """,
                (
                    watch_id,
                    jalali_date,
                    fingerprint,
                    payload,
                    time.time(),
                ),
            )

    def deactivate(self, watch_id: int, chat_id: int) -> bool:
        with self._lock, self._connect() as con:
            cur = con.execute(
                "UPDATE watches SET active=0 WHERE id=? AND chat_id=?",
                (watch_id, chat_id),
            )
            return cur.rowcount > 0

    def deactivate_all(self, chat_id: int):
        with self._lock, self._connect() as con:
            con.execute("UPDATE watches SET active=0 WHERE chat_id=?", (chat_id,))
