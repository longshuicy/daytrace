import { openUrl } from "@tauri-apps/plugin-opener";
import { scaleTime, timeHour } from "d3";
import { useLayoutEffect, useRef, useState } from "react";
import { GROUP_LABEL, LANES, SOURCE_LABEL, colorVar } from "../format";
import type { Block, Day, GroupKey } from "../types";
import { BlockDetail, PointDetail } from "./details";
import type { useTooltip } from "./Tooltip";

const LABEL_W = 128;
const LANE_H = 26;
const BAR_H = 12;
const AXIS_H = 22;
const BRIDGE_MIN = 3;  // bars in one lane closer than this (or 4px) are drawn as one stretch
const DONE = "done" as const;

type Row = GroupKey | typeof DONE;

/** Join a lane's bars into stretches, bridging short gaps. Calendar meetings stay one bar each, with their titles. */
function stretches(blocks: Block[], bridgeMs: number): Block[] {
  const out: Block[] = [];
  const sorted = [...blocks].sort((a, b) => a.start.localeCompare(b.start));
  let run: Block[] = [];
  let runEnd = 0;
  const flush = () => {
    if (!run.length) return;
    // Minutes are time actually covered, not the bridged gaps.
    let covered = 0, edge = 0;
    for (const b of run) {
      const s = Math.max(+new Date(b.start), edge), e = +new Date(b.end);
      if (e > s) covered += e - s;
      edge = Math.max(edge, e);
    }
    const unique = <T,>(xs: T[]) => [...new Set(xs)];
    const projects = unique(run.map((b) => b.project).filter(Boolean));
    const sources = unique(run.map((b) => b.source));
    out.push({
      ...run[0],
      end: new Date(runEnd).toISOString(),
      minutes: Math.round(covered / 6000) / 10,
      project: projects.join(", ") || null,
      source: sources.length === 1 ? sources[0] : sources.map((s) => SOURCE_LABEL[s] ?? s).join(", "),
      titles: unique(run.flatMap((b) => b.titles)).slice(0, 3),
    });
    run = [];
  };
  for (const b of sorted) {
    if (b.evidence === "scheduled") { out.push(b); continue; }
    if (run.length && +new Date(b.start) - runEnd > bridgeMs) flush();
    run.push(b);
    runEnd = Math.max(run.length > 1 ? runEnd : 0, +new Date(b.end));
  }
  flush();
  return out;
}

interface Props {
  day: Day;
  fullDay: boolean;
  tooltip: ReturnType<typeof useTooltip>;
}

/** A "Done" row of single traces (commits, PRs, mail), then one lane per activity group with spans joined into
 *  stretches. Lanes can overlap in time (browsing during a meeting). */
export default function Timeline({ day, fullDay, tooltip }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(800);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const done = day.points.filter((p) => p.sensitivity !== "private");
  const lanes: Row[] = [
    ...(done.length ? [DONE] : []),
    ...LANES.filter((g) => day.blocks.some((b) => b.group === g)),
  ];
  const dayStart = new Date(day.start);
  const dayEnd = new Date(day.end);
  const now = new Date(day.now);
  const isToday = now >= dayStart && now < dayEnd;

  let from = dayStart;
  let to = dayEnd;
  if (!fullDay) {
    const first = day.summary.first ? new Date(day.summary.first) : now;
    const last = day.summary.last ? new Date(isToday ? Math.max(+new Date(day.summary.last), +now) : day.summary.last) : now;
    from = timeHour.floor(new Date(Math.min(+first, +new Date(dayStart).setHours(8))));
    to = timeHour.ceil(new Date(Math.max(+last, +new Date(dayStart).setHours(18))));
    from = new Date(Math.max(+from, +dayStart));
    to = new Date(Math.min(+to, +dayEnd));
  }
  const plotW = Math.max(200, width - LABEL_W - 8);
  const x = scaleTime().domain([from, to]).range([0, plotW]).clamp(true);
  const hours = (to.getTime() - from.getTime()) / 3_600_000;
  const ticks = x.ticks(timeHour.every(hours > 14 || plotW < 640 ? 2 : 1)!);
  const height = AXIS_H + lanes.length * LANE_H + 4;
  const laneY = (g: Row) => AXIS_H + lanes.indexOf(g) * LANE_H;
  const bridgeMs = Math.max(BRIDGE_MIN * 60_000, x.invert(4).getTime() - x.invert(0).getTime());
  const bars = LANES.flatMap((g) => stretches(day.blocks.filter((b) => b.group === g), bridgeMs));
  const faded = (g: Row) => g === "other" || g === "private";

  return (
    <div ref={ref} className="timeline">
      {lanes.length === 0 ? (
        <div className="empty">No traces for this day.</div>
      ) : (
        <svg width={width} height={height} role="img" aria-label="Timeline of the day by activity">
          {lanes.map((g) => (
            <g key={g} transform={`translate(0, ${laneY(g)})`} opacity={faded(g) ? 0.6 : 1}>
              {g === DONE
                ? <circle cx={4} cy={LANE_H / 2} r={4} fill="var(--ink-2)" />
                : <rect x={0} y={LANE_H / 2 - 4} width={8} height={8} rx={2} fill={colorVar(g)} />}
              <text x={14} y={LANE_H / 2 + 4} className="lane-label">{g === DONE ? "Done" : GROUP_LABEL[g]}</text>
            </g>
          ))}
          <g transform={`translate(${LABEL_W}, 0)`}>
            {day.idle.map((iv, i) => (
              <rect key={i} x={x(new Date(iv.start))} y={AXIS_H} height={height - AXIS_H}
                width={Math.max(0, x(new Date(iv.end)) - x(new Date(iv.start)))} fill="var(--surface-2)">
                <title>Away from keyboard</title>
              </rect>
            ))}
            {ticks.map((t) => (
              <g key={+t} transform={`translate(${x(t)}, 0)`}>
                <line y1={AXIS_H - 4} y2={height} stroke="var(--grid)" />
                <text y={12} textAnchor="middle" className="axis-label tabular">
                  {t.getHours().toString().padStart(2, "0")}
                </text>
              </g>
            ))}
            {done.length > 0 && (
              <line x1={0} x2={plotW} y1={laneY(DONE) + LANE_H} y2={laneY(DONE) + LANE_H} stroke="var(--grid)" />
            )}
            {bars.map((b, i) => {
              const x0 = x(new Date(b.start));
              const w = Math.max(2, x(new Date(b.end)) - x0);
              const y = laneY(b.group) + (LANE_H - BAR_H) / 2;
              return (
                <rect key={i} x={x0} y={y} width={w} height={BAR_H} rx={Math.min(4, w / 2)}
                  fill={colorVar(b.group)} fillOpacity={faded(b.group) ? 0.4 : 1}
                  onMouseMove={(e) => tooltip.show(e, <BlockDetail b={b} />)} onMouseLeave={tooltip.hide} />
              );
            })}
            {isToday && x(now) > 0 && x(now) < plotW && (
              <g transform={`translate(${x(now)}, 0)`}>
                <line y1={AXIS_H - 6} y2={height} stroke="var(--ink)" strokeWidth={1.5} />
                <text y={AXIS_H - 9} textAnchor="middle" className="now-label">now</text>
              </g>
            )}
            {done.map((p, i) => {
              const cx = x(new Date(p.at));
              const cy = laneY(DONE) + LANE_H / 2;
              return (
                <g key={i} className={p.url ? "clickable" : undefined}
                  onMouseMove={(e) => tooltip.show(e, <PointDetail p={p} />)} onMouseLeave={tooltip.hide}
                  onClick={() => p.url && openUrl(p.url)}>
                  <circle cx={cx} cy={cy} r={10} fill="transparent" />
                  <circle cx={cx} cy={cy} r={4.5} fill={colorVar(p.group)} stroke="var(--surface)" strokeWidth={2} />
                </g>
              );
            })}
          </g>
        </svg>
      )}
    </div>
  );
}
