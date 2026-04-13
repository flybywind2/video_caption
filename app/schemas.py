from __future__ import annotations

from pydantic import BaseModel, Field


class CaptionCue(BaseModel):
    id: str
    start: float = Field(..., ge=0)
    end: float = Field(..., ge=0)
    text: str = Field(..., min_length=1)
    speaker: str | None = None


class CaptionUpdateRequest(BaseModel):
    cues: list[CaptionCue]
    rerender: bool = True


class ArtifactLinks(BaseModel):
    source_video: str | None = None
    rendered_video: str | None = None
    captions_json: str | None = None
    transcript_json: str | None = None
    srt: str | None = None


class TaskSummary(BaseModel):
    id: str
    original_filename: str
    language: str
    status: str
    pending_action: str
    progress: float
    message: str
    error_message: str | None = None
    delete_requested: bool
    created_at: str
    updated_at: str
    started_at: str | None = None
    completed_at: str | None = None
    artifacts: ArtifactLinks


class TaskDetail(TaskSummary):
    transcript_text: str | None = None
    speakers: list[dict] = Field(default_factory=list)
    cues: list[CaptionCue] = Field(default_factory=list)


class DeleteTaskResponse(BaseModel):
    accepted: bool
    detail: str


class HealthResponse(BaseModel):
    status: str
    ffmpeg_available: bool
    ffprobe_available: bool
    whisper_configured: bool
    queue_size: int
    worker_count: int
    task_counts: dict[str, int]
