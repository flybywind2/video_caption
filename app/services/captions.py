from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any


DEFAULT_SPEAKER_RE = re.compile(r"^SPEAKER[_ -]?\d+$", re.IGNORECASE)
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
FONT_SUFFIXES = (".ttf", ".otf", ".ttc")
ASS_PLAY_RES_X = 1920
ASS_PLAY_RES_Y = 1080
GENERIC_FONT_FAMILY_KEYS = {
    "",
    "auto",
    "default",
    "sans",
    "sansserif",
    "serif",
    "monospace",
    "systemui",
}

DEFAULT_CAPTION_STYLE = {
    "font_family": "auto",
    "font_size": 48,
    "text_color": "#ffffff",
    "outline_color": "#101010",
    "alignment": "bottom-center",
    "offset_x": 0,
    "offset_y": 0,
}

ALIGNMENT_TO_ASS = {
    "bottom-left": 1,
    "bottom-center": 2,
    "bottom-right": 3,
    "middle-left": 4,
    "middle-center": 5,
    "middle-right": 6,
    "top-left": 7,
    "top-center": 8,
    "top-right": 9,
}

ALIGNMENT_TO_PERCENT = {
    "top-left": (10, 12),
    "top-center": (50, 12),
    "top-right": (90, 12),
    "middle-left": (10, 50),
    "middle-center": (50, 50),
    "middle-right": (90, 50),
    "bottom-left": (10, 90),
    "bottom-center": (50, 90),
    "bottom-right": (90, 90),
}

PREFERRED_HANGUL_FONTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Noto Sans CJK KR", ("notosanscjk", "notoserifcjk", "sourcehansans", "sourcehanserif")),
    ("Noto Sans KR", ("notosanskr",)),
    ("NanumGothic", ("nanumgothic", "nanummyeongjo", "nanumbarungothic")),
    ("Malgun Gothic", ("malgun",)),
    ("Droid Sans Fallback", ("droidsansfallback",)),
    ("Baekmuk Gulim", ("baekmuk",)),
    ("UnDotum", ("undotum",)),
)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def _font_key(value: str) -> str:
    return "".join(char for char in str(value or "").lower() if char.isalnum())


def _contains_hangul(value: str) -> bool:
    return any(
        ("\u1100" <= char <= "\u11ff")
        or ("\u3130" <= char <= "\u318f")
        or ("\uac00" <= char <= "\ud7a3")
        for char in str(value or "")
    )


def _alignment_anchor_percent(alignment: str) -> tuple[int, int]:
    return ALIGNMENT_TO_PERCENT.get(alignment, ALIGNMENT_TO_PERCENT[DEFAULT_CAPTION_STYLE["alignment"]])


def _legacy_offsets_from_position(style: dict[str, Any], alignment: str) -> tuple[int | None, int | None]:
    anchor_x, anchor_y = _alignment_anchor_percent(alignment)
    offset_x: int | None = None
    offset_y: int | None = None

    if style.get("position_x") not in (None, ""):
        position_x = _clamp(_int(style.get("position_x"), anchor_x), 0, 100)
        offset_x = round(ASS_PLAY_RES_X * (position_x - anchor_x) / 100)
    if style.get("position_y") not in (None, ""):
        position_y = _clamp(_int(style.get("position_y"), anchor_y), 0, 100)
        offset_y = round(ASS_PLAY_RES_Y * (position_y - anchor_y) / 100)

    return offset_x, offset_y


def default_caption_style() -> dict[str, Any]:
    return dict(DEFAULT_CAPTION_STYLE)


def normalize_caption_style(
    raw_style: dict[str, Any] | None,
    *,
    partial: bool = False,
) -> dict[str, Any]:
    style = raw_style if isinstance(raw_style, dict) else {}
    normalized = {} if partial else default_caption_style()

    def set_if_present(key: str, value: Any) -> None:
        normalized[key] = value

    if not partial or "font_family" in style:
        font_family = str(style.get("font_family") or "").strip()
        if _font_key(font_family) in {"", "auto", "default"}:
            font_family = "auto"
        if partial:
            if "font_family" in style:
                set_if_present("font_family", font_family or "auto")
        else:
            set_if_present("font_family", font_family or DEFAULT_CAPTION_STYLE["font_family"])

    if not partial or style.get("font_size") not in (None, ""):
        font_size = _clamp(
            _int(style.get("font_size"), DEFAULT_CAPTION_STYLE["font_size"]),
            18,
            120,
        )
        set_if_present("font_size", font_size)

    if not partial or style.get("text_color") not in (None, ""):
        text_color = _normalize_color(
            style.get("text_color"),
            DEFAULT_CAPTION_STYLE["text_color"],
        )
        set_if_present("text_color", text_color)

    if not partial or style.get("outline_color") not in (None, ""):
        outline_color = _normalize_color(
            style.get("outline_color"),
            DEFAULT_CAPTION_STYLE["outline_color"],
        )
        set_if_present("outline_color", outline_color)

    alignment = DEFAULT_CAPTION_STYLE["alignment"]
    if not partial or style.get("alignment") not in (None, ""):
        alignment = str(
            style.get("alignment") or DEFAULT_CAPTION_STYLE["alignment"]
        ).strip()
        if alignment not in ALIGNMENT_TO_ASS:
            alignment = DEFAULT_CAPTION_STYLE["alignment"]
        set_if_present("alignment", alignment)
    elif partial:
        alignment = str(style.get("alignment") or DEFAULT_CAPTION_STYLE["alignment"]).strip()
        if alignment not in ALIGNMENT_TO_ASS:
            alignment = DEFAULT_CAPTION_STYLE["alignment"]

    legacy_offset_x, legacy_offset_y = _legacy_offsets_from_position(style, alignment)

    if not partial or style.get("offset_x") not in (None, "") or legacy_offset_x is not None:
        if style.get("offset_x") not in (None, ""):
            offset_x = _clamp(_int(style.get("offset_x"), 0), -ASS_PLAY_RES_X, ASS_PLAY_RES_X)
        else:
            offset_x = _clamp(legacy_offset_x or DEFAULT_CAPTION_STYLE["offset_x"], -ASS_PLAY_RES_X, ASS_PLAY_RES_X)
        set_if_present("offset_x", offset_x)

    if not partial or style.get("offset_y") not in (None, "") or legacy_offset_y is not None:
        if style.get("offset_y") not in (None, ""):
            offset_y = _clamp(_int(style.get("offset_y"), 0), -ASS_PLAY_RES_Y, ASS_PLAY_RES_Y)
        else:
            offset_y = _clamp(legacy_offset_y or DEFAULT_CAPTION_STYLE["offset_y"], -ASS_PLAY_RES_Y, ASS_PLAY_RES_Y)
        set_if_present("offset_y", offset_y)

    return normalized


def merge_caption_style(
    global_style: dict[str, Any] | None,
    override_style: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = normalize_caption_style(global_style)
    merged.update(normalize_caption_style(override_style, partial=True))
    return merged


def normalize_caption_document(
    raw_payload: Any,
    *,
    fallback_style: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if isinstance(raw_payload, dict):
        global_style = normalize_caption_style(
            raw_payload.get("global_style") or fallback_style
        )
        cues = normalize_cues(raw_payload.get("cues") or [], global_style=global_style)
        return {
            "global_style": global_style,
            "cues": cues,
        }

    global_style = normalize_caption_style(fallback_style)
    cues = normalize_cues(raw_payload or [], global_style=global_style)
    return {
        "global_style": global_style,
        "cues": cues,
    }


def normalize_cues(
    raw_cues: list[dict[str, Any]],
    *,
    global_style: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    base_style = normalize_caption_style(global_style)

    for index, cue in enumerate(raw_cues, start=1):
        text = str(cue.get("text", "")).strip()
        if not text:
            continue

        start = max(0.0, _float(cue.get("start"), 0.0))
        end = max(start + 0.05, _float(cue.get("end"), start + 2.0))
        speaker = _normalize_speaker(cue.get("speaker"))
        style = _normalize_style_override(cue.get("style"), base_style)

        normalized.append(
            {
                "id": str(cue.get("id") or f"cue-{index:04d}"),
                "start": round(start, 3),
                "end": round(end, 3),
                "text": text,
                "speaker": speaker,
                "style": style,
            }
        )

    normalized.sort(key=lambda item: (item["start"], item["end"]))
    return normalized


def _normalize_style_override(
    raw_style: Any,
    global_style: dict[str, Any],
) -> dict[str, Any]:
    override = normalize_caption_style(raw_style, partial=True)
    return {
        key: value
        for key, value in override.items()
        if global_style.get(key) != value
    }


def _normalize_color(value: Any, default: str) -> str:
    color = str(value or "").strip()
    if HEX_COLOR_RE.match(color):
        return color.lower()
    return default


def _normalize_speaker(value: Any) -> str | None:
    speaker = str(value or "").strip()
    if not speaker:
        return None
    if DEFAULT_SPEAKER_RE.match(speaker):
        return None
    return speaker


def cues_from_transcript(transcript: dict[str, Any]) -> list[dict[str, Any]]:
    speakers = transcript.get("speakers") or []
    if speakers:
        return normalize_cues(
            [
                {
                    "id": speaker.get("speaker") or f"speaker-{index:04d}",
                    "speaker": speaker.get("speaker"),
                    "start": speaker.get("start"),
                    "end": speaker.get("end"),
                    "text": speaker.get("text"),
                }
                for index, speaker in enumerate(speakers, start=1)
            ]
        )

    segments = transcript.get("segments") or []
    if segments:
        return normalize_cues(
            [
                {
                    "id": segment.get("id") or f"segment-{index:04d}",
                    "start": segment.get("start"),
                    "end": segment.get("end"),
                    "text": segment.get("text"),
                }
                for index, segment in enumerate(segments, start=1)
            ]
        )

    transcript_text = str(transcript.get("text", "")).strip()
    duration = _float(transcript.get("duration"), 5.0)
    if transcript_text:
        return normalize_cues(
            [
                {
                    "id": "cue-0001",
                    "start": 0.0,
                    "end": max(duration, 2.0),
                    "text": transcript_text,
                }
            ]
        )
    return []


def srt_timestamp(seconds: float) -> str:
    total_milliseconds = int(round(max(seconds, 0) * 1000))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"


def ass_timestamp(seconds: float) -> str:
    total_centiseconds = int(round(max(seconds, 0) * 100))
    hours, remainder = divmod(total_centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, centiseconds = divmod(remainder, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def cue_to_text(cue: dict[str, Any]) -> str:
    speaker = str(cue.get("speaker") or "").strip()
    text = str(cue.get("text") or "").strip()
    if speaker:
        return f"{speaker}: {text}"
    return text


def build_srt(cues: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, cue in enumerate(normalize_cues(cues), start=1):
        lines.extend(
            [
                str(index),
                f"{srt_timestamp(cue['start'])} --> {srt_timestamp(cue['end'])}",
                cue_to_text(cue),
                "",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _font_dirs_signature(font_dirs: tuple[Path, ...] | list[Path] | None) -> tuple[str, ...]:
    return tuple(str(Path(entry).expanduser()) for entry in font_dirs or [])


@lru_cache(maxsize=8)
def _iter_font_files_cached(font_dirs_signature: tuple[str, ...]) -> tuple[Path, ...]:
    paths: list[Path] = []
    for raw_path in font_dirs_signature:
        path = Path(raw_path)
        if not path.is_dir():
            continue
        try:
            for candidate in path.rglob("*"):
                if candidate.is_file() and candidate.suffix.lower() in FONT_SUFFIXES:
                    paths.append(candidate)
        except OSError:
            continue
    return tuple(paths)


def _iter_font_files(font_dirs: tuple[Path, ...] | list[Path] | None) -> tuple[Path, ...]:
    return _iter_font_files_cached(_font_dirs_signature(font_dirs))


def _guess_hangul_font_name(font_dirs: tuple[Path, ...] | list[Path] | None) -> str:
    for path in _iter_font_files(font_dirs):
        key = _font_key(path.stem)
        for family, patterns in PREFERRED_HANGUL_FONTS:
            if any(pattern in key for pattern in patterns):
                return family
    return ""


def _font_family_patterns(font_family: str) -> tuple[str, ...]:
    key = _font_key(font_family)
    patterns = [key]
    for family, aliases in PREFERRED_HANGUL_FONTS:
        family_key = _font_key(family)
        if key == family_key or any(alias in key or key in alias for alias in aliases):
            patterns.extend([family_key, *aliases])
            break
    return tuple(pattern for pattern in patterns if pattern)


def _font_family_available(
    font_family: str,
    font_dirs: tuple[Path, ...] | list[Path] | None,
) -> bool:
    patterns = _font_family_patterns(font_family)
    if not patterns:
        return False
    for path in _iter_font_files(font_dirs):
        key = _font_key(path.stem)
        if any(pattern in key or key in pattern for pattern in patterns):
            return True
    return False


def _resolve_ass_font_family(
    requested_font_family: str,
    *,
    text: str,
    default_font_family: str,
    font_dirs: tuple[Path, ...] | list[Path] | None,
) -> str:
    requested = str(requested_font_family or "").strip()
    default = str(default_font_family or "").strip()
    requested_key = _font_key(requested)
    default_key = _font_key(default)
    fallback = _guess_hangul_font_name(font_dirs)

    if not _contains_hangul(text):
        if requested and requested_key not in GENERIC_FONT_FAMILY_KEYS:
            return requested
        if default and default_key not in GENERIC_FONT_FAMILY_KEYS:
            return default
        return fallback or "Sans"

    for candidate in (requested, default):
        candidate_key = _font_key(candidate)
        if not candidate or candidate_key in GENERIC_FONT_FAMILY_KEYS:
            continue
        if not font_dirs or _font_family_available(candidate, font_dirs):
            return candidate

    if fallback:
        return fallback
    if requested and requested_key not in GENERIC_FONT_FAMILY_KEYS:
        return requested
    if default and default_key not in GENERIC_FONT_FAMILY_KEYS:
        return default
    return "Sans"


def build_ass(
    cues: list[dict[str, Any]],
    global_style: dict[str, Any] | None,
    *,
    default_font_family: str = "auto",
    play_res_x: int = ASS_PLAY_RES_X,
    play_res_y: int = ASS_PLAY_RES_Y,
    font_dirs: tuple[Path, ...] | list[Path] | None = None,
) -> str:
    base_style = normalize_caption_style(global_style)
    normalized_cues = normalize_cues(cues, global_style=base_style)
    script_text = "\n".join(cue_to_text(cue) for cue in normalized_cues)
    effective_default_font = _resolve_ass_font_family(
        str(base_style.get("font_family") or default_font_family or "auto"),
        text=script_text,
        default_font_family=default_font_family,
        font_dirs=font_dirs,
    )

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {play_res_x}",
        f"PlayResY: {play_res_y}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,"
        "Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
        "Alignment,MarginL,MarginR,MarginV,Encoding",
        "Style: Default,"
        f"{_clean_ass_value(effective_default_font)},"
        f"{base_style['font_size']},"
        f"{_ass_color(base_style['text_color'])},"
        f"{_ass_color(base_style['text_color'])},"
        f"{_ass_color(base_style['outline_color'])},"
        f"{_ass_color('#000000')},"
        "0,0,0,0,100,100,0,0,1,2.2,0.8,"
        f"{ALIGNMENT_TO_ASS[base_style['alignment']]},24,24,24,1",
        "",
        "[Events]",
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
    ]

    for cue in normalized_cues:
        effective_style = merge_caption_style(base_style, cue.get("style"))
        anchor_x, anchor_y = _alignment_anchor_percent(effective_style["alignment"])
        position_x = _clamp(
            round(play_res_x * anchor_x / 100) + int(effective_style.get("offset_x", 0)),
            0,
            play_res_x,
        )
        position_y = _clamp(
            round(play_res_y * anchor_y / 100) + int(effective_style.get("offset_y", 0)),
            0,
            play_res_y,
        )
        font_family = _resolve_ass_font_family(
            str(effective_style.get("font_family") or effective_default_font),
            text=cue_to_text(cue),
            default_font_family=effective_default_font,
            font_dirs=font_dirs,
        )
        tags = [
            f"\\an{ALIGNMENT_TO_ASS[effective_style['alignment']]}",
            f"\\pos({position_x},{position_y})",
            f"\\fn{_clean_ass_value(font_family)}",
            f"\\fs{effective_style['font_size']}",
            f"\\c{_ass_color(effective_style['text_color'])}",
            f"\\3c{_ass_color(effective_style['outline_color'])}",
            "\\bord2.2",
            "\\shad0.8",
        ]
        lines.append(
            "Dialogue: 0,"
            f"{ass_timestamp(cue['start'])},"
            f"{ass_timestamp(cue['end'])},"
            "Default,,0,0,0,,"
            "{"
            f"{''.join(tags)}"
            "}"
            f"{_escape_ass_text(cue_to_text(cue))}"
        )

    return "\n".join(lines).strip() + "\n"


def _clean_ass_value(value: str) -> str:
    return str(value or "Sans").replace(",", " ").replace("{", "(").replace("}", ")").strip()


def _ass_color(value: str) -> str:
    color = _normalize_color(value, "#ffffff").lstrip("#")
    return f"&H00{color[4:6]}{color[2:4]}{color[0:2]}&"


def _escape_ass_text(value: str) -> str:
    return (
        str(value or "")
        .replace("\\", r"\\")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", r"\N")
    )
