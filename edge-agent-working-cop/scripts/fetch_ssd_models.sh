#!/usr/bin/env bash
# Download MobileNet-SSD Caffe models for edge-agent (Pi 4) into edge-agent/models/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL_DIR="$ROOT/models"
mkdir -p "$MODEL_DIR"
PROTO_URL="https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master/deploy.prototxt"
WEIGHTS_URL="https://github.com/chuanqi305/MobileNet-SSD/raw/master/mobilenet_iter_73000.caffemodel"
echo "Downloading prototxt -> ${MODEL_DIR}/MobileNetSSD_deploy.prototxt"
curl -fsSL -o "${MODEL_DIR}/MobileNetSSD_deploy.prototxt" "${PROTO_URL}"
echo "Downloading weights -> ${MODEL_DIR}/mobilenet_iter_73000.caffemodel"
curl -fsSL -L -o "${MODEL_DIR}/mobilenet_iter_73000.caffemodel" "${WEIGHTS_URL}"
echo "Done."
