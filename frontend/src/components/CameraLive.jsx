import React, { useEffect, useState } from "react";
import { streamUrl, getDiagnostics } from "@/lib/api";
import { Panel, PanelHeader, LiveTag } from "@/components/common";
import { VideoCamera, ArrowClockwise } from "@phosphor-icons/react";

const Dot = ({ ok }) => <span className={`w-2 h-2 rounded-full inline-block ${ok ? "bg-[#34C759]" : "bg-[#FF3B30]"}`} />;

const Check = ({ label, ok }) => (
  <div className="flex items-center justify-between gap-2 border border-[#27272A] rounded-sm px-3 py-2">
    <span className="micro-label">{label}</span>
    <span className="flex items-center gap-1.5 font-mono text-[10px]" style={{ color: ok ? "#34C759" : "#FF3B30" }}>
      <Dot ok={ok} />{ok ? "OK" : "FAIL"}
    </span>
  </div>
);

export const CameraLive = ({ camera }) => {
  const [diag, setDiag] = useState(null);
  const [imgKey, setImgKey] = useState(Date.now());
  const [imgError, setImgError] = useState(false);

  const loadDiag = () => getDiagnostics().then(setDiag).catch(() => setDiag(null));
  useEffect(() => { loadDiag(); const t = setInterval(loadDiag, 15000); return () => clearInterval(t); }, []);

  const reconnect = () => { setImgError(false); setImgKey(Date.now()); loadDiag(); };
  const online = diag?.stream_endpoint_reachable;

  return (
    <Panel data-testid="camera-live-panel">
      <PanelHeader title="Live Camera Feed" icon={VideoCamera}
        right={<div className="flex items-center gap-2">{online && <LiveTag />}
          <button data-testid="camera-reconnect" onClick={reconnect} className="p-1 rounded-sm border border-[#27272A] text-[#71717A] hover:text-white"><ArrowClockwise size={14} /></button></div>} />
      <div className="p-4 grid grid-cols-1 lg:grid-cols-[1fr_260px] gap-4">
        <div className="relative aspect-video bg-[#0A0A0A] rounded-sm overflow-hidden border border-[#27272A] scanline">
          {!imgError ? (
            <img key={imgKey} data-testid="camera-stream-img" src={streamUrl(camera)} alt="Live Pi camera"
              className="w-full h-full object-contain" onError={() => setImgError(true)} />
          ) : (
            <div data-testid="camera-stream-unavailable" className="w-full h-full flex flex-col items-center justify-center gap-2 text-center px-6">
              <VideoCamera size={32} className="text-[#FF3B30]" />
              <div className="text-[#FF3B30] font-bold">CAMERA FEED UNAVAILABLE</div>
              <div className="text-xs text-[#71717A]">Backend could not reach the Pi camera pipeline{diag?.pi_base_url ? ` at ${diag.pi_base_url}` : ""}. Check the diagnostics panel.</div>
            </div>
          )}
        </div>
        <div className="space-y-2" data-testid="camera-diagnostics">
          <div className="micro-label mb-1">PIPELINE DIAGNOSTICS</div>
          <Check label="Camera detected" ok={diag?.camera_detected} />
          <Check label="Camera service" ok={diag?.camera_service_running} />
          <Check label="Stream reachable" ok={diag?.stream_endpoint_reachable} />
          <Check label="Frame received" ok={diag?.frame_received} />
          <Check label="Status API" ok={diag?.status_endpoint_reachable} />
          <div className="micro-label pt-1 break-all">FEED: {diag?.video_url || "not found"}</div>
          {diag?.errors?.length > 0 && (
            <div className="text-[10px] text-[#FF9500] font-mono border-l-2 border-[#FF9500]/50 pl-2 space-y-1">
              {diag.errors.map((e, i) => <div key={i}>{e}</div>)}
            </div>
          )}
        </div>
      </div>
    </Panel>
  );
};
