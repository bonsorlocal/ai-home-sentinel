#!/usr/bin/env bash
# Install dlib + face_recognition into the Sentinel venv on Raspberry Pi.
# Python 3.13 aarch64 often lacks wheels — builds from source when needed.
# Does not silently fake a face stack: exits non-zero if import fails.
set -euo pipefail
cd /home/sentinel/ai-home-sentinel
# shellcheck disable=SC1091
source venv/bin/activate

echo "== face_recognition install =="
python - <<'PY'
import sys
print("python", sys.version)
PY

# Build deps for dlib (cmake + boost headers commonly needed).
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -y
sudo apt-get install -y --no-install-recommends \
  build-essential cmake \
  libopenblas-dev liblapack-dev \
  libx11-dev libgtk-3-dev \
  python3-dev

python -m pip install --upgrade pip setuptools wheel

# Prefer a prebuilt wheel if one exists; otherwise compile dlib.
if python -c "import face_recognition" 2>/dev/null; then
  echo "face_recognition already importable"
else
  echo "Installing dlib then face_recognition..."
  # Cap jobs to avoid OOM on Pi.
  export CMAKE_BUILD_PARALLEL_LEVEL="${CMAKE_BUILD_PARALLEL_LEVEL:-2}"
  python -m pip install --no-cache-dir dlib || {
    echo "ERROR: dlib install failed on $(python -c 'import sys; print(sys.version)')"
    echo "Do not enable face_recognition until dlib builds successfully."
    exit 1
  }
  python -m pip install --no-cache-dir face_recognition || {
    echo "ERROR: face_recognition pip install failed after dlib."
    exit 1
  }
fi

python - <<'PY'
import face_recognition
import dlib
print("OK face_recognition", getattr(face_recognition, "__version__", "?"))
print("OK dlib", dlib.__version__)
PY

echo "== face_recognition install complete =="
