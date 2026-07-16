import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { askAssistant, getHistory } from "@/lib/api";
import { Panel, fmtTimeShort } from "@/components/common";
import { Robot, PaperPlaneRight, User, LinkSimple, ShieldStar } from "@phosphor-icons/react";
import { toast } from "sonner";

const SESSION = "sentinel-main";
const SUGGESTIONS = [
  "Did any packages arrive today?",
  "Was there an unknown person at the door recently?",
  "When did Jane last come home?",
  "Anything unusual at night?",
];

export default function SentinelAI() {
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const endRef = useRef(null);

  useEffect(() => { getHistory(SESSION).then(setMessages); }, []);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, loading]);

  const send = async (text) => {
    const msg = (text ?? input).trim();
    if (!msg || loading) return;
    setInput(""); setLoading(true);
    setMessages((m) => [...m, { role: "user", content: msg, timestamp: new Date().toISOString() }]);
    try {
      const r = await askAssistant(SESSION, msg);
      setMessages((m) => [...m, { role: "assistant", content: r.answer, sources: r.sources, timestamp: new Date().toISOString() }]);
    } catch { toast.error("Sentinel AI error"); }
    finally { setLoading(false); }
  };

  return (
    <div data-testid="sentinel-page" className="space-y-6 max-w-4xl">
      <div className="flex items-center gap-3">
        <ShieldStar size={32} weight="fill" className="text-[#007AFF]" />
        <div>
          <h1 className="text-4xl sm:text-5xl font-black tracking-tighter">SENTINEL AI</h1>
          <p className="micro-label mt-1">GROUNDED IN EVENT LOG → DVR FALLBACK · GEMINI 3.1 PRO</p>
        </div>
      </div>

      <Panel className="flex flex-col h-[calc(100vh-260px)] min-h-[420px]">
        <div className="flex-1 overflow-y-auto p-4 space-y-4" data-testid="chat-window">
          {messages.length === 0 && !loading && (
            <div className="h-full flex flex-col items-center justify-center text-center gap-4">
              <Robot size={48} className="text-[#27272A]" />
              <p className="text-[#71717A] text-sm max-w-md">Ask about people, packages, unknown visitors, cameras or timeframes. I answer only from your events & DVR — separating facts from inference.</p>
              <div className="flex flex-wrap gap-2 justify-center">
                {SUGGESTIONS.map((s) => (
                  <button key={s} data-testid="suggestion-chip" onClick={() => send(s)} className="px-3 py-1.5 rounded-sm border border-[#27272A] hover:border-[#007AFF] text-[#A1A1AA] hover:text-white text-xs font-mono transition-colors">{s}</button>
                ))}
              </div>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`flex gap-3 fade-up ${m.role === "user" ? "flex-row-reverse" : ""}`}>
              <div className={`w-8 h-8 shrink-0 rounded-sm flex items-center justify-center border ${m.role === "user" ? "border-[#27272A] text-[#71717A]" : "border-[#007AFF]/40 text-[#007AFF] bg-[#007AFF]/10"}`}>
                {m.role === "user" ? <User size={16} /> : <Robot size={16} weight="fill" />}
              </div>
              <div className={`max-w-[80%] ${m.role === "user" ? "text-right" : ""}`}>
                <div className={`rounded-sm px-3 py-2 text-sm whitespace-pre-wrap ${m.role === "user" ? "bg-[#1C1C1F] text-white" : "bg-[#007AFF]/5 border border-[#007AFF]/20 text-[#D4D4D8]"}`}>
                  {m.content}
                </div>
                {m.sources?.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {m.sources.slice(0, 5).map((s, j) => (
                      <Link key={j} to={s.type === "dvr" ? "/dvr" : "/events"} className="inline-flex items-center gap-1 font-mono text-[10px] px-1.5 py-0.5 bg-[#0A0A0A] border border-[#27272A] text-[#71717A] hover:text-[#007AFF] uppercase">
                        <LinkSimple size={10} /> {s.type}:{fmtTimeShort(s.timestamp)}
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
          {loading && <div className="flex items-center gap-2 text-[#007AFF] micro-label live-dot"><Robot size={16} weight="fill" /> SENTINEL IS ANALYZING…</div>}
          <div ref={endRef} />
        </div>

        <div className="border-t border-[#27272A] p-3 flex gap-2">
          <input data-testid="chat-input" value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder="Ask Sentinel about what happened…"
            className="flex-1 bg-[#0A0A0A] border border-[#27272A] text-white rounded-sm px-3 py-2.5 font-mono text-sm focus:outline-none focus:ring-1 focus:ring-[#007AFF]" />
          <button data-testid="chat-send-btn" onClick={() => send()} disabled={loading}
            className="px-4 rounded-sm bg-[#007AFF] hover:bg-[#0056B3] text-white disabled:opacity-50 flex items-center gap-2 font-mono text-xs uppercase">
            <PaperPlaneRight size={16} weight="fill" /> Send
          </button>
        </div>
      </Panel>
    </div>
  );
}
