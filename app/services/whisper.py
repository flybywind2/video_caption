from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from app.config import Settings


class WhisperPayloadTooLargeError(RuntimeError):
    pass


class WhisperClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def is_configured(self) -> bool:
        if not self.settings.whisper_api_url.strip():
            return False
        if not self.settings.whisper_require_auth:
            return True
        return bool(
            self.settings.whisper_dep_ticket.strip()
            and self.settings.whisper_user_id.strip()
        )

    def _headers(self) -> dict[str, str]:
        headers = {"content_type": "application/json"}
        if self.settings.whisper_dep_ticket:
            headers["x-dep-ticket"] = self.settings.whisper_dep_ticket
        if self.settings.whisper_user_id:
            headers["user-id"] = self.settings.whisper_user_id
        return headers

    async def transcribe(self, audio_path: Path, language: str) -> dict[str, Any]:
        if not self.is_configured():
            raise RuntimeError(
                "Whisper API credentials are not configured. "
                "Set WHISPER_DEP_TICKET and WHISPER_USER_ID."
            )

        data = {
            "model": self.settings.whisper_model,
            "language": language,
            "timestamp_granularities": "word",
            "response_format": "diarized_json",
        }

        timeout = httpx.Timeout(
            self.settings.whisper_timeout_seconds,
            connect=30.0,
        )

        async with httpx.AsyncClient(timeout=timeout) as client:
            with audio_path.open("rb") as handle:
                response = await client.post(
                    self.settings.whisper_api_url,
                    headers=self._headers(),
                    data=data,
                    files={"file": (audio_path.name, handle, "audio/mpeg")},
                )

        if response.status_code == 413:
            raise WhisperPayloadTooLargeError(
                f"Whisper upload rejected {audio_path.name} with HTTP 413."
            )

        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Whisper API returned an unexpected payload.")
        return payload


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _offset_words(words: Any, offset: float) -> list[dict[str, Any]]:
    adjusted: list[dict[str, Any]] = []
    for word in words or []:
        item = dict(word)
        if "start" in item:
            item["start"] = round(_to_float(item.get("start")) + offset, 3)
        if "end" in item:
            item["end"] = round(_to_float(item.get("end")) + offset, 3)
        adjusted.append(item)
    return adjusted


def merge_transcripts(chunk_transcripts: list[tuple[float, dict[str, Any]]]) -> dict[str, Any]:
    if not chunk_transcripts:
        raise RuntimeError("No chunk transcripts were available to merge.")

    first = chunk_transcripts[0][1]
    merged_segments: list[dict[str, Any]] = []
    merged_words: list[dict[str, Any]] = []
    merged_speakers: list[dict[str, Any]] = []
    text_parts: list[str] = []
    segment_id = 0
    total_duration = 0.0

    for offset, transcript in chunk_transcripts:
        text_value = str(transcript.get("text") or "").strip()
        if text_value:
            text_parts.append(text_value)

        segments = transcript.get("segments") or []
        if segments:
            for raw_segment in segments:
                segment_id += 1
                segment = dict(raw_segment)
                segment["id"] = segment_id
                segment["start"] = round(_to_float(segment.get("start")) + offset, 3)
                segment["end"] = round(_to_float(segment.get("end")) + offset, 3)
                if "words" in segment:
                    segment["words"] = _offset_words(segment.get("words"), offset)
                merged_segments.append(segment)
        elif text_value:
            duration = _to_float(transcript.get("duration"), 0.0)
            segment_id += 1
            merged_segments.append(
                {
                    "id": segment_id,
                    "start": round(offset, 3),
                    "end": round(offset + max(duration, 0.1), 3),
                    "text": text_value,
                    "words": [],
                }
            )

        merged_words.extend(_offset_words(transcript.get("words"), offset))

        for raw_speaker in transcript.get("speakers") or []:
            speaker = dict(raw_speaker)
            speaker["start"] = round(_to_float(speaker.get("start")) + offset, 3)
            speaker["end"] = round(_to_float(speaker.get("end")) + offset, 3)
            merged_speakers.append(speaker)

        total_duration = max(
            total_duration,
            offset + _to_float(transcript.get("duration"), 0.0),
        )

    return {
        "text": " ".join(part for part in text_parts if part).strip(),
        "task": first.get("task", "transcribe"),
        "language": first.get("language"),
        "duration": round(total_duration, 3),
        "segments": merged_segments,
        "words": merged_words or None,
        "speakers": merged_speakers,
    }
