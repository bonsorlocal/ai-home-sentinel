import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getHealth, getCameras, getEvents, getSettings, updateSettings, askAssistant, streamUrl } from "@/lib/api";
import { Panel, PanelHeader, LiveTag, OfflineBanner, EmptyState, TypePill, ImportanceBadge, fmtTime } from "@/components/common";
import { Warning, Cpu, Thermometer, HardDrives, VideoCamera, PaperPlaneRight, House, Airplane, MoonStars } from "@phosphor-icons/react";
import { toast } from "sonner";

const MODES = [
  { key: "home", label: "Home", icon: House },
  { key: "away", label: "Away", icon: Airplane },
  { key: "night", label: "Night", icon: MoonStars },
];

export default function Dashboard() {
  const [health, setHealth] = useState(null);
  const [cameras, setCameras] = useState([]);
  const [events, setEvents] = useState([]);
  const [settings, setSettings] = useState(null);
  const [q, setQ] = useState("");
  const [answer, setAnswer] = useState(null);
  const [asking, setAsking] = useState(false);

  const load = () => {
    getHealth().then(setHealth);
    getCameras().then(setCameras);
    getEvents({ limit: 6 }).then(setEvents);
    getSettings().then(setSettings);
  };
  useEffect(() => { load(); const t = setInterval(() => { getHealth().then(setHealth); getEvents({ limit: 6 }).then(setEvents); }, 8000); return () => clearInterval(t); }, []);

  const changeMode = async (mode) => {
    const s = await updateSettings({ mode });
    setSettings(s);
    toast.success(`Mode set to ${mode.toUpperCase()}`);
  };

  const unknown = events.find((e) => e.type === "unknown_person");
  const currentPerson = events.find((e) => e.type === "person" && e.known);
  const m = health?.metrics || {};

  const ask = async () => {
    if (!q.trim()) return;
    setAsking(true); setAnswer(null);
    try { const r = await askAssistant("dashboard", q); setAnswer(r.answer); }
    catch { toast.error("Assistant failed"); }
    finally { setAsking(false); }
  };

  return (
    <div data-testid="dashboard-page" className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-4xl sm:text-5xl font-black tracking-tighter">DASHBOARD</h1>
          <p className="micro-label mt-1">HOME AWARENESS OVERVIEW</p>
        </div>
        <div className="flex items-center gap-2" data-testid="mode-switcher">
          {MODES.map((mo) => (
            <button key={mo.key} data-testid={`mode-${mo.key}`} onClick={() => changeMode(mo.key)}
              className={`flex items-center gap-2 px-3 py-2 rounded-sm border font-mono text-xs uppercase tracking-wider transition-colors ${settings?.mode === mo.key ? "border-[#007AFF] bg-[#007AFF]/10 text-[#007AFF]" : "border-[#27272A] text-[#71717A] hover:border-white hover:text-white"}`}>
              <mo.icon size={16} weight="bold" /> {mo.label}
            </button>
          ))}
        </div>
      </div>

      {/* Pi offline banner */}
      {health?.offline && <OfflineBanner piBaseUrl={health?.pi_base_url} />}

      {/* Unknown warning */}
      {unknown && (
        <Panel data-testid="unknown-warning" className="border-[#FF3B30]/40 bg-[#FF3B30]/5">
          <div className="flex items-center gap-3 p-4">
            <Warning size={28} weight="fill" className="text-[#FF3B30] live-dot" />
            <div className="flex-1">
              <div className="font-bold text-[#FF3B30]">UNKNOWN PERSON DETECTED</div>
              <div className="text-sm text-[#A1A1AA]">{unknown.ai_interpretation}</div>
            </div>
            <div className="text-right font-mono text-xs text-[#A1A1AA]">
              <div>{unknown.camera_name}</div>
              <div>{fmtTime(unknown.timestamp)}</div>
            </div>
            <Link to="/live" data-testid="unknown-view-btn" className="px-3 py-2 rounded-sm bg-[#FF3B30] text-white font-mono text-xs uppercase">View</Link>
          </div>
        </Panel>
      )}

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Live preview grid */}
        <Panel className="xl:col-span-2">
          <PanelHeader title="Live Camera Preview" icon={VideoCamera} right={health?.pi_connected && <LiveTag />} />
          {health?.offline || cameras.length === 0 ? (
            <EmptyState label="No cameras reported by the Pi." online={health?.pi_connected} />
          ) : (
          <div className="grid grid-cols-2 gap-px bg-[#27272A]">
            {cameras.map((c) => (
              <div key={c.id} data-testid={`cam-tile-${c.id}`} className="relative aspect-video bg-[#0A0A0A] scanline overflow-hidden group">
                <img src={streamUrl(c.id)} alt={c.name} data-testid={`cam-stream-${c.id}`}
                  className="w-full h-full object-cover"
                  onError={(e) => { e.currentTarget.style.display = "none"; e.currentTarget.nextSibling.style.display = "flex"; }} />
                <div className="w-full h-full items-center justify-center text-[#3F3F46] font-mono text-xs" style={{ display: "none" }}>STREAM UNAVAILABLE</div>
                <div className="absolute top-2 left-2 flex items-center gap-2">
                  <span className="micro-label bg-black/60 px-1.5 py-0.5" style={{ color: "#fff" }}>{c.name}</span>
                </div>
                <div className="absolute top-2 right-2">
                  <span className={`w-2 h-2 rounded-full inline-block ${c.status === "online" ? "bg-[#34C759] live-dot" : "bg-[#71717A]"}`} />
                </div>
              </div>
            ))}
          </div>
          )}
        </Panel>

        {/* Right column: status + assistant */}
        <div className="space-y-6">
          <Panel>
            <PanelHeader title="Current Activity" />
            <div className="p-4 space-y-3 text-sm">
              <Row label="Mode" value={<span className="text-[#007AFF] uppercase font-mono">{settings?.mode}</span>} />
              <Row label="Person" value={currentPerson ? <span className="text-[#34C759]">{currentPerson.person_name}</span> : <span className="text-[#71717A]">none</span>} />
              <Row label="Unknown" value={unknown ? <span className="text-[#FF3B30]">yes</span> : <span className="text-[#34C759]">clear</span>} />
              <Row label="Cameras" value={<span className="font-mono">{health?.cameras_online}/{health?.cameras_total} online</span>} />
            </div>
          </Panel>

          <Panel>
            <PanelHeader title="System Health" icon={Cpu} right={<span className={`micro-label ${health?.pi_connected ? "" : "text-[#FF3B30]"}`}>{health?.pi_connected ? "LIVE" : "OFFLINE"}</span>} />
            <div className="p-4 grid grid-cols-2 gap-3">
              <Metric icon={Cpu} label="CPU" value={m.cpu_percent != null ? `${m.cpu_percent}%` : "—"} />
              <Metric icon={Thermometer} label="Temp" value={m.temp_c != null ? `${m.temp_c}°C` : "—"} />
              <Metric icon={HardDrives} label="Disk" value={m.disk_percent != null ? `${m.disk_percent}%` : "—"} />
              <Metric icon={VideoCamera} label="Pi" value={health?.pi_connected ? "LINKED" : "OFFLINE"} />
            </div>
          </Panel>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Recent events */}
        <Panel className="xl:col-span-2">
          <PanelHeader title="Recent Events" right={<Link to="/events" className="micro-label hover:text-white" data-testid="view-all-events">VIEW ALL →</Link>} />
          <div className="divide-y divide-[#27272A]">
            {events.map((e) => (
              <Link to="/live" key={e.id} data-testid={`recent-event-${e.id}`} className="flex items-center gap-3 p-3 hover:bg-[#1C1C1F] transition-colors">
                <img src={e.thumbnail_url} alt="" className="w-14 h-10 object-cover rounded-sm border border-[#27272A]" />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <TypePill type={e.type} />
                    <ImportanceBadge level={e.importance} />
                    {e.is_demo && <DemoTag />}
                  </div>
                  <div className="text-sm text-[#A1A1AA] truncate mt-1">{e.ai_summary}</div>
                </div>
                <div className="text-right font-mono text-[10px] text-[#71717A]">
                  <div>{e.camera_name}</div>
                  <div>{fmtTime(e.timestamp)}</div>
                </div>
              </Link>
            ))}
          </div>
        </Panel>

        {/* Assistant quick query */}
        <Panel>
          <PanelHeader title="Ask Sentinel" icon={PaperPlaneRight} />
          <div className="p-4 space-y-3">
            <div className="flex gap-2">
              <input data-testid="dashboard-ask-input" value={q} onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && ask()}
                placeholder="Any packages today?"
                className="flex-1 bg-[#0A0A0A] border border-[#27272A] text-white rounded-sm px-3 py-2 font-mono text-sm focus:outline-none focus:ring-1 focus:ring-[#007AFF]" />
              <button data-testid="dashboard-ask-btn" onClick={ask} disabled={asking}
                className="px-3 rounded-sm bg-[#007AFF] hover:bg-[#0056B3] text-white disabled:opacity-50">
                <PaperPlaneRight size={16} weight="fill" />
              </button>
            </div>
            {asking && <div className="micro-label live-dot">SENTINEL IS THINKING…</div>}
            {answer && <div data-testid="dashboard-answer" className="text-sm text-[#D4D4D8] border-l-2 border-[#007AFF] pl-3 whitespace-pre-wrap">{answer}</div>}
            <Link to="/sentinel" className="micro-label hover:text-white inline-block">OPEN FULL ASSISTANT →</Link>
          </div>
        </Panel>
      </div>
    </div>
  );
}

const Row = ({ label, value }) => (
  <div className="flex items-center justify-between">
    <span className="micro-label">{label}</span>
    <span>{value}</span>
  </div>
);
const Metric = ({ icon: Icon, label, value }) => (
  <div className="border border-[#27272A] rounded-sm p-3">
    <div className="flex items-center gap-1.5 micro-label"><Icon size={12} /> {label}</div>
    <div className="font-mono text-lg mt-1">{value}</div>
  </div>
);
