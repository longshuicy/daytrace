import { listen } from "@tauri-apps/api/event";
import { useEffect, useMemo, useState } from "react";
import { backup, syncNow, useDay } from "../api";
import {
  GROUP_LABEL, LANES, SOURCE_LABEL, clock, colorVar, longDate, minutes, shiftDate, today,
} from "../format";
import type { Day, Note, PeriodKind } from "../types";
import DatePicker from "./DatePicker";
import DayClock from "./DayClock";
import { NotePanel } from "./NotePanel";
import { Change, MeetingNotes, ProjectsAndSites, commitNote, ShippedCard, Tile, WorkingOnCard, useSort } from "./parts";
import PeriodView, { periodLabel, shiftPeriod } from "./PeriodView";
import Timeline from "./Timeline";
import { Tooltip, useTooltip } from "./Tooltip";
import "./dayview.css";

type Mode = "day" | PeriodKind;
const MODES: [Mode, string][] = [["day", "Day"], ["week", "Week"], ["month", "Month"]];

/** The main window: one day, or the week or month around the chosen date. */
export default function DayView() {
  const [date, setDate] = useState(today());
  const [mode, setMode] = useState<Mode>("day");
  const tooltip = useTooltip();

  useEffect(() => {
    const off = listen<string>("open-date", (e) => { setDate(e.payload); setMode("day"); });
    return () => { off.then((f) => f()); };
  }, []);

  const openDay = (d: string) => { setDate(d); setMode("day"); };

  return (
    <div className="dayview" onScroll={tooltip.hide}>
      <Titlebar mode={mode} setMode={setMode} date={date} setDate={setDate} />
      <main>
        {mode === "day" ? <DayBody date={date} tooltip={tooltip} />
          : <PeriodView kind={mode} date={date} openDay={openDay} />}
      </main>
      <Tooltip tip={tooltip.tip} />
    </div>
  );
}

function Titlebar({ mode, setMode, date, setDate }:
  { mode: Mode; setMode: (m: Mode) => void; date: string; setDate: (d: string) => void }) {
  const [syncing, setSyncing] = useState(false);
  useEffect(() => {
    const offs = [listen("syncing", () => setSyncing(true)), listen("synced", () => setSyncing(false))];
    return () => offs.forEach((p) => p.then((off) => off()));
  }, []);

  const now = today();
  const shift = (n: number) => (mode === "day" ? shiftDate(date, n) : shiftPeriod(mode, date, n));
  // The next day / week / month starts after today: nothing to show yet.
  const atEnd = mode === "day" ? date >= now : shiftPeriod(mode, date, 1) > now;
  const unit = mode === "day" ? "day" : mode;

  return (
    <header className="titlebar" data-tauri-drag-region>
      <div className="nav" data-tauri-drag-region>
        <button className="step" onClick={() => setDate(shift(-1))} aria-label={`Previous ${unit}`}>‹</button>
        <DatePicker date={date} onChange={setDate} label={mode === "day" ? undefined : periodLabel(mode, date)}
          initialMode={mode === "month" ? "month" : "day"} />
        <button className="step" onClick={() => setDate(shift(1))} disabled={atEnd} aria-label={`Next ${unit}`}>›</button>
        {(mode === "day" ? date !== now : !atEnd) && (
          <button className="plain back" onClick={() => setDate(now)}>Back to {mode === "day" ? "today" : `this ${mode}`}</button>
        )}
      </div>
      <div className="nav">
        <div className="segmented" role="tablist" aria-label="Period">
          {MODES.map(([key, text]) => (
            <button key={key} role="tab" aria-selected={mode === key} className={mode === key ? "on" : undefined}
              onClick={() => setMode(key)}>{text}</button>
          ))}
        </div>
        <button className="sync" onClick={() => syncNow()} disabled={syncing}>{syncing ? "Syncing…" : "Sync now"}</button>
      </div>
    </header>
  );
}

function DayBody({ date, tooltip }: { date: string; tooltip: ReturnType<typeof useTooltip> }) {
  const { day, error } = useDay(date);
  const [fullDay, setFullDay] = useState(false);
  const [view, setView] = useState<View>("timeline");
  return (
    <>
      <header className="day-head">
        <h1>{longDate(date)}</h1>
        {day && !day.before_tracking && <p className="line">{day.line}</p>}
      </header>
      {error && <div className="notice error">Couldn't load this day: {error}</div>}
      {day && <SyncAlert day={day} />}
      {day && day.storage.bytes > day.storage.limit && <StorageAlert storage={day.storage} />}
      {day && <Body day={day} fullDay={fullDay} setFullDay={setFullDay} view={view} setView={setView} tooltip={tooltip} />}
    </>
  );
}

type View = "timeline" | "clock" | "table";
const VIEWS: [View, string][] = [["timeline", "Timeline"], ["clock", "Day clock"], ["table", "Table"]];

interface BodyProps {
  day: Day;
  fullDay: boolean;
  setFullDay: (v: boolean) => void;
  view: View;
  setView: (v: View) => void;
  tooltip: ReturnType<typeof useTooltip>;
}

function Body({ day, fullDay, setFullDay, view, setView, tooltip }: BodyProps) {
  const s = day.summary;
  const h = day.highlights;
  const [openNote, setOpenNote] = useState<string | null>(null);
  const notes = useMemo(() => [  // in time order, so the panel can step through them
    ...day.blocks.flatMap((b) => (b.notes ?? []).map((n) => ({ at: b.start, n }))),
    ...day.points.flatMap((p) => p.id ? [{ at: p.at, n: { id: p.id, title: p.title ?? "", url: p.url, path: "" } as Note }] : []),
  ].sort((a, b) => +new Date(a.at) - +new Date(b.at)).map((x) => x.n), [day]);
  if (day.before_tracking) {
    return <div className="notice">Tracking started {new Date(day.day_zero).toLocaleString()}. Nothing is recorded before that.</div>;
  }
  return (
    <>
      <section className="g5 day-tiles">
        <Tile label="Active" value={minutes(s.active_min)}
          note={s.first ? `${clock(s.first)}–${clock(s.last!)}${s.groups.other ? ` · incl. ${minutes(s.groups.other)} other` : ""}`
            : "no activity yet"} />
        <Tile label="Meetings" value={minutes(s.counts.meeting_min)}
          note={s.counts.meetings ? `${s.counts.meetings} meeting${s.counts.meetings === 1 ? "" : "s"}` : "none on your calendar"}>
          {notes.length > 0 && <MeetingNotes notes={notes} open={setOpenNote} />}
        </Tile>
        <Tile label="Email sent" value={String(s.counts.emails_sent)} note={`${s.counts.emails_received} received`} />
        {h.focus && h.focus.minutes >= 10
          ? <Tile label="Longest focus" value={minutes(h.focus.minutes)}
            note={`${h.focus.label} · ${clock(h.focus.start)}–${clock(h.focus.end)}`} />
          : <Tile label="Longest focus" value="—" note="no stretch of 10 minutes yet" />}
        {h.commits
          ? <Tile label="Total commits" value={<Change {...h.commits} />} note={commitNote(h.commits)}
            extra={h.commits.projects.join(", ")} />
          : <Tile label="Total commits" value="—" note="no commits yet" />}
      </section>

      <section className="card activity" aria-label="Activity">
        <div className="card-head">
          <div className="segmented" role="tablist" aria-label="Activity view">
            {VIEWS.map(([key, label]) => (
              <button key={key} role="tab" aria-selected={view === key} className={view === key ? "on" : undefined}
                onClick={() => setView(key)}>{label}</button>
            ))}
          </div>
          {view === "timeline" && (
            <div className="toggles">
              <label><input type="checkbox" checked={fullDay} onChange={(e) => setFullDay(e.target.checked)} /> Full 24 hours</label>
            </div>
          )}
        </div>
        {view === "timeline" && <Timeline day={day} fullDay={fullDay} tooltip={tooltip} setOpenNote={setOpenNote} />}
        {view === "clock" && (
          <div className="clock-view">
            <DayClock day={day} tooltip={tooltip} />
            <ul className="legend">
              {LANES.filter((g) => s.groups[g]).map((g) => (
                <li key={g}>
                  <span className="swatch" style={{ background: colorVar(g) }} />
                  {GROUP_LABEL[g]} <span className="muted tabular">{minutes(s.groups[g] ?? 0)}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {view === "table" && <EventTable day={day} />}
      </section>

      {(h.shipped.length > 0 || h.working_on.length > 0) && (
        <section className="g2">
          {h.shipped.length > 0 && <ShippedCard items={h.shipped} />}
          {h.working_on.length > 0 && <WorkingOnCard label="Working on" items={h.working_on} />}
        </section>
      )}

      <ProjectsAndSites projects={s.projects} sites={day.sites} />
      {openNote && <NotePanel id={openNote} ids={notes.map((n) => n.id)} onOpen={setOpenNote}
        onClose={() => setOpenNote(null)} />}
    </>
  );
}

/** Shown only when a configured source failed its last sync. */
function SyncAlert({ day }: { day: Day }) {
  const failing = day.sources.filter((src) => src.configured !== false && !src.ok);
  if (!failing.length) return null;
  return (
    <div className="notice error">
      Sync failed for {failing.map((src) => SOURCE_LABEL[src.name] ?? src.name).join(", ")}, so this day may be
      incomplete.
      {failing.map((src) => src.error && (
        <div key={src.name} className="muted error-detail">{SOURCE_LABEL[src.name] ?? src.name}: {src.error}</div>
      ))}
    </div>
  );
}

const mb = (bytes: number) => `${Math.round(bytes / 1024 / 1024)} MB`;

/** Past the size limit: back up, then keep only today. Asks twice, since it deletes earlier days. */
function StorageAlert({ storage }: { storage: Day["storage"] }) {
  const [state, setState] = useState<"idle" | "confirm" | "busy">("idle");
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null);
  const run = () => {
    setState("busy");
    backup(true).then(
      (text) => { setResult({ ok: true, text }); setState("idle"); },
      (e) => { setResult({ ok: false, text: String(e) }); setState("idle"); },
    );
  };
  return (
    <div className="notice">
      daytrace's database is {mb(storage.bytes)}, over {mb(storage.limit)}. Back it up and start over: the whole
      database is copied to ~/.daytrace/backups, then everything before today is deleted here.
      <div className="actions">
        {state === "confirm"
          ? <>
            <button className="primary" onClick={run}>Yes, back up and delete earlier days</button>
            <button className="quiet" onClick={() => setState("idle")}>Cancel</button>
          </>
          : <button className="primary" onClick={() => setState("confirm")} disabled={state === "busy"}>
            {state === "busy" ? "Backing up…" : "Back up and start over"}
          </button>}
      </div>
      {result && <div className={`error-detail${result.ok ? " muted" : ""}`}>{result.text}</div>}
    </div>
  );
}

type EventKey = "at" | "group" | "what" | "project" | "source" | "minutes";

function EventTable({ day }: { day: Day }) {
  const { header, sorted } = useSort<EventKey>("at");
  const rows = useMemo(() => [
    ...day.blocks.map((b) => ({ at: b.start, end: b.end, group: b.group, what: b.titles[0] ?? b.category,
      project: b.project, source: SOURCE_LABEL[b.source] ?? b.source, minutes: b.minutes })),
    ...day.points.map((p) => ({ at: p.at, end: null, group: p.group, what: p.title ?? p.category,
      project: p.project, source: SOURCE_LABEL[p.source] ?? p.source, minutes: 0 })),
  ], [day]);
  return (
    <div className="table-wrap">
      <table className="events">
        <thead>
          <tr>
            {header("at", "Time")}{header("group", "Activity")}{header("what", "What")}
            {header("project", "Project")}{header("source", "Source")}{header("minutes", "Length", "num", -1)}
          </tr>
        </thead>
        <tbody>
          {sorted(rows, (r, k) => k === "minutes" ? r.minutes
            : k === "group" ? GROUP_LABEL[r.group] : (r[k] ?? "").toLowerCase()).map((r, i) => (
            <tr key={i}>
              <td className="tabular">{clock(r.at)}{r.end ? `–${clock(r.end)}` : ""}</td>
              <td><span className="swatch" style={{ background: colorVar(r.group) }} /> {GROUP_LABEL[r.group]}</td>
              <td className="what">{r.what}</td>
              <td>{r.project ?? ""}</td>
              <td>{r.source}</td>
              <td className="num tabular">{r.minutes ? minutes(r.minutes) : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
