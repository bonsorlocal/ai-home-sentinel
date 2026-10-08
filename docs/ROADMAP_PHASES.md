# AI Home Sentinel Canonical Phase Map (9A-10)

This document is the single source of truth for phase naming in this repo.
If another note or comment conflicts with this file, follow this file.

## Implemented foundation

- Phase 2: camera + live dashboard
- Phase 3: motion sessions + event ledger
- Phase 4: object detection
- Phase 5: face recognition
- Phase 7: rule reasoner (tiering)
- Phase 7b: phone push notifications (ntfy)
- Phase 8: clip capture + clip metadata

## Unified roadmap (9A-10)

| Phase | Scope | Status |
|-------|-------|--------|
| 9A.1 | Roadmap/config/doc conflict cleanup | Done |
| 9A.2 | USB-boot DVR policy + health checks | Done |
| 9A.3 | DVR pin/export APIs for 48h review | Done |
| 9B | Adaptive ResourceGuard + detector `auto`/`cloud` | Done |
| 9B.1 | Reasoning quality (planner + evidence confidence) | Done |
| 9C | Cloud live door vision + reasoner P4/P5 | Done |
| 9C.1 | Owner enrollment from natural language | Done |
| 9D | ntfy action buttons + ActionHandler | Done |
| 9D.1 | Resident capabilities + privileged gates | Done |
| 9E | CameraVoice (`voice_out`) + Google TTS playback | Done |
| 9E.1 | Google-first provider with Grok fallback | Done |
| 9F | On-demand DVR export + brain DVR context | Done |
| 10 | Household expansion + optional Twilio bridge | Partial (profiles; telephony stub behind flags) |

## Phase 9 detail

- **9A.1** — `docs/ROADMAP_PHASES.md` is canonical; module headers and README link here.
- **9A.2** — DVR writes to OS USB path (`data/dvr`); `/api/dvr/status` reports `storage_root`, `using_fallback`, disk usage.
- **9B** — Query planner in `sentinel/brain.py`; confidence labels; reasoner config in `config.yaml`.
- **9C** — Chat phrases like "I'm Jordan, make me the owner" trigger enrollment; `/api/profile/*` APIs + dashboard card.
- **9D** — Owner/resident profiles in `sentinel/memory.py`; `_owner_gate` on privileged dashboard actions.
- **9E** — `provider: auto` tries Google Gemini when `google_api_key` is set, else Grok.

## Phase 10 detail

- Multiple resident profiles with appearance signatures (not face-only).
- `household.max_residents` cap; optional tracks behind flags:
  - `household.mobile_pwa_enabled` — Track A (PWA polish)
  - `household.telephony_enabled` — Track B (telephony bridge)
- Dashboard reports Phase 10 when at least one resident profile exists.

## Notes

- "Phase 9" in this repo refers to the 9A-9E sequence above.
- "Phase 10" UX tracks remain optional and feature-flagged.
- Keep module comments aligned with these labels.
