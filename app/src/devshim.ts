// Browser-only preview (`npm run dev` outside Tauri): fake the desktop shell with Tauri's IPC mocks.
// Data comes from dev/dev-day.json (`daytrace day --json > dev/dev-day.json`). That folder is gitignored
// and this module is only loaded in dev, so the fixture never reaches a build.
import { mockIPC, mockWindows } from "@tauri-apps/api/mocks";

const fixtures = import.meta.glob("../dev/dev-day.json", { import: "default" });

export async function installDevShim() {
  const label = new URLSearchParams(location.search).get("window") ?? "main";
  mockWindows(label);
  const load = Object.values(fixtures)[0];
  const day = load ? await load() : null;
  mockIPC((cmd) => {
    if (cmd === "day") return day;
    if (cmd === "sync_now") return "";
    return null;
  }, { shouldMockEvents: true });
}
