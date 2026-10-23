import { getCurrentWindow } from "@tauri-apps/api/window";
import { useRef } from "react";
import { showDay, useDay } from "../api";
import DayClock from "./DayClock";
import "./pill.css";

const SIZE = 112;

/** The floating window: today's day clock, a warning dot when a source fails to sync. */
export default function Pill() {
  const { day, error, syncing } = useDay();
  const down = useRef<{ x: number; y: number; dragged: boolean } | null>(null);

  // Drag anywhere to move it; a click without movement opens the day view.
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

  const failing = day?.sources.filter((s) => s.configured !== false && !s.ok) ?? [];
  const title = error ? "daytrace can't read today's data"
    : !day ? "Loading…"
    : [day.line, syncing && "syncing…", ...failing.map((s) => `sync failed: ${s.name}`)].filter(Boolean).join("\n");

  return (
    <div className="pill" onMouseDown={onMouseDown} onMouseMove={onMouseMove} onMouseUp={onMouseUp}
      title={`${title}\n\nClick for the day view · drag to move`}>
      {day ? <DayClock day={day} size={SIZE} compact /> : <div className="pill-empty muted">{error ? "!" : "…"}</div>}
      {failing.length > 0 && <span className="pill-warn" aria-label={`${failing.length} sources failed to sync`} />}
    </div>
  );
}
