"use client";

import { useMemo, useRef, useState } from "react";

type JobCreateResponse = {
  job_id: string;
  status_url?: string;
  download_url?: string;
};

type JobStatus =
  | "queued"
  | "started"
  | "finished"
  | "failed"
  | "deferred"
  | "scheduled";

type JobStatusResponse = {
  job_id: string;
  status: JobStatus;
  error?: string;
  result?: any;
};

function sleep(ms: number) {
  return new Promise((r) => setTimeout(r, ms));
}

export default function Home() {
  const [file, setFile] = useState<File | null>(null);
  const [minutes, setMinutes] = useState<number>(2);
  const [seconds, setSeconds] = useState<number>(0);
  const [keepAudio, setKeepAudio] = useState<boolean>(true);

  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string>("");
  const [downloadUrl, setDownloadUrl] = useState<string>("");
  const [downloadName, setDownloadName] = useState<string>("edited.mp4");

  const [jobId, setJobId] = useState<string>("");
  const [jobStatus, setJobStatus] = useState<JobStatus | "">("");
  const [statusText, setStatusText] = useState<string>("");

  const MAX_UPLOAD_MB = 200; // must match backend
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

  // Used to avoid setting state after a new request starts
  const requestTokenRef = useRef(0);

  const targetSeconds = useMemo(() => {
    const m = Number.isFinite(minutes) ? minutes : 0;
    const s = Number.isFinite(seconds) ? seconds : 0;
    return Math.max(1, m * 60 + s);
  }, [minutes, seconds]);

  function friendlyStatus(s: JobStatus | "") {
    switch (s) {
      case "queued":
        return "Queued…";
      case "started":
        return "Processing…";
      case "finished":
        return "Finished";
      case "failed":
        return "Failed";
      case "deferred":
        return "Deferred…";
      case "scheduled":
        return "Scheduled…";
      default:
        return "";
    }
  }

  async function parseError(res: Response): Promise<string> {
    let detail = `Request failed (${res.status})`;
    try {
      const data = await res.json();
      if (data?.detail) detail = data.detail;
    } catch {
      // ignore
    }

    if (res.status === 413) return "File too large. Please upload a smaller video.";
    if (res.status === 429) return "Too many requests. Please wait and try again.";
    if (res.status === 0) return "Network error. Please check the API server.";
    return detail;
  }

  async function pollJob(job_id: string, token: number) {
    setJobStatus("queued");
    setStatusText("Queued…");

    // Up to ~10 minutes (600 * 1s)
    for (let i = 0; i < 600; i++) {
      // If a new request started, stop polling
      if (requestTokenRef.current !== token) return;

      const res = await fetch(`${apiBase}/jobs/${job_id}`, { cache: "no-store" });
      if (!res.ok) {
        throw new Error(await parseError(res));
      }

      const data = (await res.json()) as JobStatusResponse;

      setJobStatus(data.status);
      setStatusText(friendlyStatus(data.status));

      if (data.status === "failed") {
        throw new Error(data.error || "Job failed.");
      }

      if (data.status === "finished") {
        return;
      }

      await sleep(1000);
    }

    throw new Error("Timed out waiting for processing. Please try again with a smaller video.");
  }

  async function onSubmit() {
    setError("");
    setDownloadUrl("");
    setDownloadName("edited.mp4");
    setJobId("");
    setJobStatus("");
    setStatusText("");

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

    // New request token cancels any previous polling
    const token = ++requestTokenRef.current;

    setIsSubmitting(true);
    setStatusText("Uploading…");

    try {
      const form = new FormData();
      form.append("target_seconds", String(targetSeconds));
      form.append("keep_audio", keepAudio ? "true" : "false");
      form.append("video", file);

      const res = await fetch(`${apiBase}/jobs`, {
        method: "POST",
        body: form,
      });

      if (!res.ok) {
        throw new Error(await parseError(res));
      }

      const created = (await res.json()) as JobCreateResponse;
      const id = created.job_id;

      if (!id) {
        throw new Error("API did not return a job_id.");
      }

      setJobId(id);

      // Poll until finished
      await pollJob(id, token);

      // If a new request started while polling, do nothing
      if (requestTokenRef.current !== token) return;

      setStatusText("Finished");
      setDownloadName(`edited_${targetSeconds}s.mp4`);
      setDownloadUrl(`${apiBase}/jobs/${id}/download`);
    } catch (e: any) {
      if (requestTokenRef.current !== token) return;
      setError(e?.message || "Something went wrong.");
      setJobStatus("failed");
      setStatusText("Failed");
    } finally {
      if (requestTokenRef.current === token) {
        setIsSubmitting(false);
      }
    }
  }

  return (
    <main
      style={{
        maxWidth: 760,
        margin: "40px auto",
        padding: 16,
        fontFamily: "system-ui, sans-serif",
      }}
    >
      <h1 style={{ fontSize: 28, marginBottom: 6 }}>Video Editor (FFmpeg)</h1>
      <p style={{ marginTop: 0, color: "#555" }}>
        Upload a video, choose a target duration (shorter or longer), choose whether to keep audio, then download the result.
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
            {isSubmitting ? "Working…" : "Edit video"}
          </button>

          {statusText && (
            <span style={{ color: jobStatus === "failed" ? "crimson" : "#555" }}>
              {statusText}
              {jobId ? (
                <span style={{ color: "#777" }}>
                  {" "}
                  (Job: <code>{jobId.slice(0, 8)}…</code>)
                </span>
              ) : null}
            </span>
          )}

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

        {error && <p style={{ marginTop: 12, color: "crimson" }}>{error}</p>}

        <p style={{ marginTop: 16, color: "#777", fontSize: 13 }}>
          Note: making a video longer slows it down; making it shorter speeds it up. Large videos may take time.
          Max upload: {MAX_UPLOAD_MB} MB.
        </p>
      </div>
    </main>
  );
}
