# Video Caption Studio

FastAPI based web service for:

- uploading a video
- extracting audio with FFmpeg
- sending audio to a `whisper-large-v3` compatible transcription API
- queueing large volumes of jobs with background workers
- optionally splitting very large uploads into ordered batch tasks
- tracking each uploaded task
- deleting uploaded tasks and generated artifacts
- editing captions in the browser
- editing global and per-caption subtitle styles in the browser
- re-rendering a subtitle-burned video after edits

## Features

- FastAPI backend with async upload and API endpoints
- in-process worker queue with configurable concurrency
- SQLite task registry with task workspace snapshots for persistence across restarts
- optional large-upload splitting into sequential 10-minute parts
- FFmpeg audio extraction and burned subtitle rendering
- diarized transcription support using the response shape in `request_option.md`
- browser UI for upload, preview, task tracking, caption editing, rerender, and delete
- ASS-based subtitle rendering so NanumGothic-backed size, color, and position can be controlled globally or per cue

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
If `APP_STORAGE_ROOT` is omitted, the default storage location is the project-local `data/` directory.

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
UPLOAD_SPLIT_THRESHOLD_BYTES=524288000
UPLOAD_SPLIT_PROMPT_SECONDS=1200
UPLOAD_SPLIT_CHUNK_SECONDS=600
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
`UPLOAD_SPLIT_THRESHOLD_BYTES` is the browser prompt threshold for suggesting split registration.
`UPLOAD_SPLIT_PROMPT_SECONDS` is the duration threshold for suggesting split registration.
`UPLOAD_SPLIT_CHUNK_SECONDS` controls how long each split video part should be.
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

On Ubuntu, the app now asks FFmpeg to load fonts directly from `SUBTITLE_FONT_DIRS` and tries to auto-detect a Korean subtitle font with `fc-match`. If auto-detection still picks the wrong family, check the actual installed family name and pin it in `.env`:

```bash
fc-match -f '%{family[0]}\n' 'sans-serif:lang=ko'
```

Then set:

```bash
SUBTITLE_FONT_NAME=the family name returned above
```

## Run

```bash
uvicorn app.main:app --reload
```

Open `http://127.0.0.1:8000`.

## API Summary

- `POST /api/tasks` uploads a video and enqueues processing. It also accepts `split_mode=chunked` to register large uploads as ordered split tasks.
- `GET /api/tasks` lists all tasks
- `GET /api/tasks/{task_id}` returns task detail and caption data
- `PUT /api/tasks/{task_id}/captions` saves edited captions and subtitle styles, then queues rerender
- `POST /api/tasks/{task_id}/retry` requeues a failed task
- `DELETE /api/tasks/{task_id}` deletes a task and generated files
- `GET /api/tasks/{task_id}/artifacts/{artifact_name}` serves task artifacts

## Notes

- The queue is in-process. For very large deployments, move the worker layer to Redis or a dedicated job system.
- Browser-based subtitle preview depends on the rendered output being generated successfully by FFmpeg.
- Task metadata is mirrored into each task workspace, so completed tasks can be recovered into the UI after a restart even if the SQLite file was recreated.
- If Chromium-style preview tooling is needed later, install the missing system libraries in the runtime image separately.
