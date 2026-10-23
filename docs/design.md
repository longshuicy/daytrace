# daytrace — design

_Status: draft for discussion · 2026-10-22_

> "Where did my Wednesday go?"

daytrace is a personal, local-first desktop tool that quietly collects the traces my **work** leaves behind
(meetings, email, commits, docs, wiki edits, focused apps) and reconstructs what each day actually looked like:
a timeline, honest statistics, and an LLM-written description of the day.

## 1. Principles

1. **Local-first.** All data lives on this Mac. The only thing that leaves is scrubbed day payloads to Lumen.
2. **Known vs. inferred.** Every fact carries its evidence level. Calendar meetings are taken as
   attended: checking attendance isn't worth the complexity. An email timestamp means *sent at*, not *time spent writing*. LLM output is always *inferred* and links back to
   the events it came from.
3. **Not a productivity tool.** No scores, no streaks, no goals. It describes; it doesn't judge.
4. **Overlap is normal.** Browsing during a meeting is not a bug. Categories don't need to sum to 24 hours.
5. **Private by list, not by guess.** Sorting work from personal proved unworkable. Instead a short `[private]`
   list (apps, titles, folders, browser profiles) marks what is personal; that is kept as time only and never
   sent. Everything else is plain activity. The dry-run audit is the check on what would leave.

## 2. Scope (v1)

- **Work only.** No music, fitness, photos, video.
- **Forward only.** Tracking starts at install (`day_zero`). No historical backfill. Adapters do catch up on
  gaps *after* day zero (e.g. laptop was asleep), since the remote APIs keep that history.
- **Single user, single Mac.**

## 3. Sources

| Source | Access | Cursor | What we keep | Work? |
|---|---|---|---|---|
| ActivityWatch (window + AFK) | local REST `localhost:5600` | last event timestamp | app, categorized title, active/idle spans | by rules (§6) |
| Outlook calendar | Microsoft Graph `Calendars.Read` | delta token | subject, start/end, organizer, attendee count, response status | always |
| Outlook mail | Microsoft Graph `Mail.ReadBasic` | delta token | sent/received time, subject, counterpart domains, thread id — **never bodies** | always |
| GitHub | REST, fine-grained read-only token | `since` timestamp | commits, PRs, reviews, issues, comments (titles + links) | allowlisted orgs/repos |
| Local git | scan `~/Documents/Projects/**/.git` | last seen commit per repo | commit time, repo, message subject, files-changed count | path rules |
| Claude Code | `~/.claude/projects/*` session files | file offset | session start/end, project path, turn count — **not prompt content** | path rules |
| Confluence (+ Jira if used) | Atlassian REST, read-only token | `lastModified` | page/issue created/edited/commented, titles, space | always |
| Google Drive | Drive Activity API, `illinois.edu` account only | timestamp | doc edited/created/commented, title, type (Doc/Sheet/Slides) | always |

**Risk to verify first:** Microsoft Graph may require tenant admin consent for UIUC. Fallbacks: published ICS
calendar URL for meetings; mail becomes optional.

Browser extension for ActivityWatch: **not installed in v1.** Window titles give enough context.

## 4. Architecture

```
┌───────────────────────── this Mac ─────────────────────────────────────────┐
│                                                                            │
│  ActivityWatch ─┐                                                          │
│  Graph/GitHub/  ├─► adapters ─► intake transform ─► SQLite ─► analysis ─┐  │
│  Atlassian/     │   (pull,      (classify work,     (events,   (stats,  │  │
│  Drive/git/CC  ─┘   cursors)    categorize, redact)  episodes)  episodes)│  │
│                                                                    │     │  │
│        collector daemon (Python, launchd, every N min)             ▼     │  │
│                                                          egress gate ────┼──► Lumen (work only)
│                                                                    │     │  │
│  Tauri app: menu-bar icon · floating pill · day/week view ◄────────┘     │  │
└────────────────────────────────────────────────────────────────────────────┘
```

- **Collector daemon**: Python 3.12, run by `launchd`, independent of the UI so collection continues when the app
  is closed. Each adapter is a small module: `fetch(since) -> list[RawEvent]`.
- **Store**: everything lives under `~/.daytrace/` (`daytrace.db`, `rules.toml`, `.env`, `logs/`, `audit/`).
  `DAYTRACE_HOME` overrides the location.
- **Analysis**: Python (DuckDB/pandas over SQLite) for deterministic stats and episode building.
- **LLM**: Lumen via OpenAI-compatible API, behind the egress gate (§8).
- **Desktop UI**: Tauri + React + D3.
- **Secrets**: `~/.daytrace/.env` (chmod 600, outside the repo), loaded at startup. `launchd` doesn't inherit the
  shell environment, so a file is simpler than exported variables. Real environment variables override it.

## 5. Event model

```sql
CREATE TABLE events (
  id          TEXT PRIMARY KEY,        -- stable hash of (source, source_id)
  source      TEXT NOT NULL,           -- 'activitywatch' | 'outlook_cal' | 'github' | ...
  source_id   TEXT,                    -- id in the upstream system
  kind        TEXT NOT NULL,           -- 'point' | 'span' | 'plan'
  evidence    TEXT NOT NULL,           -- 'observed' | 'scheduled' | 'recorded' | 'inferred'
  start_at    TEXT NOT NULL,           -- UTC ISO-8601
  end_at      TEXT,                    -- null for points
  category    TEXT NOT NULL,           -- 'meeting' | 'email' | 'code' | 'docs' | 'wiki' | 'review' | 'browse' | 'idle' | ...
  project     TEXT,                    -- normalized project key, e.g. 'incore', 'pdg'
  title       TEXT,                    -- redacted, may be null
  url         TEXT,                    -- link back to source (query string stripped)
  sensitivity TEXT NOT NULL,           -- 'normal' | 'private'
  attrs       TEXT,                    -- JSON, source-specific extras
  ingested_at TEXT NOT NULL
);

CREATE TABLE episodes (                -- built by analysis, regenerable
  id TEXT PRIMARY KEY, day TEXT, start_at TEXT, end_at TEXT,
  project TEXT, categories TEXT, sensitivity TEXT,
  label TEXT,                          -- LLM-generated, evidence = inferred
  event_ids TEXT                       -- JSON array, provenance
);

CREATE TABLE day_summaries (day TEXT PRIMARY KEY, stats TEXT, narrative TEXT, model TEXT, generated_at TEXT);
CREATE TABLE cursors (source TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);   -- day_zero, schema_version
```

## 6. Intake transformation

Runs on every raw event **before** it is written to `events`.

1. **Drop** private/incognito windows entirely (not stored at all).
2. **Private check** with `[private]` in `~/.daytrace/rules.toml`:

   ```toml
   [private]
   apps = ["Messages", "WhatsApp", "FaceTime", "Photos"]
   titles = ["(?i)\\bbank", "(?i)gmail\\.com"]
   paths = []              # folders whose commits, sessions and windows are private
   browser_profiles = []   # Chrome profile names from window titles
   drop = ["(?i)private browsing", "(?i)incognito"]
   ```

   A match makes the event `private`: it keeps only its timing, with category `private` and no app, title, URL
   or project. GitHub events are checked against the path of their local clone.
3. **Categorize** everything else: map app + title → `category`, and path → `project` (the top-level folder under
   the most specific root in `[paths] projects`).
4. **Scrub**: titles pass a secret scrubber (tokens, keys, long hex/base64, `password=`). URLs lose query strings.
5. **Raw retention**: ActivityWatch raw buckets older than **7 days** are deleted after they've been ingested
   (configurable). daytrace keeps only the transformed events.

## 7. Analysis (deterministic, no LLM)

Per day / week:

- Time by category (overlap-aware: union of spans, not sum)
- Meeting load: count, total hours, back-to-back chains
- Focus blocks: contiguous ≥ 25 min in one project with no meeting and < N app switches
- Context switches per hour; project switches per day
- First / last active time; idle gaps
- Making vs. coordinating ratio (code/docs/wiki vs. meeting/email)
- Projects touched, with time per project
- Data completeness: which sources reported, gaps (laptop off ≠ idle)

**Episodes**: cluster non-private events by time proximity (gap < 10 min) and shared project/repo/doc/people.

## 8. LLM (Lumen) and the egress gate

**Endpoint**: OpenAI-compatible; `base_url` + `model` in config, API key in Keychain.

**Egress gate** — the only code path that can talk to Lumen:

1. Episodes are built from non-private events. Private time is replaced by an opaque block:
   `{"start": "12:00", "end": "12:40", "minutes": 40}`.
2. Project to an allowlisted field set: `start_at, end_at, category, project, title, source`. Nothing else.
3. Run the secret scrubber again on the final payload.
4. Write the exact payload to `audit/YYYY-MM-DD/*.json` before sending.
5. **Dry-run mode** (default for the first weeks): build + audit, don't send.

**LLM tasks**:

- *Day narrative*: one paragraph + 2–4 themes, given per-project entries + stats. No per-entry labels. The prompt
  forbids inventing time spent, and names meetings without saying what happened in them.
- *Week / month narrative*: the same shape, from the days' already-gated payloads merged per project (days it
  appeared, summed minutes and counts, up to three titles). Per-day totals replace time ranges; private time is
  one total. Withheld once any event it used turns private.

All outputs are stored with `evidence = 'inferred'`, the model name, and the episode/event ids used.

## 9. Desktop app (Tauri)

- **Menu-bar icon** — always present; shows sync status.
- **Floating clock** — tiny always-on-top day clock in the bottom-right corner, toggleable; the summary line is
  its tooltip. Click to open the day view.
- **Day view** — Lumen's narrative on top, stats, one activity card that switches between timeline, day clock and
  a sortable table, projects, top sites. Source status shows only as an alert when a sync fails.
- **Week view** — stacked categories, meeting load, focus blocks.
- **Settings** — sources on/off, rules editor, retention, Lumen dry-run toggle, audit log viewer.

## 10. Later (separate tasks)

- **Website export**: aggregate-only JSON (no titles, no people), manual review, interactive visuals on the
  personal site (day clock, week river, context-switch graph, year heatmap).

## 11. Security & privacy checklist

- [ ] Read-only scopes everywhere
- [ ] Tokens only in `~/.daytrace/.env` (chmod 600), never in the repo
- [ ] No telemetry, no third-party analytics libs in the UI
- [ ] Egress gate is the single network path to Lumen; covered by tests
- [ ] Private browsing dropped at intake
- [ ] Private events never leave the machine except as bare time ranges
- [ ] Raw ActivityWatch data purged after retention window
- [ ] `daytrace forget --from --to` deletes a period everywhere (events, episodes, summaries, audit)

## 12. Milestones

1. **Skeleton** — repo layout, SQLite schema, rules engine, launchd daemon, `day_zero`.
2. **First traces** — ActivityWatch + local git + Claude Code adapters (all local, no auth). CLI `daytrace today`.
3. **Work systems** — GitHub, Outlook calendar (verify Graph consent early), Confluence, Drive.
4. **Analysis** — stats + episodes; CLI report.
5. **Lumen** — egress gate, dry-run, audit, episode labels, day narrative.
6. **Desktop** — Tauri tray + floating pill + day view.

## 13. Open questions

- Does UIUC's Microsoft 365 tenant allow user consent for `Calendars.Read` / `Mail.ReadBasic`?
- Lumen: base URL, model name, embeddings endpoint available?
- Jira in use, or only Confluence?
- Teams chat activity — worth including (needs `Chat.Read`, likely admin-gated)?
- Floating pill: always visible, or only on menu-bar click?
- Exact GitHub orgs / repos that count as work.
- Raw retention: is 7 days right?
