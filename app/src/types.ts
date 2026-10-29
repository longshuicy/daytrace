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
  /** Meeting notes taken in this meeting (calendar blocks only). */
  notes?: Note[];
}

export interface Note {
  /** Granola's id; `daytrace note ID` returns the saved copy. */
  id: string;
  title: string;
  /** Granola's page for the note. */
  url: string | null;
  /** The Markdown copy in ~/.daytrace/notes/granola/. */
  path: string;
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
  /** Granola notes that matched no meeting carry the note's id. */
  id?: string;
}

export interface SavedNote extends Note {
  meeting_at: string;
  markdown: string;
}

export interface Source {
  name: string;
  events: number;
  ok: boolean;
  configured?: boolean;
  error: string | null;
  warning?: string | null;  // synced, but the source itself looks broken (e.g. a stopped watcher)
  at: string;
}

export interface ProjectRow {
  project: string | null;  // null is "Other": time no project claims
  minutes: number;
  inferred_min: number;    // part of minutes borrowed from an agent session or the activity around it
  parts?: Partial<Record<"meeting" | "comms" | "untagged", number>>;  // Other only: why no project claims it
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
  /** Size of the database file and the size at which the app asks for a backup. */
  storage: { bytes: number; limit: number };
}

export interface Observation {
  kind: "open_loop" | "recurring" | "time_vs_weight" | "pattern";
  text: string;
  evidence: string;
}

/** A week's draft todo list for the next one, grouped by project. */
export interface TodoGroup {
  project: string;
  todos: { text: string; why: string }[];
}

export interface Story {
  narrative: string | null;
  themes: string[];
  observations: Observation[];
  next_week: TodoGroup[];
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
  meetings: ReviewMeeting[];
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

/** A meeting's note as the request has it: topics and action items, people as "[person]". */
export interface ReviewMeeting {
  key: string;
  day: string;
  time: string;
  title: string | null;
  topics: string[];
  mine: string[];
  waiting: string[];
  unassigned: string[];
}

export type MeetingField = "topics" | "mine" | "waiting" | "unassigned";

/** What a review took out: a whole entry, or one title, site or branch of it; a whole meeting's notes, or one
 * topic or action item of them. */
export type Exclusion = { entry: string } | { entry: string; field: "titles" | "sites" | "branches"; value: string }
  | { meeting: string } | { meeting: string; field: MeetingField; value: string };

export interface Site {
  site: string | null;  // null: sites and pages under a minute, together
  page: boolean;  // site is the page's title: it named no site
  minutes: number;
}

/** What the day produced, built locally from stored traces. */
export interface Highlights {
  shipped: { title: string; url: string | null; project: string | null; category: string }[];
  commits: Commits | null;
  working_on: { project: string | null; branch: string; minutes: number; commits: number; trunk: boolean }[];
  focus: Focus | null;
}

/** All of the commits in a day or period, added up. */
export interface Commits {
  count: number;
  /** Included in count; private commits have no project, files or lines. */
  private: number;
  files: number;
  insertions: number;
  deletions: number;
  projects: string[];
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
    active_min: number; avg_active_min: number; avg_active_min_all: number; workdays: number; data_days: number;
    meeting_min: number; meetings: number;
    emails_sent: number; emails_received: number;
  };
  focus: (Focus & { date: string }) | null;
  commits: Commits | null;
  shipped: (Highlights["shipped"][number] & { date: string })[];
  working_on: Highlights["working_on"];
  projects: ProjectRow[];
  sites: Site[];
  story: Story | null;
}
