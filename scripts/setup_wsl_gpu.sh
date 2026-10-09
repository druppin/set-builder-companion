#!/usr/bin/env bash
# One-time setup on a Windows PC with an NVIDIA GPU, inside WSL2 Ubuntu (22.04 or 24.04).
# Installs: the app (for the `hsb` command), allin1 with CUDA, and CUE-DETR with CUDA.
# Run from the repository folder:  bash scripts/setup_wsl_gpu.sh
# Takes 30-60 minutes, most of it compiling NATTEN for the GPU.
set -euo pipefail

if ! command -v nvidia-smi >/dev/null || ! nvidia-smi >/dev/null 2>&1; then
  echo "The GPU isn't visible in WSL. Install a current NVIDIA driver in Windows (not inside Ubuntu), then run 'wsl --shutdown' in PowerShell and reopen Ubuntu."
  exit 1
fi
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader

sudo apt-get update
sudo apt-get install -y software-properties-common git ffmpeg build-essential cmake ninja-build wget \
  libgl1 libegl1 libxkbcommon0 libdbus-1-3 libfontconfig1 libglib2.0-0
if ! command -v python3.11 >/dev/null; then
  sudo add-apt-repository -y ppa:deadsnakes/ppa
  sudo apt-get update
  sudo apt-get install -y python3.11 python3.11-venv python3.11-dev
fi

# CUDA compiler and headers only (to build NATTEN): the full cuda-toolkit package also pulls in
# Nsight, which needs libtinfo5 and can't install on Ubuntu 24.04. CUDA 12.4 accepts 24.04's
# GCC 13 (12.1 doesn't). The WSL repo has no driver package, by design: Windows provides it.
if [ ! -x /usr/local/cuda-12.4/bin/nvcc ]; then
  wget -q https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-keyring_1.1-1_all.deb
  sudo dpkg -i cuda-keyring_1.1-1_all.deb && rm cuda-keyring_1.1-1_all.deb
  sudo apt-get update
  sudo apt-get install -y cuda-nvcc-12-4 cuda-cudart-dev-12-4 cuda-libraries-dev-12-4 cuda-cccl-12-4
fi
export CUDA_HOME=/usr/local/cuda-12.4
export PATH="$CUDA_HOME/bin:$PATH"

DATA="$HOME/.local/share/HarmonicSetBuilder/Harmonic Set Builder"
TORCH_CUDA="--index-url https://download.pytorch.org/whl/cu124"  # matches the CUDA 12.4 compiler

echo "== app"
python3.11 -m venv .venv
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q -e .

echo "== allin1 (GPU)"
A="$DATA/allin1-venv"
python3.11 -m venv "$A"
"$A/bin/pip" install -q -U pip wheel setuptools cython numpy==1.26.4
"$A/bin/pip" install -q torch==2.4.1 torchaudio==2.4.1 $TORCH_CUDA
"$A/bin/pip" install -q --no-build-isolation git+https://github.com/CPJKU/madmom
# RTX 20xx = compute capability 7.5; building only that keeps the compile short.
# Few workers: each nvcc job needs several GB of RAM.
NATTEN_CUDA_ARCH="${NATTEN_CUDA_ARCH:-7.5}" NATTEN_N_WORKERS="${NATTEN_N_WORKERS:-4}" \
  "$A/bin/pip" install --no-build-isolation natten==0.17.1
"$A/bin/pip" install -q allin1==1.1.0 demucs

echo "== CUE-DETR (GPU)"
C="$DATA/tools/cue-detr-venv"
python3.11 -m venv "$C"
"$C/bin/pip" install -q -U pip
"$C/bin/pip" install -q torch==2.4.1 $TORCH_CUDA
"$C/bin/pip" install -q numpy==1.26.4 transformers==4.42.3 timm==1.0.7 librosa==0.10.2.post1 matplotlib==3.9.1 \
  scipy==1.14.0 pillow==10.4.0

echo "== checks"
"$A/bin/python" -W ignore -c "import torch, natten, allin1; assert torch.cuda.is_available(); print('allin1 on', torch.cuda.get_device_name(0))"
"$C/bin/python" -W ignore -c "import torch, transformers; assert torch.cuda.is_available(); print('CUE-DETR on', torch.cuda.get_device_name(0))"
echo "Setup done. Next: bash scripts/desktop_benchmark.sh"
