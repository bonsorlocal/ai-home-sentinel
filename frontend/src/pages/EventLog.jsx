import React, { useEffect, useState } from "react";
import { getEvents, getCameras, getPeople } from "@/lib/api";
import { Panel, PanelHeader, DemoTag, TypePill, ImportanceBadge, fmtTime } from "@/components/common";
import { ListMagnifyingGlass, MagnifyingGlass, Tag, ShieldWarning } from "@phosphor-icons/react";

const TYPES = ["", "unknown_person", "person", "object", "motion"];

export default function EventLog() {
  const [events, setEvents] = useState([]);
  const [cameras, setCameras] = useState([]);
  const [people, setPeople] = useState([]);
  const [f, setF] = useState({ search: "", camera_id: "", type: "", person_id: "" });
  const [selected, setSelected] = useState(null);

  useEffect(() => { getCameras().then(setCameras); getPeople().then(setPeople); }, []);
  useEffect(() => {
    const params = { saved: true };
    Object.entries(f).forEach(([k, v]) => { if (v) params[k] = v; });
    getEvents(params).then(setEvents);
  }, [f]);

  const inputCls = "bg-[#0A0A0A] border border-[#27272A] text-white rounded-sm px-3 py-2 font-mono text-sm focus:outline-none focus:ring-1 focus:ring-[#007AFF]";

  return (
    <div data-testid="event-log-page" className="space-y-6">
      <div>
        <h1 className="text-4xl sm:text-5xl font-black tracking-tighter">EVENT LOG</h1>
        <p className="micro-label mt-1">SAVED IMPORTANT EVENTS · SEPARATE FROM CONTINUOUS DVR</p>
      </div>

      <Panel>
        <div className="p-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <div className="relative">
            <MagnifyingGlass size={16} className="absolute left-2.5 top-2.5 text-[#71717A]" />
            <input data-testid="event-search" value={f.search} onChange={(e) => setF({ ...f, search: e.target.value })} placeholder="Search keyword / tag…" className={`${inputCls} w-full pl-8`} />
          </div>
          <select data-testid="filter-camera" value={f.camera_id} onChange={(e) => setF({ ...f, camera_id: e.target.value })} className={inputCls}>
            <option value="">All cameras</option>
            {cameras.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <select data-testid="filter-type" value={f.type} onChange={(e) => setF({ ...f, type: e.target.value })} className={inputCls}>
            {TYPES.map((t) => <option key={t} value={t}>{t ? t.replace("_", " ") : "All types"}</option>)}
          </select>
          <select data-testid="filter-person" value={f.person_id} onChange={(e) => setF({ ...f, person_id: e.target.value })} className={inputCls}>
            <option value="">All people</option>
            {people.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      </Panel>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <Panel className="lg:col-span-2">
          <PanelHeader title={`Saved Events (${events.length})`} icon={ListMagnifyingGlass} />
          <div className="divide-y divide-[#27272A]">
            {events.length === 0 && <div className="p-8 text-center text-[#71717A] font-mono text-sm">NO EVENTS MATCH FILTERS</div>}
            {events.map((e) => (
              <button key={e.id} data-testid={`log-event-${e.id}`} onClick={() => setSelected(e)}
                className={`w-full text-left flex items-center gap-3 p-3 hover:bg-[#1C1C1F] transition-colors ${selected?.id === e.id ? "bg-[#1C1C1F]" : ""}`}>
                <img src={e.thumbnail_url} alt="" className="w-20 h-14 object-cover rounded-sm border border-[#27272A]" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <TypePill type={e.type} /><ImportanceBadge level={e.importance} />{e.is_demo && <DemoTag />}
                  </div>
                  <div className="text-sm text-white truncate mt-1">{e.ai_summary}</div>
                  <div className="flex gap-1.5 mt-1 flex-wrap">
                    {e.tags.map((t) => <span key={t} className="font-mono text-[10px] text-[#71717A]">#{t}</span>)}
                  </div>
                </div>
                <div className="text-right font-mono text-[10px] text-[#71717A]">
                  <div>{e.camera_name}</div><div>{fmtTime(e.timestamp)}</div>
                </div>
              </button>
            ))}
          </div>
        </Panel>

        <Panel className="h-fit sticky top-6">
          <PanelHeader title="Event Detail" icon={ShieldWarning} />
          {!selected ? (
            <div className="p-8 text-center text-[#71717A] font-mono text-sm">SELECT AN EVENT</div>
          ) : (
            <div data-testid="event-detail" className="p-4 space-y-3">
              <div className="relative aspect-video bg-[#0A0A0A] rounded-sm overflow-hidden border border-[#27272A]">
                <img src={selected.thumbnail_url} alt="" className="w-full h-full object-cover" />
                <span className="absolute bottom-2 left-2 micro-label bg-black/70 px-1.5 py-0.5" style={{ color: "#fff" }}>CLIP · {selected.clip_url ? "AVAILABLE" : "N/A (mock)"}</span>
              </div>
              <div className="flex items-center gap-2 flex-wrap"><TypePill type={selected.type} /><ImportanceBadge level={selected.importance} /></div>
              <Detail label="FACT" value={selected.ai_summary} />
              <Detail label="AI INFERENCE" value={selected.ai_interpretation} accent />
              <Detail label="Camera" value={selected.camera_name} />
              <Detail label="Time" value={fmtTime(selected.timestamp)} />
              <Detail label="Person" value={selected.person_name || "unknown / none"} />
              <Detail label="Confidence" value={`${Math.round(selected.confidence * 100)}%`} />
              <div>
                <div className="micro-label flex items-center gap-1 mb-1"><Tag size={11} /> Objects</div>
                <div className="flex gap-1.5 flex-wrap">{selected.objects.map((o) => <span key={o} className="font-mono text-[10px] px-1.5 py-0.5 bg-[#0A0A0A] border border-[#27272A] uppercase">{o}</span>)}</div>
              </div>
            </div>
          )}
        </Panel>
      </div>
    </div>
  );
}

const Detail = ({ label, value, accent }) => (
  <div>
    <div className="micro-label" style={accent ? { color: "#007AFF" } : {}}>{label}</div>
    <div className={`text-sm ${accent ? "text-[#D4D4D8] border-l-2 border-[#007AFF]/50 pl-2" : "text-white"}`}>{value}</div>
  </div>
);
