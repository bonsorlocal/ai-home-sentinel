"""Sentinel AI: Event-Log-first, DVR-fallback retrieval + Gemini grounded reasoning."""
import os
import logging
from emergentintegrations.llm.chat import LlmChat, UserMessage

logger = logging.getLogger(__name__)
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")

SYSTEM_PROMPT = """You are Sentinel, a security-aware home-awareness assistant. You are NOT a generic chatbot.
Answer ONLY using the CONTEXT provided (event log, DVR segment summaries, people, cameras, rules, system status).
Rules:
- Clearly separate FACTS (what was detected) from INFERENCE (what it likely means). Label inference as such.
- Cite sources when possible using the given [source: ...] identifiers.
- State confidence and uncertainty. If the context is insufficient, say so plainly and suggest checking the DVR timeline.
- Be concise, calm and precise, like a security operator. Never invent detections that are not in the context."""


def _score(text_blob, terms):
    blob = text_blob.lower()
    return sum(1 for t in terms if t and t in blob)


def build_context(question, events, segments, people, cameras, rules, settings):
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
    lines.append(f"SYSTEM STATUS: mode={settings.get('mode')}, processing={settings.get('processing_mode')}, "
                 f"DVR retention={settings.get('dvr_retention_hours')}h, segment={settings.get('dvr_segment_minutes')}min, "
                 f"recognition={'on' if settings.get('recognition_enabled') else 'off'}.")

    lines.append("\n== EVENT LOG (searched first) ==")
    if top_events:
        for e in top_events:
            sid = f"event:{e['id']}"
            sources.append({"type": "event", "id": e["id"], "label": e.get("ai_summary") or e.get("type"), "timestamp": e.get("timestamp")})
            lines.append(f"[source: {sid}] {e.get('timestamp')} | {e.get('camera_name')} | type={e.get('type')} | "
                         f"person={e.get('person_name') or 'unknown/none'} | objects={e.get('objects')} | "
                         f"confidence={e.get('confidence')} | importance={e.get('importance')} | "
                         f"FACT={e.get('ai_summary')} | INFERENCE={e.get('ai_interpretation')}")
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
                sources.append({"type": "dvr", "id": sg["id"], "label": sg.get("ai_summary"), "timestamp": sg.get("start_time")})
                lines.append(f"[source: {sid}] {sg.get('start_time')} -> {sg.get('end_time')} | {sg.get('camera_name')} | "
                             f"people={sg.get('people')} | objects={sg.get('objects')} | tags={sg.get('tags')} | "
                             f"summary={sg.get('ai_summary')}")
        else:
            lines.append("No matching DVR segments.")

    # People + rules context (small, always included)
    lines.append("\n== ENROLLED PEOPLE ==")
    for p in people:
        lines.append(f"{p.get('name')} ({p.get('status')}, {p.get('relationship') or 'n/a'}), last_seen={p.get('last_seen')}")
    active_rules = [r for r in rules if r.get("active")]
    if active_rules:
        lines.append("\n== ACTIVE RULES ==")
        for r in active_rules:
            lines.append(f"{r.get('name')} [{r.get('severity')}]: {r.get('description')}")

    return "\n".join(lines), sources


async def ask_sentinel(session_id, question, context_str):
    if not EMERGENT_LLM_KEY:
        return "AI is not configured: missing EMERGENT_LLM_KEY."
    chat = LlmChat(
        api_key=EMERGENT_LLM_KEY,
        session_id=session_id,
        system_message=SYSTEM_PROMPT,
    ).with_model("gemini", "gemini-3.1-pro-preview")
    prompt = f"CONTEXT:\n{context_str}\n\nUSER QUESTION: {question}"
    resp = await chat.send_message(UserMessage(text=prompt))
    return resp
