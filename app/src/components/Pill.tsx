import { getCurrentWindow } from "@tauri-apps/api/window";
import { scaleTime } from "d3";
import { useRef } from "react";
import { showDay, useDay } from "../api";
import { GROUP_LABEL, LANES, clock, colorVar } from "../format";
import "./pill.css";

const WIDTH = 432;
const HEIGHT = 8;
// Later lanes paint over earlier ones, so meetings sit on top of coding and so on.
const PAINT_ORDER = [...LANES].reverse();

export default function Pill() {
  const { day, error, syncing } = useDay();
  const down = useRef<{ x: number; y: number; dragged: boolean } | null>(null);

  // Drag anywhere to move the pill; a click without movement opens the day view.
  const onMouseDown = (e: React.MouseEvent) => {
    if (e.button === 0) down.current = { x: e.screenX, y: e.screenY, dragged: false };
  };
  const onMouseMove = (e: React.MouseEvent) => {
    const d = down.current;
    if (d && !d.dragged && Math.hypot(e.screenX - d.x, e.screenY - d.y) > 3) {
      d.dragged = true;
      getCurrentWindow().startDragging();
    }
  };
  const onMouseUp = () => {
    if (down.current && !down.current.dragged) showDay();
    down.current = null;
  };

  if (error || !day) {
    return (
      <div className="pill" onMouseDown={onMouseDown} onMouseMove={onMouseMove} onMouseUp={onMouseUp}>
        <div className="pill-line muted">{error ? "daytrace can't read today's data" : "Loading…"}</div>
      </div>
    );
  }

  const now = new Date(day.now);
  const first = day.summary.first ? new Date(day.summary.first) : now;
  const from = new Date(first);
  from.setMinutes(0, 0, 0);
  from.setHours(Math.min(from.getHours(), 8));
  const to = new Date(now);
  to.setHours(Math.max(now.getHours() + 1, 18), 0, 0, 0);
  const x = scaleTime().domain([from, to]).range([0, WIDTH]).clamp(true);

  const failing = day.sources.filter((s) => s.configured !== false && !s.ok);
  const lanes = PAINT_ORDER.map((g) => day.blocks.filter((b) => b.group === g));

  return (
    <div
      className="pill"
      onMouseDown={onMouseDown}
      onMouseMove={onMouseMove}
      onMouseUp={onMouseUp}
      title="Click for the day view · drag to move"
    >
      <svg width={WIDTH} height={HEIGHT + 6} className="pill-ribbon" aria-label="Today so far">
        <rect x={0} y={3} width={WIDTH} height={HEIGHT} rx={4} fill="var(--pill-track)" />
        {lanes.flat().map((b, i) => {
          const x0 = x(new Date(b.start));
          const w = Math.max(1.5, x(new Date(b.end)) - x0);
          return (
            <rect key={i} x={x0} y={3} width={w} height={HEIGHT} rx={Math.min(3, w / 2)} fill={colorVar(b.group)}>
              <title>{`${GROUP_LABEL[b.group]} ${clock(b.start)}–${clock(b.end)}${b.project ? ` · ${b.project}` : ""}`}</title>
            </rect>
          );
        })}
        <rect x={x(now) - 1} y={0} width={2} height={HEIGHT + 6} rx={1} fill="var(--pill-ink)" />
      </svg>
      <div className="pill-line">
        <span className="pill-text">{day.line}</span>
        <span className="pill-meta tabular">
          {syncing ? "syncing…" : `${clock(from.toISOString())}–${clock(to.toISOString())}`}
          {failing.length > 0 && (
            <span className="pill-warn" title={failing.map((s) => `${s.name}: ${s.error}`).join("\n")}>
              ! {failing.length} source{failing.length > 1 ? "s" : ""}
            </span>
          )}
        </span>
      </div>
    </div>
  );
}
