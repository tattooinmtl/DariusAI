"""
TripoSR HTTP service — CPU-only, single serialized worker.

One job at a time by design: TripoSR on CPU saturates every core, and two
concurrent jobs are slower than two sequential ones plus a real OOM risk.

Endpoints
  GET  /health                  -> model state, queue depth
  POST /generate                -> multipart file OR json {image_url|image_b64}
  GET  /jobs/{job_id}           -> status/progress/error
  GET  /jobs/{job_id}/model     -> the .glb (or .obj)
  DELETE /jobs/{job_id}         -> drop result + files

Auth: Authorization: Bearer $TRIPOSR_TOKEN
"""
from __future__ import annotations

import base64
import io
import os
import queue
import sys
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Optional

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.requests import Request

REPO = os.environ.get("TRIPOSR_REPO", "/opt/triposr/repo")
OUT_DIR = Path(os.environ.get("TRIPOSR_OUT", "/var/lib/triposr/outputs"))
UPLOAD_DIR = Path(os.environ.get("TRIPOSR_UPLOADS", "/var/lib/triposr/uploads"))
TOKEN = os.environ.get("TRIPOSR_TOKEN", "")
MC_RESOLUTION = int(os.environ.get("TRIPOSR_MC_RESOLUTION", "192"))
CHUNK_SIZE = int(os.environ.get("TRIPOSR_CHUNK_SIZE", "4096"))
THREADS = int(os.environ.get("TRIPOSR_THREADS", "0")) or None
JOB_TTL = int(os.environ.get("TRIPOSR_JOB_TTL", "86400"))

sys.path.insert(0, REPO)
OUT_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

import numpy as np  # noqa: E402
import torch  # noqa: E402
from PIL import Image  # noqa: E402

if THREADS:
    torch.set_num_threads(THREADS)

from tsr.system import TSR  # noqa: E402
from tsr.utils import remove_background, resize_foreground  # noqa: E402

import rembg  # noqa: E402


# --------------------------------------------------------------------------- #
# model
# --------------------------------------------------------------------------- #

_model: Optional[TSR] = None
_rembg_session = None
_model_lock = threading.Lock()


def load_model() -> TSR:
    global _model, _rembg_session
    with _model_lock:
        if _model is None:
            m = TSR.from_pretrained(
                "stabilityai/TripoSR",
                config_name="config.yaml",
                weight_name="model.ckpt",
            )
            m.renderer.set_chunk_size(CHUNK_SIZE)
            m.to("cpu")
            _model = m
        if _rembg_session is None:
            _rembg_session = rembg.new_session()
    return _model


def preprocess(img: Image.Image, do_remove_bg: bool, foreground_ratio: float) -> Image.Image:
    if do_remove_bg:
        img = remove_background(img, _rembg_session)
        img = resize_foreground(img, foreground_ratio)
        arr = np.array(img).astype(np.float32) / 255.0
        arr = arr[:, :, :3] * arr[:, :, 3:4] + (1 - arr[:, :, 3:4]) * 0.5
        img = Image.fromarray((arr * 255.0).astype(np.uint8))
    else:
        img = img.convert("RGB")
    return img


def extract_mesh_compat(model: TSR, scene_codes: Any, resolution: int):
    """extract_mesh() gained has_vertex_color between repo revisions."""
    try:
        return model.extract_mesh(scene_codes, True, resolution=resolution)
    except TypeError:
        return model.extract_mesh(scene_codes, resolution=resolution)


# --------------------------------------------------------------------------- #
# job store
# --------------------------------------------------------------------------- #

Status = Literal["queued", "running", "done", "error"]


@dataclass
class Job:
    id: str
    status: Status = "queued"
    stage: str = "queued"
    error: Optional[str] = None
    created: float = field(default_factory=time.time)
    started: Optional[float] = None
    finished: Optional[float] = None
    input_path: Optional[str] = None
    output_path: Optional[str] = None
    params: dict = field(default_factory=dict)

    def public(self) -> dict:
        return {
            "job_id": self.id,
            "status": self.status,
            "stage": self.stage,
            "error": self.error,
            "created": self.created,
            "elapsed": round((self.finished or time.time()) - (self.started or self.created), 1),
            "output_format": self.params.get("output_format"),
            "download_url": f"/jobs/{self.id}/model" if self.status == "done" else None,
        }


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()
WORK: "queue.Queue[str]" = queue.Queue()


def worker_loop() -> None:
    while True:
        job_id = WORK.get()
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if job is None:
            WORK.task_done()
            continue
        try:
            job.status, job.stage, job.started = "running", "loading_model", time.time()
            model = load_model()

            job.stage = "preprocess"
            img = Image.open(job.input_path)
            img = preprocess(img, job.params["remove_background"], job.params["foreground_ratio"])

            job.stage = "inference"
            with torch.no_grad():
                scene_codes = model([img], device="cpu")

            job.stage = "marching_cubes"
            meshes = extract_mesh_compat(model, scene_codes, job.params["mc_resolution"])

            job.stage = "export"
            ext = job.params["output_format"]
            out = OUT_DIR / f"{job.id}.{ext}"
            meshes[0].export(str(out))
            job.output_path = str(out)

            job.status, job.stage = "done", "done"
        except Exception:
            job.status = "error"
            job.stage = "failed"
            job.error = traceback.format_exc(limit=6)
        finally:
            job.finished = time.time()
            WORK.task_done()


def reaper_loop() -> None:
    while True:
        time.sleep(600)
        cutoff = time.time() - JOB_TTL
        with JOBS_LOCK:
            stale = [j for j in JOBS.values() if j.created < cutoff]
            for j in stale:
                JOBS.pop(j.id, None)
        for j in stale:
            for p in (j.input_path, j.output_path):
                if p:
                    Path(p).unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# api
# --------------------------------------------------------------------------- #

app = FastAPI(title="TripoSR", version="1.0")


def auth(request: Request) -> None:
    if not TOKEN:
        return
    header = request.headers.get("authorization", "")
    if header != f"Bearer {TOKEN}":
        raise HTTPException(status_code=401, detail="bad or missing bearer token")


@app.on_event("startup")
def _startup() -> None:
    threading.Thread(target=worker_loop, daemon=True, name="triposr-worker").start()
    threading.Thread(target=reaper_loop, daemon=True, name="triposr-reaper").start()
    if os.environ.get("TRIPOSR_PRELOAD", "1") == "1":
        threading.Thread(target=load_model, daemon=True, name="triposr-preload").start()


@app.get("/health")
def health() -> dict:
    with JOBS_LOCK:
        running = sum(1 for j in JOBS.values() if j.status == "running")
        queued = sum(1 for j in JOBS.values() if j.status == "queued")
    return {
        "ok": True,
        "model_loaded": _model is not None,
        "queued": queued,
        "running": running,
        "threads": torch.get_num_threads(),
        "mc_resolution_default": MC_RESOLUTION,
    }


class GenerateJson(BaseModel):
    image_url: Optional[str] = None
    image_b64: Optional[str] = None
    remove_background: bool = True
    foreground_ratio: float = 0.85
    mc_resolution: Optional[int] = None
    output_format: Literal["glb", "obj"] = "glb"


def _enqueue(raw: bytes, params: dict) -> dict:
    job_id = uuid.uuid4().hex
    src = UPLOAD_DIR / f"{job_id}.png"
    try:
        Image.open(io.BytesIO(raw)).convert("RGBA").save(src)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"unreadable image: {exc}") from exc

    job = Job(id=job_id, input_path=str(src), params=params)
    with JOBS_LOCK:
        JOBS[job_id] = job
    WORK.put(job_id)
    return job.public()


@app.post("/generate", dependencies=[Depends(auth)])
async def generate(
    request: Request,
    file: Optional[UploadFile] = File(default=None),
    remove_background: bool = Form(default=True),
    foreground_ratio: float = Form(default=0.85),
    mc_resolution: Optional[int] = Form(default=None),
    output_format: str = Form(default="glb"),
) -> dict:
    if file is not None:
        raw = await file.read()
        params = {
            "remove_background": remove_background,
            "foreground_ratio": foreground_ratio,
            "mc_resolution": mc_resolution or MC_RESOLUTION,
            "output_format": output_format if output_format in ("glb", "obj") else "glb",
        }
        return _enqueue(raw, params)

    body = GenerateJson(**(await request.json()))
    if body.image_b64:
        raw = base64.b64decode(body.image_b64)
    elif body.image_url:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(body.image_url)
            resp.raise_for_status()
            raw = resp.content
    else:
        raise HTTPException(status_code=400, detail="provide file, image_url or image_b64")

    params = {
        "remove_background": body.remove_background,
        "foreground_ratio": body.foreground_ratio,
        "mc_resolution": body.mc_resolution or MC_RESOLUTION,
        "output_format": body.output_format,
    }
    return _enqueue(raw, params)


@app.get("/jobs/{job_id}", dependencies=[Depends(auth)])
def job_status(job_id: str) -> dict:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return job.public()


@app.get("/jobs/{job_id}/model", dependencies=[Depends(auth)])
def job_model(job_id: str) -> FileResponse:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    if job.status != "done" or not job.output_path:
        raise HTTPException(status_code=409, detail=f"job is {job.status}")
    return FileResponse(
        job.output_path,
        filename=Path(job.output_path).name,
        media_type="model/gltf-binary" if job.output_path.endswith(".glb") else "text/plain",
    )


@app.delete("/jobs/{job_id}", dependencies=[Depends(auth)])
def job_delete(job_id: str) -> dict:
    with JOBS_LOCK:
        job = JOBS.pop(job_id, None)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    for p in (job.input_path, job.output_path):
        if p:
            Path(p).unlink(missing_ok=True)
    return {"deleted": job_id}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("TRIPOSR_HOST", "127.0.0.1"),
        port=int(os.environ.get("TRIPOSR_PORT", "8231")),
        workers=1,
    )
