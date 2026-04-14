from __future__ import annotations

import json
import logging
import mimetypes
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.artifacts import (
    build_task_artifacts,
    read_json,
    remove_task_workspace,
    split_part_filename,
    temp_upload_path,
    write_json,
    write_text,
)
from app.config import Settings
from app.database import TaskRepository, utc_now
from app.queue import TaskProcessor
from app.schemas import (
    ArtifactLinks,
    CaptionCue,
    CaptionUpdateRequest,
    DeleteTaskResponse,
    HealthResponse,
    TaskCreateResponse,
    TaskDetail,
    TaskSummary,
)
from app.services.captions import (
    build_ass,
    build_srt,
    default_caption_style,
    normalize_caption_document,
)
from app.services.ffmpeg import split_video

settings = Settings.from_env()
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
logger = logging.getLogger("video_caption.main")


def configure_app_logging() -> None:
    app_logger = logging.getLogger("video_caption")
    uvicorn_logger = logging.getLogger("uvicorn.error")

    if uvicorn_logger.handlers:
        app_logger.handlers = list(uvicorn_logger.handlers)
        app_logger.setLevel(uvicorn_logger.level or logging.INFO)
        app_logger.propagate = False
        return

    if not app_logger.handlers:
        logging.basicConfig(level=logging.INFO)
    app_logger.setLevel(logging.INFO)


def ready_artifact_url(task_id: str, artifact_name: str, raw_path: str | None) -> str | None:
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_file():
        return None
    return f"api/tasks/{task_id}/artifacts/{artifact_name}?file={quote(path.name)}"


def resolve_artifact_path(
    raw_path: str,
    artifact_name: str,
    requested_filename: str | None,
) -> Path:
    current_path = Path(raw_path)
    if not requested_filename:
        return current_path

    filename = Path(requested_filename).name
    if filename != requested_filename:
        raise HTTPException(status_code=404, detail="Artifact not found.")

    if filename == current_path.name:
        return current_path

    if artifact_name == "rendered_video":
        candidate = current_path.parent / filename
        if candidate.parent == current_path.parent:
            return candidate

    raise HTTPException(status_code=404, detail="Artifact not found.")


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
        batch_id=task.get("batch_id"),
        batch_index=int(task.get("batch_index") or 1),
        batch_total=int(task.get("batch_total") or 1),
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
    caption_document = normalize_caption_document(
        [],
        fallback_style=default_caption_style(),
    )

    transcript_path = task.get("transcript_path")
    captions_path = task.get("captions_path")

    if transcript_path and Path(transcript_path).is_file():
        transcript_payload = read_json(Path(transcript_path))
    if captions_path and Path(captions_path).is_file():
        caption_document = normalize_caption_document(read_json(Path(captions_path)))

    return TaskDetail(
        **task_to_summary(task).dict(),
        transcript_text=transcript_payload.get("text"),
        speakers=transcript_payload.get("speakers") or [],
        global_style=caption_document["global_style"],
        cues=[CaptionCue(**cue) for cue in caption_document["cues"]],
    )


def get_repository(request: Request) -> TaskRepository:
    return request.app.state.repository


def get_processor(request: Request) -> TaskProcessor:
    return request.app.state.processor


def delete_task_files(repository: TaskRepository, task_id: str) -> None:
    remove_task_workspace(settings.storage_root, task_id)
    repository.delete_task(task_id)


async def write_upload_to_path(file: UploadFile, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        while chunk := await file.read(1024 * 1024):
            handle.write(chunk)


def collect_uploads(
    file: UploadFile | None,
    files: list[UploadFile] | None,
) -> list[UploadFile]:
    uploads: list[UploadFile] = []
    if file and file.filename:
        uploads.append(file)
    for upload in files or []:
        if upload and upload.filename:
            uploads.append(upload)
    return uploads


async def register_upload(
    repository: TaskRepository,
    processor: TaskProcessor,
    upload: UploadFile,
    language: str,
    split_mode: str,
) -> list[dict]:
    temp_source_path = temp_upload_path(settings.storage_root, upload.filename)
    try:
        await write_upload_to_path(upload, temp_source_path)
    finally:
        await upload.close()

    if split_mode == "chunked":
        split_batch_id = uuid4().hex[:10]
        split_dir = temp_source_path.parent / f"{temp_source_path.stem}-parts"
        created_tasks: list[dict] = []
        batch_created_at = utc_now()
        try:
            chunk_paths = split_video(
                temp_source_path,
                split_dir,
                settings.upload_split_chunk_seconds,
                settings.ffmpeg_bin,
            )
            total = len(chunk_paths)
            previous_task_id: str | None = None
            for index, chunk_path in enumerate(chunk_paths, start=1):
                task_id = uuid4().hex[:12]
                part_filename = split_part_filename(upload.filename, index, total)
                artifacts = build_task_artifacts(settings.storage_root, task_id, part_filename)
                artifacts.ensure_directories()
                shutil.move(str(chunk_path), str(artifacts.source_video_path))
                task = repository.create_task(
                    create_task_record(
                        task_id=task_id,
                        original_filename=part_filename,
                        language=language,
                        artifacts=artifacts,
                        status="queued" if index == 1 else "blocked",
                        message=(
                            f"Split part {index}/{total} queued."
                            if index == 1
                            else f"Split part {index}/{total} waiting for the previous part."
                        ),
                        batch_id=split_batch_id,
                        batch_index=index,
                        batch_total=total,
                        blocked_by_task_id=previous_task_id,
                        created_at=batch_created_at,
                    )
                )
                created_tasks.append(task)
                previous_task_id = task_id
        except Exception:
            for task in created_tasks:
                delete_task_files(repository, task["id"])
            raise
        finally:
            shutil.rmtree(split_dir, ignore_errors=True)
            temp_source_path.unlink(missing_ok=True)

        if not created_tasks:
            raise HTTPException(status_code=500, detail="Split upload produced no tasks.")

        await processor.enqueue(created_tasks[0]["id"], action="transcribe")
        return created_tasks

    task_id = uuid4().hex[:12]
    artifacts = build_task_artifacts(settings.storage_root, task_id, upload.filename)
    artifacts.ensure_directories()
    shutil.move(str(temp_source_path), str(artifacts.source_video_path))
    task = repository.create_task(
        create_task_record(
            task_id=task_id,
            original_filename=upload.filename,
            language=language,
            artifacts=artifacts,
            status="queued",
            message="Upload complete. Waiting in queue.",
        )
    )
    await processor.enqueue(task_id, action="transcribe")
    return [task]


def create_task_record(
    *,
    task_id: str,
    original_filename: str,
    language: str,
    artifacts,
    status: str,
    message: str,
    batch_id: str | None = None,
    batch_index: int = 1,
    batch_total: int = 1,
    blocked_by_task_id: str | None = None,
    created_at: str | None = None,
) -> dict:
    return {
        "id": task_id,
        "original_filename": original_filename,
        "language": language,
        "status": status,
        "batch_id": batch_id,
        "batch_index": batch_index,
        "batch_total": batch_total,
        "blocked_by_task_id": blocked_by_task_id,
        "pending_action": "transcribe",
        "progress": 0.0 if status == "blocked" else 0.05,
        "message": message,
        "source_video_path": str(artifacts.source_video_path),
        "audio_path": str(artifacts.audio_path),
        "transcript_path": str(artifacts.transcript_path),
        "captions_path": str(artifacts.captions_path),
        "srt_path": str(artifacts.srt_path),
        "rendered_video_path": None,
        "created_at": created_at,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_app_logging()
    settings.ensure_directories()
    repository = TaskRepository(settings.database_path, settings.storage_root)
    repository.init_db()
    recovered = repository.recover_from_storage()
    repository.backfill_snapshots()
    processor = TaskProcessor(settings, repository)
    app.state.repository = repository
    app.state.processor = processor
    logger.info(
        "startup storage_root=%s database_path=%s recovered_tasks=%s",
        settings.storage_root,
        settings.database_path,
        recovered,
    )
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
    recovered = repository.ensure_recovered_if_empty()
    if recovered:
        logger.info("health fallback_recovery recovered_tasks=%s", recovered)
    return HealthResponse(
        status="ok",
        ffmpeg_available=shutil.which(settings.ffmpeg_bin) is not None or Path(settings.ffmpeg_bin).is_file(),
        ffprobe_available=shutil.which(settings.ffprobe_bin) is not None or Path(settings.ffprobe_bin).is_file(),
        whisper_configured=processor.whisper.is_configured(),
        queue_size=processor.queue_size(),
        worker_count=settings.worker_count,
        task_counts=repository.status_counts(),
        upload_split_threshold_bytes=settings.upload_split_threshold_bytes,
        upload_split_prompt_seconds=settings.upload_split_prompt_seconds,
        upload_split_chunk_seconds=settings.upload_split_chunk_seconds,
    )


@app.get("/api/tasks", response_model=list[TaskSummary])
async def list_tasks(request: Request) -> list[TaskSummary]:
    repository = get_repository(request)
    recovered = repository.ensure_recovered_if_empty()
    if recovered:
        logger.info("list_tasks fallback_recovery recovered_tasks=%s", recovered)
    return [task_to_summary(task) for task in repository.list_tasks()]


@app.post("/api/tasks", response_model=TaskCreateResponse)
async def create_task(
    request: Request,
    file: UploadFile | None = File(default=None),
    files: list[UploadFile] | None = File(default=None),
    language: str = Form("en"),
    split_mode: str = Form("single"),
    split_mode_plan: str | None = Form(default=None),
) -> TaskCreateResponse:
    uploads = collect_uploads(file, files)
    if not uploads:
        raise HTTPException(status_code=400, detail="At least one video file is required.")

    repository = get_repository(request)
    processor = get_processor(request)
    if split_mode_plan:
        try:
            planned_modes = json.loads(split_mode_plan)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid split mode plan.") from exc
        if not isinstance(planned_modes, list) or len(planned_modes) != len(uploads):
            raise HTTPException(status_code=400, detail="Split mode plan does not match uploads.")
        split_modes = [str(item or "single") for item in planned_modes]
    else:
        split_modes = [split_mode] + ["single"] * (len(uploads) - 1)

    for mode in split_modes:
        if mode not in {"single", "chunked"}:
            raise HTTPException(status_code=400, detail="Unsupported split mode.")

    created_tasks: list[dict] = []
    for upload, mode in zip(uploads, split_modes):
        created_tasks.extend(await register_upload(repository, processor, upload, language, mode))

    if not created_tasks:
        raise HTTPException(status_code=500, detail="No tasks were created.")

    input_file_count = len(uploads)
    task_count = len(created_tasks)
    batch_created = any((task.get("batch_total") or 1) > 1 for task in created_tasks)
    if input_file_count == 1 and task_count == 1:
        message = "Task queued."
    elif input_file_count == 1:
        message = f"1개 파일이 {task_count}개 분할 작업으로 등록되었습니다."
    else:
        message = f"{input_file_count}개 파일이 등록되었습니다. 총 {task_count}개 작업이 큐에 추가되었습니다."

    return TaskCreateResponse(
        tasks=[task_to_detail(task) for task in created_tasks],
        primary_task_id=created_tasks[0]["id"],
        batch_created=batch_created,
        input_file_count=input_file_count,
        task_count=task_count,
        message=message,
    )


@app.get("/api/tasks/{task_id}", response_model=TaskDetail)
async def get_task(task_id: str, request: Request) -> TaskDetail:
    repository = get_repository(request)
    recovered = repository.ensure_recovered_if_empty()
    if recovered:
        logger.info(
            "get_task fallback_recovery task_id=%s recovered_tasks=%s",
            task_id,
            recovered,
        )
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

    artifacts = build_task_artifacts(
        settings.storage_root,
        task_id,
        task["original_filename"],
    )
    captions_path = Path(task["captions_path"])
    srt_path = Path(task["srt_path"])
    if not captions_path.parent.exists():
        raise HTTPException(status_code=409, detail="Task artifacts are missing.")

    caption_document = normalize_caption_document(
        {
            "global_style": payload.global_style.dict(),
            "cues": [cue.dict() for cue in payload.cues],
        }
    )
    if not caption_document["cues"]:
        raise HTTPException(status_code=400, detail="At least one caption cue is required.")

    write_json(captions_path, caption_document)
    write_text(srt_path, build_srt(caption_document["cues"]))
    write_text(
        artifacts.ass_path,
        build_ass(
            caption_document["cues"],
            caption_document["global_style"],
            default_font_family=settings.subtitle_font_name or "NanumGothic",
            font_dirs=settings.subtitle_font_dirs,
        ),
    )
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

    path = resolve_artifact_path(
        raw_path,
        artifact_name,
        request.query_params.get("file"),
    )
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Artifact file is not ready yet.")

    media_type, _ = mimetypes.guess_type(path.name)
    return FileResponse(path, media_type=media_type or "application/octet-stream")
