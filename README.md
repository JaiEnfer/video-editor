# FFmpeg Web Video Editor

A production-ready, containerized web video editor built with:

- FastAPI
- Next.js
- FFmpeg
- Redis
- RQ (background job queue)
- Docker
- GitHub Actions CI

---

## Features

- Upload video
- Change duration (shorter or longer)
- Optional audio removal
- Background job processing
- Status polling
- Rate limiting
- Upload size limits
- Dockerized microservice architecture
- CI pipeline

---

## Architecture

Web (Next.js) → API (FastAPI) → Redis → Worker (RQ) → FFmpeg → Shared Volume

---

## Run locally

```sh
docker compose up --build
```

---
