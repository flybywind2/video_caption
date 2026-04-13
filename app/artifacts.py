from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any


def safe_filename(filename: str) -> str:
    name = Path(filename or "upload.mp4").name
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.")
    return name or "upload.mp4"


@dataclass(frozen=True, slots=True)
class TaskArtifacts:
    task_id: str
    task_dir: Path
    uploads_dir: Path
    source_video_path: Path
    audio_path: Path
    chunk_dir: Path
    transcript_path: Path
    captions_path: Path
    srt_path: Path
    rendered_video_path: Path

    def ensure_directories(self) -> None:
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.chunk_dir.mkdir(parents=True, exist_ok=True)


def build_task_artifacts(storage_root: Path, task_id: str, original_filename: str) -> TaskArtifacts:
    task_dir = storage_root / "tasks" / task_id
    uploads_dir = task_dir / "uploads"
    return TaskArtifacts(
        task_id=task_id,
        task_dir=task_dir,
        uploads_dir=uploads_dir,
        source_video_path=uploads_dir / safe_filename(original_filename),
        audio_path=task_dir / "audio.mp3",
        chunk_dir=task_dir / "audio-chunks",
        transcript_path=task_dir / "transcript.json",
        captions_path=task_dir / "captions.json",
        srt_path=task_dir / "captions.srt",
        rendered_video_path=task_dir / "rendered.mp4",
    )


def task_workspace(storage_root: Path, task_id: str) -> Path:
    return storage_root / "tasks" / task_id


def remove_task_workspace(storage_root: Path, task_id: str) -> None:
    shutil.rmtree(task_workspace(storage_root, task_id), ignore_errors=True)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        json.dump(payload, tmp, ensure_ascii=True, indent=2)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    tmp_path.replace(path)
