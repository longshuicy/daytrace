import type { GroupKey } from "./types";

export function minutes(m: number): string {
  const total = Math.round(m);
  const h = Math.floor(total / 60);
  const rest = total % 60;
  if (!h) return `${rest}m`;
  return rest ? `${h}h ${String(rest).padStart(2, "0")}m` : `${h}h`;
}

export const clock = (iso: string) =>
  new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

export const longDate = (isoDate: string) =>
  new Date(`${isoDate}T12:00:00`).toLocaleDateString([], { weekday: "long", month: "long", day: "numeric" });

export function shiftDate(isoDate: string, days: number): string {
  const d = new Date(`${isoDate}T12:00:00`);
  d.setDate(d.getDate() + days);
  return d.toLocaleDateString("en-CA");
}

export const today = () => new Date().toLocaleDateString("en-CA");

export function ago(iso: string, now = new Date()): string {
  const s = Math.max(0, (now.getTime() - new Date(iso).getTime()) / 1000);
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export const GROUP_LABEL: Record<GroupKey, string> = {
  meeting: "Meetings",
  comms: "Mail & chat",
  code: "Coding",
  review: "Review & issues",
  writing: "Docs & wiki",
  browse: "Browsing",
  other: "Other",
  private: "Private",
};

/** Lane order, top to bottom, following the palette order. */
export const LANES: GroupKey[] = ["meeting", "comms", "code", "review", "writing", "browse", "other", "private"];

export const colorVar = (g: GroupKey) => `var(--g-${g})`;

export const SOURCE_LABEL: Record<string, string> = {
  activitywatch: "ActivityWatch",
  git: "Local git",
  claude_code: "Claude Code",
  cursor: "Cursor",
  github: "GitHub",
  confluence: "Wiki",
  outlook_calendar: "Outlook calendar",
  outlook_mail: "Outlook mail",
  granola: "Granola",
};

export const CATEGORY_LABEL: Record<string, string> = {
  private: "Private",
  meeting: "Meeting",
  email: "Email sent",
  commit: "Commit",
  push: "Push",
  pr: "Pull request",
  review: "Review",
  issue: "Issue",
  release: "Release",
  repo: "Branch / repo",
  wiki: "Wiki",
  notes: "Meeting notes",
};
