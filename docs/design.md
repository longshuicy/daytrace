# daytrace — design

> "Where did my Wednesday go?"

daytrace collects the traces a workday leaves behind (meetings, mail, commits, PRs, wiki edits, focused apps)
and reconstructs what each day looked like. Setup: [setup.md](setup.md).

## Principles

1. **Local-first.** Data lives in `~/.daytrace/`. Only a reviewed week/month request ever goes to an LLM.
2. **Known vs. inferred.** Every event carries an evidence level. Meetings are taken as attended; an email
   timestamp means *sent at*. LLM output is always inferred.
3. **Describe, don't judge.** No scores, streaks or goals.
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

## Storage

SQLite at `~/.daytrace/daytrace.db` (`store.py`):

| Table | Holds |
|---|---|
| `events` | one row per trace: source, kind (`point`/`span`/`plan`), evidence, times (UTC), category, project, scrubbed title, URL, sensitivity (`normal`/`private`), attrs |
| `day_summaries` | week/month requests awaiting review and their LLM replies, keyed `week:<sunday>` / `month:<YYYY-MM>` |
| `cursors`, `meta` | per-source sync cursors and status; `day_zero` |

Stats and episodes are computed on read (`dayjson.py`, `period.py`, `episodes.py`), never stored.

## LLM egress

`egress.py` is the only code path to the LLM (`lumen.py`, any OpenAI-compatible endpoint):

1. Episodes are built from non-private events and merged into one entry per project (or meetings, or activity
   kind): days, minutes per activity, counts, ≤3 titles, site names, branch names.
2. Titles lose people's names (Slack DMs, Teams/Outlook windows, meeting attendees); sent-mail subjects are
   dropped. Private time is a single total.
3. The payload is scrubbed again and refused if anything still looks like a secret or address.
4. The exact request is written to `~/.daytrace/audit/` and shown in the app. Review can only remove (entries,
   titles, sites, branches). **Send** sends it once; the reply is saved alongside.
5. A stored summary is hidden once any event it used becomes private.

## Desktop app

Tauri shell (`app/src-tauri`) + React/D3 UI (`app/src`). It shells out to the CLI (`daytrace day|period|
summarize|sync`), so all logic stays in Python. Windows: menu-bar icon, floating day clock, Day/Week/Month view.

## Not yet

- Google Drive activity, Jira, Teams chat.
- Settings UI (rules editor, sources on/off); rules are edited in `~/.daytrace/rules.toml`.
- `forget` deletes events only, not stored summaries or audit files.
- Website export of aggregate-only stats.
