import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { useCallback, useEffect, useState } from "react";
import type { Day } from "./types";

export const loadDay = (date?: string) => invoke<Day>("day", { date: date ?? null });
export const syncNow = () => invoke<string>("sync_now");
export const showDay = (date?: string) => invoke("show_day", { date: date ?? null });

const REFRESH_MS = 60_000;

/** A day's data, reloaded every minute, after any sync, and when the window regains focus. */
export function useDay(date?: string) {
  const [day, setDay] = useState<Day | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);

  const reload = useCallback(() => {
    loadDay(date).then(
      (d) => { setDay(d); setError(null); },
      (e) => setError(String(e)),
    );
  }, [date]);

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

  return { day, error, syncing, reload };
}
