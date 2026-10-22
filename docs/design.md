# daytrace — design

_Status: draft for discussion · 2026-10-22_

> "Where did my Wednesday go?"

daytrace is a personal, local-first desktop tool that quietly collects the traces my **work** leaves behind
(meetings, email, commits, docs, wiki edits, focused apps) and reconstructs what each day actually looked like:
a timeline, honest statistics, and an LLM-written description of the day.

## 1. Principles

1. **Local-first.** All data lives on this Mac. The only things that leave are (a) work-only payloads to Lumen and
   (b) a sanitized, aggregate export for my website that I review before publishing.
2. **Known vs. inferred.** Every fact carries its evidence level. A calendar event means *scheduled*, not *attended*.
   An email timestamp means *sent at*, not *time spent writing*. LLM output is always *inferred* and links back to
   the events it came from.
3. **Not a productivity tool.** No scores, no streaks, no goals. It describes; it doesn't judge.
4. **Overlap is normal.** Browsing during a meeting is not a bug. Categories don't need to sum to 24 hours.
5. **Fail closed.** When in doubt whether something is work, it is treated as personal: kept local, never sent.

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
│                                                                          │  │
│  website export (aggregate JSON, manual review) ─────────────────────────┼──► longshuicy.github.io
└────────────────────────────────────────────────────────────────────────────┘
```

- **Collector daemon**: Python 3.12, run by `launchd`, independent of the UI so collection continues when the app
  is closed. Each adapter is a small module: `fetch(since) -> list[RawEvent]`.
- **Store**: SQLite at `~/Library/Application Support/daytrace/daytrace.db`.
- **Analysis**: Python (DuckDB/pandas over SQLite) for deterministic stats and episode building.
- **LLM**: Lumen via OpenAI-compatible API, behind the egress gate (§8).
- **Desktop UI**: Tauri + React + D3. The same chart components are reused on the website.
- **Secrets**: macOS Keychain only. No tokens in files or env.

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
  sensitivity TEXT NOT NULL,           -- 'work' | 'personal' | 'unknown'
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

1. **Classify sensitivity** with rules from `~/.daytrace/rules.toml`:

   ```toml
   [work]
   github_orgs   = ["IN-CORE", "PermafrostDiscoveryGateway"]   # TBD
   repo_paths    = ["~/Documents/Projects/*"]
   apps          = ["Microsoft Outlook", "Microsoft Teams", "zoom.us", "Code", "iTerm2", "Terminal"]
   domains       = ["*.illinois.edu", "*.atlassian.net", "github.com"]
   google_account = "cwang138@illinois.edu"

   [personal]                       # always wins over [work]
   repo_paths = ["~/Documents/Projects/personal/*"]
   apps       = ["Messages", "WhatsApp", "Spotify", "Photos"]
   title_patterns = ["(?i)private browsing", "(?i)incognito", "(?i)bank", "(?i)gmail\\.com"]
   ```

   Order: `personal` match → personal; else `work` match → work; else **unknown**.
   An app match alone isn't enough for editors/terminals: VS Code is work only if the title resolves to a work repo.

2. **Drop** private/incognito windows entirely (not stored at all).
3. **Categorize**: map app + title → `category` and `project`.
4. **Redact**: personal/unknown events keep only `category` and duration — title set to null.
   Work titles pass a secret scrubber (tokens, keys, long hex/base64, `password=`). URLs lose query strings.
5. **Raw retention**: ActivityWatch raw buckets older than **7 days** are deleted after they've been ingested
   (configurable). daytrace keeps only the transformed events.

## 7. Analysis (deterministic, no LLM)

Per day / week:

- Time by category (overlap-aware: union of spans, not sum)
- Meeting load: count, total scheduled hours, back-to-back chains
- Focus blocks: contiguous ≥ 25 min in one project with no meeting and < N app switches
- Context switches per hour; project switches per day
- First / last active time; idle gaps
- Making vs. coordinating ratio (code/docs/wiki vs. meeting/email)
- Projects touched, with time per project
- Data completeness: which sources reported, gaps (laptop off ≠ idle)

**Episodes**: cluster work events by time proximity (gap < 10 min) and shared project/repo/doc/people.

## 8. LLM (Lumen) and the egress gate

**Endpoint**: OpenAI-compatible; `base_url` + `model` in config, API key in Keychain.

**Egress gate** — the only code path that can talk to Lumen:

1. Select events/episodes with `sensitivity = 'work'` only. `personal` and `unknown` are replaced by an opaque
   block: `{"category": "non-work", "minutes": 45}`.
2. Project to an allowlisted field set: `start_at, end_at, category, project, title, source`. Nothing else.
3. Run the secret scrubber again on the final payload.
4. Write the exact payload to `audit/YYYY-MM-DD/*.json` before sending.
5. **Dry-run mode** (default for the first weeks): build + audit, don't send.

**LLM tasks**:

- *Episode labels*: "Fixed the INCORE Helm chart deployment and documented it on the wiki."
- *Day narrative*: one paragraph + 3–5 themes, given episodes + stats. Prompt requires distinguishing scheduled
  vs. observed and not inventing time spent.
- *Week/month themes* (later): which topics rose and fell.
- *Semantic search* (later): embeddings of episode labels, if Lumen exposes an embeddings endpoint.

All outputs are stored with `evidence = 'inferred'`, the model name, and the episode/event ids used.

## 9. Desktop app (Tauri)

- **Menu-bar icon** — always present; shows sync status.
- **Floating pill** — tiny always-on-top window, toggleable: a thin 24h ribbon of today so far plus one line,
  e.g. `3 meetings · longest focus 1h40 · 4 repos`. Click to expand.
- **Day view** — day clock, timeline with evidence styling (solid = observed, outlined = scheduled,
  hatched = inferred), stats, narrative with "show evidence" links.
- **Week view** — stacked categories, meeting load, focus blocks.
- **Settings** — sources on/off, rules editor, retention, Lumen dry-run toggle, audit log viewer.

## 10. Website export

- Command: `daytrace export --range 2026-11 --out site/data/`
- Output: aggregate JSON only — per-day category minutes, meeting counts, focus blocks, project *keys*
  (renameable/hideable), no titles, no people, no LLM narrative unless explicitly approved per day.
- Review step: export writes a diff preview; nothing is copied to the website repo automatically.
- Visuals (shared React/D3 components): day clock, week river (streamgraph), context-switch graph, year heatmap.

## 11. Security & privacy checklist

- [ ] Read-only scopes everywhere
- [ ] Tokens in Keychain only
- [ ] No telemetry, no third-party analytics libs in the UI
- [ ] Egress gate is the single network path to Lumen; covered by tests
- [ ] Private browsing dropped at intake
- [ ] Personal/unknown never leave the machine
- [ ] Raw ActivityWatch data purged after retention window
- [ ] `daytrace forget --from --to` deletes a period everywhere (events, episodes, summaries, audit)

## 12. Milestones

1. **Skeleton** — repo layout, SQLite schema, rules engine, launchd daemon, `day_zero`.
2. **First traces** — ActivityWatch + local git + Claude Code adapters (all local, no auth). CLI `daytrace today`.
3. **Work systems** — GitHub, Outlook calendar (verify Graph consent early), Confluence, Drive.
4. **Analysis** — stats + episodes; CLI report.
5. **Lumen** — egress gate, dry-run, audit, episode labels, day narrative.
6. **Desktop** — Tauri tray + floating pill + day view.
7. **Website** — export + interactive visuals on the personal site.

## 13. Open questions

- Does UIUC's Microsoft 365 tenant allow user consent for `Calendars.Read` / `Mail.ReadBasic`?
- Lumen: base URL, model name, embeddings endpoint available?
- Jira in use, or only Confluence?
- Teams chat activity — worth including (needs `Chat.Read`, likely admin-gated)?
- Floating pill: always visible, or only on menu-bar click?
- Exact GitHub orgs / repos that count as work.
- Raw retention: is 7 days right?
