"""
Darius-side client for the TripoSR service.

Drop into your tools package. Configure with:
    TRIPOSR_URL=https://triposr.yourdomain.tld     (or http://127.0.0.1:8231)
    TRIPOSR_TOKEN=<token from /opt/triposr/triposr.env>
    TRIPOSR_ASSET_DIR=./assets/3d

Two surfaces:
  - TripoSRClient  : plain sync/async client, use anywhere
  - triposr_tool   : LangChain StructuredTool for the LangGraph agent (optional import)
"""
from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Literal, Optional

import httpx

TRIPOSR_URL = os.environ.get("TRIPOSR_URL", "http://127.0.0.1:8231").rstrip("/")
TRIPOSR_TOKEN = os.environ.get("TRIPOSR_TOKEN", "")
ASSET_DIR = Path(os.environ.get("TRIPOSR_ASSET_DIR", "./assets/3d"))

# CPU inference is minutes, not seconds. Do not lower these blindly.
POLL_INTERVAL = float(os.environ.get("TRIPOSR_POLL_INTERVAL", "5"))
DEFAULT_TIMEOUT = float(os.environ.get("TRIPOSR_TIMEOUT", "1800"))


class TripoSRError(RuntimeError):
    pass


class TripoSRClient:
    def __init__(
        self,
        base_url: str = TRIPOSR_URL,
        token: str = TRIPOSR_TOKEN,
        asset_dir: Path | str = ASSET_DIR,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.asset_dir = Path(asset_dir)
        self.asset_dir.mkdir(parents=True, exist_ok=True)

    # -- low level ---------------------------------------------------------- #

    def health(self) -> dict:
        with httpx.Client(timeout=15) as c:
            r = c.get(f"{self.base_url}/health", headers=self.headers)
            r.raise_for_status()
            return r.json()

    def submit(
        self,
        image_path: Optional[str | Path] = None,
        image_url: Optional[str] = None,
        image_bytes: Optional[bytes] = None,
        *,
        remove_background: bool = True,
        foreground_ratio: float = 0.85,
        mc_resolution: Optional[int] = None,
        output_format: Literal["glb", "obj"] = "glb",
    ) -> str:
        payload = {
            "remove_background": remove_background,
            "foreground_ratio": foreground_ratio,
            "output_format": output_format,
        }
        if mc_resolution:
            payload["mc_resolution"] = mc_resolution

        with httpx.Client(timeout=60) as c:
            if image_path is not None:
                image_bytes = Path(image_path).read_bytes()
            if image_bytes is not None:
                payload["image_b64"] = base64.b64encode(image_bytes).decode()
            elif image_url is not None:
                payload["image_url"] = image_url
            else:
                raise TripoSRError("need image_path, image_bytes or image_url")

            r = c.post(f"{self.base_url}/generate", json=payload, headers=self.headers)
            if r.status_code >= 400:
                raise TripoSRError(f"{r.status_code}: {r.text[:400]}")
            return r.json()["job_id"]

    def status(self, job_id: str) -> dict:
        with httpx.Client(timeout=15) as c:
            r = c.get(f"{self.base_url}/jobs/{job_id}", headers=self.headers)
            r.raise_for_status()
            return r.json()

    def download(self, job_id: str, dest: Optional[Path] = None) -> Path:
        info = self.status(job_id)
        ext = info.get("output_format") or "glb"
        dest = dest or self.asset_dir / f"{job_id}.{ext}"
        with httpx.Client(timeout=300) as c:
            with c.stream("GET", f"{self.base_url}/jobs/{job_id}/model", headers=self.headers) as r:
                r.raise_for_status()
                with open(dest, "wb") as fh:
                    for chunk in r.iter_bytes():
                        fh.write(chunk)
        return dest

    # -- blocking convenience ---------------------------------------------- #

    def generate(
        self,
        image_path: Optional[str | Path] = None,
        image_url: Optional[str] = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        on_progress=None,
        **kwargs,
    ) -> Path:
        job_id = self.submit(image_path=image_path, image_url=image_url, **kwargs)
        deadline = time.time() + timeout
        last_stage = None

        while time.time() < deadline:
            info = self.status(job_id)
            if on_progress and info["stage"] != last_stage:
                last_stage = info["stage"]
                on_progress(info)
            if info["status"] == "done":
                return self.download(job_id)
            if info["status"] == "error":
                raise TripoSRError(info.get("error") or "job failed")
            time.sleep(POLL_INTERVAL)

        raise TripoSRError(f"timed out after {timeout}s (job {job_id} still running)")


# --------------------------------------------------------------------------- #
# LangGraph / LangChain tool
# --------------------------------------------------------------------------- #

def image_to_3d(
    image_path: str,
    output_format: str = "glb",
    mc_resolution: int = 192,
    remove_background: bool = True,
) -> str:
    """Convert a single product/object image into a 3D mesh.

    Runs on a self-hosted CPU TripoSR instance: expect 2-8 minutes per model and
    untextured, low-to-medium detail geometry. Best with a single centered object
    on a plain background. Returns the local path of the written mesh file.
    """
    client = TripoSRClient()
    path = client.generate(
        image_path=image_path,
        output_format=output_format,
        mc_resolution=mc_resolution,
        remove_background=remove_background,
    )
    return str(path)


try:  # optional — only if langchain_core is present in the Darius env
    from langchain_core.tools import StructuredTool

    triposr_tool = StructuredTool.from_function(
        func=image_to_3d,
        name="image_to_3d",
        description=(
            "Generate a 3D mesh (.glb or .obj) from a single image of one object. "
            "Slow: 2-8 minutes on CPU. Untextured vertex-colour geometry only. "
            "Use for draft/preview assets, not final production models."
        ),
    )
except ImportError:  # pragma: no cover
    triposr_tool = None


if __name__ == "__main__":
    import sys

    c = TripoSRClient()
    print("health:", c.health())
    if len(sys.argv) > 1:
        out = c.generate(sys.argv[1], on_progress=lambda i: print(" ", i["stage"], i["elapsed"], "s"))
        print("wrote", out)
