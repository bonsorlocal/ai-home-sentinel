# Beta kink list (daily notes)

Log one row per issue. Prefer fixing with `config.yaml` first, then `.\scripts\deploy_pi.ps1`.

| Date | Symptom | Change tried | Better / worse / same | Next |
|------|---------|--------------|------------------------|------|
| 2026-07-14 | (template) Live stream feels heavy with multiple tabs | Prefer one viewer tab | — | Remind household |
|  |  |  |  |  |
|  |  |  |  |  |

## What to watch each day

- False alarms (pets, shadows, headlights)
- Missed people
- Clip timing/quality
- DVR disk filling up
- Alert spam vs silence
- Tunnel / phone access after overnight

## Safe tweak order

1. `config.yaml` thresholds / FPS / detector interval
2. Deploy to Pi
3. Soak overnight
4. Code change only if config cannot fix it
