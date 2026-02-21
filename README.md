![CI](https://github.com/YOUR_USERNAME/video-editor/actions/workflows/ci.yml/badge.svg)
![Dockerized](https://img.shields.io/badge/dockerized-yes-blue?logo=docker)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green?logo=fastapi)
![Redis](https://img.shields.io/badge/Redis-queue-red?logo=redis)
![FFmpeg](https://img.shields.io/badge/FFmpeg-video_processing-black?logo=ffmpeg)
![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python)
![Node](https://img.shields.io/badge/Node-20-green?logo=node.js)
![License](https://img.shields.io/badge/license-MIT-green)
 
# FFmpeg Web Video Editor (Async)

A containerized web video editor that lets users upload a video, set a target duration (shorter or longer), optionally remove audio, and download the result.

Built as a portfolio-grade project with a real async architecture:
**Next.js Web → FastAPI API → Redis queue → RQ Worker → FFmpeg processing → Download**.

---

## Features

- Upload video from browser
- Change duration to any target time (shorter or longer)
- Optional audio removal
- Async processing (job queue + status polling)
- Download result when ready
- Upload size limit and basic per-IP rate limiting (MVP safety)
- Fully Dockerized services
- GitHub Actions CI with end-to-end processing test

---

## Architecture

```text
flowchart LR
  U[User Browser] --> W[Next.js Web]
  W -->|POST /jobs| A[FastAPI API]
  A --> R[(Redis)]
  R -->|Queue: video| Q[RQ Worker]
  Q -->|FFmpeg| F[Video Processing]
  F --> V[(Shared Docker Volume)]
  A -->|GET /jobs/{id}| W
  W -->|GET /jobs/{id}/download| A
  A --> V
```

---

## Tech Stack

- Frontend: Next.js (App Router)
- Backend: FastAPI (Python)
- Queue: Redis + RQ worker
- Video processing: FFmpeg
- Containers: Docker + Docker Compose
- CI: GitHub Actions (build + async E2E test)

---

## Run locally (Windows/macOS/Linux)

Requirements
- Docker Desktop

Start

```sh
docker compose up --build
```

Open

- [Web UI](http://localhost:3000)
- [API docs](http://localhost:8000/docs)
- [API health](http://localhost:8000/health)
- [API version](http://localhost:8000/version)

---

## API Overview

Create a job

POST /jobs (multipart form)

- video: file upload
- target_seconds: number (e.g., 120)
- keep_audio: true/false

Returns:

- job_id
- status URL
- download URL

Poll job status

GET /jobs/{job_id}

- statuses: queued, started, finished, failed

Download output

GET /jobs/{job_id}/download

- returns MP4 when ready

---

## CI

GitHub Actions runs:

1. Docker build for all services
2. Starts stack (api + redis + worker)
3. Generates a tiny sample video
4. Creates a job via API
5. Polls until finished
6. Downloads output
7. Verifies output duration (ffprobe)

---

## Notes / Limitations (MVP)

1. Increasing duration slows the video; decreasing duration speeds it up.
2. Very large uploads will take longer.
3. Rate limiting and upload limit are in-memory/basic (intended for local/demo).
4. For a real public deployment, add:
    - Redis-backed rate limiting
    - persistent object storage (S3)
    - auth / quotas
    - automatic cleanup scheduler

---

__Thank You__
