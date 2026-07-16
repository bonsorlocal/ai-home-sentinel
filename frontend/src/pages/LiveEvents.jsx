import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getEvents, saveEvent, getHealth } from "@/lib/api";
import { Panel, PanelHeader, LiveTag, OfflineBanner, EmptyState, TypePill, ImportanceBadge, fmtTime } from "@/components/common";
import { CameraLive } from "@/components/CameraLive";
import { Broadcast, BookmarkSimple, FilmSlate } from "@phosphor-icons/react";
import { toast } from "sonner";

export default function LiveEvents() {
  const [events, setEvents] = useState([]);
  const [health, setHealth] = useState(null);
  const load = () => { getEvents({ limit: 40 }).then(setEvents); getHealth().then(setHealth); };
  useEffect(() => { load(); const t = setInterval(load, 6000); return () => clearInterval(t); }, []);

  const toggleSave = async (e) => {
    setEvents((prev) => prev.map((x) => (x.id === e.id ? { ...x, saved: !x.saved } : x)));
    try { await saveEvent(e.id, !e.saved); toast.success(!e.saved ? "Saved to Event Log" : "Removed from Event Log"); }
    catch { toast.error("Pi offline — cannot update"); load(); }
  };

  return (
    <div data-testid="live-events-page" className="space-y-6">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-4xl sm:text-5xl font-black tracking-tighter">LIVE EVENTS</h1>
          <p className="micro-label mt-1">REAL-TIME DETECTION STREAM · AUTO-REFRESH 6s</p>
        </div>
        <div className="flex items-center gap-2">{health?.pi_connected && <LiveTag />}</div>
      </div>

      {health?.offline && <OfflineBanner piBaseUrl={health?.pi_base_url} />}

      <CameraLive />

      <Panel>
        <PanelHeader title="Detection Feed" icon={Broadcast} />
        <div className="divide-y divide-[#27272A]">
          {events.length === 0 ? (
            <EmptyState online={health?.pi_connected} label="No live events reported yet." />
          ) : events.map((e) => (
            <div key={e.id} data-testid={`live-event-${e.id}`} className="grid grid-cols-1 md:grid-cols-[110px_1fr_auto] gap-4 p-4 hover:bg-[#1C1C1F] transition-colors fade-up">
              {e.thumbnail_url ? (
                <img src={e.thumbnail_url} alt="" className="w-full md:w-[110px] h-20 object-cover rounded-sm border border-[#27272A]" />
              ) : <div className="w-full md:w-[110px] h-20 rounded-sm border border-[#27272A] bg-[#0A0A0A] flex items-center justify-center"><Broadcast size={20} className="text-[#3F3F46]" /></div>}
              <div className="min-w-0 space-y-1.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <TypePill type={e.type} />
                  <ImportanceBadge level={e.importance} />
                  {e.known && <span className="font-mono text-[10px] text-[#34C759] uppercase">{e.person_name}</span>}
                </div>
                <div className="text-sm text-white">{e.ai_summary}</div>
                <div className="text-sm text-[#A1A1AA] border-l-2 border-[#007AFF]/50 pl-2">
                  <span className="micro-label mr-2" style={{ color: "#007AFF" }}>AI INFERENCE</span>{e.ai_interpretation}
                </div>
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {(e.objects || []).map((o) => <span key={o} className="font-mono text-[10px] px-1.5 py-0.5 bg-[#0A0A0A] border border-[#27272A] text-[#71717A] uppercase">{o}</span>)}
                </div>
              </div>
              <div className="flex md:flex-col items-end justify-between gap-2 text-right">
                <div className="font-mono text-[10px] text-[#71717A]">
                  <div>{e.camera_name}</div>
                  <div>{fmtTime(e.timestamp)}</div>
                  <div className="text-[#007AFF]">CONF {Math.round(e.confidence * 100)}%</div>
                </div>
                <div className="flex gap-1.5">
                  <button data-testid={`save-event-${e.id}`} onClick={() => toggleSave(e)} title="Save to Event Log"
                    className={`p-1.5 rounded-sm border transition-colors ${e.saved ? "border-[#007AFF] text-[#007AFF] bg-[#007AFF]/10" : "border-[#27272A] text-[#71717A] hover:text-white"}`}>
                    <BookmarkSimple size={16} weight={e.saved ? "fill" : "regular"} />
                  </button>
                  <Link to="/dvr" data-testid={`dvr-link-${e.id}`} title="Open in DVR" className="p-1.5 rounded-sm border border-[#27272A] text-[#71717A] hover:text-white">
                    <FilmSlate size={16} />
                  </Link>
                </div>
              </div>
            </div>
          ))}
        </div>
      </Panel>
    </div>
  );
}
