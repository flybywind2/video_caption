from __future__ import annotations

import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.sax.saxutils import escape as xml_escape


class FfmpegError(RuntimeError):
    pass


FONT_SUFFIXES = (".ttf", ".otf", ".ttc")


def _run(
    command: list[str],
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
    except FileNotFoundError as exc:
        raise FfmpegError(f"Command not found: {command[0]}") from exc

    if result.returncode != 0:
        raise FfmpegError(result.stderr.strip() or "FFmpeg command failed.")
    return result


def _escape_filter_value(value: str) -> str:
    return (
        value.replace("\\", "/")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(",", "\\,")
    )


def _contains_font_files(directory: Path) -> bool:
    try:
        return any(
            path.is_file() and path.suffix.lower() in FONT_SUFFIXES
            for path in directory.rglob("*")
        )
    except OSError:
        return False


def _resolve_font_dirs(font_dirs: tuple[Path, ...] | list[Path] | None) -> list[Path]:
    resolved: list[Path] = []
    for entry in font_dirs or []:
        path = Path(entry).expanduser()
        if path.is_dir() and _contains_font_files(path):
            resolved.append(path)
    return resolved


def _subtitle_needs_unicode_font(subtitle_path: Path) -> bool:
    payload = subtitle_path.read_text(encoding="utf-8", errors="ignore")
    return any(ord(char) > 127 for char in payload)


def _fontconfig_xml(font_dirs: list[Path]) -> str:
    dirs = "".join(f"<dir>{xml_escape(str(path))}</dir>" for path in font_dirs)
    return (
        "<?xml version=\"1.0\"?>\n"
        "<fontconfig>\n"
        f"{dirs}\n"
        "</fontconfig>\n"
    )


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
    subtitle_font_dirs: tuple[Path, ...] | list[Path] | None = None,
    subtitle_font_name: str = "",
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    font_dirs = _resolve_font_dirs(subtitle_font_dirs)
    if _subtitle_needs_unicode_font(srt_path) and not font_dirs:
        raise FfmpegError(
            "No usable subtitle fonts were found for non-ASCII captions. "
            "Install a Korean-capable font or set SUBTITLE_FONT_DIRS."
        )

    subtitle_filter = f"subtitles={_escape_filter_value(str(srt_path))}:charenc=UTF-8"
    if subtitle_font_name.strip():
        escaped_font_name = subtitle_font_name.strip().replace("'", "\\'")
        subtitle_filter += f":force_style='FontName={escaped_font_name}'"

    command = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        subtitle_filter,
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

    if not font_dirs:
        _run(command)
        return

    with TemporaryDirectory() as temp_dir:
        config_path = Path(temp_dir) / "fonts.conf"
        config_path.write_text(_fontconfig_xml(font_dirs), encoding="utf-8")
        env = os.environ.copy()
        env["FONTCONFIG_FILE"] = str(config_path)
        env["FONTCONFIG_PATH"] = temp_dir
        _run(command, env=env)
