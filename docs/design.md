# daytrace — design

> "Where did my Wednesday go?"

daytrace collects the traces a workday leaves behind (meetings, meeting notes, mail, commits, PRs, wiki edits,
focused apps) and reconstructs what each day looked like. Setup: [setup.md](setup.md).

## Principles

1. **Local-first.** Data lives in `~/.daytrace/`. Only a reviewed week/month request ever goes to an LLM.
2. **Known vs. inferred.** Every event carries an evidence level. Meetings are taken as attended; an email
   timestamp means *sent at*. LLM output is always inferred.
3. **Observe, don't judge.** No scores, streaks or goals. Summaries point out tensions with the evidence behind
   them and leave the conclusions to you; the one suggestion is a week's draft todo list for the next one.
4. **Overlap is normal.** Totals are unions of spans, not sums.
5. **Private by list.** No work/personal guessing: `[private]` rules mark what is private; it is kept as bare
   time only.
6. **Forward only.** Tracking starts at `daytrace init` (`day_zero`); adapters catch up on gaps after that.

## Pipeline

Each adapter (`src/daytrace/adapters/`) implements `fetch(since, until) -> RawEvent*` with a cursor and an
`overlap` for late-arriving data. `sync` runs them all; one failing source doesn't stop the others.

**Intake** (`intake.py`), before anything is stored:

1. Drop incognito/private-browsing windows entirely.
2. Private check (`[private]` apps, titles, paths, browser profiles): a match keeps only start/end.
3. Categorize (app → category, browser title → web app) and map path → project (top folder under the most
   specific `[paths] projects` root; GitHub repos via their local clone).
4. Scrub secrets from titles (`scrub.py`), strip URL query strings.

Raw ActivityWatch events are purged from ActivityWatch after `[retention] activitywatch_raw_days`.

The Granola adapter is the exception to this path: it yields no events. It copies new or changed notes to
Markdown files (below), so note text never enters the database. The day view matches each note to the calendar
meeting running when it was started, or starting within 15 minutes (`adapters/granola.py: match`).

## Storage

SQLite at `~/.daytrace/daytrace.db` (`store.py`):

| Table | Holds |
|---|---|
| `events` | one row per trace: source, kind (`point`/`span`/`plan`), evidence, times (UTC), category, project, scrubbed title, URL, sensitivity (`normal`/`private`), attrs |
| `day_summaries` | week/month requests awaiting review and their LLM replies, keyed `week:<sunday>` / `month:<YYYY-MM>` |
| `cursors`, `meta` | per-source sync cursors and status; `day_zero` |

Meeting notes live in `~/.daytrace/notes/granola/`: one Markdown file per note, plus `index.json` (title, time,
Granola URL, file name) and `account.json` (your name as Granola has it, to tell your action items from others').

Stats, episodes and patterns are computed on read (`dayjson.py`, `period.py`, `episodes.py`, `patterns.py`), never
stored.

## LLM egress

`egress.py` is the only code path to the LLM (`lumen.py`, any OpenAI-compatible endpoint):

1. Episodes are built from non-private events and merged into one entry per project (or meetings, or activity
   kind): days, minutes per activity, counts, ≤3 titles, site names, branch names.
2. Per-day patterns, counted locally so the LLM only interprets them (`patterns.py`): project switches,
   back-to-back meetings, the longest meeting-free stretch of the morning, focus stretches of 25+ minutes and what
   ended them, and active time outside 9–5.
3. Each meeting's notes as a digest: its topics and its action items, split into mine, waiting on someone else,
   and unassigned (`adapters/granola.py: digest`). Never the note text itself.
4. Titles lose people's names (Slack DMs, Teams/Outlook windows, meeting attendees); sent-mail subjects are
   dropped. In note digests, names that appear as action-item owners or in note titles become `[person]` and
   yours becomes "me"; a name that appears only in running text can slip through, which the review is for.
   Private time is a single total, and only a "break" when it ends a focus stretch.
5. The payload is scrubbed again and refused if anything still looks like a secret or address.
6. The exact request is written to `~/.daytrace/audit/` and shown in the app. Review can only remove (entries,
   titles, sites, branches; meetings, topics, action items). **Send** sends it once; the reply is saved alongside.
7. A stored summary is hidden once any event it used becomes private.

The prompt (`lumen.py`) asks for a short narrative, 3–6 observations each with its evidence (open loops,
recurring topics, time vs. weight, patterns) and themes; a week also asks for next week's todos grouped by
project. Week and month share the prompt otherwise. Reply time is dominated by the reply's length, not the
request's: a month-sized request (~13k tokens) took as long as a week's (~2.5k).

## Desktop app

Tauri shell (`app/src-tauri`) + React/D3 UI (`app/src`). It shells out to the CLI (`daytrace day|period|
note|summarize|sync`), so all logic stays in Python. Windows: menu-bar icon, floating day clock, Day/Week/Month view.

## Not yet

- Google Drive activity, Jira, Teams chat.
- Settings UI (rules editor, sources on/off); rules are edited in `~/.daytrace/rules.toml`.
- `forget` deletes events only, not stored summaries or audit files.
- Website export of aggregate-only stats.
