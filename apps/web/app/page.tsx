"use client";

import { useMemo, useState } from "react";

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [minutes, setMinutes] = useState<number>(2);
  const [seconds, setSeconds] = useState<number>(0);
  const [keepAudio, setKeepAudio] = useState<boolean>(true);

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string>("");
  const [downloadUrl, setDownloadUrl] = useState<string>("");
  const [downloadName, setDownloadName] = useState<string>("edited.mp4");

  const MAX_UPLOAD_MB = 500; 
  
  const targetSeconds = useMemo(() => {
    const m = Number.isFinite(minutes) ? minutes : 0;
    const s = Number.isFinite(seconds) ? seconds : 0;
    return Math.max(1, m * 60 + s);
  }, [minutes, seconds]);

  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

  async function onSubmit() {
    setError("");
    setDownloadUrl("");

    if (!file) {
      setError("Please select a video file.");
      return;
    }

    const fileSizeMB = file.size / 1024 / 1024;
    if (fileSizeMB > MAX_UPLOAD_MB) {
      setError(`File too large. Max allowed is ${MAX_UPLOAD_MB} MB.`);
      return;
    }

    if (targetSeconds <= 0) {
      setError("Target duration must be at least 1 second.");
      return;
    }

    setIsSubmitting(true);
    try {
      const form = new FormData();
      form.append("target_seconds", String(targetSeconds));
      form.append("keep_audio", keepAudio ? "true" : "false");
      form.append("video", file);

      const res = await fetch(`${apiBase}/edit`, {
        method: "POST",
        body: form,
      });

      if (!res.ok) {
        let detail = `Request failed (${res.status})`;

        try {
          const data = await res.json();
          if (data?.detail) detail = data.detail;
        } catch {}

        if (res.status === 413) {
          detail = "File too large. Please upload a smaller video.";
        }

        if (res.status === 429) {
          detail = "Too many requests. Please wait and try again.";
        }

        throw new Error(detail);
      }

      const blob = await res.blob();
      const url = URL.createObjectURL(blob);

      // try to use server filename if present
      const disposition = res.headers.get("content-disposition") || "";
      const match = disposition.match(/filename="?([^"]+)"?/i);
      const nameFromHeader = match?.[1];

      setDownloadName(nameFromHeader || `edited_${targetSeconds}s.mp4`);
      setDownloadUrl(url);
    } catch (e: any) {
      setError(e?.message || "Something went wrong.");
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main
      style={{
        maxWidth: 720,
        margin: "40px auto",
        padding: 16,
        fontFamily: "system-ui, sans-serif",
      }}
    >
      <h1 style={{ fontSize: 28, marginBottom: 6 }}>Video Editor (FFmpeg)</h1>
      <p style={{ marginTop: 0, color: "#555" }}>
        Upload a video, set a target duration (shorter or longer), choose whether to keep audio, and download the result.
      </p>

      <div
        style={{
          border: "1px solid #ddd",
          borderRadius: 12,
          padding: 16,
          marginTop: 16,
        }}
      >
        <label style={{ display: "block", fontWeight: 600, marginBottom: 8 }}>Video file</label>
        <input
          type="file"
          accept="video/*"
          onChange={(e) => setFile(e.target.files?.[0] || null)}
          disabled={isSubmitting}
        />

        {file && (
          <div style={{ marginTop: 8, color: "#555" }}>
            Selected: <b>{file.name}</b> ({Math.round(file.size / 1024 / 1024)} MB)
          </div>
        )}

        <hr style={{ margin: "16px 0" }} />

        <label style={{ display: "block", fontWeight: 600, marginBottom: 8 }}>Target duration</label>
        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <div>
            <div style={{ fontSize: 12, color: "#666" }}>Minutes</div>
            <input
              type="number"
              min={0}
              value={minutes}
              onChange={(e) => setMinutes(parseInt(e.target.value || "0", 10))}
              style={{ width: 120, padding: 8 }}
              disabled={isSubmitting}
            />
          </div>

          <div>
            <div style={{ fontSize: 12, color: "#666" }}>Seconds</div>
            <input
              type="number"
              min={0}
              max={59}
              value={seconds}
              onChange={(e) => setSeconds(parseInt(e.target.value || "0", 10))}
              style={{ width: 120, padding: 8 }}
              disabled={isSubmitting}
            />
          </div>

          <div style={{ color: "#555" }}>
            Total: <b>{targetSeconds}</b> seconds
          </div>
        </div>

        <div style={{ marginTop: 16 }}>
          <label style={{ display: "flex", gap: 10, alignItems: "center" }}>
            <input
              type="checkbox"
              checked={keepAudio}
              onChange={(e) => setKeepAudio(e.target.checked)}
              disabled={isSubmitting}
            />
            Keep audio
          </label>
        </div>

        <div style={{ marginTop: 16, display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <button
            onClick={onSubmit}
            disabled={isSubmitting}
            style={{
              padding: "10px 14px",
              borderRadius: 10,
              border: "1px solid #111",
              background: isSubmitting ? "#ccc" : "#111",
              color: "#fff",
              cursor: isSubmitting ? "not-allowed" : "pointer",
            }}
          >
            {isSubmitting ? "Processing video... Please wait" : "Edit video"}
          </button>

          {downloadUrl && (
            <a
              href={downloadUrl}
              download={downloadName}
              style={{ color: "#0b57d0", textDecoration: "underline" }}
            >
              Download {downloadName}
            </a>
          )}
        </div>

        {error && (
          <p style={{ marginTop: 12, color: "crimson" }}>
            {error}
          </p>
        )}

        <p style={{ marginTop: 16, color: "#777", fontSize: 13 }}>
          Note: making a video longer slows it down; making it shorter speeds it up. Large videos may take time.
        </p>
      </div>
    </main>
  );
}
