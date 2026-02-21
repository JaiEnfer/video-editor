import os
import subprocess

def build_atempo_chain(factor: float) -> str:
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

def probe_duration_seconds(path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    s = subprocess.check_output(cmd, text=True).strip()
    return float(s)

def process_video_job(input_path: str, output_path: str, target_seconds: float, keep_audio: bool) -> dict:
    original = probe_duration_seconds(input_path)
    if original <= 0:
        raise RuntimeError("Invalid video duration")

    factor = original / float(target_seconds)
    v_filter = f"setpts=PTS/{factor}"

    cmd = ["ffmpeg", "-y", "-i", input_path]

    if keep_audio:
        a_filter = build_atempo_chain(factor)
        cmd += [
            "-filter_complex", f"[0:v]{v_filter}[v];[0:a]{a_filter}[a]",
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac",
            "-movflags", "+faststart",
            output_path,
        ]
    else:
        cmd += [
            "-filter:v", v_filter,
            "-an",
            "-c:v", "libx264", "-preset", "veryfast",
            "-movflags", "+faststart",
            output_path,
        ]

    subprocess.run(cmd, check=True, capture_output=True, text=True)

    return {
        "original_seconds": original,
        "target_seconds": float(target_seconds),
        "keep_audio": bool(keep_audio),
        "output_path": output_path,
    }