// Shape of `daytrace day --json`. Times are local ISO strings with offset.

export type GroupKey = "meeting" | "comms" | "code" | "review" | "writing" | "browse" | "other" | "private";
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
  request: ReviewRequest | null;  // the built request waiting for review; null once sent
}

/** A week's or month's request to Lumen exactly as it would be sent; the review can only take things out. */
export interface ReviewRequest {
  period: string;
  days: { date: string; active_min: number | null; meeting_min: number | null; first: string | null; last: string | null }[];
  entries: ReviewEntry[];
  also: { episodes?: number; minutes?: number };
  private: { minutes: number };
}

export interface ReviewEntry {
  key: string;
  kind: "project" | "meeting" | "activity";
  project: string | null;
  group: GroupKey | null;
  days: string[];
  minutes: number;
  activities: Record<string, number>;
  counts: Record<string, number>;
  titles: string[];
  sites: Record<string, number>;
  branches: string[];
}

/** What a review took out: a whole entry, or one title, site or branch of it. */
export type Exclusion = { entry: string } | { entry: string; field: "titles" | "sites" | "branches"; value: string };

export interface Site {
  site: string | null;  // null: sites and pages under a minute, together
  page: boolean;  // site is the page's title: it named no site
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

export type PeriodKind = "week" | "month";

/** One day of a week or month; days after today, or with nothing recorded, have has_data false. */
export interface PeriodDay {
  date: string;
  future: boolean;
  has_data: boolean;
  active_min: number;
  meeting_min: number;
  emails_sent: number;
  focus_min: number;
}

/** Shape of `daytrace period week|month`. */
export interface Period {
  start: string;
  end: string;
  today: string;
  days: PeriodDay[];
  elapsed: number;
  line: string;
  totals: {
    active_min: number; avg_active_min: number; meeting_min: number; meetings: number;
    emails_sent: number; emails_received: number;
  };
  focus: (Focus & { date: string }) | null;
  biggest: (NonNullable<Highlights["biggest"]> & { date: string }) | null;
  shipped: (Highlights["shipped"][number] & { date: string })[];
  working_on: Highlights["working_on"];
  projects: ProjectRow[];
  sites: Site[];
  story: Story | null;
}
