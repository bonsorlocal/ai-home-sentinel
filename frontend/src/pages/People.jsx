import React, { useEffect, useState } from "react";
import { getPeople, createPerson, updatePerson, deletePerson } from "@/lib/api";
import { Panel, PanelHeader, StatusBadge, DemoTag, fmtTime } from "@/components/common";
import { UsersThree, Plus, Trash, PencilSimple, ClockCounterClockwise, Camera } from "@phosphor-icons/react";
import { toast } from "sonner";

const STATUSES = ["trusted", "normal", "watch"];
const empty = { name: "", relationship: "", status: "normal", photo_url: "", notes: "" };

export default function People() {
  const [people, setPeople] = useState([]);
  const [form, setForm] = useState(null);

  const load = () => getPeople().then(setPeople);
  useEffect(() => { load(); }, []);

  const save = async () => {
    if (!form.name.trim()) return toast.error("Name required");
    if (form.id) { await updatePerson(form.id, form); toast.success("Person updated"); }
    else { await createPerson(form); toast.success("Person added — enroll face angles from the Pi backend"); }
    setForm(null); load();
  };
  const remove = async (id) => { await deletePerson(id); toast.success("Removed"); load(); };

  const inputCls = "w-full bg-[#0A0A0A] border border-[#27272A] text-white rounded-sm px-3 py-2 font-mono text-sm focus:outline-none focus:ring-1 focus:ring-[#007AFF]";

  return (
    <div data-testid="people-page" className="space-y-6">
      <div className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-4xl sm:text-5xl font-black tracking-tighter">PEOPLE</h1>
          <p className="micro-label mt-1">ENROLLED FACES · ENCODINGS COME FROM PI / PYTHON BACKEND</p>
        </div>
        <button data-testid="add-person-btn" onClick={() => setForm({ ...empty })} className="flex items-center gap-2 px-4 py-2 rounded-sm bg-[#007AFF] hover:bg-[#0056B3] text-white font-mono text-xs uppercase tracking-wider">
          <Plus size={16} weight="bold" /> Add Person
        </button>
      </div>

      {form && (
        <Panel data-testid="person-form" className="fade-up">
          <PanelHeader title={form.id ? "Edit Person" : "New Person"} icon={PencilSimple} />
          <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-3">
            <input data-testid="person-name" className={inputCls} placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <input className={inputCls} placeholder="Relationship (e.g. Spouse)" value={form.relationship} onChange={(e) => setForm({ ...form, relationship: e.target.value })} />
            <select data-testid="person-status" className={inputCls} value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
              {STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <input className={inputCls} placeholder="Photo URL (optional)" value={form.photo_url} onChange={(e) => setForm({ ...form, photo_url: e.target.value })} />
            <textarea className={`${inputCls} md:col-span-2`} placeholder="Notes" value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          </div>
          <div className="p-4 pt-0 flex gap-2">
            <button data-testid="save-person-btn" onClick={save} className="px-4 py-2 rounded-sm bg-[#007AFF] hover:bg-[#0056B3] text-white font-mono text-xs uppercase">Save</button>
            <button onClick={() => setForm(null)} className="px-4 py-2 rounded-sm border border-[#27272A] hover:border-white text-white font-mono text-xs uppercase">Cancel</button>
          </div>
        </Panel>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {people.map((p) => (
          <Panel key={p.id} data-testid={`person-card-${p.id}`} className="hover:border-[#3F3F46] transition-colors">
            <div className="flex items-start gap-3 p-4">
              <div className="w-16 h-16 rounded-sm border border-[#27272A] bg-[#0A0A0A] overflow-hidden flex items-center justify-center shrink-0">
                {p.photo_url ? <img src={p.photo_url} alt={p.name} className="w-full h-full object-cover" /> : <UsersThree size={24} className="text-[#3F3F46]" />}
              </div>
              <div className="flex-1 min-w-0">
                <div className="font-bold text-white truncate">{p.name}</div>
                <div className="text-xs text-[#71717A]">{p.relationship || "—"}</div>
                <div className="flex items-center gap-1.5 mt-2 flex-wrap">
                  <StatusBadge status={p.status} />{p.is_demo && <DemoTag />}
                </div>
              </div>
            </div>
            <div className="px-4 pb-3 space-y-1.5 text-xs font-mono text-[#A1A1AA]">
              <div className="flex items-center gap-1.5"><Camera size={12} /> {p.encodings_count} face encodings</div>
              <div className="flex items-center gap-1.5"><ClockCounterClockwise size={12} /> {p.recognition_count} recognitions</div>
              <div>Last seen: {p.last_seen ? fmtTime(p.last_seen) : "never"}</div>
            </div>
            <div className="flex border-t border-[#27272A]">
              <button data-testid={`edit-person-${p.id}`} onClick={() => setForm({ id: p.id, name: p.name, relationship: p.relationship || "", status: p.status, photo_url: p.photo_url || "", notes: p.notes || "" })}
                className="flex-1 py-2.5 text-[#71717A] hover:text-white hover:bg-[#1C1C1F] flex items-center justify-center gap-1.5 font-mono text-xs uppercase"><PencilSimple size={14} /> Edit</button>
              <button data-testid={`delete-person-${p.id}`} onClick={() => remove(p.id)}
                className="flex-1 py-2.5 text-[#71717A] hover:text-[#FF3B30] hover:bg-[#FF3B30]/5 border-l border-[#27272A] flex items-center justify-center gap-1.5 font-mono text-xs uppercase"><Trash size={14} /> Remove</button>
            </div>
          </Panel>
        ))}
      </div>
    </div>
  );
}
