import { useCallback, useState, type ReactNode } from "react";

export interface Tip {
  x: number;
  y: number;
  content: ReactNode;
}

/** One floating tooltip per view, positioned at the pointer and kept on screen. */
export function useTooltip() {
  const [tip, setTip] = useState<Tip | null>(null);
  const show = useCallback((e: React.MouseEvent, content: ReactNode) => {
    setTip({ x: e.clientX, y: e.clientY, content });
  }, []);
  const hide = useCallback(() => setTip(null), []);
  return { tip, show, hide };
}

export function Tooltip({ tip }: { tip: Tip | null }) {
  if (!tip) return null;
  const flip = tip.x > window.innerWidth - 280;
  return (
    <div
      className="tooltip"
      role="tooltip"
      style={{
        left: flip ? undefined : tip.x + 14,
        right: flip ? window.innerWidth - tip.x + 14 : undefined,
        top: Math.min(tip.y + 14, window.innerHeight - 140),
      }}
    >
      {tip.content}
    </div>
  );
}
