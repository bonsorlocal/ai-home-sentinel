# Deploying to Raspberry Pi

The Pi runs AI Home Sentinel as a systemd service (`sentinel.service`) from:

```
/home/sentinel/ai-home-sentinel
```

## Prerequisites

- Raspberry Pi on your LAN with the repo cloned and venv set up (see [README.md](../README.md))
- SSH access to the Pi as user `sentinel`
- `secrets.yaml` on the Pi with your keys (never commit)

## SSH setup (one-time on your laptop)

Add your Pi to `~/.ssh/config`:

```
Host sentinel-pi
    HostName 192.168.1.XXX
    User sentinel
    IdentityFile ~/.ssh/id_ed25519
```

Test: `ssh sentinel-pi echo ok`

> **Blocked on user SSH:** If the Pi is not reachable from this machine, complete the steps below manually on the Pi (or configure SSH first, then use the deploy script).

## First-time Pi setup

On the Pi (SSH in or use keyboard/monitor):

```bash
cd /home/sentinel/ai-home-sentinel
bash scripts/pi_setup.sh
# Edit secrets.yaml, then:
sudo systemctl start sentinel
```

## Manual deploy (on the Pi)

```bash
cd /home/sentinel/ai-home-sentinel
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart sentinel
```

Verify:

```bash
systemctl status sentinel
curl http://localhost:5000/health
curl http://localhost:5000/status | python3 -m json.tool
```

Brain smoke test (needs `grok_api_key` in `secrets.yaml`):

```bash
source venv/bin/activate
python scripts/smoke_brain.py
```

Open the dashboard: `http://<pi-ip>:5000`

## Automated deploy (from Windows laptop)

```powershell
$env:PI_HOST = "sentinel@192.168.1.XXX"   # your Pi IP
.\scripts\deploy_pi.ps1
```

This syncs code + `secrets.yaml` to the Pi and restarts the service.

## Automated deploy (from Mac/Linux laptop)

Once SSH works:

```bash
export PI_HOST=sentinel@192.168.1.XXX   # or use ~/.ssh/config Host alias
chmod +x scripts/deploy_pi.sh
./scripts/deploy_pi.sh
```

## After deploy checklist

- [ ] `/health` returns `{"ok": true}`
- [ ] `/status` shows `camera_active`, `motion_active`, `detector_active`
- [ ] Live video at `/video`
- [ ] `/api/dvr/status` shows `enabled: true`, `available: true`, and expected `storage_root`
- [ ] `smoke_brain.py` exits 0 (if brain enabled)
- [ ] Tier-2 event triggers phone push (if notifications enabled)

## DVR storage policy (USB-boot Pi)

For a Pi that boots from USB SSD, using the OS drive for DVR is valid.

Recommended config:

```yaml
dvr:
  enabled: true
  storage_root: "/home/sentinel/ai-home-sentinel/data/dvr"
  fallback_storage_root: ""
```

Quick checks:

```bash
mkdir -p /home/sentinel/ai-home-sentinel/data/dvr
sudo systemctl restart sentinel
curl -s http://localhost:5000/api/dvr/status | python3 -m json.tool
df -h /home/sentinel/ai-home-sentinel/data/dvr
```

Look for:

- `available: true`
- `active: true`
- `storage_root` matches your configured path
- enough free disk for your retention target

## Notifications on Pi

1. Subscribe to your ntfy topic on your phone.
2. Set `notifications.enabled: true` and `topic` in `config.yaml`.
3. Restart: `sudo systemctl restart sentinel`

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Camera unavailable | `sudo apt install python3-picamera2`; recreate venv with `--system-site-packages` |
| Detector inactive | Ensure `models/yolo_nano.pt` exists; check `journalctl -u sentinel -f` |
| Brain offline | Add `google_api_key` and/or `grok_api_key` to `secrets.yaml` on the Pi |
| No push alerts | Enable notifications in config; pick a unique topic; check cooldown |
