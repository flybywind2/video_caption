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
from app.services.ffmpeg import (
    extract_audio,
    probe_duration,
    render_subtitles,
    split_audio,
)
from app.services.whisper import (
    WhisperClient,
    WhisperPayloadTooLargeError,
    merge_transcripts,
)


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
            message="Extracting compressed audio from uploaded video",
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
        transcript = await self._transcribe_with_fallback(
            task_id,
            artifacts.audio_path,
            artifacts.chunk_dir,
            task["language"],
        )
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

    async def _transcribe_with_fallback(
        self,
        task_id: str,
        audio_path: Path,
        chunk_dir: Path,
        language: str,
    ) -> dict[str, Any]:
        audio_size = audio_path.stat().st_size if audio_path.exists() else 0
        if audio_size > self.settings.whisper_max_upload_bytes:
            return await self._transcribe_in_chunks(
                task_id,
                audio_path,
                chunk_dir,
                language,
                reason=(
                    "Compressed audio still exceeds the configured upload limit. "
                    "Switching to chunked transcription"
                ),
            )

        try:
            return await self.whisper.transcribe(audio_path, language)
        except WhisperPayloadTooLargeError:
            return await self._transcribe_in_chunks(
                task_id,
                audio_path,
                chunk_dir,
                language,
                reason="Gateway rejected single audio upload with HTTP 413",
            )

    async def _transcribe_in_chunks(
        self,
        task_id: str,
        audio_path: Path,
        chunk_dir: Path,
        language: str,
        *,
        reason: str,
    ) -> dict[str, Any]:
        self.repository.update_task(
            task_id,
            status="processing",
            progress=0.4,
            message=(
                f"{reason}. Splitting audio into "
                f"{self.settings.whisper_chunk_seconds // 60}-minute chunks"
            ),
        )

        chunk_paths = await asyncio.to_thread(
            split_audio,
            audio_path,
            chunk_dir,
            self.settings.whisper_chunk_seconds,
            self.settings.ffmpeg_bin,
        )

        merged_inputs: list[tuple[float, dict[str, Any]]] = []
        offset = 0.0
        total_chunks = len(chunk_paths)

        for index, chunk_path in enumerate(chunk_paths, start=1):
            if self._should_delete(task_id):
                self._purge_task(task_id)
                raise RuntimeError("Task deleted while transcribing audio chunks.")

            self.repository.update_task(
                task_id,
                status="processing",
                progress=0.4 + (0.25 * index / max(total_chunks, 1)),
                message=f"Transcribing chunk {index}/{total_chunks}",
            )
            transcript = await self.whisper.transcribe(chunk_path, language)
            chunk_duration = await asyncio.to_thread(
                probe_duration,
                chunk_path,
                self.settings.ffprobe_bin,
            )
            merged_inputs.append((offset, transcript))
            offset += max(chunk_duration, float(transcript.get("duration") or 0.0), 0.1)

        return merge_transcripts(merged_inputs)

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
