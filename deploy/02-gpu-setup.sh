#!/usr/bin/env bash
# Lets Docker containers use an NVIDIA GPU (Immich ML on CUDA, NVENC transcoding, Whisper).
# Run with: sudo bash 02-gpu-setup.sh
#
# Restarting Docker briefly restarts running containers (Immich comes back on its own).
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run this with sudo" >&2; exit 1; }
command -v nvidia-smi >/dev/null || { echo "install the NVIDIA driver first (nvidia-smi not found)" >&2; exit 1; }

# 1. NVIDIA Container Toolkit, from NVIDIA's own apt repo
#    https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html
if ! command -v nvidia-ctk >/dev/null; then
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | gpg --dearmor --yes -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
    > /etc/apt/sources.list.d/nvidia-container-toolkit.list
  apt-get update -qq
  apt-get install -y -qq nvidia-container-toolkit
fi

# 2. Register the nvidia runtime with Docker (merges into an existing daemon.json, keeps a backup)
[ -f /etc/docker/daemon.json ] && cp -n /etc/docker/daemon.json /etc/docker/daemon.json.before-nvidia || true
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker

echo
echo "== result =="
nvidia-ctk --version | head -1
docker info --format 'docker runtimes: {{range $k, $v := .Runtimes}}{{$k}} {{end}}'
