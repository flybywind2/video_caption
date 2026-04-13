from __future__ import annotations

import mimetypes
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.artifacts import (
    build_task_artifacts,
    read_json,
    remove_task_workspace,
    write_json,
    write_text,
)
from app.config import Settings
from app.database import TaskRepository
from app.queue import TaskProcessor
from app.schemas import (
    ArtifactLinks,
    CaptionCue,
    CaptionUpdateRequest,
    DeleteTaskResponse,
    HealthResponse,
    TaskDetail,
    TaskSummary,
)
from app.services.captions import build_srt, normalize_cues

settings = Settings.from_env()
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


def ready_artifact_url(task_id: str, artifact_name: str, raw_path: str | None) -> str | None:
    if not raw_path:
        return None
    if not Path(raw_path).is_file():
        return None
    return f"/api/tasks/{task_id}/artifacts/{artifact_name}"


def build_artifact_links(task: dict) -> ArtifactLinks:
    task_id = task["id"]
    return ArtifactLinks(
        source_video=ready_artifact_url(task_id, "source_video", task.get("source_video_path")),
        rendered_video=ready_artifact_url(task_id, "rendered_video", task.get("rendered_video_path")),
        captions_json=ready_artifact_url(task_id, "captions_json", task.get("captions_path")),
        transcript_json=ready_artifact_url(task_id, "transcript_json", task.get("transcript_path")),
        srt=ready_artifact_url(task_id, "srt", task.get("srt_path")),
    )


def task_to_summary(task: dict) -> TaskSummary:
    return TaskSummary(
        id=task["id"],
        original_filename=task["original_filename"],
        language=task["language"],
        status=task["status"],
        pending_action=task.get("pending_action", "idle"),
        progress=float(task.get("progress") or 0.0),
        message=task.get("message") or "",
        error_message=task.get("error_message"),
        delete_requested=bool(task.get("delete_requested")),
        created_at=task["created_at"],
        updated_at=task["updated_at"],
        started_at=task.get("started_at"),
        completed_at=task.get("completed_at"),
        artifacts=build_artifact_links(task),
    )


def task_to_detail(task: dict) -> TaskDetail:
    transcript_payload = {}
    cues_payload = []

    transcript_path = task.get("transcript_path")
    captions_path = task.get("captions_path")

    if transcript_path and Path(transcript_path).is_file():
        transcript_payload = read_json(Path(transcript_path))
    if captions_path and Path(captions_path).is_file():
        cues_payload = read_json(Path(captions_path))

    return TaskDetail(
        **task_to_summary(task).dict(),
        transcript_text=transcript_payload.get("text"),
        speakers=transcript_payload.get("speakers") or [],
        cues=[CaptionCue(**cue) for cue in normalize_cues(cues_payload)],
    )


def get_repository(request: Request) -> TaskRepository:
    return request.app.state.repository


def get_processor(request: Request) -> TaskProcessor:
    return request.app.state.processor


def delete_task_files(repository: TaskRepository, task_id: str) -> None:
    remove_task_workspace(settings.storage_root, task_id)
    repository.delete_task(task_id)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.ensure_directories()
    repository = TaskRepository(settings.database_path)
    repository.init_db()
    processor = TaskProcessor(settings, repository)
    app.state.repository = repository
    app.state.processor = processor
    await processor.start()
    try:
        yield
    finally:
        await processor.stop()
        repository.close()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    repository = get_repository(request)
    processor = get_processor(request)
    return HealthResponse(
        status="ok",
        ffmpeg_available=shutil.which(settings.ffmpeg_bin) is not None or Path(settings.ffmpeg_bin).is_file(),
        ffprobe_available=shutil.which(settings.ffprobe_bin) is not None or Path(settings.ffprobe_bin).is_file(),
        whisper_configured=processor.whisper.is_configured(),
        queue_size=processor.queue_size(),
        worker_count=settings.worker_count,
        task_counts=repository.status_counts(),
    )


@app.get("/api/tasks", response_model=list[TaskSummary])
async def list_tasks(request: Request) -> list[TaskSummary]:
    repository = get_repository(request)
    return [task_to_summary(task) for task in repository.list_tasks()]


@app.post("/api/tasks", response_model=TaskDetail)
async def create_task(
    request: Request,
    file: UploadFile = File(...),
    language: str = Form("en"),
) -> TaskDetail:
    if not file.filename:
        raise HTTPException(status_code=400, detail="A video file is required.")

    task_id = uuid4().hex[:12]
    artifacts = build_task_artifacts(settings.storage_root, task_id, file.filename)
    artifacts.ensure_directories()

    with artifacts.source_video_path.open("wb") as handle:
        while chunk := await file.read(1024 * 1024):
            handle.write(chunk)
    await file.close()

    repository = get_repository(request)
    processor = get_processor(request)
    task = repository.create_task(
        {
            "id": task_id,
            "original_filename": file.filename,
            "language": language,
            "status": "queued",
            "pending_action": "transcribe",
            "progress": 0.05,
            "message": "Upload complete. Waiting in queue.",
            "source_video_path": str(artifacts.source_video_path),
            "audio_path": str(artifacts.audio_path),
            "transcript_path": str(artifacts.transcript_path),
            "captions_path": str(artifacts.captions_path),
            "srt_path": str(artifacts.srt_path),
            "rendered_video_path": str(artifacts.rendered_video_path),
        }
    )
    await processor.enqueue(task_id, action="transcribe")
    return task_to_detail(task)


@app.get("/api/tasks/{task_id}", response_model=TaskDetail)
async def get_task(task_id: str, request: Request) -> TaskDetail:
    repository = get_repository(request)
    task = repository.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task_to_detail(task)


@app.put("/api/tasks/{task_id}/captions", response_model=TaskDetail)
async def update_captions(
    task_id: str,
    payload: CaptionUpdateRequest,
    request: Request,
) -> TaskDetail:
    repository = get_repository(request)
    processor = get_processor(request)
    task = repository.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    captions_path = Path(task["captions_path"])
    srt_path = Path(task["srt_path"])
    if not captions_path.parent.exists():
        raise HTTPException(status_code=409, detail="Task artifacts are missing.")

    cues = normalize_cues([cue.dict() for cue in payload.cues])
    if not cues:
        raise HTTPException(status_code=400, detail="At least one caption cue is required.")

    write_json(captions_path, cues)
    write_text(srt_path, build_srt(cues))
    repository.update_task(
        task_id,
        message="Captions saved",
        error_message=None,
        delete_requested=False,
    )

    if payload.rerender:
        await processor.enqueue(task_id, action="render")

    refreshed = repository.get_task(task_id)
    if not refreshed:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task_to_detail(refreshed)


@app.post("/api/tasks/{task_id}/retry", response_model=TaskDetail)
async def retry_task(task_id: str, request: Request) -> TaskDetail:
    repository = get_repository(request)
    processor = get_processor(request)
    task = repository.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    await processor.enqueue(task_id, action="transcribe")
    refreshed = repository.get_task(task_id)
    if not refreshed:
        raise HTTPException(status_code=404, detail="Task not found.")
    return task_to_detail(refreshed)


@app.delete("/api/tasks/{task_id}", response_model=DeleteTaskResponse)
async def delete_task(task_id: str, request: Request) -> DeleteTaskResponse:
    repository = get_repository(request)
    task = repository.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    if task["status"] in {"processing", "rendering"}:
        repository.update_task(
            task_id,
            delete_requested=True,
            status="deleting",
            message="Delete requested. Cleanup will happen after the current step.",
        )
        return DeleteTaskResponse(
            accepted=True,
            detail="Delete request accepted for an active task.",
        )

    delete_task_files(repository, task_id)
    return DeleteTaskResponse(accepted=False, detail="Task deleted.")


@app.get("/api/tasks/{task_id}/artifacts/{artifact_name}")
async def get_artifact(task_id: str, artifact_name: str, request: Request) -> FileResponse:
    repository = get_repository(request)
    task = repository.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")

    artifact_map = {
        "source_video": task.get("source_video_path"),
        "rendered_video": task.get("rendered_video_path"),
        "captions_json": task.get("captions_path"),
        "transcript_json": task.get("transcript_path"),
        "srt": task.get("srt_path"),
    }
    raw_path = artifact_map.get(artifact_name)
    if not raw_path:
        raise HTTPException(status_code=404, detail="Artifact not found.")

    path = Path(raw_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact file is not ready yet.")

    media_type, _ = mimetypes.guess_type(path.name)
    return FileResponse(path, media_type=media_type or "application/octet-stream")
