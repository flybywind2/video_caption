from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


class CaptionStyle(BaseModel):
    font_family: str = "Sans"
    font_size: int = Field(48, ge=18, le=120)
    text_color: str = "#ffffff"
    outline_color: str = "#101010"
    alignment: Literal[
        "top-left",
        "top-center",
        "top-right",
        "middle-left",
        "middle-center",
        "middle-right",
        "bottom-left",
        "bottom-center",
        "bottom-right",
    ] = "bottom-center"
    position_x: int = Field(50, ge=0, le=100)
    position_y: int = Field(90, ge=0, le=100)


class CueStyleOverride(BaseModel):
    font_family: str | None = None
    font_size: int | None = Field(None, ge=18, le=120)
    text_color: str | None = None
    outline_color: str | None = None
    alignment: Literal[
        "top-left",
        "top-center",
        "top-right",
        "middle-left",
        "middle-center",
        "middle-right",
        "bottom-left",
        "bottom-center",
        "bottom-right",
    ] | None = None
    position_x: int | None = Field(None, ge=0, le=100)
    position_y: int | None = Field(None, ge=0, le=100)


class CaptionCue(BaseModel):
    id: str
    start: float = Field(..., ge=0)
    end: float = Field(..., ge=0)
    text: str = Field(..., min_length=1)
    speaker: str | None = None
    style: CueStyleOverride = Field(default_factory=CueStyleOverride)


class CaptionUpdateRequest(BaseModel):
    global_style: CaptionStyle = Field(default_factory=CaptionStyle)
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
    global_style: CaptionStyle = Field(default_factory=CaptionStyle)
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
