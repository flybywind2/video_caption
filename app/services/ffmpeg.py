from __future__ import annotations

import subprocess
from pathlib import Path


class FfmpegError(RuntimeError):
    pass


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
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
    return result


def extract_audio(video_path: Path, audio_path: Path, ffmpeg_bin: str) -> None:
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            str(video_path),
            "-vn",
            "-map",
            "0:a:0?",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "32k",
            str(audio_path),
        ]
    )


def split_audio(
    audio_path: Path,
    chunk_dir: Path,
    chunk_seconds: int,
    ffmpeg_bin: str,
) -> list[Path]:
    chunk_dir.mkdir(parents=True, exist_ok=True)
    for existing in chunk_dir.glob("chunk-*.mp3"):
        existing.unlink(missing_ok=True)

    pattern = chunk_dir / "chunk-%03d.mp3"
    _run(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            str(audio_path),
            "-f",
            "segment",
            "-segment_time",
            str(chunk_seconds),
            "-reset_timestamps",
            "1",
            "-map",
            "0:a:0",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "32k",
            str(pattern),
        ]
    )

    chunk_paths = sorted(chunk_dir.glob("chunk-*.mp3"))
    if not chunk_paths:
        raise FfmpegError("Audio splitting produced no chunks.")
    return chunk_paths


def probe_duration(media_path: Path, ffprobe_bin: str) -> float:
    result = _run(
        [
            ffprobe_bin,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ]
    )
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise FfmpegError(f"Could not parse media duration for {media_path}.") from exc


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
