"""Lightweight job queue worker for KiranaLens."""
import json
import os
import queue
import threading
import time
import uuid
from typing import Any

from vision import analyze_images
from geo import get_geo_signals
from fraud import run_fraud_checks
from scoring import compute_kcs_score

JOB_QUEUE: queue.Queue = queue.Queue()
JOBS: dict[str, dict[str, Any]] = {}
LOCK = threading.Lock()


def is_model_unavailable(err: Exception) -> bool:
    msg = str(err).lower()
    return "11434" in msg or "connection" in msg or "refused" in msg or "timeout" in msg


def enqueue_job(payload: dict) -> str:
    job_id = str(uuid.uuid4())
    with LOCK:
        JOBS[job_id] = {"status": "queued", "created_at": time.time(), "result": None, "error": None}
    JOB_QUEUE.put((job_id, payload))
    return job_id


def get_job(job_id: str) -> dict:
    with LOCK:
        return JOBS.get(job_id, {"status": "not_found"})


def _process(payload: dict) -> dict:
    saved_paths = payload["image_paths"]
    lat = float(payload.get("lat", 0) or 0)
    lng = float(payload.get("lng", 0) or 0)
    optional = payload.get("optional", {})
    use_mock = bool(payload.get("mock", False))

    vision = analyze_images(saved_paths, mock=use_mock)
    geo = get_geo_signals(lat, lng, mock=use_mock)
    fraud = run_fraud_checks(saved_paths, vision, geo, lat, lng, mock=use_mock)
    result = compute_kcs_score(vision, geo, fraud, optional)
    result["images_used"] = len(saved_paths)
    return result


def worker_loop():
    while True:
        job_id, payload = JOB_QUEUE.get()
        try:
            with LOCK:
                JOBS[job_id]["status"] = "processing"
            result = _process(payload)

            session_dir = payload.get("session_dir")
            if session_dir:
                os.makedirs(session_dir, exist_ok=True)
                with open(os.path.join(session_dir, "result.json"), "w") as fh:
                    json.dump(result, fh, indent=2)

            with LOCK:
                JOBS[job_id]["status"] = "completed"
                JOBS[job_id]["result"] = result
        except Exception as e:
            with LOCK:
                JOBS[job_id]["status"] = "failed"
                JOBS[job_id]["error"] = str(e)
        finally:
            JOB_QUEUE.task_done()


def start_worker_once():
    if getattr(start_worker_once, "_started", False):
        return
    t = threading.Thread(target=worker_loop, daemon=True)
    t.start()
    start_worker_once._started = True
