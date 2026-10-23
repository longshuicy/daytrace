import { arc } from "d3";
import { GROUP_LABEL, LANES, colorVar, minutes } from "../format";
import type { Day } from "../types";
import { BlockDetail, PointDetail } from "./details";
import type { useTooltip } from "./Tooltip";

const SIZE = 300;
const INNER = 58;
const GAP = 3;
const LABEL_ROOM = 34; // hour labels and the outer dot ring

/** 24 hours as a circle, midnight at the top; one ring per activity group, inside out in lane order. */
export default function DayClock({ day, tooltip }: { day: Day; tooltip: ReturnType<typeof useTooltip> }) {
  const rings = LANES.filter((g) => day.blocks.some((b) => b.group === g));
  // Rings share whatever room is left, capped so a quiet day doesn't get fat rings.
  const RING = Math.min(12, (SIZE / 2 - LABEL_ROOM - INNER) / Math.max(1, rings.length) - GAP);
  const start = new Date(day.start).getTime();
  const angle = (iso: string) => ((new Date(iso).getTime() - start) / 86_400_000) * 2 * Math.PI;
  const outer = INNER + rings.length * (RING + GAP);
  const pointR = outer + 8;
  const r = SIZE / 2;
  const path = arc<{ s: number; e: number; i: number }>()
    .innerRadius((d) => INNER + d.i * (RING + GAP))
    .outerRadius((d) => INNER + d.i * (RING + GAP) + RING)
    .startAngle((d) => d.s)
    .endAngle((d) => Math.max(d.e, d.s + 0.012))
    .cornerRadius(3);
  const now = new Date(day.now).getTime();
  const nowAngle = now >= start && now < start + 86_400_000 ? angle(day.now) : null;

  return (
    <svg width={SIZE} height={SIZE} viewBox={`${-r} ${-r} ${SIZE} ${SIZE}`} role="img"
      aria-label="Day clock: 24 hours, one ring per activity">
      <circle r={pointR} fill="none" stroke="var(--grid)" />
      {rings.map((g, i) => (
        <circle key={g} r={INNER + i * (RING + GAP) + RING / 2} fill="none" stroke="var(--surface-2)" strokeWidth={RING} />
      ))}
      {[0, 6, 12, 18].map((h) => {
        const a = (h / 24) * 2 * Math.PI;
        const lr = pointR + 14;
        return (
          <g key={h}>
            <line x1={Math.sin(a) * (INNER - 6)} y1={-Math.cos(a) * (INNER - 6)}
              x2={Math.sin(a) * pointR} y2={-Math.cos(a) * pointR} stroke="var(--grid)" />
            <text x={Math.sin(a) * lr} y={-Math.cos(a) * lr + 4} textAnchor="middle" className="axis-label tabular">
              {String(h).padStart(2, "0")}
            </text>
          </g>
        );
      })}
      {day.blocks.map((b, k) => {
        const i = rings.indexOf(b.group);
        if (i < 0) return null;
        return (
          <path key={k} d={path({ s: angle(b.start), e: angle(b.end), i }) ?? ""} fill={colorVar(b.group)}
            onMouseMove={(e) => tooltip.show(e, <BlockDetail b={b} />)} onMouseLeave={tooltip.hide}>
            <title>{GROUP_LABEL[b.group]}</title>
          </path>
        );
      })}
      {day.points.map((p, k) => {
        const a = angle(p.at);
        return (
          <circle key={k} cx={Math.sin(a) * pointR} cy={-Math.cos(a) * pointR} r={4} fill={colorVar(p.group)}
            stroke="var(--surface)" strokeWidth={2}
            onMouseMove={(e) => tooltip.show(e, <PointDetail p={p} />)} onMouseLeave={tooltip.hide} />
        );
      })}
      {nowAngle !== null && (
        <line x1={0} y1={0} x2={Math.sin(nowAngle) * (pointR + 4)} y2={-Math.cos(nowAngle) * (pointR + 4)}
          stroke="var(--ink)" strokeWidth={1.5} strokeLinecap="round" opacity={0.7} />
      )}
      <circle r={INNER - 10} fill="var(--surface)" />
      <text y={2} textAnchor="middle" className="clock-value">{minutes(day.summary.work_min)}</text>
      <text y={20} textAnchor="middle" className="clock-caption">work</text>
    </svg>
  );
}
