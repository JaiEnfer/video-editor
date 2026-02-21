import os
import math
import shutil
import subprocess
import tempfile
import time
from collections import deque
from typing import Deque, Dict
import uuid
from redis import Redis
from rq import Queue
from rq.job import Job
from datetime import datetime, timezone

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from fastapi.responses import JSONResponse
from jobs import process_video_job

app = FastAPI(title="Video Editor API", version="0.3.1")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DATA_DIR = os.getenv("DATA_DIR", "/data")

def redis_conn() -> Redis:
    return Redis.from_url(REDIS_URL)

def q() -> Queue:
    return Queue("video", connection=redis_conn())

# ---- CORS (dev-friendly). For production, replace "*" with your real domain(s).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Limits (env-configurable)
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "500"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))
WINDOW_SECONDS = 60

JOB_TTL_SECONDS = int(os.getenv("JOB_TTL_SECONDS", "3600"))

def _uploads_dir() -> str:
    return os.path.join(DATA_DIR, "uploads")

def _outputs_dir() -> str:
    return os.path.join(DATA_DIR, "outputs")

def _job_output_path(job_id: str) -> str:
    return os.path.join(_outputs_dir(), f"{job_id}.mp4")

# In-memory per-IP request timestamps (MVP: good for single instance)
_ip_hits: Dict[str, Deque[float]] = {}


def _client_ip(request: Request) -> str:
    # If later you put a reverse proxy (NGINX/Cloudflare), you may want X-Forwarded-For handling.
    return request.client.host if request.client else "unknown"


class UploadSizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Never block CORS preflight
        if request.method == "OPTIONS":
            return await call_next(request)

        # Only enforce on edit endpoints
        if request.method in ("POST", "PUT", "PATCH") and request.url.path.startswith("/edit"):
            cl = request.headers.get("content-length")
            if cl:
                try:
                    length = int(cl)
                    if length > MAX_UPLOAD_BYTES:
                        return JSONResponse(
                            {"detail": f"File too large. Max upload is {MAX_UPLOAD_MB} MB."},
                            status_code=413,
                        )
                except ValueError:
                    # Ignore malformed content-length; let request proceed.
                    pass

        return await call_next(request)


class SimpleRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Never rate-limit CORS preflight
        if request.method == "OPTIONS":
            return await call_next(request)

        # Only rate-limit the heavy endpoint(s)
        if request.method == "POST" and request.url.path.startswith("/edit"):
            ip = _client_ip(request)
            now = time.time()

            q = _ip_hits.get(ip)
            if q is None:
                q = deque()
                _ip_hits[ip] = q

            cutoff = now - WINDOW_SECONDS
            while q and q[0] < cutoff:
                q.popleft()

            if len(q) >= RATE_LIMIT_PER_MINUTE:
                return JSONResponse(
                    {"detail": f"Rate limit exceeded. Try again later (max {RATE_LIMIT_PER_MINUTE}/min)."},
                    status_code=429,
                )

            q.append(now)

        return await call_next(request)


app.add_middleware(UploadSizeLimitMiddleware)
app.add_middleware(SimpleRateLimitMiddleware)


@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/version")
def version():
    return {"service": "video-editor-api", "version": app.version}

def probe_duration_seconds(path: str) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    try:
        s = subprocess.check_output(cmd, text=True).strip()
        return float(s)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read video duration: {e}")


def build_atempo_chain(factor: float) -> str:
    """
    atempo supports 0.5..2.0. For factors outside, chain multiple.
    factor > 1.0 => faster; factor < 1.0 => slower.
    """
    if factor <= 0:
        raise ValueError("factor must be > 0")

    parts = []
    remaining = factor

    while remaining > 2.0:
        parts.append(2.0)
        remaining /= 2.0

    while remaining < 0.5:
        parts.append(0.5)
        remaining /= 0.5

    parts.append(remaining)

    return ",".join([f"atempo={p:.8f}".rstrip("0").rstrip(".") for p in parts])


@app.post("/edit")
async def edit_video(
    background_tasks: BackgroundTasks,
    target_seconds: float = Form(..., gt=0),
    keep_audio: bool = Form(True),
    video: UploadFile = File(...),
):
    """
    User-controlled edit:
    - target_seconds: desired final duration (can be shorter or longer)
    - keep_audio: include audio or remove it
    """
    if not video.filename:
        raise HTTPException(status_code=400, detail="Missing filename")

    # Keep input in a temp dir, but output as a persistent temp file
    tmpdir = tempfile.mkdtemp(prefix="video-edit-")
    in_path = os.path.join(tmpdir, "input")

    # Output file must survive until response is sent
    out_fd, out_path = tempfile.mkstemp(prefix="edited_", suffix=".mp4")
    os.close(out_fd)  # ffmpeg will write to this path

    try:
        # Save upload
        with open(in_path, "wb") as f:
            shutil.copyfileobj(video.file, f)

        original = probe_duration_seconds(in_path)
        if original <= 0:
            raise HTTPException(status_code=400, detail="Invalid video duration")

        # factor = original / target
        # original 278 -> target 120 => factor 2.3167 (speed up)
        # original 120 -> target 240 => factor 0.5 (slow down)
        factor = original / float(target_seconds)

        # Video timing: setpts=PTS/factor changes speed accordingly
        v_filter = f"setpts=PTS/{factor}"

        cmd = ["ffmpeg", "-y", "-i", in_path]

        if keep_audio:
            a_filter = build_atempo_chain(factor)
            cmd += [
                "-filter_complex",
                f"[0:v]{v_filter}[v];[0:a]{a_filter}[a]",
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                out_path,
            ]
        else:
            cmd += [
                "-filter:v",
                v_filter,
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-movflags",
                "+faststart",
                out_path,
            ]

        subprocess.run(cmd, check=True, capture_output=True, text=True)

        # Cleanup AFTER response is sent
        background_tasks.add_task(lambda p: os.path.exists(p) and os.remove(p), out_path)
        background_tasks.add_task(lambda d: os.path.isdir(d) and shutil.rmtree(d, ignore_errors=True), tmpdir)

        return FileResponse(
            out_path,
            media_type="video/mp4",
            filename=f"edited_{math.floor(target_seconds)}s.mp4",
            background=background_tasks,
        )

    except subprocess.CalledProcessError as e:
        # Cleanup on failure too
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass

        err = (e.stderr or "")[-2000:]
        raise HTTPException(status_code=500, detail=f"FFmpeg failed: {err}")

@app.post("/jobs")
async def create_job(
    target_seconds: float = Form(..., gt=0),
    keep_audio: bool = Form(True),
    video: UploadFile = File(...),
):
    job_id = str(uuid.uuid4())

    uploads_dir = os.path.join(DATA_DIR, "uploads")
    outputs_dir = os.path.join(DATA_DIR, "outputs")
    os.makedirs(uploads_dir, exist_ok=True)
    os.makedirs(outputs_dir, exist_ok=True)

    input_path = os.path.join(uploads_dir, f"{job_id}_{video.filename}")
    output_path = os.path.join(outputs_dir, f"{job_id}.mp4")

    # Save upload to shared volume
    with open(input_path, "wb") as f:
        shutil.copyfileobj(video.file, f)

    # Enqueue job
    job = q().enqueue(
        process_video_job,
        input_path,
        output_path,
        float(target_seconds),
        bool(keep_audio),
        job_id=job_id,
        result_ttl=3600,   # keep result metadata for 1 hour
        failure_ttl=3600,
    )

    return {
        "job_id": job.id,
        "status_url": f"/jobs/{job.id}",
        "download_url": f"/jobs/{job.id}/download",
    }

@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    try:
        job = Job.fetch(job_id, connection=redis_conn())
    except Exception:
        raise HTTPException(status_code=404, detail="Job not found")

    status = job.get_status()  # queued/started/finished/failed
    response = {"job_id": job_id, "status": status}

    if status == "failed":
        response["error"] = str(job.exc_info)[-2000:] if job.exc_info else "Job failed"
    if status == "finished":
        # job.result contains dict returned by process_video_job
        response["result"] = job.result

    return response

@app.get("/jobs/{job_id}/download")
def download(job_id: str):
    # Output file name is deterministic
    output_path = os.path.join(DATA_DIR, "outputs", f"{job_id}.mp4")
    if not os.path.exists(output_path):
        raise HTTPException(status_code=404, detail="Output not ready")

    return FileResponse(output_path, media_type="video/mp4", filename=f"edited_{job_id}.mp4")

@app.post("/admin/cleanup")
def cleanup_old_files():
    """
    Deletes uploads/outputs older than JOB_TTL_SECONDS.
    Local-only admin endpoint (no auth) — do NOT expose publicly as-is.
    """
    now = time.time()
    deleted = {"uploads": 0, "outputs": 0}

    for folder_key, folder in [("uploads", _uploads_dir()), ("outputs", _outputs_dir())]:
        if not os.path.isdir(folder):
            continue
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            try:
                if not os.path.isfile(path):
                    continue
                age = now - os.path.getmtime(path)
                if age > JOB_TTL_SECONDS:
                    os.remove(path)
                    deleted[folder_key] += 1
            except Exception:
                # best-effort cleanup
                pass

    return {"deleted": deleted, "ttl_seconds": JOB_TTL_SECONDS}