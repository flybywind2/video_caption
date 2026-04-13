from __future__ import annotations

import sqlite3
from pathlib import Path
from threading import RLock
from typing import Any


def utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


class TaskRepository:
    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._conn = sqlite3.connect(str(database_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def init_db(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    original_filename TEXT NOT NULL,
                    language TEXT NOT NULL,
                    status TEXT NOT NULL,
                    pending_action TEXT NOT NULL DEFAULT 'transcribe',
                    progress REAL NOT NULL DEFAULT 0,
                    message TEXT DEFAULT '',
                    delete_requested INTEGER NOT NULL DEFAULT 0,
                    source_video_path TEXT NOT NULL,
                    audio_path TEXT,
                    transcript_path TEXT,
                    captions_path TEXT,
                    srt_path TEXT,
                    rendered_video_path TEXT,
                    error_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                )
                """
            )
            self._conn.commit()

    def create_task(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        payload = {
            "id": record["id"],
            "original_filename": record["original_filename"],
            "language": record["language"],
            "status": record.get("status", "queued"),
            "pending_action": record.get("pending_action", "transcribe"),
            "progress": record.get("progress", 0.0),
            "message": record.get("message", ""),
            "delete_requested": int(record.get("delete_requested", 0)),
            "source_video_path": record["source_video_path"],
            "audio_path": record.get("audio_path"),
            "transcript_path": record.get("transcript_path"),
            "captions_path": record.get("captions_path"),
            "srt_path": record.get("srt_path"),
            "rendered_video_path": record.get("rendered_video_path"),
            "error_message": record.get("error_message"),
            "created_at": now,
            "updated_at": now,
            "started_at": record.get("started_at"),
            "completed_at": record.get("completed_at"),
        }

        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tasks (
                    id, original_filename, language, status, pending_action, progress, message,
                    delete_requested, source_video_path, audio_path, transcript_path, captions_path,
                    srt_path, rendered_video_path, error_message, created_at, updated_at,
                    started_at, completed_at
                ) VALUES (
                    :id, :original_filename, :language, :status, :pending_action, :progress,
                    :message, :delete_requested, :source_video_path, :audio_path,
                    :transcript_path, :captions_path, :srt_path, :rendered_video_path,
                    :error_message, :created_at, :updated_at, :started_at, :completed_at
                )
                """,
                payload,
            )
            self._conn.commit()
        return self.get_task(payload["id"])

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_unfinished_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM tasks
                WHERE status IN ('queued', 'processing', 'rendering', 'deleting')
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def update_task(self, task_id: str, **fields: Any) -> dict[str, Any] | None:
        if not fields:
            return self.get_task(task_id)

        assignments = []
        values: list[Any] = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            if isinstance(value, bool):
                values.append(int(value))
            else:
                values.append(value)

        assignments.append("updated_at = ?")
        values.append(utc_now())
        values.append(task_id)

        with self._lock:
            self._conn.execute(
                f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            self._conn.commit()
        return self.get_task(task_id)

    def delete_task(self, task_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            self._conn.commit()

    def status_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
            ).fetchall()
        return {row["status"]: row["count"] for row in rows}

    def close(self) -> None:
        with self._lock:
            self._conn.close()
