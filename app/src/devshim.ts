// Browser-only preview (`npm run dev` outside Tauri): fake the desktop shell with Tauri's IPC mocks.
// Data comes from dev/dev-day.json (`daytrace day --json > dev/dev-day.json`) and dev/dev-week.json /
// dev/dev-month.json (`daytrace period week > dev/dev-week.json`, same for month). That folder is gitignored
// and this module is only loaded in dev, so the fixture never reaches a build.
import { mockIPC, mockWindows } from "@tauri-apps/api/mocks";

const fixtures = import.meta.glob("../dev/dev-*.json", { import: "default" });
const fixture = async (name: string) => {
  const load = fixtures[`../dev/dev-${name}.json`];
  return load ? await load() : null;
};

export async function installDevShim() {
  const label = new URLSearchParams(location.search).get("window") ?? "main";
  mockWindows(label);
  const [day, week, month] = await Promise.all(["day", "week", "month"].map(fixture));
  mockIPC((cmd, args) => {
    if (cmd === "day") return day;
    if (cmd === "period") return (args as { kind: string }).kind === "week" ? week : month;
    if (cmd === "sync_now") return "";
    return null;
  }, { shouldMockEvents: true });
}
