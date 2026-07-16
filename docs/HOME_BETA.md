# Home Beta: 24/7 Camera + Away Access

Personal beta checklist for running AI Home Sentinel at home and checking it while away.

## Stage status (maintained by deploy)

| Stage | Status |
|-------|--------|
| 24/7 local (service + camera + clips/DVR) | Ready — `sentinel` enabled; multi-day clips/DVR present |
| Dashboard token auth | Required — set `dashboard_token` in `secrets.yaml` |
| Free remote access | Cloudflare quick tunnel via `scripts/deploysshtunnel.ps1` |
| Phone alerts | ntfy topic in `config.yaml` → `notifications.topic` |
| Cheap domain (~$1 .xyz) | Deferred until budget allows — see [CHEAP_DOMAIN.md](CHEAP_DOMAIN.md) |
| Daily kink list | [BETA_KINKS.md](BETA_KINKS.md) |

## Open the dashboard when auth is on

```
http://192.168.1.244:5000/?token=YOUR_DASHBOARD_TOKEN
```

Or after a tunnel refresh:

```
https://YOUR-TUNNEL.trycloudflare.com/?token=YOUR_DASHBOARD_TOKEN
```

`/health` stays open without a token (for monitoring).

## Phone alerts (ntfy)

1. Install the [ntfy app](https://ntfy.sh).
2. Subscribe to topic: `home-sentinel-60dc9084` (also in `config.yaml` → `notifications.topic`).
3. Tier-2 events (unknown face, repeat person, night person) send a push.
4. A test push was sent when home-beta was enabled — open the ntfy app to confirm.

## Remote access (free)

```powershell
cd C:\Users\jorda\OneDrive\Documents\ai-home-sentinel-pi
.\scripts\deploysshtunnel.ps1
```

Bookmark the printed URL. Quick-tunnel URLs can change when the tunnel is restarted.

## Overnight soak check (do this tomorrow morning)

- [ ] `http://192.168.1.244:5000/health` still OK
- [ ] Live camera works
- [ ] New events/clips since yesterday
- [ ] DVR segments still growing
- [ ] `journalctl -u sentinel -n 50` has no crash loop
- [ ] Note anything annoying in [BETA_KINKS.md](BETA_KINKS.md)

## Tweak safely

1. Change one setting in `config.yaml` when possible.
2. Deploy: `.\scripts\deploy_pi.ps1`
3. Re-check overnight.
4. Change Python only when config is not enough.
