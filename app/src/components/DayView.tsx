import { listen } from "@tauri-apps/api/event";
import { openUrl } from "@tauri-apps/plugin-opener";
import { useEffect, useMemo, useState } from "react";
import { syncNow, useDay } from "../api";
import {
  GROUP_LABEL, LANES, SOURCE_LABEL, clock, colorVar, longDate, minutes, shiftDate, today,
} from "../format";
import type { Day } from "../types";
import DayClock from "./DayClock";
import Story from "./Story";
import Timeline from "./Timeline";
import { Tooltip, useTooltip } from "./Tooltip";
import "./dayview.css";

export default function DayView() {
  const [date, setDate] = useState(today());
  const { day, error, syncing } = useDay(date);
  const [fullDay, setFullDay] = useState(false);
  const [view, setView] = useState<View>("timeline");
  const tooltip = useTooltip();

  useEffect(() => {
    const off = listen<string>("open-date", (e) => setDate(e.payload));
    return () => { off.then((f) => f()); };
  }, []);

  const isToday = date === today();

  return (
    <div className="dayview" onScroll={tooltip.hide}>
      <header className="titlebar" data-tauri-drag-region>
        <div className="nav" data-tauri-drag-region>
          <button onClick={() => setDate(shiftDate(date, -1))} aria-label="Previous day">‹</button>
          <button onClick={() => setDate(today())} disabled={isToday}>Today</button>
          <button onClick={() => setDate(shiftDate(date, 1))} disabled={isToday} aria-label="Next day">›</button>
        </div>
        <button className="sync" onClick={() => syncNow()} disabled={syncing}>
          {syncing ? "Syncing…" : "Sync now"}
        </button>
      </header>

      <main>
        <h1>{longDate(date)}</h1>
        {error && <div className="notice error">Couldn't load this day: {error}</div>}
        {day && <SyncAlert day={day} />}
        {day && <Body day={day} fullDay={fullDay} setFullDay={setFullDay} view={view} setView={setView}
          tooltip={tooltip} />}
      </main>
      <Tooltip tip={tooltip.tip} />
    </div>
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
  if (day.before_tracking) {
    return <div className="notice">Tracking started {new Date(day.day_zero).toLocaleString()}. Nothing is recorded before that.</div>;
  }
  return (
    <>
      <p className="line">{day.line}</p>

      <Story day={day} />

      <section className="overview">
        <Tile label="Active" value={minutes(s.active_min)} hero
          note={s.first ? `${clock(s.first)}–${clock(s.last!)}${s.groups.other ? ` · incl. ${minutes(s.groups.other)} other` : ""}`
            : "no activity yet"} />
        <Tile label="Meetings" value={minutes(s.counts.meeting_min)}
          note={s.counts.meetings ? `${s.counts.meetings} meeting${s.counts.meetings === 1 ? "" : "s"}` : "none on your calendar"} />
        <Tile label="Email sent" value={String(s.counts.emails_sent)} note={`${s.counts.emails_received} received`} />
      </section>
      <Highlights day={day} />

      <section className="card">
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
        {view === "timeline" && <Timeline day={day} fullDay={fullDay} tooltip={tooltip} />}
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

      <section className="card">
        <h2>Projects</h2>
        <Projects day={day} />
      </section>
      <section className="card">
        <h2>Top sites</h2>
        <Sites day={day} />
      </section>
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

type Dir = 1 | -1;

/** Click a header to sort by it; click again to reverse. */
function useSort<K extends string>(initial: K, initialDir: Dir = 1) {
  const [sort, setSort] = useState<{ key: K; dir: Dir }>({ key: initial, dir: initialDir });
  const header = (key: K, label: string, className?: string, firstDir: Dir = 1) => (
    <th className={className} aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      <button className="sort" onClick={() => setSort(sort.key === key ? { key, dir: -sort.dir as Dir } : { key, dir: firstDir })}>
        {label}<span className="sort-mark">{sort.key === key ? (sort.dir === 1 ? "▲" : "▼") : ""}</span>
      </button>
    </th>
  );
  const sorted = <T,>(rows: T[], value: (row: T, key: K) => string | number) =>
    [...rows].sort((a, b) => {
      const x = value(a, sort.key), y = value(b, sort.key);
      return (x < y ? -1 : x > y ? 1 : 0) * sort.dir;
    });
  return { header, sorted };
}

const SHOWN = 3;

/** One column per highlight, each a short list; long lists end in "+N more". */
function Highlights({ day }: { day: Day }) {
  const h = day.highlights;
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;
  const columns = [
    h.shipped.length > 0 && (
      <HighlightColumn key="shipped" label="Shipped" more={h.shipped.length - SHOWN}>
        {h.shipped.slice(0, SHOWN).map((x, i) => (
          <li key={i} title={x.title}>
            {x.url ? <button className="link hl-main" onClick={() => openUrl(x.url!)}>{x.title}</button>
              : <span className="hl-main">{x.title}</span>}
            {x.project && <span className="hl-sub">{x.project}</span>}
          </li>
        ))}
      </HighlightColumn>
    ),
    h.biggest && (
      <HighlightColumn key="biggest" label="Biggest change">
        <li title={h.biggest.title}>
          <span className="hl-main tabular">+{h.biggest.insertions} −{h.biggest.deletions} · {plural(h.biggest.files, "file")}</span>
          <span className="hl-sub">{[h.biggest.project, h.biggest.title].filter(Boolean).join(" · ")}</span>
        </li>
      </HighlightColumn>
    ),
    h.working_on.length > 0 && (
      <HighlightColumn key="working" label="Working on" more={h.working_on.length - SHOWN}>
        {h.working_on.slice(0, SHOWN).map((w) => (
          <li key={`${w.project}/${w.branch}`} title={w.branch}>
            <code className="hl-main branch">{w.branch}</code>
            <span className="hl-sub tabular">{[w.project, minutes(w.minutes)].filter(Boolean).join(" · ")}</span>
          </li>
        ))}
      </HighlightColumn>
    ),
    h.focus && h.focus.minutes >= 10 && (
      <HighlightColumn key="focus" label="Longest focus">
        <li title={h.focus.label}>
          <span className="hl-main tabular">{minutes(h.focus.minutes)}</span>
          <span className="hl-sub tabular">{h.focus.label} · {clock(h.focus.start)}–{clock(h.focus.end)}</span>
        </li>
      </HighlightColumn>
    ),
  ].filter(Boolean);
  if (!columns.length) return null;
  return <section className="highlights">{columns}</section>;
}

function HighlightColumn({ label, more = 0, children }: { label: string; more?: number; children: React.ReactNode }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <ul>{children}</ul>
      {more > 0 && <div className="tile-note">+{more} more</div>}
    </div>
  );
}

function Tile({ label, value, note, hero }: { label: string; value: string; note: string; hero?: boolean }) {
  return (
    <div className={`tile${hero ? " hero" : ""}`}>
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      <div className="tile-note">{note}</div>
    </div>
  );
}

function Projects({ day }: { day: Day }) {
  const { header, sorted } = useSort<"project" | "minutes" | "commits" | "prs">("minutes", -1);
  const rows = day.summary.projects;
  if (!rows.length) return <div className="empty">No projects yet.</div>;
  const max = Math.max(...rows.map((r) => r.minutes), 1);
  return (
    <table className="projects">
      <thead>
        <tr>
          {header("project", "Project")}{header("minutes", "Time", "bar-col", -1)}
          {header("commits", "Commits", "num", -1)}{header("prs", "PRs", "num", -1)}
        </tr>
      </thead>
      <tbody>
        {sorted(rows, (r, k) => (k === "project" ? r.project.toLowerCase() : r[k])).map((r) => (
          <tr key={r.project}>
            <td>{r.project}</td>
            <td className="bar-col">
              <span className="bar" style={{ width: `${(r.minutes / max) * 100}%` }} />
              <span className="tabular">{minutes(r.minutes)}</span>
            </td>
            <td className="num tabular">{r.commits || ""}</td>
            <td className="num tabular">{r.prs || ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Sites({ day }: { day: Day }) {
  if (!day.sites.length) return <div className="empty">No browsing recorded yet.</div>;
  const max = Math.max(...day.sites.map((s) => s.minutes), 1);
  return (
    <table className="projects">
      <tbody>
        {day.sites.map((s) => (
          <tr key={s.site ?? "_other"}>
            <td className={s.site ? undefined : "muted"}>{s.site ?? "Other pages"}</td>
            <td className="bar-col">
              <span className="bar" style={{ width: `${(s.minutes / max) * 100}%`, background: colorVar("browse") }} />
              <span className="tabular">{minutes(s.minutes)}</span>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
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
