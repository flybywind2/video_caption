from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskRepository:
    def __init__(self, database_path: Path, storage_root: Path) -> None:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._conn = sqlite3.connect(str(database_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._storage_root = storage_root

    def init_db(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    original_filename TEXT NOT NULL,
                    language TEXT NOT NULL,
                    status TEXT NOT NULL,
                    batch_id TEXT,
                    batch_index INTEGER NOT NULL DEFAULT 1,
                    batch_total INTEGER NOT NULL DEFAULT 1,
                    blocked_by_task_id TEXT,
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
            self._ensure_columns()
            self._conn.commit()

    def _ensure_columns(self) -> None:
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(tasks)").fetchall()
        }
        additions = [
            ("batch_id", "TEXT"),
            ("batch_index", "INTEGER NOT NULL DEFAULT 1"),
            ("batch_total", "INTEGER NOT NULL DEFAULT 1"),
            ("blocked_by_task_id", "TEXT"),
        ]
        for column_name, definition in additions:
            if column_name in columns:
                continue
            self._conn.execute(
                f"ALTER TABLE tasks ADD COLUMN {column_name} {definition}"
            )

    def recover_from_storage(self) -> int:
        inserted = 0
        tasks_root = self._storage_root / "tasks"
        if not tasks_root.exists():
            return inserted

        for task_dir in sorted(path for path in tasks_root.iterdir() if path.is_dir()):
            task_id = task_dir.name
            if self.get_task(task_id):
                continue

            record = self._load_task_snapshot(task_dir / "task.json")
            if record is None:
                record = self._reconstruct_task_record(task_dir)
            if record is None:
                continue

            self._insert_task(record)
            inserted += 1

        return inserted

    def ensure_recovered_if_empty(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS count FROM tasks").fetchone()
        task_count = int(row["count"]) if row else 0
        if task_count > 0:
            return 0
        return self.recover_from_storage()

    def backfill_snapshots(self) -> None:
        for task in self.list_tasks():
            self._write_task_snapshot(task)

    def create_task(self, record: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        payload = {
            "id": record["id"],
            "original_filename": record["original_filename"],
            "language": record["language"],
            "status": record.get("status", "queued"),
            "batch_id": record.get("batch_id"),
            "batch_index": int(record.get("batch_index", 1) or 1),
            "batch_total": int(record.get("batch_total", 1) or 1),
            "blocked_by_task_id": record.get("blocked_by_task_id"),
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
            "created_at": record.get("created_at") or now,
            "updated_at": record.get("updated_at") or now,
            "started_at": record.get("started_at"),
            "completed_at": record.get("completed_at"),
        }

        self._insert_task(payload)
        task = self.get_task(payload["id"])
        if task:
            self._write_task_snapshot(task)
        return task

    def _insert_task(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO tasks (
                    id, original_filename, language, status, batch_id, batch_index, batch_total,
                    blocked_by_task_id, pending_action, progress, message,
                    delete_requested, source_video_path, audio_path, transcript_path, captions_path,
                    srt_path, rendered_video_path, error_message, created_at, updated_at,
                    started_at, completed_at
                ) VALUES (
                    :id, :original_filename, :language, :status, :batch_id, :batch_index,
                    :batch_total, :blocked_by_task_id, :pending_action, :progress,
                    :message, :delete_requested, :source_video_path, :audio_path,
                    :transcript_path, :captions_path, :srt_path, :rendered_video_path,
                    :error_message, :created_at, :updated_at, :started_at, :completed_at
                )
                """,
                payload,
            )
            self._conn.commit()

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_tasks(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC, batch_index ASC, updated_at DESC"
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

    def release_blocked_successors(self, completed_task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM tasks
                WHERE blocked_by_task_id = ? AND status = 'blocked'
                ORDER BY created_at ASC
                """,
                (completed_task_id,),
            ).fetchall()
            if not rows:
                return []

            released_at = utc_now()
            task_ids = [row["id"] for row in rows]
            self._conn.executemany(
                """
                UPDATE tasks
                SET status = 'queued',
                    pending_action = 'transcribe',
                    progress = 0.05,
                    blocked_by_task_id = NULL,
                    message = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                [
                    (
                        "Previous split finished. Added to queue in order.",
                        released_at,
                        task_id,
                    )
                    for task_id in task_ids
                ],
            )
            self._conn.commit()

        released = [self.get_task(task_id) for task_id in task_ids]
        return [task for task in released if task]

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
        task = self.get_task(task_id)
        if task:
            self._write_task_snapshot(task)
        return task

    def delete_task(self, task_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            self._conn.commit()
        self._task_snapshot_path(task_id).unlink(missing_ok=True)

    def status_counts(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS count FROM tasks GROUP BY status"
            ).fetchall()
        return {row["status"]: row["count"] for row in rows}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _task_snapshot_path(self, task_id: str) -> Path:
        return self._storage_root / "tasks" / task_id / "task.json"

    def _write_task_snapshot(self, task: dict[str, Any]) -> None:
        snapshot_path = self._task_snapshot_path(task["id"])
        snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=snapshot_path.parent,
            delete=False,
        ) as handle:
            json.dump(task, handle, ensure_ascii=True, indent=2)
            temp_path = Path(handle.name)
        temp_path.replace(snapshot_path)

    def _load_task_snapshot(self, snapshot_path: Path) -> dict[str, Any] | None:
        if not snapshot_path.is_file():
            return None
        with snapshot_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return None
        payload["delete_requested"] = int(payload.get("delete_requested", 0))
        payload["batch_index"] = int(payload.get("batch_index", 1) or 1)
        payload["batch_total"] = int(payload.get("batch_total", 1) or 1)
        return payload

    def _reconstruct_task_record(self, task_dir: Path) -> dict[str, Any] | None:
        uploads_dir = task_dir / "uploads"
        upload_candidates = sorted(path for path in uploads_dir.iterdir() if path.is_file()) if uploads_dir.exists() else []
        source_video_path = upload_candidates[0] if upload_candidates else task_dir / "uploads" / "upload.mp4"
        rendered_candidates = sorted(task_dir.glob("rendered-*.mp4"))
        transcript_path = task_dir / "transcript.json"
        captions_path = task_dir / "captions.json"
        srt_path = task_dir / "captions.srt"
        audio_path = task_dir / "audio.mp3"

        has_rendered = bool(rendered_candidates)
        has_caption_data = transcript_path.exists() or captions_path.exists() or srt_path.exists()
        timestamp = datetime.fromtimestamp(task_dir.stat().st_mtime, tz=timezone.utc).isoformat()

        return {
            "id": task_dir.name,
            "original_filename": source_video_path.name,
            "language": "ko",
            "status": "completed" if has_rendered else ("failed" if has_caption_data else "queued"),
            "batch_id": None,
            "batch_index": 1,
            "batch_total": 1,
            "blocked_by_task_id": None,
            "pending_action": "idle" if has_rendered else ("transcribe" if source_video_path.exists() else "idle"),
            "progress": 1.0 if has_rendered else (0.6 if has_caption_data else 0.0),
            "message": "Recovered from persisted task workspace",
            "delete_requested": 0,
            "source_video_path": str(source_video_path),
            "audio_path": str(audio_path) if audio_path.exists() else None,
            "transcript_path": str(transcript_path) if transcript_path.exists() else None,
            "captions_path": str(captions_path) if captions_path.exists() else None,
            "srt_path": str(srt_path) if srt_path.exists() else None,
            "rendered_video_path": str(rendered_candidates[-1]) if rendered_candidates else None,
            "error_message": None,
            "created_at": timestamp,
            "updated_at": timestamp,
            "started_at": timestamp if has_caption_data else None,
            "completed_at": timestamp if has_rendered else None,
        }
