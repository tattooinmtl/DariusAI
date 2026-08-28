#!/usr/bin/env bash
# TripoSR CPU-only install for a Debian/Ubuntu VPS.
# Run as root:  bash install_triposr.sh
set -euo pipefail

INSTALL_DIR=${INSTALL_DIR:-/opt/triposr}
PYVER=${PYVER:-3.11}
SERVICE_USER=${SERVICE_USER:-triposr}

echo "==> 1/8  System packages"
apt-get update
apt-get install -y --no-install-recommends \
  git curl ca-certificates build-essential cmake ninja-build \
  libgl1 libglib2.0-0 \
  software-properties-common

# Python 3.11 (TripoSR deps are unreliable on 3.12+)
if ! command -v python${PYVER} >/dev/null 2>&1; then
  add-apt-repository -y ppa:deadsnakes/ppa
  apt-get update
fi
apt-get install -y python${PYVER} python${PYVER}-venv python${PYVER}-dev

echo "==> 2/8  Swap check (CPU inference needs headroom)"
TOTAL_MB=$(free -m | awk '/^Mem:/{print $2}')
SWAP_MB=$(free -m | awk '/^Swap:/{print $2}')
echo "    RAM: ${TOTAL_MB} MB, swap: ${SWAP_MB} MB"
if [ "$SWAP_MB" -lt 4096 ] && [ ! -f /swapfile-triposr ]; then
  echo "    Creating 4G swapfile"
  fallocate -l 4G /swapfile-triposr
  chmod 600 /swapfile-triposr
  mkswap /swapfile-triposr
  swapon /swapfile-triposr
  grep -q swapfile-triposr /etc/fstab || echo '/swapfile-triposr none swap sw 0 0' >> /etc/fstab
fi

echo "==> 3/8  Service user + directories"
id -u "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/triposr "$SERVICE_USER"
mkdir -p "$INSTALL_DIR" /var/lib/triposr/outputs /var/lib/triposr/uploads /var/lib/triposr/hf
chown -R "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR" /var/lib/triposr

echo "==> 4/8  Clone TripoSR"
if [ ! -d "$INSTALL_DIR/repo/.git" ]; then
  sudo -u "$SERVICE_USER" git clone --depth 1 https://github.com/VAST-AI-Research/TripoSR.git "$INSTALL_DIR/repo"
fi

echo "==> 5/8  Virtualenv + CPU PyTorch"
sudo -u "$SERVICE_USER" python${PYVER} -m venv "$INSTALL_DIR/venv"
PIP="$INSTALL_DIR/venv/bin/pip"
sudo -u "$SERVICE_USER" "$PIP" install --upgrade pip "setuptools>=49.6.0" wheel
sudo -u "$SERVICE_USER" "$PIP" install torch torchvision \
  --index-url https://download.pytorch.org/whl/cpu

echo "==> 6/8  TripoSR dependencies"
# torchmcubes builds CPU-only automatically when no nvcc is present. That is what we want.
sudo -u "$SERVICE_USER" "$PIP" install \
  omegaconf einops transformers trimesh rembg onnxruntime pillow \
  huggingface-hub imageio "numpy<2" xatlas moderngl \
  fastapi "uvicorn[standard]" python-multipart httpx
sudo -u "$SERVICE_USER" "$PIP" install git+https://github.com/tatsy/torchmcubes.git

echo "==> 7/8  Config + API token"
if [ ! -f "$INSTALL_DIR/triposr.env" ]; then
  TOKEN=$(head -c 32 /dev/urandom | base64 | tr -d '=+/' | cut -c1-40)
  cat > "$INSTALL_DIR/triposr.env" <<EOF
TRIPOSR_TOKEN=${TOKEN}
TRIPOSR_REPO=${INSTALL_DIR}/repo
TRIPOSR_OUT=/var/lib/triposr/outputs
TRIPOSR_UPLOADS=/var/lib/triposr/uploads
TRIPOSR_HOST=127.0.0.1
TRIPOSR_PORT=8231
TRIPOSR_MC_RESOLUTION=192
TRIPOSR_CHUNK_SIZE=4096
TRIPOSR_THREADS=$(nproc)
HF_HOME=/var/lib/triposr/hf
OMP_NUM_THREADS=$(nproc)
MKL_NUM_THREADS=$(nproc)
EOF
  chown "$SERVICE_USER":"$SERVICE_USER" "$INSTALL_DIR/triposr.env"
  chmod 600 "$INSTALL_DIR/triposr.env"
fi

echo "==> 8/8  Warming model cache (downloads ~1.7GB from HuggingFace)"
sudo -u "$SERVICE_USER" env HF_HOME=/var/lib/triposr/hf \
  "$INSTALL_DIR/venv/bin/python" -c "
from huggingface_hub import hf_hub_download
for f in ['config.yaml','model.ckpt']:
    print(hf_hub_download('stabilityai/TripoSR', f))
"

echo
echo "Done. Next:"
echo "  cp server.py       ${INSTALL_DIR}/server.py"
echo "  cp triposr.service /etc/systemd/system/triposr.service"
echo "  chown ${SERVICE_USER}:${SERVICE_USER} ${INSTALL_DIR}/server.py"
echo "  systemctl daemon-reload && systemctl enable --now triposr"
echo
echo "API token:"
grep TRIPOSR_TOKEN "$INSTALL_DIR/triposr.env"
