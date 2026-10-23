// Shape of `daytrace day --json`. Times are local ISO strings with offset.

export type GroupKey = "coord" | "code" | "review" | "writing" | "browse" | "other" | "private";
export type Evidence = "observed" | "scheduled" | "recorded" | "inferred";
export type Sensitivity = "normal" | "private";

export interface Block {
  start: string;
  end: string;
  minutes: number;
  group: GroupKey;
  category: string;
  project: string | null;
  sensitivity: Sensitivity;
  evidence: Evidence;
  source: string;
  titles: string[];
  url: string | null;
}

export interface Point {
  at: string;
  group: GroupKey;
  category: string;
  project: string | null;
  sensitivity: Sensitivity;
  source: string;
  title: string | null;
  url: string | null;
}

export interface Source {
  name: string;
  events: number;
  ok: boolean;
  configured?: boolean;
  error: string | null;
  at: string;
}

export interface ProjectRow {
  project: string;
  minutes: number;
  commits: number;
  prs: number;
  reviews: number;
}

export interface Summary {
  first: string | null;
  last: string | null;
  active_min: number;
  private_min: number;
  idle_min: number;
  groups: Partial<Record<GroupKey, number>>;
  projects: ProjectRow[];
  longest: Focus | null;
  counts: {
    commits: number; prs: number; reviews: number; pushes: number; wiki: number; issues: number;
    meetings: number; meeting_min: number; emails_sent: number; emails_received: number;
  };
}

export interface Day {
  date: string;
  start: string;
  end: string;
  now: string;
  day_zero: string;
  before_tracking: boolean;
  groups: { key: GroupKey; label: string }[];
  sources: Source[];
  summary: Summary;
  line: string;
  blocks: Block[];
  points: Point[];
  idle: { start: string; end: string }[];
  story: Story | null;
  sites: Site[];
  highlights: Highlights;
}

export interface Story {
  narrative: string | null;
  themes: string[];
  model: string;
  generated_at: string;
  dry_run: boolean;
  audit: string | null;
  entries: number;  // projects and activities in the request
}

export interface Site {
  site: string | null;  // null: pages whose title names no site, and sites under two minutes
  minutes: number;
}

/** What the day produced, built locally from stored traces. */
export interface Highlights {
  shipped: { title: string; url: string | null; project: string | null; category: string }[];
  biggest: { title: string; project: string | null; files: number; insertions: number; deletions: number } | null;
  working_on: { project: string | null; branch: string; minutes: number }[];
  focus: Focus | null;
}

/** Longest stretch without interruptions; label is a project, document title, site or app. */
export interface Focus {
  label: string;
  kind: "project" | "document" | "site" | "app";
  minutes: number;
  start: string;
  end: string;
}
