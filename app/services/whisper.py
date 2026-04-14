from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings


class WhisperPayloadTooLargeError(RuntimeError):
    pass


logger = logging.getLogger("video_caption.whisper")
RETRYABLE_HTTP_STATUS_CODES = {502, 503, 504}


def _retry_delay(base_seconds: float, attempt: int) -> float:
    return max(0.5, base_seconds) * max(1, attempt)


def _response_excerpt(response: httpx.Response, limit: int = 180) -> str:
    text = (response.text or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


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

        attempts = self.settings.whisper_retry_attempts

        for attempt in range(1, attempts + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    with audio_path.open("rb") as handle:
                        response = await client.post(
                            self.settings.whisper_api_url,
                            headers=self._headers(),
                            data=data,
                            files={"file": (audio_path.name, handle, "audio/mpeg")},
                        )
            except httpx.RequestError as exc:
                if attempt >= attempts:
                    raise RuntimeError(
                        "Whisper API request failed after "
                        f"{attempts} attempts. Last network error: {exc}"
                    ) from exc
                logger.warning(
                    "whisper_request_retry file=%s attempt=%s/%s reason=%s",
                    audio_path.name,
                    attempt,
                    attempts,
                    exc,
                )
                await asyncio.sleep(
                    _retry_delay(self.settings.whisper_retry_backoff_seconds, attempt)
                )
                continue

            if response.status_code == 413:
                raise WhisperPayloadTooLargeError(
                    f"Whisper upload rejected {audio_path.name} with HTTP 413."
                )

            if response.status_code in RETRYABLE_HTTP_STATUS_CODES:
                if attempt >= attempts:
                    excerpt = _response_excerpt(response)
                    suffix = f" Response: {excerpt}" if excerpt else ""
                    raise RuntimeError(
                        "Whisper API returned "
                        f"HTTP {response.status_code} after {attempts} attempts. "
                        "The upstream gateway appears unavailable; try again shortly."
                        f"{suffix}"
                    )
                logger.warning(
                    "whisper_gateway_retry file=%s attempt=%s/%s status=%s",
                    audio_path.name,
                    attempt,
                    attempts,
                    response.status_code,
                )
                await asyncio.sleep(
                    _retry_delay(self.settings.whisper_retry_backoff_seconds, attempt)
                )
                continue

            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("Whisper API returned an unexpected payload.")
            return payload

        raise RuntimeError("Whisper transcription failed without a recoverable response.")


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
