from __future__ import annotations

import subprocess
from pathlib import Path


class FfmpegError(RuntimeError):
    pass


def _run(command: list[str]) -> None:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise FfmpegError(f"Command not found: {command[0]}") from exc

    if result.returncode != 0:
        raise FfmpegError(result.stderr.strip() or "FFmpeg command failed.")


def extract_audio(video_path: Path, audio_path: Path, ffmpeg_bin: str) -> None:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(audio_path),
        ]
    )


def render_subtitles(
    video_path: Path,
    srt_path: Path,
    output_path: Path,
    ffmpeg_bin: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subtitle_filter_path = (
        str(srt_path).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
    )
    _run(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            str(video_path),
            "-vf",
            f"subtitles={subtitle_filter_path}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
