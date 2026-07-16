import React, { useEffect, useState } from "react";
import { getSettings, updateSettings, getHealth } from "@/lib/api";
import { Panel, PanelHeader } from "@/components/common";
import { GearSix, Cpu, ShieldCheck, Microphone, Database, HardDrives, Thermometer, VideoCamera } from "@phosphor-icons/react";
import { toast } from "sonner";

export default function Settings() {
  const [s, setS] = useState(null);
  const [health, setHealth] = useState(null);
  useEffect(() => { getSettings().then(setS); getHealth().then(setHealth); }, []);

  const patch = async (p) => { const u = await updateSettings(p); setS(u); toast.success("Settings saved"); };
  if (!s) return null;
  const m = health?.metrics || {};

  return (
    <div data-testid="settings-page" className="space-y-6 max-w-4xl">
      <div>
        <h1 className="text-4xl sm:text-5xl font-black tracking-tighter">SETTINGS / SYSTEM</h1>
        <p className="micro-label mt-1">PROCESSING · PRIVACY · RETENTION · DEVICE HEALTH</p>
      </div>

      <Panel>
        <PanelHeader title="Processing Mode" icon={Cpu} />
        <div className="p-4 space-y-4">
          <div>
            <div className="micro-label mb-2">Hybrid architecture — local for continuous/private, cloud for reasoning</div>
            <div className="flex gap-2" data-testid="processing-mode">
              {["local", "hybrid", "cloud"].map((p) => (
                <button key={p} data-testid={`proc-${p}`} onClick={() => patch({ processing_mode: p })}
                  className={`px-4 py-2 rounded-sm border font-mono text-xs uppercase ${s.processing_mode === p ? "border-[#007AFF] bg-[#007AFF]/10 text-[#007AFF]" : "border-[#27272A] text-[#71717A] hover:text-white"}`}>{p}</button>
              ))}
            </div>
          </div>
          <Field label="Inference FPS (Pi limit gating)">
            <div className="flex gap-2">
              {[2, 5, 10].map((n) => (
                <button key={n} onClick={() => patch({ inference_fps: n })} data-testid={`fps-${n}`}
                  className={`px-3 py-1.5 rounded-sm border font-mono text-xs ${s.inference_fps === n ? "border-[#007AFF] text-[#007AFF]" : "border-[#27272A] text-[#71717A]"}`}>{n} FPS</button>
              ))}
            </div>
          </Field>
          <Toggle label="Motion gating" desc="Only run inference when motion detected (saves Pi CPU)" checked={s.motion_gating} onChange={(v) => patch({ motion_gating: v })} testid="toggle-motion" />
        </div>
      </Panel>

      <Panel>
        <PanelHeader title="Recognition & Privacy" icon={ShieldCheck} />
        <div className="p-4 space-y-4">
          <Toggle label="Face recognition" desc="Match faces against enrolled people" checked={s.recognition_enabled} onChange={(v) => patch({ recognition_enabled: v })} testid="toggle-recognition" icon={ShieldCheck} />
          <Toggle label="Microphone / voice" desc="Wake-word 'Sentinel' + STT (deferred phase)" checked={s.mic_enabled} onChange={(v) => patch({ mic_enabled: v })} testid="toggle-mic" icon={Microphone} />
          <Toggle label="Cloud upload permission" desc="Allow sending frames/metadata to cloud AI for reasoning" checked={s.privacy_cloud_upload} onChange={(v) => patch({ privacy_cloud_upload: v })} testid="toggle-cloud" />
        </div>
      </Panel>

      <Panel>
        <PanelHeader title="DVR & Retention" icon={Database} />
        <div className="p-4 grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Field label="DVR segment length">
            <div className="flex gap-2">
              {[15, 30, 60].map((n) => (
                <button key={n} onClick={() => patch({ dvr_segment_minutes: n })} data-testid={`set-seg-${n}`}
                  className={`px-3 py-1.5 rounded-sm border font-mono text-xs ${s.dvr_segment_minutes === n ? "border-[#007AFF] text-[#007AFF]" : "border-[#27272A] text-[#71717A]"}`}>{n}m</button>
              ))}
            </div>
          </Field>
          <Field label="Retention (rolling overwrite)">
            <div className="flex gap-2">
              {[24, 48, 72].map((n) => (
                <button key={n} onClick={() => patch({ dvr_retention_hours: n })} data-testid={`set-ret-${n}`}
                  className={`px-3 py-1.5 rounded-sm border font-mono text-xs ${s.dvr_retention_hours === n ? "border-[#007AFF] text-[#007AFF]" : "border-[#27272A] text-[#71717A]"}`}>{n}h</button>
              ))}
            </div>
          </Field>
        </div>
      </Panel>

      <Panel>
        <PanelHeader title="Device & API Health" icon={GearSix} right={<span className={`micro-label ${health?.pi_connected ? "" : "text-[#FF3B30]"}`}>{health?.pi_connected ? "LIVE" : "OFFLINE"}</span>} />
        <div className="p-4 grid grid-cols-2 md:grid-cols-4 gap-3">
          <HealthCard icon={VideoCamera} label="Pi Agent" value={health?.pi_connected ? "LINKED" : "OFFLINE"} good={health?.pi_connected} />
          <HealthCard icon={Cpu} label="CPU" value={m.cpu_percent != null ? `${m.cpu_percent}%` : "—"} />
          <HealthCard icon={Thermometer} label="Temp" value={m.temp_c != null ? `${m.temp_c}°C` : "—"} />
          <HealthCard icon={HardDrives} label="Disk" value={m.disk_percent != null ? `${m.disk_percent}%` : "—"} />
        </div>
        <div className="px-4 pb-4 micro-label">
          {health?.pi_connected
            ? `Live metrics from the Pi at ${health?.pi_base_url}.`
            : `Pi Sentinel stack offline${health?.pi_base_url ? ` at ${health.pi_base_url}` : ""} — no live metrics available.`}
        </div>
      </Panel>
    </div>
  );
}

const Field = ({ label, children }) => (
  <div><div className="micro-label mb-2">{label}</div>{children}</div>
);
const Toggle = ({ label, desc, checked, onChange, testid, icon: Icon }) => (
  <div className="flex items-center justify-between gap-4 border border-[#27272A] rounded-sm p-3">
    <div className="flex items-center gap-3">
      {Icon && <Icon size={18} className="text-[#71717A]" />}
      <div><div className="text-sm text-white">{label}</div><div className="text-xs text-[#71717A]">{desc}</div></div>
    </div>
    <button data-testid={testid} onClick={() => onChange(!checked)} className={`w-11 h-6 rounded-full relative transition-colors shrink-0 ${checked ? "bg-[#007AFF]" : "bg-[#27272A]"}`}>
      <span className={`absolute top-0.5 w-5 h-5 bg-white rounded-full transition-all ${checked ? "left-[22px]" : "left-0.5"}`} />
    </button>
  </div>
);
const HealthCard = ({ icon: Icon, label, value, good }) => (
  <div className="border border-[#27272A] rounded-sm p-3">
    <div className="flex items-center gap-1.5 micro-label"><Icon size={12} /> {label}</div>
    <div className={`font-mono text-lg mt-1 ${good === undefined ? "text-white" : good ? "text-[#34C759]" : "text-[#FF3B30]"}`}>{value}</div>
  </div>
);
