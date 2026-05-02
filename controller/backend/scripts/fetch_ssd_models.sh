#!/usr/bin/env bash
# Downloads MobileNet-SSD Caffe models expected by app/detector.py into backend/models/.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL_DIR="$ROOT/models"
mkdir -p "$MODEL_DIR"
PROTO_URL="https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master/deploy.prototxt"
# GitHub hosts VOC weights as mobilenet_iter_73000.caffemodel (MobileNetSSD_deploy.caffemodel is absent / 404).
WEIGHTS_URL="https://github.com/chuanqi305/MobileNet-SSD/raw/master/mobilenet_iter_73000.caffemodel"

echo "Downloading prototxt -> ${MODEL_DIR}/MobileNetSSD_deploy.prototxt"
curl -fsSL -o "${MODEL_DIR}/MobileNetSSD_deploy.prototxt" "${PROTO_URL}"

echo "Downloading weights -> ${MODEL_DIR}/mobilenet_iter_73000.caffemodel"
curl -fsSL -L -o "${MODEL_DIR}/mobilenet_iter_73000.caffemodel" "${WEIGHTS_URL}"

echo "Done. Verify with: curl -s http://127.0.0.1:8000/system/recording | python3 -m json.tool"
