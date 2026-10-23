import { CATEGORY_LABEL, GROUP_LABEL, SOURCE_LABEL, clock, colorVar, minutes } from "../format";
import type { Block, Point } from "../types";

const EVIDENCE: Record<string, string> = {
  observed: "observed on this Mac",
  recorded: "recorded by the source",
  scheduled: "scheduled, not necessarily attended",
  inferred: "inferred",
};

export function BlockDetail({ b }: { b: Block }) {
  return (
    <>
      <div className="tip-head">
        <span className="swatch" style={{ background: colorVar(b.group) }} />
        {GROUP_LABEL[b.group]}
        {b.project && <span className="tip-project">{b.project}</span>}
      </div>
      <div className="tip-row tabular">
        {clock(b.start)}–{clock(b.end)} · {minutes(b.minutes)}
      </div>
      {b.titles.map((t) => (
        <div key={t} className="tip-title">{t}</div>
      ))}
      <div className="tip-foot">
        {SOURCE_LABEL[b.source] ?? b.source} · {EVIDENCE[b.evidence] ?? b.evidence}
        {b.sensitivity !== "work" && " · details not kept"}
      </div>
    </>
  );
}

export function PointDetail({ p }: { p: Point }) {
  return (
    <>
      <div className="tip-head">
        <span className="swatch round" style={{ background: colorVar(p.group) }} />
        {CATEGORY_LABEL[p.category] ?? p.category}
        {p.project && <span className="tip-project">{p.project}</span>}
      </div>
      <div className="tip-row tabular">{clock(p.at)}</div>
      {p.title && <div className="tip-title">{p.title}</div>}
      <div className="tip-foot">
        {SOURCE_LABEL[p.source] ?? p.source}
        {p.url ? " · click to open" : ""}
        {p.sensitivity !== "work" && " · details not kept"}
      </div>
    </>
  );
}
