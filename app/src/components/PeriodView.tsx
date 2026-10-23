import { useState } from "react";
import { summarizePeriod, usePeriod } from "../api";
import { ago, clock, minutes, shiftDate, today } from "../format";
import type { Period, PeriodDay, PeriodKind } from "../types";
import { Change, ProjectsAndSites, ShippedCard, Tile, WorkingOnCard } from "./parts";

const parse = (iso: string) => new Date(`${iso}T12:00:00`);
const iso = (d: Date) => d.toLocaleDateString("en-CA");
const fmt = (s: string, o: Intl.DateTimeFormatOptions) => parse(s).toLocaleDateString([], o);

/** First and last day of the week (Sunday to Saturday) or month around a date, as the CLI computes them. */
export function bounds(kind: PeriodKind, date: string): [string, string] {
  const d = parse(date);
  if (kind === "week") {
    const start = new Date(d.getFullYear(), d.getMonth(), d.getDate() - d.getDay());
    return [iso(start), iso(new Date(start.getFullYear(), start.getMonth(), start.getDate() + 6))];
  }
  return [iso(new Date(d.getFullYear(), d.getMonth(), 1)), iso(new Date(d.getFullYear(), d.getMonth() + 1, 0))];
}

/** The first day of the week or month n periods away. */
export function shiftPeriod(kind: PeriodKind, date: string, n: number): string {
  const [start] = bounds(kind, date);
  if (kind === "week") return shiftDate(start, 7 * n);
  const d = parse(start);
  return iso(new Date(d.getFullYear(), d.getMonth() + n, 1));
}

/** "This week", "Oct 11–17", "Sep 27 – Oct 3"; "October 2026". */
export function periodLabel(kind: PeriodKind, date: string): string {
  const [start, end] = bounds(kind, date);
  if (kind === "month") return fmt(start, { month: "long", year: "numeric" });
  const now = today();
  if (start <= now && now <= end) return "This week";
  return range(start, end, "short");
}

function range(start: string, end: string, month: "short" | "long"): string {
  const sameMonth = start.slice(0, 7) === end.slice(0, 7);
  return sameMonth
    ? `${fmt(start, { month, day: "numeric" })}–${parse(end).getDate()}`
    : `${fmt(start, { month, day: "numeric" })} – ${fmt(end, { month, day: "numeric" })}`;
}

/** A week or month: totals with a per-day sparkline, a chart of the days, and what was shipped and worked on. */
export default function PeriodView({ kind, date, openDay }: { kind: PeriodKind; date: string; openDay: (d: string) => void }) {
  const { data: p, error } = usePeriod(kind, date);
  const [start, end] = bounds(kind, date);
  return (
    <>
      <header className="day-head">
        <h1>{kind === "week" ? `Week of ${range(start, end, "long")}` : fmt(start, { month: "long", year: "numeric" })}</h1>
        {p && <p className="line">{p.line}</p>}
        {p && <p className="subline">{coverage(kind, p)}</p>}
      </header>
      {error && <div className="notice error">Couldn't load this {kind}: {error}</div>}
      {p && p.start === start && <Body kind={kind} p={p} openDay={openDay} />}
    </>
  );
}

function coverage(kind: PeriodKind, p: Period): string {
  const days = p.days.filter((d) => d.has_data);
  const soFar = p.elapsed < p.days.length ? " so far" : "";
  const names = days.length && days.length <= 3
    ? ` · ${days.map((d) => fmt(d.date, kind === "week" ? { weekday: "long" } : { month: "long", day: "numeric" })).join(", ")}`
    : "";
  return `Data for ${days.length} of ${p.elapsed} day${p.elapsed === 1 ? "" : "s"}${soFar}${names}`;
}

function Body({ kind, p, openDay }: { kind: PeriodKind; p: Period; openDay: (d: string) => void }) {
  const t = p.totals;
  const word = kind === "week" ? "week" : "month";
  const when = (d: string) => fmt(d, kind === "week" ? { weekday: "short", month: "short", day: "numeric" } : { month: "short", day: "numeric" });
  const spark = (value: (d: PeriodDay) => number, color: string, what: string) =>
    <Spark kind={kind} p={p} value={value} color={color} what={what} />;
  return (
    <>
      <PeriodStory kind={kind} p={p} />

      <section className="g5">
        <Tile label={`Active · ${word} total`} value={minutes(t.active_min)}
          note={t.avg_active_min ? `avg ${minutes(t.avg_active_min)} per day with data` : "no data yet"}>
          {spark((d) => d.active_min, "var(--bar)", "Active")}
        </Tile>
        <Tile label={`Meetings · ${word} total`} value={minutes(t.meeting_min)}
          note={`${t.meetings} meeting${t.meetings === 1 ? "" : "s"}`}>
          {spark((d) => d.meeting_min, "var(--g-meeting)", "Meetings")}
        </Tile>
        <Tile label={`Email sent · ${word} total`} value={String(t.emails_sent)} note={`${t.emails_received} received`}>
          {spark((d) => d.emails_sent, "var(--bar)", "Email sent")}
        </Tile>
        {p.focus
          ? <Tile label={`Longest focus · ${word} best`} value={minutes(p.focus.minutes)}
            note={`${p.focus.label} · ${fmt(p.focus.date, kind === "week" ? { weekday: "short" } : { month: "short", day: "numeric" })}${kind === "week" ? " " : ", "}${clock(p.focus.start)}–${clock(p.focus.end)}`}>
            {spark((d) => d.focus_min, "var(--g-code)", "Longest focus")}
          </Tile>
          : <Tile label={`Longest focus · ${word} best`} value="—" note="no focus stretch yet" />}
        {p.biggest
          ? <Tile label={`Biggest change · ${word}`} value={<Change {...p.biggest} />}
            note={[`${p.biggest.files} file${p.biggest.files === 1 ? "" : "s"}`, p.biggest.project, when(p.biggest.date)]
              .filter(Boolean).join(" · ")}
            extra={p.biggest.title} />
          : <Tile label={`Biggest change · ${word}`} value="—" note="no commits yet" />}
      </section>

      {kind === "week" ? <ByDay p={p} openDay={openDay} /> : <MonthGrid p={p} openDay={openDay} />}

      {(p.shipped.length > 0 || p.working_on.length > 0) && (
        <section className="g2">
          {p.shipped.length > 0 && <ShippedCard items={p.shipped} meta={(i) => when(p.shipped[i].date)} />}
          {p.working_on.length > 0 && <WorkingOnCard label="Worked on" items={p.working_on} />}
        </section>
      )}

      <ProjectsAndSites projects={p.projects} sites={p.sites} />
    </>
  );
}

/** One small bar per day under a tile: grey stubs for days without data, faint stubs for days still to come. */
function Spark({ kind, p, value, color, what }:
  { kind: PeriodKind; p: Period; value: (d: PeriodDay) => number; color: string; what: string }) {
  const max = Math.max(...p.days.map(value), 1);
  const withData = p.days.filter((d) => d.has_data && value(d));
  const label = `${what} per day: ${withData.map((d) => `${what === "Email sent" ? value(d) : minutes(value(d))} on ${fmt(d.date, { weekday: "long", month: "long", day: "numeric" })}`).join(", ") || "nothing yet"}`;
  return (
    <div className="spark" role="img" aria-label={label}>
      <div className="spark-bars" style={{ gridTemplateColumns: `repeat(${p.days.length}, minmax(0, 1fr))`, gap: kind === "week" ? 4 : 1 }}>
        {p.days.map((d) => (
          <div key={d.date} className={d.future ? "future" : d.has_data && value(d) ? undefined : "none"}
            style={d.has_data && value(d) ? { height: Math.max(3, (value(d) / max) * 28), background: color } : undefined} />
        ))}
      </div>
      {kind === "week" ? (
        <div className="spark-labels week" aria-hidden="true">
          {p.days.map((d) => (
            <span key={d.date} className={d.date === p.today ? "today" : d.future ? "future" : undefined}>
              {fmt(d.date, { weekday: "narrow" })}
            </span>
          ))}
        </div>
      ) : (
        <div className="spark-labels" aria-hidden="true">
          <span>1</span>
          {p.today >= p.start && p.today <= p.end && <span className="today">{parse(p.today).getDate()}</span>}
          <span>{p.days.length}</span>
        </div>
      )}
    </div>
  );
}

/** Week: active time per day as columns, meetings at the bottom of each. */
function ByDay({ p, openDay }: { p: Period; openDay: (d: string) => void }) {
  const maxH = Math.max(...p.days.map((d) => d.active_min)) / 60;
  const step = maxH > 12 ? 4 : 2;
  const top = Math.max(step, Math.ceil(maxH / step) * step);
  const ticks = Array.from({ length: top / step + 1 }, (_, i) => i * step);
  return (
    <section className="card by-day" aria-label="By day">
      <div className="card-head">
        <h2>By day</h2>
        <div className="key">
          <span><span className="swatch" style={{ background: "var(--bar)" }} />Active</span>
          <span><span className="swatch" style={{ background: "var(--g-meeting)" }} />of which meetings</span>
        </div>
      </div>
      <div className="chart">
        <div className="y-axis" aria-hidden="true">
          {ticks.map((h) => <span key={h} style={{ bottom: `${(h / top) * 100}%` }}>{h ? `${h}h` : "0"}</span>)}
        </div>
        <div className="plot">
          <div className="gridlines" aria-hidden="true">
            {ticks.map((h) => <div key={h} style={{ bottom: `${(h / top) * 100}%` }} />)}
          </div>
          <div className="columns">
            {p.days.map((d) => {
              const name = `${fmt(d.date, { weekday: "short" })} ${parse(d.date).getDate()}`;
              return (
                <div key={d.date} className="column">
                  <div className="stack">
                    {d.has_data ? (
                      <>
                        <span className="value">{minutes(d.active_min)}</span>
                        <button className="bar-stack" onClick={() => openDay(d.date)}
                          style={{ height: `${Math.max(1, (d.active_min / 60 / top) * 100)}%` }}
                          aria-label={`${fmt(d.date, { weekday: "long", month: "long", day: "numeric" })}: ${minutes(d.active_min)} active, ${minutes(d.meeting_min)} meetings`}>
                          <span className="meet" style={{ height: `${Math.min(100, (d.meeting_min / Math.max(d.active_min, 1)) * 100)}%` }} />
                        </button>
                      </>
                    ) : d.future ? (
                      <><span className="value faint">—</span><span className="placeholder future" /></>
                    ) : (
                      <><span className="value muted">No data</span><span className="placeholder" /></>
                    )}
                  </div>
                  {d.has_data
                    ? <button className="link day-link" onClick={() => openDay(d.date)}>{name}</button>
                    : <span className={`day-name${d.future ? " faint" : ""}`}>{name}</span>}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}

const LEVELS = ["var(--heat-0)", "var(--heat-1)", "var(--heat-2)", "var(--heat-3)", "var(--heat-4)"];
const level = (min: number) => Math.min(4, Math.floor(min / 120));

/** Month: a calendar shaded by active time, with each week's total on the right. */
function MonthGrid({ p, openDay }: { p: Period; openDay: (d: string) => void }) {
  const lead = parse(p.start).getDay();
  const cells: (PeriodDay | null)[] = [...Array(lead).fill(null), ...p.days];
  while (cells.length % 7) cells.push(null);
  const weeks = Array.from({ length: cells.length / 7 }, (_, i) => cells.slice(i * 7, i * 7 + 7));
  return (
    <section className="card month" aria-label="Active time by day">
      <div className="card-head">
        <h2>Active time by day</h2>
        <div className="key">
          <span>0h {LEVELS.map((c) => <span key={c} className="heat-swatch" style={{ background: c }} />)} 8h+</span>
          <span><span className="heat-swatch empty" />No data</span>
        </div>
      </div>
      <div className="table-scroll">
        <div className="calendar">
          {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((w) => <div key={w} className="cal-head">{w}</div>)}
          <div className="cal-head right">Week</div>
          {weeks.map((week, i) => {
            const days = week.filter((d): d is PeriodDay => !!d);
            const data = days.filter((d) => d.has_data);
            const total = data.reduce((a, d) => a + d.active_min, 0);
            return [
              ...week.map((d, j) => <Cell key={d?.date ?? `blank-${i}-${j}`} d={d} today={p.today} openDay={openDay} />),
              <div key={`total-${i}`} className={`cal-total${data.length ? "" : " muted"}`}>
                {data.length ? minutes(total) : days.every((d) => d.future) ? "" : "—"}
              </div>,
            ];
          })}
        </div>
      </div>
    </section>
  );
}

function Cell({ d, today: now, openDay }: { d: PeriodDay | null; today: string; openDay: (d: string) => void }) {
  if (!d) return <div />;
  const n = parse(d.date).getDate();
  if (d.future) return <div className="cal-cell future"><span>{n}</span></div>;
  if (!d.has_data) return <div className="cal-cell none"><span>{n}</span><span className="muted">—</span></div>;
  const lv = level(d.active_min);
  return (
    <button className={`cal-cell data${lv >= 3 ? " dark" : ""}${d.date === now ? " today" : ""}`}
      style={{ background: LEVELS[lv] }} onClick={() => openDay(d.date)}
      aria-label={`${fmt(d.date, { weekday: "long", month: "long", day: "numeric" })}: ${minutes(d.active_min)} active`}>
      <span className="cal-day">{n}</span>
      <span className="cal-time">{minutes(d.active_min)}</span>
    </button>
  );
}

/** Lumen's look back at the week or month: summary and themes, with the same dry-run / send steps as a day. */
function PeriodStory({ kind, p }: { kind: PeriodKind; p: Period }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const story = p.story;
  const hasData = p.days.some((d) => d.has_data);

  const run = (send: boolean) => {
    setBusy(true);
    setError(null);
    summarizePeriod(kind, p.start, send).catch((e) => setError(String(e))).finally(() => setBusy(false));
  };

  return (
    <section className="card period-story" aria-label={`Your ${kind}`}>
      <div className="card-head">
        <h2>Your {kind}</h2>
        <div className="actions">
          {story?.narrative ? (
            <button className="quiet" disabled={busy} onClick={() => run(true)}>{busy ? "Writing…" : "Rewrite"}</button>
          ) : story?.dry_run ? (
            <>
              <button className="primary" disabled={busy} onClick={() => run(true)}>{busy ? "Sending…" : "Send to Lumen"}</button>
              <button className="quiet" disabled={busy} onClick={() => run(false)}>Rebuild request</button>
            </>
          ) : (
            <button className="primary" disabled={busy || !hasData} onClick={() => run(false)}>{busy ? "Building…" : "Write summary"}</button>
          )}
        </div>
      </div>
      <div className="story-boxes">
        <div className={`story-box main${story?.narrative ? "" : " empty"}`}>
          <div className="box-label">Summary</div>
          {story?.narrative ? (
            <>
              <p className="narrative">{story.narrative}</p>
              <p className="caption">
                Written by {story.model} {ago(story.generated_at)} from your days, without private time. Inferred: check it
                against the days below.
              </p>
            </>
          ) : story?.dry_run ? (
            <>
              <p className="muted">
                <strong>Dry run.</strong> The request for {story.entries} projects and activities across the {kind} was built
                and saved; nothing was sent. Read it before sending:
              </p>
              <code className="path">{story.audit}</code>
            </>
          ) : (
            <p className="muted">
              {hasData
                ? `No summary yet. Built from each day's request (the same limits apply), with per-day totals instead of time ranges and private time as one total.`
                : `Nothing recorded this ${kind} yet.`}
            </p>
          )}
        </div>
        <div className={`story-box themes-box${story?.themes.length ? "" : " empty"}`}>
          <div className="box-label">Themes</div>
          {story?.themes.length
            ? <div className="themes">{story.themes.map((t) => <span key={t} className="chip">{t}</span>)}</div>
            : <p className="muted">Short labels Lumen picks from the {kind}.</p>}
        </div>
      </div>
      {error && <div className="notice error">{error}</div>}
    </section>
  );
}
