import React from "react";

export const Panel = ({ children, className = "", ...rest }) => (
  <div className={`bg-[#141416] border border-[#27272A] rounded-sm ${className}`} {...rest}>
    {children}
  </div>
);

export const PanelHeader = ({ title, right, icon: Icon }) => (
  <div className="flex items-center justify-between px-4 py-3 border-b border-[#27272A]">
    <div className="flex items-center gap-2">
      {Icon && <Icon size={16} className="text-[#71717A]" weight="bold" />}
      <span className="micro-label" style={{ color: "#A1A1AA" }}>{title}</span>
    </div>
    {right}
  </div>
);

const badgeBase = "inline-flex items-center gap-1 rounded-none px-2 py-0.5 font-mono text-[10px] uppercase tracking-wider border";

export const StatusBadge = ({ status }) => {
  const map = {
    trusted: "bg-green-500/10 text-[#34C759] border-green-500/20",
    normal: "bg-blue-500/10 text-[#007AFF] border-blue-500/20",
    watch: "bg-orange-500/10 text-[#FF9500] border-orange-500/20",
  };
  return <span data-testid={`status-badge-${status}`} className={`${badgeBase} ${map[status] || map.normal}`}>{status}</span>;
};

export const ImportanceBadge = ({ level }) => {
  const map = {
    low: "bg-zinc-500/10 text-[#A1A1AA] border-zinc-500/20",
    medium: "bg-blue-500/10 text-[#007AFF] border-blue-500/20",
    high: "bg-orange-500/10 text-[#FF9500] border-orange-500/20",
    critical: "bg-red-500/10 text-[#FF3B30] border-red-500/20",
  };
  return <span className={`${badgeBase} ${map[level] || map.low}`}>{level}</span>;
};

export const DemoTag = () => (
  <span data-testid="demo-tag" className={`${badgeBase} bg-purple-500/10 text-[#C084FC] border-purple-500/25`} title="Demo / mock data — will be replaced by real Pi data">
    demo
  </span>
);

export const LiveTag = () => (
  <span className={`${badgeBase} bg-red-500/10 text-[#FF3B30] border-red-500/20`}>
    <span className="w-1.5 h-1.5 rounded-full bg-[#FF3B30] live-dot" /> live
  </span>
);

export const TypePill = ({ type }) => {
  const label = { unknown_person: "UNKNOWN", person: "PERSON", object: "OBJECT", motion: "MOTION" }[type] || type;
  const cls = type === "unknown_person" ? "text-[#FF9500] border-orange-500/25 bg-orange-500/10"
    : type === "person" ? "text-[#34C759] border-green-500/20 bg-green-500/10"
    : "text-[#A1A1AA] border-zinc-500/20 bg-zinc-500/10";
  return <span className={`${badgeBase} ${cls}`}>{label}</span>;
};

export const fmtTime = (iso) => {
  try { return new Date(iso).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }); }
  catch { return iso; }
};
export const fmtTimeShort = (iso) => {
  try { return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }
  catch { return iso; }
};
