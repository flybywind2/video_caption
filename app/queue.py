from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.artifacts import (
    build_task_artifacts,
    read_json,
    remove_task_workspace,
    write_json,
    write_text,
)
from app.config import Settings
from app.database import TaskRepository, utc_now
from app.services.captions import build_srt, cues_from_transcript, normalize_cues
from app.services.ffmpeg import extract_audio, render_subtitles
from app.services.whisper import WhisperClient


@dataclass(frozen=True, slots=True)
class QueuedJob:
    task_id: str
    action: str


class TaskProcessor:
    def __init__(self, settings: Settings, repository: TaskRepository) -> None:
        self.settings = settings
        self.repository = repository
        self.whisper = WhisperClient(settings)
        self.queue: asyncio.Queue[QueuedJob] = asyncio.Queue()
        self.workers: list[asyncio.Task[Any]] = []
        self._queued_keys: set[str] = set()

    async def start(self) -> None:
        for worker_id in range(self.settings.worker_count):
            self.workers.append(asyncio.create_task(self._worker_loop(worker_id)))
        await self.requeue_pending_tasks()

    async def stop(self) -> None:
        for worker in self.workers:
            worker.cancel()
        if self.workers:
            await asyncio.gather(*self.workers, return_exceptions=True)
        self.workers.clear()

    async def requeue_pending_tasks(self) -> None:
        for task in self.repository.list_unfinished_tasks():
            if task.get("delete_requested"):
                self._purge_task(task["id"])
                continue
            action = task.get("pending_action") or "transcribe"
            await self.enqueue(task["id"], action=action, refresh_status=False)

    async def enqueue(
        self,
        task_id: str,
        *,
        action: str = "transcribe",
        refresh_status: bool = True,
    ) -> None:
        task = self.repository.get_task(task_id)
        if not task:
            return

        key = f"{task_id}:{action}"
        if key in self._queued_keys:
            return

        self._queued_keys.add(key)
        if refresh_status:
            if action == "render":
                self.repository.update_task(
                    task_id,
                    status="queued",
                    pending_action="render",
                    progress=min(max(float(task.get("progress", 0)), 0.6), 0.9),
                    message="Queued for subtitle render",
                    error_message=None,
                )
            else:
                self.repository.update_task(
                    task_id,
                    status="queued",
                    pending_action="transcribe",
                    progress=0.05,
                    message="Queued for audio extraction and transcription",
                    error_message=None,
                    completed_at=None,
                )

        await self.queue.put(QueuedJob(task_id=task_id, action=action))

    async def _worker_loop(self, worker_id: int) -> None:
        while True:
            job = await self.queue.get()
            key = f"{job.task_id}:{job.action}"
            self._queued_keys.discard(key)

            try:
                await self._run_job(job)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                task = self.repository.get_task(job.task_id)
                if task:
                    if task.get("delete_requested"):
                        self._purge_task(job.task_id)
                    else:
                        self.repository.update_task(
                            job.task_id,
                            status="failed",
                            message="Processing failed",
                            error_message=str(exc),
                            completed_at=utc_now(),
                        )
            finally:
                self.queue.task_done()

    async def _run_job(self, job: QueuedJob) -> None:
        task = self.repository.get_task(job.task_id)
        if not task:
            return
        if task.get("delete_requested"):
            self._purge_task(job.task_id)
            return

        if job.action == "render":
            await self._render_only(job.task_id)
        else:
            await self._transcribe_and_render(job.task_id)

    async def _transcribe_and_render(self, task_id: str) -> None:
        task = self.repository.get_task(task_id)
        if not task:
            return

        source_path = Path(task["source_video_path"])
        artifacts = build_task_artifacts(
            self.settings.storage_root, task_id, task["original_filename"]
        )
        artifacts.ensure_directories()

        self.repository.update_task(
            task_id,
            status="processing",
            pending_action="transcribe",
            progress=0.1,
            message="Extracting audio from uploaded video",
            error_message=None,
            started_at=utc_now(),
        )
        await asyncio.to_thread(
            extract_audio,
            source_path,
            artifacts.audio_path,
            self.settings.ffmpeg_bin,
        )

        if self._should_delete(task_id):
            self._purge_task(task_id)
            return

        self.repository.update_task(
            task_id,
            status="processing",
            progress=0.35,
            message="Calling Whisper transcription API",
        )
        transcript = await self.whisper.transcribe(artifacts.audio_path, task["language"])
        cues = cues_from_transcript(transcript)
        if not cues:
            raise RuntimeError("Transcription returned no usable caption segments.")

        if self._should_delete(task_id):
            self._purge_task(task_id)
            return

        write_json(artifacts.transcript_path, transcript)
        write_json(artifacts.captions_path, cues)
        write_text(artifacts.srt_path, build_srt(cues))

        self.repository.update_task(
            task_id,
            status="processing",
            pending_action="render",
            progress=0.7,
            message="Rendering subtitled video",
        )
        await asyncio.to_thread(
            render_subtitles,
            source_path,
            artifacts.srt_path,
            artifacts.rendered_video_path,
            self.settings.ffmpeg_bin,
        )

        if self._should_delete(task_id):
            self._purge_task(task_id)
            return

        self.repository.update_task(
            task_id,
            status="completed",
            pending_action="idle",
            progress=1.0,
            message="Subtitled video is ready",
            error_message=None,
            completed_at=utc_now(),
        )

    async def _render_only(self, task_id: str) -> None:
        task = self.repository.get_task(task_id)
        if not task:
            return

        captions_path = Path(task["captions_path"])
        if not captions_path.exists():
            raise RuntimeError("Captions are missing. Generate a transcript first.")

        source_path = Path(task["source_video_path"])
        srt_path = Path(task["srt_path"])
        rendered_video_path = Path(task["rendered_video_path"])

        cues = normalize_cues(read_json(captions_path))
        if not cues:
            raise RuntimeError("No captions available to render.")

        write_json(captions_path, cues)
        write_text(srt_path, build_srt(cues))

        self.repository.update_task(
            task_id,
            status="rendering",
            pending_action="render",
            progress=0.85,
            message="Rendering edited subtitles",
            error_message=None,
        )

        await asyncio.to_thread(
            render_subtitles,
            source_path,
            srt_path,
            rendered_video_path,
            self.settings.ffmpeg_bin,
        )

        if self._should_delete(task_id):
            self._purge_task(task_id)
            return

        self.repository.update_task(
            task_id,
            status="completed",
            pending_action="idle",
            progress=1.0,
            message="Updated subtitle render is ready",
            error_message=None,
            completed_at=utc_now(),
        )

    def _should_delete(self, task_id: str) -> bool:
        task = self.repository.get_task(task_id)
        return bool(task and task.get("delete_requested"))

    def _purge_task(self, task_id: str) -> None:
        remove_task_workspace(self.settings.storage_root, task_id)
        self.repository.delete_task(task_id)

    def queue_size(self) -> int:
        return self.queue.qsize()
