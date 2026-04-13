from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_FFMPEG = PROJECT_ROOT / "tools" / "ffmpeg" / "ffmpeg"
LOCAL_FFPROBE = PROJECT_ROOT / "tools" / "ffmpeg" / "ffprobe"

load_dotenv(PROJECT_ROOT / ".env", override=False)


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


@dataclass(slots=True)
class Settings:
    app_name: str
    storage_root: Path
    database_path: Path
    ffmpeg_bin: str
    ffprobe_bin: str
    whisper_api_url: str
    whisper_model: str
    whisper_dep_ticket: str
    whisper_user_id: str
    whisper_timeout_seconds: float
    whisper_require_auth: bool
    worker_count: int

    @classmethod
    def from_env(cls) -> "Settings":
        storage_root = Path(
            os.getenv("APP_STORAGE_ROOT", Path.cwd() / "data")
        ).expanduser()
        database_path = Path(
            os.getenv("APP_DATABASE_PATH", storage_root / "app.db")
        ).expanduser()

        ffmpeg_default = str(LOCAL_FFMPEG) if LOCAL_FFMPEG.is_file() else "ffmpeg"
        ffprobe_default = str(LOCAL_FFPROBE) if LOCAL_FFPROBE.is_file() else "ffprobe"

        return cls(
            app_name=os.getenv("APP_NAME", "Video Caption Studio"),
            storage_root=storage_root,
            database_path=database_path,
            ffmpeg_bin=os.getenv("FFMPEG_BIN", ffmpeg_default),
            ffprobe_bin=os.getenv("FFPROBE_BIN", ffprobe_default),
            whisper_api_url=os.getenv(
                "WHISPER_API_URL",
                "http://api.net/whisper-large-v3/v1/audio/transcriptions",
            ),
            whisper_model=os.getenv("WHISPER_MODEL", "openai/whisper-large-v3"),
            whisper_dep_ticket=os.getenv("WHISPER_DEP_TICKET", ""),
            whisper_user_id=os.getenv("WHISPER_USER_ID", ""),
            whisper_timeout_seconds=float(os.getenv("WHISPER_TIMEOUT_SECONDS", "600")),
            whisper_require_auth=_as_bool(
                os.getenv("WHISPER_REQUIRE_AUTH"), default=True
            ),
            worker_count=max(1, int(os.getenv("APP_WORKER_COUNT", "2"))),
        )

    def ensure_directories(self) -> None:
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        (self.storage_root / "tasks").mkdir(parents=True, exist_ok=True)
