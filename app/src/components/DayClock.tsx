import { arc } from "d3";
import { GROUP_LABEL, LANES, clock, colorVar, minutes } from "../format";
import type { Day, GroupKey } from "../types";
import { BlockDetail, PointDetail } from "./details";
import type { useTooltip } from "./Tooltip";

const HOURS = Array.from({ length: 24 }, (_, h) => h);
const hoursApart = (a: number, b: number) => Math.min(Math.abs(a - b), 24 - Math.abs(a - b));

interface Props {
  day: Day;
  /** Hover details; without it (the floating clock) bars only get a native title. */
  tooltip?: ReturnType<typeof useTooltip>;
  size?: number;
  /** Small version for the floating window: one ring, meetings painted on top, no hour labels. */
  compact?: boolean;
}

/** 24 hours as a circle, midnight at the top; one ring per activity group, inside out in lane order. */
export default function DayClock({ day, tooltip, size = 300, compact = false }: Props) {
  const inner = compact ? size * 0.31 : 58;
  const gap = 3;
  const labelRoom = compact ? 8 : 34; // hour labels and the outer dot ring
  const rings = LANES.filter((g) => day.blocks.some((b) => b.group === g));
  const ringOf = (g: GroupKey) => (compact ? 0 : rings.indexOf(g));
  // Rings share whatever room is left, capped so a quiet day doesn't get fat rings.
  const RING = compact ? size / 2 - labelRoom - inner - 6
    : Math.min(12, (size / 2 - labelRoom - inner) / Math.max(1, rings.length) - gap);
  // Later lanes paint first, so meetings sit on top of coding and so on in the single compact ring.
  const blocks = compact ? [...day.blocks].sort((a, b) => LANES.indexOf(b.group) - LANES.indexOf(a.group)) : day.blocks;
  const start = new Date(day.start).getTime();
  const angle = (iso: string) => ((new Date(iso).getTime() - start) / 86_400_000) * 2 * Math.PI;
  const outer = inner + (compact ? 1 : rings.length) * (RING + gap);
  const pointR = outer + (compact ? 4 : 8);
  const r = size / 2;
  const path = arc<{ s: number; e: number; i: number }>()
    .innerRadius((d) => inner + d.i * (RING + gap))
    .outerRadius((d) => inner + d.i * (RING + gap) + RING)
    .startAngle((d) => d.s)
    .endAngle((d) => Math.max(d.e, d.s + 0.012))
    .cornerRadius(compact ? 1.5 : 3);
  const now = new Date(day.now).getTime();
  const nowAngle = now >= start && now < start + 86_400_000 ? angle(day.now) : null;
  const nowHour = nowAngle === null ? null : (nowAngle / (2 * Math.PI)) * 24;

  return (
    <svg width={size} height={size} viewBox={`${-r} ${-r} ${size} ${size}`} role="img"
      aria-label="Day clock: 24 hours, one ring per activity">
      <circle r={pointR} fill="none" stroke="var(--grid)" />
      {(compact ? rings.slice(0, 1) : rings).map((g, i) => (
        <circle key={g} r={inner + i * (RING + gap) + RING / 2} fill="none" stroke="var(--surface-2)" strokeWidth={RING} />
      ))}
      {HOURS.map((h) => {
        const a = (h / 24) * 2 * Math.PI;
        const lr = pointR + 14;
        const major = h % 6 === 0;
        // A tick per hour outside the ring, longer every 3 hours; a label every 3 hours unless "now" sits there.
        const tick = compact ? (major ? 4 : 2) : h % 3 === 0 ? 5 : 3;
        const label = !compact && h % 3 === 0 && (nowHour === null || hoursApart(h, nowHour) > 0.75);
        return (
          <g key={h}>
            {major && (
              <line x1={Math.sin(a) * (compact ? outer : inner - 6)} y1={-Math.cos(a) * (compact ? outer : inner - 6)}
                x2={Math.sin(a) * pointR} y2={-Math.cos(a) * pointR} stroke="var(--grid)" />
            )}
            <line x1={Math.sin(a) * pointR} y1={-Math.cos(a) * pointR}
              x2={Math.sin(a) * (pointR + tick)} y2={-Math.cos(a) * (pointR + tick)}
              stroke={major ? "var(--muted)" : "var(--axis)"} />
            {label && (
              <text x={Math.sin(a) * lr} y={-Math.cos(a) * lr + 4} textAnchor="middle"
                className={`axis-label tabular${major ? " major" : ""}`}>
                {String(h).padStart(2, "0")}
              </text>
            )}
          </g>
        );
      })}
      {!compact && nowAngle !== null && (
        <text x={Math.sin(nowAngle) * (pointR + 14)} y={-Math.cos(nowAngle) * (pointR + 14) + 4} textAnchor="middle"
          className="now-label tabular">{clock(day.now)}</text>
      )}
      {blocks.map((b, k) => {
        const i = ringOf(b.group);
        if (i < 0) return null;
        return (
          <path key={k} d={path({ s: angle(b.start), e: angle(b.end), i }) ?? ""} fill={colorVar(b.group)}
            fillOpacity={compact && (b.group === "other" || b.group === "private") ? 0.5 : 1}
            onMouseMove={tooltip && ((e) => tooltip.show(e, <BlockDetail b={b} />))} onMouseLeave={tooltip?.hide}>
            {!tooltip && <title>{`${GROUP_LABEL[b.group]} ${clock(b.start)}–${clock(b.end)}`}</title>}
          </path>
        );
      })}
      {day.points.map((p, k) => {
        const a = angle(p.at);
        return (
          <circle key={k} cx={Math.sin(a) * pointR} cy={-Math.cos(a) * pointR} r={compact ? 2 : 4} fill={colorVar(p.group)}
            stroke="var(--surface)" strokeWidth={compact ? 1 : 2}
            onMouseMove={tooltip && ((e) => tooltip.show(e, <PointDetail p={p} />))} onMouseLeave={tooltip?.hide} />
        );
      })}
      {nowAngle !== null && (
        <line x1={compact ? Math.sin(nowAngle) * inner : 0} y1={compact ? -Math.cos(nowAngle) * inner : 0} x2={Math.sin(nowAngle) * (pointR + 4)} y2={-Math.cos(nowAngle) * (pointR + 4)}
          stroke="var(--ink)" strokeWidth={1.5} strokeLinecap="round" opacity={0.7} />
      )}
      <circle r={inner - (compact ? 3 : 10)} fill={compact ? "transparent" : "var(--surface)"} />
      {compact ? (
        <text y={4} textAnchor="middle" className="clock-value compact">{minutes(day.summary.active_min)}</text>
      ) : (
        <>
          <text y={2} textAnchor="middle" className="clock-value">{minutes(day.summary.active_min)}</text>
          <text y={20} textAnchor="middle" className="clock-caption">active</text>
        </>
      )}
    </svg>
  );
}
