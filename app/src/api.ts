import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { useCallback, useEffect, useState } from "react";
import type { Day, Exclusion, Period, PeriodKind } from "./types";

export const loadDay = (date?: string) => invoke<Day>("day", { date: date ?? null });
export const loadPeriod = (kind: PeriodKind, date: string) => invoke<Period>("period", { kind, date });
export const syncNow = () => invoke<string>("sync_now");
/** Build the week's or month's request for review (send false), or send the reviewed request minus exclusions. */
export const summarizePeriod = (kind: PeriodKind, date: string, send: boolean, exclude: Exclusion[] = []) =>
  invoke<string>("summarize", { date, send, period: kind, exclude: send ? JSON.stringify(exclude) : null });
/** Copy the database to ~/.daytrace/backups; with startOver, then keep only today. */
export const backup = (startOver: boolean) => invoke<string>("backup", { startOver });
export const showDay = (date?: string) => invoke("show_day", { date: date ?? null });

const REFRESH_MS = 60_000;

/** A day's data, reloaded every minute, after any sync, and when the window regains focus. */
export function useDay(date?: string) {
  const { data: day, error, syncing, reload } = useLive(useCallback(() => loadDay(date), [date]));
  return { day, error, syncing, reload };
}

/** A week's or month's data, refreshed like useDay. */
export function usePeriod(kind: PeriodKind, date: string) {
  return useLive(useCallback(() => loadPeriod(kind, date), [kind, date]));
}

function useLive<T>(load: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);

  const reload = useCallback(() => {
    load().then(
      (d) => { setData(d); setError(null); },
      (e) => setError(String(e)),
    );
  }, [load]);

  useEffect(() => {
    reload();
    const timer = window.setInterval(reload, REFRESH_MS);
    window.addEventListener("focus", reload);
    const offs = [
      listen("syncing", () => setSyncing(true)),
      listen("synced", () => { setSyncing(false); reload(); }),
    ];
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", reload);
      offs.forEach((p) => p.then((off) => off()));
    };
  }, [reload]);

  return { data, error, syncing, reload };
}
