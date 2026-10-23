// Shape of `daytrace day --json`. Times are local ISO strings with offset.

export type GroupKey = "coord" | "code" | "ai" | "review" | "writing" | "browse" | "other" | "nonwork" | "unknown";
export type Evidence = "observed" | "scheduled" | "recorded" | "inferred";
export type Sensitivity = "work" | "personal" | "unknown";

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
  work_min: number;
  nonwork_min: number;
  unknown_min: number;
  idle_min: number;
  groups: Partial<Record<GroupKey, number>>;
  projects: ProjectRow[];
  longest: { project: string; minutes: number; start: string; end: string } | null;
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
  episodes: Episode[];
}

export interface Story {
  narrative: string | null;
  themes: string[];
  model: string;
  generated_at: string;
  dry_run: boolean;
  audit: string | null;
}

export interface Episode {
  id: string;
  start: string;
  end: string;
  kind: "meeting" | "project" | "activity";
  project: string | null;
  group: GroupKey;
  minutes: number;
  label: string | null;
  counts: Record<string, number>;
}
