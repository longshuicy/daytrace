import { openUrl } from "@tauri-apps/plugin-opener";
import { scaleTime, timeHour } from "d3";
import { useLayoutEffect, useRef, useState } from "react";
import { GROUP_LABEL, LANES, SOURCE_LABEL, colorVar } from "../format";
import type { Block, Day, GroupKey } from "../types";
import { BlockDetail, PointDetail } from "./details";
import type { useTooltip } from "./Tooltip";

const LABEL_W = 150;
const LANE_H = 36;
const BAR_H = 14;
const AXIS_H = 28;
const NOW_H = 22;  // room under the lanes for the "now" label
const BRIDGE_MIN = 3;  // bars in one lane closer than this (or 4px) are drawn as one stretch
const DONE = "done" as const;
const WORK_START = 8;  // default window, hours
const WORK_END = 18;

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
      notes: run.flatMap((b) => b.notes ?? []),
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
  setOpenNote: (id: string) => void;
}

/** A "Done" row of single traces (commits, PRs, mail), then one lane per activity group with spans joined into
 *  stretches. Lanes can overlap in time (browsing during a meeting). */
export default function Timeline({ day, fullDay, tooltip, setOpenNote }: Props) {
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
  const dayStart = new Date(day.start);
  const dayEnd = new Date(day.end);
  const now = new Date(day.now);
  const isToday = now >= dayStart && now < dayEnd;

  // Work hours (08–18) unless the full day is asked for; anything outside the window is left out.
  const from = fullDay ? dayStart : new Date(Math.max(+new Date(dayStart).setHours(WORK_START), +dayStart));
  const to = fullDay ? dayEnd : new Date(Math.min(+new Date(dayStart).setHours(WORK_END), +dayEnd));
  const inView = (s: string, e: string = s) => +new Date(e) >= +from && +new Date(s) <= +to;
  const plotW = Math.max(670, width - LABEL_W - 14);
  const x = scaleTime().domain([from, to]).range([0, plotW]).clamp(true);
  const hours = (to.getTime() - from.getTime()) / 3_600_000;
  const ticks = x.ticks(timeHour.every(hours > 14 || plotW < 640 ? 2 : 1)!);
  const bridgeMs = Math.max(BRIDGE_MIN * 60_000, x.invert(4).getTime() - x.invert(0).getTime());
  const bars = LANES.flatMap((g) => stretches(day.blocks.filter((b) => b.group === g), bridgeMs))
    .filter((b) => inView(b.start, b.end));
  const shown = done.filter((p) => inView(p.at));
  // Only lanes with something in the window, so a quiet lane doesn't sit empty at work hours.
  const lanes: Row[] = [
    ...(shown.length ? [DONE] : []),
    ...LANES.filter((g) => bars.some((b) => b.group === g)),
  ];
  const lanesBottom = AXIS_H + lanes.length * LANE_H;
  const height = lanesBottom + NOW_H;
  const laneY = (g: Row) => AXIS_H + lanes.indexOf(g) * LANE_H;

  return (
    <div ref={ref} className="timeline">
      {lanes.length === 0 ? (
        <div className="empty">
          {fullDay || !(day.blocks.length || done.length) ? "No traces for this day." : `Nothing between ${WORK_START}:00 and ${WORK_END}:00. Turn on Full 24 hours to see the rest.`}
        </div>
      ) : (
        <svg width={Math.max(width, LABEL_W + plotW + 14)} height={height} role="img" aria-label="Timeline of the day by activity">
          {lanes.map((g) => (
            <g key={g} transform={`translate(0, ${laneY(g)})`}>
              {g === DONE
                ? <circle cx={5} cy={LANE_H / 2} r={5} fill="var(--ink-2)" />
                : <rect x={0} y={LANE_H / 2 - 5} width={10} height={10} rx={3} fill={colorVar(g)} />}
              <text x={20} y={LANE_H / 2 + 5} className="lane-label">{g === DONE ? "Done" : GROUP_LABEL[g]}</text>
            </g>
          ))}
          <g transform={`translate(${LABEL_W}, 0)`}>
            {day.idle.map((iv, i) => (
              <rect key={i} x={x(new Date(iv.start))} y={AXIS_H} height={lanesBottom - AXIS_H}
                width={Math.max(0, x(new Date(iv.end)) - x(new Date(iv.start)))} fill="var(--ink)" fillOpacity={0.035}>
                <title>Away from keyboard</title>
              </rect>
            ))}
            {ticks.map((t) => (
              <g key={+t} transform={`translate(${x(t)}, 0)`}>
                <line y1={AXIS_H} y2={lanesBottom} stroke="var(--grid)" />
                <text y={16} textAnchor="middle" className="axis-label tabular">
                  {t.getHours().toString().padStart(2, "0")}
                </text>
              </g>
            ))}
            {lanes.map((g) => (
              <line key={g} x1={0} x2={plotW} y1={laneY(g) + LANE_H} y2={laneY(g) + LANE_H} stroke="var(--divider)" />
            ))}
            {bars.map((b, i) => {
              const x0 = x(new Date(b.start));
              const w = Math.max(2, x(new Date(b.end)) - x0);
              const y = laneY(b.group) + (LANE_H - BAR_H) / 2;
              const note = b.notes?.[0];
              return (
                <rect key={i} x={x0} y={y} width={w} height={BAR_H} rx={Math.min(4, w / 2)}
                  fill={colorVar(b.group)} className={note ? "clickable" : undefined}
                  onClick={() => note && setOpenNote(note.id)}
                  onMouseMove={(e) => tooltip.show(e, <BlockDetail b={b} />)} onMouseLeave={tooltip.hide} />
              );
            })}
            {isToday && x(now) > 0 && x(now) < plotW && (
              <g transform={`translate(${x(now)}, 0)`}>
                <line y1={AXIS_H - 4} y2={lanesBottom + 4} stroke="var(--ink)" strokeWidth={2} />
                <text y={lanesBottom + 18} textAnchor="middle" className="now-label">now</text>
              </g>
            )}
            {shown.map((p, i) => {
              const cx = x(new Date(p.at));
              const cy = laneY(DONE) + LANE_H / 2;
              return (
                <g key={i} className={p.url || p.id ? "clickable" : undefined}
                  onMouseMove={(e) => tooltip.show(e, <PointDetail p={p} />)} onMouseLeave={tooltip.hide}
                  onClick={() => p.id ? setOpenNote(p.id) : p.url && openUrl(p.url)}>
                  <circle cx={cx} cy={cy} r={10} fill="transparent" />
                  <circle cx={cx} cy={cy} r={5} fill={colorVar(p.group)} />
                </g>
              );
            })}
          </g>
        </svg>
      )}
    </div>
  );
}
