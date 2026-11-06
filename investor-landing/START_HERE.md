# Investor landing page — Emergent prompt pack

Use this folder to have Emergent build a **static investor landing page** for AI Home Sentinel. This page explains the product. It does not replace the live Pi demo (`scripts/investor_demo.ps1`).

You paste **one file** into Emergent chat:

1. **`PROMPT.txt`** → full build brief (structure, copy rules, design, acceptance)

---

## How the product is portrayed

AI Home Sentinel is a **home camera with a memory and a judgment**, running today as a single-home beta on a Raspberry Pi. The camera is the sensor. The product is the decision: who belongs here, what changed, and whether you need to look.

Investors should leave with four ideas:

- Traditional cameras sell footage and subscriptions. This sells fewer, better alerts.
- Detection stays on the device (motion, YOLO, faces, a tiered reasoner, local DVR). A cloud model is optional and only for language: chat, daily recap, clip summaries.
- The household is the data model: known people, appearance notes, Home / Away / Night, and an event ledger you can ask questions about.
- This is a working prototype with a bright line between what runs and what is next. Trust is the pitch.

| Line | Copy |
|------|------|
| Primary | A home camera that knows who belongs there. |
| Support | It watches locally, remembers the household, and only interrupts you when the moment actually matters. |

---

## Marketing technique

**Problem → mechanism → proof → boundary.** One job, one diagram, one night, then an honest limit.

1. **Job, not stack.** Open on the decision a person wants at 11:40pm: do I need to look at this? Do not open on YOLO, Raspberry Pi, or “AI-powered.”
2. **Category contrast, no fear ads.** The failure mode is alert fatigue and rented cloud footage. Competitors are implied by behavior (motion clips, subscriptions, cameras that do not know the household), never named.
3. **Mechanism as credibility.** One pipeline: camera → motion → detection → face/appearance → reasoner → phone or silence → “ask what happened.”
4. **One concrete night as proof.** Unknown person at night → one phone push. Known resident → quiet. Morning: “What happened last night?” from the ledger.
5. **Boundary as the close.** Two columns: “Running in the home beta” vs “Not claimed.” No TAM, ARR, user counts, or logos. The only ask is a live demo.
6. **Tone.** Calm and exact. Short sentences. Visual language matches the product UI: near-black, zinc borders, blue `#007AFF` rarely, red `#FF3B30` only for the unknown-person example.

### Claims that must not appear

Paying customers, multi-home SaaS, app-store product, always-on wake word, phone-call alerts, professional monitoring, guaranteed identification, “military grade,” cloud video storage as the product. Face match is a household aid, not proof of identity.

---

## In Emergent

1. Open your Emergent project (or start a new one for the landing page only).
2. Open `investor-landing/PROMPT.txt` on this PC.
3. Select all → copy → paste into Emergent chat.
4. Ask Emergent for a preview when the page builds.

This page is **static marketing only**. It must not call the Pi, require login, or invent a backend.

---

## After Emergent builds — check before sharing

- [ ] The unknown-person phone card is labeled **Example, not a live feed**
- [ ] The **What’s real** section (Running vs Not claimed) is intact
- [ ] No metrics, waitlist counts, competitor names, or fake testimonials appear
- [ ] “Request a demo” scrolls to the form; form does not pretend email was sent unless a real endpoint exists
- [ ] Usable at ~375px and ~1280px

Live product demo stays on the Pi:

```powershell
.\scripts\investor_demo.ps1
```
