from __future__ import annotations

import re
from typing import Any


DEFAULT_SPEAKER_RE = re.compile(r"^SPEAKER[_ -]?\d+$", re.IGNORECASE)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_cues(raw_cues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []

    for index, cue in enumerate(raw_cues, start=1):
        text = str(cue.get("text", "")).strip()
        if not text:
            continue

        start = max(0.0, _float(cue.get("start"), 0.0))
        end = max(start + 0.05, _float(cue.get("end"), start + 2.0))
        speaker = _normalize_speaker(cue.get("speaker"))

        normalized.append(
            {
                "id": str(cue.get("id") or f"cue-{index:04d}"),
                "start": round(start, 3),
                "end": round(end, 3),
                "text": text,
                "speaker": speaker,
            }
        )

    normalized.sort(key=lambda item: (item["start"], item["end"]))
    return normalized


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
