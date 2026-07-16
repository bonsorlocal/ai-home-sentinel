import React, { useEffect, useState } from "react";
import { getRules, createRule, updateRule, deleteRule } from "@/lib/api";
import { Panel, PanelHeader, ImportanceBadge, DemoTag, fmtTime } from "@/components/common";
import { ShieldCheck, Plus, Trash, Lightning, Brain } from "@phosphor-icons/react";
import { toast } from "sonner";

const TRIGGERS = [
  { v: "unknown_after_hours", l: "Unknown person after hours" },
  { v: "package_arrival", l: "Package arrival" },
  { v: "loitering", l: "Loitering (person > 60s)" },
  { v: "scheduled_expectation", l: "Scheduled expectation" },
  { v: "mode_violation", l: "Mode violation" },
];
const SEV = ["low", "medium", "high", "critical"];
const empty = { name: "", description: "", trigger_type: "unknown_after_hours", severity: "medium", schedule: "", active: true };

export default function Rules() {
  const [rules, setRules] = useState([]);
  const [form, setForm] = useState(null);
  const load = () => getRules().then(setRules);
  useEffect(() => { load(); }, []);

  const toggle = async (r) => { const u = await updateRule(r.id, { active: !r.active }); setRules((p) => p.map((x) => x.id === r.id ? u : x)); };
  const save = async () => {
    if (!form.name.trim()) return toast.error("Name required");
    await createRule(form); toast.success("Rule created"); setForm(null); load();
  };
  const remove = async (id) => { await deleteRule(id); toast.success("Rule deleted"); load(); };

  const inputCls = "w-full bg-[#0A0A0A] border border-[#27272A] text-white rounded-sm px-3 py-2 font-mono text-sm focus:outline-none focus:ring-1 focus:ring-[#007AFF]";

  return (
    <div data-testid="rules-page" className="space-y-6">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-4xl sm:text-5xl font-black tracking-tighter">RULES / PATTERNS</h1>
          <p className="micro-label mt-1">DETECTION RULES + ROUTINE / ANOMALY ARCHITECTURE</p>
        </div>
        <button data-testid="add-rule-btn" onClick={() => setForm({ ...empty })} className="flex items-center gap-2 px-4 py-2 rounded-sm bg-[#007AFF] hover:bg-[#0056B3] text-white font-mono text-xs uppercase tracking-wider">
          <Plus size={16} weight="bold" /> Add Rule
        </button>
      </div>

      {/* Architecture note — honest about unfinished learning */}
      <Panel className="border-[#007AFF]/30 bg-[#007AFF]/5">
        <div className="flex items-start gap-3 p-4">
          <Brain size={22} className="text-[#007AFF] shrink-0 mt-0.5" />
          <div className="text-sm text-[#A1A1AA]">
            <span className="text-white font-semibold">Routine & anomaly learning is scaffolded, not trained yet.</span> Rules below are explicit, deterministic triggers evaluated by the Pi agent. Learned baselines (e.g. "UPS usually 3–5 PM") are stored as <span className="font-mono text-[#007AFF]">scheduled_expectation</span> rules — the statistical learning engine is a future phase and is <span className="font-mono">not faked</span>.
          </div>
        </div>
      </Panel>

      {form && (
        <Panel data-testid="rule-form" className="fade-up">
          <PanelHeader title="New Rule" icon={ShieldCheck} />
          <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-3">
            <input data-testid="rule-name" className={inputCls} placeholder="Rule name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <select className={inputCls} value={form.trigger_type} onChange={(e) => setForm({ ...form, trigger_type: e.target.value })}>
              {TRIGGERS.map((t) => <option key={t.v} value={t.v}>{t.l}</option>)}
            </select>
            <select className={inputCls} value={form.severity} onChange={(e) => setForm({ ...form, severity: e.target.value })}>
              {SEV.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <input className={inputCls} placeholder="Schedule e.g. 23:00-06:00 (optional)" value={form.schedule} onChange={(e) => setForm({ ...form, schedule: e.target.value })} />
            <textarea className={`${inputCls} md:col-span-2`} placeholder="Description" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
          </div>
          <div className="p-4 pt-0 flex gap-2">
            <button data-testid="save-rule-btn" onClick={save} className="px-4 py-2 rounded-sm bg-[#007AFF] hover:bg-[#0056B3] text-white font-mono text-xs uppercase">Save</button>
            <button onClick={() => setForm(null)} className="px-4 py-2 rounded-sm border border-[#27272A] hover:border-white text-white font-mono text-xs uppercase">Cancel</button>
          </div>
        </Panel>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {rules.map((r) => (
          <Panel key={r.id} data-testid={`rule-card-${r.id}`} className="hover:border-[#3F3F46] transition-colors">
            <div className="p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="flex items-center gap-2 flex-wrap">
                  <Lightning size={16} weight="fill" className={r.active ? "text-[#007AFF]" : "text-[#3F3F46]"} />
                  <span className="font-bold text-white">{r.name}</span>
                  <ImportanceBadge level={r.severity} />
                  {r.is_demo && <DemoTag />}
                </div>
                <button data-testid={`toggle-rule-${r.id}`} onClick={() => toggle(r)}
                  className={`w-11 h-6 rounded-full relative transition-colors shrink-0 ${r.active ? "bg-[#007AFF]" : "bg-[#27272A]"}`}>
                  <span className={`absolute top-0.5 w-5 h-5 bg-white rounded-full transition-all ${r.active ? "left-[22px]" : "left-0.5"}`} />
                </button>
              </div>
              <p className="text-sm text-[#A1A1AA] mt-2">{r.description}</p>
              <div className="flex items-center gap-3 mt-3 font-mono text-[10px] text-[#71717A]">
                <span className="uppercase">{r.trigger_type.replace(/_/g, " ")}</span>
                {r.schedule && <span>· {r.schedule}</span>}
                <span>· triggered {r.triggered_count}×</span>
                {r.last_triggered && <span>· last {fmtTime(r.last_triggered)}</span>}
              </div>
            </div>
            <div className="border-t border-[#27272A]">
              <button data-testid={`delete-rule-${r.id}`} onClick={() => remove(r.id)} className="w-full py-2 text-[#71717A] hover:text-[#FF3B30] hover:bg-[#FF3B30]/5 flex items-center justify-center gap-1.5 font-mono text-xs uppercase"><Trash size={14} /> Delete</button>
            </div>
          </Panel>
        ))}
      </div>
    </div>
  );
}
