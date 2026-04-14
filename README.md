# Video Caption Studio

FastAPI based web service for:

- uploading a video
- extracting audio with FFmpeg
- sending audio to a `whisper-large-v3` compatible transcription API
- queueing large volumes of jobs with background workers
- tracking each uploaded task
- deleting uploaded tasks and generated artifacts
- editing captions in the browser
- re-rendering a subtitle-burned video after edits

## Features

- FastAPI backend with async upload and API endpoints
- in-process worker queue with configurable concurrency
- SQLite task registry for persistence across restarts
- FFmpeg audio extraction and burned subtitle rendering
- diarized transcription support using the response shape in `request_option.md`
- browser UI for upload, preview, task tracking, caption editing, rerender, and delete

## Project Layout

```text
app/
  main.py
  config.py
  database.py
  artifacts.py
  queue.py
  schemas.py
  services/
    captions.py
    ffmpeg.py
    whisper.py
  static/
    index.html
    app.js
    styles.css
```

## Requirements

- Python 3.10+
- network access from the backend to your Whisper API
- `ffmpeg` and `ffprobe` available on the host, or set custom binary paths in `.env`
- large audio uploads are automatically chunked before Whisper retry if the gateway rejects a single request

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Configuration

The app loads environment variables from `.env` in the project root.

```bash
APP_NAME=Video Caption Studio
APP_STORAGE_ROOT=/home/jks/project/video_caption/data
APP_WORKER_COUNT=2

WHISPER_API_URL=http://api.net/whisper-large-v3/v1/audio/transcriptions
WHISPER_MODEL=openai/whisper-large-v3
WHISPER_DEP_TICKET=credential:...
WHISPER_USER_ID=YOUR_AD_ID
WHISPER_REQUIRE_AUTH=true
WHISPER_TIMEOUT_SECONDS=600
WHISPER_MAX_UPLOAD_BYTES=8388608
WHISPER_CHUNK_SECONDS=480
SUBTITLE_FONT_DIRS=/usr/share/fonts:/usr/local/share/fonts:~/.local/share/fonts
SUBTITLE_FONT_NAME=
```

Optional overrides:

```bash
FFMPEG_BIN=ffmpeg
FFPROBE_BIN=ffprobe
```

`WHISPER_MAX_UPLOAD_BYTES` is the pre-check threshold before the app switches to chunked transcription.
`WHISPER_CHUNK_SECONDS` controls per-chunk audio duration for fallback uploads.
`SUBTITLE_FONT_DIRS` is a `:` separated list of font directories used when FFmpeg burns subtitles.
`SUBTITLE_FONT_NAME` is optional. Set it only if you want to force a specific libass font family.
If you keep project-local binaries instead, point these values at those absolute paths.
Keep real credentials in `.env`. The repo includes `.env.example` as the template.

If Korean captions render as broken boxes or missing glyphs, install a Korean-capable font on Ubuntu and keep `SUBTITLE_FONT_DIRS` pointed at it. A common fix is:

```bash
sudo apt-get update
sudo apt-get install -y fontconfig fonts-noto-cjk
```

If you cannot install system packages, place a `.ttf`, `.ttc`, or `.otf` font inside the project `fonts/` directory. That directory is included in the default subtitle font search path.

## Run

```bash
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## API Summary

- `POST /api/tasks` uploads a video and enqueues processing
- `GET /api/tasks` lists all tasks
- `GET /api/tasks/{task_id}` returns task detail and caption data
- `PUT /api/tasks/{task_id}/captions` saves edited captions and queues rerender
- `POST /api/tasks/{task_id}/retry` requeues a failed task
- `DELETE /api/tasks/{task_id}` deletes a task and generated files
- `GET /api/tasks/{task_id}/artifacts/{artifact_name}` serves task artifacts

## Notes

- The queue is in-process. For very large deployments, move the worker layer to Redis or a dedicated job system.
- Browser-based subtitle preview depends on the rendered output being generated successfully by FFmpeg.
- If Chromium-style preview tooling is needed later, install the missing system libraries in the runtime image separately.
