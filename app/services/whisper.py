from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from app.config import Settings


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

        # httpx.AsyncClient requires form fields here to be a mapping.
        # A list of tuples produces a sync multipart stream and fails at send time.
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
                    files={"file": (audio_path.name, handle, "audio/wav")},
                )

        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Whisper API returned an unexpected payload.")
        return payload
