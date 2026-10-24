import { useState } from "react";
import { summarizePeriod, usePeriod } from "../api";
import { GROUP_LABEL, ago, clock, minutes, shiftDate, today } from "../format";
import type { Exclusion, Period, PeriodDay, PeriodKind, ReviewEntry, ReviewRequest, TodoGroup } from "../types";
import { Change, ProjectsAndSites, commitNote, ShippedCard, Tile, WorkingOnCard } from "./parts";

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
        {p.commits
          ? <Tile label={`Total commits · ${word}`} value={<Change {...p.commits} />} note={commitNote(p.commits)}
            extra={p.commits.projects.join(", ")} />
          : <Tile label={`Total commits · ${word}`} value="—" note="no commits yet" />}
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

/** Lumen's look back at the week or month. Nothing is sent until the exact request has been reviewed here. */
function PeriodStory({ kind, p }: { kind: PeriodKind; p: Period }) {
  const [busy, setBusy] = useState<"build" | "send" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const story = p.story;
  const hasData = p.days.some((d) => d.has_data);

  const run = (send: boolean, exclude: Exclusion[] = []) => {
    setBusy(send ? "send" : "build");
    setError(null);
    summarizePeriod(kind, p.start, send, exclude).catch((e) => setError(String(e))).finally(() => setBusy(null));
  };

  if (story?.request) {
    return (
      <section className="card period-story" aria-label={`Review your ${kind}'s request`}>
        <Review kind={kind} request={story.request} audit={story.audit} busy={busy}
          onSend={(exclude) => run(true, exclude)} onRebuild={() => run(false)} />
        {error && <div className="notice error">{error}</div>}
      </section>
    );
  }

  return (
    <section className="card period-story" aria-label={`Your ${kind}`}>
      <div className="card-head">
        <h2>Your {kind}</h2>
        <div className="actions">
          <button className={story?.narrative ? "quiet" : "primary"} disabled={!!busy || !hasData} onClick={() => run(false)}>
            {busy ? "Building…" : story?.narrative ? "Write again" : "Write summary"}
          </button>
        </div>
      </div>
      <div className="story-boxes">
        <div className={`story-box main${story?.narrative ? "" : " empty"}`}>
          <div className="box-label">Summary</div>
          {story?.narrative ? (
            <>
              <p className="narrative">{story.narrative}</p>
              <p className="caption">
                Written by {story.model} {ago(story.generated_at)} from the request you reviewed. Inferred: check it
                against the days below.
              </p>
            </>
          ) : (
            <p className="muted">
              {hasData
                ? `No summary yet. You'll see exactly what would be sent to Lumen and can take anything out before it leaves.`
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
      {story?.narrative && (story.observations?.length > 0 || story.next_week?.length > 0) && (
        <div className="story-boxes">
          {story.observations?.length > 0 && (
            <div className="story-box main">
              <div className="box-label">Worth a look</div>
              <ul className="observations">
                {story.observations.map((o, i) => (
                  <li key={i}>
                    <span className="obs-kind">{OBSERVATION_LABEL[o.kind] ?? o.kind}</span>
                    <p>{o.text}</p>
                    {o.evidence && <p className="obs-evidence">{o.evidence}</p>}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {kind === "week" && story.next_week?.length > 0 && <NextWeek groups={story.next_week} />}
        </div>
      )}
      {error && <div className="notice error">{error}</div>}
    </section>
  );
}

const OBSERVATION_LABEL: Record<string, string> = {
  open_loop: "Open loop", recurring: "Recurring", time_vs_weight: "Time vs. weight", pattern: "Pattern",
};

/** The week's draft todo list for the next one, to copy into the 1:1 doc. */
function NextWeek({ groups }: { groups: TodoGroup[] }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    const text = groups.map((g) => [g.project, ...g.todos.map((t) => `- ${t.text}`)].join("\n")).join("\n\n");
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    });
  };
  return (
    <div className="story-box todos-box">
      <div className="box-head">
        <div className="box-label">Draft for next week</div>
        <button className="quiet small" onClick={copy}>{copied ? "Copied" : "Copy todos"}</button>
      </div>
      {groups.map((g) => (
        <div key={g.project} className="todo-group">
          <div className="todo-project">{g.project}</div>
          <ul>
            {g.todos.map((t, i) => (
              <li key={i}>
                {t.text}
                {t.why && <span className="todo-why">{t.why}</span>}
              </li>
            ))}
          </ul>
        </div>
      ))}
      <p className="caption">A draft from this week's open loops. Edit it before it goes in your list.</p>
    </div>
  );
}

const FIELDS = [["titles", "Titles"], ["sites", "Sites and pages"], ["branches", "Branches"]] as const;
const MEETING_FIELDS = [["topics", "Topics"], ["mine", "Mine"], ["waiting", "Waiting on"], ["unassigned", "Unassigned"]] as const;

function entryName(e: ReviewEntry): string {
  if (e.kind === "meeting") return "Meetings";
  return e.project ?? (e.group ? GROUP_LABEL[e.group] : e.key);
}

/** The request as it would leave, entry by entry. Untick an entry or click a title, site or branch to keep it out. */
function Review({ kind, request, audit, busy, onSend, onRebuild }: {
  kind: PeriodKind; request: ReviewRequest; audit: string | null; busy: "build" | "send" | null;
  onSend: (exclude: Exclusion[]) => void; onRebuild: () => void;
}) {
  const [out, setOut] = useState<Set<string>>(new Set());
  const id = (entry: string, field?: string, value?: string) => JSON.stringify([entry, field ?? null, value ?? null]);
  const toggle = (k: string) => setOut((s) => {
    const next = new Set(s);
    if (!next.delete(k)) next.add(k);
    return next;
  });
  const mid = (meeting: string, field?: string, value?: string) => JSON.stringify(["meeting", meeting, field ?? null, value ?? null]);
  const exclusions: Exclusion[] = [...out].map((k) => {
    const parts = JSON.parse(k);
    if (parts[0] === "meeting" && parts.length === 4) {
      const [, meeting, field, value] = parts;
      return field ? { meeting, field, value } : { meeting };
    }
    const [entry, field, value] = parts;
    return field ? { entry, field, value } : { entry };
  });
  const meetings = request.meetings ?? [];
  const kept = request.entries.filter((e) => !out.has(id(e.key))).length;
  const privateMin = request.private.minutes;

  return (
    <>
      <div className="card-head">
        <h2>Review before sending</h2>
        <div className="actions">
          <button className="quiet" disabled={!!busy} onClick={onRebuild}>{busy === "build" ? "Building…" : "Rebuild"}</button>
          <button className="primary" disabled={!!busy || kept === 0} onClick={() => onSend(exclusions)}>
            {busy === "send" ? "Sending…" : `Send ${kept} of ${request.entries.length} to Lumen`}
          </button>
        </div>
      </div>
      <p className="review-note">
        This is everything the request for {request.period} holds. Nothing has left your Mac. Untick an entry to leave it
        out, or click a title, site or branch to remove just that.
      </p>
      <ul className="review-list">
        {request.entries.map((e) => {
          const off = out.has(id(e.key));
          const counts = Object.entries(e.counts).map(([c, n]) => `${n} ${c}${n === 1 ? "" : "s"}`).join(" · ");
          return (
            <li key={e.key} className={off ? "off" : undefined}>
              <label className="review-entry">
                <input type="checkbox" checked={!off} onChange={() => toggle(id(e.key))} />
                <span className="review-name">{entryName(e)}</span>
                <span className="muted tabular">{minutes(e.minutes)} · {e.days.join(", ")}{counts && ` · ${counts}`}</span>
              </label>
              {!off && FIELDS.map(([field, label]) => {
                const values = field === "sites" ? Object.keys(e.sites) : e[field];
                if (!values.length) return null;
                return (
                  <div key={field} className="review-field">
                    <span className="review-label">{label}</span>
                    <div className="review-values">
                      {values.map((v) => {
                        const k = id(e.key, field, v);
                        return (
                          <button key={v} className={`review-chip${out.has(k) ? " off" : ""}`} onClick={() => toggle(k)}
                            aria-pressed={!out.has(k)} title={out.has(k) ? "Left out. Click to include" : "Click to leave out"}>
                            {v}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </li>
          );
        })}
      </ul>
      {meetings.length > 0 && (
        <>
          <h3 className="review-heading">Meeting notes</h3>
          <p className="review-note">
            Topics and action items from your Granola notes, with people's names replaced by [person]. A name that was
            missed can be clicked out.
          </p>
          <ul className="review-list">
            {meetings.map((m) => {
              const off = out.has(mid(m.key));
              return (
                <li key={m.key} className={off ? "off" : undefined}>
                  <label className="review-entry">
                    <input type="checkbox" checked={!off} onChange={() => toggle(mid(m.key))} />
                    <span className="review-name">{m.title ?? "Not on the calendar"}</span>
                    <span className="muted tabular">{m.day} · {m.time}</span>
                  </label>
                  {!off && MEETING_FIELDS.map(([field, label]) => m[field].length > 0 && (
                    <div key={field} className="review-field">
                      <span className="review-label">{label}</span>
                      <div className="review-values">
                        {m[field].map((v) => {
                          const k = mid(m.key, field, v);
                          return (
                            <button key={v} className={`review-chip${out.has(k) ? " off" : ""}`} onClick={() => toggle(k)}
                              aria-pressed={!out.has(k)} title={out.has(k) ? "Left out. Click to include" : "Click to leave out"}>
                              {v}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </li>
              );
            })}
          </ul>
        </>
      )}
      <p className="review-note muted">
        Also sent: totals for the {kind} and for each of {request.days.length} day{request.days.length === 1 ? "" : "s"} with
        data (active and meeting minutes, first and last activity, project switches, back-to-back meetings, focus
        stretches and what ended them, time outside 9–5)
        {privateMin ? `, private time as one total (${minutes(privateMin)})` : ""}
        {request.also.episodes ? `, and a count of ${request.also.episodes} short bits too small to list` : ""}.
        {audit && <> The full request is saved at <code className="path inline">{audit}</code>.</>}
      </p>
    </>
  );
}
