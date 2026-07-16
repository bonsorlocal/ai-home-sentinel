#!/usr/bin/env bash
# Ensure Pi CSI camera overlay when auto-detect finds nothing.
# Requires reboot after changes. If overlays still fail with I2C errors,
# reseat the CSI ribbon cable on both the Pi and camera module.
set -euo pipefail

CONFIG="${1:-/boot/firmware/config.txt}"
if [[ ! -f "$CONFIG" ]]; then
  CONFIG="/boot/config.txt"
fi
if [[ ! -f "$CONFIG" ]]; then
  echo "Could not find boot config.txt"
  exit 1
fi

echo "Using boot config: $CONFIG"

if rpicam-hello --list-cameras 2>/dev/null | grep -q "^0 :"; then
  echo "Camera already detected; no overlay change needed."
  exit 0
fi

echo "No camera detected. Checking kernel probe messages..."
dmesg | grep -iE 'imx708|imx219|imx477|probe with driver' | tail -6 || true

backup="${CONFIG}.bak.$(date +%Y%m%d%H%M%S)"
sudo cp "$CONFIG" "$backup"
echo "Backup saved to $backup"

add_overlay() {
  local overlay="$1"
  if grep -qE "^dtoverlay=${overlay}\b" "$CONFIG"; then
    echo "Overlay already present: $overlay"
    return 0
  fi
  echo "Adding overlay: $overlay"
  sudo tee -a "$CONFIG" >/dev/null <<EOF

# AI Home Sentinel camera fix ($(date -Iseconds))
dtoverlay=${overlay}
EOF
}

# Prefer Pi 5 Camera Module 3 on cam0, then cam1, then Module 2 (imx219).
if grep -qE "^dtoverlay=imx708,cam0\b" "$CONFIG"; then
  if ! grep -qE "^dtoverlay=imx708,cam1\b" "$CONFIG"; then
    add_overlay "imx708,cam1"
  elif ! grep -qE "^dtoverlay=imx219,cam0\b" "$CONFIG"; then
    add_overlay "imx219,cam0"
  else
    echo "Common overlays already present."
  fi
else
  add_overlay "imx708,cam0"
fi

echo
echo "Overlay update complete. Reboot once:"
echo "  sudo reboot"
echo
echo "If rpicam-hello still shows no camera AND dmesg has"
echo "'failed to read chip id', reseat the CSI ribbon cable"
echo "on both the Pi and the camera module, then reboot again."
