from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path


class FfmpegError(RuntimeError):
    pass


FONT_SUFFIXES = (".ttf", ".otf", ".ttc")
PREFERRED_HANGUL_FONTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Noto Sans CJK KR", ("notosanscjk", "notoserifcjk", "sourcehansans", "sourcehanserif")),
    ("Noto Sans KR", ("notosanskr",)),
    ("NanumGothic", ("nanumgothic", "nanummyeongjo", "nanumbarungothic")),
    ("Malgun Gothic", ("malgun",)),
    ("Droid Sans Fallback", ("droidsansfallback",)),
    ("Baekmuk Gulim", ("baekmuk",)),
    ("UnDotum", ("undotum",)),
)


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


def _font_key(value: str) -> str:
    return "".join(char for char in value.lower() if char.isalnum())


def _pick_fontsdir(font_dirs: list[Path]) -> Path | None:
    for path in font_dirs:
        if path.is_dir():
            return path
    return None


def _subtitle_contains_hangul(subtitle_path: Path) -> bool:
    payload = subtitle_path.read_text(encoding="utf-8", errors="ignore")
    return any(
        ("\u1100" <= char <= "\u11ff")
        or ("\u3130" <= char <= "\u318f")
        or ("\uac00" <= char <= "\ud7a3")
        for char in payload
    )


def _guess_hangul_font_name(font_dirs: list[Path]) -> str:
    for font_dir in font_dirs:
        try:
            candidates = sorted(font_dir.rglob("*"))
        except OSError:
            continue
        for path in candidates:
            if not path.is_file() or path.suffix.lower() not in FONT_SUFFIXES:
                continue
            key = _font_key(path.stem)
            for family, patterns in PREFERRED_HANGUL_FONTS:
                if any(pattern in key for pattern in patterns):
                    return family
    return ""


def _fc_match_hangul_font() -> str:
    try:
        result = subprocess.run(
            [
                "fc-match",
                "-f",
                "%{family[0]}",
                "sans-serif:lang=ko",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return ""

    if result.returncode != 0:
        return ""
    return result.stdout.strip()


@lru_cache(maxsize=8)
def _subtitles_filter_supports_option(ffmpeg_bin: str, option_name: str) -> bool:
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-h", "filter=subtitles"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return False

    if result.returncode != 0:
        return False
    return option_name in result.stdout


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


def split_video(
    video_path: Path,
    chunk_dir: Path,
    chunk_seconds: int,
    ffmpeg_bin: str,
) -> list[Path]:
    chunk_dir.mkdir(parents=True, exist_ok=True)
    suffix = video_path.suffix or ".mp4"
    for existing in chunk_dir.glob("part-*"):
        existing.unlink(missing_ok=True)

    pattern = chunk_dir / f"part-%03d{suffix}"
    _run(
        [
            ffmpeg_bin,
            "-y",
            "-i",
            str(video_path),
            "-map",
            "0",
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(chunk_seconds),
            "-reset_timestamps",
            "1",
            str(pattern),
        ]
    )

    chunk_paths = sorted(chunk_dir.glob(f"part-*{suffix}"))
    if not chunk_paths:
        raise FfmpegError("Video splitting produced no chunks.")
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
    subtitle_path: Path,
    output_path: Path,
    ffmpeg_bin: str,
    subtitle_font_dirs: tuple[Path, ...] | list[Path] | None = None,
    subtitle_font_name: str = "",
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    font_dirs = _resolve_font_dirs(subtitle_font_dirs)
    needs_unicode_font = _subtitle_needs_unicode_font(subtitle_path)
    if needs_unicode_font and not font_dirs:
        raise FfmpegError(
            "No usable subtitle fonts were found for non-ASCII captions. "
            "Install a Korean-capable font or set SUBTITLE_FONT_DIRS."
        )

    fontsdir = _pick_fontsdir(font_dirs)
    selected_font_name = subtitle_font_name.strip()
    if not selected_font_name and _subtitle_contains_hangul(subtitle_path):
        selected_font_name = _fc_match_hangul_font() or _guess_hangul_font_name(font_dirs)

    subtitle_filter = f"subtitles={_escape_filter_value(str(subtitle_path))}"
    if subtitle_path.suffix.lower() != ".ass":
        if _subtitles_filter_supports_option(ffmpeg_bin, "wrap_unicode"):
            subtitle_filter += ":wrap_unicode=1"
        subtitle_filter += ":charenc=UTF-8"
    if fontsdir:
        subtitle_filter += f":fontsdir={_escape_filter_value(str(fontsdir))}"
    if selected_font_name and subtitle_path.suffix.lower() != ".ass":
        escaped_font_name = selected_font_name.replace("'", "\\'")
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

    _run(command)
