import React from "react";
import { NavLink } from "react-router-dom";
import {
  SquaresFour, Broadcast, ListMagnifyingGlass, FilmSlate, UsersThree,
  Robot, ShieldCheck, GearSix, ShieldStar,
} from "@phosphor-icons/react";

const NAV = [
  { to: "/", label: "Dashboard", icon: SquaresFour, testid: "nav-dashboard", end: true },
  { to: "/live", label: "Live Events", icon: Broadcast, testid: "nav-live" },
  { to: "/events", label: "Event Log", icon: ListMagnifyingGlass, testid: "nav-events" },
  { to: "/dvr", label: "DVR", icon: FilmSlate, testid: "nav-dvr" },
  { to: "/people", label: "People", icon: UsersThree, testid: "nav-people" },
  { to: "/sentinel", label: "Sentinel AI", icon: Robot, testid: "nav-sentinel" },
  { to: "/rules", label: "Rules", icon: ShieldCheck, testid: "nav-rules" },
  { to: "/settings", label: "Settings", icon: GearSix, testid: "nav-settings" },
];

export default function Layout({ children }) {
  return (
    <div className="min-h-screen flex bg-[#0A0A0A]">
      <aside className="w-[224px] shrink-0 border-r border-[#27272A] bg-[#0A0A0A] hidden md:flex flex-col sticky top-0 h-screen">
        <div className="px-5 py-5 border-b border-[#27272A] flex items-center gap-2">
          <ShieldStar size={24} weight="fill" className="text-[#007AFF]" />
          <div>
            <div className="font-black text-lg tracking-tighter leading-none">SENTINEL</div>
            <div className="micro-label mt-0.5">HOME AWARENESS</div>
          </div>
        </div>
        <nav className="flex-1 py-3">
          {NAV.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.end}
              data-testid={n.testid}
              className={({ isActive }) =>
                `flex items-center gap-3 px-5 py-2.5 text-sm font-mono uppercase tracking-wider transition-colors border-l-2 ${
                  isActive
                    ? "border-[#007AFF] bg-[#141416] text-white"
                    : "border-transparent text-[#71717A] hover:text-white hover:bg-[#141416]"
                }`
              }
            >
              <n.icon size={18} weight="bold" />
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="px-5 py-4 border-t border-[#27272A] micro-label">MVP · v0.1</div>
      </aside>

      <div className="flex-1 min-w-0">
        {/* mobile top nav */}
        <div className="md:hidden flex items-center gap-2 overflow-x-auto border-b border-[#27272A] bg-[#0A0A0A] px-3 py-2 sticky top-0 z-20">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} data-testid={`m-${n.testid}`}
              className={({ isActive }) => `whitespace-nowrap px-2 py-1 text-xs font-mono uppercase ${isActive ? "text-[#007AFF]" : "text-[#71717A]"}`}>
              {n.label}
            </NavLink>
          ))}
        </div>
        <main className="p-4 sm:p-6 lg:p-8 fade-up">{children}</main>
      </div>
    </div>
  );
}
