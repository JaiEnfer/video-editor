import os
import math
import shutil
import subprocess
import tempfile
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI(title="Video Editor API", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def health():
    return {"status": "ok"}


def probe_duration_seconds(path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    try:
        s = subprocess.check_output(cmd, text=True).strip()
        dur = float(s)
        return dur
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

    # If speeding up: factor may be > 2
    while remaining > 2.0:
        parts.append(2.0)
        remaining /= 2.0

    # If slowing down: factor may be < 0.5
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

        factor = original / float(target_seconds)
        v_filter = f"setpts=PTS/{factor}"

        cmd = ["ffmpeg", "-y", "-i", in_path]

        if keep_audio:
            a_filter = build_atempo_chain(factor)
            cmd += [
                "-filter_complex",
                f"[0:v]{v_filter}[v];[0:a]{a_filter}[a]",
                "-map", "[v]",
                "-map", "[a]",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-c:a", "aac",
                "-movflags", "+faststart",
                out_path,
            ]
        else:
            cmd += [
                "-filter:v", v_filter,
                "-an",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-movflags", "+faststart",
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
        except:
            pass

        err = (e.stderr or "")[-2000:]
        raise HTTPException(status_code=500, detail=f"FFmpeg failed: {err}")