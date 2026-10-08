"""Sentinel AI: Event-Log-first, DVR-fallback retrieval + Gemini grounded reasoning."""
import base64
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


def _llm_key() -> str:
    return (os.environ.get("EMERGENT_LLM_KEY") or "").strip()


def _pi_base() -> str:
    return os.environ.get("PI_BASE_URL", "http://127.0.0.1:5000").rstrip("/")


def _gemini_model() -> str:
    return os.environ.get("EMERGENT_GEMINI_MODEL", "gemini-1.5-flash")


# Back-compat aliases used by older callers / tests.
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")
PI_BASE_URL = _pi_base()
GEMINI_MODEL = _gemini_model()

SYSTEM_PROMPT = """You are Sentinel, a security-aware home-awareness assistant powered by Google Gemini.
You are NOT a generic chatbot.
Answer using the CONTEXT provided (event log, DVR segment summaries, people, cameras, rules, system status, and any LIVE CAMERA note).
Rules:
- Clearly separate FACTS (what was detected) from INFERENCE (what it likely means). Label inference as such.
- Cite sources when possible using the given [source: ...] identifiers.
- State confidence and uncertainty. If the context is insufficient, say so plainly and suggest checking the DVR timeline.
- Be concise, calm and precise, like a security operator. Never invent detections that are not in the context.
- If the user asks whether you use Gemini, Google API keys, or which model: answer factually about Gemini / the configured model. Do NOT describe the camera scene for those meta questions."""

_META_HINTS = (
    "gemini",
    "google api",
    "google_api",
    "api key",
    "api keys",
    "which model",
    "what model",
    "are you using",
    "are you running",
    "running through",
    "powered by",
    "which api",
    "what api",
    "language model",
    "llm",
)

_LIVE_HINTS = (
    "what do you see",
    "what can you see",
    "right now",
    "live",
    "scene",
    "in the frame",
    "in front of the camera",
    "describe the room",
    "who is there",
    "look at",
)


def is_meta_question(question: str) -> bool:
    q = (question or "").strip().lower()
    if not q:
        return False
    return any(h in q for h in _META_HINTS)


def wants_live_frame(question: str) -> bool:
    q = (question or "").strip().lower()
    if not q or is_meta_question(q):
        return False
    return any(h in q for h in _LIVE_HINTS)


def _score(text_blob, terms):
    blob = text_blob.lower()
    return sum(1 for t in terms if t and t in blob)


def build_context(question, events, segments, people, cameras, rules, settings, live_note=None):
    """Event Log first; DVR summaries as fallback. Returns (context_str, sources)."""
    q = question.lower()
    terms = [w for w in q.replace("?", " ").replace(",", " ").split() if len(w) > 2]

    # 1) Event Log first — prefer saved/important events, ranked by term overlap.
    scored_events = []
    for e in events:
        blob = " ".join([
            e.get("camera_name", ""), e.get("type", ""), e.get("person_name") or "",
            " ".join(e.get("objects", [])), " ".join(e.get("tags", [])),
            e.get("ai_summary") or "", e.get("ai_interpretation") or "", e.get("importance", ""),
        ])
        s = _score(blob, terms)
        if e.get("saved"):
            s += 1
        if e.get("importance") in ("high", "critical"):
            s += 1
        if s > 0:
            scored_events.append((s, e))
    scored_events.sort(key=lambda x: (x[0], x[1].get("timestamp", "")), reverse=True)
    top_events = [e for _, e in scored_events[:6]]

    sources = []
    lines = []
    lines.append(
        f"SYSTEM STATUS: mode={settings.get('mode')}, processing={settings.get('processing_mode')}, "
        f"DVR retention={settings.get('dvr_retention_hours')}h, segment={settings.get('dvr_segment_minutes')}min, "
        f"recognition={'on' if settings.get('recognition_enabled') else 'off'}."
    )
    lines.append(
        f"AI RUNTIME: powered_by=Google Gemini, model={_gemini_model()}, "
        f"key_configured={'yes' if _llm_key() else 'no'}."
    )
    if live_note:
        lines.append(f"\n== LIVE CAMERA ==\n{live_note}")

    lines.append("\n== EVENT LOG (searched first) ==")
    if top_events:
        for e in top_events:
            sid = f"event:{e['id']}"
            sources.append({
                "type": "event",
                "id": e["id"],
                "label": e.get("ai_summary") or e.get("type"),
                "timestamp": e.get("timestamp"),
            })
            lines.append(
                f"[source: {sid}] {e.get('timestamp')} | {e.get('camera_name')} | type={e.get('type')} | "
                f"person={e.get('person_name') or 'unknown/none'} | objects={e.get('objects')} | "
                f"confidence={e.get('confidence')} | importance={e.get('importance')} | "
                f"FACT={e.get('ai_summary')} | INFERENCE={e.get('ai_interpretation')}"
            )
    else:
        lines.append("No matching events in the Event Log.")

    # 2) DVR fallback — used when event log is thin.
    if len(top_events) < 3:
        scored_segs = []
        for sg in segments:
            blob = " ".join([
                sg.get("camera_name", ""), " ".join(sg.get("people", [])),
                " ".join(sg.get("objects", [])), " ".join(sg.get("tags", [])), sg.get("ai_summary") or "",
            ])
            s = _score(blob, terms)
            if s > 0:
                scored_segs.append((s, sg))
        scored_segs.sort(key=lambda x: (x[0], x[1].get("start_time", "")), reverse=True)
        top_segs = [sg for _, sg in scored_segs[:5]]
        lines.append("\n== DVR SEGMENT SUMMARIES (fallback context) ==")
        if top_segs:
            for sg in top_segs:
                sid = f"dvr:{sg['id']}"
                sources.append({
                    "type": "dvr",
                    "id": sg["id"],
                    "label": sg.get("ai_summary"),
                    "timestamp": sg.get("start_time"),
                })
                lines.append(
                    f"[source: {sid}] {sg.get('start_time')} -> {sg.get('end_time')} | {sg.get('camera_name')} | "
                    f"people={sg.get('people')} | objects={sg.get('objects')} | tags={sg.get('tags')} | "
                    f"summary={sg.get('ai_summary')}"
                )
        else:
            lines.append("No matching DVR segments.")

    # People + rules context (small, always included)
    lines.append("\n== ENROLLED PEOPLE ==")
    for p in people:
        lines.append(
            f"{p.get('name')} ({p.get('status')}, {p.get('relationship') or 'n/a'}), "
            f"last_seen={p.get('last_seen')}"
        )
    active_rules = [r for r in rules if r.get("active")]
    if active_rules:
        lines.append("\n== ACTIVE RULES ==")
        for r in active_rules:
            lines.append(f"{r.get('name')} [{r.get('severity')}]: {r.get('description')}")

    return "\n".join(lines), sources


def meta_provider_answer() -> str:
    if _llm_key():
        return (
            f"Yes — Emergent Sentinel AI is powered by Google Gemini "
            f"(model: {_gemini_model()}) using EMERGENT_LLM_KEY / google_api_key."
        )
    return (
        "I am configured for Google Gemini, but EMERGENT_LLM_KEY is missing, "
        "so cloud chat cannot run yet."
    )


async def fetch_live_jpeg() -> Optional[bytes]:
    """Pull one fresh JPEG from the local Sentinel stack (PC or Pi)."""
    url = f"{_pi_base()}/api/live.jpg"
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            ctype = (r.headers.get("content-type") or "").lower()
            if "jpeg" not in ctype and "jpg" not in ctype and not r.content.startswith(b"\xff\xd8"):
                return None
            return r.content if r.content else None
    except Exception as e:  # noqa: BLE001
        logger.debug("live jpeg fetch failed: %s", e)
        return None


async def ask_gemini_vision(question: str, context_str: str, jpeg_bytes: bytes) -> str:
    """Direct Generative Language call with an optional live frame."""
    key = _llm_key()
    if not key:
        return "AI is not configured: missing EMERGENT_LLM_KEY."
    model = _gemini_model()
    if model == "gemini-3.1-pro-preview":
        model = "gemini-1.5-pro"
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={key}"
    )
    b64 = base64.b64encode(jpeg_bytes).decode("ascii")
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": f"{SYSTEM_PROMPT}\n\nCONTEXT:\n{context_str}\n\nUSER QUESTION: {question}"},
                    {"inline_data": {"mime_type": "image/jpeg", "data": b64}},
                ],
            }
        ]
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
    candidates = data.get("candidates") or []
    if not candidates:
        return "No response from Gemini."
    parts = candidates[0].get("content", {}).get("parts") or []
    text = "".join(str(p.get("text", "")) for p in parts).strip()
    return text or "Empty response from Gemini."


async def ask_sentinel(session_id, question, context_str, jpeg_bytes=None):
    if is_meta_question(question):
        return meta_provider_answer()

    key = _llm_key()
    if not key:
        return "AI is not configured: missing EMERGENT_LLM_KEY."

    if jpeg_bytes:
        try:
            return await ask_gemini_vision(question, context_str, jpeg_bytes)
        except Exception as e:  # noqa: BLE001
            logger.warning("vision call failed, falling back to text: %s", e)

    try:
        from emergentintegrations.llm.chat import LlmChat, UserMessage
    except ImportError:
        return (
            "Sentinel AI library (emergentintegrations) is not installed on this device. "
            "The camera and events still work; install it to enable chat."
        )
    chat = LlmChat(
        api_key=key,
        session_id=session_id,
        system_message=SYSTEM_PROMPT,
    ).with_model("gemini", _gemini_model())
    prompt = f"CONTEXT:\n{context_str}\n\nUSER QUESTION: {question}"
    resp = await chat.send_message(UserMessage(text=prompt))
    return resp
