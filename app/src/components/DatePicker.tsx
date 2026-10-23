import { useEffect, useRef, useState } from "react";
import { today } from "../format";

type Mode = "day" | "month" | "year";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
  "November", "December"];
const DOWS = ["Su", "Mo", "Tu", "We", "Th", "Fr", "Sa"];

const iso = (d: Date) => d.toLocaleDateString("en-CA");
const parse = (s: string) => new Date(`${s}T12:00:00`);

/** The date button in the title bar and its popover: a month grid, or months / years to jump further. No future days. */
export default function DatePicker({ date, onChange, label: fixedLabel, initialMode = "day" }:
  { date: string; onChange: (date: string) => void; label?: string; initialMode?: "day" | "month" }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<Mode>("day");
  const sel = parse(date);
  const now = parse(today());
  const [cur, setCur] = useState({ y: sel.getFullYear(), m: sel.getMonth() });
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const toggle = () => {
    setCur({ y: sel.getFullYear(), m: sel.getMonth() });
    setMode(initialMode);
    setOpen(!open);
  };
  const pick = (d: Date) => {
    if (d > now) return;
    onChange(iso(d));
    setOpen(false);
  };
  const ym = (y: number, m: number) => y * 12 + m;
  const nowYm = ym(now.getFullYear(), now.getMonth());
  const isToday = date === today();
  const label = fixedLabel ?? (isToday ? "Today"
    : sel.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric",
      year: sel.getFullYear() !== now.getFullYear() ? "numeric" : undefined }));

  let title: string, prev: () => void, next: () => void, nextOff: boolean;
  const y0 = Math.floor(cur.y / 12) * 12;
  if (mode === "day") {
    title = `${MONTHS[cur.m]} ${cur.y}`;
    nextOff = ym(cur.y, cur.m) >= nowYm;
    const shift = (n: number) => { const d = new Date(cur.y, cur.m + n, 1); setCur({ y: d.getFullYear(), m: d.getMonth() }); };
    prev = () => shift(-1);
    next = () => shift(1);
  } else if (mode === "month") {
    title = String(cur.y);
    nextOff = cur.y >= now.getFullYear();
    prev = () => setCur({ ...cur, y: cur.y - 1 });
    next = () => setCur({ ...cur, y: cur.y + 1 });
  } else {
    title = `${y0}–${y0 + 11}`;
    nextOff = y0 + 12 > now.getFullYear();
    prev = () => setCur({ ...cur, y: cur.y - 12 });
    next = () => setCur({ ...cur, y: cur.y + 12 });
  }

  const first = new Date(cur.y, cur.m, 1);
  const days = Array.from({ length: 42 }, (_, i) => new Date(cur.y, cur.m, 1 - first.getDay() + i));

  return (
    <div className="datepicker" ref={ref} onKeyDown={(e) => e.key === "Escape" && setOpen(false)}>
      <button className={`date-button${open ? " open" : ""}`} aria-haspopup="dialog" aria-expanded={open} onClick={toggle}>
        <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
          strokeLinecap="round" aria-hidden="true"><rect x="2" y="3" width="12" height="11" rx="2" /><path d="M2 6.5h12M5.5 1.5v3M10.5 1.5v3" /></svg>
        <span>{label}</span>
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.5"
          strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M2 4l3 3 3-3" /></svg>
      </button>
      {open && (
        <div className="picker" role="dialog" aria-label="Choose a date">
          <div className="segmented full" role="tablist" aria-label="Jump by">
            {(["day", "month", "year"] as Mode[]).map((m) => (
              <button key={m} role="tab" aria-selected={mode === m} className={mode === m ? "on" : undefined}
                onClick={() => setMode(m)}>{m[0].toUpperCase() + m.slice(1)}</button>
            ))}
          </div>
          <div className="picker-head">
            <button className="picker-step" aria-label="Previous" onClick={prev}>‹</button>
            <button className="picker-title" onClick={() => setMode(mode === "day" ? "month" : "year")}>{title}</button>
            <button className="picker-step" aria-label="Next" disabled={nextOff} onClick={next}>›</button>
          </div>
          {mode === "day" && (
            <div className="picker-days">
              {DOWS.map((w) => <div key={w} className="picker-dow">{w}</div>)}
              {days.map((d) => {
                const key = iso(d);
                const cls = [key === date && "selected", key === today() && "today", d.getMonth() !== cur.m && "outside"]
                  .filter(Boolean).join(" ");
                return (
                  <button key={key} className={cls || undefined} disabled={d > now} onClick={() => pick(d)}
                    aria-label={d.toLocaleDateString([], { dateStyle: "full" })}
                    aria-current={key === today() ? "date" : undefined}>{d.getDate()}</button>
                );
              })}
            </div>
          )}
          {mode === "month" && (
            <div className="picker-tiles">
              {MONTHS.map((name, m) => (
                <button key={m} disabled={ym(cur.y, m) > nowYm}
                  className={cur.y === sel.getFullYear() && m === sel.getMonth() ? "selected" : undefined}
                  onClick={() => {
                    if (initialMode === "month") pick(new Date(Math.min(+new Date(cur.y, m, 1), +now)));
                    else { setCur({ y: cur.y, m }); setMode("day"); }
                  }}>{name.slice(0, 3)}</button>
              ))}
            </div>
          )}
          {mode === "year" && (
            <div className="picker-tiles">
              {Array.from({ length: 12 }, (_, i) => y0 + i).map((y) => (
                <button key={y} disabled={y > now.getFullYear()} className={y === sel.getFullYear() ? "selected" : undefined}
                  onClick={() => { setCur({ y, m: Math.min(cur.m, y === now.getFullYear() ? now.getMonth() : 11) }); setMode("month"); }}>
                  {y}
                </button>
              ))}
            </div>
          )}
          <div className="picker-foot">
            <div className="picker-quick">
              <button className="quiet" onClick={() => pick(now)}>Today</button>
              <button className="quiet" onClick={() => pick(new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1))}>Yesterday</button>
            </div>
            <button className="plain" onClick={() => setOpen(false)}>Close</button>
          </div>
        </div>
      )}
    </div>
  );
}
