import { listen } from "@tauri-apps/api/event";
import { openUrl } from "@tauri-apps/plugin-opener";
import { useEffect, useState } from "react";
import { syncNow, useDay } from "../api";
import {
  CATEGORY_LABEL, GROUP_LABEL, SOURCE_LABEL, ago, clock, colorVar, longDate, minutes, shiftDate, today,
} from "../format";
import type { Day } from "../types";
import DayClock from "./DayClock";
import Timeline from "./Timeline";
import { Tooltip, useTooltip } from "./Tooltip";
import "./dayview.css";

export default function DayView() {
  const [date, setDate] = useState(today());
  const { day, error, syncing } = useDay(date);
  const [fullDay, setFullDay] = useState(false);
  const [asTable, setAsTable] = useState(false);
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
        {day && <Body day={day} fullDay={fullDay} setFullDay={setFullDay} asTable={asTable}
          setAsTable={setAsTable} tooltip={tooltip} />}
      </main>
      <Tooltip tip={tooltip.tip} />
    </div>
  );
}

interface BodyProps {
  day: Day;
  fullDay: boolean;
  setFullDay: (v: boolean) => void;
  asTable: boolean;
  setAsTable: (v: boolean) => void;
  tooltip: ReturnType<typeof useTooltip>;
}

function Body({ day, fullDay, setFullDay, asTable, setAsTable, tooltip }: BodyProps) {
  const s = day.summary;
  if (day.before_tracking) {
    return <div className="notice">Tracking started {new Date(day.day_zero).toLocaleString()}. Nothing is recorded before that.</div>;
  }
  return (
    <>
      <p className="line">{day.line}</p>

      <section className="tiles">
        <Tile label="Work" value={minutes(s.work_min)} hero
          note={s.first ? `active ${clock(s.first)}–${clock(s.last!)}` : "no activity yet"} />
        <Tile label="Longest stretch" value={s.longest ? minutes(s.longest.minutes) : "—"}
          note={s.longest ? `${s.longest.project}, ${clock(s.longest.start)}–${clock(s.longest.end)}` : "on one project"} />
        <Tile label="Commits" value={String(s.counts.commits)} note={`${s.counts.pushes} push${s.counts.pushes === 1 ? "" : "es"}`} />
        <Tile label="Pull requests" value={String(s.counts.prs)} note={`${s.counts.reviews} review${s.counts.reviews === 1 ? "" : "s"}`} />
        <Tile label="Wiki edits" value={String(s.counts.wiki)} note={s.counts.issues ? `${s.counts.issues} issues` : "Confluence"} />
        <Tile label="Non-work" value={minutes(s.nonwork_min)}
          note={s.unknown_min ? `+ ${minutes(s.unknown_min)} unclassified` : "time only, no details"} />
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Timeline</h2>
          <div className="toggles">
            <label><input type="checkbox" checked={fullDay} onChange={(e) => setFullDay(e.target.checked)} /> Full 24 hours</label>
            <label><input type="checkbox" checked={asTable} onChange={(e) => setAsTable(e.target.checked)} /> Table</label>
          </div>
        </div>
        {asTable ? <EventTable day={day} /> : <Timeline day={day} fullDay={fullDay} tooltip={tooltip} />}
        <p className="caption">
          Lanes overlap: you can browse during a meeting. Bars are spans of activity; dots are single traces
          (a commit, a PR). Grey background is time away from the keyboard.
        </p>
      </section>

      <div className="row">
        <section className="card clock-card">
          <h2>Day clock</h2>
          <DayClock day={day} tooltip={tooltip} />
          <ul className="legend">
            {day.groups.concat([{ key: "nonwork", label: GROUP_LABEL.nonwork }])
              .filter((g) => s.groups[g.key])
              .map((g) => (
                <li key={g.key}>
                  <span className="swatch" style={{ background: colorVar(g.key) }} />
                  {g.label} <span className="muted tabular">{minutes(s.groups[g.key] ?? 0)}</span>
                </li>
              ))}
          </ul>
        </section>

        <section className="card grow">
          <h2>Projects</h2>
          <Projects day={day} />
          <h2 className="spaced">Traces</h2>
          <Traces day={day} />
        </section>
      </div>

      <Sources day={day} />
    </>
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
  const rows = day.summary.projects;
  if (!rows.length) return <div className="empty">No work projects yet.</div>;
  const max = Math.max(...rows.map((r) => r.minutes), 1);
  return (
    <table className="projects">
      <thead>
        <tr><th>Project</th><th className="bar-col">Time</th><th className="num">Commits</th><th className="num">PRs</th></tr>
      </thead>
      <tbody>
        {rows.map((r) => (
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

function Traces({ day }: { day: Day }) {
  const points = [...day.points].sort((a, b) => a.at.localeCompare(b.at));
  if (!points.length) return <div className="empty">No commits, PRs or wiki edits yet.</div>;
  return (
    <ul className="traces">
      {points.map((p, i) => (
        <li key={i}>
          <span className="tabular muted">{clock(p.at)}</span>
          <span className="swatch round" style={{ background: colorVar(p.group) }} />
          <span className="trace-kind">{CATEGORY_LABEL[p.category] ?? p.category}</span>
          {p.sensitivity === "work" ? (
            p.url ? <button className="link" onClick={() => openUrl(p.url!)}>{p.title ?? p.url}</button>
              : <span>{p.title}</span>
          ) : <span className="muted">non-work — details not kept</span>}
          {p.project && <span className="tag">{p.project}</span>}
        </li>
      ))}
    </ul>
  );
}

function EventTable({ day }: { day: Day }) {
  const rows = [
    ...day.blocks.map((b) => ({ at: b.start, end: b.end, group: b.group, what: b.titles[0] ?? b.category,
      project: b.project, source: b.source, dur: minutes(b.minutes) })),
    ...day.points.map((p) => ({ at: p.at, end: null, group: p.group, what: p.title ?? p.category,
      project: p.project, source: p.source, dur: "" })),
  ].sort((a, b) => a.at.localeCompare(b.at));
  return (
    <div className="table-wrap">
      <table className="events">
        <thead><tr><th>Time</th><th>Activity</th><th>What</th><th>Project</th><th>Source</th><th className="num">Length</th></tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              <td className="tabular">{clock(r.at)}{r.end ? `–${clock(r.end)}` : ""}</td>
              <td><span className="swatch" style={{ background: colorVar(r.group) }} /> {GROUP_LABEL[r.group]}</td>
              <td className="what">{r.what}</td>
              <td>{r.project ?? ""}</td>
              <td>{SOURCE_LABEL[r.source] ?? r.source}</td>
              <td className="num tabular">{r.dur}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Sources({ day }: { day: Day }) {
  return (
    <section className="sources">
      {day.sources.map((src) => {
        const state = src.configured === false ? "off" : src.ok ? "ok" : "failed";
        const icon = { ok: "✓", failed: "!", off: "–" }[state];
        const text = { ok: `synced ${ago(src.at)}`, failed: "sync failed", off: "not configured" }[state];
        return (
          <div key={src.name} className={`source ${state}`} title={src.error ?? undefined}>
            <span className="source-icon" aria-hidden>{icon}</span>
            <span className="source-name">{SOURCE_LABEL[src.name] ?? src.name}</span>
            <span className="muted">{text} · {src.events} events</span>
          </div>
        );
      })}
    </section>
  );
}
