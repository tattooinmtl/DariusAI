# specs_triposr-vps.md

TripoSR image-to-3D on a CPU-only Contabo VPS, consumed by the Darius AI harness.

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | TripoSR, not TRELLIS/Hunyuan3D | Only model in the family that finishes on CPU in minutes rather than tens of minutes |
| 2 | Python 3.11 in a dedicated venv | TripoSR deps (rembg, onnxruntime, numpy<2) are unreliable on 3.12+ |
| 3 | CPU PyTorch wheel from `download.pytorch.org/whl/cpu` | Default pip torch pulls ~2.5GB of CUDA libs that will never be used |
| 4 | `torchmcubes` built from source, CPU-only | No nvcc on the box, so CMake builds the CPU module. The "not compiled with CUDA" warning is expected and correct here |
| 5 | Single serialized worker (`queue.Queue` + one thread) | Inference saturates all cores. Two concurrent jobs are slower than two sequential ones and risk OOM |
| 6 | Async job API, not a blocking POST | A 2-8 min request dies to every reverse proxy default timeout |
| 7 | `mc_resolution` default 192, not 256 | Marching cubes at 256³ is ~16M voxels; on 8GB RAM that is the OOM point |
| 8 | `chunk_size` 4096 | Lower peak memory during density query, at some speed cost |
| 9 | Bind 127.0.0.1, bearer token auth | Service is only reached by Darius or an nginx vhost, never directly public |
| 10 | systemd `MemoryMax=8G`, `Nice=10` | Prevents a runaway job taking the rest of the VPS with it |
| 11 | 4GB swapfile if absent | Peak RSS spikes during mesh extraction; swap is the difference between slow and killed |
| 12 | 24h job TTL reaper | Meshes are ~2-20MB each; unbounded output dir fills the disk |

## Deployment

```bash
# on the VPS, as root
bash install_triposr.sh
cp server.py /opt/triposr/server.py
chown triposr:triposr /opt/triposr/server.py
cp triposr.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now triposr
journalctl -u triposr -f
```

Smoke test:

```bash
TOKEN=$(grep TRIPOSR_TOKEN /opt/triposr/triposr.env | cut -d= -f2)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8231/health

JOB=$(curl -s -H "Authorization: Bearer $TOKEN" \
  -F file=@/opt/triposr/repo/examples/chair.png \
  http://127.0.0.1:8231/generate | python3 -c 'import sys,json;print(json.load(sys.stdin)["job_id"])')

watch -n5 "curl -s -H 'Authorization: Bearer $TOKEN' http://127.0.0.1:8231/jobs/$JOB"
curl -s -H "Authorization: Bearer $TOKEN" -o chair.glb http://127.0.0.1:8231/jobs/$JOB/model
```

## Darius integration

```bash
pip install httpx
export TRIPOSR_URL=http://127.0.0.1:8231     # or the nginx vhost
export TRIPOSR_TOKEN=...                     # from triposr.env
```

```python
from darius_triposr_tool import TripoSRClient, triposr_tool

# direct
path = TripoSRClient().generate("ref.png", on_progress=print)

# as a LangGraph tool
tools = [triposr_tool, ...]
```

If Darius runs on the Windows box and TripoSR on the VPS, expose it through
nginx with `proxy_read_timeout 300s;` and keep the bearer token.

## Known limits

- No textures. Vertex colours only; `--bake-texture` needs a GL context the VPS lacks.
- Single object, centered, plain background. Scenes and multiple objects fail badly.
- 4GB RAM VPS will OOM even at resolution 192. 8GB is the floor.
- First request after boot is slower: model load is ~30-60s on CPU.

## Open

- [ ] nginx vhost + TLS if Darius is off-box
- [ ] Decide whether draft meshes get promoted to a rented-GPU TRELLIS pass
