import { useState } from "react";
import { summarizeDay } from "../api";
import { GROUP_LABEL, ago, clock, colorVar, minutes } from "../format";
import type { Day, Episode } from "../types";

/** The LLM-written description of the day, its episodes, and the dry-run / send controls. */
export default function Story({ day }: { day: Day }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const story = day.story;

  const run = (send: boolean) => {
    setBusy(true);
    setError(null);
    summarizeDay(day.date, send).catch((e) => setError(String(e))).finally(() => setBusy(false));
  };

  return (
    <section className="card story">
      <div className="card-head">
        <h2>Your day</h2>
        {story?.narrative && (
          <button className="quiet" disabled={busy} onClick={() => run(true)}>{busy ? "Writing…" : "Rewrite"}</button>
        )}
      </div>

      {story?.narrative ? (
        <>
          <p className="narrative">{story.narrative}</p>
          {story.themes.length > 0 && (
            <div className="themes">{story.themes.map((t) => <span key={t} className="tag">{t}</span>)}</div>
          )}
          <p className="caption">
            Written by {story.model} {ago(story.generated_at)} from work traces only. Inferred: check it against the
            timeline below.
          </p>
        </>
      ) : story?.dry_run ? (
        <div className="dry-run">
          <p>
            <strong>Dry run.</strong> The request for {day.episodes.length} episodes was built and saved; nothing was
            sent. Read it before sending:
          </p>
          <code className="path">{story.audit}</code>
          <div className="actions">
            <button className="primary" disabled={busy} onClick={() => run(true)}>{busy ? "Sending…" : "Send to Lumen"}</button>
            <button className="quiet" disabled={busy} onClick={() => run(false)}>Rebuild request</button>
          </div>
        </div>
      ) : (
        <div className="actions">
          <span className="muted">No summary yet. Only work episodes and counts are used; non-work time is sent as bare time ranges.</span>
          <button className="primary" disabled={busy} onClick={() => run(false)}>{busy ? "Building…" : "Write summary"}</button>
        </div>
      )}
      {error && <div className="notice error">{error}</div>}

      {day.episodes.length > 0 && <Episodes episodes={day.episodes} />}
    </section>
  );
}

function Episodes({ episodes }: { episodes: Episode[] }) {
  return (
    <ol className="episodes">
      {episodes.map((ep) => {
        const where = ep.kind === "meeting" ? "Meeting" : ep.project ?? GROUP_LABEL[ep.group];
        const counts = Object.entries(ep.counts).map(([k, n]) => `${n} ${k}${n > 1 ? "s" : ""}`).join(", ");
        return (
          <li key={ep.id}>
            <span className="tabular muted ep-time">{clock(ep.start)}–{clock(ep.end)}</span>
            <span className="swatch" style={{ background: colorVar(ep.kind === "meeting" ? "coord" : ep.group) }} />
            <span className="ep-where">{where}</span>
            <span className={ep.label ? "ep-label" : "ep-label muted"}>
              {ep.label ?? [ep.minutes ? minutes(ep.minutes) : "", counts].filter(Boolean).join(" · ")}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
