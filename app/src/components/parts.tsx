import { openUrl } from "@tauri-apps/plugin-opener";
import { useState, type ReactNode } from "react";
import { colorVar, minutes } from "../format";
import type { Highlights, ProjectRow, Site } from "../types";

/** Pieces the day, week and month views share. */

export function Tile({ label, value, note, extra, children }:
  { label: string; value: ReactNode; note: string; extra?: string; children?: ReactNode }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      <div className="tile-note">{note}</div>
      {extra && <div className="tile-note" title={extra}>{extra}</div>}
      {children}
    </div>
  );
}

export function Change({ insertions, deletions }: { insertions: number; deletions: number }) {
  return <><span className="added">+{insertions}</span> <span className="removed">−{deletions}</span></>;
}

type Dir = 1 | -1;

/** Click a header to sort by it; click again to reverse. */
export function useSort<K extends string>(initial: K, initialDir: Dir = 1) {
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

const SHOWN = 5;

/** A card of one-line rows: the thing on the left, where and when on the right. */
export function ListCard({ label, more = 0, children }: { label: string; more?: number; children: ReactNode }) {
  return (
    <section className="card hl-card" aria-label={label}>
      <h2 className="hl-label">{label}</h2>
      <ul className="hl-list">{children}</ul>
      {more > 0 && <div className="tile-note hl-more">+{more} more</div>}
    </section>
  );
}

export function ShippedCard({ items, meta }:
  { items: Highlights["shipped"]; meta?: (i: number) => string | null }) {
  return (
    <ListCard label="Shipped" more={items.length - SHOWN}>
      {items.slice(0, SHOWN).map((x, i) => (
        <li key={i}>
          {x.url ? <button className="link hl-main" title={x.title} onClick={() => openUrl(x.url!)}>{x.title}</button>
            : <span className="hl-main" title={x.title}>{x.title}</span>}
          <span className="hl-sub">{[x.project, meta?.(i)].filter(Boolean).join(" · ")}</span>
        </li>
      ))}
    </ListCard>
  );
}

export function WorkingOnCard({ label, items }: { label: string; items: Highlights["working_on"] }) {
  return (
    <ListCard label={label} more={items.length - SHOWN}>
      {items.slice(0, SHOWN).map((w) => (
        <li key={`${w.project}/${w.branch}`}>
          <code className="branch" title={w.branch}>{w.branch}</code>
          <span className="hl-sub">{[w.project, minutes(w.minutes)].filter(Boolean).join(" · ")}</span>
        </li>
      ))}
    </ListCard>
  );
}

export function ProjectsTable({ rows }: { rows: ProjectRow[] }) {
  const { header, sorted } = useSort<"project" | "minutes" | "commits" | "prs">("minutes", -1);
  if (!rows.length) return <div className="empty">No projects yet.</div>;
  const max = Math.max(...rows.map((r) => r.minutes), 1);
  return (
    <div className="table-scroll">
      <table className="bars projects">
        <colgroup><col style={{ width: 120 }} /><col /><col style={{ width: 64 }} /><col style={{ width: 84 }} /><col style={{ width: 44 }} /></colgroup>
        <thead>
          <tr>
            {header("project", "Project")}{header("minutes", "Time", undefined, -1)}
            <th><span className="sr-only">Duration</span></th>
            {header("commits", "Commits", "num", -1)}{header("prs", "PRs", "num", -1)}
          </tr>
        </thead>
        <tbody>
          {sorted(rows, (r, k) => (k === "project" ? r.project.toLowerCase() : r[k])).map((r) => (
            <tr key={r.project}>
              <td className="name" title={r.project}>{r.project}</td>
              <td className="bar-col"><div className="bar" style={{ width: `${(r.minutes / max) * 100}%` }} /></td>
              <td className="num">{minutes(r.minutes)}</td>
              <td className="num">{r.commits || ""}</td>
              <td className="num">{r.prs || ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function SitesTable({ rows }: { rows: Site[] }) {
  if (!rows.length) return <div className="empty">No browsing recorded yet.</div>;
  const max = Math.max(...rows.map((s) => s.minutes), 1);
  return (
    <div className="table-scroll">
      <table className="bars sites">
        <colgroup><col style={{ width: 140 }} /><col /><col style={{ width: 52 }} /></colgroup>
        <thead><tr><th>Site or page</th><th>Time</th><th><span className="sr-only">Duration</span></th></tr></thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.site ?? "_other"}>
              <td className={`name${s.site && !s.page ? "" : " muted"}`} title={s.site ?? undefined}>{s.site ?? "Other pages"}</td>
              <td className="bar-col">
                <div className="bar" style={{ width: `${(s.minutes / max) * 100}%`, background: colorVar("browse") }} />
              </td>
              <td className="num">{minutes(s.minutes)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ProjectsAndSites({ projects, sites }: { projects: ProjectRow[]; sites: Site[] }) {
  return (
    <div className="split">
      <section className="card projects-card">
        <h2>Projects</h2>
        <ProjectsTable rows={projects} />
      </section>
      <section className="card sites-card">
        <h2>Top sites</h2>
        <SitesTable rows={sites} />
      </section>
    </div>
  );
}
