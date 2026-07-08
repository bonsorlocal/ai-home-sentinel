import React, { useEffect, useState } from "react";
import { getSegments, getCameras, getSettings, updateSettings } from "@/lib/api";
import { Panel, PanelHeader, DemoTag, fmtTime, fmtTimeShort } from "@/components/common";
import { FilmSlate, MagnifyingGlass, Play, Clock, Database, Tag } from "@phosphor-icons/react";
import { toast } from "sonner";

const SEG_LENGTHS = [15, 30, 60];

export default function DVR() {
  const [segments, setSegments] = useState([]);
  const [cameras, setCameras] = useState([]);
  const [settings, setSettings] = useState(null);
  const [f, setF] = useState({ camera_id: "", search: "" });
  const [selected, setSelected] = useState(null);

  useEffect(() => { getCameras().then(setCameras); getSettings().then(setSettings); }, []);
  useEffect(() => {
    const params = {};
    if (f.camera_id) params.camera_id = f.camera_id;
    if (f.search) params.search = f.search;
    getSegments(params).then((s) => { setSegments(s); setSelected((cur) => cur || (s.length ? s[0] : cur)); });
  }, [f]);

  const setSegLen = async (n) => { const s = await updateSettings({ dvr_segment_minutes: n }); setSettings(s); toast.success(`Segment length: ${n} min (applies to new recordings)`); };
  const setRetention = async (n) => { const s = await updateSettings({ dvr_retention_hours: n }); setSettings(s); toast.success(`Retention: ${n}h`); };

  const inputCls = "bg-[#0A0A0A] border border-[#27272A] text-white rounded-sm px-3 py-2 font-mono text-sm focus:outline-none focus:ring-1 focus:ring-[#007AFF]";

  return (
    <div data-testid="dvr-page" className="space-y-6">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-4xl sm:text-5xl font-black tracking-tighter">DVR</h1>
          <p className="micro-label mt-1">CONTINUOUS RECORDING · ~{settings?.dvr_retention_hours || 48}H ROLLING · THEN OVERWRITES OLDEST</p>
        </div>
        <DemoTag />
      </div>

      {/* Controls */}
      <Panel>
        <div className="p-4 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 items-end">
          <div>
            <div className="micro-label flex items-center gap-1 mb-1.5"><Clock size={11} /> Segment Length</div>
            <div className="flex gap-1.5" data-testid="segment-length-control">
              {SEG_LENGTHS.map((n) => (
                <button key={n} data-testid={`seg-len-${n}`} onClick={() => setSegLen(n)}
                  className={`px-3 py-2 rounded-sm border font-mono text-xs ${settings?.dvr_segment_minutes === n ? "border-[#007AFF] bg-[#007AFF]/10 text-[#007AFF]" : "border-[#27272A] text-[#71717A] hover:text-white"}`}>{n}m</button>
              ))}
            </div>
          </div>
          <div>
            <div className="micro-label flex items-center gap-1 mb-1.5"><Database size={11} /> Retention (h)</div>
            <div className="flex gap-1.5">
              {[24, 48, 72].map((n) => (
                <button key={n} data-testid={`retention-${n}`} onClick={() => setRetention(n)}
                  className={`px-3 py-2 rounded-sm border font-mono text-xs ${settings?.dvr_retention_hours === n ? "border-[#007AFF] bg-[#007AFF]/10 text-[#007AFF]" : "border-[#27272A] text-[#71717A] hover:text-white"}`}>{n}h</button>
              ))}
            </div>
          </div>
          <select data-testid="dvr-camera-filter" value={f.camera_id} onChange={(e) => setF({ ...f, camera_id: e.target.value })} className={inputCls}>
            <option value="">All cameras</option>
            {cameras.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          <div className="relative">
            <MagnifyingGlass size={16} className="absolute left-2.5 top-2.5 text-[#71717A]" />
            <input data-testid="dvr-search" value={f.search} onChange={(e) => setF({ ...f, search: e.target.value })} placeholder="Search segments…" className={`${inputCls} w-full pl-8`} />
          </div>
        </div>
      </Panel>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Player + selected segment */}
        <Panel className="lg:col-span-2 h-fit">
          <PanelHeader title="Playback" icon={Play} right={selected && <span className="font-mono text-[10px] text-[#71717A]">{selected.camera_name}</span>} />
          {selected ? (
            <div data-testid="dvr-player" className="p-4 space-y-4">
              <div className="relative aspect-video bg-[#0A0A0A] rounded-sm overflow-hidden border border-[#27272A] scanline">
                <img src={selected.thumbnail_url} alt="" className="w-full h-full object-cover opacity-70" />
                <div className="absolute inset-0 flex items-center justify-center">
                  <div className="w-14 h-14 rounded-full bg-black/60 border border-white/30 flex items-center justify-center"><Play size={24} weight="fill" className="text-white ml-1" /></div>
                </div>
                <span className="absolute bottom-2 left-2 micro-label bg-black/70 px-1.5 py-0.5" style={{ color: "#fff" }}>MOCK PLAYBACK · {selected.length_minutes}MIN SEGMENT</span>
              </div>
              {/* timeline scrubber */}
              <div>
                <div className="flex justify-between micro-label mb-1"><span>{fmtTimeShort(selected.start_time)}</span><span>{fmtTimeShort(selected.end_time)}</span></div>
                <div className="h-2 bg-[#0A0A0A] border border-[#27272A] rounded-sm relative">
                  <div className="absolute inset-y-0 left-0 bg-[#007AFF]/30" style={{ width: "38%" }} />
                  <div className="absolute inset-y-[-3px] left-[38%] w-0.5 bg-[#007AFF]" />
                </div>
              </div>
              <div className="grid grid-cols-2 gap-3 text-sm">
                <SegField label="AI Summary" value={selected.ai_summary} full accent />
                <SegField label="People" value={selected.people.join(", ") || "none"} />
                <SegField label="Objects" value={selected.objects.join(", ") || "none"} />
                <SegField label="Linked Events" value={`${selected.linked_event_ids.length} event(s)`} />
                <SegField label="Segment" value={`${fmtTime(selected.start_time)} → ${fmtTimeShort(selected.end_time)}`} />
              </div>
              <div className="flex gap-1.5 flex-wrap">
                {selected.tags.map((t) => <span key={t} className="font-mono text-[10px] px-1.5 py-0.5 bg-[#0A0A0A] border border-[#27272A] text-[#71717A]"><Tag size={9} className="inline mr-1" />{t}</span>)}
              </div>
            </div>
          ) : <div className="p-8 text-center text-[#71717A] font-mono text-sm">NO SEGMENTS</div>}
        </Panel>

        {/* Segment timeline list */}
        <Panel className="h-fit">
          <PanelHeader title={`Segments (${segments.length})`} icon={FilmSlate} />
          <div className="divide-y divide-[#27272A] max-h-[560px] overflow-y-auto">
            {segments.map((s) => (
              <button key={s.id} data-testid={`dvr-segment-${s.id}`} onClick={() => setSelected(s)}
                className={`w-full text-left flex gap-3 p-3 hover:bg-[#1C1C1F] transition-colors ${selected?.id === s.id ? "bg-[#1C1C1F] border-l-2 border-[#007AFF]" : "border-l-2 border-transparent"}`}>
                <img src={s.thumbnail_url} alt="" className="w-16 h-11 object-cover rounded-sm border border-[#27272A]" />
                <div className="min-w-0 flex-1">
                  <div className="font-mono text-[10px] text-[#71717A]">{fmtTime(s.start_time)}</div>
                  <div className="text-sm text-white truncate">{s.ai_summary}</div>
                  <div className="font-mono text-[10px] text-[#007AFF]">{s.camera_name} · {s.length_minutes}m</div>
                </div>
              </button>
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}

const SegField = ({ label, value, full, accent }) => (
  <div className={full ? "col-span-2" : ""}>
    <div className="micro-label" style={accent ? { color: "#007AFF" } : {}}>{label}</div>
    <div className="text-white">{value}</div>
  </div>
);
