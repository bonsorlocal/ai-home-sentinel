#!/usr/bin/env bash
# Install Pi Python deps without pulling CUDA torch stacks.
set -euo pipefail
cd /home/sentinel/ai-home-sentinel
# shellcheck disable=SC1091
source venv/bin/activate

python -m pip install --upgrade pip
# Shadow broken system numpy from apt when using --system-site-packages.
python -m pip install --force-reinstall 'numpy<2.3'

# Ensure torch is present (CPU runtime; skip nvidia-* deps).
if ! python -c "import torch" 2>/dev/null; then
  python -m pip install --no-deps torch==2.15.0 torchvision || \
    python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
fi
python -m pip install filelock typing-extensions sympy networkx fsspec jinja2

# Core app deps without face_recognition (Python 3.13 / dlib often fails).
grep -vE '^(face_recognition|#|$)' requirements.txt > /tmp/req_core.txt || true
# Avoid re-resolving torch via ultralytics meta-deps that pull CUDA.
python -m pip install Flask psutil opencv-python-headless google-auth requests pytest
python -m pip install matplotlib pillow polars ultralytics-thop
python -m pip install --no-deps ultralytics

python - <<'PY'
import numpy
import torch
print("numpy", numpy.__version__, numpy.__file__)
print("torch", torch.__version__, "cuda", torch.cuda.is_available())
import flask
import cv2
print("flask", flask.__version__)
print("cv2", cv2.__version__)
import ultralytics
print("ultralytics", ultralytics.__version__)
PY

# Optional face stack (may fail on Python 3.13 aarch64 — do not fake it).
if [[ "${INSTALL_FACE_RECOGNITION:-0}" == "1" ]]; then
  bash scripts/_pi_install_face_recognition.sh
else
  echo "Skipping face_recognition (set INSTALL_FACE_RECOGNITION=1 to build dlib)."
  echo "Or run: bash scripts/_pi_install_face_recognition.sh"
fi

df -h /
du -sh venv
