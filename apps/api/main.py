import os
import math
import shutil
import subprocess
import tempfile
import time
from collections import deque
from typing import Deque, Dict

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

app = FastAPI(title="Video Editor API", version="0.3.1")

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